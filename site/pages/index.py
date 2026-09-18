"""`/` — the whole site, bar the 404."""

from components import landing
from layouts.site import BASE
from layouts.site import page as document


def page():
    return document(landing.body(), landing.TITLE, landing.DESCRIPTION, canonical=f"{BASE}/")
