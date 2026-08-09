"""Running commands.

The default form takes an argv *list*, never a string:

    sh("aws", "s3", "ls", "--profile", profile, path)

There is no string interpolation step, so a value containing a space, a quote or
a `$` is data and cannot become syntax. `just`'s `{{ }}` splices text into bash
source before the shell parses it, which is why its recipes are littered with
manual `quote()` calls and defensive double-quoting. Here the hazard is absent
by construction, and a shell is only involved when you explicitly ask for one
via `sh.pipe()` / `sh.bash()`.

Every entry point honours `ctx.dry_run`, so `--dry-run` reports what *would*
run rather than expanding text.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from .context import current, echo, paint
from .errors import CommandFailed, ToolMissing

__all__ = ["Result", "sh"]


@dataclass(frozen=True)
class Result:
    """Outcome of one command."""

    argv: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    skipped: bool = False
    """True when the command was not executed because of `--dry-run`."""

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def out(self) -> str:
        return self.stdout.strip()

    @property
    def lines(self) -> list[str]:
        return [line for line in self.stdout.splitlines() if line.strip()]

    def __bool__(self) -> bool:
        return self.ok


def _stringify(args: Sequence[Any]) -> list[str]:
    argv: list[str] = []
    for arg in args:
        if arg is None:
            continue
        if isinstance(arg, (list, tuple)):
            argv.extend(_stringify(arg))
        elif isinstance(arg, Path):
            argv.append(str(arg))
        elif isinstance(arg, bool):
            argv.append("true" if arg else "false")
        else:
            argv.append(str(arg))
    return argv


def _merged_env(extra: Mapping[str, str] | None) -> dict[str, str] | None:
    context = current()
    if not context.env and not extra:
        return None
    merged = dict(os.environ)
    merged.update(context.env)
    if extra:
        merged.update({k: str(v) for k, v in extra.items()})
    return merged


def _echo_command(display: str, *, skipped: bool) -> None:
    context = current()
    if context.quiet and not skipped:
        return
    prefix = paint("[dry-run] ", "yellow") if skipped else ""
    echo(prefix + paint("$ ", "cyan", "bold") + paint(display, "cyan"))


class _Sh:
    """Callable namespace: `sh(...)` plus the `sh.out` / `sh.ok` / ... helpers."""

    def __call__(
        self,
        *args: Any,
        check: bool = True,
        capture: bool = False,
        cwd: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        input: str | None = None,
        stdin: IO[Any] | int | None = None,
        echo_cmd: bool = True,
        dry_result: Result | None = None,
        timeout: float | None = None,
        text: bool = True,
    ) -> Result:
        argv = _stringify(args)
        if not argv:
            raise ValueError("sh() needs at least one argument")
        display = shlex.join(argv)

        if current().dry_run:
            if echo_cmd:
                _echo_command(display, skipped=True)
            return dry_result or Result(argv=argv, returncode=0, skipped=True)

        if echo_cmd:
            _echo_command(display, skipped=False)

        try:
            completed = subprocess.run(
                argv,
                cwd=str(cwd) if cwd else None,
                env=_merged_env(env),
                input=input,
                stdin=stdin,
                capture_output=capture,
                text=text,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ToolMissing(
                f"{argv[0]!r} is not on PATH",
                hint="declare it with @recipe(requires=[...]) so this fails before "
                "anything else runs, or install it first",
            ) from exc

        result = Result(
            argv=argv,
            returncode=completed.returncode,
            stdout=completed.stdout or "" if capture else "",
            stderr=completed.stderr or "" if capture else "",
        )
        if check and not result.ok:
            raise CommandFailed(argv, result.returncode, result.stdout + result.stderr)
        return result

    # -- captured variants -------------------------------------------------

    def out(self, *args: Any, dry: str = "", strip: bool = True, **kwargs: Any) -> str:
        """stdout as text. `dry=` is the stand-in value under --dry-run.

        Supply `dry=` whenever the value steers later logic -- an empty string
        silently taking a different branch is the classic way a dry run stops
        resembling the real one.
        """
        kwargs.setdefault("check", True)
        result = self(*args, capture=True, **kwargs)
        if result.skipped:
            return dry
        return result.stdout.strip() if strip else result.stdout

    def lines(self, *args: Any, dry: Sequence[str] = (), **kwargs: Any) -> list[str]:
        text = self.out(*args, dry="\n".join(dry), **kwargs)
        return [line for line in text.splitlines() if line.strip()]

    def ok(self, *args: Any, dry: bool = True, **kwargs: Any) -> bool:
        """True when the command exits 0. Never raises on non-zero.

        `dry` is the answer under --dry-run. It defaults to True because most
        uses are presence checks ("is this tool installed") where assuming yes
        keeps the dry run going. Pass `dry=False` when a True would make the
        recipe report work it would not actually do -- `sh.ok("lsof", ...)`
        deciding that a port is busy, for instance.
        """
        kwargs.setdefault("echo_cmd", False)
        result = self(*args, check=False, capture=True, **kwargs)
        if result.skipped:
            return dry
        return result.ok

    def code(self, *args: Any, **kwargs: Any) -> int:
        return self(*args, check=False, **kwargs).returncode

    # -- explicit shell ----------------------------------------------------

    def pipe(self, script: str, **kwargs: Any) -> Result:
        """Run a shell pipeline. Marked explicitly because it *is* the hazard."""
        return self._shell(script, login_shell=False, **kwargs)

    def bash(self, script: str, *, strict: bool = True, **kwargs: Any) -> Result:
        """Run a multi-line bash script, `set -euo pipefail` by default.

        Mostly a migration aid: it lets a `just` recipe body move across
        verbatim so behaviour can be compared, then be dismantled into typed
        Python one piece at a time.
        """
        body = "set -euo pipefail\n" + script if strict else script
        return self._shell(body, login_shell=True, **kwargs)

    def _shell(self, script: str, *, login_shell: bool, **kwargs: Any) -> Result:
        shell = shutil.which("bash") if login_shell else None
        argv = [shell or "/bin/sh", "-c", script]
        kwargs.setdefault("echo_cmd", False)
        if not current().quiet or current().dry_run:
            _echo_command(script.strip().replace("\n", " ; "), skipped=current().dry_run)
        return self(*argv, **kwargs)

    # -- environment -------------------------------------------------------

    def which(self, tool: str) -> str | None:
        return shutil.which(tool)

    def require(self, *tools: str, hint: str | None = None) -> None:
        """Fail loudly, up front, for every missing tool -- not one per attempt."""
        missing = [tool for tool in tools if shutil.which(tool) is None]
        if missing:
            names = ", ".join(missing)
            raise ToolMissing(
                f"required tool{'s' if len(missing) > 1 else ''} not on PATH: {names}", hint=hint
            )

    def background(
        self,
        *args: Any,
        cwd: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        log: str | Path | None = None,
    ) -> subprocess.Popen[Any] | None:
        """Spawn a detached child (dev servers, watchers). None under --dry-run."""
        argv = _stringify(args)
        display = shlex.join(argv)
        if current().dry_run:
            _echo_command(display + "  &", skipped=True)
            return None
        _echo_command(display + "  &", skipped=False)
        stream: IO[Any] | int
        if log:
            Path(log).parent.mkdir(parents=True, exist_ok=True)
            stream = open(log, "ab")  # noqa: SIM115 - owned by the child
        else:
            stream = subprocess.DEVNULL
        return subprocess.Popen(
            argv,
            cwd=str(cwd) if cwd else None,
            env=_merged_env(env),
            stdout=stream,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

    def replace_process(self, *args: Any, env: Mapping[str, str] | None = None) -> None:
        """`exec` into a command, replacing this process. Never returns."""
        argv = _stringify(args)
        if current().dry_run:
            _echo_command("exec " + shlex.join(argv), skipped=True)
            return
        _echo_command("exec " + shlex.join(argv), skipped=False)
        merged = _merged_env(env) or dict(os.environ)
        executable = shutil.which(argv[0])
        if executable is None:
            raise ToolMissing(f"{argv[0]!r} is not on PATH")
        os.execve(executable, argv, merged)


sh = _Sh()
