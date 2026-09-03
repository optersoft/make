"""End-to-end: discovery, dispatch, listing, and the uv bootstrap."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from helpers import write

from make.bootstrap import ScriptMetadata, needs_bootstrap, read_metadata
from make.cli import main
from make.discovery import create_task_file, find_task_file, require_task_file
from make.errors import UsageError

TASKS = """
from pathlib import Path
from make import task, group, sh

web = group("web")

@web
def start(*, port: int = 8001) -> None:
    \"\"\"Start the dev server.\"\"\"
    sh("dx", "serve", "--port", port)

@web
def stop() -> None:
    \"\"\"Stop it.\"\"\"
    sh("pkill", "dx")

@task
def copy(src: Path, dest: str = ".") -> None:
    \"\"\"Copy a thing.\"\"\"
    sh("cp", src, dest)

@task(hidden=True)
def internal() -> None:
    sh("true")
"""


@pytest.fixture
def repo(project: Path) -> Path:
    write(project / "Makefile.py", TASKS)
    return project


def test_list_is_the_default(repo: Path, capsys):
    assert main([]) == 0
    assert "web.start" in capsys.readouterr().err


def test_hidden_tasks_are_not_listed(repo: Path, capsys):
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


def test_dispatch_runs_the_task(repo: Path, capsys):
    assert main(["--dry-run", "web.start", "--port", "9000"]) == 0
    assert "dx serve --port 9000" in capsys.readouterr().err


def test_several_tasks_in_one_invocation(repo: Path, capsys):
    assert main(["--dry-run", "web.start", "web.stop"]) == 0
    err = capsys.readouterr().err
    assert "dx serve" in err
    assert "pkill dx" in err


def test_an_open_positional_slot_wins_over_starting_a_new_task(repo: Path, capsys):
    """A value is never silently reinterpreted as the next task.

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


def test_task_help(repo: Path, capsys):
    assert main(["web.start", "--help"]) == 0
    out = capsys.readouterr().err
    assert "usage: web.start" in out
    assert "--port" in out


def test_unknown_task_exits_two(repo: Path, capsys):
    assert main(["nope"]) == 2
    assert "no task named" in capsys.readouterr().err


def test_discovery_walks_up(repo: Path, monkeypatch, capsys):
    nested = repo / "a" / "b"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert find_task_file(nested) == repo / "Makefile.py"
    assert main(["--dry-run", "web.stop"]) == 0


def test_tasks_run_at_the_task_file_root(repo: Path, monkeypatch, capsys):
    write(repo / "Makefile.py", TASKS + "\n@task\ndef where() -> None:\n    print(Path.cwd())\n")
    nested = repo / "deep"
    nested.mkdir()
    monkeypatch.chdir(nested)
    main(["where"])
    assert capsys.readouterr().out.strip() == str(repo)


def test_keep_cwd_runs_where_the_user_stood(repo: Path, monkeypatch, capsys):
    write(
        repo / "Makefile.py", TASKS + "\n@task(keep_cwd=True)\ndef where() -> None:\n    print(Path.cwd())\n"
    )
    nested = repo / "deep"
    nested.mkdir()
    monkeypatch.chdir(nested)
    main(["where"])
    assert capsys.readouterr().out.strip() == str(nested)


def test_missing_task_file_explains_how_to_start(project: Path):
    with pytest.raises(UsageError) as caught:
        require_task_file(project)
    assert "no task file found" in caught.value.message
    assert "Makefile.py" in (caught.value.hint or "")


def test_missing_task_file_is_an_error_off_a_terminal(project: Path, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))  # a pipe: nobody can answer
    assert main([]) == 2
    err = capsys.readouterr().err
    assert "no task file found" in err
    assert not (project / "Makefile.py").exists()


def test_missing_task_file_is_offered_on_a_terminal(project: Path, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", _Tty("y\n"))
    assert main([]) == 0
    err = capsys.readouterr().err
    assert "create" in err and "Makefile.py" in err
    created = project / "Makefile.py"
    assert created.is_file()
    assert "def hello" in created.read_text()
    assert "hello" in err  # ...and the new file's task list follows straight away


def test_declining_the_offer_creates_nothing(project: Path, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", _Tty("n\n"))
    assert main([]) == 130
    assert not (project / "Makefile.py").exists()


def test_yes_creates_the_task_file_without_asking(project: Path, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["--yes"]) == 0
    assert (project / "Makefile.py").is_file()
    assert "created" in capsys.readouterr().err


def test_create_task_file_refuses_to_overwrite(project: Path):
    write(project / "Makefile.py", "x = 1\n")
    with pytest.raises(UsageError):
        create_task_file(project)
    assert (project / "Makefile.py").read_text() == "x = 1\n"


class _Tty(io.StringIO):
    """stdin that claims to be a terminal, so `confirm()` reads the scripted answer."""

    def isatty(self) -> bool:
        return True


def test_a_stray_make_py_is_diagnosed(project: Path):
    write(project / "make.py", "x = 1\n")
    with pytest.raises(UsageError) as caught:
        require_task_file(project)
    assert "rename it to Makefile.py" in (caught.value.hint or "")


def test_env_flag_reaches_commands(repo: Path, capsys):
    write(
        repo / "Makefile.py",
        "from make import task, env\n\n@task\ndef show() -> None:\n    print(env.get('TOKEN'))\n",
    )
    assert main(["--env", "TOKEN=abc", "show"]) == 0
    assert capsys.readouterr().out.strip() == "abc"


def test_doctor_reports_a_missing_tool(repo: Path, capsys, monkeypatch):
    write(
        repo / "Makefile.py",
        'from make import task, sh\n\n@task(requires=["definitely-not-installed"])\n'
        "def build() -> None:\n    sh('true')\n",
    )
    assert main(["--doctor"]) == 1
    assert "definitely-not-installed" in capsys.readouterr().err


def test_the_only_installed_command_is_mk():
    """Installing this must never shadow GNU make.

    A `make` console script lands in ~/.local/bin and wins over /usr/bin/make on
    the PATH of essentially every Unix machine -- a large thing to take from
    someone who installed a task runner for one repository. The import name is
    still `make`; only the command is not.
    """
    import importlib.metadata as md

    entry_points = md.distribution("mkrun").entry_points
    assert {e.name for e in entry_points if e.group == "console_scripts"} == {"mk"}


def test_completions_are_emitted(repo: Path, capsys):
    assert main(["--completions", "zsh"]) == 0
    out = capsys.readouterr().out
    assert "#compdef mk" in out
    # Completing `make` would offer these tasks to someone building a C
    # project. The script says how to opt in; it must not do it for you.
    assert "#compdef make" not in out
    assert "compdef _mk make" in out, "the opt-in line for an alias should still be documented"


def test_error_in_a_task_points_at_the_user_file(repo: Path, capsys):
    write(repo / "Makefile.py", "from make import task\n\n@task\ndef boom() -> None:\n    1 / 0\n")
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
    assert not needs_bootstrap(ScriptMetadata(dependencies=["mkrun>=0.2"]))


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


def test_no_lockfile_and_no_sources_resolves_from_the_declared_ranges(project: Path, monkeypatch):
    """The plain case stays on the fast path: `uv run --with`, no script sync.

    Script mode reads the file, which is what `[tool.uv.sources]` needs, but it
    costs a `uv sync` per run. A file that declares neither a source nor a lock
    has nothing to gain from it.
    """
    import subprocess

    from make.bootstrap import ScriptMetadata, reexec

    seen: list[list[str]] = []

    def record(argv, *args, **kwargs):
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", record)
    reexec(ScriptMetadata(dependencies=["httpx"]), ["--list"], project / "Makefile.py")

    assert seen, "nothing ran"
    assert "--with" in seen[-1]
    assert not any("sync" in command and "--script" in command for command in seen)


def test_a_lockfile_uv_cannot_use_falls_back_rather_than_failing(project: Path, monkeypatch):
    """A slower correct path beats a fast wrong one.

    If the lock is stale, corrupt, or its environment somehow lacks `make`, the
    run must still work -- by resolving from the declared ranges. This is only
    safe because no source is declared; when one is, resolving from the ranges
    would silently fetch a different package, so `reexec` fails instead.
    """
    import subprocess

    from make.bootstrap import script_interpreter

    write(project / "Makefile.py", "x = 1\n")
    write(project / "Makefile.py.lock", "version = 1\n[[package]]\nname = 'bogus'\n")

    def failing(argv, *args, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "no solution found")

    monkeypatch.setattr(subprocess, "run", failing)
    assert script_interpreter(project / "Makefile.py", "uv") is None


def test_a_file_that_declares_the_tool_gets_no_second_source():
    """Two sources for one package is a hard error in uv.

    A consumer of a private task package pins the tool to a git URL; injecting
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
        ("acme_tasks_web>=0.1", "acme-tasks-web"),
        ("MkRun", "mkrun"),
        ("pkg[extra]==1.0", "pkg"),
        ('pkg ; python_version > "3.11"', "pkg"),
        ("pkg @ file:///tmp/x", "pkg"),
    ]:
        assert _requirement_name(written) == expected


def test_loading_a_task_file_leaves_no_pycache(repo: Path):
    """Most repos this runs in are Rust or Android projects that do not
    gitignore `__pycache__/`, so it shows up as untracked noise forever."""
    from make.discovery import load_task_file

    load_task_file(repo / "Makefile.py")
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


def test_bootstrap_checks_name_the_distribution_not_the_import_name():
    """`make` is the import name; `mkrun` is the distribution.

    An environment carrying both -- easy after the rename, or by installing the
    unrelated PyPI `make` -- once made a dependency check pass locally and fail
    in CI. Nothing here may depend on a distribution called `make` existing.
    """
    source = Path(__file__).read_text()
    for line in source.splitlines():
        if "ScriptMetadata(dependencies=" in line:
            assert '"make' not in line and "'make" not in line, line


def test_docstring_task_names_expand_braces_and_slashes():
    from make.cli import docstring_task_names

    doc = """Tasks.

        mk check                     type-check
        mk test.{unit,http}          suites
        mk server.build / server.deploy [--restart]
        mk android.{auto,
                    test-auto}
        mk gateway.build / .deploy / .deploy-all
        mk --doctor
    """
    assert docstring_task_names(doc) == [
        "check",
        "test.unit",
        "test.http",
        "server.build",
        "server.deploy",
        "android.auto",
        "android.test-auto",
        "gateway.build",
        "gateway.deploy",
        "gateway.deploy-all",
    ]


def test_doctor_warns_about_a_stale_docstring(repo: Path, capsys):
    write(repo / "Makefile.py", '"""Tasks.\n\n    mk web.start\n    mk web.gone / vanished\n"""\n' + TASKS)
    main(["--doctor"])
    err = capsys.readouterr().err
    assert "do not exist: vanished, web.gone" in err
    assert "web.start" not in err.split("do not exist:")[-1]
