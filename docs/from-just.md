# Coming from `just`

`just` is a good dispatcher wrapped around a language that recipes outgrow. Once
a task body has a loop, an `if`, or three variables that must agree, you are
writing shell inside string interpolation with no types, no tests, and no way to
share it except copying a file.

Migration is incremental and reversible: add a `Makefile.py`, keep the
`justfile`, and have the old names delegate while the new ones are proven.

```make
# justfile -- unchanged names, new implementation
web-start *ARGS:
    @mk web.start {{ ARGS }}
```

Retire a group from the justfile only once its `make` version has run for a
while. To roll back entirely, delete `Makefile.py`; the justfile never stopped
working.

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

## What `just` still does better

- **Startup.** `just` starts in about 5 ms; this takes about 30 ms. Both are
  imperceptible, but `just` is genuinely faster, and staying under a 150 ms
  budget here is an explicit, tested constraint rather than a free property.
- **One binary, no runtime.** `just` is a single Rust binary. This needs Python,
  and shared task packages need `uv`.
- **Bash is right for one-liners.** A task that is genuinely `cargo test` is
  shorter in a justfile. `sh.bash(...)` exists so such a body can move across
  verbatim and stay that way.

The case for switching is not that `just` is bad. It is that recipes stop being
one-liners, and the language does not grow with them.
