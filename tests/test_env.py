"""Layered secrets, including the worktree bug this replaces."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from helpers import write

from make import env
from make.errors import ConfigError
from make.testing import context


def test_parse_handles_export_quotes_and_comments():
    values = env.parse(
        """
        # a comment
        export TOKEN=abc
        QUOTED="two words"
        LITERAL='no $INTERP here'
        TRAILING=value  # not part of it
        """
    )
    assert values == {
        "TOKEN": "abc",
        "QUOTED": "two words",
        "LITERAL": "no $INTERP here",
        "TRAILING": "value",
    }


def test_parse_interpolates_earlier_values():
    values = env.parse("BASE=/opt\nBIN=${BASE}/bin\n")
    assert values["BIN"] == "/opt/bin"


def test_later_layers_win(project: Path):
    a = write(project / "a.env", "PORT=1\nSHARED=from-a\n")
    b = write(project / "b.env", "PORT=2\n")
    with context(root=project):
        merged = env.layered(files=[a, b], export=False)
    assert merged == {"PORT": "2", "SHARED": "from-a"}


def test_an_explicit_export_outranks_every_file(project: Path, monkeypatch):
    a = write(project / "a.env", "TOKEN=from-file\n")
    monkeypatch.setenv("TOKEN", "from-caller")
    with context(root=project) as active:
        env.layered(files=[a])
        from make.context import current

        assert current().env.get("TOKEN") != "from-file"
        assert env.get("TOKEN") == "from-caller"
    assert active is not None


def test_override_true_lets_files_win(project: Path, monkeypatch):
    a = write(project / "a.env", "TOKEN=from-file\n")
    monkeypatch.setenv("TOKEN", "from-caller")
    with context(root=project):
        env.layered(files=[a], override=True)
        assert env.get("TOKEN") == "from-file"


def test_export_does_not_mutate_os_environ(project: Path):
    a = write(project / "a.env", "NEW_THING=1\n")
    import os

    with context(root=project):
        env.layered(files=[a])
        assert env.get("NEW_THING") == "1"
    assert "NEW_THING" not in os.environ


def test_require_names_where_to_put_the_value(project: Path):
    with context(root=project):
        with pytest.raises(ConfigError) as caught:
            env.require("PLAY_ACCOUNT_JSON")
    assert "PLAY_ACCOUNT_JSON is not set" in caught.value.message
    assert "secrets.env" in (caught.value.hint or "")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.mark.skipif(
    not Path("/usr/bin/git").exists() and not Path("/opt/homebrew/bin/git").exists(), reason="git"
)
def test_repo_name_is_the_main_checkout_even_inside_a_worktree(tmp_path: Path):
    """The failure `just` shipped: `--show-toplevel` names the *worktree*.

    A worktree at `.../myapp/worktrees/feature` looked up `~/.just/feature.env`,
    found nothing, and started the dev server with no application environment at
    all -- silently, because a missing layer is not an error.
    """
    main = tmp_path / "myapp"
    main.mkdir()
    _git(main, "init", "-q")
    _git(main, "config", "user.email", "t@example.com")
    _git(main, "config", "user.name", "t")
    (main / "README").write_text("x")
    _git(main, "add", ".")
    _git(main, "commit", "-qm", "init")

    linked = tmp_path / "elsewhere" / "feature"
    _git(main, "worktree", "add", "-q", str(linked), "-b", "feature")

    assert env.repo_name(main) == "myapp"
    assert env.repo_name(linked) == "myapp"  # not "feature"
