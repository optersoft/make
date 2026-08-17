"""Aliases: `dx.start` for `dioxus.start`, and `ship` for `play.publish`.

The two things worth guarding here are that an alias never becomes a *rename*
-- every printed name stays canonical -- and that `__contains__` learns about
group aliases too, because that is what tells the command line where one
invocation ends and the next begins.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from helpers import write

from make.cli import main
from make.errors import TaskError, UsageError
from make.tasks import alias, group, task

# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


def test_a_group_alias_covers_every_task_in_the_group(registry):
    dioxus = group("dioxus", alias="dx", into=registry)

    @dioxus
    def start() -> None: ...

    @dioxus
    def stop() -> None: ...

    registry.finalize()
    assert registry.get("dx.start") is registry.get("dioxus.start")
    assert registry.get("dx.stop") is registry.get("dioxus.stop")
    assert "dx.start" in registry
    assert "dx.missing" not in registry


def test_the_alias_is_declared_before_the_tasks_exist(registry):
    """`group("dioxus", alias="dx")` runs first, and a task added later is covered."""
    dioxus = group("dioxus", alias="dx", into=registry)
    assert registry.get("dx.start") is None

    @dioxus
    def start() -> None: ...

    registry.finalize()
    assert registry.get("dx.start") is not None


def test_alias_works_for_a_group_the_caller_does_not_declare(registry):
    @task(group="dioxus", into=registry)
    def start() -> None: ...

    alias("dx", "dioxus", into=registry)
    registry.finalize()
    assert registry.get("dx.start") is start.__make_task__


def test_a_dotted_target_aliases_one_task(registry):
    @task(group="play", into=registry)
    def publish() -> None: ...

    alias("ship", "play.publish", into=registry)
    registry.finalize()
    assert registry.get("ship") is registry.get("play.publish")


def test_several_aliases_for_one_group(registry):
    dioxus = group("dioxus", aliases=["dx", "d"], into=registry)

    @dioxus
    def start() -> None: ...

    registry.finalize()
    assert registry.group_aliases("dioxus") == ("d", "dx")
    assert registry.get("d.start") is registry.get("dx.start")


def test_the_canonical_name_survives_the_alias(registry):
    dioxus = group("dioxus", alias="dx", into=registry)

    @dioxus
    def start() -> None: ...

    registry.finalize()
    found = registry.require("dx.start")
    assert found.full_name == "dioxus.start"
    assert found.usage.startswith("dioxus.start")


def test_aliases_normalize_like_task_names(registry):
    dioxus = group("dioxus", alias="d_x", into=registry)

    @dioxus
    def test_gate() -> None: ...

    registry.finalize()
    assert registry.get("d-x.test-gate") is registry.get("d_x.test_gate")


def test_names_for_lists_every_spelling(registry):
    dioxus = group("dioxus", alias="dx", into=registry)

    @dioxus.task(aliases=["up"])
    def start() -> None: ...

    registry.finalize()
    assert registry.names_for(registry.require("dx.start")) == ["dioxus.start", "up", "dx.start"]


def test_an_unknown_task_suggests_the_aliased_spelling(registry):
    dioxus = group("dioxus", alias="dx", into=registry)

    @dioxus
    def start() -> None: ...

    registry.finalize()
    with pytest.raises(UsageError) as caught:
        registry.require("dx.strt")
    assert "dx.start" in (caught.value.hint or "")


def test_snapshot_carries_the_aliases(registry):
    dioxus = group("dioxus", alias="dx", into=registry)

    @dioxus
    def start() -> None: ...

    registry.finalize()
    saved = registry.snapshot()
    registry.clear()
    assert registry.get("dx.start") is None
    registry.restore(saved)
    assert registry.get("dx.start") is not None


# --------------------------------------------------------------------------
# What must be an error
# --------------------------------------------------------------------------


def test_an_alias_pointing_at_nothing_is_an_error(registry):
    @task(group="dioxus", into=registry)
    def start() -> None: ...

    alias("dx", "dixous", into=registry)
    with pytest.raises(TaskError, match="group 'dixous', which has no tasks"):
        registry.finalize()


def test_an_alias_that_is_already_a_group_is_an_error(registry):
    @task(group="dioxus", into=registry)
    def start() -> None: ...

    @task(group="dx", into=registry)
    def build() -> None: ...

    alias("dx", "dioxus", into=registry)
    with pytest.raises(TaskError, match="already a group name"):
        registry.finalize()


def test_an_alias_for_a_task_that_does_not_exist_is_an_error(registry):
    alias("ship", "play.publish", into=registry)
    with pytest.raises(TaskError, match="not a task"):
        registry.finalize()


def test_an_alias_shadowing_a_task_is_an_error(registry):
    @task(group="play", into=registry)
    def publish() -> None: ...

    @task(into=registry)
    def ship() -> None: ...

    alias("ship", "play.publish", into=registry)
    with pytest.raises(TaskError, match="would shadow"):
        registry.finalize()


def test_one_alias_cannot_mean_two_things(registry):
    alias("dx", "dioxus", into=registry)
    with pytest.raises(TaskError, match="already means 'dioxus'"):
        alias("dx", "drive", into=registry)


def test_declaring_the_same_alias_twice_is_fine(registry):
    """Two packages may agree; only disagreement is a mistake."""

    @task(group="dioxus", into=registry)
    def start() -> None: ...

    alias("dx", "dioxus", into=registry)
    alias("dx", "dioxus", into=registry)
    registry.finalize()
    assert registry.get("dx.start") is not None


def test_an_alias_cannot_contain_a_dot(registry):
    with pytest.raises(TaskError, match="cannot contain a dot"):
        alias("dx.start", "dioxus.start", into=registry)


def test_the_declaration_site_is_named(registry):
    alias("dx", "dioxus", into=registry)
    with pytest.raises(TaskError) as caught:
        registry.finalize()
    assert __file__ in caught.value.message


# --------------------------------------------------------------------------
# The command line
# --------------------------------------------------------------------------

TASKS = """
from make import alias, group, sh, task

dioxus = group("dioxus", alias="dx")

@dioxus
def start(*, port: int = 8001) -> None:
    \"\"\"Start the dev server.\"\"\"
    sh("dx", "serve", "--port", port)

@dioxus
def stop() -> None:
    \"\"\"Stop it.\"\"\"
    sh("pkill", "dx")

@task(group="tailwind")
def css() -> None:
    sh("tailwindcss")

alias("tw", "tailwind")
"""


@pytest.fixture
def repo(project: Path) -> Path:
    write(project / "Makefile.py", TASKS)
    return project


def test_an_aliased_task_runs(repo: Path, capsys):
    assert main(["-n", "dx.start", "--port", "9000"]) == 0
    assert "9000" in capsys.readouterr().err


def test_two_aliased_tasks_in_one_command_line(repo: Path, capsys):
    assert main(["-n", "dx.start", "dx.stop"]) == 0
    out = capsys.readouterr().err
    assert "dx serve" in out and "pkill" in out


def test_the_listing_shows_the_alias_beside_the_group(repo: Path, capsys):
    assert main(["--list"]) == 0
    out = capsys.readouterr().err
    assert "dioxus  (dx)" in out
    assert "tailwind  (tw)" in out
    assert "dx.start" not in out, "the listing stays canonical"


def test_completion_offers_both_spellings(repo: Path, capsys):
    assert main(["--names"]) == 0
    names = capsys.readouterr().out.split()
    assert "dioxus.start" in names and "dx.start" in names


def test_help_names_the_canonical_task(repo: Path, capsys):
    assert main(["--help", "dx.start"]) == 0
    out = capsys.readouterr().err
    assert "usage: dioxus.start" in out
    assert "also called: dx.start" in out


def test_the_json_listing_reports_every_spelling(repo: Path, capsys):
    assert main(["--list", "--json"]) == 0
    payload = {item["name"]: item for item in json.loads(capsys.readouterr().out)}
    assert payload["dioxus.start"]["aliases"] == ["dx.start"]


def test_a_mistyped_alias_still_fails(repo: Path, capsys):
    assert main(["dx.strt"]) != 0
    assert "dx.start" in capsys.readouterr().err
