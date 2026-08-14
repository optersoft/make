"""Recipes for the `drive` repo.

Carried over from `drive/justfile`: the box, database, android and play groups,
plus the Play test gate that repo defines. `play.test-gate` is `abstract=True`
upstream, so this is an ordinary override rather than a name that must not
collide with anything.
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

from make import recipe, sh
from make_recipes_optersoft import android, box, database, play  # noqa: F401 -- importing registers

android.Android.configure(module="drive-android", pkg="com.optersoft.drive")

play.Play.configure(store_dir="docs/store", locales=["en-US", "es-ES", "fr-FR", "pt-PT"])


@recipe(override="play.test-gate")
def test_gate() -> None:
    """Block releases on the unit and Android Auto suites."""
    android_test()
    android_test_auto()


@recipe(group="android", name="test", requires=["./gradlew"])
def android_test() -> None:
    """Unit tests for the Android module."""
    sh("./gradlew", ":drive-android:testDebugUnitTest")


@recipe(group="android", name="test-auto", requires=["./gradlew"])
def android_test_auto() -> None:
    """The Android Auto (car) suite -- required by the Play review checklist."""
    sh("./gradlew", ":drive-android:testDebugUnitTest", "--tests", "*car*")
