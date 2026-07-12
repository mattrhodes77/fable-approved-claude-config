---
name: resume-fleet
description: Auto-resume a fleet of Claude Code CLI sessions that hit the usage limit. A hands-off launchd daemon (recommended) resumes them within minutes of any reset; or an on-demand scheduler arms for a specific reset time. Sends Esc + "continue" ONLY to VS Code / Cursor terminal tabs blocked on the limit popup; working/wrapped tabs are untouched. Use for "install the resume daemon", "resume my capped sessions at reset".
argument-hint: "[daemon | daemon-off | status | detect | <reset clock time e.g. 01:50> | cancel]"
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
`~/.claude/skills/resume-fleet/`. Prefer the hands-off daemon; the timed scheduler is the
on-demand alternative.

Behavior by `$ARGUMENTS`:

- **`daemon` / `install`** (RECOMMENDED) → install the always-on launchd daemon:
  `bash ~/.claude/skills/resume-fleet/install_keybindings.sh` then
  `bash ~/.claude/skills/resume-fleet/install_daemon.sh install`. Report status + how it
  behaves (edge-triggered, notifies on resume, disable/uninstall). For Cursor thread the
  `EDITOR_*` envs. Pass `RF_SELF=<current session id>` to skip this session in edge scans.
- **`daemon-off` / `disable`** → `install_daemon.sh disable` (soft) or `uninstall` (full);
  confirm which the user wants if ambiguous.
- **`status`** → `install_daemon.sh status` + tail the daemon log.
- **`cancel` / `stop`** → `pkill -f resume_scheduler.sh` (on-demand job), confirm nothing is
  armed (`pgrep -fl resume_scheduler.sh`), report, exit.
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
