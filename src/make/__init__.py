"""make -- a command runner whose recipes are Python.

    # Makefile.py
    from make import recipe, sh

    @recipe(group="app", requires=["cargo"])
    def test(*, fast: bool = False) -> None:
        \"\"\"Run the test suite.\"\"\"
        sh("cargo", "test", *(["--lib"] if fast else []))

    $ mk app.test --fast

The command line comes from the signature, so there is no second schema to keep
in sync. Recipes are ordinary functions: importable, unit-testable, and
distributable as versioned packages instead of a directory someone `git clone`d.
"""

from __future__ import annotations

__version__ = "0.1.0"

from . import config, env, fs
from .context import Context, confirm, ctx, echo, info, note, paint, path, step, warn
from .errors import Aborted, CommandFailed, ConfigError, MakeError, RecipeError, ToolMissing, UsageError
from .params import Arg, arg
from .recipes import Group, Recipe, group, recipe, registry
from .runner import invoke
from .sh import Result, sh

__all__ = [
    "__version__",
    # authoring
    "recipe",
    "group",
    "invoke",
    "sh",
    "ctx",
    "path",
    "env",
    "fs",
    "config",
    "arg",
    # output
    "step",
    "info",
    "note",
    "warn",
    "echo",
    "paint",
    "confirm",
    # types
    "Recipe",
    "Group",
    "Context",
    "Result",
    "Arg",
    "registry",
    # errors
    "MakeError",
    "UsageError",
    "RecipeError",
    "ConfigError",
    "ToolMissing",
    "CommandFailed",
    "Aborted",
]


def main(argv: list[str] | None = None) -> int:
    """Programmatic entry point, equivalent to running `make` on the command line."""
    from .cli import main as _main

    return _main(argv)
