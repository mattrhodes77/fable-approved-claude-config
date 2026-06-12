#!/usr/bin/env bash
# pr-gate.sh — PreToolUse hook on Bash for Claude Code.
# Blocks `gh pr create` unless the PRlaunch gates passed for the EXACT current HEAD.
#
# Mechanism: PRlaunch phase 5 writes ~/.claude/prlaunch-ok/<repo>--<branch>
# containing the HEAD sha after all gates pass on the final tree. Any commit
# made after that invalidates the marker (sha mismatch) — which is the
# re-gate rule, enforced mechanically.
#
# Bypass (owner-authorized emergencies only): include PRLAUNCH_SKIP=1 in the command.
#
# Install in ~/.claude/settings.json:
#   "hooks": { "PreToolUse": [ { "matcher": "Bash", "hooks": [
#     { "type": "command", "command": "~/.claude/hooks/pr-gate.sh", "timeout": 10 }
#   ] } ] }
set -euo pipefail

INPUT=$(cat)
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // ""' 2>/dev/null || true)

# Only gate gh pr create
case "$CMD" in
  *"gh pr create"*) ;;
  *) echo '{}'; exit 0 ;;
esac

# Owner-authorized bypass
case "$CMD" in
  *"PRLAUNCH_SKIP=1"*) echo '{}'; exit 0 ;;
esac

# Resolve repo + branch. If the command cd's somewhere, honor the last cd target.
DIR=$(printf '%s' "$CMD" | grep -oE 'cd [^&;|]+' | tail -1 | sed 's/^cd //;s/[[:space:]]*$//' || true)
DIR=${DIR:-$(pwd)}
DIR=$(eval echo "$DIR" 2>/dev/null || printf '%s' "$DIR")

REPO_ROOT=$(git -C "$DIR" rev-parse --show-toplevel 2>/dev/null || true)
if [ -z "$REPO_ROOT" ]; then
  echo '{}'  # not a git repo — let normal permissions handle it
  exit 0
fi

BRANCH=$(git -C "$REPO_ROOT" branch --show-current | tr '/' '-')
HEAD=$(git -C "$REPO_ROOT" rev-parse HEAD)
MARKER="$HOME/.claude/prlaunch-ok/$(basename "$REPO_ROOT")--$BRANCH"

if [ -f "$MARKER" ] && [ "$(tr -d '[:space:]' < "$MARKER")" = "$HEAD" ]; then
  echo '{}'
  exit 0
fi

printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"[pr-gate] Blocked: no PRlaunch marker for %s @ HEAD %.8s. Run the PRlaunch gates (deep-review, secondary review, outcome eval, re-gate), then write the marker in phase 5. Emergency bypass: PRLAUNCH_SKIP=1 (owner-authorized only)."}}\n' "$(basename "$REPO_ROOT")--$BRANCH" "$HEAD"
