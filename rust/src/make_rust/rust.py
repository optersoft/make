"""The `rust` group: target/ discovery, clean and sweep for cargo repositories.

Generic on purpose -- nothing here knows whose repo it runs in. It scans from
the task-file root, where mk runs tasks, because that is the repository the
invocation is about; multi-workspace repos get every target dir, not just the
root one.
"""

from __future__ import annotations

import contextlib
import os
import re
import time
from pathlib import Path

from make import fs, note, sh, step, task
from make.errors import MakeError

#: Never descended into: nothing inside can be a workspace of THIS repo.
#: `target` itself is here so a found dir is not also searched.
PRUNE = {".git", "target", "node_modules", ".venv", "venv", "dist", "build"}

#: Markers around the settings `rust.config` owns in $CARGO_HOME/config.toml,
#: so reruns replace the block in place and everything around it survives.
CONFIG_BEGIN = "# --- managed by `mk rust.config` (make-rust) ---"
CONFIG_END = "# --- end of the `mk rust.config` block ---"

CONFIG_BLOCK = f"""{CONFIG_BEGIN}
# Dev-profile debuginfo policy, machine-wide. Config profiles MERGE OVER every
# repository's Cargo.toml, so this covers all checkouts with no per-repo
# commit; builds under another $CARGO_HOME (containers, CI) are unaffected.
# Full DWARF for one dependency, one run:
#     CARGO_PROFILE_DEV_PACKAGE_<crate>_DEBUG=2 cargo build

[profile.dev]
# Backtraces still resolve file:line in workspace crates; a debugger loses
# variable info.
debug = "line-tables-only"
split-debuginfo = "unpacked"

# Dependencies get no line tables at all: their frames show function names
# only. This is where most of the bytes are -- deps/ dwarfs everything else.
[profile.dev.package."*"]
debug = false
{CONFIG_END}"""

#: Subdirectories of a profile dir where cargo appends `name-<hash>` entries on
#: every feature/flag/dependency change and never deletes the superseded ones.
GC_DIRS = ("deps", "examples", "incremental", "build", ".fingerprint")

#: The disambiguating suffix cargo appends: 16 hex chars in deps/build, a
#: base62 token in incremental/. Anchored at the end and required to carry a
#: digit, so a crate name's own last segment (`-manager`, `-constants`) is
#: never mistaken for one.
HASH_SUFFIX = re.compile(r"-(?=[0-9a-z]*\d)[0-9a-z]{8,}$")


def family(name: str) -> str:
    """`libweb-8ff30a997dcbb8f0.rlib` -> `libweb.rlib`; `web-2f78bbnhs1lta` -> `web`."""
    stem, dot, ext = name.partition(".")
    return HASH_SUFFIX.sub("", stem) + dot + ext


def profile_dirs(target: Path) -> list[Path]:
    """Cargo output dirs inside one target/: `<profile>` and `<triple>/<profile>`.

    Recognised by holding a `deps/` subdir, so `target/dx`, `target/tmp` and
    anything else squatting in target/ is never yielded.
    """
    found = []
    for child in sorted(p for p in target.iterdir() if p.is_dir()):
        if (child / "deps").is_dir():
            found.append(child)
        else:
            found.extend(
                sub for sub in sorted(p for p in child.iterdir() if p.is_dir()) if (sub / "deps").is_dir()
            )
    return found


def _size_kb(path: Path) -> int:
    """lstat-based size; walks directories. No subprocess, gc visits thousands."""
    if not path.is_dir() or path.is_symlink():
        return path.lstat().st_size // 1024
    total = 0
    for dirpath, _, files in os.walk(path):
        for name in files:
            with contextlib.suppress(OSError):
                total += os.lstat(os.path.join(dirpath, name)).st_size
    return total // 1024


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


@task(group="rust")
def config(*, cargo_home: str = "") -> None:
    """Install the dev debuginfo policy into $CARGO_HOME/config.toml.

    Dev builds otherwise carry full DWARF for the whole dependency graph --
    one repo with no [profile.dev] of its own held 15 GB of it. The settings
    live between marker comments: created on the first run, replaced in place
    on every later one, so hand-written content around them survives. A
    [profile.dev] the user wrote *outside* the block is refused rather than
    duplicated -- two tables of the same name would be invalid TOML.

    Args:
        cargo_home: where config.toml lives (default $CARGO_HOME or ~/.cargo)
    """
    home = Path(cargo_home or os.environ.get("CARGO_HOME") or Path.home() / ".cargo")
    path = home / "config.toml"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""

    if CONFIG_BEGIN in text and CONFIG_END in text:
        head, rest = text.split(CONFIG_BEGIN, 1)
        tail = rest.split(CONFIG_END, 1)[1]
        outside, new = head + tail, head + CONFIG_BLOCK + tail
    else:
        separator = "" if not text else ("\n" if text.endswith("\n") else "\n\n")
        outside, new = text, text + separator + CONFIG_BLOCK + "\n"

    if "[profile.dev" in outside:
        raise MakeError(
            f"{path} already defines [profile.dev] outside the managed block -- "
            "merge it by hand, then delete yours: a duplicate table is invalid TOML"
        )
    if new == text:
        note(f"{path} is current")
        return
    fs.write(path, new)
    note(f"debuginfo policy {'updated' if CONFIG_BEGIN in text else 'installed'} in {path}")


@task(group="rust")
def gc(*, keep: int = 2) -> None:
    """Delete superseded hash-siblings in every target dir -- the churn collector.

    Cargo names artifacts `name-<hash>` and appends a new one whenever features,
    flags or a dependency change; it never deletes the old ones, and time-based
    sweeping cannot see churn that happened this week. One week of dx serve on
    academy left 88 copies of libacademy_web in deps/ alone. This keeps the
    newest `keep` per family (per directory) in deps/, examples/, incremental/,
    build/ and .fingerprint/ and deletes the rest.

    Safe by construction: cargo rebuilds anything it misses, so deleting a live
    artifact costs a recompile, never a wrong build. `keep=2` holds two feature
    worlds at once (rust-analyzer's check + dx's build share target/debug).
    Do not run while a build is in flight.

    Args:
        keep: newest entries kept per artifact family (default 2)
    """
    freed = 0
    for target in find_targets():
        for profile in profile_dirs(target):
            for sub in GC_DIRS:
                directory = profile / sub
                if not directory.is_dir():
                    continue
                groups: dict[str, list[Path]] = {}
                for entry in directory.iterdir():
                    groups.setdefault(family(entry.name), []).append(entry)
                doomed_kb = 0
                for members in groups.values():
                    if len(members) <= keep:
                        continue
                    members.sort(key=lambda p: p.lstat().st_mtime, reverse=True)
                    for doomed in members[keep:]:
                        doomed_kb += _size_kb(doomed)
                        if doomed.is_dir() and not doomed.is_symlink():
                            fs.rmtree(doomed)
                        else:
                            fs.remove(doomed)
                if doomed_kb:
                    freed += doomed_kb
                    step(f"{_human(doomed_kb):>8}  {directory.relative_to(Path.cwd())}")
    note(f"gc reclaimed {_human(freed)}" if freed else "gc: nothing superseded")


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
