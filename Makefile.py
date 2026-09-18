"""Tasks for developing `make` itself -- and the first thing that dogfoods it.

    mk dev.check      lint + both test suites: everything CI runs
    mk dev.bench      startup latency against the 150ms budget
    mk dist.release   tag a version, which publishes to PyPI from CI
    mk site.dev       the landing page in site/ (Astro, on the Optersoft chrome), locally
    mk site.build     site/dist
    mk site.deploy    publish site/dist to Cloudflare Pages (production)

Run `mk` with no arguments to see them all.
"""

from __future__ import annotations

from pathlib import Path

from make import note, sh, step, task, warn

# Both workspace members, listed as Python paths rather than as directories.
# Ruff formats Python code blocks inside markdown too, and handing it a whole
# directory reaches docs/ -- where examples are hand-packed to read as prose
# and reformatting them is a docs edit disguised as a lint fix.
SOURCES = ["src", "tests", "Makefile.py", "rust/src", "rust/tests", "cloudflare/src", "cloudflare/tests"]


@task(group="dev", requires=["uv"])
def sync() -> None:
    """Install every workspace member in editable mode.

    `--all-packages` is what reaches rust/ and cloudflare/; a plain `uv sync`
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


# The landing page in site/ -- an Astro static site on the Optersoft chrome
# (`@optersoft/astro`, the sibling checkout at ../astro), published to the
# Cloudflare Pages project `mkrun` (make.optersoft.com) by direct upload.
SITE = "mkrun"
SITE_LIVE = "https://make.optersoft.com"
SITE_DIR = Path(__file__).resolve().parent / "site"


def _site_deps() -> None:
    if not (SITE_DIR / "node_modules").is_dir():
        step("installing node modules")
        sh("npm", "ci", cwd=SITE_DIR)


@task(group="site", name="dev", requires=["npm"])
def site_dev(*args: str) -> None:
    """The Astro dev server for site/ on :4321, hot reload (extra arguments forward to astro)."""
    _site_deps()
    sh("npx", "astro", "dev", *args, cwd=SITE_DIR)


@task(group="site", name="check", requires=["npm"])
def site_check() -> None:
    """`astro check` over site/."""
    _site_deps()
    sh("npx", "astro", "check", cwd=SITE_DIR)


@task(group="site", name="build", requires=["npm"])
def site_build() -> None:
    """Build site/ into site/dist."""
    _site_deps()
    sh("npx", "astro", "build", cwd=SITE_DIR)


@task(group="site", name="deploy", needs=[site_build], requires=["wrangler"], dangerous=True)
def site_deploy() -> None:
    """Build, then publish site/dist to Cloudflare Pages as the production deployment.

    Auth is wrangler's: `CLOUDFLARE_API_TOKEN`, else a cached `wrangler login`.
    `--branch main` is what makes this PRODUCTION -- without it wrangler infers
    the branch from git and anything but main lands as a preview that never
    reaches the live URL.
    """
    step(f"deploying site/dist to Cloudflare Pages ({SITE})")
    sh(
        "wrangler",
        "pages",
        "deploy",
        str(SITE_DIR / "dist"),
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

    for path, expected in ((f"{base}/", 200), (f"{base}/nowhere", 404)):
        got = http.get(path, headers=browser).status
        if got != expected:
            raise SystemExit(f"{path}: expected {expected}, got {got}")

    page = http.get(f"{base}/", headers=browser).body
    for link in ("pypi.org/project/mkrun", "github.com/optersoft/make", "academy.optersoft.com/project/make"):
        if link not in page:
            raise SystemExit(f"the live page no longer links to {link}")
    note(f"{base} is live, with all three links")
