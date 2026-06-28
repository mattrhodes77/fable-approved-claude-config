#!/usr/bin/env bash
# check-no-edit-on-main.sh — PreToolUse hook on Edit|Write.
# Denies editing a file inside a PRIMARY clone (.git is a directory) when that
# checkout is sitting on its DEFAULT branch (main/master) — the "never work
# directly on main" rule. It's the edit-side complement to check-worktree.sh,
# which only blocks `git commit`: plain edits + untracked files can pile up on a
# base-on-main without ever tripping the commit guard.
#
# Why it matters beyond the worktree discipline: a base checkout left dirty on
# its default branch silently stops local `main` from advancing. A common setup
# keeps local main current with a periodic fast-forward-only pull — and ff-only
# (correctly) refuses to clobber a dirty tree, so local main quietly falls behind
# for as long as the dirt sits there. We hit exactly this: a base checkout parked
# dirty-on-main went many days and hundreds of commits stale before anyone
# noticed. Work in a worktree on a feature branch and the base stays clean.
#
# Allowed (does NOT fire):
#   - linked worktrees (.git is a gitfile) — the correct place to work
#   - the checkout is on a feature branch, not the default branch
#   - repos under /tmp or /private/tmp (ephemeral scratch clones)
#   - paths whose toplevel is listed in ~/.claude/hooks/worktree-exempt.txt
#     exempt:   echo /path/to/repo >> ~/.claude/hooks/worktree-exempt.txt
set -euo pipefail

INPUT=$(cat)
allow() { echo '{}'; exit 0; }

FILE=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // ""' 2>/dev/null || true)
[ -z "$FILE" ] && allow

# normalize → absolute
case "$FILE" in
  /*) ;;
  '~'*) FILE="$HOME${FILE#\~}" ;;
  *) FILE="$(pwd)/$FILE" ;;
esac

# resolve a real directory to query git from (file may not exist yet on Write)
DIR=$(dirname "$FILE")
while [ ! -d "$DIR" ] && [ "$DIR" != "/" ]; do DIR=$(dirname "$DIR"); done

TOP=$(git -C "$DIR" rev-parse --show-toplevel 2>/dev/null || true)
[ -z "$TOP" ] && allow          # not in a git repo
[ -f "$TOP/.git" ] && allow     # linked worktree → the right place to edit

case "$TOP" in /tmp/*|/private/tmp/*) allow ;; esac

# explicit per-repo exemptions (shared with check-worktree.sh)
EXEMPT_FILE="$HOME/.claude/hooks/worktree-exempt.txt"
if [ -f "$EXEMPT_FILE" ]; then
  while IFS= read -r LINE; do
    LINE=$(printf '%s' "$LINE" | tr -d '[:space:]'); [ -z "$LINE" ] && continue; LINE=${LINE%/}
    case "$TOP" in "$LINE"|"$LINE"/*) allow ;; esac
  done < "$EXEMPT_FILE"
fi

# default branch: origin/HEAD if known, else first of main/master that exists
DEF=$(git -C "$TOP" symbolic-ref --quiet refs/remotes/origin/HEAD 2>/dev/null | sed 's#^refs/remotes/origin/##' || true)
if [ -z "$DEF" ]; then
  for c in main master; do
    git -C "$TOP" show-ref --verify --quiet "refs/heads/$c" 2>/dev/null && { DEF=$c; break; }
  done
fi
[ -z "$DEF" ] && allow          # can't determine default branch → don't block

BR=$(git -C "$TOP" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')
[ "$BR" != "$DEF" ] && allow    # on a feature branch in the base → not the harm we guard

jq -n --arg top "$TOP" --arg br "$BR" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:("[no-edit-on-main] Blocked: \($top) is a PRIMARY clone on its default branch (\($br)). Never work directly on main — uncommitted edits here wedge a fast-forward-only main-autopull (it refuses to clobber, so local main silently stops advancing). Create a worktree on a feature branch and edit there: git -C \($top) worktree add \($top).<slug> -b <branch>. To intentionally allow this repo: echo \($top) >> ~/.claude/hooks/worktree-exempt.txt")}}' \
  || printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"[no-edit-on-main] Blocked: editing a primary clone on its default branch. Work in a worktree."}}\n'
