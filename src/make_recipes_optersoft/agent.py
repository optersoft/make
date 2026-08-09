"""The `agent` group: run `claude -p` until TODO.md has no open items left.

Ported from `agent.just`. This is the cheapest group to move, because the work
was already Python -- `just` was only forwarding flags to `agent/todo_loop.py`
across a shell boundary. The scripts now ship *inside* the package, so they
arrive with the dependency instead of being fetched into a gitignored directory,
and the loop is importable rather than only executable.

Three safety properties are structural, not advisory, and must survive any edit:

1. **The driver owns termination.** `todo_loop.py` counts `- [ ]` checkboxes in
   TODO.md between iterations. The model's own JSON report is logged but must
   never become a stop condition, or a model that *believes* it is finished ends
   the run.
2. **`- [?]` is not `- [ ]`.** A blocked item stops counting as open, which is
   the only reason the loop can reach zero on a question only a human can
   answer. Do not tidy that marker anywhere.
3. **The PreToolUse deny hook is the boundary**, not the permission rules. The
   loop runs with `--permission-mode bypassPermissions` because nobody is there
   to answer a prompt, and hooks are the only control that fires regardless of
   mode. Its list is deliberately over-broad: a false positive costs one turn, a
   false negative pushes to a remote or restarts a production service.

What it does not do, by construction: work happens in a throwaway worktree on a
`todo-loop/<date>` branch, so the checkout stays usable and nothing reaches
main. You merge the branch yourself after reading it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from make import config, fs, group, note, sh
from make.errors import MakeError

agent = group("agent")

SCRIPT = Path(__file__).resolve().parent / "agent" / "todo_loop.py"


@config.section("agent")
@dataclass
class Agent:
    """Per-project agent configuration."""

    gate: str
    """The verification command every item must pass before it is checked off.

    Prefer a raw command (`cargo check --features server`) over a recipe: the
    loop runs in a fresh worktree, so a gate reaching into this package's own
    recipes would fail for the wrong reason.
    """


def repo_root() -> str:
    return sh.out("git", "rev-parse", "--show-toplevel", dry=".")


@agent.recipe(name="todo-loop", requires=["uv", "claude", "git"])
def todo_loop(*args: str) -> None:
    """Work TODO.md until it is empty -- one item per iteration, verified and committed.

    Defaults: 40 iterations, $25 total and $5 per item, stopping after two
    no-op iterations. Common overrides pass straight through: `--max-iters 3`
    for a short supervised run, `--max-minutes 480` overnight, `--model sonnet`,
    `--in-place` for no worktree, `--dry-run` to preflight without spending a
    token.
    """
    if not SCRIPT.is_file():  # pragma: no cover - packaging guard
        raise MakeError(f"the loop driver is missing from the package: {SCRIPT}")
    sh(str(SCRIPT), "--repo", repo_root(), "--gate", Agent.gate, *args)


@agent.recipe(name="todo-loop-dry", requires=["uv", "claude"])
def todo_loop_dry() -> None:
    """Preflight and print the exact prompt an iteration would get. Spends nothing."""
    todo_loop("--dry-run")


@agent.recipe(name="todo-loop-status")
def todo_loop_status() -> None:
    """What each iteration did, decided, and cost."""
    sh(str(SCRIPT), "--repo", repo_root(), "--status")


@agent.recipe(name="todo-loop-stop")
def todo_loop_stop() -> None:
    """Stop gracefully once the running item is committed, rather than mid-edit."""
    marker = Path(repo_root()) / ".todo-loop"
    fs.mkdir(marker)
    fs.touch(marker / "STOP")
    note("STOP set -- the loop exits once the running item is committed")
