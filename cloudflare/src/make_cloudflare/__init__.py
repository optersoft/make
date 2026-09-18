"""Cloudflare tasks for mkrun -- the `cloudflare` group.

Today it is Pages direct upload: `cloudflare.deploy` publishes a built
directory, `cloudflare.projects` lists what the account has, and
`cloudflare.deployments` shows a project's recent ones. No Node, no wrangler --
four HTTPS calls and a BLAKE3 hash.

Importing is what registers it:

    # Makefile.py
    from make_cloudflare import cloudflare

The group merges with a repo's own `cloudflare.*` tasks -- groups are
namespaces, and this package claims only the three names above.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["cloudflare", "pages"]


def __getattr__(name: str):
    """Import on first access, keeping module scope import-free.

    The same shape as the other task packages: startup latency is a feature of
    the tool this plugs into, so nothing heavy -- `blake3`, here -- may load
    before it is asked for.
    """
    if name in __all__:
        import importlib

        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
