"""VS Code Marketplace tasks for mkrun -- the `marketplace` group.

Build an extension's `.vsix`, see what is already published, ask CI whether its
token can publish at all, and cut a release: version, changelog, commit, tag,
push. The publish itself belongs to a tag-triggered CI job, so nothing here --
and no laptop -- can ever reach the Marketplace directly.

Importing is what registers it:

    # Makefile.py
    from make_marketplace import marketplace

    marketplace.Marketplace.configure(repo="owner/name")

The group merges with a repo's own `marketplace.*` tasks -- groups are
namespaces -- which is how a consumer fills the `gate` and `preflight` hooks.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["gallery", "marketplace"]


def __getattr__(name: str):
    """Import on first access, keeping module scope import-free.

    The same shape as the other task packages: startup latency is a feature of
    the tool this plugs into, so a repo that only builds a `.vsix` should not
    pay for the release machinery.
    """
    if name in __all__:
        import importlib

        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
