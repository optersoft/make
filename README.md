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

[mkrun-dcd.pages.dev](https://mkrun-dcd.pages.dev) — the short version, on one page.
The step-by-step tutorial is on [academy](https://academy.optersoft.com/project/make).

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

## Is this for you?

You have a repository with a handful of commands worth remembering — build, test,
run the dev server, cut a release, reset the database — and they currently live in
a `scripts/` folder, a `package.json`, a shell history, or a wiki page. It works
until one of them needs a loop, a condition, or three values that must agree, and
until a second repository needs the same command and you copy it.

`make` is for that moment. A task is a Python function, so the moment a task
outgrows one line you already have the language you need — and you did not have to
rewrite it to get there.

**You will get the most out of it if:**

- your tasks take arguments, and you would rather declare them than parse them;
- more than one repository runs the same commands, and you want one version of
  them rather than several copies that have drifted;
- a mistake in a task is expensive — it deploys, kills, deletes, or uploads — and
  you want a dry run and a test rather than care;
- your team is polyglot: the tasks are Python, whatever they drive is not.

**You probably do not need it if** your repository has one command and it is
`cargo test`, or if you cannot have Python on the machines that run the tasks.

## Why it is worth it

### The command line is the signature

Every other runner asks you to describe the arguments twice: once for the parser
and once for the function. Here the signature *is* the interface — types, defaults,
required-ness, help text, shell completions and the `--help` output all come from
it, and they cannot fall out of sync with the body because there is nothing to
keep in sync.

### Values stay values

There is no interpolation step anywhere. `sh()` takes an argv list, so a filename
with a space, a commit message with a quote, a password with a `$` is data and can
never become syntax. The one place shell is genuinely the right tool —
`sh.pipe("du -sk target | cut -f1")` — is spelled differently, so it is visible in
review.

### Tasks are code, so they are testable

A task is an importable function, and `make.testing` records what it would have
run. That turns "does the teardown reap the right process" from something you find
out during an incident into a three-line unit test that runs in CI.

```python
def test_the_teardown_reaps_the_lock_holder(recorder):
    stop()
    assert recorder.commands == [["kill", "-TERM", "4711"]]
```

### The dry run is real

`--dry-run` suppresses every command, every file write through `fs`, every poll,
every HTTP call and every process signal — not a printed expansion of what a
string would have become. Reads are untouched, so the dry run follows the same
branches the real run does.

### Configuration fails with instructions

A shared task declares what it needs as a typed dataclass. A missing value stops
before anything runs, and the error names the field, its type, and all three
places it can be set — the task file, `make.toml`, or the environment.

### Sharing is a dependency, not a copy

Tasks ship as ordinary Python packages. A consumer names the package, `mk --sync`
writes a lock file, and upgrading is a version bump in a diff. Nothing is cloned
into a gitignored directory at whatever `HEAD` happened to be; nothing silently
diverges between repositories.

### Namespaces, and a way out of them

Groups give real names — `web.start`, `db.reset` — rather than prefix conventions.
A task you inherit from a shared package can be replaced with
`@task(override="web.start")`, and a package can declare a task `abstract=True` to
say *you must supply this*. Both are checked: overriding something that no longer
exists is an error, not a silently unused function.

### It stays fast

A task list is about 30 ms, with a 150 ms budget enforced by a test in this
repository's own suite. Groups import lazily, so a task package you are not using
costs nothing.

## What it costs

Honesty is worth more than a clean sweep:

- **A runtime.** Python 3.11+, and `uv` for shared task packages. A single static
  binary this is not.
- **~30 ms, not ~5 ms.** Imperceptible in use, but it is a real number and it is
  guarded by a test rather than free.
- **Alpha.** The authoring API is stable in practice; the internals move.

## Direction

- **The authoring API settles first.** `@task`, `sh`, `fs`, `config`, `env` and
  `testing` are what everything is built on. A 1.0 means those stopped moving.
- **It is a command runner, not a build system.** File targets and staleness
  graphs were tried and removed: `cargo`, `npm`, `uv` and every compiler already
  do that job for their own inputs, and a second, worse dependency graph on top of
  them is a source of wrong answers. Tasks depend on tasks (`needs=`), and that is
  the whole model.
- **The runner stays generic.** Tasks that wrap a tool belong beside that tool, as
  its own `<project>-make` package. Nothing tool-specific ships inside `mkrun`.
- **Bodies stay Python.** No DSL, no template language, no configuration format
  that grows conditionals.
- **Startup stays under budget.** A runner typed dozens of times a day gets
  abandoned the moment it stops feeling instant.

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

### Layered environment

```python
from make import env
env.layered()      # ~/.make/secrets.env -> ~/.make/<repo>.env -> ./.env
```

Later layers win, but a variable exported by the caller still beats all of them.
`<repo>` resolves through `git rev-parse --git-common-dir`, so it is the *main*
checkout's name even from inside a linked worktree — otherwise a worktree finds no
file and starts with no application environment at all, silently, because a
missing layer is not an error.

### Secrets

A layer mixes configuration with credentials, and only one of the two should
reach a child process. `env.layered()` exports the configuration; anything whose
name reads as a credential is held back until a task asks for it:

```python
@task(secrets=["KEYSTORE_PASSWORD"])          # resolved before the body runs
def release() -> None:
    sh("./gradlew", "assembleRelease")        # sees it

@task
def lint() -> None:
    sh("./gradlew", "lint")                   # does not
```

`env.require("KEYSTORE_PASSWORD")` is the same thing asked for in the body, and
returns the value. Either way it goes into that task's environment and no other
one's, so a build tool and its plugins stop receiving every credential the
machine has on the way to signing an APK.

A value obtained this way is **masked in everything the runner prints** — the
echoed command, `--dry-run`, `--verbose`, the tail of a failed command's
output, the error message. Your own `print()` is untouched: printing a secret on
purpose is a thing tasks do.

Values can stay in the plaintext layers. They can also be encrypted, which is
where they belong on a laptop:

```console
$ mk secure.init          # an age identity, held in a keychain that locks
$ mk secure.set API_TOKEN # prompts; never an argv, never a shell history
$ mk secure.list          # names, never values
```

```
~/.make/secrets/recipient.txt     the age public key. Not a secret.
~/.make/secrets/global.age        every project, like secrets.env
~/.make/secrets/<repo>.age        one project, like <repo>.env
~/.make/secrets/files/<name>.age  a keystore, a service-account JSON
```

Decrypted, a layer is exactly the `KEY=value` text above, so nothing about the
format is new and `age -d -i <key> global.age` is the whole escape hatch. The
recipient is public, so **writing a secret costs nothing**; only reading unlocks
the keychain, once per session. `secrets.file("upload.jks")` hands a task a
private temporary path for the tools that insist on a real file, and removes it
on exit.

Off macOS — CI, a server — `MAKE_AGE_IDENTITY` names the key or a file holding
it. `mk --doctor` prints which layers exist, the names in them, and where the
identity would come from.

The `secure.*` tasks are [optersoft-make](https://code.optersoft.com/make-optersoft.git);
the store itself is `make.secrets`, and any task package can offer its own front
end to it.

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
are plain functions — `needs=` and the confirmation gate belong to the runner,
not the function.

## Command line

```
mk [options] <task> [arguments] [<task> [arguments] ...]

-l, --list             list tasks (the default with no task)
-h, --help [RECIPE]    help, or full help for one task
-n, --dry-run          print commands instead of running them
-y, --yes              pre-answer confirmations for dangerous tasks
-j, --jobs N           run independent prerequisites in parallel
-q, --quiet            only errors
-v, --verbose          more detail (repeatable)
-C, --cwd DIR          change directory before finding the task file
-F, --file PATH        use this task file
-e, --env KEY=VALUE    set a variable for every command
    --json             machine-readable --list
    --doctor           every declared tool, where each package resolved from, and
                       whether the task file's docstring names tasks that exist
    --sync             resolve and pin dependencies
    --upgrade          with --sync, move the pins
    --add PKG          with --sync, add a package (--path DIR | --git URL)
    --completions SH   bash | zsh | fish
```

Task files, searched from the current directory upward: `Makefile.py`,
`makefile.py`, `mk.py`, `.make/main.py`. Not `make.py` — that name can shadow
`import make`. (A `make/` *directory* is fine: namespace packages rank below
installed ones, so it cannot shadow anything.) When none exists, `mk` on a
terminal offers to create a starter `Makefile.py` in the current directory and
lists its tasks; `mk --yes` creates it without asking, and off a terminal it is
an error, since nothing should write into a repository unasked.

## Repository layout

`src/make/` is this tool, published as `mkrun`. `rust/`, `cloudflare/` and
`marketplace/` are three further, separate distributions — `make-rust`, generic cargo
hygiene tasks; `make-cloudflare`, Cloudflare Pages direct upload with no Node and no
wrangler; and `make-marketplace`, building and releasing a VS Code extension —
kept here as uv workspace members so a change to the runner is tested against real
tasks in the same commit. All three are excluded from the `mkrun` sdist and wheel;
installing this tool never installs them, and `mkrun` itself has no dependencies.
`site/` is the landing page.

They are generic on purpose, because that is the convention the tool encourages:
a project ships its tasks in its own `make/` directory, as `<project>-make`, and
consumers name the source. Only groups that wrap something *nobody* owns — cargo,
a hosting API, `vsce` — belong beside the runner.

## Status

Alpha. The task-authoring API — `@task`, `sh`, `fs`, `config`, `env` — is what
several task packages are already built on and is not expected to change shape.
The internals may. Releases are tagged in this repository and published to PyPI
from CI.

Why it works the way it does, decision by decision: [`docs/design.md`](docs/design.md).
Coming from `just`: [`docs/from-just.md`](docs/from-just.md).

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
