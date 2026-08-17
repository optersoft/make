# make

A command runner whose tasks are Python.

```python
# Makefile.py
from make import task, sh

@task(group="app", requires=["cargo"])
def test(*, fast: bool = False) -> None:
    """Run the test suite."""
    sh("cargo", "test", *(["--lib"] if fast else []))
```

```console
$ mk app.test --fast
$ cargo test --lib
```

The command line is derived from the function signature, so there is no second
schema to keep in sync. Tasks are ordinary functions — importable,
unit-testable, and **distributable as versioned packages** rather than a
directory someone `git clone`d.

```console
$ uv tool install mkrun          # installs one command: mk
```

**Three names, deliberately different.** The PyPI distribution is `mkrun`, the
import name is `make`, and the command is `mk` — all independent, the same way
`pip install pillow` gives you `import PIL`. The distribution is not `make` or
`mk` because both are taken by unrelated projects: a task file declaring
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
a task body has a loop, an `if`, or three variables that must agree, you are
writing shell inside string interpolation with no types, no tests, and no way to
share it except copying a file.

|  | `just` | `make` |
|---|---|---|
| Task body | bash, with `{{ }}` spliced in **as text** | Python; values are values |
| Arguments | positional strings | typed, from the signature — `int`, `Path`, `Literal`, `list[str]` |
| Required input | omit the default so it becomes a *parse error* | declared, with an error naming the field and where to set it |
| Namespacing | one flat namespace, `web-`/`box-` prefixes by convention | modules: `web.start`, `box.ls` |
| Overriding a shared task | impossible — duplicates are fatal | `@task(override="web.start")`, and `abstract=True` upstream |
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

## Tasks

### Arguments come from the signature

Parameters **before `*`** are positional; parameters **after `*`** are options.

```python
from pathlib import Path
from typing import Literal
from make import task, sh

@task
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

### Options on `@task`

```python
@task(
    group="play",           # namespace -> play.publish
    needs=[build, sign],    # run first, once per invocation
    requires=["fastlane"],  # must be on PATH; checked before anything runs
    dangerous=True,         # demand --yes or an interactive confirmation
    inputs=["src/**/*.rs"], # skip when outputs are newer than inputs
    outputs=["dist/app"],
    aliases=["ship"],
    abstract=False,         # declared but unimplemented; a consumer must override
    override=False,         # True, or the full name of the task being replaced
    keep_cwd=False,         # run where the user stood, not at the task-file root
)
```

### Shorter names

`aliases=` gives one task a second name. A whole namespace gets one from the
group it belongs to, or from anywhere with `alias()`:

```python
dioxus = group("dioxus", alias="dx")    # dx.start, dx.stop, dx.tailwind, ...

alias("dx", "dioxus")                   # same, for a group you did not declare
alias("ship", "play.publish")           # a dotted target is that one task
```

A group alias is a prefix, so a task added to the group later is covered without
touching the alias. It is only an input spelling: `--list`, `--help`, `needs=`
and every error message keep saying `dioxus.start`, and an exact name is never
reinterpreted — the alias is consulted only after the real name misses. Pointing
one at a group that has no tasks, or at a name that already belongs to a group or
a task, is an error naming the line that declared it.

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

Watchers that exit the moment stdin closes (tailwindcss `--watch`) take
`sh.background(..., hold_stdin=True)` — a stdin that never reaches EOF, without
the `tail -f /dev/null |` shell wrapper.

### Waiting, HTTP and processes

The three loops every task file used to hand-roll in bash — poll-until-ready,
`curl -w '%{http_code}'`, and `lsof | kill`:

```python
from make import poll, http, proc

pid = poll(lambda: sh.out("pidof", "-s", pkg, check=False, dry="4711"),
           timeout=10, message=f"{pkg} to start")   # returns the truthy value

http.get(url).status              # 200, 404, ... or 0 when unreachable
http.ok(url)                      # bool; a bad status is a result, not an exception
http.wait("http://localhost:8002/", timeout=60)     # raises WaitTimeout if never up

proc.port_pids(8080)              # who is LISTENING on :8080
proc.reap_port(8080)              # terminate them; -> the pids signalled
proc.reap("dx serve.*8002")       # pgrep -f pattern; anchor it to YOUR thing
```

All of them honour `--dry-run`: `poll()` returns its `dry=` stand-in without
looping, `http` answers with `dry_status=`, and `proc` reports what it would
signal without killing anything.

### Changing files

A dry run that suppresses every command but still writes files looks safe and
is not. Use `fs` wherever a task changes something:

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

Consumers set it from the task file, a config file, or the environment — last
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

## Sharing tasks

This is the point. Declare dependencies inline (PEP 723):

```python
# Makefile.py
# /// script
# requires-python = ">=3.11"
# dependencies = ["mkrun>=0.2", "acme-tasks>=0.4"]
# ///
from make import task, sh
from acme_tasks import deploy, docker       # importing registers deploy.* and docker.*

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

Publishing a task package is publishing a wheel. Nothing about it is special:

```python
# acme_tasks/__init__.py
from make import group, sh

docker = group("docker")

@docker
def build(*, tag: str = "latest") -> None:
    """Build the image."""
    sh("docker", "build", "-t", f"app:{tag}", ".")
```

### Where a package comes from

A registry is not the only answer, and often not the right one: tasks that
wrap *your* tool belong in the repository that builds it, so the tool and the
task that drives it change in one commit. Name the source and uv fetches it —
the same two forms cargo offers:

```python
# /// script
# dependencies = ["mkrun>=0.2", "acme-tasks>=0.4"]
#
# [tool.uv.sources]
# acme-tasks = { git = "ssh://git@github.com/acme/tool.git", subdirectory = "make" }
# ///
```

`mk --sync --add acme-tasks --git ssh://…` writes that for you, and
`mk --doctor` prints where each package actually resolved from — a source is the
one thing about a dependency you cannot see by reading the file.

To work on a package and its consumer at the same time, redirect it to a
checkout without touching the committed file — cargo's `[patch]`:

```toml
# .make/sources.toml, gitignored. ~/.make/sources.toml covers every repo at
# once; relative paths resolve against the repo root either way.
[sources]
acme-tasks = { path = "../tool/make" }
```

An override is local by definition, so nothing is written to the lockfile while
one is in force — a path pins no commit.

## Testing tasks

```python
from make.testing import record
from acme_tasks import web

def test_start_reaps_a_stale_lock_holder():
    with record(responses={"lsof -t": "4711"}) as rec:
        web.start(port=8105)
    assert rec.saw("kill", "-9", "4711")
    assert rec.matched(r"dx serve .*--port 8105")
```

`record()` captures every command instead of running it, answers `sh.out()` with
canned text, and reports declared tools as present. Tasks called from Python
are plain functions — `needs=`, the confirmation gate and staleness belong to the
runner, not the function.

## Command line

```
mk [options] <task> [arguments] [<task> [arguments] ...]

-l, --list             list tasks (the default with no task)
-h, --help [RECIPE]    help, or full help for one task
-n, --dry-run          print commands instead of running them
-y, --yes              pre-answer confirmations for dangerous tasks
-f, --force            ignore inputs=/outputs= staleness
-j, --jobs N           run independent prerequisites in parallel
-q, --quiet            only errors
-v, --verbose          more detail (repeatable)
-C, --cwd DIR          change directory before finding the task file
-F, --file PATH        use this task file
-e, --env KEY=VALUE    set a variable for every command
    --json             machine-readable --list
    --doctor           every declared tool, and where each package resolved from
    --sync             resolve and pin dependencies
    --upgrade          with --sync, move the pins
    --add PKG          with --sync, add a package (--path DIR | --git URL)
    --completions SH   bash | zsh | fish
```

Task files, searched from the current directory upward: `Makefile.py`,
`makefile.py`, `mk.py`, `.make/main.py`. Not `make.py` — that name can shadow
`import make`. (A `make/` *directory* is fine: namespace packages rank below
installed ones, so it cannot shadow anything.)

## Repository layout

`src/` is this tool. `optersoft/` is a second, separate distribution —
`optersoft-make`, the author's own fleet tasks — kept here as a uv workspace
member so a change to the runner is tested against real tasks in the same
commit. It is excluded from the `mkrun` sdist and wheel; installing this tool
never installs it.

That directory is named after its *owner*, not after this repository, because
that is the convention the tool encourages: a project ships its tasks in its
own `make/` directory, as `<project>-make`, and consumers name the source.
`hetzner-make` (the `box` group, beside the `hetzner-box` CLI it wraps) is the
first one; `optersoft-make` is what is left once every group that belongs to a
project has gone to live there.

## Status

Alpha. The task-authoring API — `@task`, `sh`, `fs`, `config`, `env` — is
what a private fleet of seven task groups is already built on, and is not
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
