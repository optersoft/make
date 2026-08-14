"""The `database` group: render a doc's mermaid blocks to a zoomable SVG.

Ported from `database.just`, which was one recipe of six backslash-continued
shell lines. The behaviour it encodes is worth keeping exactly:

* Output goes to a **per-repo** directory. Every optersoft repo names its doc
  `docs/database.md`, so a single shared `/tmp/<name>.svg` had them overwrite
  each other -- and after a failed render, `open` would show a *different*
  repo's stale diagram.
* A failed render does not open anything. A broken diagram must not be masked
  by whatever was there before.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Literal

from make import env, fs, group, note, sh, step
from make.errors import MakeError

database = group("database")

#: Grayscale on mid-grey stays legible in both light and dark viewers; the dark
#: theme is for viewers who want the diagram to match a dark editor.
THEMES = {"grey": ("neutral", "#9ca3af"), "dark": ("dark", "#1f2937")}


def output_dir() -> Path:
    """A scratch directory unique to this repository."""
    return Path(tempfile.gettempdir()) / "make-database" / env.repo_name()


@database.recipe(name="render", aliases=["database"], requires=["bunx"])
def render(
    file: Path = Path("docs/database.md"),
    *,
    theme: Literal["grey", "dark"] = "grey",
    open_result: bool = True,
) -> list[Path]:
    """Render a doc's mermaid blocks to SVG and open them.

    Args:
        file: markdown file containing the mermaid blocks
        theme: grey (default, legible either way) or dark
        open_result: open the result in the default viewer
    """
    if not file.is_file():
        raise MakeError(f"{file} does not exist", hint="pass the path: make database.render docs/er.md")

    mermaid_theme, background = THEMES[theme]
    out_dir = output_dir()
    fs.rmtree(out_dir)
    fs.mkdir(out_dir)
    target = out_dir / f"{file.stem}.svg"

    step(f"rendering {file} -> {target}")
    result = sh(
        "bunx",
        "@mermaid-js/mermaid-cli",
        "-t",
        mermaid_theme,
        "-b",
        background,
        "-i",
        file,
        "-o",
        target,
        check=False,
    )
    if not result.ok:
        raise MakeError(
            f"mermaid could not render {file} (see the error above)",
            hint="nothing was opened -- a broken diagram must not be masked by a stale one",
        )

    # A doc with several blocks yields <name>-1.svg, <name>-2.svg, ...
    produced = sorted(out_dir.glob(f"{file.stem}-*.svg")) or ([target] if target.exists() else [])
    if not produced:
        raise MakeError(f"mermaid reported success but produced nothing in {out_dir}")

    note("rendered: " + ", ".join(str(p) for p in produced))
    if open_result:
        sh("open", *produced)
    return produced
