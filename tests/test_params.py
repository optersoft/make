"""The signature is the command line -- so these are the contract tests."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import pytest

from make.errors import RecipeError, UsageError
from make.params import arg, build_params, parse_args, render_usage


def call(fn, argv):
    params = build_params(fn)
    parsed = parse_args(params, argv, recipe="r")
    return parsed.args, parsed.kwargs


def test_positional_before_star_and_option_after():
    def fn(src: str, dest: str = ".", *, force: bool = False): ...

    assert call(fn, ["a"]) == (["a", "."], {})
    assert call(fn, ["a", "b", "--force"]) == (["a", "b"], {"force": True})


def test_types_convert():
    def fn(count: int, ratio: float, path: Path): ...

    args, _ = call(fn, ["3", "1.5", "/tmp/x"])
    assert args == [3, 1.5, Path("/tmp/x")]


def test_bool_option_is_a_flag_and_negatable():
    def fn(*, color: bool = True, debug: bool = False): ...

    # Options that were not given are omitted, so the function's own defaults apply.
    assert call(fn, [])[1] == {}
    assert call(fn, ["--no-color", "--debug"])[1] == {"color": False, "debug": True}
    assert call(fn, ["--color=false"])[1]["color"] is False


def test_literal_becomes_choices():
    def fn(*, track: Literal["alpha", "prod"] = "alpha"): ...

    assert call(fn, ["--track", "prod"])[1] == {"track": "prod"}
    with pytest.raises(UsageError, match="expected one of alpha, prod"):
        call(fn, ["--track", "beta"])


def test_list_option_repeats():
    def fn(*, locale: list[str] = []): ...

    assert call(fn, ["--locale", "es", "--locale", "en"])[1] == {"locale": ["es", "en"]}


def test_varargs_collect_the_tail():
    def fn(*extra: str, port: int = 1): ...

    args, kwargs = call(fn, ["--port", "9", "a", "b"])
    assert args == ["a", "b"]
    assert kwargs == {"port": 9}


def test_double_dash_stops_option_parsing():
    def fn(*extra: str, port: int = 1): ...

    args, _ = call(fn, ["--", "--port", "9"])
    assert args == ["--port", "9"]


def test_short_flag_via_annotated():
    def fn(*, port: Annotated[int, arg("-p", help="the port")] = 1): ...

    assert call(fn, ["-p", "8080"])[1] == {"port": 8080}
    assert call(fn, ["-p=8080"])[1] == {"port": 8080}
    assert build_params(fn)[0].help == "the port"


def test_env_fallback_for_options(monkeypatch):
    def fn(*, token: Annotated[str, arg(env="MAKE_TEST_TOKEN")] = ""): ...

    monkeypatch.setenv("MAKE_TEST_TOKEN", "abc")
    assert call(fn, [])[1] == {"token": "abc"}
    assert call(fn, ["--token", "explicit"])[1] == {"token": "explicit"}


def test_missing_required_names_the_argument():
    def fn(target: str): ...

    with pytest.raises(UsageError, match="missing required argument <TARGET>"):
        call(fn, [])


def test_unknown_option_suggests_a_near_match():
    def fn(*, verbose: bool = False): ...

    with pytest.raises(UsageError, match="did you mean --verbose"):
        call(fn, ["--verbos"])


def test_unexpected_positional_is_rejected():
    def fn(*, flag: bool = False): ...

    with pytest.raises(UsageError, match="unexpected argument"):
        call(fn, ["stray"])


def test_docstring_supplies_parameter_help():
    def fn(*, port: int = 1):
        """Serve.

        Args:
            port: which port to bind
        """

    assert build_params(fn, doc_help={"port": "which port to bind"})[0].help == "which port to bind"


def test_kwargs_are_rejected_at_registration():
    def fn(**anything): ...

    with pytest.raises(RecipeError, match="cannot take arbitrary keyword arguments"):
        build_params(fn)


def test_underscores_become_dashes():
    def fn(*, dry_run: bool = False): ...

    assert build_params(fn)[0].flag == "--dry-run"
    assert call(fn, ["--dry-run"])[1] == {"dry_run": True}


def test_usage_line():
    def fn(src: str, dest: str = ".", *, force: bool = False): ...

    assert render_usage("cp", build_params(fn)) == "cp <SRC> [DEST] [options]"


def test_negative_number_is_not_a_flag():
    def fn(delta: int): ...

    assert call(fn, ["-5"]) == ([-5], {})
