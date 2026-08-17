"""`proc` centralises the lsof/pgrep/kill discipline the task files repeated."""

from __future__ import annotations

from make import proc
from make.testing import context, record


def test_port_pids_asks_for_listeners_only():
    with record(responses={"lsof": "123\n456\n"}) as rec:
        assert proc.port_pids(8080) == [123, 456]
    assert rec.saw("-iTCP:8080")
    assert rec.saw("-sTCP:LISTEN")


def test_reap_port_signals_each_listener():
    with record(responses={"lsof": "123\n"}) as rec:
        assert proc.reap_port(8080) == [123]
    assert rec.saw("kill", "-15", "123")


def test_reap_force_uses_sigkill():
    with record(responses={"pgrep": "99\n"}) as rec:
        assert proc.reap("dx serve.*port 8002", force=True) == [99]
    assert rec.saw("kill", "-9", "99")


def test_nothing_matching_is_the_normal_case_not_an_error():
    with record() as rec:
        assert proc.reap_port(8080) == []
    assert not rec.saw("kill")


def test_dry_run_finds_but_never_kills():
    with context(dry_run=True), record() as rec:
        assert proc.reap_port(8080, dry=["123"]) == []
    assert rec.commands == []
