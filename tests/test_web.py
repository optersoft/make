"""Tests for the `web` group.

These are the tests `web.just` could not have. Every case below corresponds to
something that shipped broken, or to a rule stated in a comment with nothing
enforcing it.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from make_recipes_optersoft import web
from make_recipes_optersoft.web import Web

from make.testing import context, record


@pytest.fixture(autouse=True)
def configured(tmp_path: Path, monkeypatch):
    for name in ("MAKE_WEB_PORT", "JUST_WEB_PORT"):
        monkeypatch.delenv(name, raising=False)
    Web.reset()
    Web.configure(bin="alma", port=8005, serve_dir="alma-server", watch=["alma-web"])
    yield
    Web.reset()


# -- ports -----------------------------------------------------------------


def test_port_defaults_to_the_configured_one(tmp_path: Path):
    with context(root=tmp_path):
        assert web.resolve_port() == 8005


def test_an_explicit_port_wins(tmp_path: Path):
    with context(root=tmp_path):
        assert web.resolve_port(8105) == 8105


def test_a_checkout_claims_its_own_port_from_dot_env(tmp_path: Path):
    """The per-checkout override, so a worktree can serve alongside main.

    It lives in ./.env and deliberately not in ~/.make/<repo>.env: that file is
    shared by every worktree of the repo, so it cannot describe one checkout.
    """
    (tmp_path / ".env").write_text("MAKE_WEB_PORT=8105\n")
    with context(root=tmp_path):
        assert web.resolve_port() == 8105


def test_the_legacy_port_name_still_works(tmp_path: Path):
    (tmp_path / ".env").write_text("JUST_WEB_PORT=8205\n")
    with context(root=tmp_path):
        assert web.resolve_port() == 8205


def test_an_exported_port_beats_dot_env(tmp_path: Path, monkeypatch):
    (tmp_path / ".env").write_text("MAKE_WEB_PORT=8105\n")
    monkeypatch.setenv("MAKE_WEB_PORT", "8305")
    with context(root=tmp_path):
        assert web.resolve_port() == 8305


def test_cdp_port_is_stable_and_offset_by_a_thousand():
    """The MCP attaches with a fixed --browserUrl, so this cannot float."""
    assert [web.cdp_port(p) for p in (8001, 8002, 8003, 8005, 8105)] == [9001, 9002, 9003, 9005, 9105]


# -- the OAuth base URL ----------------------------------------------------


def test_the_base_url_is_exported_under_both_names():
    """Exporting only the legacy name was a silent no-op for xtec and alma.

    `axum-oauth` resolves first-found-wins, canonical first, and those repos pin
    the canonical name in their secrets -- so the app kept redirecting to the
    pinned port while the server listened on another one.
    """
    exported = web.oauth_env(8105)
    assert exported["AXUM_OAUTH_BASE_URL"] == "http://localhost:8105"
    assert exported["HIVE_AUTH_BASE_URL"] == "http://localhost:8105"


# -- the two reaps ---------------------------------------------------------


def test_the_port_reap_is_scoped_to_this_checkouts_port(tmp_path: Path):
    with context(root=tmp_path), record(responses={"lsof -ti tcp:8005": "4711\n4712"}) as rec:
        assert web.reap_port(8005) == [4711, 4712]
    assert rec.saw("kill", "-9", "4711", "4712")
    # Never an unscoped `pkill -f 'dx serve'` -- that would take out sibling repos.
    assert rec.matched(r"pkill -f 'dx serve\.\*--port 8005'")
    assert not rec.matched(r"pkill -f .?dx serve.?$")


def test_the_port_reap_does_nothing_when_the_port_is_free(tmp_path: Path):
    with context(root=tmp_path), record(failures={"lsof -iTCP:8005": 1}) as rec:
        assert web.reap_port(8005) == []
    assert not rec.saw("kill", "-9")


def test_the_database_reap_finds_an_orphan_that_holds_no_port(tmp_path: Path):
    """The reap that actually matters.

    The turso WAL lock, not the port, is what blocks the next boot. An orphan
    stranded off the port -- a crashed parent whose child kept the descriptor, a
    hand-run `cargo run --features server` -- is invisible to the port reap
    while still deadlocking every subsequent start.
    """
    dev = tmp_path / "tmp"
    dev.mkdir()
    (dev / "dev.db").write_text("")

    with context(root=tmp_path), record(responses={"lsof -t --": "4711"}) as rec:
        assert web.reap_database_holders(dev) == [4711]
    assert rec.saw("kill", "-9", "4711")


def test_the_database_reap_never_matches_the_chrome_profile(tmp_path: Path):
    """The glob is non-recursive, so Chrome's own SQLite files are out of scope."""
    dev = tmp_path / "tmp"
    (dev / "chrome").mkdir(parents=True)
    (dev / "chrome" / "History.db").write_text("")

    with context(root=tmp_path), record() as rec:
        assert web.reap_database_holders(dev) == []
    assert rec.commands == []


# -- disk reclamation ------------------------------------------------------


def test_stale_dx_bundles_are_reaped_by_mtime(tmp_path: Path):
    """Renaming the dx binary orphans target/dx/<old>/ in place, silently."""
    bundles = tmp_path / "target" / "dx"
    stale = bundles / "xtec"
    fresh = bundles / "academy"
    stale.mkdir(parents=True)
    fresh.mkdir(parents=True)
    old = time.time() - 30 * 86400
    import os

    os.utime(stale, (old, old))

    with context(root=tmp_path), record():
        reaped = web.reap_dx()

    assert reaped == [stale]
    assert not stale.exists()
    assert fresh.exists()


def test_incremental_caches_are_kept_while_under_budget(tmp_path: Path):
    (tmp_path / "target" / "debug" / "incremental").mkdir(parents=True)
    with context(root=tmp_path), record(responses={"du -sk": "1024\ttarget"}):
        assert web.reap_incremental() == []
    assert (tmp_path / "target" / "debug" / "incremental").exists()


def test_incremental_caches_are_dropped_over_budget(tmp_path: Path):
    caches = tmp_path / "target" / "debug" / "incremental"
    caches.mkdir(parents=True)
    fifty_gb_in_kb = 50 * 1024 * 1024
    with context(root=tmp_path), record(responses={"du -sk": f"{fifty_gb_in_kb}\ttarget"}):
        assert web.reap_incremental() == [caches]
    assert not caches.exists()


def test_a_zero_budget_disables_the_prune(tmp_path: Path):
    caches = tmp_path / "target" / "debug" / "incremental"
    caches.mkdir(parents=True)
    Web.configure(target_budget_gb=0)
    with context(root=tmp_path), record(responses={"du -sk": "999999999\ttarget"}):
        assert web.reap_incremental() == []
    assert caches.exists()


# -- teardown --------------------------------------------------------------


def test_restart_keeps_chrome_and_never_pays_for_disk_reclamation(tmp_path: Path):
    """A cold rebuild on every restart is the loop this module exists to avoid."""
    (tmp_path / "tmp" / "chrome").mkdir(parents=True)
    (tmp_path / "alma-server").mkdir()

    with context(root=tmp_path, dry_run=True), record() as rec:
        web.stop_server()

    assert not rec.matched(r"pkill -f user-data-dir")  # Chrome survives
    assert not rec.saw("du", "-sk")  # no budget check, so no cold rebuild


def test_full_stop_takes_chrome_down_scoped_to_this_repo(tmp_path: Path):
    profile = tmp_path / "tmp" / "chrome"
    profile.mkdir(parents=True)

    with context(root=tmp_path), record(responses={"du -sk": "1\ttarget"}) as rec:
        web.stop()

    # Matched on the absolute profile path, so another repo's dev Chrome and the
    # main browser are left alone.
    assert rec.matched(rf"pkill -f user-data-dir={profile}")


def test_temp_file_sweep_removes_atomic_write_leftovers(tmp_path: Path):
    watched = tmp_path / "alma-web"
    watched.mkdir()
    stray = watched / ".!12345!tmp"
    stray.write_text("")
    keep = watched / "app.rs"
    keep.write_text("")

    with context(root=tmp_path):
        web.sweep_temp_files()

    assert not stray.exists()
    assert keep.exists()


# -- Tailwind --------------------------------------------------------------


def test_tailwind_sources_are_generated_from_cargo_metadata(tmp_path: Path):
    """Replaces the hand-copied @source inline(...) safelists that drifted."""
    (tmp_path / "assets").mkdir()
    Web.configure(css_in="assets/tailwind.input.css", css_out="assets/tailwind.css")

    metadata = (
        '{"packages":[{"manifest_path":"/Users/x/.cargo/git/checkouts/dioxus-ab12/f00/dioxus-chrome/Cargo.toml"},'
        '{"manifest_path":"/Users/x/.cargo/registry/src/dioxus-core-0.7.9/Cargo.toml"},'
        '{"manifest_path":"/Users/x/.cargo/git/checkouts/dioxus-ab12/f00/dioxus-forms/Cargo.toml"}]}'
    )
    with context(root=tmp_path), record(responses={"cargo metadata": metadata}):
        out = web.tailwind_sources()

    written = out.read_text()
    assert '@source "/Users/x/.cargo/git/checkouts/dioxus-ab12/f00/dioxus-chrome/src/**/*.rs";' in written
    assert '@source "/Users/x/.cargo/git/checkouts/dioxus-ab12/f00/dioxus-forms/src/**/*.rs";' in written
    # A registry crate's directory carries a -<version> suffix, so it cannot match.
    assert "dioxus-core" not in written


def test_a_repo_without_tailwind_generates_nothing(tmp_path: Path):
    Web.configure(css_in="", css_out="")
    with context(root=tmp_path), record() as rec:
        assert web.tailwind_sources() is None
    assert rec.commands == []


def test_the_css_watcher_is_scoped_to_this_repos_input_path(tmp_path: Path):
    (tmp_path / "assets").mkdir()
    Web.configure(css_in="assets/in.css", css_out="assets/out.css")
    dev = tmp_path / "tmp"
    dev.mkdir()

    with context(root=tmp_path), record() as rec:
        web.start_css_watcher(dev)

    # The previous watcher is killed by absolute input path, so a sibling repo's
    # watcher survives -- web.start execs into dx and leaves no trap to do it.
    assert rec.matched(rf"pkill -f 'tailwindcss -i {tmp_path}/assets/in.css'")
    # Tailwind v4's watch exits on stdin EOF when backgrounded, so stdin is pinned.
    assert rec.matched(r"tail -f /dev/null \| tailwindcss")


def test_a_version_mismatch_warns_but_does_not_fail(tmp_path: Path, capsys):
    (tmp_path / "assets").mkdir()
    Web.configure(css_in="assets/in.css", css_out="assets/out.css")
    with context(root=tmp_path), record(responses={"tailwindcss --help": "tailwindcss v3.4.1"}):
        web.tailwind()
    assert "the fleet pins v" in capsys.readouterr().err
