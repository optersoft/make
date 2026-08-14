"""The `box` group: Hetzner Storage Box operations.

Thin wrappers over the `hetzner-box` CLI (github.com/optersoft/hetzner), which
owns all the actual box logic -- host derivation, sub-account login, key auth,
SFTP. Ported from `box.just`, where every recipe body began by pasting a shared
12-line bash blob (`prep`) built with string concatenation, because `just` has
no way to run something before each recipe. Here it is a function.

The `HIVE_BOX_*` environment family and the `~/.ssh/hive-box-<login>` key path
keep their names on purpose. They are operational contracts read by the CLI and
present in every `secrets.env` and on every VM -- renaming them would break box
access everywhere, and nothing would catch it, because the recipes would still
run and simply fail to authenticate.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from make import config, env, group, note, sh, step
from make.errors import ToolMissing

box = group("box")

CLI = "hetzner-box"
SOURCE_REPO = "ssh://git@github.com/optersoft/hetzner.git"


@config.section("box")
@dataclass
class Box:
    """Per-project box configuration."""

    bucket: str = ""
    """Base directory every path is scoped under; empty means the account root."""


def prepare() -> None:
    """Load the layered secrets and make sure the CLI is present.

    `box.just` ran this as an interpolated string in front of every recipe body.
    """
    env.layered()
    if sh.which(CLI) is None:
        note(f"{CLI} is not on PATH -- installing it")
        install()
        if sh.which(CLI) is None:
            raise ToolMissing(
                f"{CLI} is still not on PATH after installing", hint="is ~/.cargo/bin on your PATH?"
            )


def run(*args: object, **kwargs: object) -> str:
    """Invoke the CLI under the configured bucket."""
    prepare()
    return sh.out(CLI, "--dir", Box.bucket, *args, **kwargs)  # type: ignore[arg-type]


@box.recipe(name="install", requires=["cargo"])
def install(source: Path | None = None) -> None:
    """Install or upgrade the `hetzner-box` CLI.

    Args:
        source: a local hetzner checkout to build from, instead of git
    """
    if source:
        sh("cargo", "install", "--path", source / "hetzner-box", "--features", "cli")
    else:
        sh(
            "cargo",
            "install",
            "--git",
            SOURCE_REPO,
            "hetzner-box",
            "--features",
            "cli",
            env={"CARGO_NET_GIT_FETCH_WITH_CLI": "true"},
        )
    where = sh.which(CLI)
    note(f"installed {where}" if where else "installed, but not on PATH -- is ~/.cargo/bin on it?")


@box.recipe(name="target")
def target() -> str:
    """Print the login@host the box recipes talk to (no connection made)."""
    prepare()
    value = sh.out(CLI, "target")
    print(value)
    return value


@box.recipe(name="ls")
def ls(path: str = ".") -> str:
    """List a path on the box."""
    listing = run("ls", path)
    print(listing)
    return listing


@box.recipe(name="lsr")
def lsr(path: str = ".") -> str:
    """List a path on the box, recursively."""
    listing = run("lsr", path)
    print(listing)
    return listing


@box.recipe(name="cp")
def cp(local: Path, remote: str) -> None:
    """Upload a local file. A remote ending in `/` is a directory.

    Args:
        local: the file to upload
        remote: destination under the bucket
    """
    step(f"uploading {local} -> {remote}")
    run("cp", local, remote)


@box.recipe(name="get")
def get(remote: str, local: Path | None = None) -> None:
    """Download a file from the box (default: its basename here)."""
    step(f"downloading {remote}")
    run("get", remote, local)


@box.recipe(name="mv")
def mv(src: str, dst: str) -> None:
    """Move or rename a path under the bucket."""
    run("mv", src, dst)


@box.recipe(name="rm")
def rm(path: str) -> None:
    """Soft-delete: move a path into the bucket's `.trash/`.

    Recover with `make box.mv .trash/<file> <dir>/`; `box.purge` wipes it. The
    move is basename-only, so same-named files from different directories
    collide in `.trash`.
    """
    run("rm", path)
    note(f"moved to .trash/ -- recover with `make box.mv .trash/{Path(path).name} {Path(path).parent}/`")


@box.recipe(name="purge", dangerous=True)
def purge() -> None:
    """Permanently delete everything in the bucket's `.trash/`."""
    run("purge")
