#!/bin/bash
# reconcile-ticket.sh <TICKET-ID> [<TICKET-ID> ...]   (e.g. DEV-NNN, or bare numbers)
#
# For each ticket: if it has >=1 linked GitHub PR and EVERY linked PR is MERGED, and
# the ticket sits in a started state below Deployed, advance it to Deployed.
#   - Advance-only: never regresses a state (this is the exact rule that prevents
#     recreating the In Review->In Progress bounce).
#   - Never touches Backlog/Todo/Done/Canceled, nor an already-Deployed ticket.
#   - Skips if ANY linked PR is still open (genuinely mid-flight).
#   - Deployed != Done: Done stays a manual, prod-verified promotion. This NEVER sets Done.
#   - Idempotent; fail-open (silent exit 0 on any missing dep / API error / missing config).
#
# Why: Linear's per-PR automation thrashes on multi-PR (cross-repo) tickets — one PR
# merging while a sibling is still open bounces the issue back to In Progress and the
# later merge often never re-fires, leaving it stuck with all PRs merged.
#
# Callers (the ticket id is always known — work is ticket-driven):
#   - wrapup (Phase 1): reconcile the session's ticket(s).
#   - babysit-prs: parse ticket ids from recently-merged @me PRs and pass them in.
#
# Config via environment (the hook no-ops unless these are set):
#   LINEAR_API_KEY            your Linear personal API key, OR
#   LINEAR_KEY_FILE          path to a JSON file holding .env.LINEAR_API_KEY
#   LINEAR_DEV_TEAM_ID       UUID of the team whose issues these tickets map to
#   LINEAR_DEPLOYED_STATE_ID UUID of the "Deployed" workflow state (started-type)
# (Find the UUIDs with get_team / list_issue_statuses in the Linear MCP, or GraphQL.)

set +e
TEAM="${LINEAR_DEV_TEAM_ID:-}"
DEPLOYED="${LINEAR_DEPLOYED_STATE_ID:-}"
API="https://api.linear.app/graphql"

command -v jq   >/dev/null 2>&1 || exit 0
command -v curl >/dev/null 2>&1 || exit 0
command -v gh   >/dev/null 2>&1 || exit 0
[ -z "$TEAM" ] && exit 0
[ -z "$DEPLOYED" ] && exit 0

key="${LINEAR_API_KEY:-}"
if [ -z "$key" ] && [ -n "${LINEAR_KEY_FILE:-}" ]; then
  key=$(jq -r '.env.LINEAR_API_KEY // ""' "$LINEAR_KEY_FILE" 2>/dev/null)
fi
[ -z "$key" ] && exit 0

reconcile_one() {
  local num="$1"
  [ -z "$num" ] && return 0

  local q="query { issues(filter: { number: { eq: ${num} }, team: { id: { eq: \"${TEAM}\" } } }) { nodes { id identifier state { id type } attachments { nodes { url } } } } }"
  local resp node
  resp=$(curl -s --max-time 8 -X POST "$API" \
    -H "Authorization: $key" -H "Content-Type: application/json" \
    -d "$(jq -n --arg q "$q" '{query:$q}')")
  node=$(jq -c '.data.issues.nodes[0] // empty' <<<"$resp" 2>/dev/null)
  [ -z "$node" ] && return 0

  local iid ident stype sid
  iid=$(jq -r '.id' <<<"$node")
  ident=$(jq -r '.identifier' <<<"$node")
  stype=$(jq -r '.state.type' <<<"$node")
  sid=$(jq -r '.state.id' <<<"$node")

  # Only started-but-not-Deployed (skip backlog/unstarted/completed/canceled & already-Deployed).
  [ "$stype" != "started" ] && return 0
  [ "$sid" = "$DEPLOYED" ] && return 0

  # GitHub PR URLs from attachments. NOTE: we trust Linear's attachment set as the
  # COMPLETE list of the ticket's PRs — that completeness is guaranteed upstream by
  # branch-name-gate.sh (+ pr-gate.sh), which force every PR onto a ticket-token
  # branch so it auto-links. If a ticket were under-linked (a still-open PR never
  # attached), we could advance early; acceptable because Deployed != Done (a human
  # still gates Done).
  local prs
  prs=$(jq -r '.attachments.nodes[].url' <<<"$node" 2>/dev/null \
        | grep -oiE 'github\.com/[^/]+/[^/]+/pull/[0-9]+' | sort -u)
  [ -z "$prs" ] && return 0

  # Every PR must be MERGED; any non-merged -> bail (leave the ticket alone).
  local pr repo n state
  while IFS= read -r pr; do
    [ -z "$pr" ] && continue
    repo=$(sed -E 's#.*github\.com/([^/]+/[^/]+)/pull/[0-9]+.*#\1#i' <<<"$pr")
    n=$(grep -oE '[0-9]+$' <<<"$pr")
    state=$(gh pr view "$n" --repo "$repo" --json state -q '.state' 2>/dev/null)
    [ "$state" != "MERGED" ] && return 0
  done <<<"$prs"

  # All linked PRs merged -> advance to Deployed.
  local mq='mutation($id: String!, $sid: String!) { issueUpdate(id: $id, input: { stateId: $sid }) { success } }'
  local mresp
  mresp=$(curl -s --max-time 8 -X POST "$API" \
    -H "Authorization: $key" -H "Content-Type: application/json" \
    -d "$(jq -n --arg q "$mq" --arg id "$iid" --arg sid "$DEPLOYED" '{query:$q,variables:{id:$id,sid:$sid}}')")
  if [ "$(jq -r '.data.issueUpdate.success // false' <<<"$mresp" 2>/dev/null)" = "true" ]; then
    echo "✅ ${ident}: all linked PRs merged → Deployed"
  fi
}

# Accept TICKET-NNN / ticket-NNN / bare number args.
for arg in "$@"; do
  reconcile_one "$(grep -oE '[0-9]+' <<<"$arg" | head -1)"
done
exit 0
