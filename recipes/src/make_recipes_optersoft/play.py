"""The `play` group: ship to Google Play, ramp the rollout, sync the listing.

Ported from `play.just` plus `play/metadata.sh`, `play/promote.sh` and
`play/reviews.py`. Three things the shell version had to leave to convention are
now structural:

* **The test gate.** `just` errors on duplicate recipes across imports, so a
  shared no-op default could never be overridden -- the gate was therefore left
  *undefined*, and a missing one surfaced as a parse error. Here it is
  `abstract=True`: it lists as unimplemented and refuses to run with a message
  saying what to write.
* **Confirmation.** `promote.sh` re-implemented a `--yes` check because nothing
  else could. `dangerous=True` is the runner's job.
* **The rollout fraction.** It was a string compared against `"1"` and `"1.0"`.
  It is a float, and the "Play ignores `--rollout` unless the release stays
  inProgress" rule lives in one function.

Halting a rollout stays a Play Console action, and the first upload of a new
package stays manual -- both deliberately outside this file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from make import config, env, fs, group, note, path, recipe, sh, step, warn
from make.errors import MakeError

from . import android
from .android import Android

play = group("play")

CONSOLE = "https://play.google.com/console"


@config.section("play")
@dataclass
class Play:
    """Per-project Play configuration."""

    store_dir: Path = Path("docs/store")
    """Source of the store listing (titles, descriptions, graphics)."""

    locales: list[str] = field(default_factory=list)
    """Play locales, e.g. ["en-US", "es-ES"]."""

    metadata_dir: Path = Path(".play-metadata")
    """Gitignored build tree handed to `fastlane supply`."""


def account_json() -> str:
    """Path to the Play service-account JSON, from the layered secrets."""
    env.layered()
    value = env.require(
        "PLAY_ACCOUNT_JSON",
        hint="usually global: one service account can serve several apps, granted "
        "per-app in the Play Console",
    )
    if not Path(value).is_file():
        raise MakeError(f"PLAY_ACCOUNT_JSON points at a file that does not exist: {value}")
    return value


def _rollout_flags(rollout: float) -> list[str]:
    """Play ignores `--rollout` unless the release stays `inProgress`.

    Reaching 1.0 must therefore be sent as `completed` with no fraction at all,
    which is why this is one function rather than a condition at each call site.
    """
    if rollout >= 1.0:
        return ["--release_status", "completed"]
    return ["--release_status", "inProgress", "--rollout", str(rollout)]


@play.recipe(name="test-gate", abstract=True)
def test_gate() -> None:
    """Verification every release must pass. Each consumer defines this.

    Gate on the repo's own suites, or override it with an empty body to state
    explicitly that releases are ungated.
    """


@recipe(
    group="play",
    name="publish",
    aliases=["android-release"],
    needs=[test_gate],
    requires=["fastlane"],
    dangerous=True,
)
def publish(version: str, rollout: float = 0.1, *, track: str = "production") -> None:
    """Release to Play: build the signed AAB and upload it with fastlane supply.

    Ships at the CURRENT versionCode -- run `make android.bump` first, because
    Play rejects a reused one.

    Args:
        version: versionName to stamp, e.g. 1.2.3
        rollout: fraction of production users; 1.0 ships to everyone at once
        track: Play track; closed tracks do not stage
    """
    json_key = account_json()
    android.keystore()

    android.write_version("versionName", version)
    step(f"building versionName={version} versionCode={android.read_version('versionCode')}")
    android.gradle(f":{Android.module}:bundleRelease", extra=["--no-daemon"])

    if rollout >= 1.0:
        step(f"uploading to {track} at 100% (completed)")
    else:
        step(f"uploading to {track} as a staged rollout at {rollout}")

    sh(
        "fastlane",
        "supply",
        "--aab",
        android.release_aab(),
        "--track",
        track,
        *_rollout_flags(rollout),
        "--json_key",
        json_key,
        "--package_name",
        Android.pkg,
    )

    code = android.read_version("versionCode")
    note(f"released v{version} (versionCode {code}) to {track} at rollout {rollout}")
    note("ramp with `make play.promote 0.5` then `make play.promote 1.0`")
    warn("the versionName edit is uncommitted; run `make android.bump` before the next release")


@play.recipe(name="promote", requires=["fastlane"], dangerous=True)
def promote(rollout: float) -> None:
    """Raise the in-progress PRODUCTION staged rollout. Reaches real users.

    Args:
        rollout: fraction between 0 and 1; 1.0 completes the rollout
    """
    if not 0 < rollout <= 1:
        raise MakeError(f"rollout must be between 0 and 1, got {rollout}")
    json_key = account_json()
    step(f"raising the production rollout of {Android.pkg} to {rollout}")
    sh("fastlane", "supply", "--track", "production", *_rollout_flags(rollout), *_skip_everything(json_key))
    note("production rollout completed (100%)" if rollout >= 1.0 else f"production rollout now {rollout}")


@play.recipe(name="promote-track", requires=["fastlane"], dangerous=True)
def promote_track(source: str, *, to: str = "production", rollout: float = 0.1) -> None:
    """Promote a release from one track to another.

    There is no default source track on purpose: a stale default is a trap, and
    drive's `internal` track is dead.

    Args:
        source: the track to promote from
        to: the track to promote into
        rollout: fraction of users on the destination track
    """
    json_key = account_json()
    if to == "production":
        warn("this publishes to PRODUCTION (real users)")
    step(f"promoting {Android.pkg}: {source} -> {to} at rollout {rollout}")
    sh(
        "fastlane",
        "supply",
        "--track",
        source,
        "--track_promote_to",
        to,
        "--rollout",
        str(rollout),
        *_skip_everything(json_key),
    )


def _skip_everything(json_key: str) -> list[str]:
    """Upload nothing but the track change."""
    return [
        "--package_name",
        Android.pkg,
        "--json_key",
        json_key,
        "--skip_upload_aab",
        "--skip_upload_apk",
        "--skip_upload_metadata",
        "--skip_upload_images",
        "--skip_upload_screenshots",
        "--skip_upload_changelogs",
    ]


# --------------------------------------------------------------------------
# Store listing
# --------------------------------------------------------------------------


def _crop_to_2to1(source: Path, destination: Path) -> None:
    """Crop a phone capture to <=2:1, which some Play checks require.

    Phone captures are often native 20:9 (1080x2400). Play generally accepts
    modern aspect ratios; this is the fallback for when it does not.
    """
    if sh.which("sips") and sh.ok("sips", "-c", "2160", "1080", source, "--out", destination):
        return
    if sh.which("magick") and sh.ok(
        "magick", source, "-gravity", "center", "-crop", "1080x2160+0+0", "+repage", destination
    ):
        return
    if sh.which("convert") and sh.ok(
        "convert", source, "-gravity", "center", "-crop", "1080x2160+0+0", "+repage", destination
    ):
        return
    fs.copy(source, destination)
    warn(f"no sips/magick/convert -- kept the native aspect ratio for {destination.name}")


def build_listing(*, crop: bool = True) -> tuple[Path, bool]:
    """Assemble the `fastlane supply` metadata tree. -> (tree, has_images)."""
    source = path(Play.store_dir)
    out = path(Play.metadata_dir) / "android"
    if not Play.locales:
        raise MakeError(
            "play.locales is empty", hint='set it, e.g. play.configure(locales=["en-US", "es-ES"])'
        )

    if path(Play.metadata_dir).exists():
        fs.rmtree(path(Play.metadata_dir))

    icon = source / "graphics/icon-512.png"
    feature = source / "graphics/feature-graphic.png"
    has_images = False

    for locale in Play.locales:
        target = out / locale
        fs.mkdir(target / "images/phoneScreenshots")
        for name, destination in (
            ("title.txt", "title.txt"),
            ("short-description.txt", "short_description.txt"),  # supply wants underscores
            ("full-description.txt", "full_description.txt"),
        ):
            origin = source / locale / name
            if not origin.is_file():
                raise MakeError(f"missing listing file: {origin}")
            fs.copy(origin, target / destination)

        # Numbered so supply keeps the order.
        for index, shot in enumerate(sorted((source / "graphics/phone").glob("*.png")), start=1):
            destination = target / "images/phoneScreenshots" / f"{index:02d}_{shot.name}"
            if crop:
                _crop_to_2to1(shot, destination)
            else:
                fs.copy(shot, destination)

        # Only push an icon or feature graphic we actually have -- otherwise the
        # ones already in the Console must be left alone.
        if icon.is_file():
            fs.copy(icon, target / "images/icon.png")
            has_images = True
        if feature.is_file():
            fs.copy(feature, target / "images/featureGraphic.png")
            has_images = True

    note(f"built listing tree -> {out}")
    for file in sorted(out.rglob("*")):
        if file.is_file():
            note(f"  {file.relative_to(out)}")
    return out, has_images


@play.recipe(name="listing", requires=["fastlane"])
def listing(*, mode: Literal["stage", "send", "dry-run", "build-only"] = "stage", crop: bool = True) -> None:
    """Push the store listing: titles, descriptions and phone screenshots.

    Staged by default, so a human presses "Send for review" in the Console.
    Listing changes go through Play's review and do not need a new app version.

    Projected-car screenshots are not synced -- `supply` has no car image type,
    so those stay a manual upload in the Console.

    Args:
        mode: stage (default), send (submit for review), dry-run, build-only
        crop: crop phone screenshots to <=2:1
    """
    tree, has_images = build_listing(crop=crop)
    if mode == "build-only":
        note("build-only: nothing pushed")
        return

    json_key = account_json()
    args = [
        "--package_name",
        Android.pkg,
        "--json_key",
        json_key,
        "--metadata_path",
        str(tree),
        "--skip_upload_aab",
        "--skip_upload_apk",
        "--skip_upload_changelogs",
    ]
    if mode != "send":
        args += ["--changes_not_sent_for_review", "true"]
    if not has_images:
        args.append("--skip_upload_images")  # never wipe the Console's icon/feature
    if mode == "dry-run":
        args.append("--validate_only")

    sh("fastlane", "supply", *args)

    console = env.get("PLAY_CONSOLE_URL") or CONSOLE
    if mode == "send":
        note(f"listing submitted for review -- goes live after Google approves. {console}")
    else:
        note(f"listing synced and STAGED -- not yet sent for review. Review and send: {console}")


@play.recipe(name="listing-publish", requires=["fastlane"], dangerous=True)
def listing_publish() -> None:
    """Push the listing AND submit it for review."""
    listing(mode="send")


@play.recipe(name="reviews")
def reviews(maximum: int = 20) -> list[dict]:
    """Read recent Play reviews (read-only).

    Play only returns reviews from roughly the last week, and only for apps that
    have production reviews at all.
    """
    from .play_reviews import fetch

    json_key = account_json()
    found = fetch(Android.pkg, json_key, maximum)
    for review in found:
        stars = "*" * review["stars"]
        print(f"{stars:<5} {review['author']}  {review['when']}")
        if review["text"]:
            print(f"      {review['text']}")
    if not found:
        note("no reviews in the window Play exposes (about one week)")
    return found
