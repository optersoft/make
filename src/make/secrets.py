"""The encrypted secret store: `age` files, with the identity held out of reach.

A dotenv layer is a file a task reads. A *secret* layer is the same thing
encrypted to an `age` recipient, with the private identity somewhere the
filesystem alone will not give up -- a locking macOS keychain, by default.
Nothing here is a new format: decrypt one and you get exactly the
`KEY=value` text `make.env` already parses.

    ~/.make/secrets/recipient.txt   the age public key. Not a secret.
    ~/.make/secrets/global.age      every project, like `secrets.env`
    ~/.make/secrets/<repo>.age      one project, like `<repo>.env`
    ~/.make/secrets/files/<name>.age  a whole file: a keystore, a service-account JSON

The layering, and the rule that a caller's exported variable still wins, are
`make.env`'s -- this module only supplies the values. What it adds is that a
value which came from here is *marked sensitive*, so the runner's own output
cannot print it, and it is exported only to the task that asked for it.

Three deliberate choices:

* **The recipient is plaintext, the identity is not.** Encrypting needs only
  the public key, so `secure.set` can add a secret without unlocking anything.
  Only reading costs a prompt -- which is the direction that should be
  expensive.
* **`age` is a subprocess, not a library.** No dependency is added to a runner
  whose startup budget is 150ms, and the files stay decryptable by hand with
  one documented command if this code is ever in the way.
* **The identity never appears in an argv or a temporary file.** It goes to
  `age` on stdin, so it is not in `ps` output and never touches the disk in
  cleartext.
"""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
from pathlib import Path

from .context import debug, mark_sensitive
from .errors import ConfigError, MakeError, ToolMissing

__all__ = [
    "available",
    "file",
    "get",
    "identity",
    "keychain",
    "layer_files",
    "layered",
    "load",
    "recipient",
    "store_dir",
    "write",
]

#: Keychain item coordinates. `-s` service, `-a` account.
SERVICE = "mkrun"
ACCOUNT = "age-identity"

#: Cache of decrypted layers, keyed by absolute path. A task file that calls
#: `env.layered()` in five tasks unlocks once per process, not five times.
_cache: dict[Path, dict[str, str]] = {}
_files: dict[Path, Path] = {}
_identity: str | None = None


def store_dir() -> Path:
    """Where the encrypted layers live. `MAKE_SECRETS_DIR` overrides it."""
    override = os.environ.get("MAKE_SECRETS_DIR")
    if override:
        return Path(override).expanduser()
    from .env import config_dir

    return config_dir() / "secrets"


def keychain() -> str | None:
    """Path of the keychain holding the identity, or None for the login one.

    A separate keychain is the point of the design: it can be set to lock on
    sleep and after a timeout without doing that to everything else the login
    keychain holds. `MAKE_KEYCHAIN` overrides the location; an explicit empty
    value means the login keychain.
    """
    if "MAKE_KEYCHAIN" in os.environ:
        return os.environ["MAKE_KEYCHAIN"] or None
    default = Path.home() / "Library" / "Keychains" / "mkrun.keychain-db"
    return str(default) if default.exists() else None


def recipient() -> str:
    """The age public key to encrypt to."""
    path = store_dir() / "recipient.txt"
    if not path.is_file():
        raise ConfigError(
            f"no age recipient at {path}", hint="run `mk secure.init` to create the store and its identity"
        )
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("age1"):
            return line
    raise ConfigError(f"{path} contains no age public key (a line starting with `age1`)")


def identity() -> str:
    """The age private key, from the keychain -- or a file, off macOS.

    Order: `MAKE_AGE_IDENTITY` (a path, or the key itself, for CI), then the
    keychain, then `~/.config/make/age.key`. Cached for the process, so one
    unlock covers a whole `mk` invocation however many layers it reads.
    """
    global _identity
    if _identity is not None:
        return _identity

    override = os.environ.get("MAKE_AGE_IDENTITY")
    if override:
        candidate = Path(override).expanduser()
        text = candidate.read_text(encoding="utf-8") if candidate.is_file() else override
        _identity = _check_identity(text, "MAKE_AGE_IDENTITY")
        return _identity

    from_keychain = _keychain_identity()
    if from_keychain:
        _identity = _check_identity(from_keychain, "the keychain")
        return _identity

    fallback = Path.home() / ".config" / "make" / "age.key"
    if fallback.is_file():
        _identity = _check_identity(fallback.read_text(encoding="utf-8"), str(fallback))
        return _identity

    raise ConfigError(
        "no age identity: the secret store cannot be read",
        hint="on this machine: `mk secure.init` creates one in a keychain that locks.\n"
        "elsewhere (CI, a server): set MAKE_AGE_IDENTITY to the key or a file holding it",
    )


def _check_identity(text: str, origin: str) -> str:
    for line in text.splitlines():
        if line.strip().startswith("AGE-SECRET-KEY-"):
            mark_sensitive(line.strip())
            return line.strip()
    raise ConfigError(f"{origin} holds no age identity (a line starting with `AGE-SECRET-KEY-`)")


def _keychain_identity() -> str | None:
    """Ask the macOS keychain. A locked one prompts here, once."""
    if not shutil.which("security"):
        return None
    argv = ["security", "find-generic-password", "-s", SERVICE, "-a", ACCOUNT, "-w"]
    target = keychain()
    if target:
        argv.append(target)
    # Not through `sh()`: this must not be echoed, must not be skipped by
    # --dry-run, and its stdout is the secret itself.
    done = subprocess.run(argv, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        debug(f"secrets: keychain lookup failed ({done.stderr.strip() or done.returncode})")
        return None
    return done.stdout.strip() or None


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def _age(*args: str, input_text: str, stdin_identity: bool = True) -> bytes:
    """Run `age`, feeding the identity on stdin so it stays out of argv."""
    if not shutil.which("age"):
        raise ToolMissing(
            "`age` is not on PATH, and the secret store is encrypted with it",
            hint="brew install age  (or https://age-encryption.org)",
        )
    done = subprocess.run(["age", *args], input=input_text.encode(), capture_output=True, check=False)
    if done.returncode != 0:
        message = done.stderr.decode(errors="replace").strip()
        raise MakeError(
            f"age failed: {message or done.returncode}",
            hint="the identity may not match the recipient this file was encrypted to; "
            "`mk secure.doctor` compares them"
            if stdin_identity
            else None,
        )
    return done.stdout


def decrypt(path: str | Path) -> str:
    """Decrypt one `.age` file to text. Unlocks the identity if it has to."""
    target = Path(path).expanduser()
    if not target.is_file():
        raise ConfigError(f"no such secret file: {target}")
    debug(f"secrets: decrypting {target}")
    return _age("-d", "-i", "-", str(target), input_text=identity()).decode()


def load(path: str | Path) -> dict[str, str]:
    """Decrypt and parse one layer. Missing is empty, as with a plain layer.

    Every value is marked sensitive on the way out, whatever it is called: a
    name-based guess is right for `*_PASSWORD` and wrong for the one that
    matters, and everything in here was put here deliberately.
    """
    target = Path(path).expanduser()
    if target in _cache:
        return dict(_cache[target])
    if not target.is_file():
        return {}
    from .env import parse

    values = parse(decrypt(target))
    mark_sensitive(*values.values())
    _cache[target] = values
    return dict(values)


def layer_files(repo: str | None = None) -> list[Path]:
    """The encrypted layers that exist, in precedence order (last wins)."""
    from .env import repo_name

    directory = store_dir()
    name = repo or repo_name()
    return [p for p in (directory / "global.age", directory / f"{name}.age") if p.is_file()]


def layered(repo: str | None = None) -> dict[str, str]:
    """Merge the encrypted layers. Nothing is exported: the caller decides."""
    merged: dict[str, str] = {}
    for path in layer_files(repo):
        merged.update(load(path))
    return merged


def available(repo: str | None = None) -> bool:
    """True when there is an encrypted layer to read at all."""
    return bool(layer_files(repo))


def get(key: str, repo: str | None = None) -> str | None:
    """One value from the encrypted layers, or None."""
    return layered(repo).get(key)


def file(name: str, *, repo: str | None = None) -> Path:
    """Decrypt a whole file -- a keystore, a service-account JSON -- to a temp path.

    Written 0600 in a private directory and removed when the process exits, so
    a task hands the path to a tool that insists on one (gradle's signing
    config, `fastlane supply --json_key`) without the cleartext ever living in
    `~/.make`.
    """
    del repo  # files are global; a per-repo one would be `<repo>-<name>`
    source = store_dir() / "files" / f"{name}.age"
    if not source.is_file():
        raise ConfigError(
            f"no secret file named {name!r} ({source} does not exist)",
            hint=f"add one with `mk secure.add-file {name} <path>`",
        )
    if source in _files and _files[source].is_file():
        return _files[source]

    import tempfile

    directory = Path(tempfile.mkdtemp(prefix="mk-secret-"))
    os.chmod(directory, 0o700)
    target = directory / name
    data = _age("-d", "-i", "-", str(source), input_text=identity())
    target.write_bytes(data)
    os.chmod(target, 0o600)
    _files[source] = target

    def _clean(directory: Path = directory) -> None:
        shutil.rmtree(directory, ignore_errors=True)

    atexit.register(_clean)
    return target


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


def encrypt(text: str | bytes, target: str | Path) -> Path:
    """Encrypt to the store's recipient. Needs no identity, so no unlock."""
    path = Path(target).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not shutil.which("age"):
        raise ToolMissing("`age` is not on PATH", hint="brew install age")
    payload = text.encode() if isinstance(text, str) else text
    done = subprocess.run(
        ["age", "-r", recipient(), "-o", str(path)], input=payload, capture_output=True, check=False
    )
    if done.returncode != 0:
        raise MakeError(f"age failed: {done.stderr.decode(errors='replace').strip()}")
    os.chmod(path, 0o600)
    _cache.pop(path, None)
    return path


def write(name: str, values: dict[str, str], *, directory: Path | None = None) -> Path:
    """Replace one layer with `values`, as sorted `KEY=value` lines."""
    body = "".join(f"{key}={values[key]}\n" for key in sorted(values))
    return encrypt(body, (directory or store_dir()) / f"{name}.age")


def reset_cache() -> None:
    """Forget decrypted layers and the identity. For tests, and after a rekey."""
    global _identity
    _cache.clear()
    _files.clear()
    _identity = None
