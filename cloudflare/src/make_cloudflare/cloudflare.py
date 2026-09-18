"""The `cloudflare` group: deploy to Pages, and see what is there.

Nothing here knows whose account it runs in. The project name comes from the
task line or from `CLOUDFLARE_PAGES_PROJECT` in the repo's env layer, and the
credentials come from `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN` -- the
same two names wrangler reads, so a repository that already deploys through CI
needs no new secret.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from make import arg, note, step, task

from . import pages


@task(group="cloudflare", name="deploy", dangerous=True, secrets=["CLOUDFLARE_API_TOKEN"])
def deploy(
    directory: str = "dist",
    *,
    project: str | None = None,
    branch: Annotated[str, arg(help="the Pages branch; the production one publishes live")] = "main",
) -> None:
    """Publish a built directory to Cloudflare Pages by direct upload.

    No Node and no wrangler: four HTTPS calls. `--branch` is what decides
    production -- the project's production branch goes live, every other name
    lands as a preview on its own URL.
    """
    url = pages.deploy(Path(directory), project=pages.project_name(project), branch=branch)
    note(f"deployed -- {url}")


@task(group="cloudflare", name="projects", secrets=["CLOUDFLARE_API_TOKEN"])
def projects() -> None:
    """List the account's Pages projects, and where each one deploys from."""
    for project in pages.projects():
        source = (project.get("source") or {}).get("type") or "direct upload"
        domains = ", ".join(project.get("domains") or []) or "--"
        step(f"{project.get('name', '?')}  [{source}]  {domains}")


@task(group="cloudflare", name="deployments", secrets=["CLOUDFLARE_API_TOKEN"])
def deployments(project: str | None = None, *, limit: int = 10) -> None:
    """The most recent deployments of one project, newest first."""
    for entry in pages.deployments(pages.project_name(project))[:limit]:
        stage = (entry.get("latest_stage") or {}).get("status", "?")
        env_name = entry.get("environment", "?")
        step(f"{entry.get('created_on', '?')}  {env_name:<10} {stage:<10} {entry.get('url', '')}")
