"""Shell completion, generated from the signatures rather than hand-maintained.

Each script asks the tool itself (`mk --names`) for the recipes visible in the
current directory, so completion follows whatever the recipe file imports --
including recipes that arrived with a dependency.

The command is `mk`. These deliberately do not complete `make`: that name
belongs to GNU make, and completing it here would offer this tool's recipes to
someone building a C project. Anyone aliasing `make=mk` can add the one line
each script needs, which is noted in it.
"""

from __future__ import annotations

from .errors import UsageError

__all__ = ["emit"]

_BASH = """\
# mk completion for bash -- source this, or drop it in /etc/bash_completion.d/
# Aliased make=mk? add: complete -F _mk_complete make
_mk_complete() {
    local cur prev
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    if [[ "$cur" == -* ]]; then
        COMPREPLY=( $(compgen -W "--list --help --version --dry-run --yes --force \
--jobs --quiet --verbose --cwd --file --env --json --doctor --sync --add --path --git \
--completions --no-bootstrap --traceback --no-color" -- "$cur") )
        return
    fi
    COMPREPLY=( $(compgen -W "$(mk --names 2>/dev/null)" -- "$cur") )
}
complete -F _mk_complete mk
"""

_ZSH = """\
#compdef mk
# mk completion for zsh -- put this on your $fpath as _mk
# Aliased make=mk? add: compdef _mk make
_mk() {
    local -a recipes
    recipes=(${(f)"$(mk --names 2>/dev/null)"})
    _arguments -s \\
        '(-l --list)'{-l,--list}'[list recipes]' \\
        '(-n --dry-run)'{-n,--dry-run}'[print commands instead of running them]' \\
        '(-y --yes)'{-y,--yes}'[pre-answer confirmations]' \\
        '(-f --force)'{-f,--force}'[ignore staleness]' \\
        '(-j --jobs)'{-j,--jobs}'[parallel prerequisites]:jobs:' \\
        '(-q --quiet)'{-q,--quiet}'[only show errors]' \\
        '(-v --verbose)'{-v,--verbose}'[more detail]' \\
        '(-C --cwd)'{-C,--cwd}'[change directory]:dir:_files -/' \\
        '(-F --file)'{-F,--file}'[recipe file]:file:_files' \\
        '--doctor[check declared tools and where each package resolved from]' \\
        '--sync[pin dependencies]' \\
        '--add[add a recipe package]:package:' \\
        '*:recipe:(${recipes})'
}
_mk "$@"
"""

_FISH = """\
# mk completion for fish -- save as ~/.config/fish/completions/mk.fish
# Aliased make=mk? add: complete -c make -f -a '(__mk_recipes)'
function __mk_recipes
    mk --names 2>/dev/null
end
complete -c mk -f -a '(__mk_recipes)'
complete -c mk -s l -l list    -d 'list recipes'
complete -c mk -s n -l dry-run -d 'print commands instead of running them'
complete -c mk -s y -l yes     -d 'pre-answer confirmations'
complete -c mk -s j -l jobs    -d 'parallel prerequisites' -r
complete -c mk -s F -l file    -d 'recipe file' -r
complete -c mk      -l doctor  -d 'check declared tools'
complete -c mk      -l sync    -d 'pin dependencies'
complete -c mk      -l add     -d 'add a recipe package' -r
"""

_SCRIPTS = {"bash": _BASH, "zsh": _ZSH, "fish": _FISH}


def emit(shell: str) -> str:
    try:
        return _SCRIPTS[shell.lower().strip()]
    except KeyError:
        raise UsageError(
            f"no completion script for {shell!r}", hint="supported shells: " + ", ".join(sorted(_SCRIPTS))
        ) from None
