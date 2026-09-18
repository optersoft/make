"""`404.html` — what Cloudflare Pages serves, with a 404 status, for every unmatched path.

`noindex`, and no canonical: it is not a page anyone should reach on purpose.
"""

from components import not_found
from layouts.site import page as document

PATH = "/404.html"


def page():
    return document(not_found.body(), not_found.TITLE, not_found.DESCRIPTION, noindex=True)
