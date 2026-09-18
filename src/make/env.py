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

from .context import current, debug, mark_sensitive
from .errors import ConfigError

__all__ = ["config_dir", "layered", "load", "parse", "repo_name", "require", "secret", "sensitive"]

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

#: A plaintext layer mixes configuration with credentials -- `ANDROID_KEY_ALIAS`
#: and `KEYSTORE_PASSWORD` sit in the same file -- and only the second kind must
#: be kept out of a child process's environment and out of the terminal. The
#: name is the only signal available, so these are the endings that mean
#: "credential", and the endings that override them because they name a
#: *location* of one: `PLAY_ACCOUNT_JSON` is a path, `ANDROID_KEY_ALIAS` is a
#: label. A value from the encrypted store is sensitive regardless of its name;
#: this heuristic exists only for the plaintext layers it replaces.
_SENSITIVE_SUFFIXES = ("PASSWORD", "PASSPHRASE", "SECRET", "TOKEN", "KEY", "CREDENTIAL", "CREDENTIALS")
_LOCATION_SUFFIXES = ("_PATH", "_FILE", "_DIR", "_ID", "_ALIAS", "_URL", "_HOST", "_JSON", "_NAME")


def sensitive(key: str) -> bool:
    """True when a variable's *name* says it holds a credential."""
    upper = key.upper()
    if upper.endswith(_LOCATION_SUFFIXES):
        return False
    return upper.endswith(_SENSITIVE_SUFFIXES)


#: Directories searched for `secrets.env` / `<repo>.env`, in order. The
#: `~/.just` entry is deliberate migration support: if you are coming from
#: `just`, your credentials already live there, and a tool that demands you move
#: your secrets before it will run is a tool nobody adopts.
#:
#: `MAKE_CONFIG_DIR` replaces the whole list -- the hermeticity valve. A test
#: harness (including this repo's own) must not inherit the developer's real
#: `~/.make/sources.toml` and secrets through a spawned `mk`.
_configured = os.environ.get("MAKE_CONFIG_DIR")
CONFIG_DIRS = (
    (Path(_configured).expanduser(),) if _configured else (Path.home() / ".make", Path.home() / ".just")
)


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

    # The encrypted store is deliberately NOT read here. Reading it unlocks a
    # keychain, and `layered()` is called by tasks that want configuration --
    # a port, a directory -- which would make a passphrase prompt appear in
    # front of work that needs no credential at all. `require`/`secret` reach
    # the store, on demand, for the task that actually asks.

    for key, value in merged.items():
        if sensitive(key):
            mark_sensitive(value)

    if export:
        context = current()
        updated = dict(context.env)
        for key, value in merged.items():
            if sensitive(key):
                if override:
                    _forced[key] = value
                # Held back deliberately. Exporting the whole layer handed every
                # child process every credential in it: `mk android.release` gave
                # gradle, fastlane and their plugins the trading account's
                # password. A task gets a secret by asking -- `env.require`,
                # `env.secret`, or `@task(secrets=[...])` -- and then only that
                # task's children see it.
                _vault[key] = value
                continue
            if not override and key in os.environ and key not in context.env:
                continue  # an explicit export from the caller outranks a file
            updated[key] = value
        from .context import set_context

        set_context(context.with_(env=updated))
    else:
        for key, value in merged.items():
            if sensitive(key):
                (_forced if override else _vault)[key] = value
    return merged


#: Sensitive values that a layer supplied but which were not exported. Held for
#: `require`/`secret` to hand out on request, so reading a layer is not the same
#: act as publishing it to every subprocess.
_vault: dict[str, str] = {}

#: Sensitive values a layer was read with `override=True`, which is an
#: instruction that the file outranks even an exported variable. Without this
#: they would land in `_vault`, which `secret` consults *after* the environment
#: -- and `override=` would silently do nothing for exactly the variables it is
#: usually reached for.
_forced: dict[str, str] = {}


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
    """Look a variable up in the run context first, then the real environment.

    A credential a layer supplied this run is visible here even though it was
    not exported -- `get` is a lookup, not a hand-off, and a task that reads one
    and passes it on deliberately (`sh(..., env={...})`) is doing the right
    thing. What `get` will not do is reach the encrypted store: a keychain
    prompt should never happen behind a non-committal lookup. Ask for a
    credential with `secret` or `require`.
    """
    context = current()
    if key in context.env:
        return context.env[key]
    if key in os.environ:
        return os.environ[key]
    if key in _forced or key in _vault:
        return _forced.get(key) or _vault[key]
    return default


def secret(key: str, *, export_to_children: bool = True) -> str | None:
    """A credential, from wherever it lives. None when there is none.

    Order: the caller's environment, then this run's context, then a layer
    already read, then the encrypted store. The value is masked in everything
    `make` prints from here on, and -- unless you say otherwise -- exported for
    this task's child processes, because a task that asks for a secret is
    normally about to hand it to a tool.
    """
    found = _forced.get(key) or os.environ.get(key) or current().env.get(key) or _vault.get(key)
    if found is None:
        from . import secrets as secret_store

        if secret_store.available():
            found = secret_store.layered().get(key)
    if found is None:
        return None
    mark_sensitive(found)
    if export_to_children:
        from .context import set_context

        context = current()
        if context.env.get(key) != found:
            set_context(context.with_(env={**context.env, key: found}))
    return found


def require(key: str, *, hint: str | None = None) -> str:
    """Fetch a variable or fail with a message naming where to put it.

    Reaches the encrypted store for anything whose name says it is a
    credential, so a task written before the store existed keeps working after
    its value moves into one.
    """
    value = secret(key) if sensitive(key) else get(key)
    if value:
        return value
    from . import secrets as secret_store

    where = (
        f"`mk secure.set {key} <value>` puts it in the encrypted store"
        if sensitive(key)
        else f"add {key}=... to {config_dir()}/secrets.env (global) or "
        f"{config_dir()}/{repo_name()}.env (this project)"
    )
    del secret_store
    raise ConfigError(f"{key} is not set", hint=hint or where + ", then re-run")


def reset_cache() -> None:
    """Forget held-back sensitive values. For tests."""
    _vault.clear()
    _forced.clear()
