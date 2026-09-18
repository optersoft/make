"""The built site, checked the way a reader and a crawler meet it.

The fixture builds the whole thing once -- it takes under a second -- so every assertion
here is about real output rather than about a component in isolation.
"""

from __future__ import annotations

import base64
import hashlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def dist(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("dist")
    subprocess.run(
        [sys.executable, "-m", "frontage", "site", str(ROOT), "--out", str(out), "--tailwind"],
        check=True,
        capture_output=True,
    )
    return out


@pytest.fixture(scope="session")
def index(dist: Path) -> str:
    return (dist / "index.html").read_text()


def test_the_site_is_one_page_and_a_404(dist: Path):
    assert (dist / "index.html").is_file()
    assert (dist / "404.html").is_file()


def test_no_javascript_is_fetched(index: str):
    """Nothing here is interactive, so no page may carry a `<script src>`.

    The two inline scripts are the chrome's theme, which cannot be anything else.
    """
    assert "<script src" not in index
    assert index.count("<script>") == 2


def test_the_page_says_what_it_is(index: str):
    assert "<title>mkrun — a command runner whose tasks are Python</title>" in index
    assert '<link rel="canonical" href="https://make.optersoft.com/">' in index
    assert '<meta name="robots" content="index, follow">' in index


def test_the_404_is_noindex_and_has_no_canonical(dist: Path):
    page = (dist / "404.html").read_text()
    assert '<meta name="robots" content="noindex, follow">' in page
    assert 'rel="canonical"' not in page


@pytest.mark.parametrize(
    "target",
    [
        "https://pypi.org/project/mkrun/",
        "https://github.com/optersoft/make",
        "https://academy.optersoft.com/project/make",
        "https://github.com/optersoft/make/blob/main/docs/design.md",
    ],
)
def test_the_page_still_carries_its_outbound_links(index: str, target: str):
    assert target in index


def test_every_section_the_header_points_at_exists(index: str):
    for anchor in ("what", "worth", "start"):
        assert f'id="{anchor}"' in index


def test_the_csp_hash_covers_every_inline_script(dist: Path, index: str):
    """The chrome's two inline scripts, allowed by hash in `public/_headers`.

    A chrome change rewrites those scripts, and a stale hash does not break the build or
    the deploy: the browser silently refuses to run them, the theme toggle stops working
    and the page flashes the wrong colour scheme. This is the only thing that notices.
    """
    headers = (dist / "_headers").read_text()
    for script in re.findall(r"<script>(.*?)</script>", index, re.S):
        digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
        assert f"'sha256-{digest}'" in headers, "stale CSP hash -- run `mk site.csp`"


def test_the_stylesheet_is_this_origin_only(index: str):
    assert 'href="/tailwind.out.css"' in index
    assert "cdn." not in index
