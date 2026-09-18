"""Every page of make.optersoft.com: the head a crawler reads, and the chrome a reader sees.

The document itself — `<html>`, the common head, the icons, the pre-paint theme script — is
`optersoft_brand`'s, the same chrome optersoft.com and frontage.optersoft.com wear. What is
here is only what this site fills in: its brand, its five links, and the canonical.

⚠ The header links are in-page anchors plus two outbound ones, because this is one page. A
second page would make them paths; until then an anchor is the honest target.
"""

import optersoft_brand as brand

BASE = "https://make.optersoft.com"

#: A literal path, never a hashed asset. A content hash makes a stylesheet cacheable forever
#: and a favicon invisible: Google re-crawls an icon on its own slow schedule and treats a
#: moved one as a new one to re-evaluate.
ICONS = [("/favicon.svg", "icon", "image/svg+xml", None)]

BRAND = {"href": "/", "name": "mk", "suffix": "_"}

LINKS = [
    brand.link("/#what", "What it is"),
    brand.link("/#worth", "Why"),
    brand.link("/#start", "Get started"),
    brand.link("https://academy.optersoft.com/project/make", "Tutorial"),
    brand.link("https://github.com/optersoft/make", "GitHub"),
]


def page(children, title, description, canonical=None, noindex=False):
    """A whole page: the head, then the chrome around the body."""
    return [
        brand.head(
            title,
            description,
            canonical=canonical,
            noindex=noindex,
            site="mkrun",
            locale="en_US",
            icons=ICONS,
        ),
        # `letter` puts the Optersoft mark in the footer as the O of the company's name
        # (2026-09-17): the company row is the wordmark, not an icon beside a word.
        brand.shell(children, brand=BRAND, links=LINKS, letter=brand.LETTER),
    ]
