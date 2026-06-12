#!/usr/bin/env bash
# check-worktree.sh — PreToolUse hook on Bash.
# Denies `git commit` in a PRIMARY clone (.git is a directory) and allows it
# in linked worktrees (.git is a gitfile). Enforces the worktree-per-ticket
# rule: humans and agents share primary clones, so agent commits there get
# clobbered by parallel rebase/amend/rename. Mutation needs isolation.
#
# Exemptions (allow committing in a primary clone):
#   - repos under /tmp or /private/tmp (ephemeral scratch clones)
#   - path prefixes listed one-per-line in ~/.claude/hooks/worktree-exempt.txt
#     exempt:   echo /path/to/repo >> ~/.claude/hooks/worktree-exempt.txt
#     unexempt: edit/remove that line
set -euo pipefail

INPUT=$(cat)

allow() { echo '{}'; exit 0; }

CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // ""' 2>/dev/null || true)
[ -z "$CMD" ] && allow

# Fast path: nothing resembling a git commit.
case "$CMD" in
  *git*commit*) ;;
  *) allow ;;
esac

# Precise match: `git`, optionally followed by option tokens (-C <p>, -c <kv>,
# --flag), then the `commit` subcommand. Does NOT match `git log --grep commit`.
GIT_RE='(^|[;&|[:space:]])git([[:space:]]+(-C[[:space:]]+[^[:space:];&|]+|-c[[:space:]]+[^[:space:]]+|--?[^[:space:]]+))*[[:space:]]+commit([[:space:]]|$)'
printf '%s' "$CMD" | grep -qE "$GIT_RE" || allow

# Resolve the directory the commit targets: -C path > last cd > hook cwd > pwd.
strip_quotes() { sed -E "s/^[\"']//; s/[\"']\$//"; }

DIR=$(printf '%s' "$CMD" | grep -oE '(^|[;&|[:space:]])git[[:space:]]+-C[[:space:]]+[^[:space:];&|]+' | head -1 | sed -E 's/.*-C[[:space:]]+//' | strip_quotes || true)
if [ -z "$DIR" ]; then
  DIR=$(printf '%s' "$CMD" | grep -oE '(^|[;&|][[:space:]]*)cd[[:space:]]+[^[:space:];&|]+' | tail -1 | sed -E 's/.*cd[[:space:]]+//' | strip_quotes || true)
fi
if [ -z "$DIR" ]; then
  DIR=$(printf '%s' "$INPUT" | jq -r '.cwd // ""' 2>/dev/null || true)
fi
[ -z "$DIR" ] && DIR=$(pwd)
case "$DIR" in
  /*) ;;
  '~'*) DIR="$HOME${DIR#\~}" ;;
  *) DIR="$(pwd)/$DIR" ;;
esac

# Not a git repo (commit would fail on its own) -> allow.
TOP=$(git -C "$DIR" rev-parse --show-toplevel 2>/dev/null || true)
[ -z "$TOP" ] && allow

# Linked worktree: .git at toplevel is a gitfile, not a directory.
[ -f "$TOP/.git" ] && allow

# Ephemeral scratch clones are exempt.
case "$TOP" in
  /tmp/*|/private/tmp/*) allow ;;
esac

# Explicit exemptions.
EXEMPT_FILE="$HOME/.claude/hooks/worktree-exempt.txt"
if [ -f "$EXEMPT_FILE" ]; then
  while IFS= read -r LINE; do
    LINE=$(printf '%s' "$LINE" | tr -d '[:space:]')
    [ -z "$LINE" ] && continue
    LINE=${LINE%/}
    case "$TOP" in
      "$LINE"|"$LINE"/*) allow ;;
    esac
  done < "$EXEMPT_FILE"
fi

jq -n --arg top "$TOP" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:("[worktree] Blocked: \($top) is a PRIMARY clone — commits here can be clobbered by parallel human work. Create a worktree and commit there: git -C \($top) worktree add <repo>.<slug> -b <branch>. To intentionally allow this repo: echo \($top) >> ~/.claude/hooks/worktree-exempt.txt")}}' \
  || printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"[worktree] Blocked: committing in a primary clone. Create a worktree and commit there."}}\n'
