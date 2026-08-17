"""The `rust` group: target/ discovery, clean and sweep for cargo repositories.

Generic on purpose -- nothing here knows whose repo it runs in. It scans from
the task-file root, where mk runs tasks, because that is the repository the
invocation is about; multi-workspace repos get every target dir, not just the
root one.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from make import fs, note, sh, step, task

#: Never descended into: nothing inside can be a workspace of THIS repo.
#: `target` itself is here so a found dir is not also searched.
PRUNE = {".git", "target", "node_modules", ".venv", "venv", "dist", "build"}


def find_targets(root: str | Path | None = None) -> list[Path]:
    """Every real cargo target dir under root (default: cwd), outermost first.

    Real means cargo made it: a `CACHEDIR.TAG` inside, or a `Cargo.toml`
    beside it. A directory that merely shares the name is never returned.
    """
    root = Path(root) if root else Path.cwd()
    found = []
    for dirpath, dirnames, _ in os.walk(root):
        d = Path(dirpath)
        if "target" in dirnames and ((d / "target/CACHEDIR.TAG").exists() or (d / "Cargo.toml").exists()):
            found.append(d / "target")
        dirnames[:] = [n for n in dirnames if n not in PRUNE and not n.startswith(".")]
    return found


def _du_kb(path: Path) -> int:
    """Size in KiB. `du` is a read, but it goes through sh for the dry-run trace."""
    return int(sh.out("du", "-sxk", path, dry=f"0\t{path}").split()[0])


def _idle_days(path: Path) -> float:
    return (time.time() - path.stat().st_mtime) / 86400


def _human(kb: int) -> str:
    for unit, factor in (("T", 1 << 30), ("G", 1 << 20), ("M", 1 << 10)):
        if kb >= factor:
            return f"{kb / factor:.1f}{unit}"
    return f"{kb}K"


@task(group="rust")
def usage() -> None:
    """Every cargo target/ dir in this repo -- size and days since last write."""
    total = 0
    targets = find_targets()
    for t in targets:
        kb = _du_kb(t)
        total += kb
        step(f"{_human(kb):>8}  {_idle_days(t):4.0f}d idle  {t.relative_to(Path.cwd())}")
    note(f"{_human(total)} across {len(targets)} target dir(s)")


@task(group="rust")
def clean(*, older_than: int = 0) -> None:
    """Delete this repo's target/ dirs. Regenerable -- but slowly, so age-gate it.

    Args:
        older_than: only dirs nothing wrote to for this many days (0 = all)
    """
    doomed = [t for t in find_targets() if _idle_days(t) >= older_than]
    if not doomed:
        note(f"no target/ dir idle >= {older_than}d")
        return
    total = 0
    for t in doomed:
        kb = _du_kb(t)
        total += kb
        step(f"{_human(kb):>8}  {t.relative_to(Path.cwd())}")
        fs.rmtree(t)
    note(f"reclaimed {_human(total)} from {len(doomed)} target dir(s)")


@task(group="rust", requires=["cargo", "cargo-sweep"])
def sweep(*, days: int = 30) -> None:
    """`cargo sweep` every workspace: drop artifacts untouched for N days, keep the hot ones.

    The gentler default -- incremental state survives, so the next build is
    warm. Needs cargo-sweep (`cargo install cargo-sweep`).

    Args:
        days: age threshold handed to `cargo sweep --time`
    """
    for t in find_targets():
        sh("cargo", "sweep", "--time", str(days), cwd=t.parent)
