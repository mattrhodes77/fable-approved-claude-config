---
name: resume-fleet
version: 0.1
description: Auto-resume a fleet of Claude Code CLI sessions that hit the 5-hour usage limit. Use when you have many Claude Code sessions open in VS Code / Cursor terminal tabs and want them to continue automatically when the limit resets (e.g. "restart my capped sessions in 5 hours", "resume all my Claude sessions after the limit resets", "my sessions hit the session limit, continue them at reset"). Schedules a detached job that, at reset, sends Esc + "continue" ONLY to tabs actually blocked on the limit popup — working/wrapped/idle tabs are left untouched.
---

# resume-fleet — auto-continue capped Claude Code sessions at reset

You run N Claude Code sessions across editor terminal tabs. When the shared 5-hour
window is exhausted, each blocked session parks on the usage-limit popup
(`Stop and wait for limit to reset / Add funds / Switch to Team plan`). This skill
schedules a detached job that, when the window resets, walks every terminal tab and
sends **Esc → `continue` → Return** to exactly the tabs sitting on that popup — and
nothing else.

## How it works (and why it's built this way)

- **Input must be keystrokes.** macOS blocks `TIOCSTI` TTY injection, and the editor
  renders its terminal to a canvas that can't be read from outside. So we drive the
  editor's own commands via `osascript`/System Events.
- **Cycling** uses three dedicated keybindings (`f17/f18/f19` → terminal
  focusNext / selectAll / copySelection). The command palette is too fuzzy to trust.
- **Detection** reads each tab (selectAll → copySelection → `pbpaste`) and flags a tab
  as blocked ONLY when the **last on-screen line** is the popup's `Enter to confirm ·
  Esc to cancel` action line with a limit-menu option right above it (or the inline
  `hit your monthly spend limit` notice is itself the last line). A "usage limit" line
  that has scrolled up above a live prompt (a subagent-level error) does NOT match —
  those sessions aren't blocked and are skipped.
- **Bracketing.** A monthly-spend-cap label is a known CC UI bug for the 5-hour limit;
  the real reset is ~5h from first use. The scheduler fires a bit before the estimate
  and retries every ~10 min, stopping after 2 consecutive empty rounds.

## Files (next to this SKILL.md)

- `resume_fleet.sh` — one pass: detect blocked tabs, optionally send Esc+continue.
- `resume_scheduler.sh` — wait until reset, then run `resume_fleet` in retry rounds.
- `install_keybindings.sh` — idempotently add the `f17/f18/f19` bindings.

## Usage

### 0. One-time setup — install the keybindings
```bash
# VS Code (default). For Cursor: EDITOR_DIR="Cursor"
bash ~/.claude/skills/resume-fleet/install_keybindings.sh
```
The editor reloads `keybindings.json` live; no restart needed.

### 1. Dry-run detection (sends NOTHING — validate first)
```bash
MODE=detect bash ~/.claude/skills/resume-fleet/resume_fleet.sh
cat ~/.claude/resume_fleet.log     # shows which tabs read as BLOCKED vs skipped
```
Run this while sessions are actually capped to confirm it flags the right tabs. It
briefly steals focus and flickers through the tabs (selectAll/copy on each).

### 2. Arm the scheduler for the reset
Compute the first-fire epoch (~5–10 min before your estimated reset) and launch
detached so it survives this session:
```bash
TARGET=$(date -j -f "%Y-%m-%d %H:%M" "YYYY-MM-DD HH:MM" +%s)   # your reset clock time
nohup caffeinate -i env TARGET="$TARGET" ROUNDS=10 GAP=600 \
  bash ~/.claude/skills/resume-fleet/resume_scheduler.sh >/dev/null 2>&1 &
```
- **Check:** `cat ~/.claude/resume_fleet.log`
- **Cancel:** `pkill -f resume_scheduler.sh`

### Cursor instead of VS Code
Prefix all commands with `EDITOR_APP="Cursor" EDITOR_PROC="Cursor"` (and use
`EDITOR_DIR="Cursor"` for the keybinding installer).

## Config (env)

| var | default | meaning |
|-----|---------|---------|
| `EDITOR_APP` | `Visual Studio Code` | app to `activate` |
| `EDITOR_PROC` | `Code` | System Events process name |
| `NTABS` | auto | # of terminal tabs to cycle (auto = claude procs in that editor) |
| `MODE` | `act` | `detect` = read-only |
| `TARGET` | now+3h | epoch of first scheduler fire |
| `ROUNDS` / `GAP` / `DRY_STOP` | 10 / 600 / 2 | retry-round shape |

## Caveats (be honest with the user)

1. **Focus-steal.** Every round grabs editor focus for ~30s. Fine on an always-on
   machine you're not touching; warn if they'll be working during the window.
2. **Popup-only.** It resumes sessions blocked on the limit popup. Sessions whose
   *subagents* errored but whose main prompt is live are left alone (they don't need
   `continue`); if they later block on the popup, a retry round catches them.
3. **Timing.** The retry window must cover the real reset. If unsure of the exact
   reset time, widen `ROUNDS`/`GAP`.
4. **macOS + VS Code/Cursor only.** Relies on `osascript`, `pbpaste`, and the editor's
   terminal keybindings. Not for tmux/iTerm/plain Terminal (for tmux, `capture-pane`
   + `send-keys` is simpler and more robust — a better fit if you control launch).
5. **Accessibility permission** must be granted to the editor (System Settings →
   Privacy & Security → Accessibility) or the keystrokes silently no-op.
