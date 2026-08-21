"""make-rust: discovery finds only cargo's dirs; clean age-gates; sweep visits every workspace."""

from __future__ import annotations

import os
import time
from pathlib import Path

from make.testing import record
from make_rust import rust


def _target(root: Path, rel: str, *, marker: str = "cachedir") -> Path:
    t = root / rel / "target"
    (t / "debug").mkdir(parents=True)
    (t / "debug" / "junk.bin").write_bytes(b"x" * 2048)
    if marker == "cachedir":
        (t / "CACHEDIR.TAG").write_text("Signature: 8a477f597d28d172789f06886806bc55\n")
    else:
        (root / rel / "Cargo.toml").write_text('[package]\nname = "x"\n')
    return t


def test_find_targets_takes_only_what_cargo_made(tmp_path, monkeypatch):
    a = _target(tmp_path, "app")
    b = _target(tmp_path, "crates/web", marker="manifest")
    # Merely named target: no CACHEDIR.TAG inside, no Cargo.toml beside.
    (tmp_path / "misc" / "target").mkdir(parents=True)
    # Inside another target dir, or inside node_modules: never descended into.
    (a / "vendored" / "target").mkdir(parents=True)
    _target(tmp_path, "node_modules/dep")
    monkeypatch.chdir(tmp_path)
    assert set(rust.find_targets()) == {a, b}


def test_clean_age_gates_then_deletes(tmp_path, monkeypatch):
    t = _target(tmp_path, "app")
    monkeypatch.chdir(tmp_path)
    rust.clean(older_than=30)  # freshly written -> survives
    assert t.exists()
    rust.clean()  # 0 days -> gone
    assert not t.exists()


def test_clean_older_than_takes_the_idle_one(tmp_path, monkeypatch):
    fresh = _target(tmp_path, "hot")
    stale = _target(tmp_path, "cold")
    old = time.time() - 45 * 86400
    os.utime(stale, (old, old))
    monkeypatch.chdir(tmp_path)
    rust.clean(older_than=30)
    assert fresh.exists() and not stale.exists()


def test_sweep_visits_every_workspace(tmp_path, monkeypatch):
    _target(tmp_path, "app")
    _target(tmp_path, "crates/web", marker="manifest")
    monkeypatch.chdir(tmp_path)
    with record() as rec:
        rust.sweep(days=45)
    assert rec.matched(r"cargo sweep --time 45")
    assert rec.count("cargo", "sweep") == 2


# -- gc --------------------------------------------------------------------


def test_family_strips_only_a_hash_suffix():
    assert rust.family("libweb-8ff30a997dcbb8f0.rlib") == "libweb.rlib"
    assert rust.family("web-2f78bbnhs1lta") == "web"  # incremental, base62
    assert rust.family("academy_web-9f16e406e4efc830.d") == "academy_web.d"
    # A crate's own last segment is not a hash: no digit, or too short.
    assert rust.family("libfoo-manager-aaa.rlib") == "libfoo-manager-aaa.rlib"
    assert rust.family("libfoo-constants.rlib") == "libfoo-constants.rlib"


def _artifact(directory: Path, name: str, age_s: float = 0, data: bytes = b"x" * 1024) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    f = directory / name
    f.write_bytes(data)
    old = time.time() - age_s
    os.utime(f, (old, old))
    return f


def test_gc_keeps_the_newest_per_family(tmp_path, monkeypatch):
    t = _target(tmp_path, "app")
    deps = t / "debug" / "deps"
    newest = _artifact(deps, "libweb-aaaa111100000000.rlib", age_s=0)
    middle = _artifact(deps, "libweb-bbbb222200000000.rlib", age_s=100)
    oldest = _artifact(deps, "libweb-cccc333300000000.rlib", age_s=200)
    single = _artifact(deps, "libother-dddd444400000000.rlib")
    plain = _artifact(deps, "libnohash.rlib")
    monkeypatch.chdir(tmp_path)
    rust.gc(keep=2)
    assert newest.exists() and middle.exists() and single.exists() and plain.exists()
    assert not oldest.exists()


def test_gc_collects_incremental_directories_and_triple_profiles(tmp_path, monkeypatch):
    t = _target(tmp_path, "app")
    profile = t / "aarch64-apple-darwin" / "server-dev"
    (profile / "deps").mkdir(parents=True)  # what marks a profile dir
    fresh = profile / "incremental" / "web-aaaa1111"
    stale = profile / "incremental" / "web-bbbb2222"
    for d in (fresh, stale):
        d.mkdir(parents=True)
        (d / "query-cache.bin").write_bytes(b"x" * 2048)
    old = time.time() - 300
    os.utime(stale, (old, old))
    # target/dx squats in target/ but holds no deps/: never a profile dir.
    dx = t / "dx" / "app" / "debug"
    dx.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    rust.gc(keep=1)
    assert fresh.exists() and not stale.exists()
    assert dx.exists()
