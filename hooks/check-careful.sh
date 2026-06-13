#!/usr/bin/env bash
# check-careful.sh — PreToolUse hook on Bash.
# Warns (permissionDecision: ask) on destructive commands; allows everything else.
#
# Loop-mode: when ~/.claude/hooks/loop-mode exists, flagged destructive commands
# are AUTO-PROCEEDED instead of prompting — the command runs, a note is appended
# to ~/.claude/cleanup-needed.log, and a systemMessage surfaces it. Drop the file
# before an unattended /loop run so the gate never wedges the loop; remove it to
# restore interactive prompting.
#   enable:  touch ~/.claude/hooks/loop-mode
#   disable: rm ~/.claude/hooks/loop-mode
# Adapted from garrytan/gstack careful/bin/check-careful.sh (analytics removed,
# output modernized to hookSpecificOutput format).
set -euo pipefail

INPUT=$(cat)

# Extract the "command" field value from tool_input.
# jq first (handles escaped quotes correctly), python3 fallback, then naive grep.
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // ""' 2>/dev/null || true)

if [ -z "$CMD" ]; then
  CMD=$(printf '%s' "$INPUT" | python3 -c 'import sys,json; print(json.loads(sys.stdin.read()).get("tool_input",{}).get("command",""))' 2>/dev/null || true)
fi

if [ -z "$CMD" ]; then
  CMD=$(printf '%s' "$INPUT" | grep -o '"command"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*:[[:space:]]*"//;s/"$//' || true)
fi

# If we still couldn't extract a command, allow
if [ -z "$CMD" ]; then
  echo '{}'
  exit 0
fi

CMD_LOWER=$(printf '%s' "$CMD" | tr '[:upper:]' '[:lower:]')

# --- Safe exceptions: rm -rf of temp paths or build artifacts only ---
# Walk tokens tracking whether we're inside an rm invocation; separators
# (;, &&, ||, |) end it, so trailing `cd ...`/`git ...` segments in compound
# commands don't count as rm targets.
if printf '%s' "$CMD" | grep -qE '(^|[;&|[:space:]])rm\s+(-[a-zA-Z]*r[a-zA-Z]*(\s|$)|--recursive(\s|$))' 2>/dev/null; then
  SAFE_ONLY=true
  IN_RM=false
  set -f
  for tok in $CMD; do
    case "$tok" in
      rm)
        IN_RM=true
        continue
        ;;
      *';'*|*'&&'*|*'||'*|*'|'*|*'&'*)
        IN_RM=false
        continue
        ;;
    esac
    if [ "$IN_RM" = true ]; then
      case "$tok" in
        -*) ;;       # flag
        *'>'*|*'<'*) ;; # redirection
        /tmp/?*|/private/tmp/?*|/var/folders/?*)
          ;; # temp path
        */node_modules|node_modules|*/\.next|\.next|*/dist|dist|*/__pycache__|__pycache__|*/\.cache|\.cache|*/build|build|*/\.turbo|\.turbo|*/coverage|coverage)
          ;; # build artifact
        .venv*|*/.venv*|*.venv|*/*.venv)
          ;; # python virtualenv (incl. suffixed e.g. .venv-myfeature)
        test_*.db|test_*.db-shm|test_*.db-wal|*/test_*.db|*/test_*.db-shm|*/test_*.db-wal)
          ;; # local test sqlite db + WAL sidecars
        *)
          SAFE_ONLY=false
          break
          ;;
      esac
    fi
  done
  set +f
  if [ "$SAFE_ONLY" = true ]; then
    echo '{}'
    exit 0
  fi
fi

# --- Destructive pattern checks ---
# Deliberately narrow: only patterns that are (a) rare in normal flow and
# (b) catastrophic when wrong. Routine-but-sharp commands (git reset --hard,
# git checkout ., --force-with-lease pushes) are NOT gated — they prompted
# constantly and trained the user to click through, which is worse than
# no gate at all.
WARN=""

if printf '%s' "$CMD" | grep -qE 'rm\s+(-[a-zA-Z]*r|--recursive)' 2>/dev/null; then
  WARN="Destructive: recursive delete (rm -r) of a non-temp, non-build path. This permanently removes files."
fi

# True force-push only: --force-with-lease is the sanctioned safe variant and must pass.
if [ -z "$WARN" ] && printf '%s' "$CMD" | grep -qE 'git\s+push\s+[^;|&]*(--force([[:space:]]|$)|-f([[:space:]]|$))' 2>/dev/null \
  && ! printf '%s' "$CMD" | grep -q -- '--force-with-lease' 2>/dev/null; then
  WARN="Destructive: git push --force (without --with-lease) rewrites remote history. Use --force-with-lease instead."
fi

# SQL DROP/TRUNCATE only in the context of a SQL client invocation — word-matching
# the whole command string fires on prose in commit-message heredocs.
if [ -z "$WARN" ] && printf '%s' "$CMD_LOWER" | grep -qE '(psql|mysql|sqlite3)\b' 2>/dev/null \
  && printf '%s' "$CMD_LOWER" | grep -qE 'drop\s+(table|database)|truncate\s' 2>/dev/null; then
  WARN="Destructive: SQL DROP/TRUNCATE via a database client. This permanently deletes data."
fi

if [ -z "$WARN" ] && printf '%s' "$CMD" | grep -qE 'kubectl\s+delete' 2>/dev/null; then
  WARN="Destructive: kubectl delete removes Kubernetes resources. May impact production."
fi

if [ -z "$WARN" ] && printf '%s' "$CMD" | grep -qE 'docker\s+(rm\s+-f|system\s+prune)' 2>/dev/null; then
  WARN="Destructive: Docker force-remove or prune. May delete running containers or cached images."
fi

# --- Output ---
LOOPMODE_FILE="$HOME/.claude/hooks/loop-mode"
CLEANUP_LOG="$HOME/.claude/cleanup-needed.log"

# loop-mode is active when the file exists AND is unexpired. Content rules:
#   absent            -> inactive (normal interactive prompting)
#   empty/non-numeric -> active, never expires (manual `touch`, user-managed)
#   numeric epoch      -> active only while now < epoch; expired -> self-disarm
loop_mode_active() {
  [ -f "$LOOPMODE_FILE" ] || return 1
  local content now
  content=$(tr -d '[:space:]' < "$LOOPMODE_FILE" 2>/dev/null || true)
  case "$content" in
    ''|*[!0-9]*) return 0 ;;  # manual indefinite arm
  esac
  now=$(date +%s 2>/dev/null || echo 0)
  [ "$now" -lt "$content" ] && return 0
  rm -f "$LOOPMODE_FILE" 2>/dev/null || true  # expired -> disarm
  return 1
}

if [ -n "$WARN" ]; then
  if loop_mode_active; then
    # Unattended loop: auto-proceed, but leave a paper trail.
    TS=$(date '+%Y-%m-%d %H:%M:%S %Z' 2>/dev/null || true)
    printf '%s\t%s\t%s\n' "$TS" "$WARN" "$CMD" >> "$CLEANUP_LOG" 2>/dev/null || true
    if jq -n --arg w "$WARN" --arg cmd "$CMD" '{
      systemMessage: ("[careful/loop-mode] Auto-proceeded a flagged command (logged to ~/.claude/cleanup-needed.log): " + $w),
      hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "allow", permissionDecisionReason: ("[careful] loop-mode active — auto-proceeded: " + $w)},
      additionalContext: ("loop-mode auto-approved a destructive command. Command: " + $cmd + "  |  Flagged: " + $w + ". Track any resulting cleanup and surface it in your wrap-up; the running log is ~/.claude/cleanup-needed.log.")
    }' 2>/dev/null; then
      exit 0
    fi
    # jq fallback: bare allow so the command isn't wedged.
    echo '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow"}}'
    exit 0
  fi
  WARN_ESCAPED=$(printf '%s' "$WARN" | sed 's/"/\\"/g')
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"[careful] %s"}}\n' "$WARN_ESCAPED"
else
  echo '{}'
fi
