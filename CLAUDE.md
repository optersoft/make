# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Read [`README.md`](README.md) first** — it is the authoritative description of what the tool
does and the whole authoring API (`@task`, `sh`, `fs`, `config`, `env`, `testing`). Don't
re-derive any of it here. What follows is only what a reader of the source would get wrong.

`optersoft/make` is **two Python distributions in one uv workspace**:

| Path | Distribution | What it is |
|---|---|---|
| `src/make/` | **`mkrun`** | the runner. Generic, published to PyPI, MIT OR Apache-2.0 |
| `optersoft/` | **`optersoft-make`** | optersoft's own tasks. Internal, never published |

They are packaged separately and resolved together: `[tool.uv.workspace] members = ["optersoft"]`,
with `mkrun = { workspace = true }` in the member, so a runner change is tested against real
tasks in the same commit.

## Commands

```bash
uv sync --all-extras --all-packages   # --all-packages, or optersoft/ is not installed
uv run pytest                         # both suites (testpaths covers optersoft/tests)
uv run ruff check  src tests Makefile.py optersoft/src optersoft/tests optersoft/examples
uv run ruff format src tests Makefile.py optersoft/src optersoft/tests optersoft/examples
uv run mk dev.check                   # all of the above, dogfooded
uv run mk dev.bench                   # startup latency against the 150ms budget
```

Lint paths are listed explicitly rather than as `optersoft/`: **ruff formats Python inside
markdown**, and handing it the whole directory reformats the hand-packed examples in
`optersoft/docs/` — a docs edit disguised as a lint fix.

## The five things that are easy to break

**1. The command is `mk`. There is no `make` command.** `[project.scripts]` declares `mk` only;
a `make` script would shadow GNU make on the PATH of every Unix machine. The *import* name is
still `make`, and task files are still `Makefile.py` / `mk.py`. `tests/test_cli.py` asserts the
distribution declares exactly `{mk}` — that test is the guard, because nothing else notices.

**2. `uv run --with` cannot see `[tool.uv.sources]`.** Only `uv sync --script` reads the task
file. `bootstrap.reexec()` picks script mode whenever the file declares a source, has a local
override, or has a lock, and the `--with` path otherwise — which is faster and keeps the
`--with-editable` affordance for files that don't declare `mkrun` themselves. Getting this
backwards is **silent**: the requirement still resolves, just to whatever PyPI has under that
name. See `tests/test_sources.py`, which exists for exactly this.

**3. Never fall back to `--with` when a source is declared.** That is what would fetch the
stranger's package. `reexec` raises instead.

**4. Startup latency is a feature.** ~30ms, budget 150ms, enforced by `tests/test_startup.py`. A
heavy import at module scope is the usual cause; groups are imported lazily through
`__getattr__` for the same reason.

**5. This repo must build with no sibling checkout.** Nothing may declare `hetzner-make` as a
dependency or extra — not even for the `box` shim — or CI and a bare clone both fail on
`Distribution not found at ../hetzner/make`. `optersoft_make.box` re-exports it *softly* and
raises a `MakeError` naming the package when it is absent.

## A task package belongs to the project it wraps

The convention this tool exists to enable, and which this repo now follows:

- A repo that owns a tool ships its tasks in **`<repo>/make/`**, as **`<repo>-make`**,
  importing as `<repo>_make`. First one: **`hetzner-make`** (the `box` group, beside the
  `hetzner-box` crate it drives).
- `optersoft/` here is what is *left* — the groups wrapping tools nobody owns (android, play,
  agent, database, secure). It is named after its owner, not this repo, for that reason. `web`
  should move to `dioxus/make/` next, since what it globs for Tailwind is that repo's crates.
- A consumer names the source per package (`git` or `path`) in `[tool.uv.sources]`, and can
  redirect any of them to a local checkout with a gitignored `.make/sources.toml` without
  touching the committed file. Relative paths there resolve against the **repo root**, so one
  line in `~/.make/sources.toml` is correct from every sibling checkout.

⚠️ `play` imports `android` (`play.py`), so those two cannot be separated.

## Releasing

`mk dist.release X.Y.Z` tags; `.github/workflows/release.yml` publishes to PyPI through Trusted
Publishing, so no token exists on any laptop. The job's **sdist check is load-bearing**: it
asserts the tarball is `src/make/` + `tests/` + metadata and nothing else. `optersoft/` is
internal, lives in this repo, and is one `only-include` line away from being published by
accident.

`uv build --package mkrun` names the package deliberately — a bare `uv build` resolves to the
root today, but `dist/` is uploaded wholesale.
