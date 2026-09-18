"""Test setup for make-marketplace.

Importing the group is what registers it, and a module is imported once per
process -- so the registry is populated here, once, rather than relying on
whichever test file happened to import first.
"""

from __future__ import annotations

import pytest

from make import config
from make.tasks import registry


@pytest.fixture(scope="session", autouse=True)
def register_groups():
    from make_marketplace import marketplace  # noqa: F401

    registry.finalize()


@pytest.fixture(autouse=True)
def fresh_config():
    config.reset_cache()
    yield
    config.reset_cache()
