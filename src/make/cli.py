"""The command line: `make [global flags] <recipe> [args] [<recipe> [args] ...]`.

Global flags come before the first recipe name; everything after a recipe name
belongs to that recipe. A second recipe name starts a new invocation only once
the current recipe cannot accept another positional argument -- so a recipe with
an optional positional, or with `*args`, keeps consuming.

That rule is chosen for predictability over convenience: a value is never
silently reinterpreted as the next recipe just because it happens to share a
name with one. `mk copy a.txt web.stop` copies to a directory called
`web.stop`, exactly as the signature says it should; to chain, fill the slots
(`mk copy a.txt . web.stop`) or run the two commands separately.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .context import Context, echo, error, paint, set_context, warn
from .errors import MakeError, UsageError
from .params import parse_args, render_usage
from .recipes import Recipe, registry
from .runner import Invocation, run

GLOBAL_HELP = """\
mk -- a command runner whose recipes are Python

usage: mk [options] <recipe> [arguments] [<recipe> [arguments] ...]

options:
  -l, --list             list recipes (the default with no recipe)
  -h, --help [RECIPE]    this text, or full help for one recipe
  -V, --version          print the version
  -n, --dry-run          print commands instead of running them
  -y, --yes              pre-answer confirmations for dangerous recipes
  -f, --force            ignore inputs=/outputs= staleness and run anyway
  -j, --jobs N           run independent prerequisites in parallel
  -q, --quiet            only show errors
  -v, --verbose          more detail (repeatable)
  -C, --cwd DIR          change to DIR before looking for the recipe file
  -F, --file PATH        use this recipe file
  -e, --env KEY=VALUE    set an environment variable for every command
      --json             machine-readable output (with --list)
      --doctor           check that every declared tool is installed
      --sync             resolve and pin the recipe file's dependencies
      --upgrade          with --sync, move the pins to the newest allowed
      --add PKG          with --sync, add a recipe package to the recipe file
      --path DIR         with --add, take it from a checkout beside this one
      --git URL          with --add, take it from a repository
      --completions SH   emit a completion script (bash, zsh, fish)
      --no-bootstrap     never re-execute through uv
      --traceback        show the full traceback on an unexpected error
      --no-color         disable colour

recipe files searched, from the current directory upward:
  Makefile.py, makefile.py, mk.py, .make/main.py
"""


class _Options:
    def __init__(self) -> None:
        self.list = False
        self.help: bool | str = False
        self.version = False
        self.dry_run = False
        self.yes = False
        self.force = False
        self.jobs = 1
        self.quiet = False
        self.verbose = 0
        self.cwd: Path | None = None
        self.file: Path | None = None
        self.env: dict[str, str] = {}
        self.json = False
        self.doctor = False
        self.sync = False
        self.upgrade = False
        self.add: str | None = None
        self.source_path: str | None = None
        self.source_git: str | None = None
        self.completions: str | None = None
        self.names = False
        self.bootstrap = True
        self.traceback = False
        self.color = True


def _parse_global(argv: list[str]) -> tuple[_Options, list[str]]:
    options = _Options()
    index = 0
    while index < len(argv):
        token = argv[index]
        if not token.startswith("-") or token == "--":
            break

        def value(flag: str, current: str = token) -> str:
            """`--jobs 4` or `--jobs=4`. `current` is bound per iteration, not captured."""
            nonlocal index
            if "=" in current:
                return current.split("=", 1)[1]
            index += 1
            if index >= len(argv):
                raise UsageError(f"{flag}: expected a value")
            return argv[index]

        name = token.split("=", 1)[0]
        if name in ("-l", "--list"):
            options.list = True
        elif name in ("-h", "--help"):
            nxt = argv[index + 1] if index + 1 < len(argv) else None
            if nxt and not nxt.startswith("-"):
                options.help = nxt
                index += 1
            else:
                options.help = True
        elif name in ("-V", "--version"):
            options.version = True
        elif name in ("-n", "--dry-run"):
            options.dry_run = True
        elif name in ("-y", "--yes"):
            options.yes = True
        elif name in ("-f", "--force"):
            options.force = True
        elif name in ("-j", "--jobs"):
            options.jobs = max(1, int(value(name)))
        elif name in ("-q", "--quiet"):
            options.quiet = True
        elif name in ("-v", "--verbose"):
            options.verbose += 1
        elif name in ("-C", "--cwd"):
            options.cwd = Path(value(name)).expanduser()
        elif name in ("-F", "--file"):
            options.file = Path(value(name)).expanduser()
        elif name in ("-e", "--env"):
            raw = value(name)
            if "=" not in raw:
                raise UsageError(f"--env expects KEY=VALUE, got {raw!r}")
            key, _, val = raw.partition("=")
            options.env[key] = val
        elif name == "--json":
            options.json = True
        elif name == "--doctor":
            options.doctor = True
        elif name == "--sync":
            options.sync = True
        elif name == "--upgrade":
            options.upgrade = True
        elif name == "--add":
            options.add = value(name)
        elif name == "--path":
            options.source_path = value(name)
        elif name == "--git":
            options.source_git = value(name)
        elif name == "--completions":
            options.completions = value(name)
        elif name == "--names":
            options.names = True  # bare recipe names, for shell completion
        elif name == "--no-bootstrap":
            options.bootstrap = False
        elif name == "--traceback":
            options.traceback = True
        elif name == "--no-color":
            options.color = False
        elif name == "--color":
            options.color = True
        else:
            raise UsageError(
                f"unknown option {name}", hint="global options come before the recipe name; run `mk --help`"
            )
        index += 1
    return options, argv[index:]


# --------------------------------------------------------------------------
# Splitting the tail into invocations
# --------------------------------------------------------------------------


def _split_invocations(tokens: list[str]) -> list[tuple[Recipe, list[str]]]:
    segments: list[tuple[Recipe, list[str]]] = []
    index = 0
    while index < len(tokens):
        name = tokens[index]
        item = registry.require(name)
        index += 1
        positional_slots = sum(1 for p in item.params if p.kind == "positional")
        greedy = any(p.kind == "varargs" for p in item.params)
        args: list[str] = []
        filled = 0
        while index < len(tokens):
            token = tokens[index]
            if token == "--":
                args.extend(tokens[index:])
                index = len(tokens)
                break
            if not token.startswith("-"):
                if not greedy and filled >= positional_slots and token in registry:
                    break  # the next recipe starts here
                filled += 1
            args.append(token)
            index += 1
        segments.append((item, args))
    return segments


# --------------------------------------------------------------------------
# Listing and help
# --------------------------------------------------------------------------


def _print_list(*, as_json: bool) -> int:
    items = registry.all()
    if as_json:
        import json

        payload = [
            {
                "name": item.full_name,
                "group": item.group,
                "summary": item.summary,
                "usage": item.usage,
                "abstract": item.abstract,
                "dangerous": item.dangerous,
                "aliases": list(item.aliases),
                "requires": list(item.requires),
                "params": [
                    {
                        "name": p.name,
                        "cli": p.flag if p.kind == "option" else p.metavar,
                        "kind": p.kind,
                        "type": p.type_label,
                        "required": p.required,
                        "help": p.help,
                        "choices": list(p.choices) if p.choices else None,
                    }
                    for p in item.params
                ],
            }
            for item in items
        ]
        print(json.dumps(payload, indent=2))
        return 0

    if not items:
        echo("no recipes defined")
        return 0

    width = max(len(_signature(item)) for item in items)
    width = min(width, 44)
    for group_name, group_items in registry.groups().items():
        echo()
        echo(paint(group_name or "(ungrouped)", "bold", "magenta"))
        for item in group_items:
            signature = _signature(item)
            summary = item.summary
            if item.abstract:
                summary = paint("[unimplemented] ", "yellow") + summary
            if item.dangerous:
                summary = paint("[dangerous] ", "red") + summary
            pad = " " * max(1, width - len(signature) + 2)
            echo(f"  {paint(signature, 'cyan')}{pad}{paint(summary, 'dim')}")
    echo()
    return 0


def _signature(item: Recipe) -> str:
    parts = [item.full_name]
    for param in item.params:
        if param.kind == "positional":
            parts.append(f"<{param.metavar}>" if param.required else f"[{param.metavar}]")
        elif param.kind == "varargs":
            parts.append(f"[{param.metavar}]")
    return " ".join(parts)


def _print_recipe_help(item: Recipe) -> int:
    echo(paint(item.full_name, "bold", "cyan") + (paint("  [dangerous]", "red") if item.dangerous else ""))
    if item.summary:
        echo("  " + item.summary)
    echo()
    echo("usage: " + render_usage(item.full_name, item.params))
    if item.description:
        echo()
        for line in item.description.splitlines():
            echo("  " + line)

    positionals = [p for p in item.params if p.kind in ("positional", "varargs")]
    options = [p for p in item.params if p.kind == "option"]
    if positionals:
        echo()
        echo(paint("arguments:", "bold"))
        for param in positionals:
            default = "" if param.required else f"  (default: {param.default!r})"
            echo(f"  {param.metavar:<20} {param.type_label}{default}")
            if param.help:
                echo(f"  {'':<20} {paint(param.help, 'dim')}")
    if options:
        echo()
        echo(paint("options:", "bold"))
        for param in options:
            flags = param.flag if not param.short else f"{param.short}, {param.flag}"
            if param.is_flag and param.default is True:
                flags = param.negative_flag
            suffix = "" if param.is_flag else f" <{param.type_label}>"
            default = "" if param.required else f"  (default: {param.default!r})"
            echo(f"  {flags + suffix:<28} {default}")
            if param.help:
                echo(f"  {'':<28} {paint(param.help, 'dim')}")
    if item.needs:
        echo()
        echo(paint("runs first: ", "bold") + ", ".join(item.resolved_needs()))
    if item.requires:
        echo(paint("requires:   ", "bold") + ", ".join(item.requires))
    if item.file:
        echo()
        echo(paint(f"defined at {item.file}:{item.lineno}", "dim"))
    return 0


def _recipe_packages(recipe_file: Path, metadata: object) -> None:
    """Where each declared recipe package actually came from.

    A source is the one thing about a recipe package you cannot see by reading
    the recipe file alone: a `.make/sources.toml` may be redirecting it to a
    checkout, and two copies of `box` from different places behave differently
    while looking identical. This prints the answer rather than leaving it to be
    deduced from a failure.
    """
    import importlib.metadata as md

    from .bootstrap import _requirement_name, read_overrides
    from .discovery import recipe_root

    dependencies = getattr(metadata, "dependencies", [])
    if not dependencies:
        return

    sources = getattr(metadata, "sources", {})
    try:
        overrides = read_overrides(recipe_root(recipe_file))
    except MakeError as exc:  # a broken override file must not hide the rest
        overrides = {}
        warn(str(exc))

    echo()
    echo(paint("recipe packages", "bold"))
    for requirement in dependencies:
        name = _requirement_name(requirement)
        try:
            installed = md.version(name)
        except md.PackageNotFoundError:
            installed = ""
        marker = paint("ok  ", "green") if installed else paint("--  ", "dim")

        if name in overrides:
            origin = f"path {overrides[name]['path']}"
            suffix = paint(f"  (override: {Path(overrides[name]['origin']).name})", "yellow")
        elif name in sources:
            spec = sources[name]
            detail = " ".join(f"{k}={v}" for k, v in spec.items() if k not in {"path", "git"})
            origin = f"path {spec['path']}" if "path" in spec else f"git {spec.get('git', '?')}"
            suffix = paint(f"  {detail}", "dim") if detail else ""
        else:
            origin, suffix = "PyPI", ""
        label = f"{name} {installed}" if installed else name
        echo(f"  {marker}{label:<28} {paint(origin, 'dim')}{suffix}")


def _doctor(recipe_file: Path | None = None, metadata: object = None) -> int:
    import shutil

    echo(paint("tools declared by recipes", "bold"))
    tools: dict[str, list[str]] = {}
    for item in registry.all(include_hidden=True):
        for tool in item.requires:
            tools.setdefault(tool, []).append(item.full_name)
    if not tools:
        echo("  (no recipe declares requires=)")
    missing = 0
    for tool in sorted(tools):
        path = shutil.which(tool)
        users = ", ".join(sorted(tools[tool])[:4])
        if path:
            echo(f"  {paint('ok  ', 'green')}{tool:<18} {paint(path, 'dim')}")
        else:
            missing += 1
            echo(f"  {paint('MISS', 'red')}{tool:<18} {paint('needed by ' + users, 'dim')}")

    if recipe_file is not None and metadata is not None:
        _recipe_packages(recipe_file, metadata)

    echo()
    echo(paint("environment", "bold"))
    from . import env as env_module

    for path in env_module.layer_files():
        echo(f"  {paint('ok  ', 'green')}{path}")
    if not env_module.layer_files():
        echo(f"  {paint('--  ', 'dim')}no env layers found ({env_module.config_dir()}/secrets.env)")

    for tool in ("uv", "git"):
        path = shutil.which(tool)
        marker = paint("ok  ", "green") if path else paint("MISS", "yellow")
        echo(f"  {marker}{tool:<18} {paint(path or 'not on PATH', 'dim')}")

    echo()
    abstract = [i.full_name for i in registry.all(include_hidden=True) if i.abstract]
    if abstract:
        warn("unimplemented recipes: " + ", ".join(abstract))
    return 1 if missing else 0


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(argv if argv is not None else sys.argv[1:])
    try:
        options, tail = _parse_global(raw)
    except MakeError as exc:
        _report(exc, traceback_wanted=False)
        return exc.exit_code

    if options.version:
        # The distribution is `mkrun`; the import name and commands are `make`.
        print(f"mkrun {__version__} (import name: make)")
        return 0
    if options.help is True and not tail:
        print(GLOBAL_HELP, end="")
        return 0
    if options.completions:
        from .completions import emit

        print(emit(options.completions))
        return 0

    invocation_dir = Path.cwd()
    if options.cwd:
        os.chdir(options.cwd)

    try:
        from .bootstrap import needs_bootstrap, read_metadata, reexec, sync
        from .discovery import load_recipe_file, require_recipe_file

        recipe_file = options.file.resolve() if options.file else require_recipe_file()
        if options.file and not recipe_file.is_file():
            raise UsageError(f"{recipe_file} does not exist")

        root = recipe_file.parent
        if recipe_file.name == "main.py" and root.name == ".make":
            root = root.parent

        set_context(
            Context(
                root=root,
                invocation_dir=invocation_dir,
                recipe_file=recipe_file,
                dry_run=options.dry_run,
                yes=options.yes,
                force=options.force,
                quiet=options.quiet,
                verbose=options.verbose,
                jobs=options.jobs,
                json=options.json,
                color=options.color and not os.environ.get("NO_COLOR"),
                env=dict(options.env),
            )
        )

        metadata = read_metadata(recipe_file)
        if options.add:
            from .bootstrap import add

            return add(recipe_file, options.add, source_path=options.source_path, git=options.source_git)
        if options.sync:
            return sync(recipe_file, metadata, upgrade=options.upgrade)
        if options.bootstrap and needs_bootstrap(metadata):
            return reexec(metadata, raw, recipe_file)

        os.chdir(root)
        load_recipe_file(recipe_file)
        registry.finalize()

        if options.names:
            for item in registry.all():
                print(item.full_name)
                for alias in item.aliases:
                    print(alias)
            return 0
        if options.doctor:
            return _doctor(recipe_file, metadata)
        if isinstance(options.help, str):
            return _print_recipe_help(registry.require(options.help))
        if options.list or not tail:
            return _print_list(as_json=options.json)

        invocations: list[Invocation] = []
        for item, tokens in _split_invocations(tail):
            if "--help" in tokens or "-h" in tokens:
                return _print_recipe_help(item)
            parsed = parse_args(item.params, tokens, recipe=item.full_name)
            invocations.append(Invocation(item, parsed.args, parsed.kwargs))

        run(invocations)
        return 0

    except MakeError as exc:
        _report(exc, traceback_wanted=options.traceback)
        return exc.exit_code
    except KeyboardInterrupt:
        echo()
        error("interrupted")
        return 130
    except Exception as exc:
        if options.traceback:
            raise
        import traceback as tb

        frames = tb.extract_tb(exc.__traceback__)
        user_frames = [f for f in frames if "/make/" not in f.filename or f.filename.endswith("Makefile.py")]
        error(f"{type(exc).__name__}: {exc}")
        if user_frames:
            last = user_frames[-1]
            echo(paint(f"  at {last.filename}:{last.lineno} in {last.name}", "dim"))
            if last.line:
                echo(paint(f"    {last.line}", "dim"))
        echo(paint("  (run with --traceback for the full trace)", "dim"))
        return 1


def _report(exc: MakeError, *, traceback_wanted: bool) -> None:
    error(exc.message)
    if exc.hint:
        for line in exc.hint.splitlines():
            echo(paint("  " + line, "dim"))
    if traceback_wanted:
        import traceback as tb

        tb.print_exc()


def console_main() -> None:  # pragma: no cover - console_scripts shim
    sys.exit(main())
