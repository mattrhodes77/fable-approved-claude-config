#!/usr/bin/env bash
# ledger-append.sh — append one validated JSON record to the automation ledger.
#
# The automation fleet (bulldozer, babysit-prs, PRlaunch,
# wrapup) calls this once per result/event so there is a DURABLE quality record
# that /scorecard aggregates (run state otherwise lives only in /tmp and is lost).
#
# Interface — matched EXACTLY by every call site (bulldozer Step 2.4 is canonical):
#     ~/.claude/hooks/ledger-append.sh '<one JSON object string>'
# Exactly ONE argument, a JSON object. A `ts` (ISO8601 UTC) is added when absent;
# an existing `ts` is preserved. The record is appended as one compact line to
# $HOME/.claude/automation-ledger.jsonl.
#
# This is a VALIDATOR, not a fail-open safety hook. A bad payload (missing arg,
# non-JSON, non-object) fails LOUDLY: it exits 1 with a one-line stderr message
# and writes NOTHING. It never partial-writes. Do NOT change this to exit 0 on
# bad input — a silently-swallowed record defeats the measurement loop.
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "ledger-append: expected exactly 1 arg (a JSON object), got $#" >&2
  exit 1
fi

payload="$1"

# Must parse AND be a JSON object. `jq -e` exits nonzero on a false/null result
# or a parse error, so this one gate covers non-JSON, arrays, scalars, and null.
if ! printf '%s' "$payload" | jq -e 'type == "object"' >/dev/null 2>&1; then
  echo "ledger-append: argument is not a JSON object" >&2
  exit 1
fi

# Add ts only if absent; emit one compact line. Computed BEFORE the append, so a
# jq failure here aborts (set -e) without touching the ledger.
ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
line="$(printf '%s' "$payload" | jq -c --arg ts "$ts" 'if has("ts") then . else . + {ts: $ts} end')"

ledger="$HOME/.claude/automation-ledger.jsonl"
mkdir -p "$(dirname "$ledger")"
printf '%s\n' "$line" >>"$ledger"
