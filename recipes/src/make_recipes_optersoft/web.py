"""The `web` group: the Dioxus fullstack dev server, its dev Chrome, and Tailwind.

Ported from `web.just` -- 499 lines of bash whose only available check was
`just --fmt --check`, i.e. that it parsed. Every bug it shipped was in the
logic: the port-versus-lock reap, the worktree repo-name resolution, the
OAuth base-URL export. All three are functions here, each with a test.

The domain rules are unchanged and are the reason this file is not simpler:

**One port per app, overridable per checkout.** Google matches OAuth redirect
URIs exactly -- scheme, host, port, path, no wildcards -- so the port cannot
float. Every port a checkout may bind has to be registered in the app's Cloud
Console client as `http://localhost:<port>/auth/google/callback`.

    server = web.port     broker 8001   drive 8002   xtec 8003   alma 8005
    cdp    = server+1000  the dev Chrome's remote-debugging port, stable so the
                          chrome-devtools MCP can attach with --browserUrl

A second checkout (a worktree serving alongside main) claims its own port; the
+100 convention (8103, 8203) stays clear of every other app's pair. Register
that port's callback URI first, or sign-in fails with redirect_uri_mismatch.

**Two reaps, both narrowly scoped**, so a sibling repo's dev server is never
touched -- never replace them with an unscoped `pkill -f 'dx serve'`:

1. *Port-scoped* -- whatever listens on this checkout's port. That port belongs
   to this checkout alone, so the listener is a previous run of this recipe.
2. *Database-file-scoped* -- anything holding `tmp/*.db`. This is the one that
   matters: the turso WAL lock, not the port, is what blocks the next boot. An
   orphan stranded *off* the port (a crashed parent whose child kept the file
   descriptor, a hand-run `cargo run --features server`) is invisible to the
   first reap while still deadlocking every subsequent start. The glob is
   non-recursive, so the dev Chrome profile's own SQLite files one level down
   are never matched.

**CSS is decoupled from cargo.** No `build.rs` shells out to Tailwind.
`dx build --fullstack` compiles the crate twice, server and wasm, and a build.rs
running Tailwind per pass over a `target/` tree that grew in between emitted a
*different* stylesheet each time -- two `asset!()` hashes, one of which 404s, so
the deployed site came up unstyled. One explicit generate before the build
cannot desync. It also means a CSS-only edit costs no Rust rebuild, so dx
hot-reloads the stylesheet in about a second.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from make import config, ctx, env, fs, group, invoke, note, sh, step, warn
from make.errors import MakeError

web = group("web")

#: The one Tailwind version the fleet compiles with. It matters more than a
#: usual tool pin: the committed stylesheet is a reviewed artifact, so a
#: different build produces a diff nobody asked for -- and across v3 to v4 the
#: semantics of the space, shadow and outline utilities changed, so the same
#: classes render differently.
TAILWIND_VERSION = "4.3.0"

TAILWIND_ASSETS = {
    ("Darwin", "arm64"): "tailwindcss-macos-arm64",
    ("Darwin", "x86_64"): "tailwindcss-macos-x64",
    ("Linux", "aarch64"): "tailwindcss-linux-arm64",
    ("Linux", "x86_64"): "tailwindcss-linux-x64",
}

#: Per-checkout port override, read from `./.env`. The legacy name is still
#: honoured so an existing checkout keeps its port through the migration.
PORT_ENV = "MAKE_WEB_PORT"
LEGACY_PORT_ENV = "JUST_WEB_PORT"


@config.section("web")
@dataclass
class Web:
    """Per-project dev-server configuration."""

    bin: str
    """target/dx binary name, for the lock reaper: 'broker-web', 'drive-web', ..."""

    port: int = 8001
    """The default dev-server port. A checkout overrides it in its own ./.env."""

    ready: str = "/"
    """Readiness probe path."""

    serve_dir: str = "."
    """Directory to enter before `dx serve` (where Dioxus.toml lives)."""

    serve_flags: list[str] = field(default_factory=list)
    """Flags between `dx serve` and `--port`."""

    watch: list[str] = field(default_factory=list)
    """Directories swept for `.!*!*` atomic-write temp files."""

    css_in: str = ""
    """Tailwind input, repo-relative. Empty if the repo has no Tailwind."""

    css_out: str = ""
    """Compiled stylesheet, repo-relative."""

    target_budget_gb: int = 40
    """How large target/ may grow before `web.stop` drops the incremental caches."""


# --------------------------------------------------------------------------
# Ports and paths
# --------------------------------------------------------------------------


def resolve_port(explicit: int | None = None) -> int:
    """The port this checkout will actually bind.

    Precedence, highest first:

    1. `--port` on the command line
    2. `MAKE_WEB_PORT` (or the legacy `JUST_WEB_PORT`) exported by the caller
    3. the same name in this checkout's `./.env`
    4. `web.port`

    The per-checkout override lives in `./.env` and deliberately *not* in
    `~/.make/<repo>.env`: that file is per-repo and shared by every worktree of
    it, so it cannot describe one checkout. An earlier attempt to source the
    port from there had to be reverted for exactly that reason.
    """
    if explicit is not None:
        return explicit
    for name in (PORT_ENV, LEGACY_PORT_ENV):
        value = os.environ.get(name)
        if value:
            return int(value)
    local = ctx.root / ".env"
    if local.is_file():
        values = env.parse(local.read_text(encoding="utf-8"))
        for name in (PORT_ENV, LEGACY_PORT_ENV):
            if values.get(name):
                return int(values[name])
    return int(Web.port)


def cdp_port(server_port: int) -> int:
    """Remote-debugging port for the dev Chrome: server + 1000.

    Stable, so the chrome-devtools MCP can attach to *this* window with a fixed
    `--browserUrl=http://127.0.0.1:<cdp>` instead of launching its own.
    """
    return server_port + 1000


def state_dir() -> Path:
    """Repo-scoped dev state: dev database, Chrome profile, logs.

    Absolute, so the Chrome `--user-data-dir` (and therefore the `pkill` that
    matches on it) is unique per repository, and so `--log-to-file` stays
    correct after entering `serve_dir`.
    """
    return ctx.root / "tmp"


# --------------------------------------------------------------------------
# Reaping
# --------------------------------------------------------------------------


def reap_port(port: int) -> list[int]:
    """Kill whatever listens on this checkout's port.

    SIGKILL, not SIGTERM: the server traps SIGTERM for graceful shutdown and
    ignores it in dev.
    """
    if not sh.ok("lsof", f"-iTCP:{port}", "-sTCP:LISTEN", dry=False):
        return []
    sh("pkill", "-f", f"dx serve.*--port {port}", check=False, echo_cmd=False)
    pids = [int(p) for p in sh.lines("lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN", check=False)]
    if pids:
        note(f"reaping the previous dev server on :{port} ({', '.join(map(str, pids))})")
        sh("kill", "-9", *pids, check=False)
        time.sleep(1)
    return pids


def reap_database_holders(dev: Path | None = None) -> list[int]:
    """Kill anything holding `tmp/*.db` -- the reap that actually matters.

    The port reap only finds an orphan that is still listening, but what blocks
    the next boot is the turso lock, and an orphan can hold it from no port at
    all. Symptom: the boot panics with "Failed locking file ... is locked by
    another process", then /healthz 500s forever.

    The glob is non-recursive and `dev` is repo-scoped, so the dev Chrome
    profile's SQLite files (one level down, in `tmp/chrome`) are never matched --
    anything holding one of these is our own orphan by construction.
    """
    directory = dev or state_dir()
    databases = sorted(directory.glob("*.db"))
    if not databases:
        return []
    pids = sorted({int(p) for p in sh.lines("lsof", "-t", "--", *databases, check=False)})
    if pids:
        note(f"reaping orphan(s) holding {directory}/*.db: {', '.join(map(str, pids))}")
        sh("kill", "-9", *pids, check=False)
        time.sleep(1)
    return pids


@web.recipe(name="reap-dx", hidden=True)
def reap_dx(*, older_than_days: int = 7) -> list[Path]:
    """Delete dx bundle directories the toolchain has stopped writing.

    `target/dx/<app>/` is keyed on the dx app name, so renaming the binary
    orphans the whole tree in place and nothing errors -- it simply stops being
    written. One such rename left 7 GB behind.

    Matched by mtime, not by name, so this needs no per-repo configuration and
    cannot disagree with whatever the consumer calls its binary. Seven days, not
    thirty, because reaping a bundle costs a re-bundle and not a recompile --
    the cargo caches live elsewhere in `target/`.
    """
    root = ctx.root / "target" / "dx"
    if not root.is_dir():
        return []
    cutoff = time.time() - older_than_days * 86400
    reaped = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and child.stat().st_mtime < cutoff:
            note(f"reaped stale dx bundle  {child}")
            fs.rmtree(child)
            reaped.append(child)
    return reaped


@web.recipe(name="reap-incremental", hidden=True)
def reap_incremental() -> list[Path]:
    """Drop incremental caches when `target/` outgrows the budget.

    Called from `web.stop` only, never from the server-only teardown that
    `web.restart` goes through -- a cold rebuild on every restart is precisely
    the loop this module exists to keep fast. Here it costs one slow build,
    after you already said you were done.

    `deps/` is deliberately left alone: `cargo sweep` is the right tool for that
    and it is not a dependency this file should impose.

    Cargo garbage-collects `~/.cargo` but has nothing for a target directory --
    `incremental/` and `deps/` only grow, and every `cargo update`, toolchain
    bump or branch switch strands artifacts nothing will reference again. One
    repo reached 112 GB, 45.6 GB of it `incremental/`, and filled a 460 GB disk
    to 100%, at which point a Docker build failed with `no space left on device`.
    """
    target = ctx.root / "target"
    budget = int(Web.target_budget_gb)
    if not target.is_dir() or budget <= 0:
        return []

    used_kb = int(sh.out("du", "-sk", target, dry="0").split()[0] or 0)
    if used_kb < budget * 1024 * 1024:
        return []

    warn(
        f"target/ is {used_kb // 1024 // 1024} GB, over the {budget} GB budget -- dropping incremental caches"
    )
    reaped = []
    for depth in ("*/incremental", "*/*/incremental"):
        for directory in sorted(target.glob(depth)):
            if directory.is_dir():
                note(f"reaped {directory}")
                fs.rmtree(directory)
                reaped.append(directory)
    note("next build is a cold one")
    return reaped


def sweep_temp_files() -> None:
    """Remove `.!*!*` atomic-write leftovers, which desync dx's hot reload."""
    for watched in Web.watch:
        for stray in (ctx.root / watched).rglob(".!*!*"):
            fs.remove(stray)


# --------------------------------------------------------------------------
# Environment
# --------------------------------------------------------------------------


def oauth_env(server_port: int) -> dict[str, str]:
    """The OAuth base URL, under both names.

    `axum-oauth` resolves the base URL first-found-wins: `AXUM_OAUTH_BASE_URL`
    (canonical) then the legacy `HIVE_AUTH_BASE_URL`. Exporting only the legacy
    name was a silent no-op for every repo whose secrets pin the canonical one --
    xtec and alma both do -- so the app kept redirecting to the pinned port while
    the server listened on another, and Google exact-matches the URI.

    This must be applied *after* the layered secrets, so it overrides whatever
    they carried: the redirect URI has to match the port actually bound.
    """
    base = f"http://localhost:{server_port}"
    return {"AXUM_OAUTH_BASE_URL": base, "HIVE_AUTH_BASE_URL": base}


@web.recipe(name="preflight")
def preflight() -> None:
    """Project-specific setup before serving. Override this if you need it.

    In `just` this was a shell string interpolated into the recipe body, because
    there was no way for a consumer to supply behaviour. Here it is a recipe
    with a no-op default:

        @recipe(override="web.preflight")
        def preflight() -> None:
            sh("cargo", "run", "--bin", "seed-dev-db")
    """


# --------------------------------------------------------------------------
# Tailwind
# --------------------------------------------------------------------------


def manifest_dir(start: Path) -> Path:
    """The nearest directory at or above `start`, within the repo, holding a Cargo.toml.

    `cargo metadata` has to run somewhere with a manifest. Most repos here have
    one at the root, so the repo root worked -- but drive has none: its crates
    are siblings and the stylesheet belongs to drive-web, so cargo failed with
    "could not find Cargo.toml". Resolving from the crate that owns the
    stylesheet is the same answer wherever a root manifest exists, because cargo
    reports the whole workspace from any member, and the right one where it does
    not. Falls back to the repo root, which keeps the old behaviour for a repo
    with no manifest anywhere.
    """
    candidate = start
    while True:
        if (candidate / "Cargo.toml").is_file():
            return candidate
        if candidate == ctx.root or candidate == candidate.parent:
            return ctx.root
        candidate = candidate.parent


@web.recipe(name="tailwind-sources")
def tailwind_sources() -> Path | None:
    """Regenerate the `@source` globs pointing Tailwind at the shared crates.

    The shared `dioxus-*` crates emit Tailwind classes from source living at an
    unstable `~/.cargo/git/checkouts/<repo>-<hash>/<rev>/` path. Every app used
    to retype a *subset* of those classes as `@source inline(...)` and let it
    drift. The path is unstable but queryable: `cargo metadata` resolves each
    crate against this repo's Cargo.lock, so the generated globs follow a
    `cargo update -p dioxus-chrome` with no edit anywhere.

    The output holds absolute machine-specific paths, so it is gitignored.
    """
    if not Web.css_in:
        return None
    css_dir = ctx.root / Path(Web.css_in).parent
    out = css_dir / "tailwind-sources.css"

    metadata = sh.out("cargo", "metadata", "--format-version", "1", cwd=manifest_dir(css_dir), dry="{}")
    # A registry crate's directory carries a -<version> suffix, so
    # `dioxus-core-0.7.9/Cargo.toml` cannot match this shape -- only git checkouts.
    import re

    paths = sorted(
        {
            match.group(1)
            for match in re.finditer(r'"manifest_path":"([^"]*/dioxus-[a-z]+)/Cargo\.toml"', metadata)
        }
    )
    fs.write(
        out,
        "/* GENERATED by `make web.tailwind-sources` -- do not edit, do not commit.\n"
        "   Absolute @source globs for the shared dioxus-* crates, resolved from this\n"
        "   repo's Cargo.lock. Replaces the hand-copied `@source inline(...)` safelists,\n"
        "   which only ever covered a subset and drifted from the crates. */\n"
        + "".join(f'@source "{path}/src/**/*.rs";\n' for path in paths),
    )
    note(f"{len(paths)} dioxus-* crate(s) -> {out.name}")
    return out


@web.recipe(name="tailwind", needs=[tailwind_sources])
def tailwind() -> None:
    """Compile `css_in` to `css_out`, minified to match the committed file.

    Run after a CSS edit when you are *not* using `web.start`, which runs the
    watcher for you.
    """
    if not Web.css_in:
        note("no Tailwind in this repo (web.css_in is empty)")
        return
    if sh.which("tailwindcss") is None:
        raise MakeError("no standalone `tailwindcss` on PATH", hint="run `make web.tailwind-install`")
    installed = sh.out("tailwindcss", "--help", check=False, dry=f"v{TAILWIND_VERSION}")
    import re

    found = re.search(r"v\d+\.\d+\.\d+", installed)
    if found and found.group(0) != f"v{TAILWIND_VERSION}":
        warn(
            f"tailwindcss {found.group(0)} on PATH, the fleet pins v{TAILWIND_VERSION}. "
            "The committed stylesheet is a reviewed artifact, so a different build makes it "
            "drift -- `make web.tailwind-install` to match."
        )
    sh("tailwindcss", "-i", Web.css_in, "-o", Web.css_out, "--minify")
    note(f"compiled {Web.css_out}")


@web.recipe(name="tailwind-install", requires=["curl"])
def tailwind_install() -> Path:
    """Install the pinned standalone `tailwindcss` -- no Node, no npm.

    The same single-file binary the deploy Dockerfiles download, so dev and
    production compile identically.
    """
    import platform

    key = (platform.system(), platform.machine())
    asset = TAILWIND_ASSETS.get(key)
    if asset is None:
        raise MakeError(f"no upstream tailwindcss build for {key[0]}-{key[1]}")

    cargo_home = Path(env.get("CARGO_HOME") or Path.home() / ".cargo")
    destination = cargo_home / "bin" / "tailwindcss"
    url = f"https://github.com/tailwindlabs/tailwindcss/releases/download/v{TAILWIND_VERSION}/{asset}"
    step(f"{asset} v{TAILWIND_VERSION} -> {destination}")

    # Download beside the target and move into place, so an interrupted fetch
    # cannot leave a truncated binary on PATH.
    temporary = destination.with_suffix(".tmp")
    sh("curl", "-fsSL", "-o", temporary, url)
    fs.chmod(temporary, 0o755)
    fs.replace(temporary, destination)
    note(f"installed {destination}")
    return destination


def start_css_watcher(dev: Path) -> None:
    """Recompile the stylesheet on edit, with no cargo rebuild.

    `web.start` execs into dx at the end, so no trap survives to reap this on
    exit: the previous watcher is killed here instead, matched on *this* repo's
    absolute input path so a sibling repo's watcher survives.
    """
    if not Web.css_in:
        return
    css_in = ctx.root / Web.css_in
    css_out = ctx.root / Web.css_out
    sh("pkill", "-f", f"tailwindcss -i {css_in}", check=False, echo_cmd=False)

    if sh.which("tailwindcss") is None:
        warn(f"no tailwindcss on PATH -- serving the committed {css_out.name} as-is")
        return

    # Tailwind v4's Bun-based --watch exits on stdin EOF when backgrounded with
    # no TTY, so stdin is held open rather than closed.
    sh.background(
        "/bin/sh",
        "-c",
        f"tail -f /dev/null | tailwindcss -i {css_in} -o {css_out} --minify --watch",
        log=dev / "css-watch.log",
    )
    note(f"tailwind --watch -> {css_out.name} (no cargo rebuild) · log: {dev / 'css-watch.log'}")


# --------------------------------------------------------------------------
# The dev server
# --------------------------------------------------------------------------


def chrome_is_running(profile: Path) -> bool:
    """Is *this* repo's dev Chrome up? Matched on the absolute profile path."""
    return sh.ok("pgrep", "-f", f"user-data-dir={profile}", dry=False)


def await_ready(server_port: int, ready_path: str, timeout: float = 600) -> bool:
    """Poll the dev server until it answers."""
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout
    url = f"http://localhost:{server_port}{ready_path}"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return True
        except (urllib.error.URLError, OSError):
            time.sleep(1)
    return False


def open_dev_chrome(profile: Path, debug_port: int, url: str) -> None:
    """Open the dedicated, remote-debuggable dev Chrome."""
    sh(
        "open",
        "-na",
        "Google Chrome",
        "--args",
        f"--user-data-dir={profile}",
        f"--remote-debugging-port={debug_port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        url,
    )


@web.recipe(name="await-ready", hidden=True, keep_cwd=True)
def _await_ready_cli(*, port: int, cdp: int, profile: Path, ready: str, reuse: bool = False) -> None:
    """Wait for the server, then open or announce the dev Chrome.

    Run as a detached child by `web.start`, which execs into dx and so cannot do
    this itself.
    """
    if not await_ready(port, ready):
        return
    note(f"dev server at http://localhost:{port}")
    note(f"dev Chrome debuggable at http://127.0.0.1:{cdp} (chrome-devtools MCP --browserUrl)")
    if reuse:
        note("reusing the running dev Chrome -- reload the tab to pick up the rebuild")
    else:
        open_dev_chrome(profile, cdp, f"http://localhost:{port}")


@web.recipe(name="start", needs=[tailwind_sources, reap_dx])
def start(*dx_args: str, port: int | None = None) -> None:
    """Start the dev server and a dedicated dev Chrome (reused if already up).

    Extra arguments are forwarded to dx: `make web.start -- --hot-patch`.

    Args:
        port: bind this port instead of the configured one
    """
    dev = state_dir()
    fs.mkdir(dev / "chrome")
    server_port = resolve_port(port)
    debug_port = cdp_port(server_port)

    if server_port != int(Web.port):
        # Silent port sharing is the whole bug class here -- the reap below
        # SIGKILLs whatever holds the port.
        note(f"port {server_port} from the environment, not web.port={Web.port}")

    # The app environment (the AXUM_OAUTH_* family and the rest) lives in the
    # layered secrets and is passed on to the dx server we exec into.
    env.layered()
    # After the secrets, so a project's own exports can build on them -- and
    # through the registry, so a consumer's override is what actually runs.
    invoke("web.preflight")
    reuse_chrome = chrome_is_running(dev / "chrome")

    reap_port(server_port)
    reap_database_holders(dev)

    from make.context import current, set_context

    set_context(current().with_(env={**current().env, **oauth_env(server_port)}))

    log = dev / "web.log"
    start_css_watcher(dev)

    # dx's TUI grabs the terminal, so a panic on the console is otherwise lost.
    sh.background(
        sys.executable,
        "-c",
        "import sys; from make_recipes_optersoft import web; "
        "web._await_ready_cli(port=int(sys.argv[1]), cdp=int(sys.argv[2]), "
        "profile=sys.argv[3], ready=sys.argv[4], reuse=sys.argv[5]=='1')",
        server_port,
        debug_port,
        dev / "chrome",
        Web.ready,
        "1" if reuse_chrome else "0",
    )

    serve_dir = ctx.root / Web.serve_dir
    os.chdir(serve_dir)
    sh.replace_process(
        "dx", "serve", *Web.serve_flags, "--port", str(server_port), "--log-to-file", str(log), *dx_args
    )


@web.recipe(name="stop-server", hidden=True)
def stop_server(*, port: int | None = None) -> None:
    """Stop only the dev server, leaving the dev Chrome up for `web.restart`."""
    server_port = resolve_port(port)
    killed = reap_port(server_port)
    note(f"stopped dev server (:{server_port})" if killed else f"no dev server on :{server_port}")
    reap_database_holders()
    sweep_temp_files()


@web.recipe(name="stop")
def stop(*, port: int | None = None) -> None:
    """Full teardown: the dev server and the dedicated dev Chrome."""
    dev = state_dir()
    profile = dev / "chrome"
    if sh("pkill", "-f", f"user-data-dir={profile}", check=False).ok:
        note("stopped dev Chrome")
    else:
        note("no dev Chrome running")

    if Web.css_in:
        sh("pkill", "-f", f"tailwindcss -i {ctx.root / Web.css_in}", check=False, echo_cmd=False)

    stop_server(port=port)
    # Disk reclamation happens here and not in stop_server, so web.restart never
    # pays for it.
    reap_dx()
    reap_incremental()


@web.recipe(name="restart")
def restart(*dx_args: str, port: int | None = None) -> None:
    """Rebuild the server without closing the dev Chrome.

    Dioxus 0.7 fullstack has no reliable Rust hot reload -- only rsx! and assets
    hot-reload; a server-fn, signature or const change forces a full rebuild --
    so this is the common loop. Keeping Chrome alive preserves its tabs, scroll
    position, DevTools and a stable CDP port for the MCP.
    """
    stop_server(port=port)
    start(*dx_args, port=port)
