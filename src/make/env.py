"""Layered environment and secrets.

One implementation of a rule that `just` forces you to write once per task
body, because its settings cannot reach `$HOME`:

    ~/.make/secrets.env     global, every project
    ~/.make/<repo>.env      per project, overrides global
    ./.env                  per checkout -- dev config, not secrets; overrides both

Two details here are load-bearing and were both learned the hard way:

* `<repo>` is resolved through `git rev-parse --git-common-dir`, which points at
  the *main* checkout even from inside a linked worktree. `--show-toplevel`
  returns the worktree directory, so a worktree at `.../myapp/worktrees/feature`
  looks up `~/.make/feature.env`, finds nothing, and starts with no application
  environment at all -- silently, since a missing file is not an error.
* Later layers override earlier ones, but an explicitly exported variable in the
  real environment still wins over all of them unless `override=True`. Config
  files describe defaults for a machine; the caller's `FOO=1 make ...` is an
  instruction.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path

from .context import current, debug
from .errors import ConfigError

__all__ = ["config_dir", "layered", "load", "parse", "repo_name", "require"]

_LINE = re.compile(
    r"""^\s*
        (?:export\s+)?
        (?P<key>[A-Za-z_][A-Za-z0-9_]*)
        \s*=\s*
        (?P<value>.*?)
        \s*$""",
    re.VERBOSE,
)

_INTERPOLATION = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")

#: Directories searched for `secrets.env` / `<repo>.env`, in order. The
#: `~/.just` entry is deliberate migration support: if you are coming from
#: `just`, your credentials already live there, and a tool that demands you move
#: your secrets before it will run is a tool nobody adopts.
CONFIG_DIRS = (Path.home() / ".make", Path.home() / ".just")


def config_dir() -> Path:
    """The directory new secrets should be written to."""
    return CONFIG_DIRS[0]


def parse(text: str, *, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Parse dotenv-ish text: `KEY=value`, optional `export`, quotes, `#` comments."""
    values: dict[str, str] = dict(base or {})
    parsed: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE.match(line)
        if not match:
            continue
        key = match.group("key")
        value = match.group("value")

        if value.startswith("'") and value.endswith("'") and len(value) >= 2:
            value = value[1:-1]  # single quotes: literal, no interpolation
        else:
            if value.startswith('"') and value.endswith('"') and len(value) >= 2:
                value = value[1:-1]
                value = value.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
            else:
                value = value.split(" #", 1)[0].rstrip()
            value = _interpolate(value, values)

        values[key] = value
        parsed[key] = value
    return parsed


def _interpolate(value: str, known: Mapping[str, str]) -> str:
    def resolve(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        if name in known:
            return known[name]
        return os.environ.get(name, "")

    return _INTERPOLATION.sub(resolve, value)


def load(path: str | Path, *, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Parse one env file. A missing file is empty, not an error."""
    file = Path(path).expanduser()
    if not file.is_file():
        return {}
    debug(f"env: reading {file}")
    return parse(file.read_text(encoding="utf-8"), base=base)


def repo_name(start: str | Path | None = None) -> str:
    """Name of the *main* checkout, correct from inside a linked worktree."""
    cwd = Path(start) if start else current().root
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return Path(cwd).resolve().name
    if not out:
        return Path(cwd).resolve().name
    return Path(out).parent.name


def layer_files(repo: str | None = None, *, local: bool = True) -> list[Path]:
    """The env files that would be read, in precedence order (last wins)."""
    name = repo or repo_name()
    files: list[Path] = []
    for directory in CONFIG_DIRS:
        if (directory / "secrets.env").is_file():
            files.append(directory / "secrets.env")
    for directory in CONFIG_DIRS:
        if (directory / f"{name}.env").is_file():
            files.append(directory / f"{name}.env")
    if local:
        local_file = current().root / ".env"
        if local_file.is_file():
            files.append(local_file)
    return files


def layered(
    repo: str | None = None,
    *,
    files: Iterable[str | Path] | None = None,
    local: bool = True,
    export: bool = True,
    override: bool = False,
) -> dict[str, str]:
    """Merge the layers and, by default, make them visible to every `sh()` call.

    `export=True` writes into the run context rather than `os.environ`, so a
    library caller does not have its process environment mutated behind its back
    and parallel tasks cannot race each other's exports.
    """
    paths = [Path(f).expanduser() for f in files] if files is not None else layer_files(repo, local=local)
    merged: dict[str, str] = {}
    for path in paths:
        merged.update(load(path, base=merged))

    if export:
        context = current()
        updated = dict(context.env)
        for key, value in merged.items():
            if not override and key in os.environ and key not in context.env:
                continue  # an explicit export from the caller outranks a file
            updated[key] = value
        from .context import set_context

        set_context(context.with_(env=updated))
    return merged


def export(force: bool = False, **values: object) -> None:
    """Add variables to the environment every later `sh()` call sees.

    Defaults to *not* overriding something already set, matching the
    `${VAR:-default}` idiom these blocks are usually translated from: a value
    the caller exported, or one that came from a secrets layer, wins.

        env.export(AUTH_COOKIE_SECURE="false", STORE_DIR=str(dev / "store"))
        env.export(force=True, DB_PATH=str(dev / "app.db"))

    Writes to the run context rather than `os.environ`, so a library caller's
    process environment is not mutated behind its back.
    """
    from .context import set_context

    context = current()
    updated = dict(context.env)
    for key, value in values.items():
        if not force and (key in updated or key in os.environ):
            continue
        updated[key] = str(value)
    set_context(context.with_(env=updated))


def get(key: str, default: str | None = None) -> str | None:
    """Look a variable up in the run context first, then the real environment."""
    context = current()
    if key in context.env:
        return context.env[key]
    return os.environ.get(key, default)


def require(key: str, *, hint: str | None = None) -> str:
    """Fetch a variable or fail with a message naming where to put it."""
    value = get(key)
    if value:
        return value
    raise ConfigError(
        f"{key} is not set",
        hint=hint
        or f"add {key}=... to {config_dir()}/secrets.env (global) or "
        f"{config_dir()}/{repo_name()}.env (this project), then re-run",
    )
