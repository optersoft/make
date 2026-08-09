# Why this exists

`just` is a good dispatcher wrapped around a language that recipes outgrow. This
is the evidence, taken from `github.com/optersoft/just` — seven shared `.just`
files, 1112 lines, consumed by five repositories — and from its git history.

Nothing below is hypothetical. Each item is either a rule that repository's own
`CLAUDE.md` states as deliberate design, or a `fix(...)` / `revert(...)` commit.

---

## 1. Half the "design rules" are workarounds for missing language features

> **No defaults for consumer variables.** Files that need per-project config
> reference variables like `android_module` without defining them; the consumer
> must set them *before* the `import?`. A missing one is a clear parse error —
> do not "fix" this by adding defaults to the shared file.

This is a good rule *given `just`*. It exists because `just` cannot declare a
required, typed input. The best available error is a parse failure naming a
variable in a file the consumer did not write, and the price is that no shared
variable may have a sensible default.

```python
# make
@config.section("android")
@dataclass
class Android:
    module: str          # required -- no default
    pkg: str             # required
    gradle_flags: list[str] = field(default_factory=list)   # default, still overridable
```

```
error: missing required configuration for [android]:
  android.module  (str)
  android.pkg     (str)

set it in any one of:
  Makefile.py   android.configure(module=...)
  make.toml     [android]
                module = ...
  environment   MAKE_ANDROID_MODULE=...
```

## 2. A shared file cannot ship an overridable default

> **`play-test-gate` is intentionally undefined** in `play.just`. Each consumer
> must define it; `just` errors on duplicate recipes across imports, so a shared
> no-op default could never be overridden.

An entire recipe is left out of the library so that its absence forces the
consumer to act. `make` has both halves:

```python
@play.recipe(name="test-gate", abstract=True)
def test_gate() -> None:
    """Verification every release must pass. Each consumer defines this."""
```

It lists as `[unimplemented]` and refuses to run with instructions. A consumer
replaces it explicitly, and replacing something that does not exist — because
upstream renamed it — is itself an error.

## 3. Settings are global, unique, and fatal

> A `set` in an imported file applies to the whole importing justfile, and a
> setting declared in both is a fatal parse error — there is no
> `allow-duplicate-settings`. It also means a malformed `./.env` fails *every*
> recipe in the consuming repo.

One shared file owning `set dotenv-load` constrains every consumer that imports
it, forever. In `make` there is no global setting to collide over: reading an
env file is a function call inside the recipe that wants it.

## 4. Sharing has no versions, no pinning, and fails silently

The distribution mechanism is a recipe copy-pasted into every consumer:

```make
_shared:
    url=git@github.com:optersoft/just.git
    if [ "$(git -C .just-shared remote get-url origin 2>/dev/null)" = "$url" ]; then
      git -C .just-shared pull -q --ff-only || true
    else rm -rf .just-shared; git clone -q --depth 1 "$url" .just-shared 2>/dev/null || true; fi
```

Every repository runs whatever `HEAD` was when someone last ran it. `|| true`
means a failed pull is indistinguishable from a successful one. There is no way
to say "this repo needs the version from before the rename", and no way to know
what version a given checkout is on.

```python
# make
# /// script
# dependencies = ["mkrun>=0.1", "make-recipes-optersoft>=0.4"]
# ///
```

`make --sync` writes a lockfile. Upgrading is a version bump in a diff.

## 5. The same logic is written three times

The layered-secrets loop appears in `box.just` (as a 12-line bash string
concatenated into every recipe body), inline in `web.just`, and again in Rust in
`just-env/` for applications. `just` settings cannot reach `$HOME`, so each
consumer of the idea reimplements it.

```python
# make
env.layered()   # ~/.make/secrets.env -> ~/.make/<repo>.env -> ./.env
```

## 6. The logic already left `just`

> **Logic tiering.** Recipe bodies stay thin bash glue over one CLI call.
> Anything heavier gets promoted: real programs land in `play/` as standalone
> scripts; domain logic lands in a Rust crate.

By its own rule, the real work is in `play/reviews.py`, `play/metadata.sh`,
`play/promote.sh`, `agent/todo_loop.py`, and the `hetzner-box` crate. What `just`
still owns is the *interface* — and that interface is a stringly-typed shell
boundary re-crossed on every call, with `PKG=... PLAY_JSON=...` handed over as
environment variables because there is no other way to pass an argument.

## 7. The bugs were all in logic that could not be tested

> `just --fmt --check --unstable` — the closest thing to a test suite.

It verifies that the file parses. Here is what shipped anyway:

| Commit | What broke |
|---|---|
| `fix(web): reap dev-DB lock holders, not just the port listener` | The teardown reaped by port. The thing that blocks the next boot is the turso WAL lock, which an orphan can hold from no port at all — so the next start 500s with "… is locked". |
| `fix(web): resolve the repo name through --git-common-dir` | `--show-toplevel` returns the *worktree* directory, so a worktree looked up `~/.just/school.env`, found nothing, and started with **no application environment at all**. Silently — a missing layer is not an error — so sign-in could not work in the very checkout the per-checkout port exists to enable. |
| `fix(web): export the canonical AXUM_OAUTH_BASE_URL, not just the legacy name` | `axum-oauth` resolves first-found-wins, canonical first. Exporting only the legacy name was a silent no-op for the two repos that pin the canonical one, so the app redirected to a stale port while the server listened on another. |
| `revert(web): serve on the fixed web_port, drop the port grid` | A branch-hashed port grid, reverted. |
| `revert(web): drop the web_css_watch CSS fast-path` | |
| `revert(web): source the dev-server port from web_port, not JUST_WEB_PORT` | Per-repo config cannot describe one checkout. |

Every one of these is a property of a small function. In `make` they are tests:

```python
def test_the_database_reap_finds_an_orphan_that_holds_no_port(tmp_path):
    ...
    assert web.reap_database_holders(dev) == [4711]

def test_repo_name_is_the_main_checkout_even_inside_a_worktree(tmp_path):
    assert env.repo_name(linked) == "xtec"      # not "school"

def test_the_base_url_is_exported_under_both_names():
    exported = web.oauth_env(8105)
    assert exported["AXUM_OAUTH_BASE_URL"] == "http://localhost:8105"
    assert exported["HIVE_AUTH_BASE_URL"] == "http://localhost:8105"
```

## 8. `{{ }}` is textual splicing into bash

```make
box-ls PATH=".":
    hetzner-box --dir "{{ box_bucket }}" ls "{{ PATH }}"
```

The value is pasted into shell source before the shell parses it. Correctness
depends on the surrounding quotes being right at every site, and on remembering
`quote()` where they are not.

```python
sh("hetzner-box", "--dir", Box.bucket, "ls", path)
```

No interpolation step exists, so a path containing a space, a quote or a `$` is
data and cannot become syntax.

---

## What `just` still does better

Honesty is worth more than a clean sweep:

- **Startup.** `just` starts in about 5 ms; `make` takes about 25 ms. Both are
  imperceptible, but `just` is genuinely faster, and staying under a 150 ms
  budget is an explicit, tested constraint here rather than a free property.
- **One binary, no runtime.** `just` is a single Rust binary. `make` needs
  Python, and shared recipes need `uv`.
- **Bash is right for one-liners.** A recipe that is genuinely `cargo test` is
  shorter in a justfile. `sh.bash(...)` exists so such a body can move across
  verbatim and stay that way.

The case for switching is not that `just` is bad. It is that the recipes stopped
being one-liners years ago, and the language never grew with them.
