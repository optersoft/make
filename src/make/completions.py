"""Shell completion, generated from the signatures rather than hand-maintained.

Each script asks the tool itself (`make --names`) for the recipes visible in the
current directory, so completion follows whatever the recipe file imports --
including recipes that arrived with a dependency.
"""

from __future__ import annotations

from .errors import UsageError

__all__ = ["emit"]

_BASH = """\
# make completion for bash -- source this, or drop it in /etc/bash_completion.d/
_make_complete() {
    local cur prev
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    if [[ "$cur" == -* ]]; then
        COMPREPLY=( $(compgen -W "--list --help --version --dry-run --yes --force \
--jobs --quiet --verbose --cwd --file --env --json --doctor --sync --completions \
--no-bootstrap --traceback --no-color" -- "$cur") )
        return
    fi
    COMPREPLY=( $(compgen -W "$(make --names 2>/dev/null)" -- "$cur") )
}
complete -F _make_complete make mk
"""

_ZSH = """\
#compdef make mk
# make completion for zsh -- put this on your $fpath as _make
_make() {
    local -a recipes
    recipes=(${(f)"$(make --names 2>/dev/null)"})
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
        '--doctor[check declared tools]' \\
        '--sync[pin dependencies]' \\
        '*:recipe:(${recipes})'
}
_make "$@"
"""

_FISH = """\
# make completion for fish -- save as ~/.config/fish/completions/make.fish
function __make_recipes
    make --names 2>/dev/null
end
complete -c make -f -a '(__make_recipes)'
complete -c mk   -f -a '(__make_recipes)'
complete -c make -s l -l list    -d 'list recipes'
complete -c make -s n -l dry-run -d 'print commands instead of running them'
complete -c make -s y -l yes     -d 'pre-answer confirmations'
complete -c make -s j -l jobs    -d 'parallel prerequisites' -r
complete -c make -s F -l file    -d 'recipe file' -r
complete -c make      -l doctor  -d 'check declared tools'
complete -c make      -l sync    -d 'pin dependencies'
"""

_SCRIPTS = {"bash": _BASH, "zsh": _ZSH, "fish": _FISH}


def emit(shell: str) -> str:
    try:
        return _SCRIPTS[shell.lower().strip()]
    except KeyError:
        raise UsageError(
            f"no completion script for {shell!r}", hint="supported shells: " + ", ".join(sorted(_SCRIPTS))
        ) from None
