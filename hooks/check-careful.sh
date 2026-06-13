#!/usr/bin/env bash
# check-careful.sh — PreToolUse hook on Bash.
#
# Goal: stop nagging on routine cleanup, and when it DOES ask, ask in plain
# English a human can actually answer.
#
# For `rm -r`: every delete target is classified. If all targets are
# routine/regenerable (virtualenvs, build caches, test DBs, temp/log files)
# the command is ALLOWED SILENTLY. If any target isn't recognized, the prompt
# lists each item with a ✓ (routine) or ⚠ (please check) and a plain label —
# not a regex verdict. Other rare-but-catastrophic commands (true force-push,
# SQL DROP/TRUNCATE, kubectl delete, docker prune) get a plain-English warning.
#
# Loop-mode: when ~/.claude/hooks/loop-mode exists and is unexpired, flagged
# commands AUTO-PROCEED + get logged to ~/.claude/cleanup-needed.log instead of
# prompting (so an unattended loop never wedges). See loop-mode-arm.sh.
#   enable:  ~/.claude/hooks/loop-mode-arm.sh [minutes]   (or: touch the file)
#   disable: rm ~/.claude/hooks/loop-mode
# Adapted from garrytan/gstack careful/bin/check-careful.sh.
set -euo pipefail

INPUT=$(cat)

# Extract the "command" field from tool_input (jq, python3, then naive grep).
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // ""' 2>/dev/null || true)
if [ -z "$CMD" ]; then
  CMD=$(printf '%s' "$INPUT" | python3 -c 'import sys,json; print(json.loads(sys.stdin.read()).get("tool_input",{}).get("command",""))' 2>/dev/null || true)
fi
if [ -z "$CMD" ]; then
  CMD=$(printf '%s' "$INPUT" | grep -o '"command"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*:[[:space:]]*"//;s/"$//' || true)
fi
if [ -z "$CMD" ]; then
  echo '{}'
  exit 0
fi

CMD_LOWER=$(printf '%s' "$CMD" | tr '[:upper:]' '[:lower:]')
WARN=""

# --- rm -r handling: delegate to the parser (quote/comment/newline aware) -
# careful-rm.py prints a plain itemized WARN when an rm -r targets anything not
# routine/regenerable, and nothing when every target is safe (or no gated rm).
# A bash word-loop can't be trusted here — it mis-reads comments, newlines, and
# `rm` mentioned inside quoted arguments.
RM_HELPER="$HOME/.claude/hooks/careful-rm.py"
if printf '%s' "$CMD" | grep -qE '(^|[;&|[:space:]])rm([[:space:]]|$)' 2>/dev/null; then
  if [ -f "$RM_HELPER" ]; then
    if ! WARN=$(printf '%s' "$CMD" | python3 "$RM_HELPER" 2>/dev/null); then
      # parser errored -> conservative gate only if a recursive rm is present
      WARN=""
      printf '%s' "$CMD" | grep -qE 'rm\s+(-[a-zA-Z]*r|--recursive)' 2>/dev/null \
        && WARN="This command includes a recursive delete (rm -r) I couldn't fully analyze — review the paths before approving."
    fi
  elif printf '%s' "$CMD" | grep -qE 'rm\s+(-[a-zA-Z]*r|--recursive)' 2>/dev/null; then
    WARN="This command includes a recursive delete (rm -r). Review the paths before approving."
  fi
fi

# --- Other rare-but-catastrophic commands (plain-English) ----------------
# True force-push only: --force-with-lease is the sanctioned safe variant.
if [ -z "$WARN" ] && printf '%s' "$CMD" | grep -qE 'git\s+push\s+[^;|&]*(--force([[:space:]]|$)|-f([[:space:]]|$))' 2>/dev/null \
  && ! printf '%s' "$CMD" | grep -q -- '--force-with-lease' 2>/dev/null; then
  WARN="This force-pushes and OVERWRITES the remote branch's history — anyone else's commits on that branch can be lost. (The safe version is --force-with-lease.)"
fi
# SQL DROP/TRUNCATE only when a SQL client is being invoked.
if [ -z "$WARN" ] && printf '%s' "$CMD_LOWER" | grep -qE '(psql|mysql|sqlite3)\b' 2>/dev/null \
  && printf '%s' "$CMD_LOWER" | grep -qE 'drop\s+(table|database)|truncate\s' 2>/dev/null; then
  WARN="This runs a SQL DROP/TRUNCATE — it permanently deletes a table (or all of its rows) from the database."
fi
if [ -z "$WARN" ] && printf '%s' "$CMD" | grep -qE 'kubectl\s+delete' 2>/dev/null; then
  WARN="This deletes Kubernetes resources — it could take down something that's running (possibly in production)."
fi
if [ -z "$WARN" ] && printf '%s' "$CMD" | grep -qE 'docker\s+(rm\s+-f|system\s+prune)' 2>/dev/null; then
  WARN="This force-removes Docker containers or prunes images/volumes — running containers or cached data can be lost."
fi

# --- Output --------------------------------------------------------------
LOOPMODE_FILE="$HOME/.claude/hooks/loop-mode"
CLEANUP_LOG="$HOME/.claude/cleanup-needed.log"

# loop-mode active = file exists AND unexpired. Content: empty/non-numeric =>
# armed indefinitely (manual touch); numeric epoch => armed until it passes
# (expired => self-disarm so a leftover never poisons an interactive session).
loop_mode_active() {
  [ -f "$LOOPMODE_FILE" ] || return 1
  local content now
  content=$(tr -d '[:space:]' < "$LOOPMODE_FILE" 2>/dev/null || true)
  case "$content" in
    ''|*[!0-9]*) return 0 ;;
  esac
  now=$(date +%s 2>/dev/null || echo 0)
  [ "$now" -lt "$content" ] && return 0
  rm -f "$LOOPMODE_FILE" 2>/dev/null || true
  return 1
}

if [ -z "$WARN" ]; then
  echo '{}'
  exit 0
fi

if loop_mode_active; then
  TS=$(date '+%Y-%m-%d %H:%M:%S %Z' 2>/dev/null || true)
  printf '%s\t%s\n' "$TS" "$CMD" >> "$CLEANUP_LOG" 2>/dev/null || true
  jq -n --arg cmd "$CMD" '{
    systemMessage: "[careful/loop-mode] Auto-proceeded a flagged command (logged to ~/.claude/cleanup-needed.log).",
    hookSpecificOutput: {hookEventName:"PreToolUse", permissionDecision:"allow", permissionDecisionReason:"[careful] loop-mode active — auto-proceeded"},
    additionalContext: ("loop-mode auto-approved a flagged command: " + $cmd + ". Track any cleanup and surface it in your wrap-up; the running log is ~/.claude/cleanup-needed.log.")
  }' 2>/dev/null && exit 0
  echo '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow"}}'
  exit 0
fi

jq -n --arg reason "[careful] $WARN" '{hookSpecificOutput:{hookEventName:"PreToolUse", permissionDecision:"ask", permissionDecisionReason:$reason}}' 2>/dev/null \
  || printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"[careful] destructive command — review before approving"}}\n'
