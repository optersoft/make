# Migrating a repo from `just` to `make`

The order is by risk, lowest first, and every step is reversible: the `justfile`
stays in place and delegates, so `just web-start` keeps working while `make
web.start` is proven.

## 0. Install

```bash
uv tool install mkrun                     # the distribution; installs `make` and `mk`
```

The distribution is `mkrun` because `make` and `mk` are taken on PyPI by
unrelated projects. Declare `"mkrun>=0.1"` in a recipe file's dependencies —
`"make"` would install someone else's jinja2 templating tool.

`make` shadows GNU make on `PATH`. In a repo that also has a real `Makefile`,
use `mk` — it is the same program.

## 1. Point the secrets at the new home (optional)

`make` reads `~/.make/secrets.env` and `~/.make/<repo>.env` **first**, then falls
back to `~/.just/`. Nothing has to move; moving is a `mv` when you feel like it.

```bash
mkdir -p ~/.make && cp ~/.just/*.env ~/.make/ && chmod 600 ~/.make/*.env
```

`~/.just/` keeps working either way, so both runners can be live at once.

## 2. Add a recipe file

```python
# Makefile.py
# /// script
# requires-python = ">=3.11"
# dependencies = ["mkrun>=0.1", "make-recipes-optersoft>=0.1"]
# ///
"""Recipes for <repo>."""

from make import recipe, sh
from make_recipes_optersoft import android, box, database, play, web

# The variables that used to sit above each `import?`, now typed and checked.
android.Android.configure(module="drive-android", pkg="com.optersoft.drive")
play.Play.configure(store_dir="docs/store", locales=["en-US", "es-ES", "fr-FR", "pt-PT"])
web.Web.configure(
    bin="drive-web", port=8002, serve_dir=".", serve_flags=["--fullstack"],
    watch=["crates"], css_in="drive-web/assets/tailwind.input.css",
    css_out="drive-web/assets/tailwind.css",
)

# What `play-test-gate:` was. It is `abstract=True` upstream, so this is an
# override rather than a name that must not collide.
@recipe(override="play.test-gate")
def test_gate() -> None:
    """Block releases on the unit and Android Auto suites."""
    sh("./gradlew", ":drive-android:test")
```

```bash
make --sync      # resolve and pin
make             # list
make --doctor    # every declared tool, and which recipe wants it
```

Add `.venv/` and `Makefile.py.lock`'s siblings to `.gitignore` as needed;
**commit the lockfile**.

## 3. Bridge the old names

Keep the `justfile` and delegate, so muscle memory and any script calling
`just …` keep working:

```make
web-start *ARGS:
    @make web.start {{ ARGS }}
web-stop:
    @make web.stop
box-ls PATH=".":
    @make box.ls {{ PATH }}
```

Retire a group from the justfile only after its `make` version has run for a
week.

## 4. Port group by group

| Order | Group | `just` lines | Notes |
|---|---|---|---|
| 1 | `secure` | 48 | Pure function, no I/O. |
| 2 | `database` | 32 | One CLI call. |
| 3 | `box` | 153 | Already thin wrappers over `hetzner-box`. |
| 4 | `agent` | 66 | Was already Python; the scripts ship inside the package now. |
| 5 | `play` | 114 + 3 scripts | The scripts collapse into recipes with typed arguments. |
| 6 | `android` | 200 | Device selection, signing, publish. |
| 7 | `web` | 499 | Last. Write the tests first — that is the whole point. |

## Translation table

| `just` | `make` |
|---|---|
| `web_port := "8002"` before an `import?` | `web.Web.configure(port=8002)` |
| `import? '.just-shared/web.just'` | `from make_recipes_optersoft import web` |
| `just _shared` | nothing — it is a dependency |
| (no equivalent) | `make --sync` to pin, `make --sync --upgrade` to move the pin |
| `just --list` | `make` or `make --list` |
| `just -n <recipe>` | `make -n <recipe>` (a real dry run) |
| `just --fmt --check` | `make dev.test` |
| `[group('web')]` + `web-` prefix | `group="web"`, so `web.start` |
| `[private]` | `hidden=True`, or a leading underscore |
| `alias android-release := play-publish` | `aliases=["android-release"]` |
| `recipe: dep1 dep2` | `needs=[dep1, dep2]` |
| `*ARGS` | `*args: str` |
| `{{ ARGS }}` spliced into bash | `sh("cmd", *args)` |
| `{{ quote(x) }}` | nothing to do — values are never re-parsed |
| `command -v tool \|\| exit` | `requires=["tool"]` |
| `promote.sh --yes` | `dangerous=True` |
| `env_var_or_default('X', y)` | `env.get("X", y)` |
| the `for f in ~/.just/…` loop | `env.layered()` |
| `source_directory()` | `Path(__file__).parent` |
| `just -f other.just <recipe>` | `make -F other/Makefile.py <recipe>` |

## Things that deliberately did not change

- **`HIVE_BOX_*` and `~/.ssh/hive-box-<login>`.** Operational contracts read by
  the `hetzner-box` CLI and present on every VM. Renaming them breaks box access
  everywhere with nothing to catch it — the recipes would run and simply fail to
  authenticate.
- **`AXUM_OAUTH_BASE_URL` *and* `HIVE_AUTH_BASE_URL`.** Both are still exported.
  `axum-oauth` resolves first-found-wins, and two repos pin the canonical name.
- **`JUST_WEB_PORT`** in a checkout's `./.env` still claims that checkout's port,
  alongside the new `MAKE_WEB_PORT`.
- **Play's manual steps.** The first upload of a new package, halting a rollout,
  data-safety and content-rating forms — all still Console actions on purpose.

## Rolling back

Delete `Makefile.py`. The `justfile` never stopped working.
