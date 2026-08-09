"""Startup latency is a feature, so it gets a test.

A command runner is typed dozens of times an hour. `just` starts in about 5 ms;
anything that feels slower than "instant" gets abandoned regardless of how good
its recipes are. The budget is 150 ms warm -- generous next to the ~25 ms this
actually takes, but tight enough that an import-time regression (a heavy
dependency, work at module scope) fails here instead of being discovered as a
vague sense that the tool got sluggish.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest
from helpers import write

BUDGET_SECONDS = 0.15
RUNS = 5

RECIPES = """
from make import recipe, sh

@recipe
def noop() -> None:
    \"\"\"Do nothing at all.\"\"\"
    sh("true")
"""


def _time(argv: list[str], cwd: Path) -> float:
    env = {"PATH": "/usr/bin:/bin", "NO_COLOR": "1", "HOME": str(cwd)}
    best = float("inf")
    for _ in range(RUNS):
        started = time.perf_counter()
        subprocess.run(
            [sys.executable, "-m", "make", *argv],
            cwd=cwd,
            capture_output=True,
            env={**env, "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src")},
            check=True,
        )
        best = min(best, time.perf_counter() - started)
    return best


@pytest.mark.parametrize("argv", [["--list"], ["--dry-run", "noop"]])
def test_startup_is_within_budget(project: Path, argv: list[str]):
    write(project / "Makefile.py", RECIPES)
    elapsed = _time(argv, project)
    assert elapsed < BUDGET_SECONDS, (
        f"{' '.join(argv)} took {elapsed * 1000:.0f}ms (budget {BUDGET_SECONDS * 1000:.0f}ms)"
    )


def test_importing_the_package_pulls_in_nothing_heavy():
    """The import graph is the thing that actually decides startup cost."""
    code = "import sys, make; print(len([m for m in sys.modules if not m.startswith('_')]))"
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env={"PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src"), "PATH": "/usr/bin:/bin"},
    ).stdout
    assert int(out) < 200
