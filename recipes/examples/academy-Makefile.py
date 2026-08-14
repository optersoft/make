"""Recipes for the `academy` repo (xtec.dev/learn).

Carried over from `academy/justfile`. Two things the shell preflight did that
are worth stating plainly, because both are safety properties:

* The dev database lives under the per-checkout state directory, so concurrent
  branches never fight over the turso WAL lock or share a mid-migration schema.
* Only the READ-side credentials are pulled in. The write switches
  (HIVE_TURSO_WRITE, ACADEMY_PAGES_WRITE) and HIVE_TURSO_PATH are deliberately
  left unset, so a dev run can never ship a snapshot or push a page.

The shell version achieved that with `eval "$(grep -E '^(HIVE_BOX_|...)' file)"`.
Here it is a filtered dictionary, which cannot accidentally evaluate anything.
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

from make import env, recipe
from make_recipes_optersoft import box, database, web  # noqa: F401 -- importing registers

web.Web.configure(
    bin="academy",
    port=8003,
    ready="/healthz",
    serve_dir=".",
    serve_flags=["--package", "xtec-web", "--fullstack", "--debug-symbols=false"],
    watch=["xtec-web/src", "xtec-learn/src", "xtec-content/src"],
    css_in="xtec-web/assets/tailwind.input.css",
    css_out="xtec-web/assets/tailwind.css",
)

#: Read-side only. HIVE_BOX_* seeds a missing dev database from the latest prod
#: snapshot through a read-only blob store; the pages token clones the content
#: repo at boot so dev runs on real content. Anything absent degrades to an
#: empty database / the on-disk tree as-is.
READ_SIDE_KEYS = ("HIVE_BOX_", "ACADEMY_PAGES_PROJECT_TOKEN")


@recipe(override="web.preflight")
def preflight() -> None:
    """Dev environment for the learn site."""
    dev = web.state_dir()

    # force=True: this must beat the prod-ish DB_PATH in the secrets file.
    env.export(force=True, DB_PATH=dev / "xtec.db")

    secrets = env.load(env.config_dir() / "academy.env") or env.load("~/.just/academy.env")
    env.export(**{k: v for k, v in secrets.items() if k.startswith(READ_SIDE_KEYS)})
