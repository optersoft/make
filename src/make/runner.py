"""Executing tasks: prerequisites, gates, staleness, parallelism.

Everything here is deliberately outside the decorator, so that importing a
task and calling it from Python stays plain function application. `needs=`,
`requires=`, the confirmation gate and staleness skipping are properties of
*running* a task from the command line, not of the function itself -- which is
what makes tasks testable without a harness.
"""

from __future__ import annotations

import contextvars
import os
import threading
import time
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .context import confirm, current, debug, note
from .errors import Aborted, TaskError
from .sh import sh
from .tasks import Task, normalize
from .tasks import registry as default_registry

__all__ = ["Invocation", "run", "run_one"]

_memo_lock = threading.Lock()


@dataclass
class Invocation:
    """One task plus the arguments it was called with."""

    task: Task
    args: list[Any] = field(default_factory=list)
    kwargs: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Staleness
# --------------------------------------------------------------------------


def _expand(patterns: Sequence[str], root: Path) -> list[Path]:
    found: list[Path] = []
    for pattern in patterns:
        if any(ch in pattern for ch in "*?["):
            found.extend(p for p in root.glob(pattern) if p.is_file())
        else:
            path = root / pattern
            if path.is_dir():
                found.extend(p for p in path.rglob("*") if p.is_file())
            elif path.is_file():
                found.append(path)
    return found


def is_up_to_date(item: Task) -> bool:
    """True when every declared output is newer than every declared input.

    Opt-in and deliberately shallow: this is not a build system, and a task
    without `inputs=`/`outputs=` always runs. It exists so that expensive,
    obviously-cacheable steps (compiling a stylesheet, rendering a diagram) stop
    costing a second every time something else in the chain needs them.
    """
    if not item.inputs or not item.outputs:
        return False
    root = current().root
    outputs = _expand(item.outputs, root)
    if not outputs:
        return False
    for pattern in item.outputs:
        if not any(ch in pattern for ch in "*?[") and not (root / pattern).exists():
            return False
    inputs = _expand(item.inputs, root)
    if not inputs:
        return True
    newest_input = max(p.stat().st_mtime for p in inputs)
    oldest_output = min(p.stat().st_mtime for p in outputs)
    return oldest_output >= newest_input


# --------------------------------------------------------------------------
# Gates
# --------------------------------------------------------------------------


def _check_tools(item: Task) -> None:
    if item.requires:
        sh.require(*item.requires, hint=f"required by task {item.full_name}")


def _check_dangerous(item: Task) -> None:
    if not item.dangerous:
        return
    context = current()
    if context.dry_run or context.yes:
        return
    question = f"{item.full_name} is marked dangerous. Run it?"
    if not confirm(question):
        raise Aborted(
            f"{item.full_name}: declined", hint="pass --yes to run it without asking (for example from CI)"
        )


def _check_abstract(item: Task) -> None:
    if not item.abstract:
        return
    raise TaskError(
        f"{item.full_name} is declared but not implemented",
        hint=f"define it in your task file:\n"
        f"    @task(override={item.full_name!r})\n"
        f"    def {item.name.replace('-', '_')}(...):\n"
        f"        ...",
    )


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------


def run_one(item: Task, args: Sequence[Any] = (), kwargs: dict[str, Any] | None = None) -> Any:
    """Run a single task with its gates, prerequisites and staleness check."""
    context = current()
    key = normalize(item.full_name)

    with _memo_lock:
        if key in context._memo:
            return context._memo[key]

    _check_abstract(item)
    _check_tools(item)

    if is_up_to_date(item) and not context.force:
        note(f"{item.full_name}: up to date")
        with _memo_lock:
            context._memo[key] = None
        return None

    _run_needs(item)
    _check_dangerous(item)

    if context.verbose or not context.quiet:
        debug(f"running {item.full_name}")

    started = time.monotonic()
    previous_cwd = Path.cwd()
    target_cwd = context.invocation_dir if item.keep_cwd else context.root
    try:
        if Path.cwd() != target_cwd:
            os.chdir(target_cwd)
        result = item.fn(*args, **(kwargs or {}))
    finally:
        if Path.cwd() != previous_cwd:
            os.chdir(previous_cwd)

    elapsed = time.monotonic() - started
    if elapsed > 5 and not context.quiet:
        note(f"{item.full_name} took {elapsed:.1f}s")

    with _memo_lock:
        context._memo[key] = result
    return result


_running: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar("make_stack", default=())


def _run_needs(item: Task) -> None:
    names = item.resolved_needs()
    if not names:
        return

    stack = _running.get()
    if item.full_name in stack:
        chain = " -> ".join([*stack, item.full_name])
        raise TaskError(f"circular needs=: {chain}")
    token = _running.set((*stack, item.full_name))
    try:
        context = current()
        pending = [name for name in names if normalize(name) not in context._memo]
        if not pending:
            return
        # A keep_cwd task chdirs the whole process, so it can never share a
        # wave with anything else. Everything else runs at ctx.root already.
        concurrent = [n for n in pending if not default_registry.require(n).keep_cwd]
        serial = [n for n in pending if n not in concurrent]

        if context.jobs > 1 and len(concurrent) > 1:
            _run_parallel(concurrent)
            serial = [*serial]
        else:
            serial = pending
        for name in serial:
            run_one(default_registry.require(name))
    finally:
        _running.reset(token)


def _run_parallel(names: Sequence[str]) -> None:
    """Run independent prerequisites concurrently.

    Each worker thread starts with an empty `ContextVar` map, so the parent's
    context is re-applied by value rather than by entering a copied
    `contextvars.Context` -- which cannot be entered from two threads at once.
    The memo dict is shared and guarded by `_memo_lock`, so a prerequisite two
    branches depend on is still executed exactly once.
    """
    parent = current()
    stack = _running.get()
    errors: list[BaseException] = []

    def worker(name: str) -> None:
        from .context import set_context

        set_context(parent)
        _running.set(stack)
        try:
            run_one(default_registry.require(name))
        except BaseException as exc:
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=parent.jobs) as pool:
        list(pool.map(worker, names))
    if errors:
        raise errors[0]


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    """Run a task by name, honouring whatever override is in force.

    Calling an imported function directly runs *that* function -- which is
    usually what you want, and is why tasks stay plain functions. But a hook a
    consumer is expected to replace (`web.preflight`, `play.test-gate`) has to
    be dispatched through the registry, or the shared package would keep calling
    its own default and silently ignore the override.
    """
    return run_one(default_registry.require(name), args, kwargs)


def run(invocations: Iterable[Invocation]) -> Any:
    """Run each invocation in the order the user gave them."""
    result: Any = None
    for invocation in invocations:
        result = run_one(invocation.task, invocation.args, invocation.kwargs)
    return result
