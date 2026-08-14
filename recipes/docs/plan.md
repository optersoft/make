# `make` — a command runner whose recipes are Python

**Package:** `make` (PyPI) · **CLI:** `mk` · **Repo:** `optersoft/make` · **Runtime:** uv

A general-purpose `just` replacement, aimed at anyone — optersoft's recipes are just the
first consumer package, not part of the tool. The one idea: **a recipe is a Python
function**, so it can be typed, tested, imported, versioned and published to PyPI like any
other code.

---

## 1. What `just` actually costs us

Evidence from `optersoft/just` (7 shared `.just` files, 1112 lines) and its 5 consumers
(broker, drive, alma, xtec, code). These aren't hypotheticals — each maps to a rule in that
repo's `CLAUDE.md`, a `fix(...)`/`revert(...)` commit, or a workaround in the source.

| # | Problem | Evidence |
|---|---|---|
| 1 | **`{{ }}` is textual splicing into bash.** No types, no escaping guarantees; `quote()` is manual and easy to forget. `box.just` pastes a 12-line bash blob (`prep`) into *every* recipe via string concatenation. | `box.just:52-70`, `agent.just` `{{ quote(todo_loop_gate) }}` |
| 2 | **No way to declare a required input.** So the house rule became "no defaults for consumer variables" — a missing one surfaces as an obscure *parse* error. A workaround dressed as a design rule. | `CLAUDE.md` "No defaults for consumer variables" |
| 3 | **Flat namespace, colliding imports.** Duplicate recipes across imports = fatal, so a shared file *cannot* ship an overridable default (`play-test-gate` is deliberately undefined). Duplicate *settings* are fatal with no opt-out at all. Variables need `set allow-duplicate-variables`. | `CLAUDE.md` "`play-test-gate` is intentionally undefined", "Settings … are global and unique" |
| 4 | **No dependency distribution.** Sharing is `git clone --depth 1` into a gitignored `.just-shared/`, driven by a `_shared` recipe copy-pasted into every consumer justfile. No versions, no pinning, no lockfile. `pull --ff-only \|\| true` fails silently and leaves stale recipes. Every repo runs whatever HEAD was. | `README.md:25-45`, `drive/justfile:46-52` |
| 5 | **Prefix-by-convention.** `web-`, `box-`, `play-` exist because there is one namespace; `[group()]` is cosmetic. | every `.just` file |
| 6 | **Settings can't reach `$HOME`.** So the layered-secrets loop is hand-written inside recipe bodies — and written *three times*: `box.just`'s `prep`, `web.just`'s inline loop, and again in Rust in `just-env/` for apps. | `CLAUDE.md` "Layered secrets, sourced at runtime" |
| 7 | **Untestable.** The "closest thing to a test suite" is `just --fmt --check`. `web.just` is 499 lines of port arithmetic, process matching and reap logic with zero tests — and it is exactly where the bugs were: `fix(web): reap dev-DB lock holders, not just the port listener`; `fix(web): resolve the repo name through --git-common-dir` (worktrees sourced a nonexistent env file → sign-in silently broken); `fix(web): export the canonical AXUM_OAUTH_BASE_URL` (silent no-op for 2 of 5 repos); two `revert(web)` commits. | git log |
| 8 | **Logic escapes anyway.** The house rule is "recipe bodies stay thin bash glue; anything heavier gets promoted" → `play/reviews.py`, `play/promote.sh`, `agent/todo_loop.py`, and a whole Rust crate. The real work already left `just`; what remains is a stringly-typed shell boundary in front of it. | `CLAUDE.md` "Logic tiering" |
| 9 | **Nothing is reusable as a library.** `todo_loop.py` can't be imported by anything. `just-env` exists solely because Rust apps can't call a justfile. | `just-env/`, `agent/` |
| 10 | **Shell-dialect fragility.** Every body re-declares `#!/usr/bin/env bash` + `set -euo pipefail`; behaviour rides on BSD-vs-GNU flags of `lsof`, `pgrep`, `pkill`, `du`, `find`. | all files |
| 11 | **Missing outright:** real dry-run (`just -n` only expands text), parallelism, structured/JSON output, staleness skipping, tool-presence declarations, generated completions. | — |

**The through-line:** `just` is a good *dispatcher* wrapped around a bad *programming
language*, and our recipes long ago outgrew the dispatcher.

---

## 2. Design

### 2.1 A recipe is a function

Repo root holds **`mk.py`** (not `make.py` — that would shadow `import make`):

```python
# /// script
# requires-python = ">=3.12"
# dependencies = ["make>=0.3", "mk-recipes-web>=0.4"]
# ///
from make import recipe, sh, config

import mk_recipes_web as web          # registers the `web.*` group

web.configure(bin="alma", port=8005, serve_dir="alma-server",
              watch=["alma-server", "alma-music-web", "alma-web"])

@recipe(group="app")
def test(*, fast: bool = False) -> None:
    """Run the test suite."""
    sh("cargo", "test", *(["--lib"] if fast else []))

@recipe(override="web.test_gate")
def gate() -> None:
    sh("cargo", "check", "--features", "server")
```

```
mk                     # list recipes, grouped, with signatures + docstrings
mk app.test --fast
mk web.start --port 8105
mk help web.start
```

### 2.2 CLI from the signature

Parameters **before `*`** become positional CLI arguments; parameters **after `*`** become
`--options`. No separate schema to keep in sync.

| Python | CLI |
|---|---|
| `path: Path` (no default) | required positional, `Path`-converted |
| `dest: str = "."` | optional positional |
| `*, port: int = 8001` | `--port 8001` |
| `*, force: bool = False` | `--force` |
| `*, color: bool = True` | `--no-color` |
| `*, track: Literal["alpha","prod"]` | `--track {alpha,prod}` |
| `*, locale: list[str] = []` | `--locale` (repeatable) |
| `*args: str` | trailing passthrough (`mk web.start -- --hot-patch`) |

A missing required value fails *loudly and usefully* — naming the key, its type, the recipe
that needed it and where to set it. That is problem #2 solved properly rather than by
withholding defaults.

### 2.3 Namespaces are Python modules

`web.start`, `box.ls`, `play.promote` — real namespaces, so no prefix convention and no
cross-package collisions (#3, #5). Redefinition rules are explicit, not accidental:

- Duplicate recipe name without `override=` → error naming both definitions.
- `@recipe(override="web.start")` → error if there is nothing to override (catches renames
  upstream).
- `@recipe(abstract=True)` in a shared package → appears in `mk --list` as *unimplemented*
  and errors at run time with "define `play.test_gate` in your mk.py". This is exactly the
  `play-test-gate` hole, closed.

### 2.4 Sharing = uv dependencies

The headline feature, and the reason to build this at all.

- **Script mode (default):** PEP 723 inline metadata at the top of `mk.py`; `mk` runs itself
  under `uv run --script`. First run resolves; `uv lock --script mk.py` pins. A repo needs
  exactly one file.
- **Project mode:** `[tool.mk]` in an existing `pyproject.toml`, deps in
  `[project.dependencies]`, `uv.lock` committed.
- Private packages work identically via `git+ssh://…` deps — so optersoft's own recipes get
  versioning and pinning for free.
- Community packages are ordinary wheels named `mk-recipes-*`. Registration is by explicit
  `import`, not entry-point magic: importing is what makes a group appear, so `mk --list`
  never depends on what happens to be installed.

This retires `.just-shared/`, the `_shared` recipe, and the "every repo is on some HEAD"
problem in one move (#4).

### 2.5 `sh()` that isn't a footgun

```python
sh("hetzner-box", "--dir", bucket, "ls", path)     # argv list — no quoting hazard
sh.out("git", "rev-parse", "HEAD")                 # captured, stripped
sh.ok("command", "-v", "fastlane")                 # bool
sh.pipe("du -sk target | cut -f1")                 # opt-in shell, visibly marked
```

`ctx.dry_run` is honoured by every one of them (a real dry run, not text expansion).
`@recipe(requires=["fastlane", "uv"])` fails loud up front instead of a copy-pasted
`command -v … || exit` per body (#1, #10, #11).

### 2.6 Layered env/secrets, once

```python
from make import env
env.layered()   # ~/.mk/secrets.env → ~/.mk/<repo>.env → ./.env   (last wins)
```

One tested implementation replacing three (#6). Repo name resolves through
`git rev-parse --path-format=absolute --git-common-dir` so **worktrees are correct by
construction** — the bug already paid for once in `fix(web): resolve the repo name through
--git-common-dir`. For migration it also reads `~/.just/` when present, so nothing has to
move on day one. Rust apps keep using `just-env` unchanged; it reads the same files.

### 2.7 Testing — the point of the whole exercise

Recipes are functions, so:

```python
def test_web_start_reaps_db_holders(recorder):
    web.start(port=8105)
    assert recorder.saw("kill", "-9")
    assert not recorder.saw_any_matching("dx serve")   # dry-run never execs
```

`make.testing` ships a recording fake for `sh`, a temp-repo fixture, and golden `--dry-run`
snapshots. `web.just`'s 499 lines become a module with unit tests around the port
arithmetic, the two reap paths and the env precedence chain — the three things that actually
broke (#7).

### 2.8 Safety, structural not advisory

`@recipe(dangerous=True)` requires `--yes` or an interactive confirm — encoding the
"outward-facing actions are confirm-gated" rule in the decorator instead of in prose and a
`--yes` flag each script re-implements.

### 2.9 Beyond parity

`mk -j` (parallel, since recipes are functions) · `@recipe(needs=[...])` memoized per run ·
opt-in staleness (`inputs=`/`outputs=` → skip when unchanged) · `--json` for CI ·
`mk doctor` (checks every declared `requires`) · shell completions generated from
signatures.

**Non-goals:** not a build system (no rebuild DAG beyond opt-in staleness), not a package
manager (uv does that), not a Python-only tool — recipes shell out to anything.

---

## 3. Phases

| Phase | Deliverable | Notes |
|---|---|---|
| **0** | `git init`, PyPI name check for `make`/`mk` (fallback `mk-run`), repo skeleton, CI | ~½ day |
| **1** | **Core**: registry, signature→CLI, `sh`, `env`, `ctx`, discovery/walk-up, `--list`/`help`, `--dry-run`, errors. Full pytest suite. | The whole tool's quality lives here |
| **2** | **Distribution**: script mode + project mode, `uv lock` integration, `mk sync`, publish `make` to PyPI, `uvx mk` works | Ship OSS-ready at this point |
| **3** | **Port the easy groups** → `mk-recipes-optersoft`: `secure` (48 lines), `database` (32), `box` (153, already thin CLI wrappers). Validate on one low-risk repo (`code` or `isard`). | Proves the model end-to-end |
| **4** | **`agent` + `play`** — `todo_loop.py`/`deny.py` become importable modules (near-free), `play/*.py` collapse into recipes with typed args | Highest value per line moved |
| **5** | **`android`, then `web`** — the hard one; port/reap/process primitives land as tested library functions first, recipes on top | Do last, with tests written before the port |
| **6** | **Extras**: `-j`, staleness, `--json`, `mk doctor`, completions; retire `optersoft/just` | — |

**Coexistence bridge:** during 3–5 each consumer's `justfile` keeps its recipe names and
delegates (`web-start: mk web.start`), so muscle memory and any scripts calling `just …`
keep working. A group is retired from `just` only once its `mk` version has run for a week.

---

## 4. Risks

- **Startup latency.** `just` starts instantly; Python + uv resolution does not. This is the
  single most common reason a runner gets abandoned. Mitigate with lazy imports, a cached uv
  environment, and no work at import time; **budget ≤150 ms warm** and measure it in CI from
  phase 1. If it can't be met, the project isn't worth shipping.
- **Name availability.** `make` on PyPI may be taken; `import make` in a repo that also has a
  stray `make.py` would shadow. Resolve in phase 0 — fallback `mk-run` distributing the same
  `make` import name, or rename the import to `mk`.
- **`web.just` is load-bearing.** It is the daily dev loop for 4 repos. Ported last, behind
  tests, behind the bridge, with the old recipe one `git checkout` away.
- **Community adoption is not free.** A generic runner competes with `just`, `task`, `mise`,
  `invoke`, `doit`. The differentiator to lead with is the one none of them have: **recipes
  distributed as versioned, lockable, testable PyPI packages.**

---

## 5. Open questions

1. `mk.py` vs `.mk/main.py` for multi-file recipe sets in one repo — support both, or start with a single file?
2. Should `mk` re-exec itself under `uv run --script` transparently, or require the PEP 723 shebang on `mk.py` (explicit, but one more thing to get right)?
3. Windows: in scope, or POSIX-first and revisit?
