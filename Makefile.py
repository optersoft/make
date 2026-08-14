"""Recipes for developing `make` itself -- and the first thing that dogfoods it.

Run `make` with no arguments to see them.
"""

from __future__ import annotations

from pathlib import Path

from make import note, recipe, sh, step, warn

# Both workspace members, listed as Python paths rather than as `optersoft/`.
# Ruff formats Python code blocks inside markdown too, and the whole directory
# hands it docs/ -- where the examples are hand-packed to read as prose and
# reformatting them is a docs edit disguised as a lint fix.
SOURCES = ["src", "tests", "Makefile.py", "optersoft/src", "optersoft/tests", "optersoft/examples"]


@recipe(group="dev", requires=["uv"])
def sync() -> None:
    """Install both workspace members in editable mode.

    `--all-packages` is what reaches optersoft/; a plain `uv sync` installs the
    root project only, and then optersoft/tests fail on import rather than on
    anything real.

    Also removes a stray `make` distribution. The import name is `make` but the
    distribution is `mkrun`, so an environment carrying both -- easy to end up
    with after the rename, or by installing the unrelated PyPI `make` -- makes
    dependency checks pass locally that fail everywhere else. One did.
    """
    sh("uv", "sync", "--all-extras", "--all-packages")
    if _installed("make"):
        warn("removing a stray `make` distribution; this project's is `mkrun`")
        sh("uv", "pip", "uninstall", "make")


def _installed(distribution: str) -> bool:
    import importlib.metadata as metadata

    try:
        metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return False
    return True


@recipe(group="dev", requires=["uv"])
def test(*paths: str, verbose: bool = False) -> None:
    """Run the test suite.

    Args:
        verbose: show each test name
    """
    sh("uv", "run", "pytest", *(paths or ()), *(["-v"] if verbose else []))


@recipe(group="dev", requires=["uv"])
def lint(*, fix: bool = False) -> None:
    """Check formatting and lint rules.

    Args:
        fix: apply the fixes instead of only reporting them
    """
    sh("uv", "run", "ruff", "check", *(["--fix"] if fix else []), *SOURCES)
    sh("uv", "run", "ruff", "format", *([] if fix else ["--check"]), *SOURCES)


@recipe(group="dev", needs=[lint, test])
def check() -> None:
    """Everything CI runs."""
    note("lint and tests passed")


@recipe(group="dev")
def bench() -> None:
    """Measure startup latency -- the number that decides whether this gets used."""
    import statistics
    import subprocess
    import sys
    import time

    samples = []
    for _ in range(20):
        started = time.perf_counter()
        subprocess.run(
            [sys.executable, "-m", "make", "--list"],
            capture_output=True,
            check=True,
            cwd=Path(__file__).parent,
        )
        samples.append((time.perf_counter() - started) * 1000)

    best, median = min(samples), statistics.median(samples)
    step(f"startup: {best:.0f}ms best, {median:.0f}ms median (budget 150ms)")
    if median > 150:
        warn("over budget -- check for a heavy import at module scope")


@recipe(group="dist", requires=["uv"])
def build() -> None:
    """Build the wheel and sdist."""
    sh("uv", "build")


@recipe(group="dist", needs=[check], requires=["git"], dangerous=True)
def release(version: str) -> None:
    """Tag a release, which publishes to PyPI from CI.

    Uploading happens in GitHub Actions through PyPI Trusted Publishing, so no
    token exists on any laptop to leak. This only moves the tag.
    """
    from make import note

    sh("git", "tag", "-a", f"v{version}", "-m", f"mkrun {version}")
    sh("git", "push", "origin", f"v{version}")
    note(f"tagged v{version} -- watch the release workflow for the upload")


@recipe(group="dev")
def completions(shell: str = "zsh") -> None:
    """Print the completion script for a shell (bash, zsh, fish)."""
    from make.completions import emit

    print(emit(shell))
