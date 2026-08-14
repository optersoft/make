"""Prerequisites, gates, staleness, parallelism."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from make.errors import Aborted, TaskError, ToolMissing
from make.runner import is_up_to_date, run_one
from make.tasks import registry as global_registry
from make.tasks import task
from make.testing import context, record


def test_needs_run_first_and_only_once():
    calls: list[str] = []

    @task
    def prepare() -> None:
        calls.append("prepare")

    @task(needs=[prepare])
    def left() -> None:
        calls.append("left")

    @task(needs=[prepare])
    def right() -> None:
        calls.append("right")

    @task(needs=[left, right])
    def build() -> None:
        calls.append("build")

    with context():
        run_one(global_registry.require("build"))

    assert calls.count("prepare") == 1  # shared prerequisite, run once
    assert calls[-1] == "build"
    assert calls.index("prepare") < calls.index("left")


def test_circular_needs_is_reported_as_a_chain():
    @task(needs=["b"])
    def a() -> None: ...

    @task(needs=["a"])
    def b() -> None: ...

    with context():
        with pytest.raises(TaskError, match="circular needs"):
            run_one(global_registry.require("a"))


def test_requires_is_checked_before_the_body_runs():
    ran = []

    @task(requires=["fastlane"])
    def publish() -> None:
        ran.append(True)

    with context(), record(missing_tools=["fastlane"]):
        with pytest.raises(ToolMissing):
            run_one(global_registry.require("publish"))
    assert ran == []


def test_dangerous_task_refuses_without_confirmation():
    @task(dangerous=True)
    def wipe() -> None: ...

    with context():
        with pytest.raises(Aborted, match="declined"):
            run_one(global_registry.require("wipe"))


def test_yes_pre_answers_the_gate():
    ran = []

    @task(dangerous=True)
    def wipe() -> None:
        ran.append(True)

    with context(yes=True), record():
        run_one(global_registry.require("wipe"))
    assert ran == [True]


def test_abstract_task_refuses_with_instructions():
    @task(group="play", abstract=True)
    def test_gate() -> None: ...

    with context():
        with pytest.raises(TaskError) as caught:
            run_one(global_registry.require("play.test-gate"))
    assert "not implemented" in caught.value.message
    assert "override='play.test-gate'" in (caught.value.hint or "")


def test_staleness_skips_when_outputs_are_newer(project: Path):
    (project / "src").mkdir()
    (project / "src" / "a.css").write_text("a")
    time.sleep(0.01)
    (project / "out.css").write_text("compiled")

    ran = []

    @task(inputs=["src/*.css"], outputs=["out.css"])
    def css() -> None:
        ran.append(True)

    with context(root=project):
        item = global_registry.require("css")
        assert is_up_to_date(item)
        run_one(item)
    assert ran == []


def test_staleness_runs_when_an_input_is_newer(project: Path):
    (project / "src").mkdir()
    (project / "out.css").write_text("compiled")
    time.sleep(0.01)
    (project / "src" / "a.css").write_text("a")

    ran = []

    @task(inputs=["src/*.css"], outputs=["out.css"])
    def css() -> None:
        ran.append(True)

    with context(root=project):
        run_one(global_registry.require("css"))
    assert ran == [True]


def test_force_ignores_staleness(project: Path):
    (project / "out.css").write_text("compiled")
    (project / "in.css").write_text("a")

    ran = []

    @task(inputs=["in.css"], outputs=["out.css"])
    def css() -> None:
        ran.append(True)

    with context(root=project, force=True):
        run_one(global_registry.require("css"))
    assert ran == [True]


def test_missing_output_is_always_stale(project: Path):
    (project / "in.css").write_text("a")

    @task(inputs=["in.css"], outputs=["never-built.css"])
    def css() -> None: ...

    with context(root=project):
        assert not is_up_to_date(global_registry.require("css"))


def test_parallel_prerequisites_all_run():
    seen: list[str] = []
    import threading

    lock = threading.Lock()

    def note(name: str) -> None:
        time.sleep(0.02)
        with lock:
            seen.append(name)

    @task
    def one() -> None:
        note("one")

    @task
    def two() -> None:
        note("two")

    @task
    def three() -> None:
        note("three")

    @task(needs=[one, two, three])
    def all_of_them() -> None: ...

    started = time.monotonic()
    with context(jobs=4):
        run_one(global_registry.require("all-of-them"))
    elapsed = time.monotonic() - started

    assert sorted(seen) == ["one", "three", "two"]
    assert elapsed < 0.06 * 3  # actually concurrent, not merely correct


def test_a_failing_prerequisite_stops_the_run():
    ran = []

    @task
    def broken() -> None:
        raise RuntimeError("nope")

    @task(needs=[broken])
    def after() -> None:
        ran.append(True)

    with context():
        with pytest.raises(RuntimeError):
            run_one(global_registry.require("after"))
    assert ran == []
