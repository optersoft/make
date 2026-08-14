#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
#
# PreToolUse hook: hard boundary for the unattended `todo-loop` agent.
#
# The loop runs with `--permission-mode bypassPermissions` (nobody is there to answer a
# prompt), so permission RULES are not a control. Hooks are — they fire regardless of
# permission mode, which is the whole reason the guard lives here and not in a settings
# allowlist.
#
# Wired up by todo_loop.py, which generates a `--settings` payload pointing at this file
# by absolute path (the shared repo lives at an unstable `.just-shared/` location, so the
# path can't be committed).
#
# Contract (code.claude.com/docs/en/hooks): read the event on stdin, print a
# `hookSpecificOutput.permissionDecision = "deny"` object on stdout, exit 0. Staying
# silent (exit 0, no output) means "no opinion" — the normal flow continues.
#
# Deliberately over-broad: a false positive costs the agent one turn and a clear reason,
# a false negative pushes to a remote or restarts a production service.
#
import json
import re
import sys

# (pattern, what the human-readable reason calls it)
FORBIDDEN = [
    (r"\bgit\s+push\b", "git push"),
    (r"\bgit\s+remote\b", "git remote (adding/changing a push target)"),
    (r"\bgit\s+(rebase|filter-branch|filter-repo)\b", "git history rewriting"),
    (r"\bgit\s+commit\b[^\n|;&]*--amend", "git commit --amend (history rewriting)"),
    (r"\bgit\s+reset\s+--hard\s+\S", "git reset --hard onto another ref"),
    (r"\bgit\s+config\s+--global\b", "git config --global"),
    (r"\bssh\b", "ssh (reaching a remote host)"),
    (r"\bscp\b", "scp (reaching a remote host)"),
    (r"\bsftp\b", "sftp (reaching a remote host)"),
    (r"\brsync\b", "rsync (reaching a remote host)"),
    (r"\bsystemctl\b", "systemctl (service control)"),
    (r"\bsudo\b", "sudo"),
    (r"\bdoas\b", "doas"),
    (r"\bdocker\b[^\n|;&]*\bpush\b", "docker push"),
    (r"\bdocker\s+(compose\s+)?(up|run|start|restart)\b", "starting containers"),
    (r"\bkubectl\b", "kubectl"),
    (r"\bterraform\s+(apply|destroy)\b", "terraform apply/destroy"),
    (r"\bcargo\s+publish\b", "cargo publish"),
    (r"\b(npm|pnpm|yarn|uv|twine)\s+publish\b", "package publish"),
    (r"\bjust\s+[a-z0-9-]*deploy\b", "a just deploy recipe"),
    (r"\bjust\s+play-\w+", "a just play-* (Google Play) recipe"),
    (r"\bjust\s+(android-release|android-publish|ship-host)\b", "a just release recipe"),
    (r"\bdx\s+deploy\b", "dx deploy"),
    (r"\bfastlane\b", "fastlane"),
    (r"\bgh\s+(pr|release|repo|workflow)\s+(create|edit|merge|delete|run|upload)\b", "a mutating gh command"),
    (r"\bglab\s+(mr|release|repo)\s+(create|update|merge|delete)\b", "a mutating glab command"),
    (r"\b(gh|glab)\s+api\b[^\n|;&]*-X\s*(POST|PUT|PATCH|DELETE)", "a mutating API call"),
    (r"\brm\s+(-[a-zA-Z]*\s+)*(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\s+[~/]\s*$", "rm -rf on / or ~"),
    (r"\bhetzner\S*\s+(create|delete|reboot|rescue)\b", "a Hetzner control-plane action"),
]

REASON = (
    "todo-loop guardrail: {what} is blocked. This loop runs unattended and may only edit "
    "and commit inside its own worktree — it must never push, deploy, or touch a live "
    "host. If the TODO item genuinely requires this, do not work around it: mark the item "
    "`- [?] BLOCKED — <question>` in TODO.md and move on."
)


def deny(what: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": REASON.format(what=what),
            }
        },
        sys.stdout,
    )
    sys.exit(0)


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        # Malformed event: no opinion. Failing open here is correct — failing closed on a
        # parse error would deadlock every Bash call in the loop.
        sys.exit(0)

    if event.get("tool_name") != "Bash":
        sys.exit(0)

    command = str(event.get("tool_input", {}).get("command", ""))
    for pattern, what in FORBIDDEN:
        if re.search(pattern, command):
            deny(what)
    sys.exit(0)


if __name__ == "__main__":
    main()
