"""Recipes for the `broker` repo.

Carried over from `broker/justfile`. The `web_preflight` block -- fourteen lines
of `export VAR="${VAR:-default}"` interpolated into the recipe body -- is now an
override of `web.preflight`, which is a thing `just` could not express: a shared
file's hook that a consumer replaces.
"""

# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "mkrun @ git+ssh://git@github.com/optersoft/make.git",
#   "make-recipes-optersoft @ git+ssh://git@github.com/optersoft/make.git#subdirectory=recipes/optersoft",
# ]
# ///
#
# Git references until both packages are on PyPI; `make --sync` pins the exact
# commit in Makefile.py.lock, so this is versioned in a way `.just-shared/`
# never was. Once published, these become "mkrun>=0.1" and
# "make-recipes-optersoft>=0.1" and nothing else changes.

from __future__ import annotations

from make_recipes_optersoft import box, database, web  # noqa: F401 -- importing registers

from make import env, recipe, sh, step

web.Web.configure(
    bin="broker-web",
    port=8001,
    ready="/",
    serve_dir=".",
    serve_flags=["--package", "broker-web", "--fullstack"],
    watch=["broker-web", "broker-client", "broker-data", "broker-feed", "broker-oracle"],
    css_in="broker-web/assets/tailwind.input.css",
    css_out="broker-web/assets/tailwind.css",
)


@recipe(override="web.preflight")
def preflight() -> None:
    """Dev environment for the broker server.

    `env.export` does not overwrite something already set, so each line here has
    the `${VAR:-default}` semantics the shell version relied on.
    """
    dev = web.state_dir()
    (dev / "db").mkdir(parents=True, exist_ok=True)
    (dev / "store").mkdir(parents=True, exist_ok=True)

    # hive-server writes its ban list and trusted-IP snapshot to the cwd; keep
    # that under the dev state directory so runs never litter the repo tree.
    env.export(TRUST_FILE=dev / "trust.bin", BAN_FILE=dev / "banned")

    # Offline read/render loop against the box-synced snapshot (`make dev.sync`).
    env.export(DEV_OFFLINE="1")

    # Dev serves plain HTTP, so auth cookies must not be Secure -- the browser
    # drops a Secure cookie over http:// and login fails with "Missing or
    # expired login state". Production has TLS and leaves this unset.
    env.export(AUTH_COOKIE_SECURE="false")

    # The Google-login allow-list (CSV) -- the sole auth source, read at boot
    # and watched live.
    # The real allow-list lives in ~/.make/broker.env, not in a tracked file.
    env.export(BROKER_AUTH_EMAILS=env.get("BROKER_AUTH_EMAILS", ""))
    env.export(STORE_DIR=dev / "store", ACCOUNT_DB=dev / "db/account.db")

    # The OAuth callback follows the port: web.start exports the base URL under
    # both AXUM_OAUTH_BASE_URL and HIVE_AUTH_BASE_URL, and axum-oauth derives
    # {base}/auth/google/callback from it.


@recipe(group="dev", requires=["cargo"])
def check() -> None:
    """Type-check the workspace with the server feature on."""
    sh("cargo", "check", "--workspace", "--features", "server")


@recipe(group="dev", needs=["box.target"])
def sync() -> None:
    """Pull the latest snapshot from the box into the dev store."""
    step("syncing the dev snapshot")
    box.get("snapshots/latest.tar.gz", web.state_dir() / "store/latest.tar.gz")
