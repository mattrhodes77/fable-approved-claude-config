#!/bin/bash
# resume-fleet v0.1 — one pass over a VS Code / Cursor terminal-tab fleet: send
# Esc + "continue" ONLY to Claude Code sessions BLOCKED on the usage-limit popup.
# Working / wrapped / idle sessions are left untouched.
#
#   MODE=detect  read-only: classify every tab, send NOTHING (validation)
#   MODE=act     (default)  send Esc+continue to popup-blocked tabs
#
# Why keystrokes and not TTY writes: macOS blocks TIOCSTI input injection, and the
# editor renders its terminal to a canvas that isn't readable from outside — so we
# drive the editor's own commands. Cycling uses THREE dedicated keybindings you must
# add to the editor's keybindings.json (the skill does this for you):
#   f17 -> workbench.action.terminal.focusNext
#   f18 -> workbench.action.terminal.selectAll   (when terminalFocus)
#   f19 -> workbench.action.terminal.copySelection (when terminalFocus)
#
# CONFIG (env):
#   EDITOR_APP   macOS app to activate     (default "Visual Studio Code"; Cursor: "Cursor")
#   EDITOR_PROC  System Events process     (default "Code"; Cursor: "Cursor")
#   NTABS        tabs to cycle             (default: auto = # of claude procs in that editor)
#   MODE         detect|act                (default act)
#   LOG          log file                  (default ~/.claude/resume_fleet.log)

set -o pipefail
EDITOR_APP="${EDITOR_APP:-Visual Studio Code}"
EDITOR_PROC="${EDITOR_PROC:-Code}"
MODE="${MODE:-act}"
LOG="${LOG:-$HOME/.claude/resume_fleet.log}"
MAXTABS="${MAXTABS:-24}"

# Evidence that a bottom-of-screen popup is a USAGE-LIMIT popup (form-agnostic: monthly
# spend, per-model 5h e.g. "reached your Fable 5 limit", usage-limit-reached, credits).
# Kept broad because it only counts when it sits DIRECTLY above the popup action line.
CAP_REGEX='Stop and wait for limit to reset|Add funds to continue|Switch to Team plan|hit your .*limit|reached your .*limit|usage limit reached|Run /usage-credits'
# Inline blocking notices safe to auto-continue (validated forms only — NOT the soft
# "reached your <model> limit … or switch" notice, which the session flows past).
INLINE_RX='hit your monthly spend limit|Claude usage limit reached'

log(){ echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG" ; }
key(){ osascript -e "tell application \"System Events\" to tell process \"$EDITOR_PROC\" to key code $1" >/dev/null 2>&1; }
type_str(){ osascript -e "tell application \"System Events\" to tell process \"$EDITOR_PROC\" to keystroke \"$1\"" >/dev/null 2>&1; }
activate(){ osascript -e "tell application \"$EDITOR_APP\" to activate" >/dev/null 2>&1; sleep 0.5; }

read_buf(){                     # focus already on target tab; echoes the visible buffer
  local b tries=0
  while [ "$tries" -lt 3 ]; do
    printf '' | pbcopy           # clear clipboard so a FAILED copy yields empty, not stale
    key 79; sleep 0.25          # F18 selectAll
    key 80; sleep 0.25          # F19 copySelection
    b="$(pbpaste)"
    [ -n "$b" ] && break
    tries=$((tries+1)); sleep 0.2
  done
  key 53                        # Esc: clear the selection highlight
  printf '%s' "$b"
}

# auto count of claude terminals hosted by the configured editor
ntabs(){
  ps -axo pid=,comm= | awk '$2 ~ /claude$/ {print $1}' | while read -r p; do
    up="$p"
    for _ in 1 2 3 4 5 6 7 8; do
      read -r pp cc <<<"$(ps -o ppid=,comm= -p "$up" 2>/dev/null)"
      [ -z "$pp" ] && break
      case "$cc" in *MacOS/"$EDITOR_PROC"|*"$EDITOR_APP"*) echo x; break;; esac
      up="$pp"; [ "$up" -le 1 ] && break
    done
  done | wc -l | tr -d ' '
}

activate
NTABS="${NTABS:-$(ntabs)}"; [ "${NTABS:-0}" -lt 1 ] && NTABS=1; [ "$NTABS" -gt "$MAXTABS" ] && NTABS="$MAXTABS"
log "=== round start (MODE=$MODE) | editor=$EDITOR_PROC | cycling NTABS=$NTABS ==="

n=0; capped=0; acted=0
for i in $(seq 1 "$NTABS"); do
  key 64; sleep 0.3             # F17 focusNext
  buf="$(read_buf)"
  n=$((n+1))
  # BLOCKED test: last on-screen line is the popup action line AND a limit menu option
  # sits just above it; OR the inline notice is itself the last line (prompt held).
  nb="$(printf '%s' "$buf" | grep -v '^[[:space:]]*$')"
  last1="$(printf '%s' "$nb" | tail -n 1)"
  last8="$(printf '%s' "$nb" | tail -n 8)"
  blocked=0
  if printf '%s' "$last1" | grep -qiE 'Enter to confirm.*Esc to cancel' \
     && printf '%s' "$last8" | grep -qiE "$CAP_REGEX"; then blocked=1; fi
  if printf '%s' "$last1" | grep -qiE "$INLINE_RX"; then blocked=1; fi

  if [ "$blocked" -eq 1 ]; then
    capped=$((capped+1))
    if [ "$MODE" = "act" ]; then
      key 53; sleep 0.3          # Esc: dismiss the popup
      type_str "continue"; sleep 0.2
      key 36                      # Return
      acted=$((acted+1)); log "tab#$n BLOCKED -> sent continue"
    else
      log "tab#$n BLOCKED on limit popup (detect-only)"
    fi
  else
    log "tab#$n idle/working/wrapped -> skip"
  fi
done

log "=== done: visited=$n blocked=$capped acted=$acted ==="
echo "$capped"
[ "$capped" -gt 0 ]