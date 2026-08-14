"""Making shared tasks an ordinary dependency.

This is the feature the whole project exists for. `just` shares tasks by
`git clone --depth 1` into a gitignored directory, driven by a `_shared` task
copy-pasted into every consumer -- no versions, no pinning, no lockfile, and a
`git pull --ff-only || true` that fails silently and leaves you on whatever HEAD
happened to be there.

Here a task file declares its dependencies inline, in PEP 723 form:

    # /// script
    # requires-python = ">=3.11"
    # dependencies = ["mkrun>=0.2", "acme-tasks>=0.4"]
    # ///

If the current interpreter already satisfies them, nothing happens -- that is
the common case in a project with a synced virtualenv, and it costs one
`importlib.metadata` lookup per dependency. Otherwise `make` re-executes itself
under `uv run`, which resolves into a cached environment. Pin it with
`mk --sync` (`uv lock --script`).

The satisfaction check errs toward re-executing: a specifier it cannot parse
counts as unsatisfied. Being needlessly slow is recoverable; running a task
against the wrong version of its library is not.

A task package can also come from the repository that owns it, declared the
way the Rust crates here declare each other:

    # [tool.uv.sources]
    # hetzner-make = { path = "../hetzner/make" }

That table is only visible to `uv sync --script`, which reads the file --
`uv run --with` builds an environment from bare requirements and never opens it.
So a file declaring sources takes the script path, and a file that does not
keeps the faster `--with` one. Getting this wrong is silent: the requirement
still resolves, just to whatever PyPI has under that name.

`.make/sources.toml` redirects a package to a local checkout without touching
the committed file -- cargo's `[patch]`. uv has no external source override, so
that one is applied by generating a script that carries the rewritten table.
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

#: Where a repo, or the user, redirects a task package to a local checkout.
SOURCES_FILE = "sources.toml"


def canonical_name(name: str) -> str:
    """PEP 503 normalisation, so `Foo_Bar` and `foo-bar` are the same package."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


@dataclass
class ScriptMetadata:
    """The `# /// script` block of a task file."""

    dependencies: list[str] = field(default_factory=list)
    requires_python: str | None = None
    raw: dict = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not self.dependencies and not self.requires_python

    @property
    def sources(self) -> dict[str, dict]:
        """The `[tool.uv.sources]` table: where each dependency comes from.

        This is how a task package is consumed from the repository that owns
        it -- `{ path = "../hetzner/tasks" }` for a checkout beside this one,
        `{ git = "ssh://...", subdirectory = "tasks" }` otherwise -- the same
        split the Rust crates here use.
        """
        table = self.raw.get("tool", {}).get("uv", {}).get("sources", {})
        return {canonical_name(name): spec for name, spec in table.items()} if table else {}


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
    """True when this interpreter cannot run the task file as declared."""
    if os.environ.get(BOOTSTRAP_FLAG):
        return False
    if not metadata.dependencies:
        return False
    if metadata.sources:
        # A source moves the *where* out of the requirement, so what is left is
        # a bare `hetzner-make>=0.1` that an already-installed copy would
        # satisfy -- from PyPI, or from the wrong checkout. The version check
        # cannot see the difference, so it does not get to decide.
        debug("bootstrap: the task file declares [tool.uv.sources]")
        return True
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
    return canonical_name(match.group("name") if match else text)


def _declares_self(metadata: ScriptMetadata) -> bool:
    """Does the task file already say where to get the tool from?"""
    return any(_requirement_name(r) == DISTRIBUTION for r in metadata.dependencies)


def _self_requirement(metadata: ScriptMetadata) -> list[str]:
    """How the child environment should obtain `make` itself.

    Nothing, when the task file already declares it. Adding our own on top
    would hand uv two different sources for one package -- a hard error the
    moment the file pins a git URL or a specific version, which is exactly what
    a consumer of a private task package does.
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
    """Where `uv lock --script` puts the lockfile for a task file."""
    return path.with_name(path.name + ".lock")


# --------------------------------------------------------------------------
# Local source overrides -- cargo's `[patch]`, for task packages
# --------------------------------------------------------------------------


def override_files(root: Path) -> list[Path]:
    """The `sources.toml` layers that apply here, lowest precedence first.

    `~/.make/sources.toml` covers every repo at once, which is what you want
    when the whole fleet is checked out side by side; the repo's own
    `.make/sources.toml` then overrides it. Both are gitignored: an override
    says something about this machine, not about the project.
    """
    from . import env as env_module

    candidates = [env_module.config_dir() / SOURCES_FILE, root / ".make" / SOURCES_FILE]
    return [path for path in candidates if path.is_file()]


def read_overrides(root: Path) -> dict[str, dict]:
    """Merge the `sources.toml` layers into `{package: source spec}`.

    A relative `path` resolves against the *repo root*, not against the file it
    was written in, so one line in `~/.make/sources.toml` --
    `hetzner-make = { path = "../hetzner/make" }` -- is correct from
    inside every sibling checkout.
    """
    import tomllib

    merged: dict[str, dict] = {}
    for file in override_files(root):
        try:
            data = tomllib.loads(file.read_text(encoding="utf-8"))
        except Exception as exc:
            raise MakeError(f"{file}: not valid TOML -- {exc}") from exc
        table = data.get("sources") or {}
        if not isinstance(table, dict):
            raise MakeError(f"{file}: [sources] must be a table of package = {{ path = ... }}")
        for name, spec in table.items():
            if not isinstance(spec, dict) or "path" not in spec:
                raise MakeError(
                    f"{file}: source for {name!r} must be a table with a `path`",
                    hint='  [sources]\n  hetzner-make = { path = "../hetzner/make" }',
                )
            resolved = Path(spec["path"]).expanduser()
            if not resolved.is_absolute():
                resolved = (root / resolved).resolve()
            if not resolved.is_dir():
                raise MakeError(
                    f"{file}: {name} points at {resolved}, which is not a directory",
                    hint="check the sibling checkout is where the override says it is",
                )
            merged[canonical_name(name)] = {**spec, "path": str(resolved), "origin": str(file)}
    return merged


def _inline_table(spec: dict) -> str:
    """Serialise a source spec back to a TOML inline table (strings and bools only)."""

    def value(item: object) -> str:
        if isinstance(item, bool):
            return "true" if item else "false"
        return '"' + str(item).replace("\\", "\\\\").replace('"', '\\"') + '"'

    body = ", ".join(f"{key} = {value(val)}" for key, val in spec.items() if key != "origin")
    return "{ " + body + " }"


def write_shim(task_file: Path, metadata: ScriptMetadata, overrides: dict[str, dict]) -> Path:
    """Generate the script uv resolves when a local override is in force.

    uv has no way to redirect a script's source from outside the script: `uv
    sync` takes no `--with`, `uv run --script` runs *that* script, and
    `--no-sources-package` can only switch a source off, not replace it. So the
    override is expressed the only way uv accepts one -- in script metadata --
    by generating a script that carries the rewritten table and does nothing but
    hand control back to `make`.

    Deliberately not locked: a path source pins no commit, exactly like a cargo
    path dependency. The committed task file and its lockfile are untouched.
    """
    from .discovery import task_root

    sources = dict(metadata.sources)
    for name, spec in overrides.items():
        if name in {_requirement_name(r) for r in metadata.dependencies}:
            sources[name] = {"path": spec["path"], "editable": True}

    lines = ["# /// script"]
    if metadata.requires_python:
        lines.append(f'# requires-python = "{metadata.requires_python}"')
    lines.append("# dependencies = [")
    lines += [f'#   "{dependency}",' for dependency in metadata.dependencies]
    lines.append("# ]")
    if sources:
        lines.append("#")
        lines.append("# [tool.uv.sources]")
        lines += [f"# {name} = {_inline_table(spec)}" for name, spec in sorted(sources.items())]
    lines.append("# ///")

    shim = task_root(task_file) / ".make" / "bootstrap.py"
    shim.parent.mkdir(parents=True, exist_ok=True)
    shim.write_text(
        "\n".join(lines)
        + f'''
"""GENERATED by `make` -- do not edit, do not commit.

The environment for {task_file.name}, with the sources in
{", ".join(sorted({spec["origin"] for spec in overrides.values()}))}
applied. Delete the override to go back to what the task file declares.
"""

import sys

from make.cli import main

sys.exit(main())
''',
        encoding="utf-8",
    )
    return shim


#: Why the last `script_interpreter` call failed, for the error the caller
#: raises. uv's own message is the only thing that says *what* was wrong --
#: without it a CI failure reads "could not build the environment" and nothing
#: else, which is exactly how one cost an afternoon.
_last_failure: str = ""


def script_interpreter(path: Path, uv: str) -> str | None:
    """The interpreter of the environment `uv` materialised for a script.

    This is the only path that reads the script's own metadata, so it is the
    only one where `[tool.uv.sources]` means anything -- `uv run --with` builds
    an environment from bare requirements and never opens the file. It is also
    what makes `mk --sync` real: without it the lockfile would be decorative,
    which is worse than no lockfile at all.

    Returns None whenever anything is off (sync failed, the environment somehow
    lacks `make`), so the caller can fall back. A slower correct path beats a
    fast wrong one.
    """
    global _last_failure
    _last_failure = ""
    synced = subprocess.run(
        [uv, "sync", "--script", str(path), "--quiet"], capture_output=True, text=True, check=False
    )
    if synced.returncode != 0:
        _last_failure = synced.stderr.strip()
        debug(f"bootstrap: uv sync --script {path.name} failed\n{_last_failure}")
        return None
    found = subprocess.run(
        [uv, "python", "find", "--script", str(path)], capture_output=True, text=True, check=False
    )
    interpreter = found.stdout.strip()
    if found.returncode != 0 or not interpreter:
        return None
    usable = subprocess.run([interpreter, "-c", "import make"], capture_output=True, check=False)
    if usable.returncode != 0:
        debug(f"bootstrap: the environment for {path.name} has no `make` in it, ignoring it")
        return None
    return interpreter


def reexec(metadata: ScriptMetadata, argv: list[str], task_file: Path | None = None) -> int:
    """Run this command again in an environment that has the declared deps."""
    uv = shutil.which("uv")
    if uv is None:
        raise MakeError(
            "this task file declares dependencies, which needs `uv` on PATH",
            hint="install it from https://docs.astral.sh/uv/ , or install the dependencies "
            "into the current environment yourself and re-run",
        )

    environment = dict(os.environ)
    environment[BOOTSTRAP_FLAG] = "1"

    # Script mode, whenever the file says anything `uv run --with` cannot hear:
    # a local override, a `[tool.uv.sources]` table, or a lockfile to obey.
    # Only `uv sync --script` reads the file itself.
    if task_file is not None:
        from .discovery import task_root

        overrides = {
            name: spec
            for name, spec in read_overrides(task_root(task_file)).items()
            if name in {_requirement_name(r) for r in metadata.dependencies}
        }
        script = task_file
        if overrides:
            script = write_shim(task_file, metadata, overrides)
            for name, spec in sorted(overrides.items()):
                note(f"{name} overridden -> {spec['path']}  ({Path(spec['origin']).name})")
        if overrides or metadata.sources or lock_path(task_file).is_file():
            interpreter = script_interpreter(script, uv)
            if interpreter is not None:
                debug(f"bootstrap: using the environment for {script.name} at {interpreter}")
                return subprocess.run(
                    [interpreter, "-m", "make", *argv], env=environment, check=False
                ).returncode
            if overrides or metadata.sources:
                # Falling through to `--with` here would quietly resolve the
                # package from PyPI instead of the checkout or repository the
                # file names -- a different package with the same name.
                raise MakeError(
                    f"could not build the environment for {script.name}"
                    + (f"\n{_last_failure}" if _last_failure else ""),
                    hint=f"reproduce with: uv sync --script {script}",
                )

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
        note("resolving task dependencies with uv (cached after the first run)")
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


def add(path: Path, package: str, *, source_path: str | None = None, git: str | None = None) -> int:
    """Add a task package to the task file, with its source.

    `uv add --script` writes both the requirement and the `[tool.uv.sources]`
    entry, in the form uv itself will read back. Hand-editing the table is the
    same job with more ways to get the quoting wrong, and a source uv cannot
    parse fails at the least convenient moment.
    """
    uv = shutil.which("uv")
    if uv is None:
        raise MakeError("`uv` is not on PATH", hint="https://docs.astral.sh/uv/")
    if source_path and git:
        raise MakeError("--path and --git are two answers to one question; pass one")

    command = [uv, "add", "--script", str(path), package]
    if source_path:
        # Editable, so the tasks a sibling checkout is currently on are the
        # ones that run -- the point of pointing at a checkout at all.
        command += ["--editable", source_path]
    elif git:
        command += ["--git", git]
    note(f"adding {package} to {path.name}")
    return subprocess.run(command, check=False).returncode


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
