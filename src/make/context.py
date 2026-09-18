"""The per-run context, and the terminal output built on top of it.

`ctx` is a module-level proxy rather than a value you pass around: tasks call
`sh()` several layers deep, and threading a context object through every helper
is exactly the kind of ceremony this tool exists to remove. The real object
lives in a `ContextVar`, so `-j` worker threads and nested runs each see their
own without any global mutation.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Context:
    """Everything a task needs to know about *how* it is being run."""

    root: Path
    """Directory holding the task file. Tasks run with this as cwd."""

    invocation_dir: Path
    """Where the user actually typed the command."""

    task_file: Path | None = None

    dry_run: bool = False
    """`sh()` prints instead of executing. A real dry run, not text expansion."""

    yes: bool = False
    """Pre-answer confirmation prompts for `dangerous=True` tasks."""

    quiet: bool = False
    verbose: int = 0
    jobs: int = 1
    json: bool = False
    color: bool = True

    env: dict[str, str] = field(default_factory=dict)
    """Extra environment layered onto every `sh()` call (see `make.env`)."""

    _memo: dict[str, Any] = field(default_factory=dict, repr=False)
    """Per-run memo of already-satisfied `needs=`, keyed by task full name."""

    def with_(self, **changes: Any) -> Context:
        return replace(self, **changes)


def _default_context() -> Context:
    cwd = Path.cwd()
    return Context(root=cwd, invocation_dir=cwd, color=_color_enabled())


_current: ContextVar[Context | None] = ContextVar("make_context", default=None)


def current() -> Context:
    """The active context, or a permissive default when used as a plain library."""
    got = _current.get()
    if got is None:
        got = _default_context()
        _current.set(got)
    return got


def set_context(context: Context) -> None:
    _current.set(context)


@contextmanager
def using(context: Context) -> Iterator[Context]:
    token = _current.set(context)
    try:
        yield context
    finally:
        _current.reset(token)


class _ContextProxy:
    """Attribute access forwarded to the active context: `ctx.dry_run`."""

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(current(), name)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ctx {current()!r}>"


ctx = _ContextProxy()


def path(*parts: str | Path) -> Path:
    """Resolve a repo-relative path against the task-file root.

    The runner runs tasks with the root as cwd, so a bare relative path
    usually works -- but a task imported and called as a plain function from
    somewhere else has no such guarantee. Tasks that touch the filesystem
    should go through this, so they behave the same either way.

    An absolute path is returned unchanged.
    """
    candidate = Path(*parts) if parts else Path()
    return candidate if candidate.is_absolute() else current().root / candidate


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

_ANSI = {
    "reset": "\033[0m",
    "dim": "\033[2m",
    "bold": "\033[1m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
}


def _color_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stderr.isatty()


def paint(text: str, *styles: str) -> str:
    """Wrap `text` in ANSI styles, unless colour is off for this run."""
    try:
        enabled = current().color
    except Exception:  # pragma: no cover - during interpreter teardown
        enabled = False
    if not enabled or not styles:
        return text
    codes = "".join(_ANSI.get(s, "") for s in styles)
    return f"{codes}{text}{_ANSI['reset']}"


# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------

#: Values that must never reach the terminal through `make`'s own output.
#: Process-wide rather than per-context: a secret is a secret in every task.
_sensitive: set[str] = set()

#: Below this length a value is too short to be worth redacting, and too
#: likely to be a substring of something innocent ("1", "true", a port).
_MIN_SENSITIVE = 4


def mark_sensitive(*values: str) -> None:
    """Register values to be masked in every `echo` from now on.

    Called for you by the secret store and for env-file keys that look like
    credentials (`env.SENSITIVE`). Call it yourself for a value that arrived
    some other way -- a token parsed out of a tool's output, say.
    """
    for value in values:
        if value and len(value) >= _MIN_SENSITIVE:
            _sensitive.add(value)


def redact(text: str) -> str:
    """`text` with every registered secret replaced by `***`."""
    if not _sensitive:
        return text
    for value in sorted(_sensitive, key=len, reverse=True):
        if value in text:
            text = text.replace(value, "***")
    return text


def echo(message: str = "", *, err: bool = True) -> None:
    """Print one line, redacted. Every terminal line `make` writes goes through here.

    A task's own `print()` does not: printing a password with `mk secure.get`
    is the point of that task. Redaction is for the runner's channel -- echoed
    commands, `--dry-run`, `--verbose`, error messages -- where a secret is
    never the intended output.
    """
    print(redact(message), file=sys.stderr if err else sys.stdout, flush=True)


def info(message: str) -> None:
    if not current().quiet:
        echo(message)


def step(message: str) -> None:
    """A headline for a phase of work. Suppressed by --quiet."""
    if not current().quiet:
        echo(paint("==> ", "green", "bold") + paint(message, "bold"))


def note(message: str) -> None:
    if not current().quiet:
        echo(paint("note: ", "dim") + paint(message, "dim"))


def warn(message: str) -> None:
    echo(paint("warning: ", "yellow", "bold") + message)


def error(message: str) -> None:
    echo(paint("error: ", "red", "bold") + message)


def debug(message: str) -> None:
    if current().verbose:
        echo(paint("debug: " + message, "dim"))


def confirm(question: str, *, default: bool = False) -> bool:
    """Ask on the terminal. `--yes` pre-answers; a non-tty refuses rather than hangs."""
    context = current()
    if context.yes:
        return True
    if not sys.stdin.isatty():
        return False
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        answer = input(paint("?? ", "yellow", "bold") + question + suffix).strip().lower()
    except (EOFError, KeyboardInterrupt):
        echo()
        return False
    if not answer:
        return default
    return answer in ("y", "yes")
