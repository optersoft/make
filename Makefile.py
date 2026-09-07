"""Tasks for developing `make` itself -- and the first thing that dogfoods it.

    mk dev.check      lint + both test suites: everything CI runs
    mk dev.bench      startup latency against the 150ms budget
    mk dist.release   tag a version, which publishes to PyPI from CI
    mk site.serve     the landing page in site/, locally
    mk site.deploy    publish site/ to Cloudflare Pages (production)

Run `mk` with no arguments to see them all.
"""

from __future__ import annotations

from pathlib import Path

from make import note, sh, step, task, warn

# Both workspace members, listed as Python paths rather than as directories.
# Ruff formats Python code blocks inside markdown too, and handing it a whole
# directory reaches docs/ -- where examples are hand-packed to read as prose
# and reformatting them is a docs edit disguised as a lint fix.
SOURCES = ["src", "tests", "Makefile.py", "rust/src", "rust/tests"]


@task(group="dev", requires=["uv"])
def sync() -> None:
    """Install both workspace members in editable mode.

    `--all-packages` is what reaches rust/; a plain `uv sync` installs the
    root project only, and then rust/tests fail on import rather than on
    anything real.

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


# The landing page in site/ -- static files, no build step, published to the
# Cloudflare Pages project `mkrun` by direct upload. There is no framework and
# nothing to compile on purpose: the page is a description, four links and a
# changelog, and a toolchain for that is a second thing to keep alive.
SITE = "mkrun"
SITE_LIVE = "https://mkrun-dcd.pages.dev"


@task(group="site")
def serve(*, port: int = 8100) -> None:
    """Serve site/ locally, exactly as Pages will (no build step)."""
    from make import ctx

    step(f"http://localhost:{port} -- ctrl-c to stop")
    sh("python3", "-m", "http.server", str(port), "--directory", str(ctx.root / "site"))


@task(group="site", requires=["wrangler"])
def dev(*, port: int = 8101) -> None:
    """Serve site/ through the Pages runtime, so `_headers` and 404.html apply.

    `site.serve` is a plain file server -- fine for editing prose, but it does
    not apply the CSP in `_headers` and answers 200 for a missing path. This
    runs what the edge runs, with live reload.
    """
    from make import ctx

    step(f"http://localhost:{port} -- the Pages runtime, ctrl-c to stop")
    sh("wrangler", "pages", "dev", str(ctx.root / "site"), "--port", str(port), "--live-reload")


@task(group="site", requires=["wrangler"], dangerous=True)
def deploy() -> None:
    """Publish site/ to Cloudflare Pages as the production deployment.

    Auth is wrangler's: `CLOUDFLARE_API_TOKEN`, else a cached `wrangler login`.
    `--branch main` is what makes this PRODUCTION -- without it wrangler infers
    the branch from git and anything but main lands as a preview that never
    reaches the live URL.
    """
    step(f"deploying site/ to Cloudflare Pages ({SITE})")
    sh(
        "wrangler",
        "pages",
        "deploy",
        "site",
        "--project-name",
        SITE,
        "--branch",
        "main",
        "--commit-dirty=true",
    )
    note(f"done -- {SITE_LIVE}")


@task(group="site")
def smoke(*, base: str = SITE_LIVE) -> None:
    """Check the live page: it answers 200 and still carries every outbound link."""
    from make import http

    # Cloudflare's bot management answers 403 to urllib's default User-Agent,
    # so the page looks broken from a check that does not send a browser one.
    browser = {"User-Agent": "Mozilla/5.0 (mk site.smoke)"}

    for path, expected in ((f"{base}/", 200), (f"{base}/style.css", 200), (f"{base}/nowhere", 404)):
        got = http.get(path, headers=browser).status
        if got != expected:
            raise SystemExit(f"{path}: expected {expected}, got {got}")

    page = http.get(f"{base}/", headers=browser).body
    for link in ("pypi.org/project/mkrun", "github.com/optersoft/make", "academy.optersoft.com/project/make"):
        if link not in page:
            raise SystemExit(f"the live page no longer links to {link}")
    note(f"{base} is live, with all three links")
