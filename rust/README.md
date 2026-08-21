# make-rust

Generic cargo hygiene for [`mkrun`](https://github.com/optersoft/make): find every
`target/` directory a repository actually owns — multi-workspace repos have
several, and a root `cargo clean` misses most of the bytes — then report or
reclaim them.

```python
# Makefile.py in a consuming repo
# /// script
# requires-python = ">=3.11"
# dependencies = ["mkrun>=0.3", "make-rust>=0.1"]
# ///
from make_rust import rust  # importing is what registers the group
```

```console
$ mk rust.usage                    # every target/ dir: size + idle age
$ mk rust.gc                       # delete superseded name-<hash> artifacts, keep newest 2
$ mk rust.clean --older-than 30    # delete the ones nothing touched in 30 days
$ mk rust.sweep                    # cargo sweep: trim stale artifacts, keep hot ones
```

`gc` is the one that matters day-to-day. Cargo appends a new `name-<hash>`
artifact on every feature/flag/dependency change and never deletes the old
ones — one week of `dx serve` left 88 copies of one rlib — and *time*-based
sweeping (`sweep`, `clean`) cannot see churn that happened this week. `gc`
groups entries in `deps/`, `examples/`, `incremental/`, `build/` and
`.fingerprint/` by their unhashed name and keeps the newest `--keep` (default
2, so rust-analyzer's check and dx's build can coexist). Deleting a live
artifact is safe — cargo rebuilds what it misses — just don't run it while a
build is in flight.

Nothing here is specific to any owner or fleet. A `target/` dir counts only if
cargo made it — a `CACHEDIR.TAG` inside, or a `Cargo.toml` beside it — so a
directory that merely shares the name is never touched. `clean` deletes with the
runner's `fs`, so `mk -n rust.clean` shows exactly what would go and removes
nothing. `sweep` wraps [`cargo-sweep`](https://crates.io/crates/cargo-sweep),
the gentler default: artifacts untouched for N days go, the hot incremental
state stays.

The group name is `rust`, and it merges with a repo's own `rust.*` tasks —
groups are namespaces, so a repo with `rust.abi` or `rust.wasm` of its own gets
one menu, not a collision. Only a same-named task would conflict; this package
claims `usage`, `clean` and `sweep`, nothing else.
