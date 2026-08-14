"""Recipes for the `alma` repo.

Carried over from `alma/justfile`. Note `--debug-symbols=false`: it strips DWARF
from the dev wasm (about 64 MB down to 5.7 MB, 1.3 MB gzipped on the wire),
turning ~6.7 s cold loads into under a second. DWARF is roughly 79% of the debug
binary and buys only source stepping and line-mapped panic backtraces -- panic
messages, tracing and hot-patching are unaffected. Re-enable it for a deep debug
session with `make web.start -- --debug-symbols=true`.
"""

# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "mkrun>=0.1",
#   "make-recipes-optersoft @ git+ssh://git@github.com/optersoft/make-recipes.git",
# ]
# ///
#
# The runner comes from PyPI; the recipes are private, so ssh. `make --sync`
# pins both into Makefile.py.lock -- a version for mkrun, an exact commit for
# the recipes -- which is versioned in a way `.just-shared/` never was.
# `make --sync --upgrade` is how a pin moves.

from __future__ import annotations

from make import recipe, sh

from make_recipes_optersoft import android, box, database, play, web  # noqa: F401

# Deploy target and smoke host. Real values belong in ~/.make/alma.env, not in a
# tracked file -- `env.require("ALMA_DEPLOY_HOST")` reads them at run time.
# (The smoke host must be one that actually resolves: pointing it at a name with
# no DNS record made every deploy exit 1 on a false smoke failure.)

android.Android.configure(
    module="alma-android",
    pkg="com.optersoft.alma",
    gradle_flags=["--no-configuration-cache"],
    install_flags=["-g"],
)

play.Play.configure(store_dir="docs/store", locales=["es-ES", "en-US"])

web.Web.configure(
    bin="alma",
    port=8005,
    ready="/",
    serve_dir="alma-server",
    serve_flags=["--fullstack", "--debug-symbols=false"],
    watch=["alma-server", "alma-music-web", "alma-web"],
    css_in="alma-web/assets/tailwind.input.css",
    css_out="alma-web/assets/tailwind.css",
)


@recipe(override="play.test-gate")
def test_gate() -> None:
    """Block releases on the shared Rust suite."""
    sh("cargo", "test", "--workspace")
