"""Test setup for make-rust.

Importing the group is what registers it, and a module is imported once per
process -- so the registry is populated here, once, rather than relying on
whichever test file happened to import first.
"""

from __future__ import annotations

import pytest

from make.tasks import registry


@pytest.fixture(scope="session", autouse=True)
def register_groups():
    from make_rust import rust  # noqa: F401

    registry.finalize()
