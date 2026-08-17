"""`sh()` is where `just`'s quoting hazard is supposed to be structurally absent."""

from __future__ import annotations

import pytest

from make import sh
from make.errors import CommandFailed, ToolMissing
from make.testing import context, record


def test_arguments_are_never_reparsed_by_a_shell():
    nasty = 'a b"; rm -rf /; echo "'
    with record() as rec:
        sh("echo", nasty)
    assert rec.commands == [["echo", nasty]]  # one argument, intact


def test_values_are_stringified_and_nested_lists_flattened():
    from pathlib import Path

    with record() as rec:
        sh("cmd", 8001, Path("/tmp/x"), ["--flag", "value"], None)
    assert rec.commands == [["cmd", "8001", "/tmp/x", "--flag", "value"]]


def test_non_zero_raises_with_the_command_in_the_message():
    with record(failures={"false": 3}):
        with pytest.raises(CommandFailed) as caught:
            sh("false")
    assert caught.value.returncode == 3
    assert "false" in caught.value.message


def test_check_false_returns_the_code():
    with record(failures={"false": 3}):
        assert sh("false", check=False).returncode == 3


def test_out_captures_and_strips():
    with record(responses={"git rev-parse": "abc123\n"}):
        assert sh.out("git", "rev-parse", "HEAD") == "abc123"


def test_ok_never_raises():
    with record(failures={"missing-thing": 1}):
        assert sh.ok("missing-thing") is False
        assert sh.ok("present-thing") is True


def test_dry_run_executes_nothing():
    with context(dry_run=True), record() as rec:
        sh("rm", "-rf", "/important")
    assert rec.commands == []


def test_dry_run_out_uses_the_stand_in():
    with context(dry_run=True), record(responses={"git": "real"}):
        assert sh.out("git", "rev-parse", "HEAD", dry="deadbeef") == "deadbeef"


def test_require_lists_every_missing_tool_at_once():
    with record(missing_tools=["fastlane", "adb"]):
        with pytest.raises(ToolMissing) as caught:
            sh.require("git", "fastlane", "adb")
    assert "fastlane" in caught.value.message
    assert "adb" in caught.value.message


def test_missing_executable_is_a_tool_error_not_a_traceback(monkeypatch):
    import subprocess

    def boom(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(ToolMissing, match="not on PATH"):
        sh("definitely-not-a-real-binary")


def test_background_hold_stdin_keeps_a_watcher_alive():
    """`cat` exits on stdin EOF -- exactly like tailwindcss --watch."""
    import time

    with context():
        plain = sh.background("cat", log="/dev/null")
        held = sh.background("cat", log="/dev/null", hold_stdin=True)
    assert plain is not None and held is not None
    try:
        deadline = time.monotonic() + 5
        while plain.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert plain.poll() is not None  # DEVNULL stdin: immediate EOF, cat exits
        time.sleep(0.2)
        assert held.poll() is None  # held pipe: no EOF, cat stays up
    finally:
        held.kill()
        if plain.poll() is None:
            plain.kill()


def test_context_env_reaches_the_child():
    captured = {}

    import subprocess

    real = subprocess.run

    def spy(argv, *args, **kwargs):
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(argv, 0, "", "")

    subprocess.run = spy
    try:
        with context() as active:
            from make.context import set_context

            set_context(active.with_(env={"TOKEN": "s3cret"}))
            sh("printenv")
    finally:
        subprocess.run = real

    assert captured["env"]["TOKEN"] == "s3cret"
