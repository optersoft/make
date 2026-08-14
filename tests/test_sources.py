"""Recipe packages consumed from the repository that owns them.

A group like `box` belongs in `hetzner/`, beside the CLI it wraps. Getting it
from there means the same two forms the Rust crates here already use: a path to
a checkout sitting beside this one, or a git URL. Both are declared in the
recipe file's `[tool.uv.sources]`.

That table is only read by `uv sync --script`. `uv run --with`, which is how
this tool used to install dependencies, never opens the file -- so a source was
silently ignored and the package came from PyPI instead. Some of the tests below
exist purely to keep that from coming back.

The two end-to-end tests really do run uv and really do build wheels. The git
one uses a local bare repository over `file://`, so nothing here reaches the
network -- except, on a cold cache, to fetch the build backend.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import write

from make import env as env_module
from make.bootstrap import ScriptMetadata, read_metadata, read_overrides, reexec, write_shim
from make.errors import MakeError

#: This checkout, so a fixture can depend on the runner without going to PyPI.
MAKE_ROOT = Path(__file__).resolve().parent.parent

needs_uv = pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv on PATH")
from_checkout = pytest.mark.skipif(
    not (MAKE_ROOT / "pyproject.toml").is_file(), reason="needs the mkrun checkout"
)


def build_package(root: Path, marker: str) -> Path:
    """A minimal recipe package whose one recipe reports where it came from."""
    write(
        root / "pyproject.toml",
        """
[project]
name = "fixture-recipes"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = []

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/fixture_recipes"]
""",
    )
    write(
        root / "src/fixture_recipes/__init__.py",
        f'''
from make import recipe


@recipe(group="fixture")
def hello() -> None:
    """Report which copy of this package is installed."""
    print("fixture came from {marker}")
''',
    )
    return root


def consumer(project: Path, sources: dict[str, str]) -> Path:
    """A recipe file depending on the fixture, with the given sources table."""
    table = "\n".join(f"# {name} = {spec}" for name, spec in sources.items())
    return write(
        project / "Makefile.py",
        f"""
# /// script
# requires-python = ">=3.11"
# dependencies = ["mkrun", "fixture-recipes"]
#
# [tool.uv.sources]
{table}
# ///
from fixture_recipes import hello  # noqa: F401 -- importing registers
""",
    )


def run_make(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke the CLI the way a user would, from outside any bootstrapped env."""
    environment = {k: v for k, v in os.environ.items() if k != "_MAKE_BOOTSTRAPPED"}
    environment.pop("VIRTUAL_ENV", None)
    environment["NO_COLOR"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "make", *args],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
    )


# -- end to end ------------------------------------------------------------


@needs_uv
@from_checkout
def test_a_recipe_package_resolves_from_a_path(project: Path):
    """The sibling-checkout form: `{ path = "../hetzner/recipes" }`."""
    build_package(project / "provider", marker="the path")
    consumer(
        project,
        {"mkrun": f'{{ path = "{MAKE_ROOT}" }}', "fixture-recipes": f'{{ path = "{project / "provider"}" }}'},
    )

    result = run_make(project, "fixture.hello")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "fixture came from the path" in result.stdout


@needs_uv
@from_checkout
def test_a_recipe_package_resolves_from_git(project: Path):
    """The portable form. A local bare repo over file://, so this stays offline."""
    source = build_package(project / "provider", marker="git")
    bare = project / "provider.git"
    subprocess.run(["git", "init", "--quiet", "--bare", str(bare)], check=True)
    for command in (
        ["git", "init", "--quiet", "-b", "main"],
        ["git", "add", "-A"],
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "fixture"],
        ["git", "remote", "add", "origin", str(bare)],
        ["git", "push", "--quiet", "origin", "main"],
    ):
        subprocess.run(command, cwd=source, check=True, capture_output=True)

    consumer(
        project, {"mkrun": f'{{ path = "{MAKE_ROOT}" }}', "fixture-recipes": f'{{ git = "file://{bare}" }}'}
    )

    result = run_make(project, "fixture.hello")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "fixture came from git" in result.stdout


@needs_uv
@from_checkout
def test_an_override_redirects_a_package_to_a_local_checkout(project: Path, monkeypatch):
    """`.make/sources.toml` is the `[patch]` half: same file, different copy."""
    monkeypatch.setattr(env_module, "CONFIG_DIRS", (project / "home", project / "nonexistent"))
    build_package(project / "provider", marker="the override")
    # The committed source points somewhere that could not possibly work, so a
    # pass can only mean the override was honoured.
    consumer(
        project,
        {"mkrun": f'{{ path = "{MAKE_ROOT}" }}', "fixture-recipes": '{ git = "file:///nowhere/absent.git" }'},
    )
    write(project / ".make/sources.toml", '[sources]\nfixture-recipes = { path = "provider" }\n')

    result = run_make(project, "fixture.hello")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "fixture came from the override" in result.stdout
    assert (project / ".make/bootstrap.py").is_file(), "the shim carries the rewritten sources"
    # A path source pins no commit, so there is nothing honest to lock.
    assert not (project / "Makefile.py.lock").exists()
    assert not (project / ".make/bootstrap.py.lock").exists()


# -- the override file -----------------------------------------------------


def test_the_repo_override_beats_the_home_one(project: Path, monkeypatch):
    monkeypatch.setattr(env_module, "CONFIG_DIRS", (project / "home", project / "nonexistent"))
    (project / "from-home").mkdir()
    (project / "from-repo").mkdir()
    write(project / "home/sources.toml", '[sources]\nfixture-recipes = { path = "from-home" }\n')
    write(project / ".make/sources.toml", '[sources]\nfixture-recipes = { path = "from-repo" }\n')

    overrides = read_overrides(project)
    assert overrides["fixture-recipes"]["path"] == str(project / "from-repo")


def test_a_relative_override_resolves_against_the_repo_not_the_file(project: Path, monkeypatch):
    """So one line in ~/.make/sources.toml is right from every sibling checkout."""
    monkeypatch.setattr(env_module, "CONFIG_DIRS", (project / "home", project / "nonexistent"))
    (project / "sibling").mkdir()
    write(project / "home/sources.toml", '[sources]\nfixture-recipes = { path = "sibling" }\n')

    assert read_overrides(project)["fixture-recipes"]["path"] == str(project / "sibling")


def test_an_override_pointing_nowhere_says_so(project: Path, monkeypatch):
    monkeypatch.setattr(env_module, "CONFIG_DIRS", (project / "home", project / "nonexistent"))
    write(project / ".make/sources.toml", '[sources]\nfixture-recipes = { path = "gone" }\n')

    with pytest.raises(MakeError, match="not a directory"):
        read_overrides(project)


def test_an_override_without_a_path_says_what_it_wanted(project: Path, monkeypatch):
    monkeypatch.setattr(env_module, "CONFIG_DIRS", (project / "home", project / "nonexistent"))
    write(project / ".make/sources.toml", '[sources]\nfixture-recipes = "provider"\n')

    with pytest.raises(MakeError, match="must be a table with a `path`"):
        read_overrides(project)


def test_the_shim_carries_the_rewritten_sources(project: Path, monkeypatch):
    monkeypatch.setattr(env_module, "CONFIG_DIRS", (project / "home", project / "nonexistent"))
    (project / "provider").mkdir()
    recipe_file = consumer(project, {"fixture-recipes": '{ git = "file:///nowhere.git" }'})
    write(project / ".make/sources.toml", '[sources]\nfixture-recipes = { path = "provider" }\n')

    shim = write_shim(recipe_file, read_metadata(recipe_file), read_overrides(project))
    text = shim.read_text()

    assert shim == project / ".make/bootstrap.py"
    assert f'fixture-recipes = {{ path = "{project / "provider"}", editable = true }}' in text
    assert "nowhere.git" not in text, "the overridden source must not survive"
    assert '"fixture-recipes"' in text, "the dependency list is carried over verbatim"
    assert "do not commit" in text


# -- the gap that made all of this necessary -------------------------------


def test_declared_sources_never_take_the_with_path(project: Path, monkeypatch):
    """`uv run --with` cannot see [tool.uv.sources], so it must not be used.

    This is the bug: the source was ignored and the dependency came from PyPI --
    a different package that happens to share a name.
    """
    seen: list[list[str]] = []

    def record(argv, *args, **kwargs):
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "/usr/bin/python3", "")

    monkeypatch.setattr(subprocess, "run", record)
    metadata = ScriptMetadata(
        dependencies=["fixture-recipes"],
        raw={"tool": {"uv": {"sources": {"fixture-recipes": {"path": "../provider"}}}}},
    )
    reexec(metadata, ["--list"], project / "Makefile.py")

    assert seen, "nothing ran"
    assert not any("--with" in command for command in seen)
    # `uv` is an absolute path here, from shutil.which.
    assert any(Path(command[0]).name == "uv" and command[1:3] == ["sync", "--script"] for command in seen)


def test_a_source_that_cannot_be_built_fails_instead_of_falling_back(project: Path, monkeypatch):
    """Falling back would resolve the name from PyPI -- silently, and wrongly."""

    def failing(argv, *args, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "no solution found")

    monkeypatch.setattr(subprocess, "run", failing)
    metadata = ScriptMetadata(
        dependencies=["fixture-recipes"],
        raw={"tool": {"uv": {"sources": {"fixture-recipes": {"path": "../provider"}}}}},
    )

    with pytest.raises(MakeError, match="could not build the environment"):
        reexec(metadata, ["--list"], project / "Makefile.py")


def test_sources_are_read_and_normalised_from_the_metadata(project: Path):
    recipe_file = consumer(project, {"Fixture_Recipes": '{ path = "provider" }'})

    assert read_metadata(recipe_file).sources == {"fixture-recipes": {"path": "provider"}}


def test_a_file_with_no_sources_reports_none(project: Path):
    recipe_file = write(project / "Makefile.py", '# /// script\n# dependencies = ["httpx"]\n# ///\nx = 1\n')

    assert read_metadata(recipe_file).sources == {}
