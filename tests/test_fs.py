"""Filesystem changes must honour --dry-run.

A dry run that suppresses every command while still mutating the working
directory is worse than none, because it looks safe. This was found for real:
the first end-to-end dry run of a dev-server task rewrote a repo's generated
stylesheet manifest from a `cargo metadata` call that had itself been skipped.
"""

from __future__ import annotations

from pathlib import Path

from make import fs
from make.testing import context


def test_write_is_suppressed(project: Path):
    target = project / "generated.css"
    target.write_text("original")
    with context(root=project, dry_run=True):
        fs.write(target, "replacement")
    assert target.read_text() == "original"


def test_write_happens_for_real_otherwise(project: Path):
    target = project / "nested" / "generated.css"
    with context(root=project):
        fs.write(target, "content")
    assert target.read_text() == "content"


def test_rmtree_is_suppressed(project: Path):
    tree = project / "target" / "incremental"
    tree.mkdir(parents=True)
    with context(root=project, dry_run=True):
        fs.rmtree(tree)
    assert tree.exists()


def test_copy_and_mkdir_are_suppressed(project: Path):
    source = project / "a.txt"
    source.write_text("x")
    with context(root=project, dry_run=True):
        fs.mkdir(project / "new")
        fs.copy(source, project / "new" / "a.txt")
    assert not (project / "new").exists()


def test_remove_and_replace_are_suppressed(project: Path):
    doomed = project / "doomed.txt"
    doomed.write_text("x")
    moved = project / "moved.txt"
    moved.write_text("y")
    with context(root=project, dry_run=True):
        fs.remove(doomed)
        fs.replace(moved, project / "elsewhere.txt")
    assert doomed.exists()
    assert moved.exists()
    assert not (project / "elsewhere.txt").exists()


def test_a_dry_run_still_reports_what_it_would_do(project: Path, capsys):
    with context(root=project, dry_run=True):
        fs.write(project / "x.txt", "hello")
    err = capsys.readouterr().err
    assert "[dry-run]" in err
    assert "write" in err and "x.txt" in err


def test_reading_is_never_suppressed(project: Path):
    """Suppressing reads would make the dry run diverge from the real one."""
    source = project / "a.txt"
    source.write_text("content")
    with context(root=project, dry_run=True):
        assert source.read_text() == "content"
        assert list(project.glob("*.txt")) == [source]
