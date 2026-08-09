#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
#
# todo-loop — drive `claude -p` in a loop until this repo's TODO.md has no open items.
#
# One iteration = one fresh `claude -p` process that reads TODO.md cold, does exactly ONE
# item, runs the verification gate, updates TODO.md, and commits. Context never
# accumulates across items, so the loop can run for hours; all continuity lives in the two
# durable places the next iteration can read — TODO.md and `git log`.
#
# THE MODEL NEVER DECIDES WHEN TO STOP. This driver counts `- [ ]` checkboxes in TODO.md
# between iterations and stops on zero, on a stall, or on a budget/iteration/time ceiling.
# The agent's own JSON report is logged and summarised but is never a stop condition — a
# model that believes it is finished is not evidence that it is.
#
# Work happens in a throwaway git worktree on a `todo-loop/<date>` branch, so your main
# checkout stays clean and usable while the loop runs and nothing it does can reach main
# or production. A PreToolUse hook (agent/deny.py) blocks push/deploy/remote-host commands.
#
# Generic across repos: the consumer's `agent.just` recipe injects --gate from the
# `todo_loop_gate` variable.
#
#   agent/todo_loop.py --gate 'cargo check --features server' [--max-iters 40] [--dry-run]
#
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Open work. Blocked items are `- [?]` and deliberately do NOT match — that is how a
# question only a human can answer stops holding the loop open forever.
OPEN_RE = re.compile(r"^\s*[-*]\s+\[ \]", re.MULTILINE)
BLOCKED_RE = re.compile(r"^\s*[-*]\s+\[\?\]", re.MULTILINE)

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "item": {"type": "string", "description": "The TODO item worked on, verbatim."},
        "outcome": {
            "type": "string",
            "enum": ["completed", "blocked", "no_work", "failed"],
            "description": "completed = done and gate green; blocked = marked - [?]; "
            "no_work = nothing open left; failed = could not finish or commit.",
        },
        "gate_passed": {"type": "boolean"},
        "committed": {"type": "boolean"},
        "commit_subject": {"type": "string"},
        "decisions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Autonomous judgement calls recorded in TODO.md.",
        },
        "blocked_question": {
            "type": "string",
            "description": "If outcome=blocked, the one-line question that unblocks it.",
        },
        "notes": {"type": "string"},
    },
    "required": ["item", "outcome", "gate_passed", "committed"],
}


# ---------------------------------------------------------------- shell helpers


def git(repo: Path, *args: str, check: bool = True) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)
    if check and p.returncode != 0:
        die(f"git {' '.join(args)} failed: {p.stderr.strip() or p.stdout.strip()}")
    return p.stdout.strip()


def child_env() -> dict:
    """Environment for the gate and for `claude`, with uv's ephemeral venv stripped out.

    This script runs under `uv run --script`, which prepends its throwaway environment's
    bin/ to PATH and exports VIRTUAL_ENV. Inherited, that silently hijacks `python3` for
    every child — a python-based gate fails with "No module named pytest" on a repo that
    is perfectly fine, and the agent ends up running its own tooling inside a virtualenv
    nobody chose. Neither failure names uv, so strip it here rather than debug it later.
    """
    env = dict(os.environ)
    venv = env.pop("VIRTUAL_ENV", None)
    for key in ("UV", "UV_RUN_RECURSION_DEPTH"):
        env.pop(key, None)
    if venv:
        bad = str(Path(venv) / "bin")
        env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep) if p and p != bad)
    return env


def die(msg: str) -> "None":
    print(f"!!! {msg}", file=sys.stderr)
    sys.exit(1)


def say(msg: str = "") -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- loop state


def counts(todo: Path) -> tuple[int, int, str]:
    """(open items, blocked items, content hash) — the loop's entire view of progress."""
    if not todo.exists():
        return (0, 0, "")
    text = todo.read_text(encoding="utf-8", errors="replace")
    digest = hashlib.sha256(text.encode()).hexdigest()[:12]
    return (len(OPEN_RE.findall(text)), len(BLOCKED_RE.findall(text)), digest)


def snapshot(work: Path, todo: Path) -> tuple:
    open_n, blocked_n, digest = counts(todo)
    return (open_n, blocked_n, digest, git(work, "rev-parse", "HEAD", check=False))


# ---------------------------------------------------------------- prompt


def build_prompt(n: int, gate: str, todo_rel: str, open_n: int) -> str:
    return f"""\
You are iteration {n} of an unattended loop working through `{todo_rel}` in this repo.
There are {open_n} unchecked `- [ ]` item(s) left. Nobody is reading this session.

1. Read `{todo_rel}`, then `CLAUDE.md` and whatever guide files it points at. Skim
   `git log --oneline -15` so you don't redo work that already shipped.
2. Choose the SINGLE highest-leverage unchecked `- [ ]` item — prefer "Next up" order,
   and prefer an item that unblocks others. Work on **exactly one**.
3. Implement it completely. Match the surrounding code's idiom, naming and comment
   density rather than introducing your own.
4. Run the verification gate and make it pass:

       {gate}

   If you cannot get it green, restore the tree (`git checkout -- .`, `git clean -fd`),
   mark the item `- [?] BLOCKED — <what fails and what you tried>`, and stop there.
5. Update `{todo_rel}` in the SAME commit as the work: move the finished item into the
   Shipped table with its commit, re-derive "Next up" from what actually remains, and
   record any judgement call under `## Decisions made autonomously` (create that section
   if it isn't there).
6. Commit with `git add -A && git commit` and a conventional-commit subject. Do NOT push.
7. Return the JSON report. `outcome` must reflect what really happened — report
   "blocked" or "failed" honestly; the loop detects a stall either way, and a false
   "completed" only costs the next iteration time rediscovering it.

If there is genuinely no unchecked `- [ ]` item left, change nothing, commit nothing, and
return `outcome: "no_work"` immediately."""


# ---------------------------------------------------------------- claude runner


def write_settings(state: Path) -> Path:
    """Generate the --settings payload. Absolute path to deny.py, so it can't be committed
    (the shared repo lives at an unstable .just-shared/ location)."""
    path = state / "settings.json"
    path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "^Bash$",
                            "hooks": [{"type": "command", "command": str(HERE / "deny.py"), "timeout": 15}],
                        }
                    ]
                }
            },
            indent=2,
        )
    )
    return path


def run_claude(args, prompt: str, work: Path, settings: Path, budget: float, raw: Path):
    """One iteration. Returns (report|None, result_line|None, exit_code)."""
    cmd = [
        args.claude_bin,
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--json-schema",
        json.dumps(REPORT_SCHEMA),
        "--permission-mode",
        "bypassPermissions",
        "--settings",
        str(settings),
        "--append-system-prompt",
        (HERE / "doctrine.md").read_text(encoding="utf-8"),
        "--model",
        args.model,
        "--max-budget-usd",
        f"{budget:.2f}",
        # No way to ask a human, structurally. The doctrine explains what to do instead.
        "--disallowed-tools",
        "AskUserQuestion",
        "ExitPlanMode",
        "EnterPlanMode",
    ]
    if args.effort:
        cmd += ["--effort", args.effort]

    started = time.time()
    result_line = None
    with raw.open("w") as rawf:
        proc = subprocess.Popen(
            cmd,
            cwd=str(work),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            bufsize=1,
            env=child_env(),
        )
        proc.stdin.write(prompt)
        proc.stdin.close()
        for line in proc.stdout:
            rawf.write(line)
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "result":
                result_line = event
            else:
                trace(event, started, str(work))
        code = proc.wait()

    report = None
    if result_line:
        report = result_line.get("structured_output")
        if not isinstance(report, dict):
            # Degrade gracefully: the report is only for the log, never a stop signal.
            try:
                parsed = json.loads(result_line.get("result") or "")
                report = parsed if isinstance(parsed, dict) else None
            except (json.JSONDecodeError, TypeError):
                report = None
    return report, result_line, code


def trace(event: dict, started: float, work: str) -> None:
    """Compact live progress — an overnight run should be readable in `tail -f`."""
    if event.get("type") != "assistant":
        return
    elapsed = f"{time.time() - started:6.0f}s"
    for block in event.get("message", {}).get("content", []) or []:
        kind = block.get("type")
        if kind == "tool_use":
            name = block.get("name", "?")
            inp = block.get("input", {}) or {}
            detail = (
                inp.get("command")
                or inp.get("file_path")
                or inp.get("pattern")
                or inp.get("description")
                or ""
            )
            # The worktree path is long, absolute, and the same on every line — strip it,
            # or a 96-column trace shows nothing but the prefix.
            # Strip both forms: file_path args carry `<work>/`, while the agent's own Bash
            # commands tend to open with `cd "<work>" && …` — no trailing slash.
            detail = str(detail).replace(work + "/", "").replace(work, ".")
            detail = " ".join(detail.split())
            say(f"    {elapsed}  {name:<10} {detail[:96]}")
        elif kind == "text":
            text = " ".join(block.get("text", "").split())
            if text:
                say(f"    {elapsed}  ·          {text[:96]}")


# ---------------------------------------------------------------- worktree


def setup_worktree(repo: Path, in_place: bool) -> tuple[Path, str]:
    if in_place:
        return repo, git(repo, "rev-parse", "--abbrev-ref", "HEAD")

    stem = f"todo-loop/{datetime.now():%Y-%m-%d}"
    existing = set(git(repo, "branch", "--format=%(refname:short)").splitlines())
    branch, n = stem, 1
    while branch in existing:
        n += 1
        branch = f"{stem}-{n}"

    work = repo.parent / f".todo-loop-{repo.name}"
    if work.exists():
        die(
            f"{work} already exists — a previous run left it behind.\n"
            f"    Review + merge it, then: git -C {repo} worktree remove {work}"
        )
    git(repo, "worktree", "add", "-b", branch, str(work), "HEAD")
    return work, branch


# ---------------------------------------------------------------- main


def show_status(state: Path) -> None:
    log = state / "log.jsonl"
    if not log.exists():
        say(f"no run yet ({log})")
        return
    total = 0.0
    for line in log.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rep = row.get("report") or {}
        total += row["cost_usd"]
        flag = " " if row["progressed"] else "!"
        say(
            f"{flag}{row['iteration']:>3}  {row['at'][5:16]}  "
            f"{rep.get('outcome', '?'):<10} ${row['cost_usd']:>5.2f}  "
            f"open {row['open']:<3} blk {row['blocked']:<3}  {str(rep.get('item', ''))[:56]}"
        )
        for d in rep.get("decisions") or []:
            say(f"          decided: {d[:80]}")
        if rep.get("blocked_question"):
            say(f"          blocked: {rep['blocked_question'][:80]}")
        for d in row.get("denials") or []:
            say(f"          DENIED : {str(d)[:80]}")
    say(f"\n     total ${total:.2f}   (! = iteration changed nothing)")


def main() -> None:
    ap = argparse.ArgumentParser(prog="todo-loop", description="Run claude until TODO.md has no open items.")
    ap.add_argument("--gate", help="verification command; must pass per item")
    ap.add_argument("--status", action="store_true", help="print the run log and exit")
    ap.add_argument("--repo", default=".", help="repo root (default: cwd)")
    ap.add_argument("--todo", default="TODO.md")
    ap.add_argument("--max-iters", type=int, default=40)
    ap.add_argument("--max-usd", type=float, default=25.0, help="total budget, all iterations")
    ap.add_argument("--iter-usd", type=float, default=5.0, help="per-iteration ceiling")
    ap.add_argument("--max-minutes", type=int, default=0, help="wall-clock deadline (0 = none)")
    ap.add_argument("--stall", type=int, default=2, help="identical snapshots before giving up")
    ap.add_argument("--model", default="opus")
    ap.add_argument("--effort", default="", help="low|medium|high|xhigh|max (default: inherit)")
    ap.add_argument("--claude-bin", default="claude")
    ap.add_argument("--in-place", action="store_true", help="use the current checkout, no worktree")
    ap.add_argument("--skip-preflight-gate", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="preflight + print the prompt, run nothing")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    if git(repo, "rev-parse", "--show-toplevel", check=False) == "":
        die(f"{repo} is not a git repository")
    repo = Path(git(repo, "rev-parse", "--show-toplevel")).resolve()

    if args.status:
        show_status(repo / ".todo-loop")
        return
    if not args.gate:
        die("--gate is required (the consumer's `todo_loop_gate` variable)")

    if not (repo / args.todo).exists():
        die(f"no {args.todo} in {repo} — the loop has nothing to work from")
    # The driver's own state dir lives in the main checkout, so exclude it locally before
    # the dirty check — otherwise the *second* run of a repo that hasn't gitignored
    # `.todo-loop/` fails preflight blaming the user's tree for the loop's own artefacts.
    exclude = repo / ".git" / "info" / "exclude"
    if exclude.parent.is_dir():
        body = exclude.read_text() if exclude.exists() else ""
        if ".todo-loop/" not in body:
            exclude.write_text(body.rstrip("\n") + "\n.todo-loop/\n")

    # --dry-run spends nothing and touches nothing, so it must stay usable mid-edit.
    if git(repo, "status", "--porcelain") and not args.dry_run:
        die("working tree is dirty — commit or stash first, so the loop's diff is reviewable")
    if not shutil.which(args.claude_bin):
        die(f"{args.claude_bin} not on PATH")

    open_n, blocked_n, _ = counts(repo / args.todo)
    say(f"==> {repo.name}: {open_n} open, {blocked_n} blocked in {args.todo}")
    if open_n == 0:
        say("    nothing to do.")
        return

    if not args.skip_preflight_gate and not args.dry_run:
        say(f"==> preflight gate: {args.gate}")
        if subprocess.run(args.gate, shell=True, cwd=str(repo), env=child_env()).returncode != 0:
            die(
                "gate is already failing on a clean tree — fix that first, or the loop "
                "will blame itself for it and mark every item BLOCKED"
            )

    if args.dry_run:
        say(f"==> would run in a worktree on todo-loop/{datetime.now():%Y-%m-%d}")
        say(f"==> gate: {args.gate}")
        say("--- iteration prompt " + "-" * 58)
        say(build_prompt(1, args.gate, args.todo, open_n))
        return

    work, branch = setup_worktree(repo, args.in_place)
    state = repo / ".todo-loop"
    state.mkdir(exist_ok=True)
    stop_file, log = state / "STOP", state / "log.jsonl"
    stop_file.unlink(missing_ok=True)
    settings = write_settings(state)

    say(f"==> worktree {work}  on {branch}")
    say(f"==> log {log}   (touch {stop_file} to stop after the current item)")

    deadline = time.time() + args.max_minutes * 60 if args.max_minutes else float("inf")
    todo = work / args.todo
    # `n` is the iteration being *considered*; `ran` counts those that actually spawned a
    # claude process — they differ whenever the loop breaks on a top-of-loop guard.
    spent, stalls, n, ran, reason = 0.0, 0, 0, 0, "unknown"
    prev = snapshot(work, todo)

    while True:
        n += 1
        open_n, blocked_n, _, _ = prev
        if open_n == 0:
            reason = "done — no open items left"
            break
        if n > args.max_iters:
            reason = f"iteration cap ({args.max_iters})"
            break
        if spent >= args.max_usd:
            reason = f"budget cap (${args.max_usd:.2f})"
            break
        if time.time() > deadline:
            reason = f"time limit ({args.max_minutes} min)"
            break
        if stop_file.exists():
            reason = "STOP file"
            break

        say()
        say(f"=== iteration {n}  ({open_n} open, {blocked_n} blocked, ${spent:.2f} spent)")
        budget = min(args.iter_usd, args.max_usd - spent)
        report, result, code = run_claude(
            args,
            build_prompt(n, args.gate, args.todo, open_n),
            work,
            settings,
            budget,
            state / f"iter-{n:03d}.jsonl",
        )
        ran += 1

        cost = float((result or {}).get("total_cost_usd") or 0.0)
        spent += cost
        cur = snapshot(work, todo)
        moved = cur != prev
        outcome = (report or {}).get("outcome", "?")

        with log.open("a") as f:
            f.write(
                json.dumps(
                    {
                        "iteration": n,
                        "at": datetime.now().isoformat(timespec="seconds"),
                        "exit_code": code,
                        "cost_usd": round(cost, 4),
                        "open": cur[0],
                        "blocked": cur[1],
                        "head": cur[3],
                        "progressed": moved,
                        "report": report,
                        "denials": (result or {}).get("permission_denials") or [],
                    }
                )
                + "\n"
            )

        say(f"--- {outcome}: {str((report or {}).get('item', ''))[:70]}")
        for d in (report or {}).get("decisions") or []:
            say(f"    decided: {d[:96]}")
        if (report or {}).get("blocked_question"):
            say(f"    blocked: {report['blocked_question'][:96]}")
        say(f"    ${cost:.2f}  open {prev[0]}→{cur[0]}  blocked {prev[1]}→{cur[1]}")

        if code != 0 and not moved:
            reason = f"claude exited {code} with no progress"
            break

        # The stall detector is the real cost ceiling: identical open count, identical
        # blocked count, identical TODO.md, identical HEAD means the iteration achieved
        # literally nothing. Two of those in a row and it is not going to.
        stalls = 0 if moved else stalls + 1
        if stalls >= args.stall:
            reason = f"stalled — {stalls} iterations with no change"
            break
        prev = cur

    open_n, blocked_n, _, _ = snapshot(work, todo)
    say()
    say(f"==> stopped: {reason}")
    say(f"    {ran} iteration(s), ${spent:.2f}, {open_n} open / {blocked_n} blocked")
    say(f"    git -C {work} log --oneline {git(repo, 'rev-parse', '--abbrev-ref', 'HEAD')}..")
    if blocked_n:
        say(f"    answer the `- [?] BLOCKED` items in {todo}, then run again")
    if not args.in_place:
        say(f"    merge:  git -C {repo} merge {branch}")
        say(f"    clean:  git -C {repo} worktree remove {work}")


if __name__ == "__main__":
    main()
