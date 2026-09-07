# Coming from `just`

A reference for a repository that already has a `justfile`. It is a mapping, not
an argument: what each construct you are using is called here.

Migration is incremental and reversible. Add a `Makefile.py`, keep the `justfile`,
and have the old names delegate while the new ones are proven:

```make
# justfile -- unchanged names, new implementation
web-start *ARGS:
    @mk web.start {{ ARGS }}
```

Retire a group from the justfile only once its `make` version has run for a while.
To roll back entirely, delete `Makefile.py`; the justfile never stopped working.

## Translation table

| `just` | `make` |
|---|---|
| `some_var := "value"` before an `import?` | `SomeConfig.configure(some_var="value")` |
| `import? '.just-shared/web.just'` | `from your_recipe_package import web` |
| `just _shared` | nothing — it is a dependency |
| (no equivalent) | `mk --sync` to pin, `mk --sync --upgrade` to move the pin |
| `just --list` | `make` or `mk --list` |
| `just -n <recipe>` | `mk -n <recipe>` (a real dry run) |
| `just --fmt --check` | `mk dev.test` |
| `[group('web')]` + a `web-` name prefix | `group="web"`, so `web.start` |
| `[private]` | `hidden=True`, or a leading underscore |
| `alias b := a` | `aliases=["b"]` |
| an `alias` line per recipe in a group | `group("dioxus", alias="dx")`, so `dx.start` |
| `task: dep1 dep2` | `needs=[dep1, dep2]` |
| `*ARGS` | `*args: str` |
| `{{ ARGS }}` spliced into bash | `sh("cmd", *args)` |
| `{{ quote(x) }}` | nothing to do — values are never re-parsed |
| `command -v tool \|\| exit` | `requires=["tool"]` |
| `promote.sh --yes` | `dangerous=True` |
| `env_var_or_default('X', y)` | `env.get("X", y)` |
| a `for f in ~/.just/…; do set -a; . "$f"; set +a; done` loop | `env.layered()` |
| `source_directory()` | `Path(__file__).parent` |
| `just -f other.just <recipe>` | `mk -F other/Makefile.py <recipe>` |

## Two things to keep in mind

- **A justfile can stay.** Nothing here wants the whole file migrated at once, and
  a recipe that is genuinely one line of bash is fine where it is; `sh.bash(...)`
  is there for when you do move it and want it verbatim.
- **`mk` needs a runtime.** Python 3.11+, plus `uv` for shared task packages,
  where a justfile needed one binary. On a machine where that is not available,
  stay put.

Why this tool works the way it does, decision by decision: [`design.md`](design.md).
