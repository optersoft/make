"""`poll()` is the one loop the fleet used to write four different ways."""

from __future__ import annotations

import pytest

from make import poll
from make.errors import WaitTimeout
from make.testing import context


def test_returns_the_first_truthy_value_not_just_true():
    answers = iter(["", "", "4711"])
    with context():
        assert poll(lambda: next(answers), timeout=5, interval=0) == "4711"


def test_times_out_with_the_label_in_the_message():
    with context():
        with pytest.raises(WaitTimeout, match="the app to start"):
            poll(lambda: False, timeout=0, interval=0, message="the app to start")


def test_dry_run_never_calls_the_check():
    calls: list[int] = []

    def check() -> bool:
        calls.append(1)
        return False

    with context(dry_run=True):
        assert poll(check, timeout=5, dry="stand-in") == "stand-in"
    assert calls == []


def test_zero_timeout_still_tries_once():
    with context():
        assert poll(lambda: 42, timeout=0, interval=0) == 42
