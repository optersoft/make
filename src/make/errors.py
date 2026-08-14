"""Exception hierarchy.

Everything the user can plausibly hit is a `MakeError`: the CLI prints its
message and exits with `exit_code`, no traceback. A traceback escaping to the
terminal therefore means a bug in `make` itself, not a mistake in a task --
which is the whole point of keeping this hierarchy narrow.
"""

from __future__ import annotations


class MakeError(Exception):
    """Base for every expected failure. Printed without a traceback."""

    exit_code = 1

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class UsageError(MakeError):
    """Bad command line: unknown task, bad flag, missing argument."""

    exit_code = 2


class TaskError(MakeError):
    """A task is malformed, duplicated, or cannot be registered."""


class ConfigError(MakeError):
    """A required configuration value is missing or the wrong type."""


class ToolMissing(MakeError):
    """A tool declared in `requires=` is not on PATH."""

    exit_code = 127


class CommandFailed(MakeError):
    """A subprocess exited non-zero under `check=True`."""

    def __init__(self, argv: list[str], returncode: int, output: str | None = None) -> None:
        self.argv = argv
        self.returncode = returncode
        self.output = output
        import shlex

        message = f"command failed (exit {returncode}): {shlex.join(argv)}"
        if output:
            tail = output.strip().splitlines()[-10:]
            if tail:
                message += "\n" + "\n".join("  " + line for line in tail)
        super().__init__(message)
        self.exit_code = returncode or 1


class Aborted(MakeError):
    """The user declined a confirmation prompt, or pressed Ctrl-C."""

    exit_code = 130
