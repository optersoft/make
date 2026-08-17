"""The `@task` decorator and the registry behind it.

Two things `just` cannot express, and the reason this module is more than a
dictionary:

* **Overriding.** In `just`, a duplicate task name across imports is fatal, so
  a shared file can never ship a default that a consumer replaces. The result is
  `play-test-gate`: a task deliberately left *undefined* upstream so each
  consumer is forced to define it, with a parse error as the only prompt. Here a
  shared package ships `abstract=True` (listed as unimplemented, refuses to run
  with a message naming what to write) and a consumer replaces anything with an
  explicit `override=`. Accidental shadowing is still an error, naming both
  definitions and their files.
* **Namespaces.** Tasks live in modules, so `web.start` and `box.ls` need no
  `web-` / `box-` prefix convention and two packages cannot collide.
* **Short names for a whole namespace.** `alias("dx", "dioxus")` -- or
  `group("dioxus", alias="dx")` where the group is declared -- makes `dx.start`
  resolve to `dioxus.start`, for every task in the group and any added later.
  The alias is an input spelling only: `--list`, `--help`, `needs=` and every
  error message keep saying `dioxus.start`, so nothing a reader sees is renamed.

The decorator returns the original function untouched, with the task attached
as an attribute. Importing a task and calling it from Python is therefore
ordinary function application -- no wrapper, no proxy, and unit-testable without
going near the CLI.
"""

from __future__ import annotations

import inspect
import re
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import TaskError, UsageError
from .params import Param, build_params, render_usage

__all__ = ["Group", "Task", "Registry", "alias", "group", "task", "registry"]

_DOC_ARGS = re.compile(r"^\s*(?:Args|Arguments|Params|Parameters)\s*:\s*$", re.IGNORECASE)
_DOC_ENTRY = re.compile(r"^\s{1,8}(?:\*{0,2})(\w+)\s*(?:\([^)]*\))?\s*:\s*(.+?)\s*$")
_SPHINX_PARAM = re.compile(r"^\s*:param\s+(\w+)\s*:\s*(.+?)\s*$")


def _parse_docstring(doc: str | None) -> tuple[str, str, dict[str, str]]:
    """-> (summary, body, {param: help})."""
    if not doc:
        return "", "", {}
    lines = inspect.cleandoc(doc).splitlines()
    summary = lines[0].strip() if lines else ""

    params: dict[str, str] = {}
    body_lines: list[str] = []
    in_args = False
    for line in lines[1:]:
        sphinx = _SPHINX_PARAM.match(line)
        if sphinx:
            params[sphinx.group(1)] = sphinx.group(2)
            continue
        if _DOC_ARGS.match(line):
            in_args = True
            continue
        if in_args:
            entry = _DOC_ENTRY.match(line)
            if entry:
                params[entry.group(1)] = entry.group(2)
                continue
            if line.strip():
                in_args = False
        body_lines.append(line)
    return summary, "\n".join(body_lines).strip(), params


def normalize(name: str) -> str:
    """`web.test_gate` and `web.test-gate` are the same task."""
    return name.replace("_", "-").strip()


@dataclass
class Task:
    """One runnable function plus everything the runner needs to know about it."""

    fn: Callable[..., Any]
    name: str
    group: str | None
    summary: str = ""
    description: str = ""
    params: list[Param] = field(default_factory=list)
    needs: tuple[Any, ...] = ()
    requires: tuple[str, ...] = ()
    dangerous: bool = False
    abstract: bool = False
    hidden: bool = False
    aliases: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    keep_cwd: bool = False
    override: str | bool = False
    module: str = ""
    file: Path | None = None
    lineno: int = 0

    @property
    def full_name(self) -> str:
        return f"{self.group}.{self.name}" if self.group else self.name

    @property
    def location(self) -> str:
        return f"{self.file}:{self.lineno}" if self.file else self.module

    @property
    def usage(self) -> str:
        return render_usage(self.full_name, self.params)

    def resolved_needs(self) -> list[str]:
        names: list[str] = []
        for need in self.needs:
            if isinstance(need, str):
                names.append(normalize(need))
            elif callable(need):
                attached = getattr(need, "__make_task__", None)
                if attached is None:
                    raise TaskError(
                        f"{self.full_name}: needs= got {need!r}, which is not a task",
                        hint="decorate it with @task, or just call it from the body",
                    )
                names.append(attached.full_name)
            else:
                raise TaskError(f"{self.full_name}: needs= entries must be tasks or names")
        return names

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Task {self.full_name}>"


#: What `snapshot()` hands to `restore()`: tasks, task aliases, group aliases,
#: the aliases still to be checked, and the overrides still to be applied.
_State = tuple[dict[str, Task], dict[str, str], dict[str, str], list[tuple[str, str, str]], list[Task]]


class Registry:
    """Every task visible to this run."""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._aliases: dict[str, str] = {}
        self._group_aliases: dict[str, str] = {}
        self._declared_aliases: list[tuple[str, str, str]] = []
        self._overrides: list[Task] = []
        self._finalized = False

    # -- registration ------------------------------------------------------

    def add(self, item: Task) -> Task:
        key = normalize(item.full_name)
        if item.override:
            self._overrides.append(item)
            return item
        existing = self._tasks.get(key)
        if existing is not None:
            raise TaskError(
                f"duplicate task {item.full_name!r}\n"
                f"  first defined  {existing.location}\n"
                f"  again at       {item.location}",
                hint="pass override=True to replace it deliberately, or give one of them a different group=",
            )
        self._tasks[key] = item
        for alias in item.aliases:
            self._aliases[normalize(alias)] = key
        self._finalized = False
        return item

    def add_alias(self, name: str, target: str, *, where: str = "") -> None:
        """Register `name` as a second spelling of a group, or of one task.

        A dotted target is one task (`alias("ship", "play.publish")`); a bare one
        is a group prefix, so every task in the group answers to it -- including
        tasks added after the alias, which is why this stores a prefix rather
        than expanding to the names that happen to exist right now.

        Registered immediately so lookups work without a `finalize()`, but
        *checked* there: a package declares its alias on the `group()` line,
        before any of its tasks exist.
        """
        key = normalize(name)
        goal = normalize(target)
        if not key or not goal:
            raise TaskError(f"alias({name!r}, {target!r}): both a name and a target are required")
        if "." in key:
            raise TaskError(
                f"alias {name!r} cannot contain a dot",
                hint="an alias is one name: alias('dx', 'dioxus') covers the whole group",
            )
        existing = self._aliases.get(key) or self._group_aliases.get(key)
        if existing is not None and existing != goal:
            raise TaskError(
                f"alias {key!r} already means {existing!r}, so it cannot also mean {goal!r}"
                + (f"\n  declared at {where}" if where else ""),
                hint="pick a different alias, or drop one of the two declarations",
            )
        table = self._aliases if "." in goal else self._group_aliases
        table[key] = goal
        self._declared_aliases.append((key, goal, where))
        self._finalized = False

    def _check_aliases(self) -> None:
        """Every alias points somewhere real, and shadows nothing."""
        groups = {normalize(item.group) for item in self._tasks.values() if item.group}
        for key, goal, where in self._declared_aliases:
            at = f"\n  declared at {where}" if where else ""
            if "." in goal:
                if goal not in self._tasks:
                    raise TaskError(
                        f"alias {key!r} points at {goal!r}, which is not a task{at}",
                        hint="run `mk --list` to see what exists",
                    )
                if key in self._tasks:
                    raise TaskError(
                        f"alias {key!r} would shadow the task of the same name{at}",
                        hint=f"{self._tasks[key].location} defines it",
                    )
            else:
                if goal not in groups:
                    raise TaskError(
                        f"alias {key!r} points at group {goal!r}, which has no tasks{at}",
                        hint="the group may have been renamed; run `mk --list` to see what exists",
                    )
                if key in groups:
                    raise TaskError(
                        f"alias {key!r} is already a group name, so it can never resolve{at}",
                        hint="pick an alias that is not a group",
                    )
        self._declared_aliases.clear()

    def finalize(self) -> None:
        """Apply deferred overrides, then check the aliases. Runs after import.

        Deferred, because a consumer's `mk.py` imports the shared package (which
        registers the original) and then defines the replacement -- but the
        opposite order is just as reasonable, and neither should be a footgun.
        """
        for item in self._overrides:
            target = item.override if isinstance(item.override, str) else item.full_name
            key = normalize(target)
            if key not in self._tasks:
                raise TaskError(
                    f"{item.location}: override={target!r} does not match any task",
                    hint="the upstream task may have been renamed; run `mk --list` to see what exists",
                )
            replaced = self._tasks[key]
            item.override = False
            # The replacement takes over the target's identity, not just its
            # slot. A consumer writes `def test_gate()` in its own file, but the
            # task is still `play.test-gate` -- so that is what it must be
            # called in `--list`, in usage strings and in `needs=` elsewhere.
            item.group = replaced.group
            item.name = replaced.name
            item.aliases = tuple(dict.fromkeys(replaced.aliases + item.aliases))
            self._tasks[key] = item
            for alias in item.aliases:
                self._aliases[normalize(alias)] = key
        self._overrides.clear()
        self._check_aliases()
        self._finalized = True

    def clear(self) -> None:
        self._tasks.clear()
        self._aliases.clear()
        self._group_aliases.clear()
        self._declared_aliases.clear()
        self._overrides.clear()

    def snapshot(self) -> _State:
        """Capture the current contents, for `restore`.

        Registration happens at import time, and a module is imported once per
        process -- so a test that clears the registry to get isolation would
        otherwise permanently unregister every task for the rest of the run.
        Take a snapshot, clear, then restore.
        """
        return (
            dict(self._tasks),
            dict(self._aliases),
            dict(self._group_aliases),
            list(self._declared_aliases),
            list(self._overrides),
        )

    def restore(self, state: _State) -> None:
        tasks, aliases, group_aliases, declared, overrides = state
        self._tasks = dict(tasks)
        self._aliases = dict(aliases)
        self._group_aliases = dict(group_aliases)
        self._declared_aliases = list(declared)
        self._overrides = list(overrides)

    # -- lookup ------------------------------------------------------------

    def __contains__(self, name: object) -> bool:
        if not isinstance(name, str):
            return False
        return self.get(name) is not None

    def get(self, name: str) -> Task | None:
        key = normalize(name)
        key = self._aliases.get(key, key)
        found = self._tasks.get(key)
        if found is not None:
            return found
        # Only now, on a miss: an exact name is never reinterpreted, so a group
        # alias can never take a name a real group already answers to.
        prefix, dot, rest = key.partition(".")
        real = self._group_aliases.get(prefix) if dot else None
        return self._tasks.get(f"{real}.{rest}") if real else None

    def group_aliases(self, name: str) -> tuple[str, ...]:
        """Every alias that resolves to the group `name`."""
        target = normalize(name)
        return tuple(sorted(a for a, goal in self._group_aliases.items() if goal == target))

    def names_for(self, item: Task) -> list[str]:
        """Every spelling that resolves to `item`, the canonical one first."""
        key = normalize(item.full_name)
        names = [item.full_name]
        names += sorted(a for a, target in self._aliases.items() if target == key)
        if item.group:
            names += [f"{a}.{item.name}" for a in self.group_aliases(item.group)]
        return list(dict.fromkeys(names))

    def require(self, name: str) -> Task:
        found = self.get(name)
        if found is not None:
            return found
        import difflib

        candidates = list(self._tasks) + list(self._aliases)
        for short, target in self._group_aliases.items():
            prefix = f"{target}."
            candidates += [f"{short}.{key[len(prefix) :]}" for key in self._tasks if key.startswith(prefix)]
        close = difflib.get_close_matches(normalize(name), candidates, n=3, cutoff=0.5)
        hint = f"did you mean: {', '.join(close)}?" if close else "run `mk --list` to see them all"
        raise UsageError(f"no task named {name!r}", hint=hint)

    def all(self, *, include_hidden: bool = False) -> list[Task]:
        items = [r for r in self._tasks.values() if include_hidden or not r.hidden]
        return sorted(items, key=lambda r: (r.group or "", r.name))

    def groups(self, *, include_hidden: bool = False) -> dict[str, list[Task]]:
        out: dict[str, list[Task]] = {}
        for item in self.all(include_hidden=include_hidden):
            out.setdefault(item.group or "", []).append(item)
        return out


registry = Registry()


# --------------------------------------------------------------------------
# The decorator
# --------------------------------------------------------------------------


def _make_task(
    fn: Callable[..., Any],
    *,
    name: str | None,
    group: str | None,
    needs: Sequence[Any],
    requires: Sequence[str],
    dangerous: bool,
    abstract: bool,
    hidden: bool,
    aliases: Sequence[str],
    inputs: Sequence[str],
    outputs: Sequence[str],
    keep_cwd: bool,
    override: str | bool,
    into: Registry,
) -> Callable[..., Any]:
    summary, description, doc_help = _parse_docstring(fn.__doc__)
    params = build_params(fn, doc_help=doc_help)

    code = getattr(fn, "__code__", None)
    item = Task(
        fn=fn,
        name=normalize(name or fn.__name__),
        group=group,
        summary=summary,
        description=description,
        params=params,
        needs=tuple(needs),
        requires=tuple(requires),
        dangerous=dangerous,
        abstract=abstract,
        hidden=hidden or (name or fn.__name__).startswith("_"),
        aliases=tuple(aliases),
        inputs=tuple(inputs),
        outputs=tuple(outputs),
        keep_cwd=keep_cwd,
        override=override,
        module=getattr(fn, "__module__", ""),
        file=Path(code.co_filename) if code else None,
        lineno=code.co_firstlineno if code else 0,
    )
    into.add(item)
    fn.__make_task__ = item  # type: ignore[attr-defined]
    return fn


def task(
    fn: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    group: str | None = None,
    needs: Sequence[Any] = (),
    requires: Sequence[str] = (),
    dangerous: bool = False,
    abstract: bool = False,
    hidden: bool = False,
    aliases: Sequence[str] = (),
    inputs: Sequence[str] = (),
    outputs: Sequence[str] = (),
    keep_cwd: bool = False,
    override: str | bool = False,
    into: Registry | None = None,
) -> Any:
    """Mark a function as a task.

    Usable bare (`@task`) or called (`@task(group="web", dangerous=True)`).

    Args:
        name: command-line name; defaults to the function name, `_` -> `-`.
        group: namespace, so the task is `<group>.<name>`.
        needs: tasks to run first, once per invocation.
        requires: tools that must be on PATH; checked before anything runs.
        dangerous: demand `--yes` or an interactive confirmation.
        abstract: declared but unimplemented; a consumer must override it.
        hidden: keep out of `--list` (also implied by a leading underscore).
        aliases: extra names that resolve to this task.
        inputs/outputs: globs; the task is skipped when outputs are newer.
        keep_cwd: run in the caller's directory instead of the task-file root.
        override: replace an existing task -- True for the same name, or the
            full name of the one being replaced.
    """

    def decorate(target: Callable[..., Any]) -> Callable[..., Any]:
        return _make_task(
            target,
            name=name,
            group=group,
            needs=needs,
            requires=requires,
            dangerous=dangerous,
            abstract=abstract,
            hidden=hidden,
            aliases=aliases,
            inputs=inputs,
            outputs=outputs,
            keep_cwd=keep_cwd,
            override=override,
            into=into or registry,
        )

    if fn is not None:
        return decorate(fn)
    return decorate


def _caller() -> str:
    """`file:line` of the first frame outside this module, for error messages."""
    frame = sys._getframe(1)
    while frame is not None and frame.f_globals.get("__name__") == __name__:
        frame = frame.f_back
    return f"{frame.f_code.co_filename}:{frame.f_lineno}" if frame is not None else ""


def alias(name: str, target: str, *, into: Registry | None = None) -> None:
    """Give a group -- or one task -- a second, shorter name.

        alias("dx", "dioxus")           # dx.start, dx.stop, dx.tailwind, ...
        alias("ship", "play.publish")   # one task, same as aliases=["ship"]

    A bare target is a group, and covers every task in it, including ones added
    later. A dotted target is that one task. Either way the alias is only an
    input spelling: everything the tool prints keeps the canonical name.

    Declare it beside the group where the group is defined -- a package ships its
    own short name with `group("dioxus", alias="dx")` -- or in a consumer's task
    file, for a group the consumer does not own.
    """
    (into or registry).add_alias(name, target, where=_caller())


class Group:
    """A namespace you can decorate with directly.

    web = group("web", alias="w")

    @web
    def start(*, port: int = 8001) -> None: ...

    @web.task(dangerous=True)
    def reset() -> None: ...
    """

    def __init__(
        self,
        name: str,
        *,
        alias: str | None = None,
        aliases: Sequence[str] = (),
        into: Registry | None = None,
    ) -> None:
        self.name = name
        self._registry = into or registry
        where = _caller()
        for short in ([alias] if alias else []) + list(aliases):
            self._registry.add_alias(short, name, where=where)

    def __call__(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        return task(fn, group=self.name, into=self._registry)

    def task(self, **kwargs: Any) -> Any:
        kwargs.setdefault("group", self.name)
        kwargs.setdefault("into", self._registry)
        return task(**kwargs)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Group {self.name}>"


def group(
    name: str, *, alias: str | None = None, aliases: Sequence[str] = (), into: Registry | None = None
) -> Group:
    """Create a namespace for tasks defined in this module.

    `alias=` (or `aliases=` for more than one) is a shorter spelling of the whole
    namespace: `group("dioxus", alias="dx")` makes every task in it answer to
    `dx.<name>` as well.
    """
    return Group(name, alias=alias, aliases=aliases, into=into)


def tasks_of(module: Any) -> Iterable[Task]:
    """Every task defined by `module`, for introspection and tests."""
    for value in vars(module).values():
        attached = getattr(value, "__make_task__", None)
        if attached is not None:
            yield attached
