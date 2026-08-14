"""Recipes for the `drive` repo.

Replaces `justfile` (551 lines) outright -- `make` is the only runner here now.
The shared halves come from `make_recipes_optersoft` as a pinned dependency
rather than a `git clone` into `.just-shared/`; what stays below is what is
genuinely drive's: the KMP/Compose surfaces, the Rust C-ABI cores, the R2 edge
worker, and the backend build/deploy pipeline.

Where things went, for muscle memory:

    just server-dev            ->  make web.start        (shared; :8002 now, not :8080)
    just web-css               ->  make web.tailwind
    just web-dx-sources        ->  make web.tailwind-sources
    just server-build          ->  make server.build
    just server-deploy         ->  make server.deploy
    just web-deploy            ->  make cdn.deploy       (it ships the WORKER, not /app)
    just web-app-deploy        ->  make webapp.deploy
    just web-debug             ->  make webapp.debug
    just site-dev / site-tail  ->  make cdn.dev / cdn.tail
    just shared-rust*          ->  make rust.abi / rust.host / rust.wasm
    just test-ios / test-web   ->  make test.ios / test.web
    just android-*             ->  make android.*        (mostly shared)

`make` with no arguments lists everything with its signature; `make --doctor`
checks every declared tool.
"""

# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "mkrun>=0.1",
#   "make-recipes-optersoft @ git+ssh://git@github.com/optersoft/make.git#subdirectory=recipes",
# ]
# ///
#
# The runner comes from PyPI; the recipes are private, so ssh. `make --sync`
# pins both into Makefile.py.lock -- a version for mkrun, an exact commit for
# the recipes -- which is versioned in a way `.just-shared/` never was.
# `make --sync --upgrade` is how a pin moves.

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from make import MakeError, arg, ctx, env, fs, note, recipe, sh, step
from make_recipes_optersoft import android, box, database, play, web  # noqa: F401 -- importing registers

# The variables that used to sit above each `import?`, now typed and checked.
android.Android.configure(module="drive-android", pkg="com.optersoft.drive")

play.Play.configure(store_dir="docs/store", locales=["en-US", "es-ES", "fr-FR", "pt-PT"])

# `serve_dir` is drive-web because `dx serve` must run inside the crate. The port
# is drive's slot in the fleet table (broker 8001, drive 8002, academy 8003,
# alma 8005) -- `just server-dev` took dx's default :8080, which is also
# `webapp.debug`'s webpack port. `watch` is only swept for `.!*!*` atomic-write
# leftovers, so it lists the Rust crates rather than everything.
web.Web.configure(
    bin="drive-web",
    port=8002,
    ready="/",
    serve_dir="drive-web",
    serve_flags=["--fullstack"],
    watch=["drive-web", "drive-server-api", "drive-web-dashboard", "drive-server-radar"],
    css_in="drive-web/assets/tailwind.input.css",
    css_out="drive-web/assets/tailwind.css",
)

#: drive's VM (nbg-3.optersoft.com). Pinned to the IP so a deploy does not
#: depend on DNS, or on the gateway being up.
VM = "root@178.104.139.176"

#: Where the VM keeps the served assets: the /app bundle and app.apk.
ASSETS = "/home/drive/data/assets"

Host = Annotated[str, arg(env="HOST", help="target VM, as user@host")]
Assets = Annotated[str, arg(env="ASSETS", help="asset root on the VM")]


# --------------------------------------------------------------------------
# Play release gate
# --------------------------------------------------------------------------


@recipe(override="play.test-gate")
def test_gate() -> None:
    """Block releases on the unit and Android Auto suites."""
    android_test()
    android_test_auto()


# --------------------------------------------------------------------------
# android -- the shared group provides build / bump / aab / publish / device
# --------------------------------------------------------------------------


@recipe(group="android", name="test", requires=["./gradlew"])
def android_test() -> None:
    """Shared common + Android host tests."""
    sh("./gradlew", ":drive-shared-core:testAndroidHostTest")


@recipe(group="android", name="test-auto", requires=["./gradlew"])
def android_test_auto() -> None:
    """Android Auto car-app tests on the JVM via Robolectric -- no emulator, head unit or KVM.

    Drives the real CarAppService/Session/Screen launch path plus the
    Play-review "no GPS / no location permission" conditions, and asserts the
    NavigationTemplate builds without crashing. The cheap local stand-in for the
    AAOS emulator; see docs/testing.md.
    """
    sh("./gradlew", ":drive-android:testDebugUnitTest", "--tests", "com.optersoft.drive.car.*")


@recipe(group="android", name="publish-gated", needs=[android_test, android_test_auto])
def android_publish_gated() -> None:
    """Publish the web-download APK with the test suites in front.

    The shared `android.publish` is ungated on purpose -- other repos gate it
    differently -- so the gate lives here.
    """
    android.publish()


@recipe(group="android", name="emulator")
def android_emulator() -> None:
    """Boot a phone emulator, install and launch the app, and leave it running.

    The dev counterpart to `android.test-emulator`, which asserts and tears
    down. Builds the debug APK if it is missing.

    The emulator primitives stay in `.just/lib/emulator.sh` -- a shell library
    shared with `.just/release.sh` -- so this recipe is deliberately still bash.
    `set -uo pipefail` without `-e`: the body handles its own failures with
    `|| exit 2` and distinguishes between them.
    """
    sh.bash(
        r"""
        set -uo pipefail
        . .just/lib/emulator.sh
        emu_require || exit 2
        APK="drive-android/build/outputs/apk/debug/drive-android-debug.apk"
        [ -f "$APK" ] || ./gradlew -q :drive-android:assembleDebug || exit 2
        ensure_avd ci_phone "$PHONE_IMG" pixel_6 || { echo "create AVD failed" >&2; exit 2; }
        EMU_WINDOW=1   # interactive window + audio, so you can see the app and hear alerts
        S="$(boot_emulator ci_phone)" || { echo "emulator boot timeout" >&2; exit 2; }
        echo "  • booted $S"
        "$ADB" -s "$S" uninstall com.optersoft.drive >/dev/null 2>&1 || true
        "$ADB" -s "$S" install -r "$APK" >/dev/null 2>&1 || { echo "APK install failed" >&2; exit 2; }
        "$ADB" -s "$S" shell pm grant com.optersoft.drive android.permission.ACCESS_FINE_LOCATION 2>/dev/null
        "$ADB" -s "$S" emu geo fix 2.1770 41.3825 >/dev/null 2>&1   # Barcelona (lon lat)
        "$ADB" -s "$S" shell am start -n com.optersoft.drive/.MainActivity >/dev/null 2>&1
        echo "  ✓ app launched on $S — emulator left running ('$ADB emu kill' to stop it)"
        """,
        strict=False,
    )


@recipe(group="android", name="emulator-auto")
def android_emulator_auto() -> None:
    """Boot the Android Auto (AAOS) head-unit emulator in a window and leave it running.

    Its own AVD (`drive_car`), so an `android.test-emulator-auto` sweep over
    `ci_car` never touches it. Re-runnable: a prior interactive instance is
    replaced. The printed commands flip day/night and start the AUTO_DRIVE demo.
    """
    sh.bash(
        r"""
        set -uo pipefail
        . .just/lib/emulator.sh
        emu_require || exit 2
        PKG=com.optersoft.drive
        APK="drive-android/build/outputs/apk/debug/drive-android-debug.apk"
        [ -f "$APK" ] || ./gradlew -q :drive-android:assembleDebug || exit 2
        ensure_avd drive_car "$AAOS_IMG" automotive_1024p_landscape || { echo "create AVD failed" >&2; exit 2; }
        pkill -f "[-]avd drive_car" 2>/dev/null; sleep 2
        EMU_WINDOW=1   # interactive window + host GPU, so you can see and click the head unit
        S="$(boot_emulator drive_car)" || { echo "AAOS boot timeout" >&2; exit 2; }
        echo "  • booted $S"
        "$ADB" -s "$S" uninstall "$PKG" >/dev/null 2>&1 || true
        "$ADB" -s "$S" install -r "$APK" >/dev/null 2>&1 || { echo "APK install failed" >&2; exit 2; }
        "$ADB" -s "$S" shell pm grant "$PKG" android.permission.ACCESS_FINE_LOCATION 2>/dev/null
        "$ADB" -s "$S" shell cmd location set-location-enabled true 2>/dev/null
        "$ADB" -s "$S" shell settings put secure location_mode 3 >/dev/null 2>&1
        "$ADB" -s "$S" emu geo fix 2.1686 41.3874 >/dev/null 2>&1   # Barcelona (lon lat)
        "$ADB" -s "$S" shell am start -n "$PKG/androidx.car.app.activity.CarAppActivity" >/dev/null 2>&1
        echo "  ✓ Drive car app launched on $S — window left open. Drive it with:"
        echo "      day / night : $ADB -s $S shell cmd car_service day-night-mode day|night|sensor"
        echo "      demo drive  : $ADB -s $S shell dumpsys activity service $PKG/.car.DriveCarAppService AUTO_DRIVE"
        echo "      (tap the map to reveal the Navigate / start-stop / speed action strip)"
        echo "      stop        : $ADB -s $S emu kill"
        """,
        strict=False,
    )


@recipe(group="android", name="test-emulator")
def android_test_emulator(*, all_surfaces: bool = False) -> None:
    """Emulator UI suite: boot, install the debug APK, assert map + datasets + /geocode.

    Builds the APK if missing.

    Args:
        all_surfaces: also run the Android Auto (AAOS) surface -- slower, boots
            a second emulator. This was `just android-test-emulator-all`.
    """
    sh(".just/emulator-test.sh", "--all" if all_surfaces else "--phone")


@recipe(group="android", name="test-emulator-auto")
def android_test_emulator_auto(*, apk: Path | None = None, keep: bool = False) -> None:
    """Android Auto DEEP test on the AAOS emulator -- the head-unit OS itself.

    Exercises the Auto App Quality findings drive was reviewed against:
    CarAppActivity renders, the day/night map flip (MR-1) via
    `cmd car_service day-night-mode`, and the demo drive (NF-7) via
    `dumpsys ... AUTO_DRIVE`. Asserts on crashes and frame changes, and saves
    labelled screenshots to docs/test/screenshots/car-auto-*.png.

    Needs the DEBUG APK -- only the debug manifest overlay adds CarAppActivity,
    the AAOS entry point -- and builds it if missing.

    Args:
        apk: test this APK instead of the debug build.
        keep: leave the emulator running afterwards.
    """
    sh(".just/emulator-test-auto.sh", *(["--apk", str(apk)] if apk else []), *(["--keep"] if keep else []))


@recipe(group="android", name="test-firebase", needs=["android.build"], requires=["gcloud"])
def android_test_firebase(*, apk: Path | None = None, wait: bool = True) -> None:
    """Firebase Test Lab Robo crawl on real + virtual devices (cloud, run from here -- no CI).

    A launch/crash/smoke gate catching device/ABI/OS-specific crashes the local
    emulator cannot: Robo auto-crawls the UI with no test code, reporting
    crashes, ANRs and native-.so-load failures with a video and logcat. It does
    NOT inject a GPS route.

    Crawls the SIGNED RELEASE APK from `android.build` -- what Play serves, so
    R8/minify and the release-only host-validator path are exercised.

    Free tier (Spark) allows 10 virtual + 5 physical runs/day; the default of one
    each stays well under. Export `DEVICES` for a wider matrix, after
    `gcloud firebase test android models list` -- the ids drift and not all are
    free-tier.

    Args:
        apk: crawl this APK instead of the release build.
        wait: block until the crawl finishes. `--no-wait` submits and returns.
    """
    # Config comes from the repo-root .env, which `just` auto-loaded via
    # `set dotenv-load`. env.layered() reads ~/.make/secrets.env, then
    # ~/.make/drive.env, then ./.env -- so the same file still supplies these.
    env.layered()
    project = env.require("GCP_PROJECT_ID", hint="set GCP_PROJECT_ID in the repo-root .env")

    target = apk or Path("drive-android/build/outputs/apk/release/drive-android-release.apk")
    if not target.is_file():
        raise MakeError(f"APK not found: {target}", hint="`android.build` should have produced it")
    step(f"APK: {target}")

    # Free-tier Test Lab writes results to a Google-managed bucket only a *user*
    # account can write to -- a plain service account gets 403 there, and using
    # your own bucket needs billing enabled. So with no SA configured, use the
    # already-logged-in gcloud user. Set GCP_TEST_LAB_SA_JSON only on Blaze,
    # together with GCP_TEST_LAB_BUCKET.
    service_account = env.get("GCP_TEST_LAB_SA_JSON")
    if service_account:
        if not Path(service_account).is_file():
            raise MakeError(f"GCP_TEST_LAB_SA_JSON points at a missing file: {service_account}")
        step(f"authenticating service account on project {project}")
        sh("gcloud", "auth", "activate-service-account", "--key-file", service_account, "--quiet")
    else:
        accounts = [
            line
            for line in sh.lines(
                "gcloud", "auth", "list", "--filter=status:ACTIVE", "--format=value(account)", dry=()
            )
            if line and "gserviceaccount.com" not in line
        ]
        if not accounts:
            raise MakeError("no user account logged in", hint="run: gcloud auth login")
        step(f"using user account {accounts[0]} on project {project} (free-tier default bucket)")
        sh("gcloud", "config", "set", "account", accounts[0], "--quiet")
    sh("gcloud", "config", "set", "project", project, "--quiet")

    devices = (
        env.get("DEVICES")
        or "--device model=MediumPhone.arm,version=34,locale=es,orientation=portrait"
        " --device model=shiba,version=34,locale=es,orientation=portrait"
    ).split()
    bucket = env.get("GCP_TEST_LAB_BUCKET")

    sh(
        "gcloud",
        "firebase",
        "test",
        "android",
        "run",
        "--type",
        "robo",
        "--app",
        target,
        "--timeout",
        "180s",
        "--no-async" if wait else "--async",
        *(["--results-bucket", bucket] if bucket else []),
        *devices,
    )


# --------------------------------------------------------------------------
# server -- the one-binary Dioxus 0.7 fullstack VM app (drive-web)
# --------------------------------------------------------------------------
#
# The hot-reload dev server is the shared `web.start` (dx serve --fullstack,
# with SSR + wasm hydration for /admin, a Tailwind watcher and a dev Chrome).


@recipe(group="server", name="start", requires=["cargo"])
def server_start() -> None:
    """Run the whole VM app with plain `cargo run` -- SSR, /admin, and the nested API.

    No wasm client bundle, so /admin pages do not hydrate; use `web.start` for
    that. For the API alone: `cd drive-server-api && cargo run`.
    """
    sh("cargo", "run", "--features", "server", cwd="drive-web")


@recipe(group="server", name="test", requires=["cargo"])
def server_test() -> None:
    """Backend tests -- parsers, geo, PMTiles, style.json."""
    sh("cargo", "test", cwd="drive-server-api")


@recipe(group="server", name="build", requires=["docker", "file"])
def server_build() -> Path:
    """Build the prod linux/amd64 binary + its public/ hydration bundle, via Docker.

    Docker rather than a cross-build because `dx build --fullstack` must run
    without the @client/@server `--target` split, which drops the SSR hydration
    data. On Apple Silicon the linux/amd64 compile is emulated; the cache mounts
    keep re-runs incremental.

    No `--ssh` since 2026-08-13: the shared optersoft crates are PATH deps into
    sibling repos, so nothing private is fetched in-container. Each sibling is
    handed to BuildKit as a NAMED BUILD CONTEXT and copied to /<repo> by the
    Dockerfile, mirroring the ~/optersoft layout so `path = "../../dioxus/..."`
    resolves. Whole repos, because the crates use `version.workspace = true` and
    cargo validates every workspace member: hive brings ecdysis, turso-replica
    brings turso-share + hetzner-box.

    ⚠️ This builds the siblings' WORKING TREES -- uncommitted edits in
    ~/optersoft/hive ship to prod.
    """
    # Build context is the repo ROOT: drive-server-api sits there, while
    # drive-web nests it + dashboard + radar via path deps, so the context must
    # span both. The root .dockerignore trims the KMP crates and build output.
    dist = ctx.root / "drive-web/target/release-deploy"
    siblings = ["dioxus", "hive", "ecdysis", "turso", "hetzner", "axum"]
    missing = [name for name in siblings if not (ctx.root.parent / name).is_dir()]
    if missing:
        raise MakeError(
            f"missing sibling repo(s): {', '.join(missing)}",
            hint=f"they are path deps and must be checked out beside drive, under {ctx.root.parent}",
        )

    step(f"docker build (linux/amd64) -> {dist} (siblings from {ctx.root.parent})")
    fs.rmtree(dist)
    sh(
        "docker",
        "build",
        "--platform",
        "linux/amd64",
        *[f"--build-context={name}={ctx.root.parent / name}" for name in siblings],
        "-f",
        "drive-web/deploy/Dockerfile",
        "--target",
        "export",
        "--output",
        f"type=local,dest={dist}",
        ".",
        env={"DOCKER_BUILDKIT": "1"},
    )

    binary = dist / "drive-web"
    # Post-conditions on what the suppressed build would have written, so they
    # only mean anything when it actually ran.
    if not ctx.dry_run:
        if not binary.is_file():
            raise MakeError(f"the build produced no {binary}")
        if not (dist / "public").is_dir():
            raise MakeError(f"the build produced no {dist / 'public'} bundle")
    kind = sh.out("file", "-b", binary, dry="ELF 64-bit LSB pie executable").split(",")[:2]
    note(f"binary: {binary} ({','.join(kind)})")
    note(f"bundle: {dist / 'public'}/")
    return dist


@recipe(group="server", name="run")
def server_run() -> None:
    """Run the built prod binary locally on :8080.

    dioxus-server reads ./public from the cwd, so this runs from the
    release-deploy directory where the bundle sits beside the binary.
    """
    stage = ctx.root / "drive-web/target/release-deploy"
    sh(
        "./drive-web",
        cwd=stage,
        env={"HIVE_GATEWAY_PORT": "8080", "DATA_DIR": str(ctx.root / "drive-server-api/tmp/data")},
    )


@recipe(group="server", name="deploy", needs=[server_build], requires=["ssh", "scp", "cargo"])
def server_deploy(*, host: Host = VM) -> None:
    """Build, then hand the artifact to the shared `hive-deploy`, which ships and activates it.

    drive-web runs as a loopback tenant behind the `gateway` that owns :443; the
    gateway's APEX tenant must point at drive's `http_addr`. Host, listener,
    service user, workdir, data dirs, activation policy and smoke targets all
    live in hive's fleet.toml `[hosts.drive]` as of 2026-08-07, so this recipe
    keeps no second copy of any of them. `host` is only for the logrotate and
    env-template steps below.

    The systemd unit is NOT shipped from here. It is generated from fleet.toml by
    `hive-deploy bootstrap --host drive`, which is also what gave drive
    Type=notify, the hardening baseline and ExecReload; re-adding an scp here
    would revert every bit of that on the next deploy. To change the unit, edit
    hive's fleet.toml and run `hive-deploy bootstrap --host drive` (--dry-run
    first). logrotate stays app-owned -- hive-deploy has no opinion on it.

    activation = "restart", not reload, and that is measured: drive takes ~5s to
    reach READY (geocode + routing graph), at the edge of hive-server's ecdysis
    handoff window. A missed window does not fail loudly -- the upgrade aborts
    and the OLD process keeps serving, so the deploy would report success having
    shipped nothing.
    """
    # No "is anything staged?" guard here: `server.build` is a prerequisite and
    # already fails if it produced no binary or no public/ bundle. The justfile
    # checked both in both recipes, which only meant a dry run tripped over an
    # artifact a dry run cannot have made.
    stage = ctx.root / "drive-web/target/release-deploy"

    step("install logrotate + env template (env lives root-owned in /etc/drive)")
    sh("scp", ctx.root / "drive-web/deploy/drive.logrotate", f"{host}:/etc/logrotate.d/drive")
    sh("scp", ctx.root / "drive-server-api/deploy/drive.env.example", f"{host}:/tmp/drive.env.example")
    # systemd reads the env file as root before dropping to User=drive, so the
    # drive user never needs read access -- this is what closed the old
    # world-readable ~/.env hole. First deploy only: seed drive.env from the
    # template with a fresh REBUILD_TOKEN.
    sh(
        "ssh",
        host,
        """set -e
        install -d -m 750 -o root -g drive /etc/drive
        install -m 600 -o root -g root /tmp/drive.env.example /etc/drive/drive.env.example
        rm -f /tmp/drive.env.example
        if [ ! -f /etc/drive/drive.env ]; then
          token="$(head -c18 /dev/urandom | base64 | tr -dc A-Za-z0-9)"
          sed "s/^REBUILD_TOKEN=.*/REBUILD_TOKEN=$token/" /etc/drive/drive.env.example \
            > /etc/drive/drive.env
          chmod 600 /etc/drive/drive.env; chown root:root /etc/drive/drive.env
        fi""",
    )

    # Ship + swap + activate + smoke is `hive-deploy`, reading drive's host and
    # policy from hive's fleet.toml: rsync the binary + public/, atomic swap,
    # chown back to `drive`, activate, then smoke drive's OWN loopback listener
    # over ssh (smoke_via = "loopback", derived from http_addr -- so the port
    # cannot drift away from the gateway tenant row). smoke_paths =
    # ["/cameras.json", "/"] with smoke_timeout_secs = 180: a dataset route (the
    # real "is drive working" check, which warms AFTER /healthz) and the SSR home.
    #
    # The hive checkout is resolved from this repo, not from the cwd. The
    # justfile had `hive := "../hive"` used after a `cd drive-web`, which
    # resolved to a nonexistent drive/hive.
    hive = ctx.root.parent / "hive/Cargo.toml"
    if not hive.is_file():
        raise MakeError(f"no hive checkout at {hive.parent}", hint="it must sit beside drive")
    sh(
        "cargo",
        "run",
        "--manifest-path",
        hive,
        "-q",
        "-p",
        "hive-deploy",
        "--",
        "deploy",
        "--host",
        "drive",
        "--artifact",
        stage,
    )
    note("done. Point the gateway's APEX tenant (drive.optersoft.com) at this loopback port")
    note("and drop the worker's apex route (keep cdn.) -- see docs/backend.md")


# --------------------------------------------------------------------------
# webapp -- the KMP/Compose wasmJs app served at /app
# --------------------------------------------------------------------------


@recipe(group="webapp", name="debug", requires=["./gradlew", "curl"])
def webapp_debug() -> None:
    """Run the Compose web app standalone in debug with hot reload, plus a dev Chrome.

    Serves the full-screen app on http://localhost:8080/ -- no SSR site, no /app
    prefix. Datasets, tiles and brand logos still come from the live CDN.
    Chrome's CDP port is the server port + 1000, floating up if busy; attach the
    chrome-devtools MCP with --browserUrl. Ctrl-C stops the dev server;
    `webapp.debug-stop` closes the dev Chrome.
    """
    dev = ctx.root / "tmp"
    profile = dev / "web-debug-chrome"
    fs.mkdir(profile)
    port = 8080  # webpack-dev-server's default for wasmJsBrowserDevelopmentRun
    cdp = web.cdp_port(port)

    # Wait for the dev server, then open the isolated dev Chrome. Backgrounded,
    # because the gradle run below replaces this process.
    sh.background(
        "bash",
        "-c",
        f'until curl -sf "http://localhost:{port}/" >/dev/null 2>&1; do sleep 1; done; '
        f'open -na "Google Chrome" --args --user-data-dir="{profile}" '
        f"--remote-debugging-port={cdp} --no-first-run --no-default-browser-check "
        f'--new-window "http://localhost:{port}/"',
    )
    note(f"web app at http://localhost:{port}/")
    note(f"dev Chrome debuggable at http://127.0.0.1:{cdp} (chrome-devtools MCP --browserUrl)")
    sh.replace_process("./gradlew", ":drive-web-app:wasmJsBrowserDevelopmentRun", "--continuous")


@recipe(group="webapp", name="debug-stop")
def webapp_debug_stop() -> None:
    """Stop `webapp.debug` -- the gradle continuous build, webpack on :8080, and the dev Chrome.

    The Chrome match is on its profile path, so the main browser is left alone.
    """
    for label, pattern in (
        ("dev Chrome", "user-data-dir=.*tmp/web-debug-chrome"),
        ("gradle dev run", "wasmJsBrowserDevelopmentRun"),
    ):
        note(f"stopped {label}" if sh.ok("pkill", "-f", pattern, dry=False) else f"no {label} running")
    killed = web.reap_port(8080)
    note(f"stopped webpack-dev-server on :8080 ({len(killed)} pid)" if killed else "nothing on :8080")


@recipe(group="webapp", name="build", requires=["./gradlew"])
def webapp_build() -> Path:
    """Build the wasmJs production bundle, checking it is a prod build and not a dev one."""
    sh("./gradlew", ":drive-web-app:wasmJsBrowserDistribution")
    dist = ctx.root / "drive-web-app/build/dist/wasmJs/productionExecutable"
    if ctx.dry_run:  # nothing was built, so there is nothing to assert about
        return dist
    index = dist / "index.html"
    if not index.is_file():
        raise MakeError(f"no {index}")
    # webpack sets publicPath=/app/ in production, so the bundle only resolves
    # under /app -- these two checks are how a dev build that would 404 its own
    # wasm gets caught before it reaches the VM.
    if "__driveImport" not in index.read_text(encoding="utf-8"):
        raise MakeError("index.html lacks the __driveImport hook (the geo wasm will not load)")
    if not (dist / "wasm/drivecore_bg.wasm").is_file():
        raise MakeError(f"no {dist / 'wasm/drivecore_bg.wasm'}", hint="run `make rust.wasm`")
    return dist


@recipe(group="webapp", name="deploy", needs=[webapp_build], requires=["rsync", "ssh", "curl"])
def webapp_deploy(*, host: Host = VM, assets: Assets = ASSETS) -> None:
    """Ship the wasmJs bundle to the VM's ASSETS/app, which drive-web serves at /app.

    This is the ONLY thing that publishes /app: `server.deploy` ships just the
    binary + the SSR public/ bundle, and `cdn.deploy` ships only the worker.
    Without this recipe the bundle silently rots -- /app sat on a Jun-2026
    Compose build for a month, long after the app went headless, because nothing
    ever replaced it.

    Run `rust.wasm` first if the Rust cores (wasm/drivecore*, wasm/router*)
    changed.
    """
    dist = ctx.root / "drive-web-app/build/dist/wasmJs/productionExecutable"

    step(f"upload -> {host}:{assets}/app (atomic swap; previous kept as app.bak)")
    sh("rsync", "-az", "--delete", f"{dist}/", f"{host}:{assets}/app.new/")
    sh(
        "ssh",
        host,
        f"""set -e; cd {assets}
        rm -rf app.bak; [ -d app ] && mv app app.bak || true; mv app.new app
        chown -R drive:drive app""",
    )

    # The entry HTML is no-cache, so a deploy shows up at once.
    step("smoke test (public /app)")
    rollback = f"ssh {host} 'cd {assets} && rm -rf app && mv app.bak app'"
    for path in ("/", "/drive.js", "/wasm/drivecore.js", "/wasm/drivecore_bg.wasm", "/sprite.json"):
        url = f"https://drive.optersoft.com/app{path}"
        code = sh.out(
            "curl",
            "-fsS",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
            "--max-time",
            "20",
            url,
            check=False,
            dry="200",
        )
        if code != "200":
            raise MakeError(f"/app{path} -> {code or 'no-response'}", hint=f"roll back: {rollback}")
        note(f"  ✓ /app{path}")
    note("done. The map itself needs the basemap ?v= -- see docs/backend.md if /app renders blank.")


# --------------------------------------------------------------------------
# cdn -- the R2 edge worker at cdn.drive.optersoft.com
# --------------------------------------------------------------------------


@recipe(group="cdn", name="deploy", requires=["deno"])
def cdn_deploy() -> None:
    """Publish the R2 CDN edge worker -- tiles and datasets from R2, free egress.

    No build step: the Astro site/dist and the /app static bundle were removed,
    so `deno task deploy` is a plain `wrangler deploy`. Needs `wrangler login`
    or CLOUDFLARE_API_TOKEN. See drive-web-cdn/README.md.
    """
    sh("deno", "install", cwd="drive-web-cdn")
    sh("deno", "task", "deploy", cwd="drive-web-cdn")


@recipe(group="cdn", name="dev", requires=["deno"])
def cdn_dev() -> None:
    """Run the worker locally (wrangler dev) against the real `drive` R2 bucket.

    To proxy a local `web.start`, put API_ORIGIN=http://127.0.0.1:8002 in
    drive-web-cdn/.dev.vars -- 8002 is the dev port now, not 8080.
    """
    sh("deno", "install", cwd="drive-web-cdn")
    sh("deno", "task", "dev", cwd="drive-web-cdn")


@recipe(group="cdn", name="tail", requires=["wrangler"])
def cdn_tail() -> None:
    """Tail the live worker logs -- console.error and exceptions."""
    sh("wrangler", "tail", "drive", "--format", "pretty", cwd="drive-web-cdn")


# --------------------------------------------------------------------------
# desktop / rust / test
# --------------------------------------------------------------------------


@recipe(group="rust", name="abi", aliases=["shared-rust"])
def rust_abi() -> None:
    """Rebuild the app C-ABI libs for every Android ABI.

    Commit the regenerated .so files under drive-shared-core/src/androidMain/jniLibs/.
    """
    sh("drive-shared-rust/build.sh")


@recipe(group="rust", name="host", requires=["cargo"])
def rust_host() -> None:
    """Build the host Rust dylibs the desktop app links over JNA (drivecore + router, release)."""
    for crate in ("drivecore", "router"):
        sh("cargo", "build", "--release", "--manifest-path", f"drive-shared-rust/{crate}/Cargo.toml")


@recipe(group="rust", name="wasm")
def rust_wasm() -> None:
    """Build the Rust cores as a browser WASM module -- the real engine, not a Kotlin port.

    Commits drivecore/router .js + _bg.wasm under drive-web-app resources. Needs
    wasm-bindgen-cli 0.2.122 and wasm-opt.
    """
    sh("drive-shared-rust/build-wasm.sh")


@recipe(group="desktop", name="run", needs=[rust_host], requires=["./gradlew"])
def desktop_run() -> None:
    """Run the desktop app (JVM). Needs the host Rust dylibs, built by `rust.host`."""
    sh("./gradlew", ":drive-desktop:run")


@recipe(group="desktop", name="package", requires=["./gradlew"])
def desktop_package() -> None:
    """Build the desktop installer for the current OS (Dmg/Msi/Deb)."""
    sh("./gradlew", ":drive-desktop:packageDistributionForCurrentOS")


@recipe(group="test", name="ios", requires=["./gradlew"])
def test_ios() -> None:
    """iOS simulator tests."""
    sh("./gradlew", ":drive-shared-core:iosSimulatorArm64Test")


@recipe(group="test", name="web", requires=["./gradlew"])
def test_web() -> None:
    """Web (wasmJs) tests.

    The geo-engine parity suite skips on wasmJs -- same Rust core, covered on the
    Android host.
    """
    sh("./gradlew", ":drive-shared-core:wasmJsTest")


@recipe(group="test", name="ci", needs=[rust_host], requires=["./gradlew"])
def test_ci() -> None:
    """Shared unit tests (Android host + JS), over the host dylibs the JNA tests load.

    The dylibs come from `rust.host` as a prerequisite; the justfile inlined the
    same two cargo builds a second time.
    """
    sh("./gradlew", ":drive-shared-core:testAndroidHostTest", ":drive-shared-core:wasmJsTest", "--no-daemon")
