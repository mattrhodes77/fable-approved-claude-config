#!/usr/bin/env bash
# check-careful.sh — PreToolUse hook on Bash.
# Warns (permissionDecision: ask) on destructive commands; allows everything else.
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
WARN=""

if printf '%s' "$CMD" | grep -qE 'rm\s+(-[a-zA-Z]*r|--recursive)' 2>/dev/null; then
  WARN="Destructive: recursive delete (rm -r). This permanently removes files."
fi

if [ -z "$WARN" ] && printf '%s' "$CMD_LOWER" | grep -qE 'drop\s+(table|database)' 2>/dev/null; then
  WARN="Destructive: SQL DROP detected. This permanently deletes database objects."
fi

if [ -z "$WARN" ] && printf '%s' "$CMD_LOWER" | grep -qE '\btruncate\b' 2>/dev/null; then
  WARN="Destructive: TRUNCATE detected. This deletes all rows / file contents."
fi

if [ -z "$WARN" ] && printf '%s' "$CMD" | grep -qE 'git\s+push\s+.*(-f\b|--force)' 2>/dev/null; then
  WARN="Destructive: git force-push rewrites remote history. Other contributors may lose work."
fi

if [ -z "$WARN" ] && printf '%s' "$CMD" | grep -qE 'git\s+reset\s+--hard' 2>/dev/null; then
  WARN="Destructive: git reset --hard discards all uncommitted changes."
fi

if [ -z "$WARN" ] && printf '%s' "$CMD" | grep -qE 'git\s+(checkout|restore)\s+\.' 2>/dev/null; then
  WARN="Destructive: discards all uncommitted changes in the working tree."
fi

if [ -z "$WARN" ] && printf '%s' "$CMD" | grep -qE 'kubectl\s+delete' 2>/dev/null; then
  WARN="Destructive: kubectl delete removes Kubernetes resources. May impact production."
fi

if [ -z "$WARN" ] && printf '%s' "$CMD" | grep -qE 'docker\s+(rm\s+-f|system\s+prune)' 2>/dev/null; then
  WARN="Destructive: Docker force-remove or prune. May delete running containers or cached images."
fi

# --- Output ---
if [ -n "$WARN" ]; then
  WARN_ESCAPED=$(printf '%s' "$WARN" | sed 's/"/\\"/g')
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"[careful] %s"}}\n' "$WARN_ESCAPED"
else
  echo '{}'
fi
