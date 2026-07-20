#!/bin/bash
# pr-gate.sh — PreToolUse hook on Bash for Claude Code.
# Blocks `gh pr create` unless the PRlaunch gates passed for this exact
# repo+branch+HEAD. The evidence is a per-gate JSON ledger:
#   ~/.claude/prlaunch-ok/<repo>--<branch-slug>.json  (written by prlaunch-gate.sh)
# recording, per gate (deep_review/cr_cli/outcome_eval/tests), the HEAD it ran
# against; `prlaunch-gate.sh check` requires all four present AND at current HEAD.
# Any commit after a gate ran changes HEAD and re-blocks (re-gate rule).
# Back-compat: a legacy plain-sha marker file (no .json suffix) that matches HEAD
# is accepted with a migration warning while you transition to the ledger.
# Escape hatch: include PRLAUNCH_SKIP=1 in the command (owner-authorized only).
#
# Optional tracker-link gate: the PR must attach to a ticket (branch carries
# <prefix>-NNN, or the command body carries it, e.g. "Closes <PREFIX>-NNN").
# The ticket-token prefix defaults to `dev` (LINEAR_BRANCH_PREFIX to change it).
# LINEAR_SKIP=1 bypasses it for genuinely ticket-less PRs (infra/config repos).
#
# Install in ~/.claude/settings.json:
#   "hooks": { "PreToolUse": [ { "matcher": "Bash", "hooks": [
#     { "type": "command", "command": "~/.claude/hooks/pr-gate.sh", "timeout": 10 }
#   ] } ] }

PREFIX="${LINEAR_BRANCH_PREFIX:-dev}"
UPREFIX=$(printf '%s' "$PREFIX" | tr '[:lower:]' '[:upper:]')

input=$(cat)
cmd=$(jq -r '.tool_input.command // ""' <<<"$input")

# Match only real invocations: strip quoted strings first so prose mentions
# (commit messages, echo, grep patterns) don't trigger the gate.
cleaned=$(sed -E "s/'[^']*'//g; s/\"[^\"]*\"//g" <<<"$cmd")
case "$cleaned" in
  *"gh pr create"*) ;;
  *) exit 0 ;;
esac

[[ "$cmd" == *"PRLAUNCH_SKIP=1"* ]] && exit 0

deny() {
  jq -n --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
  exit 0
}

# Repo dir: explicit `cd <dir>` / `git -C <dir>` in the command wins, else hook cwd
dir=$(jq -r '.cwd // ""' <<<"$input")
# Resolve it from the LAST `cd` BEFORE the trigger — that is the directory the
# PR is actually created from. `cd /tmp && cd repo && …` runs in `repo`, and a
# trailing `&& cd /elsewhere` runs only after the PR already exists. `git -C`
# is only a fallback: it runs one command elsewhere without moving the shell,
# so it must never override an actual cd.
#
# This is a text heuristic, not a shell parser: it cannot see control flow, so
# `cd /a || cd /b && …` and a subshell-scoped `(cd /b && true) && …` can still
# pick the wrong directory. That is tolerable because it fails toward DENYING
# (the wrong repo/branch almost never has a ledger at this HEAD), and the deny
# message names the repo it resolved, so the fix is obvious. Replacing the
# heuristic with an explicit repo-dir signal is tracked in DEV-4989.
before_trigger="${cmd%%gh pr create*}"
explicit=$(grep -oE '(^|[ ;&|(])cd [^ ;&|]+' <<<"$before_trigger" | tail -1 | awk '{print $NF}')
[[ -n "$explicit" ]] \
  || explicit=$(grep -oE 'git -C [^ ;&|]+' <<<"$before_trigger" | tail -1 | awk '{print $NF}')
[[ -n "$explicit" && -d "${explicit/#\~/$HOME}" ]] && dir="${explicit/#\~/$HOME}"

toplevel=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null) \
  || deny "PR gate: cannot resolve a git repo from '$dir'. Run gh pr create from the repo, or run /PRlaunch."
repo=$(basename "$toplevel")
branch=$(git -C "$dir" branch --show-current)
head=$(git -C "$dir" rev-parse HEAD)
slug="${branch//\//-}"
marker="$HOME/.claude/prlaunch-ok/${repo}--${slug}"        # legacy plain-sha marker
ledger="$HOME/.claude/prlaunch-ok/${repo}--${slug}.json"   # per-gate evidence ledger
gate_helper="$(dirname "$0")/prlaunch-gate.sh"

# Tracker link gate: the PR must attach to a ticket (branch carries <prefix>-NNN,
# or the body/title carries the ticket id). An unlinked PR joins the under-linked
# graveyard. LINEAR_SKIP=1 for genuinely ticket-less PRs (config/infra repos).
if [[ "$cmd" != *"LINEAR_SKIP=1"* ]]; then
  if ! grep -qiE "${PREFIX}-[0-9]+" <<<"$branch" && ! grep -qiE "${PREFIX}-[0-9]+" <<<"$cmd"; then
    deny "PR BLOCKED: this PR won't link to a tracker ticket. Name the branch me/${PREFIX}-NNN-... or put 'Closes ${UPREFIX}-NNN' in the body, so the tracker auto-attaches it. (LINEAR_SKIP=1 for genuinely ticket-less PRs.)"
  fi
fi

# Prefer the per-gate JSON ledger. When it exists it is authoritative: all four
# PRlaunch gates must be recorded at the current HEAD. Delegate to prlaunch-gate.sh
# `check` (it lives beside this hook) — it names the exact missing/stale gate and
# the prescriptive fix, which we surface verbatim in the deny reason.
if [[ -f "$ledger" ]]; then
  if reason=$(bash "$gate_helper" check --repo-dir "$dir" 2>&1); then
    exit 0
  fi
  deny "PR BLOCKED (PRlaunch ledger): $reason"
fi

# BACK-COMPAT: no JSON ledger, but a legacy plain-sha marker.
# Accept only if it matches HEAD, with a migration warning; a stale marker denies.
if [[ -f "$marker" ]]; then
  if [[ "$(cat "$marker")" == "$head" ]]; then
    jq -n '{systemMessage:"legacy PRlaunch marker accepted — migrate to the per-gate ledger (prlaunch-gate.sh)"}'
    exit 0
  fi
  deny "PR BLOCKED: $repo/$branch changed since PRlaunch gates passed (gated $(cut -c1-8 "$marker"), HEAD ${head:0:8}). Re-run the affected gates (/PRlaunch Phase 4) — green earlier ≠ the final version is green."
fi

deny "PR BLOCKED: no PRlaunch gate record for $repo/$branch. Run /PRlaunch (deep-review → CR CLI → outcome eval → re-gate) before opening a PR."
