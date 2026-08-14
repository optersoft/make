"""Reading Google Play reviews through the androidpublisher API.

Ported from `play/reviews.py`, which was a standalone PEP 723 uv script invoked
by a recipe that injected `PKG` and `PLAY_JSON` through the environment. As a
module it is importable, testable, and returns data instead of printing it --
the recipe does the formatting.

`google-auth` is an optional dependency (`make-recipes-optersoft[play]`), so
importing this module is deferred to the one recipe that needs it.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from make.errors import MakeError

SCOPE = "https://www.googleapis.com/auth/androidpublisher"
ENDPOINT = "https://androidpublisher.googleapis.com/androidpublisher/v3/applications"


def _token(service_account_json: str) -> str:
    try:
        import google.auth.transport.requests as requests_transport
        from google.oauth2 import service_account
    except ImportError as exc:  # pragma: no cover - depends on the extra
        raise MakeError(
            "reading Play reviews needs google-auth",
            hint='install the extra: `uv add "make-recipes-optersoft[play]"`',
        ) from exc

    credentials = service_account.Credentials.from_service_account_file(service_account_json, scopes=[SCOPE])
    credentials.refresh(requests_transport.Request())
    return str(credentials.token)


def fetch(package: str, service_account_json: str, maximum: int = 20) -> list[dict[str, Any]]:
    """Recent reviews, newest first. Play exposes roughly the last week."""
    token = _token(service_account_json)
    url = f"{ENDPOINT}/{package}/reviews?maxResults={maximum}&translationLanguage=en"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise MakeError(f"Play API error {exc.code}: {detail}") from exc

    return [_normalise(review) for review in payload.get("reviews", [])]


def _normalise(review: dict[str, Any]) -> dict[str, Any]:
    comments = review.get("comments") or [{}]
    comment = comments[0].get("userComment", {}) or {}
    stars = comment.get("starRating", 0)
    device = comment.get("device") or (comment.get("deviceMetadata") or {}).get("productName") or ""
    return {
        "stars": int(stars) if isinstance(stars, int) else 0,
        "author": (review.get("authorName") or device or "anonymous")[:24],
        "when": (comment.get("lastModified") or {}).get("seconds", ""),
        "device": device,
        "text": " ".join((comment.get("text") or "").split()),
    }
