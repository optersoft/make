"""Finding and reaping processes, with the discipline built in.

`lsof`/`pgrep`/`pkill` calls were scattered through the fleet's task files, and
each call site had to re-learn the same three rules: match on the *listening*
socket, not every connection to the port; scope `pkill -f` patterns tightly,
because an unscoped one once matched its own caller; and never kill anything
under `--dry-run`. One implementation, so the rules cannot be re-forgotten:

    from make import proc

    proc.port_pids(8080)              # who is LISTENING on :8080
    proc.reap_port(8080)              # terminate them; -> pids it signalled
    proc.reap("dx serve.*drive-web")  # pattern reap; anchor it to YOUR thing

Everything goes through `sh`, so `make.testing.record()` sees these calls and
`--dry-run` reports instead of killing.
"""

from __future__ import annotations

from collections.abc import Sequence

from .context import current, note
from .sh import sh

__all__ = ["file_pids", "pattern_pids", "port_pids", "reap", "reap_port"]


def _pids(lines: Sequence[str]) -> list[int]:
    found: list[int] = []
    for line in lines:
        token = line.split()[0] if line.split() else ""
        if token.isdigit():
            found.append(int(token))
    return found


def port_pids(port: int, *, dry: Sequence[str] = ()) -> list[int]:
    """PIDs *listening* on a TCP port -- not everyone connected to it."""
    lines = sh.lines("lsof", "-t", f"-iTCP:{port}", "-sTCP:LISTEN", check=False, echo_cmd=False, dry=dry)
    return _pids(lines)


def file_pids(*paths: object, dry: Sequence[str] = ()) -> list[int]:
    """PIDs holding any of these files open -- WAL locks, log holders.

    The reap that actually matters for a database: an orphan can hold the lock
    from no port at all, invisible to a port-scoped reap.
    """
    if not paths:
        return []
    lines = sh.lines("lsof", "-t", "--", *paths, check=False, echo_cmd=False, dry=dry)
    return sorted(set(_pids(lines)))


def pattern_pids(pattern: str, *, dry: Sequence[str] = ()) -> list[int]:
    """PIDs whose full command line matches `pattern` (a `pgrep -f` regex).

    Anchor the pattern to something only your target carries -- a port, a
    profile directory, an AVD name. A bare program name matches other people's
    processes, and once matched a task's own parent shell.
    """
    lines = sh.lines("pgrep", "-f", pattern, check=False, echo_cmd=False, dry=dry)
    return _pids(lines)


def _kill(pids: list[int], *, force: bool, label: str) -> list[int]:
    if not pids:
        return []
    if current().dry_run:
        note(f"would signal {label}: {', '.join(map(str, pids))}")
        return []
    for pid in pids:
        sh("kill", "-9" if force else "-15", str(pid), check=False, echo_cmd=False)
    note(f"signalled {label}: {', '.join(map(str, pids))}")
    return pids


def reap_port(port: int, *, force: bool = False, dry: Sequence[str] = ()) -> list[int]:
    """Terminate whatever is listening on a port. Returns the PIDs signalled.

    An empty answer is not an error -- "nothing to reap" is the normal case.
    """
    return _kill(port_pids(port, dry=dry), force=force, label=f":{port}")


def reap(pattern: str, *, force: bool = False, dry: Sequence[str] = ()) -> list[int]:
    """Terminate every process matching a `pgrep -f` pattern. Returns the PIDs.

    The pattern rules from `pattern_pids` apply doubly here: this one kills.
    """
    return _kill(pattern_pids(pattern, dry=dry), force=force, label=pattern)
