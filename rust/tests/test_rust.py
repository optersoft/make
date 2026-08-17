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
