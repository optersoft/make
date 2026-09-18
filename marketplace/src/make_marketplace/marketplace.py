"""The `marketplace` group: build a VS Code extension, and release it.

    mk marketplace.package [EXT]        build the .vsix
    mk marketplace.published [EXT]      what is already on the Marketplace
    mk marketplace.verify-token         can CI's token actually publish?
    mk marketplace.release VERSION      tag a release; CI publishes it

**`release` never touches a Marketplace token, and that is the design.** The
credential is a `VSCE_PAT` secret on a GitHub environment, used only by a
tag-triggered publish job. A release is therefore a *push*: no laptop, and no
task in this package, is ever able to publish. What `release` does is refuse --
eight times, in the order that costs the least -- and then commit, tag and push.

Two hooks a consumer is expected to fill:

    @task(name="gate", group="marketplace", override=True)
    def gate(ext: str = "") -> None: ...        # the repo's tests

    @task(name="preflight", group="marketplace", override=True)
    def preflight(version: str, ext: str = "") -> None: ...   # anything else

`gate` is `abstract=True`: it lists as unimplemented and refuses to run with a
message saying what to write, rather than a shared package deciding what
"tested" means for a repo it knows nothing about. `preflight` defaults to doing
nothing and exists for the checks that are nobody else's business -- the Kotlin
Toolchain extension requires a tree that has been run on a real Windows desktop,
which no other extension would want and none of them could implement.

⚠ **A version published to the Marketplace can never be reused**, withdrawn or
replaced -- only superseded by a higher one. `0.2.0` is the one and only shot at
`0.2.0`. Everything in the preflight exists because of that sentence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from make import config, group, invoke, note, path, sh, step, warn
from make.errors import MakeError

from . import gallery

__all__ = ["Marketplace", "marketplace"]

marketplace = group("marketplace")


@config.section("marketplace")
@dataclass
class Marketplace:
    """Where the extensions are, and where the token that publishes them lives."""

    root: Path = Path(".")
    """Directory holding the extension directories -- `editors`, say, or the repo root."""

    repo: str = ""
    """`owner/name` on GitHub: the repository whose CI holds the token."""

    environment: str = "marketplace"
    """GitHub environment the `VSCE_PAT` secret is scoped to. `""` for a repository secret."""

    verify_workflow: str = "verify-token.yml"
    """Workflow that proves the token can publish. `""` disables the check."""

    branch: str = "main"
    """The branch a release must describe."""

    paths: list[str] = field(default_factory=list)
    """What must be committed before a release. Empty means the whole tree."""

    reminder: str = ""
    """Warned after a release -- the step outside the repo that gets forgotten.
    `{version}` is substituted."""


def _root() -> Path:
    return path(Marketplace.get().root)


def _repo() -> str:
    repo = Marketplace.get().repo
    if not repo:
        raise MakeError(
            "marketplace.repo is not configured",
            hint='Marketplace.configure(repo="owner/name") -- the repository whose CI publishes',
        )
    return repo


# -- building --------------------------------------------------------------


@marketplace.task(name="package", requires=["npm"])
def package(ext: str = "") -> None:
    """Build the `.vsix`, one extension's or every one's."""
    root = _root()
    for target in gallery.resolve(ext, root):
        built = gallery.package(target)
        note(f"built {built.relative_to(root)}" if built else f"{target.name}: no .vsix produced")


@marketplace.task(name="published")
def published(ext: str = "") -> None:
    """What is already on the Marketplace, newest first. One HTTP call, no token."""
    for target in gallery.resolve(ext, _root()):
        name = gallery.full_name(gallery.manifest(target))
        versions = sorted(gallery.published_versions(name), reverse=True)
        step(f"{name}: {', '.join(versions[:10]) if versions else 'nothing published yet'}")


# -- the hooks a consumer fills -------------------------------------------


@marketplace.task(name="gate", abstract=True)
def gate(ext: str = "") -> None:
    """The repo's own tests, run before a release writes anything.

    Override it:

        @task(name="gate", group="marketplace", override=True)
        def gate(ext: str = "") -> None:
            test(ext)
    """


@marketplace.task(name="preflight", hidden=True)
def preflight(version: str, ext: str = "") -> None:
    """Repo-specific refusals, run inside `release`'s preflight. Does nothing by default."""


# -- the token -------------------------------------------------------------


@marketplace.task(name="verify-token", requires=["gh"])
def verify_token(ext: str = "") -> None:
    """Ask CI whether the Marketplace token can publish, without releasing anything.

    The same check `release` runs, on its own -- because the answer is worth
    having while a token or a publisher membership is being sorted out, and
    because it is the one question that cannot be answered from a laptop.
    """
    data = gallery.manifest(gallery.resolve(ext, _root())[0])
    publisher = data["publisher"]
    note(f"asking CI whether the token can publish as {publisher!r} (~30s)")
    failed = gallery.token_can_publish(_repo(), Marketplace.get().verify_workflow)
    if failed is None:
        note(f"the token can publish as {publisher!r}")
        return
    raise MakeError(f"the token cannot publish as {publisher!r}", hint=_token_hint(publisher, failed))


def _token_hint(publisher: str, run_url: str = "") -> str:
    """The two things that are wrong when a PAT authenticates but cannot publish."""
    seen = f"see {run_url} -- " if run_url else ""
    return (
        f"{seen}a PAT needs Organization: All accessible organizations and Scopes: "
        f"Marketplace > Manage, AND its account must be a member of the {publisher!r} "
        "publisher at https://marketplace.visualstudio.com/manage"
    )


# -- the release -----------------------------------------------------------


@marketplace.task(name="release", requires=["git", "gh", "npm"], dangerous=True)
def release(version: str, ext: str = "", tag_only: bool = False) -> None:
    """Tag a release. CI publishes it to the Marketplace. IRREVERSIBLE once published.

    In order, refusing rather than guessing at every step:

        preflight      a version that is a version, a clean tree, the right
                       branch, whatever the repo adds, an unused tag, a
                       changelog with something in it, a version nobody has
                       published, and a token CI confirms can publish
        the gate       `marketplace.gate` -- the repo's tests
        the version    written into `package.json`
        the changelog  `## [Unreleased]` becomes `## [VERSION] - <today>`
        the package    `vsce package`, which validates the manifest BEFORE the
                       tag becomes irrevocable
        the commit     those two files, message `Release VERSION`
        the tag        `vVERSION`, pushed with the commit

    `tag-only` accepts having no working token yet: the commit and the tag are
    pushed, the publish job runs and fails, and nothing reaches the Marketplace.
    That is recoverable and occasionally what you want -- fix the token and
    re-run the failed job. The version is not burned, because nothing was ever
    published under it.
    """
    settings = Marketplace.get()
    root = _root()
    repo = _repo()

    if not gallery.SEMVER.match(version):
        raise MakeError(
            f"{version!r} is not a version",
            hint="a release is MAJOR.MINOR.PATCH, e.g. `mk marketplace.release 0.2.0`",
        )

    target = gallery.resolve(ext, root)[0]
    manifest_path = target / "package.json"
    data = gallery.manifest(target)
    name = gallery.full_name(data)
    tag = f"v{version}"

    # --- preflight: cheapest and most likely to fail, first -----------------
    # Everything down to the gate is a string comparison, a `git` call or one
    # HTTP request, so a mistyped version costs a second rather than a test run.

    dirty = gallery.git("status", "--porcelain", "--", *(settings.paths or ["."]), cwd=root)
    if dirty:
        raise MakeError(
            "the working tree has changes",
            hint="a release must be exactly what is committed; commit or stash first:\n" + dirty,
        )

    branch = gallery.git("rev-parse", "--abbrev-ref", "HEAD", cwd=root)
    if branch != settings.branch:
        raise MakeError(
            f"on branch {branch!r}, not {settings.branch!r}",
            hint="the publish job is tag-triggered, but a release should describe the main branch",
        )

    invoke("marketplace.preflight", version, ext)

    if tag in gallery.git("tag", "--list", tag, cwd=root).split():
        raise MakeError(f"the tag {tag} already exists here")
    if tag in gallery.git("ls-remote", "--tags", "origin", tag, cwd=root):
        raise MakeError(f"the tag {tag} already exists on origin")

    # Before anything is written. A release that got as far as the changelog and
    # found nothing to roll had already bumped package.json, and left a tree that
    # read like a release in progress.
    changelog = target / "CHANGELOG.md"
    if not changelog.exists() or "## [Unreleased]" not in changelog.read_text():
        raise MakeError(
            f"{changelog.name} has no `## [Unreleased]` section to release",
            hint="write what changed before releasing it; the Marketplace shows this as the Changelog tab",
        )

    if version in gallery.published_versions(name):
        raise MakeError(
            f"{name} {version} is already on the Marketplace",
            hint="a published version can never be replaced, only superseded -- pick a higher one",
        )

    # The whole design rests on the token being in CI rather than here, so its
    # absence is the one preflight worth making noisy: without it the tag lands,
    # the job runs, and nothing is published.
    secrets = gallery.secret_names(repo, settings.environment)
    has_token = "VSCE_PAT" in secrets
    if not has_token:
        where = (
            f"the repository or the {settings.environment} environment"
            if settings.environment
            else "the repository"
        )
        if not tag_only:
            raise MakeError(
                f"no VSCE_PAT secret on {where}",
                hint=(
                    "a PAT for this needs Organization: All accessible organizations and Scopes: "
                    f"Marketplace > Manage, then: gh secret set VSCE_PAT --repo {repo}"
                    + (f" --env {settings.environment}" if settings.environment else "")
                    + "; or pass tag-only to tag now and publish once the token is there"
                ),
            )
        warn(
            "no VSCE_PAT secret: the tag will be pushed and the publish job WILL FAIL. "
            "Nothing reaches the Marketplace, so the version is not burned -- add the secret "
            "and re-run the job."
        )
    # Presence is not permission. Ask CI, the only place the token is, whether it
    # can publish as this manifest's publisher -- before the tag, which is the
    # irreversible part. Skipped under tag-only, whose point is tagging ahead of
    # a working token.
    elif not tag_only and settings.verify_workflow:
        note("asking CI whether the Marketplace token can publish (~30s)")
        failed = gallery.token_can_publish(repo, settings.verify_workflow)
        if failed:
            raise MakeError(
                f"the VSCE_PAT secret exists but cannot publish as {data['publisher']!r}",
                hint=_token_hint(data["publisher"], failed) + "; or pass tag-only to tag anyway",
            )

    # --- the gate ------------------------------------------------------------

    invoke("marketplace.gate", ext)

    # --- the release ---------------------------------------------------------

    data["version"] = version
    manifest_path.write_text(json.dumps(data, indent=2) + "\n")

    if not gallery.roll_changelog(changelog, version):
        raise MakeError(f"{changelog.name} lost its `## [Unreleased]` section during the gate")

    # `vsce` validates the manifest, so a bad `contributes` block fails here
    # rather than after the tag is pushed and irrevocable.
    gallery.package(target)

    relative = [str(manifest_path.relative_to(root)), str(changelog.relative_to(root))]
    sh("git", "add", *relative, cwd=root)
    sh("git", "commit", "-m", f"Release {version}", cwd=root)
    sh("git", "tag", "-a", tag, "-m", f"{data.get('displayName', name)} {version}", cwd=root)
    sh("git", "push", "origin", settings.branch, cwd=root)
    sh("git", "push", "origin", tag, cwd=root)

    if tag_only and not has_token:
        note(f"pushed {tag}; the publish job will fail until VSCE_PAT exists")
        note(f"then: gh run rerun --failed --repo {repo}")
    else:
        note(f"pushed {tag}; CI is publishing {name} {version}")
    note(f"watch it: gh run watch --repo {repo}")

    # Said last, because it is the one that gets forgotten.
    if settings.reminder:
        warn(settings.reminder.format(version=version))
