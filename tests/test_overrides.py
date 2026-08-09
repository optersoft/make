"""Hooks a consumer is expected to replace.

This is the pattern `just` cannot express at all: a shared package shipping a
default that a consumer overrides. Duplicate recipes across imports are fatal
there, which is why `play-test-gate` had to be left *undefined* upstream.
"""

from __future__ import annotations

from make import env, invoke, recipe
from make.recipes import registry
from make.testing import context, record


def test_a_shared_default_is_replaced_by_the_consumers_override():
    calls: list[str] = []

    # ...in the shared package
    @recipe(group="web", name="preflight")
    def preflight() -> None:
        calls.append("shared default")

    # ...in the consumer's Makefile.py
    @recipe(override="web.preflight")
    def my_preflight() -> None:
        calls.append("consumer override")

    registry.finalize()

    with context():
        invoke("web.preflight")

    assert calls == ["consumer override"]


def test_calling_the_imported_function_still_runs_that_function():
    """Recipes stay plain functions -- only `invoke` consults the registry."""
    calls: list[str] = []

    @recipe(group="web", name="preflight")
    def preflight() -> None:
        calls.append("shared default")

    @recipe(override="web.preflight")
    def my_preflight() -> None:
        calls.append("consumer override")

    registry.finalize()
    preflight()
    assert calls == ["shared default"]


def test_a_preflight_can_export_variables_for_every_later_command():
    @recipe(group="web", name="preflight")
    def preflight() -> None: ...

    @recipe(override="web.preflight")
    def my_preflight() -> None:
        env.export(AUTH_COOKIE_SECURE="false", STORE_DIR="/tmp/store")

    registry.finalize()

    with context():
        invoke("web.preflight")
        assert env.get("AUTH_COOKIE_SECURE") == "false"
        assert env.get("STORE_DIR") == "/tmp/store"


def test_export_does_not_clobber_a_value_the_caller_set(monkeypatch):
    """Matches the `${VAR:-default}` idiom these blocks are translated from."""
    monkeypatch.setenv("STORE_DIR", "/explicit")
    with context():
        env.export(STORE_DIR="/default")
        assert env.get("STORE_DIR") == "/explicit"

        env.export(force=True, STORE_DIR="/forced")
        assert env.get("STORE_DIR") == "/forced"


def test_exported_variables_reach_the_child_process():
    with context(), record() as rec:
        env.export(TOKEN="s3cret")
        from make import sh

        sh("printenv")
    assert rec.commands == [["printenv"]]


def test_an_override_takes_over_the_targets_identity():
    """A consumer writes `def test_gate()`, but the recipe is still `play.test-gate`.

    Otherwise the replacement occupies the right slot while listing itself under
    a different name -- so `make --list` and `needs=` disagree with what
    actually runs.
    """

    @recipe(group="play", name="test-gate", abstract=True, aliases=["gate"])
    def test_gate() -> None: ...

    @recipe(override="play.test-gate")
    def my_gate() -> str:
        return "checked"

    registry.finalize()

    replacement = registry.require("play.test-gate")
    assert replacement.full_name == "play.test-gate"
    assert replacement.group == "play"
    assert registry.get("gate") is replacement  # the target's alias survives
    assert [r.full_name for r in registry.all()] == ["play.test-gate"]
    assert replacement.fn() == "checked"
