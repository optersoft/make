"""Test setup for the optersoft recipe package.

Importing a group is what registers it, and a module is imported once per
process -- so the registry is populated here, once, rather than relying on
whichever test file happened to import first.
"""

from __future__ import annotations

import pytest

from make import config
from make.recipes import registry


@pytest.fixture(scope="session", autouse=True)
def register_groups():
    """Import every group, so `registry.require(...)` can find them."""
    from make_recipes_optersoft import agent, android, box, database, play, secure, web  # noqa: F401

    registry.finalize()


@pytest.fixture(autouse=True)
def fresh_config():
    config.reset_cache()
    yield
    config.reset_cache()
