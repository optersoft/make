"""make -- a command runner whose tasks are Python.

    # Makefile.py
    from make import task, sh

    @task(group="app", requires=["cargo"])
    def test(*, fast: bool = False) -> None:
        \"\"\"Run the test suite.\"\"\"
        sh("cargo", "test", *(["--lib"] if fast else []))

    $ mk app.test --fast

The command line comes from the signature, so there is no second schema to keep
in sync. Tasks are ordinary functions: importable, unit-testable, and
distributable as versioned packages instead of a directory someone `git clone`d.
"""

from __future__ import annotations

__version__ = "0.4.0"

from . import config, env, fs, http, proc
from .context import Context, confirm, ctx, echo, info, note, paint, path, step, warn
from .errors import (
    Aborted,
    CommandFailed,
    ConfigError,
    MakeError,
    TaskError,
    ToolMissing,
    UsageError,
    WaitTimeout,
)
from .params import Arg, arg
from .poll import poll
from .runner import invoke
from .sh import Result, sh
from .tasks import Group, Task, alias, group, registry, task

__all__ = [
    "__version__",
    # authoring
    "task",
    "group",
    "alias",
    "invoke",
    "sh",
    "ctx",
    "path",
    "env",
    "fs",
    "config",
    "arg",
    "poll",
    "http",
    "proc",
    # output
    "step",
    "info",
    "note",
    "warn",
    "echo",
    "paint",
    "confirm",
    # types
    "Task",
    "Group",
    "Context",
    "Result",
    "Arg",
    "registry",
    # errors
    "MakeError",
    "UsageError",
    "TaskError",
    "ConfigError",
    "ToolMissing",
    "CommandFailed",
    "Aborted",
    "WaitTimeout",
]


def main(argv: list[str] | None = None) -> int:
    """Programmatic entry point, equivalent to running `make` on the command line."""
    from .cli import main as _main

    return _main(argv)
