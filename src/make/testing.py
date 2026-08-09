"""Testing recipes.

The single largest thing `just` cannot offer. A 499-line `web.just` holds port
arithmetic, two differently-scoped process reaps and a four-layer environment
precedence chain, and the only available check is `just --fmt --check` -- which
verifies that it parses. Every bug that file has shipped was in the logic, not
the syntax.

Because a recipe here is a function, the same logic is testable directly:

    from make.testing import record
    from acme_recipes import web

    def test_start_reaps_a_stale_lock_holder():
        with record(responses={"lsof -t": "4711"}) as rec:
            web.start(port=8105)
        assert rec.saw("kill", "-9", "4711")
        assert rec.matched(r"dx serve .*--port 8105")
"""

from __future__ import annotations

import re
import shlex
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any

from .context import Context, using

# `from . import sh` would bind the `sh` *object* re-exported by `make/__init__`,
# not the module that holds the `shutil` reference we need to patch.
sh_module = import_module("make.sh")

__all__ = ["Recorder", "context", "record", "run_cli"]


@dataclass
class Recorder:
    """Every command a recipe attempted, without running any of them."""

    commands: list[list[str]] = field(default_factory=list)
    responses: dict[str, str] = field(default_factory=dict)
    failures: dict[str, int] = field(default_factory=dict)
    missing_tools: set[str] = field(default_factory=set)

    # -- assertions --------------------------------------------------------

    @property
    def lines(self) -> list[str]:
        return [shlex.join(command) for command in self.commands]

    def saw(self, *fragment: str) -> bool:
        """True when some command contains these tokens, in order and adjacent."""
        wanted = [str(token) for token in fragment]
        span = len(wanted)
        for command in self.commands:
            for start in range(len(command) - span + 1):
                if command[start : start + span] == wanted:
                    return True
        return False

    def matched(self, pattern: str) -> bool:
        """True when some command matches this regular expression."""
        compiled = re.compile(pattern)
        return any(compiled.search(line) for line in self.lines)

    def argument_matched(self, pattern: str) -> bool:
        """True when a single *argument* matches, before any shell quoting.

        Use this for payloads handed to `ssh` or `sh -c`: `lines` runs them
        through `shlex.join`, which escapes the inner quotes, so a regex written
        against the script as authored will not match there.
        """
        compiled = re.compile(pattern)
        return any(compiled.search(argument) for command in self.commands for argument in command)

    def count(self, *fragment: str) -> int:
        wanted = [str(token) for token in fragment]
        span = len(wanted)
        total = 0
        for command in self.commands:
            for start in range(len(command) - span + 1):
                if command[start : start + span] == wanted:
                    total += 1
                    break
        return total

    def only(self, program: str) -> list[list[str]]:
        return [c for c in self.commands if c and Path(c[0]).name == program]

    # -- canned behaviour --------------------------------------------------

    def respond(self, fragment: str, stdout: str) -> Recorder:
        """Give a canned stdout to any command whose text contains `fragment`."""
        self.responses[fragment] = stdout
        return self

    def fail(self, fragment: str, code: int = 1) -> Recorder:
        self.failures[fragment] = code
        return self

    def _lookup(self, line: str) -> tuple[int, str]:
        for fragment, code in self.failures.items():
            if fragment in line:
                return code, ""
        for fragment, out in self.responses.items():
            if fragment in line:
                return 0, out
        return 0, ""


@contextmanager
def record(
    *,
    responses: Mapping[str, str] | None = None,
    failures: Mapping[str, int] | None = None,
    missing_tools: Sequence[str] = (),
) -> Iterator[Recorder]:
    """Capture commands instead of executing them.

    Tools are reported present by default, so `requires=` does not have to be
    satisfied by the machine running the tests; list any that should appear
    missing in `missing_tools`.
    """
    recorder = Recorder(
        responses=dict(responses or {}), failures=dict(failures or {}), missing_tools=set(missing_tools)
    )

    real_run = subprocess.run
    real_which = sh_module.shutil.which
    real_popen = subprocess.Popen

    def fake_run(argv: Any, *args: Any, **kwargs: Any) -> Any:
        command = [str(part) for part in argv] if isinstance(argv, (list, tuple)) else [str(argv)]
        recorder.commands.append(command)
        code, out = recorder._lookup(shlex.join(command))
        return subprocess.CompletedProcess(command, code, out, "")

    def fake_which(tool: str, *args: Any, **kwargs: Any) -> str | None:
        if tool in recorder.missing_tools:
            return None
        return f"/usr/bin/{tool}"

    def fake_popen(argv: Any, *args: Any, **kwargs: Any) -> Any:
        command = [str(part) for part in argv] if isinstance(argv, (list, tuple)) else [str(argv)]
        recorder.commands.append(command)

        class _Fake:
            pid = 4242

            def poll(self) -> int | None:
                return None

            def wait(self, timeout: float | None = None) -> int:
                return 0

            def terminate(self) -> None:
                return None

            kill = terminate

        return _Fake()

    subprocess.run = fake_run  # type: ignore[assignment]
    subprocess.Popen = fake_popen  # type: ignore[assignment]
    sh_module.shutil.which = fake_which  # type: ignore[assignment]
    try:
        yield recorder
    finally:
        subprocess.run = real_run  # type: ignore[assignment]
        subprocess.Popen = real_popen  # type: ignore[assignment]
        sh_module.shutil.which = real_which  # type: ignore[assignment]


@contextmanager
def context(root: str | Path | None = None, **overrides: Any) -> Iterator[Context]:
    """Run a block under a specific `ctx` -- root directory, dry_run, yes, ..."""
    base = Path(root) if root else Path.cwd()
    fresh = Context(root=base, invocation_dir=base, color=False, **overrides)
    with using(fresh) as active:
        yield active


def run_cli(args: Sequence[str], *, recipe_file: str | Path | None = None) -> int:
    """Invoke the command line in-process, for end-to-end tests."""
    from .cli import main

    argv = list(args)
    if recipe_file:
        argv = ["--file", str(recipe_file), *argv]
    return main(argv)
