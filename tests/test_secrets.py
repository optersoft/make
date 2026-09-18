"""The encrypted store, and the containment around it.

Two separate claims are tested here, because they fail separately:

* a value can be stored encrypted and read back through the ordinary
  `env.require` a task already uses, and
* a value that came from a layer does not leak -- not into a child process's
  environment, not into the echoed command line, not into an error message.

The `age` tests need the `age` binary; the containment tests do not, and must
not, because containment is the part that has to hold on every machine.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from helpers import write

from make import env, secrets
from make.context import mark_sensitive, redact
from make.errors import ConfigError
from make.runner import run_one
from make.tasks import registry as global_registry
from make.tasks import task
from make.testing import context, record

needs_age = pytest.mark.skipif(shutil.which("age") is None, reason="age is not installed")


@pytest.fixture
def store(project: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An age store with a throwaway identity, in the test's own directory."""
    directory = project / "store"
    directory.mkdir()
    identity = directory / "id.txt"
    subprocess.run(["age-keygen", "-o", str(identity)], capture_output=True, check=True)
    public = subprocess.run(
        ["age-keygen", "-y", str(identity)], capture_output=True, text=True, check=True
    ).stdout.strip()
    (directory / "recipient.txt").write_text(public + "\n")
    monkeypatch.setenv("MAKE_SECRETS_DIR", str(directory))
    monkeypatch.setenv("MAKE_AGE_IDENTITY", str(identity))
    secrets.reset_cache()
    return directory


# -- the store -------------------------------------------------------------


@needs_age
def test_a_secret_survives_a_round_trip(store: Path):
    secrets.write("global", {"API_TOKEN": "s3cret", "OTHER": "plain"})
    assert not (store / "global.age").read_bytes().count(b"s3cret")  # actually encrypted
    assert secrets.load(store / "global.age") == {"API_TOKEN": "s3cret", "OTHER": "plain"}


@needs_age
def test_the_repo_layer_wins_over_the_global_one(store: Path, monkeypatch):
    monkeypatch.setattr(env, "repo_name", lambda *a, **k: "myapp")
    secrets.write("global", {"API_TOKEN": "global", "SHARED": "global"})
    secrets.write("myapp", {"API_TOKEN": "repo"})
    merged = secrets.layered()
    assert merged == {"API_TOKEN": "repo", "SHARED": "global"}


@needs_age
def test_require_reaches_the_store_for_a_credential(store: Path, project: Path):
    secrets.write("global", {"KEYSTORE_PASSWORD": "hunter2"})
    with context(root=project):
        assert env.require("KEYSTORE_PASSWORD") == "hunter2"


@needs_age
def test_a_whole_file_comes_back_as_a_private_temp_path(store: Path):
    secrets.encrypt(b"\x00keystore-bytes", store / "files" / "upload.jks.age")
    got = secrets.file("upload.jks")  # the name keeps its extension: gradle cares
    assert got.read_bytes() == b"\x00keystore-bytes"
    assert oct(got.stat().st_mode)[-3:] == "600"


@needs_age
def test_encrypting_needs_no_identity(store: Path, monkeypatch):
    """The recipient is public, so adding a secret never unlocks anything."""
    monkeypatch.delenv("MAKE_AGE_IDENTITY")
    secrets.reset_cache()
    secrets.write("global", {"NEW_TOKEN": "x"})  # must not raise
    with pytest.raises(ConfigError) as caught:
        secrets.load(store / "global.age")
    assert "no age identity" in caught.value.message


def test_a_missing_identity_says_what_to_do(project: Path, monkeypatch):
    monkeypatch.setenv("MAKE_AGE_IDENTITY", "not-a-key")
    secrets.reset_cache()
    with pytest.raises(ConfigError) as caught:
        secrets.identity()
    assert "AGE-SECRET-KEY-" in caught.value.message


# -- containment -----------------------------------------------------------


def test_a_credential_is_not_exported_to_child_processes(project: Path):
    """The leak this whole design exists to close.

    `env.layered()` used to export every variable in every layer, so a build
    tool and its plugins received every credential the machine had -- the
    trading account's password among them, on the way to signing an APK.
    """
    layer = write(project / "a.env", "ANDROID_KEY_ALIAS=upload\nIBKR_PASSWORD=trading\n")
    with context(root=project):
        env.layered(files=[layer])
        from make.context import current

        assert current().env["ANDROID_KEY_ALIAS"] == "upload"  # configuration, exported
        assert "IBKR_PASSWORD" not in current().env  # credential, withheld
        assert env.secret("IBKR_PASSWORD") == "trading"  # available on request
        assert current().env["IBKR_PASSWORD"] == "trading"  # and only then


def test_asking_for_a_secret_scopes_it_to_that_task(project: Path):
    @task(secrets=["DEPLOY_SECRET"])
    def deploy() -> None:
        pass

    @task
    def other() -> None:
        from make.context import current

        assert "DEPLOY_SECRET" not in current().env

    layer = write(project / "a.env", "DEPLOY_SECRET=abc\n")
    with context(root=project):
        env.layered(files=[layer])
        run_one(global_registry.require("deploy"))
        run_one(global_registry.require("other"))


def test_a_declared_secret_fails_before_the_work(project: Path):
    ran: list[str] = []

    @task
    def expensive() -> None:
        ran.append("expensive")

    @task(needs=[expensive], secrets=["MISSING_TOKEN"])
    def ship() -> None:
        ran.append("ship")

    with context(root=project):
        with pytest.raises(ConfigError) as caught:
            run_one(global_registry.require("ship"))
    assert ran == []  # not after the twenty-minute build
    assert "MISSING_TOKEN" in caught.value.message


def test_a_secret_is_masked_in_the_echoed_command(project: Path, capsys):
    layer = write(project / "a.env", "API_TOKEN=swordfish\n")
    with context(root=project):
        env.layered(files=[layer])
        value = env.secret("API_TOKEN")
        with record():
            from make import sh

            sh("curl", "-H", f"Authorization: Bearer {value}", "https://example.com")
    printed = capsys.readouterr().err
    assert "swordfish" not in printed
    assert "***" in printed


def test_a_secret_is_masked_in_a_failure_message():
    mark_sensitive("swordfish")
    from make.errors import CommandFailed

    failure = CommandFailed(["curl", "-u", "swordfish"], 22, "401 swordfish rejected")
    assert "swordfish" not in failure.message
    assert redact("a line with swordfish in it") == "a line with *** in it"


def test_a_short_value_is_not_masked():
    """Masking `1` or `true` would redact half of every command line."""
    mark_sensitive("ab")
    assert redact("ab cd") == "ab cd"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("KEYSTORE_PASSWORD", True),
        ("AXUM_OAUTH_GOOGLE_CLIENT_SECRET", True),
        ("HETZNER_CLOUD_TOKEN", True),
        ("KOTLIN_TOOLCHAIN_SIGNING_KEY", True),
        ("SESSION_SECRET", True),
        ("ANDROID_KEY_ALIAS", False),  # a label
        ("PLAY_ACCOUNT_JSON", False),  # a path
        ("ANDROID_KEYSTORE", False),  # a filename
        ("HIVE_TURSO_PATH", False),
        ("AXUM_OAUTH_BASE_URL", False),
    ],
)
def test_which_names_read_as_credentials(name: str, expected: bool):
    """The classifier that decides what a plaintext layer stops exporting.

    Every case here is a real variable from the fleet's own env files, and the
    false ones are the traps: three of them end in a word that looks like a
    credential and name a location instead.
    """
    assert env.sensitive(name) is expected
