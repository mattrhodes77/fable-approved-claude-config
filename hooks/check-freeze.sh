#!/usr/bin/env bash
# check-freeze.sh — PreToolUse hook on Edit|Write.
# Hard-blocks edits outside the boundary directory named in freeze-dir.txt.
# Dormant when ~/.claude/hooks/freeze-dir.txt is absent or empty.
#   freeze:   echo /path/to/repo > ~/.claude/hooks/freeze-dir.txt
#   unfreeze: rm ~/.claude/hooks/freeze-dir.txt
# Adapted from garrytan/gstack freeze/bin/check-freeze.sh (state moved to
# ~/.claude/hooks, analytics removed, output modernized to hookSpecificOutput).
set -euo pipefail

INPUT=$(cat)

FREEZE_FILE="$HOME/.claude/hooks/freeze-dir.txt"

# If no freeze file exists, allow everything (not configured)
if [ ! -f "$FREEZE_FILE" ]; then
  echo '{}'
  exit 0
fi

FREEZE_DIR=$(tr -d '[:space:]' < "$FREEZE_FILE")

if [ -z "$FREEZE_DIR" ]; then
  echo '{}'
  exit 0
fi

# Extract file_path from tool_input JSON.
# jq first (handles escaped quotes correctly), python3 fallback, then naive grep.
FILE_PATH=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // ""' 2>/dev/null || true)

if [ -z "$FILE_PATH" ]; then
  FILE_PATH=$(printf '%s' "$INPUT" | python3 -c 'import sys,json; print(json.loads(sys.stdin.read()).get("tool_input",{}).get("file_path",""))' 2>/dev/null || true)
fi

if [ -z "$FILE_PATH" ]; then
  FILE_PATH=$(printf '%s' "$INPUT" | grep -o '"file_path"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*:[[:space:]]*"//;s/"$//' || true)
fi

# If we couldn't extract a file path, allow (don't block on parse failure)
if [ -z "$FILE_PATH" ]; then
  echo '{}'
  exit 0
fi

# Resolve file_path to absolute if it isn't already
case "$FILE_PATH" in
  /*) ;;
  *)
    FILE_PATH="$(pwd)/$FILE_PATH"
    ;;
esac

# Normalize: remove double slashes and trailing slash
FILE_PATH=$(printf '%s' "$FILE_PATH" | sed 's|/\+|/|g;s|/$||')

# Resolve symlinks and .. sequences (POSIX-portable, works on macOS)
_resolve_path() {
  local _dir _base
  _dir="$(dirname "$1")"
  _base="$(basename "$1")"
  _dir="$(cd "$_dir" 2>/dev/null && pwd -P || printf '%s' "$_dir")"
  printf '%s/%s' "$_dir" "$_base"
}
FILE_PATH=$(_resolve_path "$FILE_PATH")
FREEZE_DIR=$(_resolve_path "$FREEZE_DIR")

case "$FILE_PATH" in
  "${FREEZE_DIR}/"*|"${FREEZE_DIR}")
    echo '{}'
    ;;
  *)
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"[freeze] Blocked: %s is outside the freeze boundary (%s). Only edits within the frozen directory are allowed."}}\n' "$FILE_PATH" "$FREEZE_DIR"
    ;;
esac
