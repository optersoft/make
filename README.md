# make

A command runner whose recipes are Python.

```python
# Makefile.py
from make import recipe, sh

@recipe(group="app", requires=["cargo"])
def test(*, fast: bool = False) -> None:
    """Run the test suite."""
    sh("cargo", "test", *(["--lib"] if fast else []))
```

```console
$ mk app.test --fast
$ cargo test --lib
```

The command line is derived from the function signature, so there is no second
schema to keep in sync. Recipes are ordinary functions — importable,
unit-testable, and **distributable as versioned packages** rather than a
directory someone `git clone`d.

```console
$ uv tool install mkrun          # installs one command: mk
```

**Three names, deliberately different.** The PyPI distribution is `mkrun`, the
import name is `make`, and the command is `mk` — all independent, the same way
`pip install pillow` gives you `import PIL`. The distribution is not `make` or
`mk` because both are taken by unrelated projects: a recipe file declaring
`dependencies = ["make"]` gets a jinja2 templating tool, and `["mk"]` gets a
different task runner.

**Nothing installs a `make` command.** That would shadow GNU make on the `PATH`
of essentially every Unix machine — a large thing to take from someone who
installed a task runner for one repository. `mk` is not a builtin or a default
alias in bash, zsh or PowerShell, and it is three characters shorter to type.
If you want the old spelling anyway, it is one line in your shell profile:

```console
$ alias make=mk
```

---

## Why

`just` is a good dispatcher wrapped around a language that recipes outgrow. Once
a recipe body has a loop, an `if`, or three variables that must agree, you are
writing shell inside string interpolation with no types, no tests, and no way to
share it except copying a file.

|  | `just` | `make` |
|---|---|---|
| Recipe body | bash, with `{{ }}` spliced in **as text** | Python; values are values |
| Arguments | positional strings | typed, from the signature — `int`, `Path`, `Literal`, `list[str]` |
| Required input | omit the default so it becomes a *parse error* | declared, with an error naming the field and where to set it |
| Namespacing | one flat namespace, `web-`/`box-` prefixes by convention | modules: `web.start`, `box.ls` |
| Overriding a shared recipe | impossible — duplicates are fatal | `@recipe(override="web.start")`, and `abstract=True` upstream |
| Sharing | `git clone --depth 1` into a gitignored directory | a PyPI (or git) dependency, resolved and locked by uv |
| Pinning | none — every checkout is on some HEAD | `mk --sync` → a lockfile |
| Testing | `just --fmt --check` (it parses) | `pytest`, with a command recorder |
| Dry run | text expansion | every command actually suppressed |

Nothing here is theoretical. Every row is something that cost real bugs in a
fleet of five repositories sharing 1100 lines of `just` — a teardown that reaped
the wrong thing, a worktree that silently started with no environment at all, an
export that was a no-op for two repos out of five. All of them are properties of
small functions, and all of them are tests now.

`docs/why.md` names them, one by one, with the commits. Migrating, including the
full translation table: `docs/from-just.md`.

## Recipes

### Arguments come from the signature

Parameters **before `*`** are positional; parameters **after `*`** are options.

```python
from pathlib import Path
from typing import Literal
from make import recipe, sh

@recipe
def publish(bundle: Path, *, track: Literal["alpha", "prod"] = "alpha",
            locale: list[str] = [], dry: bool = False) -> None:
    """Upload a bundle to the store."""
    sh("fastlane", "supply", "--aab", bundle, "--track", track,
       *(["--validate_only"] if dry else []))
```

```console
$ mk publish ./app.aab --track prod --locale es-ES --locale en-US
$ mk publish --help
```

| Signature | Command line |
|---|---|
| `path: Path` | required positional |
| `dest: str = "."` | optional positional |
| `*, port: int = 8001` | `--port 8001` |
| `*, force: bool = False` | `--force` |
| `*, color: bool = True` | `--no-color` |
| `*, track: Literal["a","b"]` | `--track {a,b}` |
| `*, locale: list[str] = []` | `--locale` (repeatable) |
| `*args: str` | trailing arguments |

Short flags, help text and environment fallbacks attach without leaving the
signature:

```python
from typing import Annotated
from make import arg

def serve(*, port: Annotated[int, arg("-p", help="dev port", env="DEV_PORT")] = 8001): ...
```

### Options on `@recipe`

```python
@recipe(
    group="play",           # namespace -> play.publish
    needs=[build, sign],    # run first, once per invocation
    requires=["fastlane"],  # must be on PATH; checked before anything runs
    dangerous=True,         # demand --yes or an interactive confirmation
    inputs=["src/**/*.rs"], # skip when outputs are newer than inputs
    outputs=["dist/app"],
    aliases=["ship"],
    abstract=False,         # declared but unimplemented; a consumer must override
    override=False,         # True, or the full name of the recipe being replaced
    keep_cwd=False,         # run where the user stood, not at the recipe-file root
)
```

### Running commands

```python
sh("git", "commit", "-m", message)     # argv list -- no quoting hazard, ever
sh.out("git", "rev-parse", "HEAD")     # captured stdout, stripped
sh.lines("git", "ls-files")
sh.ok("command", "-v", "fastlane")     # bool, never raises
sh.pipe("du -sk target | cut -f1")     # explicit shell, because it is the hazard
sh.bash(script)                        # multi-line bash, set -euo pipefail
sh.background("tailwindcss", "--watch", log="tmp/css.log")
sh.replace_process("dx", "serve")      # exec, replacing this process
```

There is no interpolation step, so a value containing a space, a quote or a `$`
is data and cannot become syntax. Every one of these honours `--dry-run`; give
`sh.out(..., dry="...")` or `sh.ok(..., dry=False)` a stand-in when the value
steers later logic.

### Changing files

A dry run that suppresses every command but still writes files looks safe and
is not. Use `fs` wherever a recipe changes something:

```python
from make import fs

fs.write(path, text)          fs.copy(source, destination)
fs.mkdir(path)                fs.replace(source, destination)   # atomic move
fs.remove(path)               fs.rmtree(path)
```

Reading is untouched — `read_text`, `glob` and `stat` stay as they are, because
suppressing reads would make the dry run diverge from the real one.

### Configuration for shared packages

A package declares what it needs from the consumer, typed:

```python
from dataclasses import dataclass, field
from make import config

@config.section("web")
@dataclass
class Web:
    bin: str                                          # required
    port: int = 8001
    watch: list[str] = field(default_factory=list)
```

Consumers set it from the recipe file, a config file, or the environment — last
wins:

```python
Web.configure(bin="acme-web", port=8005, watch=["server", "web"])
```

```toml
# make.toml   (or [tool.make.web] in pyproject.toml)
[web]
port = 8005
```

```console
$ MAKE_WEB_PORT=8105 mk web.start
```

A missing required value fails with the field, its type, and all three places it
could be set.

### Layered secrets

```python
from make import env
env.layered()      # ~/.make/secrets.env -> ~/.make/<repo>.env -> ./.env
```

Later layers win, but a variable exported by the caller still beats all of them.
`<repo>` resolves through `git rev-parse --git-common-dir`, so it is the *main*
checkout's name even from inside a linked worktree — the failure mode where a
worktree silently starts with no application environment at all. `~/.just/` is
read too, so an existing setup keeps working.

## Sharing recipes

This is the point. Declare dependencies inline (PEP 723):

```python
# Makefile.py
# /// script
# requires-python = ">=3.11"
# dependencies = ["mkrun>=0.1", "acme-recipes>=0.4"]
# ///
from make import recipe, sh
from acme_recipes import deploy, docker      # importing registers deploy.* and docker.*

deploy.Deploy.configure(host="app.example.com", unit="acme-web")
```

```console
$ mk --sync              # pin -> Makefile.py.lock, committed
$ mk --sync --upgrade    # move the pins, deliberately, as a reviewable diff
$ mk web.start
```

Without `--upgrade`, an existing lock is respected: a repo stays on the version
it was pinned to even after the shared package moves. That is the whole
difference from a `git pull --ff-only || true` that drags every checkout to
whatever `HEAD` happens to be.

If the current interpreter already satisfies the dependencies, nothing happens.
Otherwise `make` re-executes itself under `uv run`, into a cached environment.
In a project that already has a `pyproject.toml` and a virtualenv, put the
dependencies there instead and `mk --sync` runs `uv sync`.

Publishing a recipe package is publishing a wheel. Nothing about it is special:

```python
# acme_recipes/__init__.py
from make import group, sh

docker = group("docker")

@docker
def build(*, tag: str = "latest") -> None:
    """Build the image."""
    sh("docker", "build", "-t", f"app:{tag}", ".")
```

### Where a package comes from

A registry is not the only answer, and often not the right one: recipes that
wrap *your* tool belong in the repository that builds it, so the tool and the
recipe that drives it change in one commit. Name the source and uv fetches it —
the same two forms cargo offers:

```python
# /// script
# dependencies = ["mkrun>=0.1", "acme-recipes>=0.4"]
#
# [tool.uv.sources]
# acme-recipes = { git = "ssh://git@github.com/acme/tool.git", subdirectory = "make" }
# ///
```

`mk --sync --add acme-recipes --git ssh://…` writes that for you, and
`mk --doctor` prints where each package actually resolved from — a source is the
one thing about a dependency you cannot see by reading the file.

To work on a package and its consumer at the same time, redirect it to a
checkout without touching the committed file — cargo's `[patch]`:

```toml
# .make/sources.toml, gitignored. ~/.make/sources.toml covers every repo at
# once; relative paths resolve against the repo root either way.
[sources]
acme-recipes = { path = "../tool/make" }
```

An override is local by definition, so nothing is written to the lockfile while
one is in force — a path pins no commit.

## Testing recipes

```python
from make.testing import record
from acme_recipes import web

def test_start_reaps_a_stale_lock_holder():
    with record(responses={"lsof -t": "4711"}) as rec:
        web.start(port=8105)
    assert rec.saw("kill", "-9", "4711")
    assert rec.matched(r"dx serve .*--port 8105")
```

`record()` captures every command instead of running it, answers `sh.out()` with
canned text, and reports declared tools as present. Recipes called from Python
are plain functions — `needs=`, the confirmation gate and staleness belong to the
runner, not the function.

## Command line

```
mk [options] <recipe> [arguments] [<recipe> [arguments] ...]

-l, --list             list recipes (the default with no recipe)
-h, --help [RECIPE]    help, or full help for one recipe
-n, --dry-run          print commands instead of running them
-y, --yes              pre-answer confirmations for dangerous recipes
-f, --force            ignore inputs=/outputs= staleness
-j, --jobs N           run independent prerequisites in parallel
-q, --quiet            only errors
-v, --verbose          more detail (repeatable)
-C, --cwd DIR          change directory before finding the recipe file
-F, --file PATH        use this recipe file
-e, --env KEY=VALUE    set a variable for every command
    --json             machine-readable --list
    --doctor           every declared tool, and where each package resolved from
    --sync             resolve and pin dependencies
    --upgrade          with --sync, move the pins
    --add PKG          with --sync, add a package (--path DIR | --git URL)
    --completions SH   bash | zsh | fish
```

Recipe files, searched from the current directory upward: `Makefile.py`,
`makefile.py`, `mk.py`, `.make/main.py`. Not `make.py` — that name can shadow
`import make`. (A `make/` *directory* is fine: namespace packages rank below
installed ones, so it cannot shadow anything.)

## Repository layout

`src/` is this tool. `optersoft/` is a second, separate distribution —
`optersoft-make`, the author's own fleet recipes — kept here as a uv workspace
member so a change to the runner is tested against real recipes in the same
commit. It is excluded from the `mkrun` sdist and wheel; installing this tool
never installs it.

That directory is named after its *owner*, not after this repository, because
that is the convention the tool encourages: a project ships its recipes in its
own `make/` directory, as `<project>-make`, and consumers name the source.
`hetzner-make` (the `box` group, beside the `hetzner-box` CLI it wraps) is the
first one; `optersoft-make` is what is left once every group that belongs to a
project has gone to live there.

## Status

Alpha. The recipe-authoring API — `@recipe`, `sh`, `fs`, `config`, `env` — is
what a private fleet of seven recipe groups is already built on, and is not
expected to change shape. The internals may.

Issues are welcome; there is no support guarantee.

## License

Licensed under either of [MIT](LICENSE-MIT) or
[Apache-2.0](LICENSE-APACHE), at your option — the pair `uv` itself ships
under. Take whichever your organisation prefers: MIT is the shorter read,
Apache-2.0 carries an express patent grant and an explicit trademark
reservation.

Unless you state otherwise, any contribution you submit for inclusion is
dual-licensed on those same terms, with no additional conditions.

Copyright © 2026 Optersoft, S.L.
