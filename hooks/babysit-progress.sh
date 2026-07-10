#!/usr/bin/env bash
# babysit-progress.sh — cross-session judgment store for /babysit-prs.
#
# The classifier (babysit_classify.py) is deliberately STATELESS: it re-derives
# every PR's state from GitHub each sweep (GitHub is the source of truth). But a
# few DECISIONS are judgment that a fresh session otherwise loses — this file is
# where they persist, so a new session (or a rebooted machine) resumes with the
# same continuity instead of re-doing work:
#
#   - cli_reviewed[repo#pr] = {head, at}  — the branch head SHA a local CR-CLI
#       review last covered. The skill re-launches CLI ONLY when the current
#       head differs (author pushed new code) — this makes that rule DURABLE
#       instead of living only in one session's chat context.
#   - known_fp[repo#pr]     = {reason, since} — PRs the classifier keeps flagging
#       HAS_ACTIONABLE that are really a CR ack-reply false-positive (e.g. #540).
#       The skill checks this to avoid re-chewing the same non-finding each sweep.
#   - merges[]              = {pr, ticket, at} — rolling log of merges babysit did
#       (last 50), so post-restart reporting has the history.
#
# Store: ~/.claude/babysit-progress.json  (durable, survives sessions/reboot —
# unlike /tmp/babysit-prs-state.json which is stall bookkeeping only).
# All writes are atomic (tmp+mv). Concurrency-safe because the sweep already
# holds babysit-lock.sh; this is a convenience layer, not a second lock.
set -euo pipefail

STORE="${BABYSIT_PROGRESS:-$HOME/.claude/babysit-progress.json}"
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

ensure() { [ -f "$STORE" ] || echo '{"cli_reviewed":{},"known_fp":{},"merges":[]}' > "$STORE"; }
save() { # save <jq-program> [args...] ; reads $STORE, writes atomically
  ensure
  local prog="$1"; shift
  local tmp; tmp="$(mktemp)"
  if jq "$@" "$prog" "$STORE" > "$tmp" 2>/dev/null; then mv "$tmp" "$STORE"; else rm -f "$tmp"; return 1; fi
}

cmd="${1:-}"; shift || true
case "$cmd" in
  load)
    ensure; cat "$STORE" ;;

  summary)
    ensure
    jq -r '"progress: \(.cli_reviewed|length) cli-reviewed heads, \(.known_fp|length) known-FP, \(.merges|length) merges logged"' "$STORE" ;;

  cli-head)            # cli-head <repo#pr>  -> prints last-reviewed head sha ("" if none)
    ensure
    jq -r --arg k "${1:?repo#pr}" '.cli_reviewed[$k].head // ""' "$STORE" ;;

  set-cli-head)        # set-cli-head <repo#pr> <sha>
    save '.cli_reviewed[$k]={head:$h,at:$t}' \
      --arg k "${1:?repo#pr}" --arg h "${2:?sha}" --arg t "$(ts)"
    echo "cli-reviewed $1 -> $2" ;;

  is-fp)               # is-fp <repo#pr>  -> exit 0 + prints reason if known FP, else exit 1
    ensure
    r="$(jq -r --arg k "${1:?repo#pr}" '.known_fp[$k].reason // ""' "$STORE")"
    [ -n "$r" ] && { echo "$r"; exit 0; } || exit 1 ;;

  add-fp)              # add-fp <repo#pr> <reason>
    save '.known_fp[$k]={reason:$r,since:$t}' \
      --arg k "${1:?repo#pr}" --arg r "${2:?reason}" --arg t "$(ts)"
    echo "known-FP $1 recorded" ;;

  clear-fp)            # clear-fp <repo#pr>  (e.g. a real finding finally landed)
    save 'del(.known_fp[$k])' --arg k "${1:?repo#pr}"
    echo "known-FP $1 cleared" ;;

  log-merge)           # log-merge <repo#pr> <ticket>
    save '.merges += [{pr:$p,ticket:$k,at:$t}] | .merges |= (.[-50:])' \
      --arg p "${1:?repo#pr}" --arg k "${2:-}" --arg t "$(ts)"
    echo "logged merge $1 ($2)" ;;

  *)
    echo "usage: babysit-progress.sh {load|summary|cli-head <pr>|set-cli-head <pr> <sha>|is-fp <pr>|add-fp <pr> <reason>|clear-fp <pr>|log-merge <pr> <ticket>}" >&2
    exit 2 ;;
esac
