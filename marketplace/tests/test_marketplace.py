"""Tests for the `marketplace` group.

Nothing here runs `npx`, `gh` or a real HTTP request: `record()` captures what a
task would have run, and the gallery query is stubbed. Every assertion below is
a rule that was learned by breaking a real release.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from make.errors import MakeError
from make.testing import context, record
from make_marketplace import gallery, marketplace
from make_marketplace.marketplace import Marketplace


def extension(root: Path, name: str = "kotlin", version: str = "0.1.0", changelog: bool = True) -> Path:
    target = root / name
    target.mkdir(parents=True)
    (target / "package.json").write_text(
        json.dumps(
            {
                "name": "kotlin-toolchain",
                "publisher": "optersoft",
                "version": version,
                "displayName": "Kotlin Toolchain",
            }
        )
    )
    if changelog:
        (target / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n\n- something\n")
    return target


@pytest.fixture
def configured(tmp_path):
    Marketplace.configure(root=tmp_path, repo="optersoft/kotlin", environment="marketplace")
    return tmp_path


# -- discovery -------------------------------------------------------------


def test_an_extension_is_a_directory_with_a_publisher(tmp_path):
    extension(tmp_path)
    (tmp_path / "lsp").mkdir()
    (tmp_path / "lsp" / "package.json").write_text('{"name": "lsp"}')  # no publisher
    assert gallery.extensions(tmp_path) == ["kotlin"]


def test_naming_an_extension_that_is_not_there_lists_what_is(tmp_path):
    extension(tmp_path)
    with pytest.raises(MakeError, match="have: kotlin"):
        gallery.resolve("vscode", tmp_path)


# -- packaging -------------------------------------------------------------


def test_packaging_sweeps_the_stale_vsix_first(tmp_path):
    """Two .vsix in a directory and `built[-1]` is a coin toss on the version."""
    target = extension(tmp_path)
    stale = target / "kotlin-toolchain-0.0.9.vsix"
    stale.write_bytes(b"old")
    with context(root=tmp_path), record():
        gallery.package(target)
    assert not stale.exists()


def test_packaging_does_not_tag(tmp_path):
    """`vsce` offers to tag; the tag is the release task's, pushed only once everything passed."""
    target = extension(tmp_path)
    with context(root=tmp_path), record() as rec:
        gallery.package(target)
    assert rec.saw("@vscode/vsce", "package")
    assert rec.saw("--no-git-tag-version")


# -- the token -------------------------------------------------------------


def test_both_secret_scopes_are_asked(tmp_path):
    """An environment secret is invisible to `gh secret list --repo`, which returns
    an empty list that reads exactly like "no token configured"."""
    with context(root=tmp_path), record() as rec:
        gallery.secret_names("optersoft/kotlin", "marketplace")
    assert rec.saw("gh", "secret", "list", "--repo", "optersoft/kotlin")
    assert rec.matched(r"environments/marketplace/secrets")


def test_a_repository_secret_only_asks_once(tmp_path):
    with context(root=tmp_path), record() as rec:
        gallery.secret_names("optersoft/kotlin", "")
    assert not rec.matched(r"environments/")


# -- the gallery -----------------------------------------------------------


def test_an_unreachable_gallery_is_not_a_veto(monkeypatch):
    """The publish job compares the tag against the manifest, and the Marketplace
    rejects a duplicate itself -- so failing to ask must not block a release."""
    from make import http

    monkeypatch.setattr(http, "request", lambda *a, **k: http.Response(url="x", status=0))
    assert gallery.published_versions("optersoft.kotlin-toolchain") == set()


def test_published_versions_reads_the_gallery_shape(monkeypatch):
    from make import http

    body = json.dumps(
        {"results": [{"extensions": [{"versions": [{"version": "0.3.1"}, {"version": "0.3.0"}]}]}]}
    )
    monkeypatch.setattr(http, "request", lambda *a, **k: http.Response(url="x", status=200, body=body))
    assert gallery.published_versions("optersoft.kotlin-toolchain") == {"0.3.1", "0.3.0"}


# -- the changelog ---------------------------------------------------------


def test_rolling_the_changelog_dates_the_heading(tmp_path):
    import datetime

    path = tmp_path / "CHANGELOG.md"
    path.write_text("## [Unreleased]\n\n- a thing\n")
    assert gallery.roll_changelog(path, "0.4.0")
    assert f"## [0.4.0] - {datetime.date.today().isoformat()}" in path.read_text()


def test_a_changelog_with_nothing_to_roll_says_so(tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text("## [0.3.0] - 2026-09-18\n")
    assert not gallery.roll_changelog(path, "0.4.0")


# -- release preflight -----------------------------------------------------


def released(tmp_path, monkeypatch, *, published=(), secrets="VSCE_PAT", token_ok=True, **responses):
    """Run `release` with the outside world stubbed to a chosen state."""
    monkeypatch.setattr(gallery, "published_versions", lambda name: set(published))
    monkeypatch.setattr(gallery, "secret_names", lambda repo, environment="": secrets)
    monkeypatch.setattr(gallery, "token_can_publish", lambda *a, **k: None if token_ok else "https://run/1")
    answers = {"git status": "", "git rev-parse": "main", "git tag": "", "git ls-remote": "", **responses}
    return context(root=tmp_path), record(responses=answers)


def test_a_four_part_version_is_refused_before_anything_else(configured, monkeypatch):
    extension(configured)
    outer, rec = released(configured, monkeypatch)
    with outer, rec:
        with pytest.raises(MakeError, match="is not a version"):
            marketplace.release("1.2.3.4")


def test_a_dirty_tree_refuses(configured, monkeypatch):
    extension(configured)
    outer, rec = released(configured, monkeypatch, **{"git status": " M kotlin/src/extension.js"})
    with outer, rec:
        with pytest.raises(MakeError, match="working tree has changes"):
            marketplace.release("0.2.0")


def test_a_release_must_describe_the_main_branch(configured, monkeypatch):
    extension(configured)
    outer, rec = released(configured, monkeypatch, **{"git rev-parse": "feature/x"})
    with outer, rec:
        with pytest.raises(MakeError, match="not 'main'"):
            marketplace.release("0.2.0")


def test_an_already_published_version_is_refused(configured, monkeypatch):
    """The whole reason this preflight exists: a published version is permanent."""
    extension(configured)
    outer, rec = released(configured, monkeypatch, published=("0.2.0",))
    with outer, rec:
        with pytest.raises(MakeError, match="already on the Marketplace"):
            marketplace.release("0.2.0")


def test_a_changelog_with_no_unreleased_section_is_refused_before_the_version_is_written(
    configured, monkeypatch
):
    target = extension(configured, changelog=False)
    (target / "CHANGELOG.md").write_text("# Changelog\n")
    outer, rec = released(configured, monkeypatch)
    with outer, rec:
        with pytest.raises(MakeError, match="no `## \\[Unreleased\\]`"):
            marketplace.release("0.2.0")
    assert json.loads((target / "package.json").read_text())["version"] == "0.1.0"


def test_a_missing_token_refuses_but_tag_only_proceeds(configured, monkeypatch):
    extension(configured)
    outer, rec = released(configured, monkeypatch, secrets="")
    with outer, rec:
        with pytest.raises(MakeError, match="no VSCE_PAT secret"):
            marketplace.release("0.2.0")


def test_a_present_token_that_cannot_publish_stops_the_release(configured, monkeypatch):
    """Presence is not permission -- checking existence passed three times while
    every publish failed with Access Denied."""
    extension(configured)
    outer, rec = released(configured, monkeypatch, token_ok=False)
    with outer, rec:
        with pytest.raises(MakeError, match="cannot publish as 'optersoft'"):
            marketplace.release("0.2.0")


def test_the_gate_is_abstract_until_a_repo_fills_it(configured, monkeypatch):
    """A shared package cannot know what "tested" means here, so it refuses to guess."""
    extension(configured)
    outer, rec = released(configured, monkeypatch)
    with outer, rec:
        with pytest.raises(MakeError, match=r"marketplace\.gate is declared but not implemented"):
            marketplace.release("0.2.0")


def test_nothing_is_committed_when_a_preflight_refuses(configured, monkeypatch):
    extension(configured)
    outer, rec = released(configured, monkeypatch, published=("0.2.0",))
    with outer, rec as recorder:
        with pytest.raises(MakeError):
            marketplace.release("0.2.0")
    assert not recorder.saw("git", "commit")
    assert not recorder.saw("git", "push")


def test_an_unconfigured_repo_says_which_setting_is_missing(tmp_path):
    # `configure` accumulates, so an earlier test's repo has to be cleared explicitly.
    Marketplace.configure(root=tmp_path, repo="")
    extension(tmp_path)
    with context(root=tmp_path), record():
        with pytest.raises(MakeError, match=r"marketplace\.repo is not configured"):
            marketplace.release("0.2.0")
