"""The `android` group: device builds, signing, and the web-download APK.

Ported from `android.just`. The shell version resolved the SDK with a `${VAR:-default}`
in every recipe, picked a device with `awk`, found `apksigner`/`aapt2` with
`ls ... | sort | tail -1`, and re-sourced the secrets four times. Those are all
one function here, each with a test.

The Google Play pipeline is the sibling `play` module, which builds on
`keystore()`, `bump()` and `aab()` from this one.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from make import config, env, fs, group, note, path, sh, step, warn
from make.errors import MakeError

android = group("android")

#: Where the SDK lives when `ANDROID_HOME` is unset -- the Homebrew commandlinetools path.
DEFAULT_SDK = Path("/opt/homebrew/share/android-commandlinetools")


@config.section("android")
@dataclass
class Android:
    """Per-project Android configuration.

    `module` is the Gradle module name, its folder, *and* the APK base name --
    all the same string, as in the original.
    """

    module: str
    pkg: str
    gradle_flags: list[str] = field(default_factory=list)
    install_flags: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# SDK plumbing
# --------------------------------------------------------------------------


def sdk_root() -> Path:
    return Path(env.get("ANDROID_HOME") or DEFAULT_SDK)


def adb() -> str:
    bundled = sdk_root() / "platform-tools" / "adb"
    if not bundled.exists() and sh.which("adb"):
        return "adb"
    return str(bundled)


def build_tool(name: str) -> str:
    """The newest `build-tools/*/<name>`, matching the shell's `sort | tail -1`."""
    candidates = sorted((sdk_root() / "build-tools").glob(f"*/{name}"))
    if not candidates:
        raise MakeError(
            f"{name} not found under {sdk_root()}/build-tools",
            hint="install the Android build tools, or set ANDROID_HOME",
        )
    return str(candidates[-1])


def physical_device() -> str:
    """The first attached USB device, skipping emulators.

    Explicitly skipping emulators is what lets this work while one is running --
    the reason `android-device` exists alongside a repo's `android-emulator`.
    """
    listing = sh.out(adb(), "devices", dry="SERIAL\tdevice")
    for line in listing.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device" and not parts[0].startswith("emulator-"):
            return parts[0]
    raise MakeError(
        "no USB device attached", hint="check `adb devices` and that USB debugging is enabled on the phone"
    )


def module_path() -> Path:
    return path(Android.module)


def release_apk() -> Path:
    return module_path() / "build/outputs/apk/release" / f"{Android.module}-release.apk"


def debug_apk() -> Path:
    return module_path() / "build/outputs/apk/debug" / f"{Android.module}-debug.apk"


def release_aab() -> Path:
    return module_path() / "build/outputs/bundle/release" / f"{Android.module}-release.aab"


def version_file() -> Path:
    return module_path() / "version.properties"


def read_version(name: str = "versionCode", file: Path | None = None) -> str:
    """Read one field out of `version.properties` -- the source of truth for releases."""
    target = file or version_file()
    if not target.is_file():
        raise MakeError(f"{target} does not exist")
    match = re.search(rf"^{name}=(.*)$", target.read_text(encoding="utf-8"), re.MULTILINE)
    if match is None:
        raise MakeError(f"no {name} in {target}")
    return match.group(1).strip()


def write_version(name: str, value: str, file: Path | None = None) -> None:
    target = file or version_file()
    text = target.read_text(encoding="utf-8")
    updated, count = re.subn(rf"^{name}=.*$", f"{name}={value}", text, flags=re.MULTILINE)
    if not count:
        raise MakeError(f"no {name} line in {target} to update")
    fs.write(target, updated)


def gradle(*tasks: str, extra: list[str] | None = None) -> None:
    sh("./gradlew", *tasks, *Android.gradle_flags, *(extra or []))


# --------------------------------------------------------------------------
# Recipes
# --------------------------------------------------------------------------


@android.recipe(name="device")
def device() -> None:
    """Build, install and launch the debug app on the attached USB device."""
    serial = physical_device()
    step(f"device {serial}")
    tool = adb()
    gradle(f":{Android.module}:assembleDebug")
    apk = debug_apk()

    # -r reinstalls keeping data. On a signature clash (a release build already
    # installed) the only fix is uninstall + reinstall, which wipes app data.
    installed = sh(tool, "-s", serial, "install", "-r", *Android.install_flags, "-t", apk, check=False)
    if not installed.ok:
        warn("reinstall failed (signature clash?) -- uninstalling first, which wipes app data")
        sh(tool, "-s", serial, "uninstall", Android.pkg, check=False)
        sh(tool, "-s", serial, "install", *Android.install_flags, "-t", apk)

    sh(
        tool,
        "-s",
        serial,
        "shell",
        "monkey",
        "-p",
        Android.pkg,
        "-c",
        "android.intent.category.LAUNCHER",
        "1",
    )


@android.recipe(name="device-debug", needs=[device])
def device_debug() -> None:
    """Install, launch, then stream only this app's logcat (Ctrl-C to stop)."""
    serial = physical_device()
    tool = adb()
    pid = ""
    for _ in range(20):
        pid = sh.out(tool, "-s", serial, "shell", "pidof", "-s", Android.pkg, check=False, dry="1234").strip()
        if pid:
            break
        time.sleep(0.5)
    if not pid:
        raise MakeError(f"{Android.pkg} did not start (no process found after 10s)")
    step(f"logcat for {Android.pkg} (pid {pid}) -- Ctrl-C to stop")
    sh.replace_process(tool, "-s", serial, "logcat", f"--pid={pid}")


@android.recipe(name="keystore", hidden=True)
def keystore() -> Path:
    """Write the gitignored `<module>/keystore.properties` from the layered secrets."""
    env.layered()
    store_password = env.require("KEYSTORE_PASSWORD")
    key_password = env.require("KEY_PASSWORD")
    store_name = env.require("ANDROID_KEYSTORE", hint="the keystore .jks basename under ~/.make/")
    alias = env.require("ANDROID_KEY_ALIAS")

    store_path = env.config_dir() / store_name
    if not store_path.is_file():
        legacy = Path.home() / ".just" / store_name
        if legacy.is_file():
            store_path = legacy
        else:
            raise MakeError(f"keystore not found: {store_path}")

    target = module_path() / "keystore.properties"
    fs.write(
        target,
        f"storeFile={store_path}\n"
        f"storePassword={store_password}\n"
        f"keyAlias={alias}\n"
        f"keyPassword={key_password}\n",
    )
    return target


@android.recipe(name="build", needs=[keystore])
def build() -> Path:
    """Build the signed release APK, verifying the signature before returning."""
    gradle(f":{Android.module}:assembleRelease")
    apk = release_apk()
    apksigner = build_tool("apksigner")
    if not sh.ok(apksigner, "verify", apk):
        raise MakeError(
            f"the release APK is NOT signed: {apk}",
            hint="check KEYSTORE_PASSWORD / ANDROID_KEYSTORE in your secrets, then rebuild",
        )
    certs = sh.out(apksigner, "verify", "--print-certs", apk, check=False, dry="certificate DN: CN=dev")
    signer = next((line for line in certs.splitlines() if "certificate DN" in line), "signed")
    note(f"signed: {signer.strip()}")
    return apk


@android.recipe(name="bump", requires=["git"])
def bump() -> int:
    """Increment versionCode in version.properties and commit it.

    Run before every Play release: Play rejects a reused versionCode, and the
    committed file is the single source of truth.
    """
    current = int(read_version("versionCode"))
    following = current + 1
    write_version("versionCode", str(following))
    sh(
        "git",
        "commit",
        str(version_file()),
        "-m",
        f"chore(android): bump versionCode {current} -> {following}",
    )
    note(f"versionCode bumped: {current} -> {following}")
    return following


@android.recipe(name="aab", needs=[keystore])
def aab() -> Path:
    """Build the signed release AAB locally. No upload -- that is `play.publish`."""
    gradle(f":{Android.module}:bundleRelease", extra=["--no-daemon"])
    note(f"AAB: {release_aab()}")
    return release_aab()


@android.recipe(name="publish-file", hidden=True, requires=["ssh", "rsync"])
def publish_file(apk: Path, version_properties: Path) -> None:
    """Deploy an already-signed release APK to the app VM.

    Kept separate from the Gradle build, and taking paths as *arguments*, so a
    repo whose Gradle project is not at the repo root (xtec's dx-generated one
    under `target/dx/`) can build its own way and still use this tail. In `just`
    that had to be arguments rather than variables because an importing file
    cannot override an imported variable; here it is simply a function.
    """
    env.layered()
    host = env.require("ANDROID_PUBLISH_HOST")
    path_template = env.require("ANDROID_PUBLISH_PATH")

    destination = path_template
    if "{versionCode}" in path_template:
        code = read_version("versionCode", version_properties)
        destination = path_template.replace("{versionCode}", code)

    # A debuggable APK is a security risk and trips Play Protect and Wallet
    # integrity checks. Refuse rather than warn.
    manifest = sh.out(
        build_tool("aapt2"), "dump", "xmltree", apk, "--file", "AndroidManifest.xml", dry="package"
    )
    if "debuggable" in manifest.lower():
        raise MakeError(f"refusing to publish: {apk} is debuggable")

    certs = sh.out(build_tool("apksigner"), "verify", "--print-certs", apk, check=False, dry="")
    signer = next((line for line in certs.splitlines() if "certificate DN" in line), "?")
    note(f"non-debuggable; signer: {signer.strip()}")

    directory, base = str(Path(destination).parent), Path(destination).name
    step(f"deploying -> {host}:{destination}")
    sh("ssh", host, f"mkdir -p '{directory}'")
    sh("rsync", "-az", apk, f"{host}:{destination}.new")
    sh("ssh", host, f"cd '{directory}' && mv -f '{base}.new' '{base}' && ls -la '{base}'")

    if "{versionCode}" in path_template:
        # Keep exactly this upload; older versioned siblings are dead weight.
        glob = Path(path_template.replace("{versionCode}", "*")).name
        sh(
            "ssh",
            host,
            f"shopt -s nullglob; cd '{directory}' && for f in {glob}; do "
            f'[ "$f" = \'{base}\' ] || rm -f -- "$f"; done',
        )

    bucket = env.get("ANDROID_R2_BUCKET")
    if bucket:
        step(f"pushing -> R2 {bucket}/app.apk")
        sh(
            "wrangler",
            "r2",
            "object",
            "put",
            f"{bucket}/app.apk",
            f"--file={apk}",
            "--content-type=application/vnd.android.package-archive",
            "--cache-control=no-cache",
            "--remote",
        )
    note("done -- served at the app's /app.apk")


@android.recipe(name="publish")
def publish() -> None:
    """Build the signed release APK and publish it as the web download."""
    apk = build()
    publish_file(apk, version_file())
