# make-recipes-optersoft

optersoft's shared recipes for [`mkrun`](https://github.com/optersoft/make) —
the successor to `github.com/optersoft/just`.

**Private.** The runner itself is public and generic; this repository is the
operational half: box account conventions, the Play release pipeline, deploy
hosts, per-app dev ports, and the unattended-agent boundary.

```python
# Makefile.py in a consuming repo
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "mkrun",
#   "make-recipes-optersoft @ git+ssh://git@github.com/optersoft/make-recipes.git",
# ]
# ///
from make_recipes_optersoft import box, web

web.Web.configure(bin="alma", port=8005, serve_dir="alma-server")
```

```bash
make --sync             # pin the exact commit into Makefile.py.lock
make --sync --upgrade   # move the pin, deliberately, as a reviewable diff
make                    # list
```

## Groups

Importing a group is what registers it; nothing is registered implicitly.

| Group | What it covers |
|---|---|
| `agent` | the unattended TODO.md loop (`claude -p` until the file is empty) |
| `android` | device builds, signing, versionCode, the web-download APK |
| `box` | Hetzner Storage Box, over the `hetzner-box` CLI |
| `database` | render a doc's mermaid blocks to a zoomable SVG |
| `play` | ship to Google Play, ramp the rollout, sync the store listing |
| `secure` | generate a strong random password |
| `web` | the Dioxus fullstack dev server, its dev Chrome, and Tailwind |

Ready-to-drop-in consumer files for broker, drive, alma, code and academy are in
`examples/`. The rollout order and the `just` → `make` translation table are in
`docs/migration.md`; `docs/why.md` is the evidence for why this move happened at
all.

## Things that deliberately did not change

- **`HIVE_BOX_*` and `~/.ssh/hive-box-<login>`** keep their names. They are
  operational contracts read by the `hetzner-box` CLI and present on every VM;
  renaming them breaks box access everywhere with nothing to catch it.
- **`~/.just/secrets.env` and `~/.just/<repo>.env`** are still read, after
  `~/.make/`, so nothing had to move.
- **Both OAuth base-URL names** are still exported — `axum-oauth` resolves
  first-found-wins and two repos pin the canonical one.
- **Play's manual steps** stay manual: the first upload of a new package,
  halting a rollout, data-safety and content-rating.

## Development

```bash
uv sync
uv run pytest
uv run ruff check src tests && uv run ruff format --check src tests
```
