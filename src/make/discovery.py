"""Finding and importing the recipe file.

`Makefile.py`, not `make.py`: a module named `make.py` at the repo root can
shadow `import make` for anything that puts the root first on `sys.path`, and a
recipe file whose first line fails to import the tool that is running it is a
bad first impression. For the same reason the root is *appended* to `sys.path`,
never prepended -- sibling modules stay importable without any local file being
able to displace an installed package.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from .context import debug
from .errors import RecipeError, UsageError

__all__ = ["RECIPE_FILENAMES", "find_recipe_file", "load_recipe_file", "recipe_root"]

#: Searched in this order, in each directory from the cwd upward.
RECIPE_FILENAMES = ("Makefile.py", "makefile.py", "mk.py", ".make/main.py")

MODULE_NAME = "__make_recipes__"


def find_recipe_file(start: Path | None = None) -> Path | None:
    """Walk up from `start` looking for a recipe file. None if there is none."""
    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        for name in RECIPE_FILENAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def require_recipe_file(start: Path | None = None) -> Path:
    found = find_recipe_file(start)
    if found is not None:
        return found

    here = (start or Path.cwd()).resolve()
    stray = here / "make.py"
    hint = (
        "create Makefile.py with:\n"
        "    from make import recipe, sh\n\n"
        "    @recipe\n"
        "    def hello() -> None:\n"
        '        """Say hello."""\n'
        '        sh("echo", "hello")'
    )
    if stray.is_file():
        hint = (
            "found make.py -- rename it to Makefile.py. A module named make.py can shadow "
            "`import make`, so it is deliberately not searched for."
        )
    raise UsageError(f"no recipe file found in {here} or any parent", hint=hint)


def recipe_root(path: Path) -> Path:
    """The project directory a recipe file governs.

    Its own directory, except for `.make/main.py`, where the project is the
    directory holding `.make/`. Recipes run here, and relative paths in a
    `.make/sources.toml` override resolve against it.
    """
    resolved = Path(path).resolve()
    root = resolved.parent
    if resolved.name == "main.py" and root.name == ".make":
        root = root.parent
    return root


def load_recipe_file(path: Path) -> object:
    """Import the recipe file, registering everything it defines."""
    resolved = Path(path).resolve()
    root = recipe_root(resolved)

    root_str = str(root)
    if root_str not in sys.path:
        sys.path.append(root_str)  # append: never shadow an installed package

    debug(f"loading recipes from {resolved}")
    spec = importlib.util.spec_from_file_location(MODULE_NAME, resolved)
    if spec is None or spec.loader is None:  # pragma: no cover - unreadable file
        raise RecipeError(f"cannot import {resolved}")
    module = importlib.util.module_from_spec(spec)

    # Importing the recipe file would otherwise drop a `__pycache__/` into the
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
