"""End-to-end: discovery, dispatch, listing, and the uv bootstrap."""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import write

from make.bootstrap import ScriptMetadata, needs_bootstrap, read_metadata
from make.cli import main
from make.discovery import find_recipe_file, require_recipe_file
from make.errors import UsageError

RECIPES = """
from pathlib import Path
from make import recipe, group, sh

web = group("web")

@web
def start(*, port: int = 8001) -> None:
    \"\"\"Start the dev server.\"\"\"
    sh("dx", "serve", "--port", port)

@web
def stop() -> None:
    \"\"\"Stop it.\"\"\"
    sh("pkill", "dx")

@recipe
def copy(src: Path, dest: str = ".") -> None:
    \"\"\"Copy a thing.\"\"\"
    sh("cp", src, dest)

@recipe(hidden=True)
def internal() -> None:
    sh("true")
"""


@pytest.fixture
def repo(project: Path) -> Path:
    write(project / "Makefile.py", RECIPES)
    return project


def test_list_is_the_default(repo: Path, capsys):
    assert main([]) == 0
    assert "web.start" in capsys.readouterr().err


def test_hidden_recipes_are_not_listed(repo: Path, capsys):
    main(["--list"])
    assert "internal" not in capsys.readouterr().err


def test_json_listing_describes_the_signature(repo: Path, capsys):
    import json

    assert main(["--list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    start = next(item for item in payload if item["name"] == "web.start")
    assert start["params"][0] == {
        "name": "port",
        "cli": "--port",
        "kind": "option",
        "type": "int",
        "required": False,
        "help": "",
        "choices": None,
    }


def test_names_output_feeds_shell_completion(repo: Path, capsys):
    assert main(["--names"]) == 0
    assert set(capsys.readouterr().out.split()) == {"copy", "web.start", "web.stop"}


def test_dispatch_runs_the_recipe(repo: Path, capsys):
    assert main(["--dry-run", "web.start", "--port", "9000"]) == 0
    assert "dx serve --port 9000" in capsys.readouterr().err


def test_several_recipes_in_one_invocation(repo: Path, capsys):
    assert main(["--dry-run", "web.start", "web.stop"]) == 0
    err = capsys.readouterr().err
    assert "dx serve" in err
    assert "pkill dx" in err


def test_an_open_positional_slot_wins_over_starting_a_new_recipe(repo: Path, capsys):
    """A value is never silently reinterpreted as the next recipe.

    `copy` still has an optional `dest`, so `web.stop` fills it -- which is what
    the signature says, and is checkable without knowing what else is registered.
    """
    assert main(["--dry-run", "copy", "a.txt", "web.stop"]) == 0
    err = capsys.readouterr().err
    assert "cp a.txt web.stop" in err
    assert "pkill dx" not in err


def test_chaining_resumes_once_the_slots_are_full(repo: Path, capsys):
    assert main(["--dry-run", "copy", "a.txt", ".", "web.stop"]) == 0
    err = capsys.readouterr().err
    assert "cp a.txt ." in err
    assert "pkill dx" in err


def test_recipe_help(repo: Path, capsys):
    assert main(["web.start", "--help"]) == 0
    out = capsys.readouterr().err
    assert "usage: web.start" in out
    assert "--port" in out


def test_unknown_recipe_exits_two(repo: Path, capsys):
    assert main(["nope"]) == 2
    assert "no recipe named" in capsys.readouterr().err


def test_discovery_walks_up(repo: Path, monkeypatch, capsys):
    nested = repo / "a" / "b"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert find_recipe_file(nested) == repo / "Makefile.py"
    assert main(["--dry-run", "web.stop"]) == 0


def test_recipes_run_at_the_recipe_file_root(repo: Path, monkeypatch, capsys):
    write(repo / "Makefile.py", RECIPES + "\n@recipe\ndef where() -> None:\n    print(Path.cwd())\n")
    nested = repo / "deep"
    nested.mkdir()
    monkeypatch.chdir(nested)
    main(["where"])
    assert capsys.readouterr().out.strip() == str(repo)


def test_keep_cwd_runs_where_the_user_stood(repo: Path, monkeypatch, capsys):
    write(
        repo / "Makefile.py",
        RECIPES + "\n@recipe(keep_cwd=True)\ndef where() -> None:\n    print(Path.cwd())\n",
    )
    nested = repo / "deep"
    nested.mkdir()
    monkeypatch.chdir(nested)
    main(["where"])
    assert capsys.readouterr().out.strip() == str(nested)


def test_missing_recipe_file_explains_how_to_start(project: Path):
    with pytest.raises(UsageError) as caught:
        require_recipe_file(project)
    assert "no recipe file found" in caught.value.message
    assert "Makefile.py" in (caught.value.hint or "")


def test_a_stray_make_py_is_diagnosed(project: Path):
    write(project / "make.py", "x = 1\n")
    with pytest.raises(UsageError) as caught:
        require_recipe_file(project)
    assert "rename it to Makefile.py" in (caught.value.hint or "")


def test_env_flag_reaches_commands(repo: Path, capsys):
    write(
        repo / "Makefile.py",
        "from make import recipe, env\n\n@recipe\ndef show() -> None:\n    print(env.get('TOKEN'))\n",
    )
    assert main(["--env", "TOKEN=abc", "show"]) == 0
    assert capsys.readouterr().out.strip() == "abc"


def test_doctor_reports_a_missing_tool(repo: Path, capsys, monkeypatch):
    write(
        repo / "Makefile.py",
        'from make import recipe, sh\n\n@recipe(requires=["definitely-not-installed"])\n'
        "def build() -> None:\n    sh('true')\n",
    )
    assert main(["--doctor"]) == 1
    assert "definitely-not-installed" in capsys.readouterr().err


def test_completions_are_emitted(repo: Path, capsys):
    assert main(["--completions", "zsh"]) == 0
    assert "#compdef make mk" in capsys.readouterr().out


def test_error_in_a_recipe_points_at_the_user_file(repo: Path, capsys):
    write(repo / "Makefile.py", "from make import recipe\n\n@recipe\ndef boom() -> None:\n    1 / 0\n")
    assert main(["boom"]) == 1
    err = capsys.readouterr().err
    assert "ZeroDivisionError" in err
    assert "Makefile.py:5" in err


# -- bootstrap -------------------------------------------------------------


def test_inline_metadata_is_read(project: Path):
    path = write(
        project / "Makefile.py",
        '# /// script\n# requires-python = ">=3.11"\n# dependencies = ["make", "requests>=2"]\n# ///\n',
    )
    metadata = read_metadata(path)
    assert metadata.dependencies == ["make", "requests>=2"]
    assert metadata.requires_python == ">=3.11"


def test_a_file_without_metadata_never_bootstraps(project: Path):
    path = write(project / "Makefile.py", "x = 1\n")
    assert read_metadata(path).empty
    assert not needs_bootstrap(read_metadata(path))


def test_a_satisfied_dependency_does_not_bootstrap():
    """Names the distribution, `mkrun` -- not the import name, `make`.

    Written as "make>=0.1" this passed on a machine with a pre-rename install
    still lying around and failed everywhere else.
    """
    assert not needs_bootstrap(ScriptMetadata(dependencies=["mkrun>=0.1"]))


def test_an_unsatisfied_dependency_bootstraps():
    assert needs_bootstrap(ScriptMetadata(dependencies=["mkrun>=99"]))
    assert needs_bootstrap(ScriptMetadata(dependencies=["not-a-real-package-xyz"]))


def test_an_unparseable_specifier_bootstraps_rather_than_guessing():
    assert needs_bootstrap(ScriptMetadata(dependencies=["mkrun @ git+ssh://example/x"]))
    assert needs_bootstrap(ScriptMetadata(dependencies=['mkrun; python_version < "3.0"']))


# -- the lockfile ----------------------------------------------------------


def test_the_lockfile_path_matches_uvs_convention(project: Path):
    from make.bootstrap import lock_path

    assert lock_path(project / "Makefile.py").name == "Makefile.py.lock"


def test_no_lockfile_means_resolve_from_the_declared_ranges(project: Path):
    from make.bootstrap import locked_interpreter

    assert locked_interpreter(project / "Makefile.py", "uv") is None


def test_a_lockfile_uv_cannot_use_falls_back_rather_than_failing(project: Path, monkeypatch):
    """A slower correct path beats a fast wrong one.

    If the lock is stale, corrupt, or its environment somehow lacks `make`, the
    run must still work -- by resolving from the declared ranges.
    """
    import subprocess

    from make.bootstrap import locked_interpreter

    write(project / "Makefile.py", "x = 1\n")
    write(project / "Makefile.py.lock", "version = 1\n[[package]]\nname = 'bogus'\n")

    def failing(argv, *args, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "no solution found")

    monkeypatch.setattr(subprocess, "run", failing)
    assert locked_interpreter(project / "Makefile.py", "uv") is None


def test_a_file_that_declares_the_tool_gets_no_second_source():
    """Two sources for one package is a hard error in uv.

    A consumer of a private recipe package pins the tool to a git URL; injecting
    our own `--with mkrun==...` on top made that combination fail outright.
    """
    from make.bootstrap import _declares_self, _requirement_name, _self_requirement

    git = ScriptMetadata(dependencies=["mkrun @ git+ssh://git@github.com/optersoft/make.git"])
    assert _requirement_name(git.dependencies[0]) == "mkrun"
    assert _declares_self(git)
    assert _self_requirement(git) == []

    pinned = ScriptMetadata(dependencies=["mkrun==0.1.0", "requests"])
    assert _declares_self(pinned)
    assert _self_requirement(pinned) == []


def test_a_file_that_does_not_declare_the_tool_still_gets_it():
    from make.bootstrap import _declares_self, _self_requirement

    metadata = ScriptMetadata(dependencies=["requests>=2"])
    assert not _declares_self(metadata)
    assert _self_requirement(metadata) != []


def test_requirement_names_normalise():
    from make.bootstrap import _requirement_name

    for written, expected in [
        ("acme_recipes_web>=0.1", "acme-recipes-web"),
        ("MkRun", "mkrun"),
        ("pkg[extra]==1.0", "pkg"),
        ('pkg ; python_version > "3.11"', "pkg"),
        ("pkg @ file:///tmp/x", "pkg"),
    ]:
        assert _requirement_name(written) == expected


def test_loading_a_recipe_file_leaves_no_pycache(repo: Path):
    """Most repos this runs in are Rust or Android projects that do not
    gitignore `__pycache__/`, so it shows up as untracked noise forever."""
    from make.discovery import load_recipe_file

    load_recipe_file(repo / "Makefile.py")
    assert not (repo / "__pycache__").exists()


def test_sync_passes_upgrade_through(project: Path, monkeypatch):
    """Moving a pin has to be asked for, and shows up as a diff."""
    import subprocess

    from make.bootstrap import ScriptMetadata, sync

    seen: list[list[str]] = []

    def spy(argv, *args, **kwargs):
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", spy)
    metadata = ScriptMetadata(dependencies=["requests"])

    sync(project / "Makefile.py", metadata)
    assert "--upgrade" not in seen[-1]

    sync(project / "Makefile.py", metadata, upgrade=True)
    assert "--upgrade" in seen[-1]


def test_version_names_the_distribution(repo: Path, capsys):
    """The distribution is `mkrun`; reporting bare "make" sends people to the
    wrong PyPI project, which is an unrelated jinja2 templating tool."""
    assert main(["--version"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("mkrun ")
    assert "make" in out
