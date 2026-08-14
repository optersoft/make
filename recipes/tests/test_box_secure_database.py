"""Tests for the `box`, `secure` and `database` groups."""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest
from make.errors import MakeError, ToolMissing
from make.testing import context, record

from make_recipes_optersoft import box, database, secure
from make_recipes_optersoft.box import Box

# -- secure ----------------------------------------------------------------


def test_a_password_has_the_requested_length():
    assert len(secure.generate(40)) == 40


def test_a_password_always_contains_a_special_character():
    """The shell version re-rolled until one appeared; here it is placed."""
    assert all(any(c in secure.SPECIAL for c in secure.generate(8)) for _ in range(200))


def test_a_password_stays_in_the_url_safe_alphabet():
    """Safe to drop into URLs, filenames and shell arguments unquoted."""
    assert set(secure.generate(200)) <= set(secure.ALPHABET)


def test_short_passwords_are_refused():
    with pytest.raises(ValueError, match="must be >= 8"):
        secure.generate(7)


def test_passwords_do_not_repeat():
    assert len({secure.generate(32) for _ in range(100)}) == 100


def test_copy_keeps_stdout_clean(capsys):
    with record() as rec:
        value = secure.password(16, copy=True)
    assert capsys.readouterr().out == ""
    assert rec.saw("pbcopy")
    assert len(value) == 16


# -- box -------------------------------------------------------------------


@pytest.fixture(autouse=True)
def bucket():
    Box.reset()
    Box.configure(bucket="backups")
    yield
    Box.reset()


def test_every_operation_is_scoped_to_the_bucket(tmp_path: Path):
    with context(root=tmp_path), record(responses={"hetzner-box": "listing"}) as rec:
        box.ls("old/")
    assert rec.saw("--dir", "backups", "ls", "old/")


def test_a_path_with_spaces_stays_one_argument(tmp_path: Path):
    """`box.just` interpolated `{{ PATH }}` into bash; here it cannot become syntax."""
    with context(root=tmp_path), record() as rec:
        box.cp(Path("my file.tar.gz"), "backups/")
    assert rec.commands[-1][-2:] == ["my file.tar.gz", "backups/"]


def test_the_cli_is_installed_on_first_use(tmp_path: Path):
    with context(root=tmp_path), record(missing_tools=["hetzner-box"]) as rec:
        with pytest.raises(ToolMissing, match="still not on PATH"):
            box.target()
    assert rec.saw("cargo", "install", "--git", box.SOURCE_REPO)


def test_purge_is_confirm_gated():
    from make.errors import Aborted
    from make.recipes import registry
    from make.runner import run_one

    with context(), record():
        with pytest.raises(Aborted):
            run_one(registry.require("box.purge"))


# -- database --------------------------------------------------------------


def test_the_output_directory_is_scoped_per_repository(tmp_path: Path):
    """Every optersoft repo names its doc docs/database.md.

    A single shared /tmp/<name>.svg had them overwrite each other, and after a
    failed render `open` would show a different repo's stale diagram.
    """
    with context(root=tmp_path), record(responses={"git rev-parse": str(tmp_path / "x/.git")}):
        assert database.output_dir().name == "x"


def test_a_failed_render_opens_nothing(tmp_path: Path):
    doc = tmp_path / "docs" / "database.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("```mermaid\nbroken\n```")

    with context(root=tmp_path), record(failures={"mermaid-cli": 1}) as rec:
        with pytest.raises(MakeError, match="mermaid could not render"):
            database.render(doc)
    assert not rec.saw("open")


def test_a_missing_document_is_a_clear_error(tmp_path: Path):
    with context(root=tmp_path), record():
        with pytest.raises(MakeError, match="does not exist"):
            database.render(tmp_path / "nope.md")


def test_the_dark_theme_switches_both_theme_and_background(tmp_path: Path):
    doc = tmp_path / "er.md"
    doc.write_text("```mermaid\ngraph TD;\n```")

    def render_with(theme):
        with context(root=tmp_path), record() as rec:
            # The render "succeeds" but writes nothing, so it fails at the check
            # after the mermaid call -- which is all we need to inspect the flags.
            with contextlib.suppress(MakeError):
                database.render(doc, theme=theme, open_result=False)
        return rec

    assert render_with("dark").saw("-t", "dark", "-b", "#1f2937")
    assert render_with("grey").saw("-t", "neutral", "-b", "#9ca3af")
