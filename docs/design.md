# Design decisions

Why the tool works the way it does. Each of these is a choice with a cost, taken
because the alternative cost more — and most of them are the reason a task file
here looks different from the shell script it replaced.

---

## 1. The signature is the interface

A task's parameters become the command line: types, defaults, whether something is
required, the help text, the completions.

```python
@task
def publish(bundle: Path, *, track: Literal["alpha", "prod"] = "alpha",
            dry: bool = False) -> None:
    """Upload a bundle to the store."""
```

```console
$ mk publish ./app.aab --track prod --dry
```

The alternative — a parser declaration beside the function — is two descriptions
of one thing, and the second one is always the one that rots. There is nothing to
keep in sync here because there is only one description. The cost is that the
command line is constrained by what a Python signature can express, which is the
constraint that keeps task interfaces boring and predictable.

## 2. Nothing is interpolated

`sh()` takes an argv list and executes it directly:

```python
sh("hetzner-box", "--dir", bucket, "ls", path)
```

Text-substitution runners paste a value into shell source *before* the shell
parses it, so correctness depends on the quoting being right at every single call
site, forever. Here a value containing a space, a quote, a newline or a `$` is
data and cannot become syntax — not because it is escaped, but because there is no
parser downstream to escape it from.

Shell is still the right tool sometimes, so it is available and *visible*:
`sh.pipe("du -sk target | cut -f1")` and `sh.bash(script)` say in the source that
this line is shell, which makes it the line a reviewer looks at.

## 3. A task is an ordinary function, so it can be tested

The bugs that hurt are never in the dispatch. They are in the twelve lines that
decide *which* process to kill, *which* file to read, *which* name to export — and
in a shell-based runner there is no way to run those twelve lines without doing
the thing they do.

```python
def test_the_teardown_reaps_the_lock_holder(recorder):
    stop()
    assert recorder.commands == [["kill", "-TERM", "4711"]]

def test_repo_name_is_the_main_checkout_even_inside_a_worktree(tmp_path):
    assert env.repo_name(linked_worktree) == "acme"
```

`make.testing` records commands instead of running them, so a task's logic is
testable in the same suite as the rest of the project.

## 4. The dry run suppresses, it does not print

A dry run that expands text and then still writes a file is worse than none: it
looks safe. `--dry-run` suppresses every command, every write through `fs`, every
`poll`, every `http` call and every `proc` signal, and each of them reports what it
*would* have done.

Reads are deliberately untouched — `read_text`, `glob`, `stat` behave normally — so
a dry run takes the same branches as the real run. Where a captured value steers
later logic, the call takes a stand-in: `sh.out(..., dry="deadbeef")`,
`http.get(..., dry_status=200)`, `poll(..., dry=4711)`.

## 5. Required configuration is declared, and the error is a set of instructions

A shared task needs values from its consumer. Making them merely *undefined* — so
that a missing one is some parse error in a file the consumer never wrote — is the
usual answer, and it also means nothing shared may have a sensible default.

```python
@config.section("android")
@dataclass
class Android:
    module: str                                              # required
    pkg: str                                                 # required
    gradle_flags: list[str] = field(default_factory=list)    # default, still overridable
```

```
error: missing required configuration for [android]:
  android.module  (str)
  android.pkg     (str)

set it in any one of:
  Makefile.py   android.configure(module=...)
  make.toml     [android]
                module = ...
  environment   MAKE_ANDROID_MODULE=...
```

It fails before anything runs, it names the field and its type, and it names every
place the value could come from. Defaults and required values coexist.

## 6. Sharing is a versioned dependency

Tasks are a Python package, declared inline (PEP 723) or in `pyproject.toml`:

```python
# /// script
# dependencies = ["mkrun>=0.4", "acme-make>=1.2"]
# ///
```

`mk --sync` writes a lock file; `mk --sync --upgrade` moves the pin; `mk --doctor`
prints where each package actually resolved from. The alternative most runners
land on — clone a repository into a gitignored directory and hope — gives every
checkout whatever `HEAD` was when someone last ran it, with no way to say "this
repository needs the version from before the rename" and no way to know what it is
on right now.

During development, `[tool.uv.sources]` names a git or path source per package, and
a gitignored `.make/sources.toml` redirects any of them to a local checkout without
touching the committed file.

## 7. Namespaces, overrides, and abstract tasks

Groups are real namespaces (`web.start`, `box.ls`), not a naming convention with
prefixes. What matters more is that a shared task can be *replaced*:

```python
@task(override="web.start")     # replaces the one from the package
def start(): ...

@task(abstract=True)            # declared upstream, must be supplied downstream
def test_gate(): ...
```

Both are checked. Overriding a task that no longer exists — because upstream
renamed it — is an error, not a function that silently never runs. An abstract task
lists as `[unimplemented]` and refuses to run with instructions instead of failing
somewhere deep in a release.

## 8. It is a command runner, not a build system

File-based staleness — `inputs=` / `outputs=`, rebuild when a timestamp moved —
was implemented and then removed. `cargo`, `npm`, `uv`, `tsc` and every compiler
already track their own inputs, far better than a task runner can, and a second
dependency graph layered on top of them mostly produces confident wrong answers:
a stale build that was declared fresh.

What is left is the part that is genuinely the runner's job: `needs=[build, sign]`
runs those tasks first, once per invocation, in order.

## 9. Startup is a feature

About 30 ms to a task list, with a 150 ms budget enforced by a test. A command
typed dozens of times a day is abandoned the moment it stops feeling instant, so
imports are lazy — a task group you did not call is never imported — and no heavy
module is loaded at the top of a file.

## 10. Dangerous things ask

Some tasks are not undoable: a deploy, a database reset, a restart that triggers a
2FA push on someone's phone. `@task(dangerous=True)` demands `--yes` or an
interactive confirmation, so "it asked me and I said yes" is part of the record
rather than a habit of typing carefully.

---

## What it costs

- **A runtime.** Python 3.11+, and `uv` for shared task packages.
- **~30 ms of startup**, which is a real number, not zero.
- **Alpha.** The authoring API is stable in practice; the internals move.
- **A one-line task is longer than a shell alias.** If `cargo test` is the whole
  task, `sh.bash(...)` lets it stay one line, but it is still a function.
