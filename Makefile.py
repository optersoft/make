"""Tasks for developing `make` itself -- and the first thing that dogfoods it.

    mk dev.check      lint + both test suites: everything CI runs
    mk dev.bench      startup latency against the 150ms budget
    mk dist.release   tag a version, which publishes to PyPI from CI
    mk site.dev       the landing page in site/ (frontage, on the Optersoft chrome), locally
    mk site.build     site/dist
    mk site.deploy    publish site/dist to Cloudflare Pages (production), via make-cloudflare

Run `mk` with no arguments to see them all.
"""

from __future__ import annotations

from pathlib import Path

from make import note, sh, step, task, warn

# Every workspace member, listed as Python paths rather than as directories.
# Ruff formats Python code blocks inside markdown too, and handing it a whole
# directory reaches docs/ -- where examples are hand-packed to read as prose
# and reformatting them is a docs edit disguised as a lint fix.
SOURCES = [
    "src",
    "tests",
    "Makefile.py",
    "rust/src",
    "rust/tests",
    "cloudflare/src",
    "cloudflare/tests",
    "marketplace/src",
    "marketplace/tests",
]


@task(group="dev", requires=["uv"])
def sync() -> None:
    """Install every workspace member in editable mode.

    `--all-packages` is what reaches rust/, cloudflare/ and marketplace/; a plain `uv sync`
    installs the root project only, and then their tests fail on import rather
    than on anything real.

    Also removes a stray `make` distribution. The import name is `make` but the
    distribution is `mkrun`, so an environment carrying both -- easy to end up
    with after the rename, or by installing the unrelated PyPI `make` -- makes
    dependency checks pass locally that fail everywhere else. One did.
    """
    sh("uv", "sync", "--all-extras", "--all-packages")
    if _installed("make"):
        warn("removing a stray `make` distribution; this project's is `mkrun`")
        sh("uv", "pip", "uninstall", "make")


def _installed(distribution: str) -> bool:
    import importlib.metadata as metadata

    try:
        metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return False
    return True


@task(group="dev", requires=["uv"])
def test(*paths: str, verbose: bool = False) -> None:
    """Run the test suite.

    Args:
        verbose: show each test name
    """
    sh("uv", "run", "pytest", *(paths or ()), *(["-v"] if verbose else []))


@task(group="dev", requires=["uv"])
def lint(*, fix: bool = False) -> None:
    """Check formatting and lint rules.

    Args:
        fix: apply the fixes instead of only reporting them
    """
    sh("uv", "run", "ruff", "check", *(["--fix"] if fix else []), *SOURCES)
    sh("uv", "run", "ruff", "format", *([] if fix else ["--check"]), *SOURCES)


@task(group="dev", needs=[lint, test])
def check() -> None:
    """Everything CI runs."""
    note("lint and tests passed")


@task(group="dev")
def bench() -> None:
    """Measure startup latency -- the number that decides whether this gets used."""
    import statistics
    import subprocess
    import sys
    import time

    samples = []
    for _ in range(20):
        started = time.perf_counter()
        subprocess.run(
            [sys.executable, "-m", "make", "--list"],
            capture_output=True,
            check=True,
            cwd=Path(__file__).parent,
        )
        samples.append((time.perf_counter() - started) * 1000)

    best, median = min(samples), statistics.median(samples)
    step(f"startup: {best:.0f}ms best, {median:.0f}ms median (budget 150ms)")
    if median > 150:
        warn("over budget -- check for a heavy import at module scope")


@task(group="dist", requires=["uv"])
def build() -> None:
    """Build the wheel and sdist.

    `--package mkrun` names the package deliberately: a bare `uv build`
    happens to resolve to the root today, but `dist/` is uploaded wholesale,
    and the `rust/` workspace member releases on its own schedule, not mkrun's.
    """
    sh("uv", "build", "--package", "mkrun")


@task(group="dist", needs=[check], requires=["git"], dangerous=True)
def release(version: str) -> None:
    """Tag a release, which publishes to PyPI from CI.

    Uploading happens in GitHub Actions through PyPI Trusted Publishing, so no
    token exists on any laptop to leak. This only moves the tag.
    """
    from make import note

    sh("git", "tag", "-a", f"v{version}", "-m", f"mkrun {version}")
    sh("git", "push", "origin", f"v{version}")
    note(f"tagged v{version} -- watch the release workflow for the upload")


@task(group="dev")
def completions(shell: str = "zsh") -> None:
    """Print the completion script for a shell (bash, zsh, fish)."""
    from make.completions import emit

    print(emit(shell))


# The landing page in site/ -- a **frontage** static site on the Optersoft
# chrome (`optersoft_brand`, the sibling checkout at ../brand), published to the
# Cloudflare Pages project `mkrun` (make.optersoft.com) by direct upload.
#
# Astro until 2026-09-14, when it was rebuilt on frontage and the deploy moved
# from wrangler to `make-cloudflare` -- this repo's own workspace member, which
# is the point: the site of a Python task runner had a node_modules and shelled
# out to a JavaScript CLI to publish itself. There is no Node here any more.
SITE = "mkrun"
SITE_LIVE = "https://make.optersoft.com"
SITE_DIR = Path(__file__).resolve().parent / "site"

#: The chrome is a path dependency on the sibling checkout -- the fleet's convention,
#: so an edit there ships with the next deploy and there is nothing to bump. A host
#: that clones ONE repository has no sibling and cannot resolve it, so `build` fetches
#: it when it is absent. Public on GitHub for exactly this reason: no credentials.
BRAND = "https://github.com/optersoft/brand.git"


def _chrome() -> None:
    """Make `../brand` exist, so the path dependency resolves.

    A no-op on any machine with the fleet checked out side by side, which is every
    laptop and the CI job. Shallow, because the build reads the working tree and
    never the history.
    """
    beside = SITE_DIR.parent.parent / "brand"
    if beside.exists():
        return
    step(f"the chrome is not beside us -- cloning it into {beside}")
    sh("git", "clone", "--depth", "1", BRAND, str(beside))


@task(group="site", name="dev", requires=["uv"])
def site_dev(*args: str) -> None:
    """The site under `frontage serve`, with live reload (extra arguments forward to it).

    `--prerender` is what makes `serve` build the site the way `site.build` does rather
    than hand out files: a site has no file at a path until the build makes one. It
    detects a site by the presence of `pages/`, so there is nothing else to pass. It was
    `--site` until frontage 0.14 replaced the flag, which this repo's lock already
    resolves -- the old spelling made `serve` exit on its usage message.
    """
    _chrome()
    sh("uv", "run", "python", "-m", "frontage", "serve", ".", "--prerender", *args, cwd=SITE_DIR)


@task(group="site", name="check", requires=["uv"])
def site_check() -> None:
    """ruff, then the site's own tests -- which build it and read the output.

    They are cheap and load-bearing: the page fetches no JavaScript, the canonical
    and the robots meta are what they claim, the 404 is noindex, every outbound
    link the page promises is still in it, and -- the one nothing else notices --
    the CSP hashes in `public/_headers` still match the chrome's two inline
    scripts. A stale hash breaks neither the build nor the deploy; the browser
    just silently refuses to run the theme.
    """
    _chrome()
    sh("uv", "run", "ruff", "check", ".", cwd=SITE_DIR)
    sh("uv", "run", "ruff", "format", "--check", ".", cwd=SITE_DIR)
    sh("uv", "run", "pytest", "-q", cwd=SITE_DIR)


@task(group="site", name="build", requires=["uv", "git"])
def site_build() -> None:
    """Build site/ into site/dist -- two pages, no runtime, no node_modules."""
    _chrome()
    sh("uv", "run", "python", "-m", "frontage", "site", ".", "--out", "dist", "--tailwind", cwd=SITE_DIR)


@task(group="site", name="csp", needs=[site_build])
def site_csp() -> None:
    """Rewrite the CSP script hashes in site/public/_headers from the built page.

    The chrome's theme scripts are inline -- they have to be, or the page flashes
    the wrong colour scheme before it paints -- so the policy allows them by hash.
    Run this after a change in `../brand`, then rebuild.
    """
    import base64
    import hashlib
    import re

    from make import fs

    page = (SITE_DIR / "dist" / "index.html").read_text()
    scripts = re.findall(r"<script>(.*?)</script>", page, re.S)
    hashes = " ".join(
        f"'sha256-{base64.b64encode(hashlib.sha256(s.encode()).digest()).decode()}'" for s in scripts
    )
    headers = SITE_DIR / "public" / "_headers"
    text = headers.read_text()
    updated = re.sub(r"script-src 'self'[^;]*;", f"script-src 'self' {hashes};", text, count=1)
    if updated == text:
        note(f"{len(scripts)} inline script(s); the policy already matches")
        return
    fs.write(headers, updated)
    warn("the hashes changed -- rebuild before deploying, or the theme will be refused")


@task(group="site", name="deploy", needs=[site_build], dangerous=True, secrets=["CLOUDFLARE_API_TOKEN"])
def site_deploy(*, branch: str = "main") -> None:
    """Build, then publish site/dist to Cloudflare Pages as the production deployment.

    Dogfood: the publishing is `make-cloudflare`, this repository's own workspace
    member -- four HTTPS calls, no wrangler and no Node. `--branch main` is what
    makes it PRODUCTION; any other name lands as a preview on its own URL and
    never reaches the live one.
    """
    from make_cloudflare import pages

    url = pages.deploy(SITE_DIR / "dist", project=SITE, branch=branch)
    note(f"done -- {url}" + ("" if branch == "main" else f" (preview; live is {SITE_LIVE})"))


@task(group="site")
def smoke(*, base: str = SITE_LIVE) -> None:
    """Check the live page: it answers 200 and still carries every outbound link."""
    from make import http

    # Cloudflare's bot management answers 403 to urllib's default User-Agent,
    # so the page looks broken from a check that does not send a browser one.
    browser = {"User-Agent": "Mozilla/5.0 (mk site.smoke)"}

    for path, expected in ((f"{base}/", 200), (f"{base}/nowhere", 404)):
        got = http.get(path, headers=browser).status
        if got != expected:
            raise SystemExit(f"{path}: expected {expected}, got {got}")

    page = http.get(f"{base}/", headers=browser).body
    for link in ("pypi.org/project/mkrun", "github.com/optersoft/make", "academy.optersoft.com/project/make"):
        if link not in page:
            raise SystemExit(f"the live page no longer links to {link}")
    note(f"{base} is live, with all three links")
