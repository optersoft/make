"""Finding and importing the task file.

`Makefile.py`, not `make.py`: a module named `make.py` at the repo root can
shadow `import make` for anything that puts the root first on `sys.path`, and a
task file whose first line fails to import the tool that is running it is a
bad first impression. For the same reason the root is *appended* to `sys.path`,
never prepended -- sibling modules stay importable without any local file being
able to displace an installed package.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from .context import debug
from .errors import TaskError, UsageError

__all__ = [
    "STARTER_TASK_FILE",
    "TASK_FILENAMES",
    "create_task_file",
    "find_task_file",
    "load_task_file",
    "require_task_file",
    "task_root",
]

#: Searched in this order, in each directory from the cwd upward.
TASK_FILENAMES = ("Makefile.py", "makefile.py", "mk.py", ".make/main.py")

MODULE_NAME = "__make_tasks__"


def find_task_file(start: Path | None = None) -> Path | None:
    """Walk up from `start` looking for a task file. None if there is none."""
    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        for name in TASK_FILENAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def require_task_file(start: Path | None = None) -> Path:
    found = find_task_file(start)
    if found is not None:
        return found

    here = (start or Path.cwd()).resolve()
    stray = here / "make.py"
    hint = (
        "run `mk` from a terminal and it offers to create a Makefile.py here (`mk --yes` skips the question)"
    )
    if stray.is_file():
        hint = (
            "found make.py -- rename it to Makefile.py. A module named make.py can shadow "
            "`import make`, so it is deliberately not searched for."
        )
    raise UsageError(f"no task file found in {here} or any parent", hint=hint)


#: What `mk` writes when asked to create a task file in an empty project.
STARTER_TASK_FILE = '''\
"""Tasks for this project. Run `mk` to list them, `mk <task>` to run one."""

from make import sh, task


@task
def hello() -> None:
    """Say hello."""
    sh("echo", "hello")
'''


def create_task_file(directory: Path | None = None) -> Path:
    """Write the starter `Makefile.py` into `directory` and return its path.

    Refuses to overwrite: an existing file is the user's, whatever it holds.
    """
    target = (directory or Path.cwd()).resolve() / TASK_FILENAMES[0]
    if target.exists():
        raise UsageError(f"{target} already exists")
    target.write_text(STARTER_TASK_FILE, encoding="utf-8")
    return target


def task_root(path: Path) -> Path:
    """The project directory a task file governs.

    Its own directory, except for `.make/main.py`, where the project is the
    directory holding `.make/`. Tasks run here, and relative paths in a
    `.make/sources.toml` override resolve against it.
    """
    resolved = Path(path).resolve()
    root = resolved.parent
    if resolved.name == "main.py" and root.name == ".make":
        root = root.parent
    return root


def load_task_file(path: Path) -> object:
    """Import the task file, registering everything it defines."""
    resolved = Path(path).resolve()
    root = task_root(resolved)

    root_str = str(root)
    if root_str not in sys.path:
        sys.path.append(root_str)  # append: never shadow an installed package

    debug(f"loading tasks from {resolved}")
    spec = importlib.util.spec_from_file_location(MODULE_NAME, resolved)
    if spec is None or spec.loader is None:  # pragma: no cover - unreadable file
        raise TaskError(f"cannot import {resolved}")
    module = importlib.util.module_from_spec(spec)

    # Importing the task file would otherwise drop a `__pycache__/` into the
    # repository root. Most of the repos this runs in are Rust or Android
    # projects that do not gitignore it, so it shows up as untracked noise in
    # every `git status` -- for a cache that saves under a millisecond on a
    # file this size. A command runner has no business littering the repo it
    # runs in.
    bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    sys.modules[MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = bytecode
    return module
