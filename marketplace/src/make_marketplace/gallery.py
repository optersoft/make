"""What the Marketplace, `vsce` and GitHub know, as plain functions.

The group in `marketplace.py` is the command surface; this is everything it has
to be right about. All of it was learned the expensive way shipping
`optersoft.kotlin-toolchain` in September 2026, and each function carries the
failure it exists to prevent.

The three that are not obvious:

* **A published version is permanent.** It cannot be replaced, withdrawn or
  re-uploaded -- only superseded by a higher one. `published_versions` is one
  HTTP call against the public gallery API, which is cheap insurance against
  learning that from a failing publish job.
* **A present token and a working token are different things.** A PAT that
  authenticates but is not permitted fails with `Access Denied ... Publish new
  extensions to an existing publisher` -- narrow scopes, or an identity that is
  not a member of the publisher. Checking that the secret *exists* passed three
  times in a row while every publish failed.
* **An environment secret is invisible to `gh secret list --repo`.** It reports
  an empty list, which reads exactly like "no token configured". `secret_names`
  therefore asks both places.
"""

from __future__ import annotations

import datetime
import json
import re
import time
from pathlib import Path

from make import http, sh
from make.errors import MakeError

__all__ = [
    "SEMVER",
    "extensions",
    "resolve",
    "manifest",
    "full_name",
    "package",
    "published_versions",
    "roll_changelog",
    "secret_names",
    "token_can_publish",
    "git",
]

#: A release is MAJOR.MINOR.PATCH, optionally pre-release. The Marketplace
#: accepts only semver, and refuses the four-part Windows-style version that
#: looks reasonable until the publish job rejects it.
SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")

#: The public gallery. Undocumented in the sense that nobody promises it, but it
#: is what the Marketplace website itself queries.
GALLERY = "https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery"


def extensions(root: Path) -> list[str]:
    """Every extension directory under `root`, by name.

    An extension is any directory holding a `package.json` with a `publisher`.
    Discovering them rather than listing them means adding the second extension
    to a repo is one `mkdir` -- and it is why a repo that keeps its extension in
    `editors/` configures `root` there rather than naming the directory twice.
    """
    found = []
    for candidate in sorted(root.glob("*/package.json")):
        try:
            if "publisher" in json.loads(candidate.read_text()):
                found.append(candidate.parent.name)
        except json.JSONDecodeError:
            continue
    return found


def resolve(ext: str, root: Path) -> list[Path]:
    """The directories a task should act on: one named extension, or all of them."""
    names = extensions(root)
    if not ext:
        if not names:
            raise MakeError(
                f"no extension under {root}",
                hint="an extension is a directory with a package.json that has a `publisher`",
            )
        return [root / name for name in names]
    if ext not in names:
        raise MakeError(f"no extension {ext!r}; have: {', '.join(names) or 'none'}")
    return [root / ext]


def manifest(target: Path) -> dict:
    """An extension's `package.json`."""
    path = target / "package.json"
    if not path.is_file():
        raise MakeError(f"{path} does not exist")
    return json.loads(path.read_text())


def full_name(data: dict) -> str:
    """`publisher.name` -- how the Marketplace identifies an extension."""
    return f"{data['publisher']}.{data['name']}"


def package(target: Path) -> Path | None:
    """Build the `.vsix`, sweeping stale ones first. Returns what was built.

    `--no-dependencies` because these extensions vendor nothing: the runtime is
    Node plus the `vscode` module, so there is no `node_modules` to bundle.
    `--no-git-tag-version` because the tag is the release task's job and is
    pushed only once everything else has passed.
    """
    for stale in target.glob("*.vsix"):
        stale.unlink()
    sh(
        "npx",
        "--yes",
        "@vscode/vsce",
        "package",
        "--no-git-tag-version",
        "--no-dependencies",
        "--allow-missing-repository",
        cwd=target,
    )
    built = sorted(target.glob("*.vsix"))
    return built[-1] if built else None


def published_versions(name: str) -> set[str]:
    """Every version of `publisher.name` already on the Marketplace.

    Not being able to ask is not a reason to refuse a release: the publish job
    compares the tag against the manifest, and the Marketplace rejects a
    duplicate itself. So a failed query is an empty set, not an error.
    """
    body = json.dumps(
        {"filters": [{"criteria": [{"filterType": 7, "value": name}], "pageSize": 1}], "flags": 0x1 | 0x10}
    )
    answer = http.request(
        GALLERY,
        method="POST",
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json;api-version=7.1-preview.1"},
        dry_body="{}",
    )
    if not answer.ok:
        return set()
    try:
        payload = json.loads(answer.body or "{}")
    except json.JSONDecodeError:
        return set()
    results = payload.get("results") or [{}]
    found = results[0].get("extensions") or []
    if not found:
        return set()
    return {version.get("version") for version in found[0].get("versions", [])}


def roll_changelog(path: Path, version: str) -> bool:
    """Turn `## [Unreleased]` into this version, dated today.

    False when there is nothing to roll -- worth refusing on, because a release
    whose changelog says nothing publishes an empty Changelog tab.
    """
    if not path.exists():
        return False
    text = path.read_text()
    if "## [Unreleased]" not in text:
        return False
    today = datetime.date.today().isoformat()
    path.write_text(text.replace("## [Unreleased]", f"## [{version}] - {today}", 1))
    return True


def secret_names(repo: str, environment: str = "") -> str:
    """The secrets `repo` has, repository-scoped AND on `environment`.

    ⚠ Both, always. A publish job that declares `environment: marketplace` reads
    a secret that `gh secret list --repo` does not show at all -- it prints an
    empty list, which reads exactly like "no token configured" and once cost a
    release that had its token all along.

    Returned as one blob to grep, because the only question asked of it is
    whether a name is somewhere in there.
    """
    names = _gh("secret", "list", "--repo", repo)
    if environment:
        names += _gh("api", f"repos/{repo}/environments/{environment}/secrets", "--jq", ".secrets[].name")
    return names


def token_can_publish(repo: str, workflow: str, timeout: int = 300) -> str | None:
    """Ask CI whether the Marketplace token can actually publish.

    The token lives in CI and nowhere else -- that is the design, so that no
    laptop can publish -- which makes CI the only place this question can be
    answered. Dispatches `workflow`, waits for it, and returns the failing run's
    URL.

    None means the token is fine, or that the workflow could not be dispatched
    at all (it is not on the default branch yet, say). The second case is a
    reason to warn rather than to block a release on our own tooling being
    absent, and the caller decides which.
    """
    started = datetime.datetime.now(datetime.UTC)
    if not sh.ok("gh", "workflow", "run", workflow, "--repo", repo, echo_cmd=False):
        return None

    # The run does not exist the instant `workflow run` returns, and taking "the
    # latest run" blind would happily report on the PREVIOUS dispatch. Match on
    # created-after instead.
    deadline = time.monotonic() + timeout
    run: dict = {}
    while time.monotonic() < deadline:
        time.sleep(4)
        listed = _gh(
            "run",
            "list",
            "--workflow",
            workflow,
            "--repo",
            repo,
            "--limit",
            "5",
            "--json",
            "databaseId,status,conclusion,createdAt,url",
        )
        try:
            candidates = json.loads(listed or "[]")
        except json.JSONDecodeError:
            continue
        for candidate in candidates:
            created = datetime.datetime.fromisoformat(candidate["createdAt"].replace("Z", "+00:00"))
            if created >= started - datetime.timedelta(seconds=30):
                run = candidate
                break
        if run and run["status"] == "completed":
            return None if run["conclusion"] == "success" else run["url"]

    return run.get("url") if run else None


def git(*args: str, cwd: Path | None = None) -> str:
    """Run git for its output, with none of it echoed as a task step.

    A preflight asks git a dozen questions and answers none of them to the
    reader; echoing each one buries the single line that matters.
    """
    return sh.out("git", *args, cwd=cwd, check=False, echo_cmd=False, dry="")


def _gh(*args: str) -> str:
    """`gh`, for its stdout, tolerating failure -- an absent secret is an answer."""
    return sh.out("gh", *args, check=False, echo_cmd=False, dry="")
