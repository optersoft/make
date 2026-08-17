"""Generic cargo hygiene for mkrun -- the `rust` group.

One group, three tasks: `usage` (every target/ dir, size + idle age), `clean`
(delete them, optionally only the idle ones) and `sweep` (cargo-sweep, which
trims stale artifacts and keeps the hot incremental state).

Importing is what registers it:

    # Makefile.py
    from make_rust import rust

The group merges with a repo's own `rust.*` tasks -- groups are namespaces, and
this package claims only `usage`, `clean` and `sweep`.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["rust"]


def __getattr__(name: str):
    """Import the group on first access, keeping module scope import-free.

    The same shape as the other task packages: startup latency is a feature of
    the tool this plugs into, so nothing heavy may load before it is asked for.
    """
    if name in __all__:
        import importlib

        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
