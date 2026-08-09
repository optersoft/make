"""Making shared recipes an ordinary dependency.

This is the feature the whole project exists for. `just` shares recipes by
`git clone --depth 1` into a gitignored directory, driven by a `_shared` recipe
copy-pasted into every consumer -- no versions, no pinning, no lockfile, and a
`git pull --ff-only || true` that fails silently and leaves you on whatever HEAD
happened to be there.

Here a recipe file declares its dependencies inline, in PEP 723 form:

    # /// script
    # requires-python = ">=3.11"
    # dependencies = ["mkrun>=0.1", "acme-recipes>=0.4"]
    # ///

If the current interpreter already satisfies them, nothing happens -- that is
the common case in a project with a synced virtualenv, and it costs one
`importlib.metadata` lookup per dependency. Otherwise `make` re-executes itself
under `uv run`, which resolves into a cached environment. Pin it with
`make --sync` (`uv lock --script`).

The satisfaction check errs toward re-executing: a specifier it cannot parse
counts as unsatisfied. Being needlessly slow is recoverable; running a recipe
against the wrong version of its library is not.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .context import debug, note
from .errors import MakeError

__all__ = ["ScriptMetadata", "needs_bootstrap", "read_metadata", "reexec"]

BOOTSTRAP_FLAG = "_MAKE_BOOTSTRAPPED"

# The regex given in PEP 723 itself.
_BLOCK = re.compile(r"(?m)^# /// (?P<type>[a-zA-Z0-9-]+)$\s(?P<content>(^#(| .*)$\s)+)^# ///$")

_REQUIREMENT = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?P<extras>\[[^\]]*\])?"
    r"\s*(?P<spec>.*)$"
)

_CLAUSE = re.compile(r"(?P<op>==|!=|>=|<=|~=|>|<)\s*(?P<version>[0-9A-Za-z.*+!-]+)")


@dataclass
class ScriptMetadata:
    """The `# /// script` block of a recipe file."""

    dependencies: list[str] = field(default_factory=list)
    requires_python: str | None = None
    raw: dict = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not self.dependencies and not self.requires_python


def read_metadata(path: Path) -> ScriptMetadata:
    """Parse the inline metadata block, if any."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ScriptMetadata()

    for match in _BLOCK.finditer(text):
        if match.group("type") != "script":
            continue
        content = "".join(
            line[2:] if line.startswith("# ") else line[1:]
            for line in match.group("content").splitlines(keepends=True)
        )
        import tomllib

        try:
            data = tomllib.loads(content)
        except Exception as exc:
            raise MakeError(f"{path}: the inline script metadata is not valid TOML -- {exc}") from exc
        return ScriptMetadata(
            dependencies=list(data.get("dependencies", [])),
            requires_python=data.get("requires-python"),
            raw=data,
        )
    return ScriptMetadata()


# --------------------------------------------------------------------------
# Is the current interpreter good enough?
# --------------------------------------------------------------------------


def _version_key(version: str) -> tuple:
    parts: list[object] = []
    for chunk in re.split(r"[._-]", version):
        if chunk.isdigit():
            parts.append((0, int(chunk)))
        elif chunk:
            parts.append((1, chunk))
    return tuple(parts)


def _satisfies(installed: str, op: str, wanted: str) -> bool:
    if op == "~=" or "*" in wanted:
        prefix = wanted.rstrip("*").rstrip(".")
        if op == "~=":
            head = ".".join(wanted.split(".")[:-1])
            return installed.startswith(head) and _version_key(installed) >= _version_key(wanted)
        return installed.startswith(prefix)
    left, right = _version_key(installed), _version_key(wanted)
    return {
        "==": left == right,
        "!=": left != right,
        ">=": left >= right,
        "<=": left <= right,
        ">": left > right,
        "<": left < right,
    }[op]


def _requirement_met(requirement: str) -> bool:
    text = requirement.split("#", 1)[0].strip()
    if not text:
        return True
    if ";" in text:
        return False  # environment markers: let uv decide
    if any(text.startswith(scheme) for scheme in ("http://", "https://", "git+", "file://")):
        return False
    if "@" in text:
        return False  # direct reference (git+ssh://..., local path)

    match = _REQUIREMENT.match(text)
    if match is None:
        return False
    name = match.group("name")
    if match.group("extras"):
        return False  # we cannot tell whether the extra's deps are present

    import importlib.metadata as md

    try:
        installed = md.version(name)
    except md.PackageNotFoundError:
        return False

    spec = match.group("spec").strip()
    if not spec:
        return True
    for clause in spec.split(","):
        clause = clause.strip()
        if not clause:
            continue
        parsed = _CLAUSE.match(clause)
        if parsed is None:
            return False
        if not _satisfies(installed, parsed.group("op"), parsed.group("version")):
            return False
    return True


def needs_bootstrap(metadata: ScriptMetadata) -> bool:
    """True when this interpreter cannot run the recipe file as declared."""
    if os.environ.get(BOOTSTRAP_FLAG):
        return False
    if not metadata.dependencies:
        return False
    for requirement in metadata.dependencies:
        if not _requirement_met(requirement):
            debug(f"bootstrap: {requirement!r} not satisfied by the current interpreter")
            return True
    return False


# --------------------------------------------------------------------------
# Re-exec through uv
# --------------------------------------------------------------------------


#: The distribution name. `make` and `mk` are taken on PyPI by unrelated
#: projects, so the package that provides `import make` is called this.
DISTRIBUTION = "mkrun"


def _requirement_name(requirement: str) -> str:
    """The bare package name of a requirement, however it is written."""
    text = requirement.split(";", 1)[0].strip()
    text = text.split("@", 1)[0].strip()  # "pkg @ git+ssh://..." -> "pkg"
    match = _REQUIREMENT.match(text)
    return (match.group("name") if match else text).replace("_", "-").lower()


def _declares_self(metadata: ScriptMetadata) -> bool:
    """Does the recipe file already say where to get the tool from?"""
    return any(_requirement_name(r) == DISTRIBUTION for r in metadata.dependencies)


def _self_requirement(metadata: ScriptMetadata) -> list[str]:
    """How the child environment should obtain `make` itself.

    Nothing, when the recipe file already declares it. Adding our own on top
    would hand uv two different sources for one package -- a hard error the
    moment the file pins a git URL or a specific version, which is exactly what
    a consumer of a private recipe package does.
    """
    if _declares_self(metadata):
        return []

    override = os.environ.get("MAKE_SELF_REQUIREMENT")
    if override:
        return ["--with", override]

    package_root = Path(__file__).resolve().parent
    project_root = package_root.parent.parent
    if (project_root / "pyproject.toml").is_file() and (project_root / "src" / "make").is_dir():
        return ["--with-editable", str(project_root)]  # running from a checkout

    from . import __version__

    return ["--with", f"{DISTRIBUTION}=={__version__}"]


def lock_path(path: Path) -> Path:
    """Where `uv lock --script` puts the lockfile for a recipe file."""
    return path.with_name(path.name + ".lock")


def locked_interpreter(path: Path, uv: str) -> str | None:
    """The interpreter of the environment `uv` materialised from the lockfile.

    Without this, `make --sync` would write a lockfile that nothing reads, and
    the pinning it promises would be decorative -- which is worse than no
    lockfile at all. `uv sync --script` builds the environment from the lock,
    and `uv python find --script` reports where it went.

    Returns None whenever anything is off (no lock, sync failed, the environment
    somehow lacks `make`), so the caller falls back to resolving from the
    declared ranges. A slower correct path beats a fast wrong one.
    """
    if not lock_path(path).is_file():
        return None
    synced = subprocess.run(
        [uv, "sync", "--script", str(path), "--quiet"], capture_output=True, text=True, check=False
    )
    if synced.returncode != 0:
        debug(f"bootstrap: uv sync --script failed, ignoring the lockfile\n{synced.stderr.strip()}")
        return None
    found = subprocess.run(
        [uv, "python", "find", "--script", str(path)], capture_output=True, text=True, check=False
    )
    interpreter = found.stdout.strip()
    if found.returncode != 0 or not interpreter:
        return None
    usable = subprocess.run([interpreter, "-c", "import make"], capture_output=True, check=False)
    if usable.returncode != 0:
        debug("bootstrap: the locked environment has no `make` in it, ignoring the lockfile")
        return None
    return interpreter


def reexec(metadata: ScriptMetadata, argv: list[str], recipe_file: Path | None = None) -> int:
    """Run this command again in an environment that has the declared deps."""
    uv = shutil.which("uv")
    if uv is None:
        raise MakeError(
            "this recipe file declares dependencies, which needs `uv` on PATH",
            hint="install it from https://docs.astral.sh/uv/ , or install the dependencies "
            "into the current environment yourself and re-run",
        )

    environment = dict(os.environ)
    environment[BOOTSTRAP_FLAG] = "1"

    # Pinned wins: if there is a lockfile, run exactly what it says.
    if recipe_file is not None:
        interpreter = locked_interpreter(recipe_file, uv)
        if interpreter is not None:
            debug(f"bootstrap: using the locked environment at {interpreter}")
            return subprocess.run([interpreter, "-m", "make", *argv], env=environment, check=False).returncode

    command = [uv, "run", "--quiet"]
    if metadata.requires_python:
        command += ["--python", metadata.requires_python]
    command += _self_requirement(metadata)
    for requirement in metadata.dependencies:
        command += ["--with", requirement]
    command += ["python", "-m", "make", *argv]

    # Only announce the wait the first time this dependency set is seen. uv
    # caches the environment, so later runs cost about 50ms and an unexplained
    # "resolving..." on every single invocation is just noise.
    marker = _bootstrap_marker(metadata)
    if not marker.exists():
        note("resolving recipe dependencies with uv (cached after the first run)")
    debug("bootstrap: " + " ".join(command))

    completed = subprocess.run(command, env=environment, check=False)
    if completed.returncode == 0:
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
        except OSError:  # pragma: no cover - a read-only cache is not fatal
            pass
    return completed.returncode


def _bootstrap_marker(metadata: ScriptMetadata) -> Path:
    """A file recording that this exact dependency set has resolved before."""
    import hashlib

    key = hashlib.sha256(
        "\n".join([metadata.requires_python or "", *sorted(metadata.dependencies)]).encode()
    ).hexdigest()[:16]
    cache = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(cache) / "make" / "bootstrap" / key


def sync(path: Path, metadata: ScriptMetadata, *, upgrade: bool = False) -> int:
    """Pin the declared dependencies -- `uv lock --script`, or `uv sync` in a project.

    Without `upgrade`, an existing lockfile is respected: a repo stays on the
    version it was pinned to even after the shared package moves. That is the
    whole difference from `.just-shared/`, where `git pull --ff-only || true`
    silently dragged every repo to whatever HEAD happened to be. Moving a pin is
    something you ask for, and it shows up as a diff.
    """
    uv = shutil.which("uv")
    if uv is None:
        raise MakeError("`uv` is not on PATH", hint="https://docs.astral.sh/uv/")

    if metadata.dependencies:
        note(f"upgrading the pins in {path.name}" if upgrade else f"locking {path.name}")
        command = [uv, "lock", "--script", str(path)]
        if upgrade:
            command.append("--upgrade")
        return subprocess.run(command, check=False).returncode

    if (path.parent / "pyproject.toml").is_file():
        note("syncing the project environment")
        command = [uv, "sync"]
        if upgrade:
            command.append("--upgrade")
        return subprocess.run(command, cwd=str(path.parent), check=False).returncode

    raise MakeError(
        f"{path.name} declares no dependencies and there is no pyproject.toml beside it",
        hint='add an inline metadata block:\n    # /// script\n    # dependencies = ["make"]\n    # ///',
    )


def python_executable() -> str:  # pragma: no cover - trivial
    return sys.executable
