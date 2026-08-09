from __future__ import annotations

from pathlib import Path


def write(path: Path, text: str) -> Path:
    """Create a file (and its parents), stripping the leading newline of a literal."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.lstrip("\n"), encoding="utf-8")
    return path
