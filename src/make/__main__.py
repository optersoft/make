"""`python -m make` -- also how the uv bootstrap re-enters the tool."""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
