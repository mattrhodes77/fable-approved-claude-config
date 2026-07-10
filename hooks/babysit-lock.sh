#!/usr/bin/env bash
# babysit-lock.sh — machine/user-wide mutex for /babysit-prs sweeps.
#
# Prevents two sweeps (hourly cron, the launchd plist, a manual invocation, or
# a second terminal) from OVERLAPPING and racing on the shared surface:
#   - /tmp/babysit-prs-state.json  (non-atomic read-modify-write of streak/fp)
#   - git fetch/reset/worktree ops in the shared ~/code/<repo> clones
#   - /tmp/cli-*.pid CR-CLI launches (double-spent quota)
#   - duplicate @coderabbitai bumps
#
# Owner token = CLAUDE_CODE_SESSION_ID: unique per Claude session, and STABLE
# across that session's hourly cron fires (so this session's own sequential
# sweeps never lock each other out — only a DIFFERENT session/terminal does).
# Falls back to host+ppid if the env var is missing.
#
# Lock file: /tmp/babysit-prs.lock (JSON: owner, host, pid, started, heartbeat).
# TTL = 1200s (20 min). A sweep should finish <10 min; a crashed sweep's lock
# goes stale after TTL and the NEXT sweep reaps it — no manual cleanup needed.
#
# Usage:
#   babysit-lock.sh acquire   exit 0 + "ACQUIRED …"  we now hold it
#                             exit 3 + "LOCKED …"     another LIVE sweep holds it
#                                                     (caller must SKIP the sweep)
#   babysit-lock.sh refresh   exit 0 if we own it (bumps heartbeat), else 3
#   babysit-lock.sh release   exit 0 (removes lock iff we own it, or --force)
#   babysit-lock.sh status    prints lock JSON or "UNLOCKED"
set -euo pipefail

LOCK="${BABYSIT_LOCK:-/tmp/babysit-prs.lock}"
TTL="${BABYSIT_LOCK_TTL:-1200}"

now() { date +%s; }
owner_token() { echo "${CLAUDE_CODE_SESSION_ID:-host-$(hostname)-ppid-$PPID}"; }
field() { jq -r --arg k "$2" '.[$k] // ""' "$1" 2>/dev/null || true; }

ME="$(owner_token)"

case "${1:-}" in
  acquire)
    if [ -f "$LOCK" ]; then
      lo="$(field "$LOCK" owner)"
      hb="$(field "$LOCK" heartbeat)"; [ -z "$hb" ] && hb=0
      age=$(( $(now) - hb ))
      if [ "$lo" != "$ME" ] && [ "$age" -lt "$TTL" ]; then
        echo "LOCKED owner=$lo age=${age}s ttl=${TTL}s"
        exit 3
      fi
      # else: stale (age>=TTL) OR already ours -> (re)take it below
      [ "$lo" != "$ME" ] && echo "REAPED stale lock owner=$lo age=${age}s" >&2
    fi
    printf '{"owner":"%s","host":"%s","pid":%s,"started":%s,"heartbeat":%s}\n' \
      "$ME" "$(hostname)" "$PPID" "$(now)" "$(now)" > "$LOCK"
    echo "ACQUIRED owner=$ME"
    exit 0
    ;;
  refresh)
    [ -f "$LOCK" ] || { echo "UNLOCKED"; exit 3; }
    lo="$(field "$LOCK" owner)"
    [ "$lo" = "$ME" ] || { echo "NOT-OWNER owner=$lo"; exit 3; }
    tmp="$(mktemp)"
    jq --arg hb "$(now)" '.heartbeat=($hb|tonumber)' "$LOCK" > "$tmp" && mv "$tmp" "$LOCK"
    echo "REFRESHED heartbeat=$(now)"
    exit 0
    ;;
  release)
    if [ -f "$LOCK" ]; then
      lo="$(field "$LOCK" owner)"
      if [ "$lo" = "$ME" ] || [ "${2:-}" = "--force" ]; then
        rm -f "$LOCK"; echo "RELEASED owner=$lo"
      else
        echo "NOT-OWNER owner=$lo (left intact)"
      fi
    else
      echo "UNLOCKED"
    fi
    exit 0
    ;;
  status)
    [ -f "$LOCK" ] && cat "$LOCK" || echo "UNLOCKED"
    exit 0
    ;;
  *)
    echo "usage: babysit-lock.sh {acquire|refresh|release|status}" >&2
    exit 2
    ;;
esac
