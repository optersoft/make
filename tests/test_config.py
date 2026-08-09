"""Typed configuration -- replacing `just`'s "never give a consumer variable a default"."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from helpers import write

from make import config
from make.errors import ConfigError
from make.testing import context


@config.section("web")
@dataclass
class Web:
    bin: str
    port: int = 8001
    watch: list[str] = field(default_factory=list)
    css_in: str | None = None


@pytest.fixture(autouse=True)
def fresh():
    Web.reset()
    yield
    Web.reset()


def test_configure_from_the_recipe_file(project: Path):
    Web.configure(bin="acme-web", port=8005)
    with context(root=project):
        assert Web.bin == "acme-web"
        assert Web.port == 8005
        assert Web.watch == []  # dataclass default still applies


def test_make_toml_supplies_values(project: Path):
    write(project / "make.toml", '[web]\nbin = "myapp"\nport = 8002\nwatch = ["src", "web"]\n')
    with context(root=project):
        assert Web.bin == "myapp"
        assert Web.watch == ["src", "web"]


def test_pyproject_tool_table_is_the_fallback(project: Path):
    write(project / "pyproject.toml", '[tool.make.web]\nbin = "other-app"\nport = 8001\n')
    with context(root=project):
        assert Web.bin == "other-app"


def test_configure_beats_the_file(project: Path):
    write(project / "make.toml", '[web]\nbin = "from-file"\nport = 1\n')
    Web.configure(port=2)
    with context(root=project):
        assert Web.bin == "from-file"
        assert Web.port == 2


def test_environment_beats_everything(project: Path, monkeypatch):
    write(project / "make.toml", '[web]\nbin = "x"\nport = 1\n')
    Web.configure(port=2)
    monkeypatch.setenv("MAKE_WEB_PORT", "8105")
    with context(root=project):
        assert Web.port == 8105


def test_missing_required_value_names_every_place_it_can_be_set(project: Path):
    with context(root=project):
        with pytest.raises(ConfigError) as caught:
            _ = Web.port
    message = caught.value.message
    assert "web.bin" in message
    assert "(str)" in message
    assert "Makefile.py" in message
    assert "make.toml" in message
    assert "MAKE_WEB_BIN" in message


def test_types_are_coerced_from_strings(project: Path, monkeypatch):
    Web.configure(bin="x")
    monkeypatch.setenv("MAKE_WEB_WATCH", "src web crates")
    with context(root=project):
        assert Web.watch == ["src", "web", "crates"]


def test_unknown_setting_is_caught_at_configure_time():
    with pytest.raises(ConfigError, match="unknown setting"):
        Web.configure(prot=8001)


def test_section_requires_a_dataclass():
    with pytest.raises(ConfigError, match="expects a dataclass"):

        @config.section("bad")
        class NotADataclass:
            pass


def test_values_are_resolved_per_root(project: Path, tmp_path_factory):
    write(project / "make.toml", '[web]\nbin = "first"\n')
    other = tmp_path_factory.mktemp("other")
    write(other / "make.toml", '[web]\nbin = "second"\n')

    with context(root=project):
        assert Web.bin == "first"
    config.reset_cache()
    with context(root=other):
        assert Web.bin == "second"
