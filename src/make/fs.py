"""Filesystem changes that honour `--dry-run`.

`sh()` is only half of what a recipe does. The other half is writing a generated
file, wiping a build tree, copying a listing into place -- and a `--dry-run` that
suppresses every command while still mutating the working directory is worse
than none, because it looks safe.

That is not hypothetical: the first end-to-end dry run of `web.start` rewrote a
repo's generated `tailwind-sources.css` from a `cargo metadata` call that had
itself been skipped, producing an empty file where a correct one had been.

Use these instead of `pathlib` and `shutil` wherever a recipe changes something:

    fs.write(path, text)
    fs.mkdir(path)
    fs.copy(source, destination)
    fs.remove(path)
    fs.rmtree(path)
    fs.replace(source, destination)

Reading is unaffected -- `Path.read_text`, `glob`, `stat` and friends stay as
they are, because reading is safe under a dry run and pretending otherwise would
make the dry run diverge from the real one.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .context import current, echo, paint

__all__ = ["write", "mkdir", "copy", "remove", "rmtree", "replace", "touch", "chmod"]


def _announce(action: str, target: object, *, extra: str = "") -> bool:
    """Report the change; return False when it must not actually happen."""
    context = current()
    skipped = context.dry_run
    if context.quiet and not skipped:
        return not skipped
    prefix = paint("[dry-run] ", "yellow") if skipped else ""
    echo(prefix + paint(f"{action} ", "blue", "bold") + paint(f"{target}{extra}", "blue"))
    return not skipped


def write(path: str | Path, content: str, *, encoding: str = "utf-8", parents: bool = True) -> Path:
    """Write a text file."""
    target = Path(path)
    if not _announce("write", target, extra=f" ({len(content)} bytes)"):
        return target
    if parents:
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding=encoding)
    return target


def mkdir(path: str | Path, *, parents: bool = True, exist_ok: bool = True) -> Path:
    target = Path(path)
    if target.is_dir():
        return target
    if not _announce("mkdir", target):
        return target
    target.mkdir(parents=parents, exist_ok=exist_ok)
    return target


def copy(source: str | Path, destination: str | Path, *, parents: bool = True) -> Path:
    target = Path(destination)
    if not _announce("copy", f"{source} -> {target}"):
        return target
    if parents:
        target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def remove(path: str | Path, *, missing_ok: bool = True) -> None:
    target = Path(path)
    if missing_ok and not target.exists():
        return
    if not _announce("remove", target):
        return
    target.unlink(missing_ok=missing_ok)


def rmtree(path: str | Path, *, missing_ok: bool = True) -> None:
    target = Path(path)
    if not target.exists():
        if not missing_ok:
            raise FileNotFoundError(target)
        return
    if not _announce("rmtree", target):
        return
    shutil.rmtree(target, ignore_errors=True)


def replace(source: str | Path, destination: str | Path) -> Path:
    """Atomically move `source` onto `destination`."""
    target = Path(destination)
    if not _announce("move", f"{source} -> {target}"):
        return target
    Path(source).replace(target)
    return target


def touch(path: str | Path, *, parents: bool = True) -> Path:
    target = Path(path)
    if not _announce("touch", target):
        return target
    if parents:
        target.parent.mkdir(parents=True, exist_ok=True)
    target.touch()
    return target


def chmod(path: str | Path, mode: int) -> None:
    target = Path(path)
    if not _announce("chmod", f"{oct(mode)} {target}"):
        return
    os.chmod(target, mode)
