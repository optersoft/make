# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Read [`README.md`](README.md) first** — it is the authoritative description of what the tool
does and the whole authoring API (`@task`, `sh`, `fs`, `config`, `env`, `testing`). Don't
re-derive any of it here. What follows is only what a reader of the source would get wrong.

`optersoft/make` is **two Python distributions in one uv workspace** — both generic, both
publishable, nothing fleet-specific anywhere in it:

| Path | Distribution | What it is |
|---|---|---|
| `src/make/` | **`mkrun`** | the runner. Published to PyPI, MIT OR Apache-2.0 |
| `rust/` | **`make-rust`** | generic cargo hygiene tasks (`rust.usage/clean/sweep`). Same licence |

They are packaged separately and resolved together: `[tool.uv.workspace] members = ["rust"]`,
with `mkrun = { workspace = true }` in the member, so a runner change is tested against real
tasks in the same commit.

⚠️ **`optersoft-make` no longer lives here.** It was the second member (`optersoft/`) until
2026-08-17, when it moved to the private `make-optersoft` repo (on the forge,
`code.optersoft.com/make-optersoft.git`) so this one can go public without shipping any of the
fleet. Consumers' git sources point there, with no `subdirectory` — the old `make.git` +
`subdirectory = "optersoft"` form resolves to a tip where the package no longer exists.

⚠️ **And it is gone from this repo's history too, as of 2026-08-24.** Moving the directory left
all 11 commits that touched `optersoft/` intact, which meant a public clone could still check out
the fleet's private tasks at any older tag — `v0.2.0` and `v0.3.0` both contained them. Publishing
to `github.com/optersoft/make` was preceded by `git-filter-repo --path optersoft --invert-paths`,
so **every SHA here changed** (35 → 31 commits). Nothing pinned them: this repo reaches consumers
as `mkrun`/`make-rust` on **PyPI**, never as a git dependency.

## Commands

```bash
uv sync --all-extras --all-packages   # --all-packages, or rust/ is not installed
uv run pytest                         # both suites (testpaths covers rust/tests)
uv run ruff check  src tests Makefile.py rust/src rust/tests
uv run ruff format src tests Makefile.py rust/src rust/tests
uv run mk dev.check                   # all of the above, dogfooded
uv run mk dev.bench                   # startup latency against the 150ms budget
```

Lint paths are listed explicitly rather than as directories: **ruff formats Python inside
markdown**, and handing it a whole directory reaches hand-packed docs examples — a docs edit
disguised as a lint fix.

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

**5. This repo must build with no sibling checkout.** No member may name a path or git source
that only resolves on this machine — CI and a bare clone are exactly the environments that
don't have it. `rust/` depends on `mkrun` alone, from the workspace.

## A task package belongs to the project it wraps

The convention this tool exists to enable:

- A repo that owns a tool ships its tasks in **`<repo>/make/`**, as **`<repo>-make`**,
  importing as `<repo>_make`. First one: **`hetzner-make`** (the `box` group, beside the
  `hetzner-box` crate it drives). Then **`dioxus-make`** (the `dioxus` group).
- Fully generic groups that wrap a tool *nobody anywhere* owns — cargo, in `rust/` — live here,
  beside the runner, and publish like it.
- optersoft's fleet groups (android, play, agent, database, secure) are in the private
  `make-optersoft` repo on the forge (`code.optersoft.com/make-optersoft.git`).
- A consumer names the source per package (`git` or `path`) in `[tool.uv.sources]`, and can
  redirect any of them to a local checkout with a gitignored `.make/sources.toml` without
  touching the committed file. Relative paths there resolve against the **repo root**, so one
  line in `~/.make/sources.toml` is correct from every sibling checkout.

## Releasing

`mk dist.release X.Y.Z` tags; `.github/workflows/release.yml` publishes to PyPI through Trusted
Publishing, so no token exists on any laptop. The job's **sdist check is load-bearing**: it
asserts the tarball is `src/make/` + `tests/` + metadata and nothing else — `rust/` releases on
its own schedule and is one `only-include` line away from riding along by accident.

`uv build --package mkrun` names the package deliberately — a bare `uv build` resolves to the
root today, but `dist/` is uploaded wholesale.
