"""optersoft's shared recipes for `make` -- the successor to github.com/optersoft/just.

Import only the groups a repo uses; importing is what registers them, so
`make --list` never depends on what happens to be installed:

    # Makefile.py
    from make_recipes_optersoft import box, web

    web.Web.configure(bin="alma", port=8005, serve_dir="alma-server")

Each group declares what it needs from the consumer as a typed config section
(`box.Box`, `web.Web`, `android.Android`, `play.Play`, `agent.Agent`), so a
missing value fails with the field, its type, and where to set it -- rather than
the `just` convention of withholding defaults so that a missing variable at
least produces a parse error.

Migration notes carried over deliberately:

* `HIVE_BOX_*` and `~/.ssh/hive-box-<login>` keep their names. They are
  operational contracts read by the `hetzner-box` CLI and present on every VM;
  renaming them breaks box access with nothing to catch it.
* `~/.just/secrets.env` and `~/.just/<repo>.env` are still read, after
  `~/.make/`, so nothing has to move on day one.
* The Play `test-gate` is `abstract=True` rather than undefined -- it lists as
  unimplemented and refuses to run with instructions.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["agent", "android", "box", "database", "play", "secure", "web"]


def __getattr__(name: str):
    """Import a group on first access, so `from ... import web` costs only web.

    Startup latency is a feature of the tool this plugs into: a repo that uses
    two groups should not pay for the other five.
    """
    if name in __all__:
        import importlib

        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
