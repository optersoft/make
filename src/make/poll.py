"""Waiting for something to become true.

Every repo in the fleet had its own spelling of the same loop: bash
`until curl -sf ...; do sleep 1; done`, bash `for _ in $(seq 1 60)`, bash
`until getprop sys.boot_completed`, and a Python `while` with a deadline. Each
one re-decided the timeout, the interval, what to print, and what a dry run
should pretend -- and two of them could hang forever.

    from make import poll, sh

    pid = poll(lambda: sh.out("pidof", "-s", pkg, check=False, dry="4711"),
               timeout=10, message=f"{pkg} to start")

`poll()` returns the first truthy value `check` produces, so the condition and
the answer are one expression. On timeout it raises `WaitTimeout`, naming what
it was waiting for.

Under `--dry-run` the loop does not run at all and `dry` is returned: the
commands inside `check` are suppressed, so looping on their stand-in answers
could never terminate.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, TypeVar

from .context import current, echo, paint
from .errors import WaitTimeout

__all__ = ["poll"]

T = TypeVar("T")


def poll(
    check: Callable[[], T],
    *,
    timeout: float = 60.0,
    interval: float = 1.0,
    message: str | None = None,
    dry: Any = True,
) -> T:
    """Call `check` until it returns a truthy value, then return that value.

    Args:
        check: called repeatedly; the first truthy return ends the wait.
        timeout: seconds before giving up with `WaitTimeout`.
        interval: seconds between attempts.
        message: what is being waited for -- used in output and in the error.
        dry: the stand-in returned immediately under `--dry-run`.
    """
    context = current()
    label = message or getattr(check, "__name__", "condition")
    if label == "<lambda>":
        label = "condition"

    if context.dry_run:
        echo(paint("[dry-run] ", "yellow") + paint(f"wait for {label}", "cyan"))
        return dry

    if not context.quiet:
        echo(paint("~ ", "cyan", "bold") + paint(f"waiting for {label} (up to {timeout:.0f}s)", "cyan"))

    deadline = time.monotonic() + timeout
    while True:
        value = check()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise WaitTimeout(
                f"timed out after {timeout:.0f}s waiting for {label}",
                hint="raise timeout= if it genuinely needs longer",
            )
        time.sleep(interval)
