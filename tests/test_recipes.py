"""Registration, namespacing, overriding -- the three things `just` gets wrong."""

from __future__ import annotations

import pytest

from make.errors import RecipeError, UsageError
from make.recipes import Registry, group, recipe


def test_decorator_returns_the_plain_function(registry):
    @recipe(into=registry)
    def build() -> str:
        return "built"

    assert build() == "built"  # importable and callable, no wrapper
    assert build.__make_recipe__.full_name == "build"


def test_group_namespaces_without_prefixes(registry):
    web = group("web", into=registry)

    @web
    def start() -> None: ...

    @web
    def stop() -> None: ...

    assert {r.full_name for r in registry.all()} == {"web.start", "web.stop"}


def test_two_packages_can_use_the_same_recipe_name(registry):
    @recipe(group="web", into=registry)
    def start() -> None: ...

    @recipe(group="box", into=registry)
    def start() -> None: ...

    assert registry.get("web.start") is not registry.get("box.start")


def test_duplicate_name_is_an_error_that_names_both_definitions(registry):
    @recipe(into=registry)
    def build() -> None: ...

    with pytest.raises(RecipeError) as caught:

        @recipe(into=registry)
        def build() -> None: ...

    message = str(caught.value)
    assert "duplicate recipe 'build'" in message
    assert message.count("test_recipes.py") == 2


def test_override_replaces_regardless_of_definition_order(registry):
    @recipe(group="play", abstract=True, into=registry)
    def test_gate() -> None: ...

    @recipe(group="play", name="test-gate", override=True, into=registry)
    def my_gate() -> str:
        return "checked"

    registry.finalize()
    assert registry.require("play.test-gate").fn() == "checked"
    assert registry.require("play.test-gate").abstract is False


def test_override_of_a_missing_recipe_is_caught_at_finalize(registry):
    @recipe(override="web.start", into=registry)
    def start() -> None: ...

    with pytest.raises(RecipeError, match="does not match any recipe"):
        registry.finalize()


def test_underscores_and_dashes_are_the_same_recipe(registry):
    @recipe(group="play", into=registry)
    def test_gate() -> None: ...

    assert registry.get("play.test-gate") is registry.get("play.test_gate")


def test_unknown_recipe_suggests_a_near_match(registry):
    @recipe(group="web", into=registry)
    def start() -> None: ...

    with pytest.raises(UsageError) as caught:
        registry.require("web.strt")
    assert caught.value.message == "no recipe named 'web.strt'"
    assert "web.start" in (caught.value.hint or "")


def test_aliases_resolve(registry):
    @recipe(aliases=["ship"], into=registry)
    def publish() -> None: ...

    assert registry.get("ship") is registry.get("publish")


def test_leading_underscore_hides_a_recipe(registry):
    @recipe(into=registry)
    def _internal() -> None: ...

    assert registry.all() == []
    assert len(registry.all(include_hidden=True)) == 1


def test_needs_accepts_functions_and_names(registry):
    @recipe(into=registry)
    def first() -> None: ...

    @recipe(needs=[first, "first"], into=registry)
    def second() -> None: ...

    assert registry.require("second").resolved_needs() == ["first", "first"]


def test_needs_rejects_a_non_recipe(registry):
    def helper() -> None: ...

    @recipe(needs=[helper], into=registry)
    def build() -> None: ...

    with pytest.raises(RecipeError, match="not a recipe"):
        registry.require("build").resolved_needs()


def test_docstring_becomes_summary_and_description(registry):
    @recipe(into=registry)
    def build() -> None:
        """Build the app.

        The long form, which shows up in `mk --help build`.

        Args:
            unused: ignored
        """

    item = registry.require("build")
    assert item.summary == "Build the app."
    assert "long form" in item.description
    assert "Args:" not in item.description


def test_registries_are_independent():
    left, right = Registry(), Registry()

    @recipe(into=left)
    def only_left() -> None: ...

    assert "only-left" in left
    assert "only-left" not in right
