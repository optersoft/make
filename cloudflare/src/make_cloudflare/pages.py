"""Cloudflare Pages direct upload, in Python.

`wrangler pages deploy` is four HTTPS calls and a hash. This is those four
calls, so a repository with no Node -- a Rust one, a frontage one -- can publish
a built directory without installing a JavaScript toolchain to do it.

    from make_cloudflare import pages

    pages.deploy("site/dist", project="mkrun")            # production
    pages.deploy("site/dist", project="mkrun", branch="x")  # a preview

The flow, and the three things a naive port gets wrong:

1. `POST /accounts/<acct>/pages/projects/<p>/upload-token` -> a short-lived JWT.
2. `POST /pages/assets/check-missing` (JWT) -> the hashes not already stored.
   Note the missing `/accounts/<acct>` prefix on this call and the next: the
   JWT carries the account, and adding the prefix 404s.
3. `POST /pages/assets/upload` (JWT), then `upsert-hashes` -- in batches.
4. `POST /accounts/<acct>/pages/projects/<p>/deployments` -> the manifest.

**The hash is BLAKE3 of a strange input**: the base64 *text* of the contents
with the extension (no dot) appended, first 32 hex characters. Not of the bytes,
not SHA-256. Get it wrong and every call still succeeds while the deployment
serves nothing.

**`_headers`, `_redirects` and `_routes.json` are not assets.** They are form
fields on the deployment, and a port that walks them in as ordinary files
reports success while the site's CSP quietly stops applying.

**Steps 2 and 3 are undocumented.** They exist because wrangler uses them;
Cloudflare can change them without a changelog. The documented `deployments`
endpoint alone cannot upload a file.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import secrets as _random
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from make import env, http, note, step
from make.context import mark_sensitive
from make.errors import ConfigError, MakeError

__all__ = [
    "Asset",
    "collect",
    "deploy",
    "deployments",
    "file_hash",
    "manifest_of",
    "project_name",
    "projects",
]

API = "https://api.cloudflare.com/client/v4"

#: Pages' own limits. Both are refused by the API anyway; refusing here names
#: the offending file instead of failing the batch that happened to carry it.
MAX_ASSET_SIZE = 25 * 1024 * 1024
MAX_ASSET_COUNT = 20_000

#: One upload request. Cloudflare accepts more; these keep a single request
#: small enough to retry cheaply on a bad connection, which matters more than
#: shaving round trips off a deploy that happens by hand.
BATCH_FILES = 100
BATCH_BYTES = 10 * 1024 * 1024

#: Handled by Pages itself, as fields on the deployment -- never as assets.
SPECIAL_FILES = ("_headers", "_redirects", "_routes.json")

#: Never uploaded. `_worker.js` and `functions/` are Pages Functions, which this
#: module does not build: a directory that has them is a Functions project and
#: belongs on wrangler until someone needs it here.
SKIP_NAMES = {".DS_Store", "Thumbs.db", ".gitkeep", *SPECIAL_FILES}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".wrangler"}
FUNCTIONS = ("_worker.js", "_worker.js.map", "_worker.bundle", "functions")


@dataclass(frozen=True)
class Asset:
    """One file, as the manifest and the upload endpoint each want it."""

    key: str
    """Manifest path, with the leading slash Cloudflare requires."""
    file: Path
    hash: str
    size: int
    content_type: str


# -- hashing ---------------------------------------------------------------


def file_hash(data: bytes, extension: str = "") -> str:
    """Cloudflare's asset key: `blake3(base64(data) + ext)` , first 32 hex chars.

    `extension` is the suffix without its dot, and the empty string for a file
    that has none. The base64 text is hashed, not the bytes -- that is not a
    mistake in the reading of wrangler, it is what wrangler does, and the store
    is keyed on the result.
    """
    from blake3 import blake3

    payload = base64.b64encode(data) + extension.encode()
    return blake3(payload).hexdigest()[:32]


# -- walking a built directory --------------------------------------------


def _walk(directory: Path) -> Iterator[Path]:
    for parent, dirs, names in os.walk(directory):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(names):
            if name not in SKIP_NAMES:
                yield Path(parent) / name


def collect(directory: str | Path) -> tuple[list[Asset], dict[str, str]]:
    """Every uploadable file under `directory`, plus the special files' contents.

    Returns `(assets, specials)`, where `specials` maps `_headers` /
    `_redirects` / `_routes.json` to their text. They are deliberately not
    assets: Pages takes them as fields on the deployment.
    """
    root = Path(directory).resolve()
    if not root.is_dir():
        raise MakeError(f"{root} is not a directory", hint="build the site first")

    for name in FUNCTIONS:
        if (root / name).exists():
            raise MakeError(
                f"{root / name} is a Pages Functions project",
                hint="this module uploads static assets only -- deploy it with wrangler",
            )

    assets: list[Asset] = []
    for file in _walk(root):
        data = file.read_bytes()
        if len(data) > MAX_ASSET_SIZE:
            raise MakeError(
                f"{file.relative_to(root)} is {len(data) / 1_048_576:.1f} MiB",
                hint=f"Cloudflare Pages refuses any asset over {MAX_ASSET_SIZE // 1_048_576} MiB",
            )
        guessed, _ = mimetypes.guess_type(file.name)
        assets.append(
            Asset(
                key="/" + file.relative_to(root).as_posix(),
                file=file,
                hash=file_hash(data, file.suffix.lstrip(".")),
                size=len(data),
                content_type=guessed or "application/octet-stream",
            )
        )

    if not assets:
        raise MakeError(f"{root} holds no files to upload")
    if len(assets) > MAX_ASSET_COUNT:
        raise MakeError(
            f"{len(assets)} files under {root}",
            hint=f"Cloudflare Pages takes at most {MAX_ASSET_COUNT} per deployment",
        )

    specials = {name: (root / name).read_text() for name in SPECIAL_FILES if (root / name).is_file()}
    return assets, specials


def manifest_of(assets: Iterable[Asset]) -> dict[str, str]:
    """The deployment manifest: `/path` -> hash."""
    return {asset.key: asset.hash for asset in assets}


# -- the API ---------------------------------------------------------------


def _credentials(account: str | None, token: str | None) -> tuple[str, str]:
    # The layers first, as every task package that needs a credential does: without
    # this, `require` sees only the process environment, and a machine whose
    # `~/.make/secrets.env` already holds these two is told to set what it has set.
    # The secret itself is still withheld from child processes until asked for --
    # `layered()` holds sensitive values back by design.
    if account is None or token is None:
        env.layered()
    account = account or env.require(
        "CLOUDFLARE_ACCOUNT_ID", hint="the account id from any zone's overview page"
    )
    token = token or env.require(
        "CLOUDFLARE_API_TOKEN",
        hint="an API token with the `Cloudflare Pages: Edit` permission, "
        "from https://dash.cloudflare.com/profile/api-tokens",
    )
    mark_sensitive(token)
    return account, token


def _call(
    method: str,
    url: str,
    *,
    auth: str,
    body: bytes | None = None,
    content_type: str | None = None,
    timeout: float = 120.0,
) -> Any:
    """One API call, returning `result` -- a dict or a list, as the endpoint says.

    Under `--dry-run` nothing is sent and this returns `None`, which every
    caller reads as "unknown". A dry run therefore prints the whole plan
    instead of stopping at the first call whose answer it needed.
    """
    headers = {"Authorization": f"Bearer {auth}"}
    if content_type:
        headers["Content-Type"] = content_type
    answer = http.request(url, method=method, headers=headers, data=body, timeout=timeout)
    if answer.skipped:
        return None
    if answer.status == 0:
        raise MakeError(f"{method} {url}: no answer from Cloudflare")
    try:
        payload = json.loads(answer.body)
    except ValueError:
        payload = {}
    if not answer.ok or not payload.get("success", False):
        detail = "; ".join(f"{e.get('code', '?')}: {e.get('message', '')}" for e in payload.get("errors", []))
        # Cloudflare's own text goes in the message, not the hint: a 403 saying
        # which permission the token lacks is the thing to read, and a hint is
        # printed as advice rather than as the failure.
        raise MakeError(
            f"{method} {url} -> {answer.status}" + (f": {detail}" if detail else ""),
            hint=None if detail else (answer.body[:300] or None),
        )
    return payload.get("result")


def _json_call(method: str, url: str, *, auth: str, payload: object, **kwargs: Any) -> Any:
    return _call(
        method, url, auth=auth, body=json.dumps(payload).encode(), content_type="application/json", **kwargs
    )


def _multipart(fields: Mapping[str, str]) -> tuple[bytes, str]:
    """Encode plain text form fields. Pages wants the manifest as one of these."""
    boundary = "----mk" + _random.token_hex(16)
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        chunks.append(value.encode())
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _batches(assets: list[Asset]) -> Iterator[list[Asset]]:
    batch: list[Asset] = []
    total = 0
    for asset in assets:
        encoded = asset.size * 4 // 3
        if batch and (len(batch) >= BATCH_FILES or total + encoded > BATCH_BYTES):
            yield batch
            batch, total = [], 0
        batch.append(asset)
        total += encoded
    if batch:
        yield batch


# -- the tasks' behaviour --------------------------------------------------


def deploy(
    directory: str | Path,
    *,
    project: str,
    branch: str = "main",
    account: str | None = None,
    token: str | None = None,
) -> str:
    """Publish a built directory and return the deployment's URL.

    `branch` is what decides production: Pages treats the project's production
    branch (`main` for every project here) as the live deployment and every
    other name as a preview on its own URL. There is no `--production` flag to
    forget; there is a branch name to get right.
    """
    account, token = _credentials(account, token)
    assets, specials = collect(directory)
    plural = "" if len(assets) == 1 else "s"
    step(f"{len(assets)} file{plural}, {sum(a.size for a in assets) / 1024:.0f} KiB -> {project} ({branch})")

    granted = _call("GET", f"{API}/accounts/{account}/pages/projects/{project}/upload-token", auth=token)
    jwt = str(granted.get("jwt", "")) if isinstance(granted, dict) else ""
    if jwt:
        mark_sensitive(jwt)
    # The JWT is what the two asset endpoints want; the account token is not
    # accepted there. Falling back to it keeps a dry run legible rather than
    # correct -- there is nothing to authenticate when nothing is sent.
    upload_auth = jwt or token

    # `check-missing` answers with the hashes Cloudflare does NOT already hold:
    # a repeat deploy of an unchanged site uploads nothing. `None` is a dry run,
    # where the honest plan is "all of them".
    missing = _json_call(
        "POST",
        f"{API}/pages/assets/check-missing",
        auth=upload_auth,
        payload={"hashes": [a.hash for a in assets]},
    )
    pending = assets if missing is None else [a for a in assets if a.hash in set(missing)]

    if pending:
        for batch in _batches(pending):
            _json_call(
                "POST",
                f"{API}/pages/assets/upload",
                auth=upload_auth,
                payload=[
                    {
                        "key": asset.hash,
                        "value": base64.b64encode(asset.file.read_bytes()).decode(),
                        "metadata": {"contentType": asset.content_type},
                        "base64": True,
                    }
                    for asset in batch
                ],
            )
        if len(pending) == len(assets):
            note(f"uploading all {len(assets)} file{plural}")
        else:
            note(f"uploading {len(pending)} of {len(assets)}; Cloudflare already holds the rest")
    else:
        note("every file was already stored; only the manifest changes")

    _json_call(
        "POST",
        f"{API}/pages/assets/upsert-hashes",
        auth=upload_auth,
        payload={"hashes": [a.hash for a in assets]},
    )

    fields = {"manifest": json.dumps(manifest_of(assets)), "branch": branch}
    fields.update(specials)
    body, content_type = _multipart(fields)
    created = _call(
        "POST",
        f"{API}/accounts/{account}/pages/projects/{project}/deployments",
        auth=token,
        body=body,
        content_type=content_type,
    )
    if specials:
        note("as deployment fields, not assets: " + ", ".join(sorted(specials)))
    url = created.get("url") if isinstance(created, dict) else None
    return str(url or f"https://{branch}.{project}.pages.dev")


def projects(*, account: str | None = None, token: str | None = None) -> list[dict]:
    """Every Pages project on the account."""
    account, token = _credentials(account, token)
    result = _call("GET", f"{API}/accounts/{account}/pages/projects", auth=token)
    return list(result) if isinstance(result, list) else []


def deployments(project: str, *, account: str | None = None, token: str | None = None) -> list[dict]:
    """A project's deployments, newest first."""
    account, token = _credentials(account, token)
    result = _call("GET", f"{API}/accounts/{account}/pages/projects/{project}/deployments", auth=token)
    return list(result) if isinstance(result, list) else []


def project_name(project: str | None) -> str:
    """The project to act on: the one given, else the repo's env layer."""
    if project:
        return project
    found = env.get("CLOUDFLARE_PAGES_PROJECT")
    if found:
        return found
    raise ConfigError(
        "no Pages project given",
        hint="pass --project, or set CLOUDFLARE_PAGES_PROJECT in this repo's env layer",
    )
