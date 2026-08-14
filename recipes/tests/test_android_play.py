"""Tests for the `android` and `play` groups -- the pipeline that reaches real users."""

from __future__ import annotations

from pathlib import Path

import pytest
from make.errors import Aborted, MakeError, RecipeError
from make.recipes import registry
from make.runner import run_one
from make.testing import context, record

from make_recipes_optersoft import android, play
from make_recipes_optersoft.android import Android
from make_recipes_optersoft.play import Play


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    Android.reset()
    Play.reset()
    Android.configure(module="drive-android", pkg="com.optersoft.drive")
    Play.configure(locales=["en-US", "es-ES"])
    yield
    Android.reset()
    Play.reset()


# -- device selection ------------------------------------------------------


def test_the_physical_device_is_picked_over_a_running_emulator():
    """Skipping emulators is what lets this work while one is running."""
    listing = "List of devices attached\nemulator-5554\tdevice\nR5CT31\tdevice\n"
    with record(responses={"adb devices": listing}):
        assert android.physical_device() == "R5CT31"


def test_an_unauthorised_device_does_not_count():
    listing = "List of devices attached\nR5CT31\tunauthorized\n"
    with record(responses={"adb devices": listing}):
        with pytest.raises(MakeError, match="no USB device"):
            android.physical_device()


def test_a_signature_clash_falls_back_to_uninstall_and_reinstall(tmp_path):
    listing = "List of devices attached\nR5CT31\tdevice\n"
    with (
        context(root=tmp_path),
        record(responses={"adb devices": listing}, failures={"install -r": 1}) as rec,
    ):
        android.device()
    assert rec.saw("uninstall", "com.optersoft.drive")
    assert rec.count("install") >= 2


# -- version.properties ----------------------------------------------------


def test_version_code_round_trips(tmp_path: Path):
    file = tmp_path / "version.properties"
    file.write_text("versionName=1.2.3\nversionCode=41\n")
    assert android.read_version("versionCode", file) == "41"
    android.write_version("versionCode", "42", file)
    assert file.read_text() == "versionName=1.2.3\nversionCode=42\n"


def test_bump_increments_and_commits(tmp_path: Path):
    module = tmp_path / "drive-android"
    module.mkdir()
    (module / "version.properties").write_text("versionCode=41\n")

    with context(root=tmp_path), record() as rec:
        assert android.bump() == 42

    assert (module / "version.properties").read_text() == "versionCode=42\n"
    assert rec.matched(r"git commit .* -m 'chore\(android\): bump versionCode 41 -> 42'")


def test_a_missing_version_field_is_a_clear_error(tmp_path: Path):
    file = tmp_path / "version.properties"
    file.write_text("versionName=1.0\n")
    with pytest.raises(MakeError, match="no versionCode in"):
        android.read_version("versionCode", file)


# -- publishing the web-download APK ---------------------------------------


def test_a_debuggable_apk_is_refused(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ANDROID_PUBLISH_HOST", "root@example")
    monkeypatch.setenv("ANDROID_PUBLISH_PATH", "/srv/app.apk")
    apk = tmp_path / "app.apk"
    apk.write_text("")

    manifest = "A: android:debuggable(0x0101000f)=(type 0x12)0xffffffff"
    with context(root=tmp_path), record(responses={"dump xmltree": manifest}) as rec:
        with pytest.raises(MakeError, match="refusing to publish"):
            android.publish_file(apk, tmp_path / "version.properties")
    assert not rec.saw("rsync")


def test_the_version_code_placeholder_is_substituted(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ANDROID_PUBLISH_HOST", "root@example")
    monkeypatch.setenv("ANDROID_PUBLISH_PATH", "/srv/assets/drive-{versionCode}.apk")
    monkeypatch.delenv("ANDROID_R2_BUCKET", raising=False)
    apk = tmp_path / "app.apk"
    apk.write_text("")
    versions = tmp_path / "version.properties"
    versions.write_text("versionCode=7\n")

    with context(root=tmp_path), record(responses={"dump xmltree": "package"}) as rec:
        android.publish_file(apk, versions)

    assert rec.matched(r"rsync -az .*app\.apk root@example:/srv/assets/drive-7\.apk\.new")
    # Older versioned siblings are pruned, so the directory holds exactly one.
    assert rec.argument_matched(r"for f in drive-\*\.apk")


def test_the_upload_is_atomic(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ANDROID_PUBLISH_HOST", "root@example")
    monkeypatch.setenv("ANDROID_PUBLISH_PATH", "/srv/app.apk")
    monkeypatch.delenv("ANDROID_R2_BUCKET", raising=False)
    apk = tmp_path / "app.apk"
    apk.write_text("")

    with context(root=tmp_path), record(responses={"dump xmltree": "package"}) as rec:
        android.publish_file(apk, tmp_path / "v.properties")

    assert rec.matched(r"rsync .*:/srv/app\.apk\.new")  # uploaded beside
    assert rec.argument_matched(r"mv -f 'app\.apk\.new' 'app\.apk'")  # then swapped in


# -- the rollout rule ------------------------------------------------------


def test_a_partial_rollout_stays_in_progress():
    """Play ignores --rollout unless the release stays inProgress."""
    assert play._rollout_flags(0.1) == ["--release_status", "inProgress", "--rollout", "0.1"]


def test_a_full_rollout_is_completed_with_no_fraction():
    """Reaching 1.0 must drop the fraction, or Play ignores the whole thing."""
    assert play._rollout_flags(1.0) == ["--release_status", "completed"]


def test_promote_rejects_an_out_of_range_rollout():
    with context(yes=True), record():
        with pytest.raises(MakeError, match="between 0 and 1"):
            play.promote(1.5)


def test_promote_is_confirm_gated():
    """It reaches that share of real users, so the gate is the runner's job."""
    with context(), record():
        with pytest.raises(Aborted):
            run_one(registry.require("play.promote"), [0.5])


# -- the test gate ---------------------------------------------------------


def test_the_gate_is_unimplemented_until_a_consumer_defines_it():
    """`just` had to leave this *undefined*, because duplicates across imports are fatal."""
    with context(yes=True), record():
        with pytest.raises(RecipeError) as caught:
            run_one(registry.require("play.test-gate"))
    assert "not implemented" in caught.value.message
    assert "override='play.test-gate'" in (caught.value.hint or "")


# -- the store listing -----------------------------------------------------


def _listing_tree(root: Path) -> None:
    for locale in ("en-US", "es-ES"):
        directory = root / "docs/store" / locale
        directory.mkdir(parents=True)
        (directory / "title.txt").write_text("Drive")
        (directory / "short-description.txt").write_text("short")
        (directory / "full-description.txt").write_text("full")
    phone = root / "docs/store/graphics/phone"
    phone.mkdir(parents=True)
    for name in ("b.png", "a.png"):
        (phone / name).write_text("")


def test_the_listing_tree_renames_files_the_way_supply_wants(tmp_path: Path):
    _listing_tree(tmp_path)
    with context(root=tmp_path), record():
        tree, has_images = play.build_listing(crop=False)

    assert (tree / "en-US" / "short_description.txt").read_text() == "short"
    assert (tree / "en-US" / "full_description.txt").read_text() == "full"
    assert not has_images  # no icon or feature graphic present


def test_screenshots_are_numbered_so_supply_keeps_the_order(tmp_path: Path):
    _listing_tree(tmp_path)
    with context(root=tmp_path), record():
        tree, _ = play.build_listing(crop=False)

    shots = sorted(p.name for p in (tree / "en-US/images/phoneScreenshots").iterdir())
    assert shots == ["01_a.png", "02_b.png"]


def test_an_absent_icon_means_images_are_skipped_not_wiped(tmp_path: Path, monkeypatch):
    _listing_tree(tmp_path)
    monkeypatch.setenv("PLAY_ACCOUNT_JSON", str(tmp_path / "sa.json"))
    (tmp_path / "sa.json").write_text("{}")

    with context(root=tmp_path), record() as rec:
        play.listing()

    # Without --skip_upload_images, supply would clear the Console's existing ones.
    assert rec.matched(r"--skip_upload_images")


def test_the_listing_is_staged_not_sent_by_default(tmp_path: Path, monkeypatch):
    _listing_tree(tmp_path)
    monkeypatch.setenv("PLAY_ACCOUNT_JSON", str(tmp_path / "sa.json"))
    (tmp_path / "sa.json").write_text("{}")

    with context(root=tmp_path), record() as rec:
        play.listing()
    assert rec.matched(r"--changes_not_sent_for_review true")

    with context(root=tmp_path), record() as rec:
        play.listing(mode="send")
    assert not rec.matched(r"--changes_not_sent_for_review")


def test_a_missing_listing_file_names_the_path(tmp_path: Path):
    (tmp_path / "docs/store/en-US").mkdir(parents=True)
    with context(root=tmp_path), record():
        with pytest.raises(MakeError, match="missing listing file"):
            play.build_listing()
