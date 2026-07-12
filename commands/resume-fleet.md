---
name: resume-fleet
description: Auto-resume a fleet of Claude Code CLI sessions that hit the 5-hour usage limit. Schedules a detached job that, at reset, sends Esc + "continue" ONLY to VS Code / Cursor terminal tabs blocked on the limit popup; working/wrapped tabs are untouched. Use for "resume my capped sessions at reset", "continue all my Claude sessions in ~5h".
argument-hint: "[reset clock time e.g. 01:50, or 'detect' for a dry run, or 'cancel']"
allowed-tools:
  - Bash
  - Read
  - Edit
  - Write
  - AskUserQuestion
  - Skill
  - TaskCreate
  - TaskUpdate
---

Invoke the `resume-fleet` skill and follow its SKILL.md. Scripts live in
`~/.claude/skills/resume-fleet/`.

Behavior by `$ARGUMENTS`:

- **`cancel` / `stop`** → `pkill -f resume_scheduler.sh`, confirm nothing is armed
  (`pgrep -fl resume_scheduler.sh`), report, exit.
- **`detect`** → one read-only pass:
  `MODE=detect bash ~/.claude/skills/resume-fleet/resume_fleet.sh`, then show the
  BLOCKED-vs-skipped tally from `~/.claude/resume_fleet.log`. Send nothing.
- **a clock time (e.g. `01:50`) or empty** → arm the reset job:
  1. Ensure keybindings installed: `bash ~/.claude/skills/resume-fleet/install_keybindings.sh`
     (for Cursor pass `EDITOR_DIR="Cursor"`).
  2. Run ONE `MODE=detect` pass and report which tabs read as blocked, so the user
     sees it's targeting the right sessions before anything is scheduled.
  3. Determine the reset clock time — from `$ARGUMENTS` if given, else ask the user
     (default: ~5h from their first-use of the current window). Compute
     `TARGET=$(date -j -f "%Y-%m-%d %H:%M" "<date> <HH:MM>" +%s)` a few minutes before it.
  4. Arm detached:
     `nohup caffeinate -i env TARGET="$TARGET" ROUNDS=10 GAP=600 bash ~/.claude/skills/resume-fleet/resume_scheduler.sh >/dev/null 2>&1 &`
  5. Verify with `pgrep -fl resume_scheduler.sh` and report: first-fire time, retry
     window, how to check (`cat ~/.claude/resume_fleet.log`) and cancel
     (`/resume-fleet cancel`).

Always surface the SKILL.md caveats that apply: ~30s focus-steal per round, popup-only
targeting, and that the retry window must cover the real reset time.

If the user runs a non-default editor, thread `EDITOR_APP` / `EDITOR_PROC` /
`EDITOR_DIR` through (VS Code defaults; Cursor = `"Cursor"` for all three).

Args: $ARGUMENTS
