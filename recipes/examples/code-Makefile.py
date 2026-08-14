"""Recipes for the `code` repo.

Carried over from `code/justfile`: the web dev server and the unattended agent
loop. The agent gate is deliberately a raw command rather than a recipe -- the
loop runs in a fresh worktree, so a gate reaching into an installed package's
recipes could fail for the wrong reason.
"""

# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "mkrun>=0.1",
#   "make-recipes-optersoft @ git+ssh://git@github.com/optersoft/make.git#subdirectory=recipes",
# ]
# ///
#
# The runner comes from PyPI; the recipes are private, so ssh. `make --sync`
# pins both into Makefile.py.lock -- a version for mkrun, an exact commit for
# the recipes -- which is versioned in a way `.just-shared/` never was.
# `make --sync --upgrade` is how a pin moves.

from __future__ import annotations

from make import recipe, sh
from make_recipes_optersoft import agent, web

agent.Agent.configure(gate="cargo check --workspace --features server")

web.Web.configure(
    bin="code",
    port=8080,
    ready="/healthz",
    serve_dir=".",
    serve_flags=["--package", "code-ui", "--fullstack"],
    watch=["code-ui/src", "code-store/src"],
    css_in="code-ui/assets/tailwind.input.css",
    css_out="code-ui/assets/tailwind.css",
)


@recipe(group="dev", requires=["cargo"])
def check() -> None:
    """What the agent loop gates on, runnable by hand."""
    sh("cargo", "check", "--workspace", "--features", "server")
