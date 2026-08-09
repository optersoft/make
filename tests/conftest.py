from __future__ import annotations

import os
from pathlib import Path

import pytest

from make import config
from make.recipes import Registry
from make.recipes import registry as global_registry


@pytest.fixture(autouse=True)
def clean_registry():
    """Each test starts with an empty registry, and gives back what was there.

    Registration happens at import time and a module is imported once per
    process, so clearing without restoring would unregister every recipe that
    any other test file imported -- permanently, for the rest of the session.
    """
    saved = global_registry.snapshot()
    global_registry.clear()
    config.reset_cache()
    yield
    global_registry.restore(saved)
    config.reset_cache()


@pytest.fixture
def registry() -> Registry:
    return Registry()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty directory that is the cwd for the duration of the test."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NO_COLOR", "1")
    for key in list(os.environ):
        if key.startswith("MAKE_"):
            monkeypatch.delenv(key, raising=False)
    return tmp_path
