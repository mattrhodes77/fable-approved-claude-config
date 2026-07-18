#!/usr/bin/env python3
"""Stop / UserPromptSubmit hook: bump ~/.claude/babysit-heartbeat while THIS
session is actively running /babysit-prs.

Purpose: let the hourly launchd backup (com.you.claude-babysit-hourly)
tell a live/healthy interactive babysit apart from an absent or stuck one:
  - fresh heartbeat  -> a terminal is actively babysitting  -> backup SKIPS
  - stale (>~70min)  -> terminal stuck / blocked on a prompt -> backup RUNS
  - missing          -> no terminal running babysit          -> backup RUNS
A healthy `/loop 1h /babysit-prs` bumps this every sweep (UserPromptSubmit +
Stop), well inside the staleness window; a terminal wedged on a permission
prompt emits no further turns, so the heartbeat ages out and the backup covers.

Detection is PRECISE: we only bump when the transcript's recent tail contains
the skill's command-INVOCATION record — a user message whose content is the
`<command-message>…</command-message>\n<command-name>/babysit-prs</command-name>`
block. Prose that merely mentions babysit (even a session quoting that exact
block, like the one that authored this hook) lives in assistant/plain-user
records and never matches, so discussion sessions never suppress the backup.

Never writes to stdout — a UserPromptSubmit hook's stdout is injected into the
model's context. Always exits 0 so it can never block a turn.
"""
import sys
import os
import json
import time

HEARTBEAT = os.path.expanduser("~/.claude/babysit-heartbeat")
TAIL_BYTES = 4 * 1024 * 1024  # scan the last 4MB of the transcript


def _is_active_babysit(tpath):
    """True iff a /babysit-prs command-invocation record appears in the tail."""
    try:
        sz = os.path.getsize(tpath)
        with open(tpath, "rb") as f:
            if sz > TAIL_BYTES:
                f.seek(sz - TAIL_BYTES)
                f.readline()  # discard the partial first line
            data = f.read().decode("utf-8", "replace")
    except Exception:
        return False
    for line in data.splitlines():
        # cheap prefilter: skip anything that can't be the invocation record
        if "command-message" not in line or "/babysit-prs" not in line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("type") != "user":
            continue
        msg = rec.get("message", {})
        content = msg.get("content") if isinstance(msg, dict) else None
        # A genuine slash-command invocation is a user record whose content is a
        # plain string that STARTS WITH the command block. When the same block is
        # echoed inside tool results or prose it is embedded mid-blob (or the
        # content is a list of blocks), so it never starts with <command-message>.
        if not isinstance(content, str):
            continue
        if (content.lstrip().startswith("<command-message>")
                and "<command-name>/babysit-prs</command-name>" in content):
            return True
    return False


def main():
    # The headless backup run itself invokes /babysit-prs — never let it bump
    # the heartbeat, or a later fire would mistake it for a live interactive one.
    if os.environ.get("HEADLESS_BABYSIT") == "1":
        return
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    tpath = payload.get("transcript_path") or ""
    if not tpath or not os.path.isfile(tpath):
        return
    if not _is_active_babysit(tpath):
        return
    try:
        with open(HEARTBEAT, "w") as f:
            f.write("%d session=%s ppid=%d\n"
                    % (int(time.time()), payload.get("session_id", ""), os.getppid()))
    except Exception:
        pass


if __name__ == "__main__":
    main()
