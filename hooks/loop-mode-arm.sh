#!/usr/bin/env bash
# loop-mode-arm.sh — arm check-careful.sh loop-mode for a bounded window.
#
# While armed (and unexpired), flagged destructive commands AUTO-PROCEED and get
# logged to ~/.claude/cleanup-needed.log instead of prompting — so an unattended
# /loop never wedges on a confirmation. Writes an expiry epoch to
# ~/.claude/hooks/loop-mode; re-run each loop iteration to keep it armed. When the
# loop stops re-arming, it self-disarms after the window (crash-safe: a leftover
# file never silently poisons a later interactive session).
#
#   arm for N min:  loop-mode-arm.sh [minutes]   (default 90)
#   disarm now:     rm ~/.claude/hooks/loop-mode
set -euo pipefail
MIN="${1:-90}"
case "$MIN" in ''|*[!0-9]*) MIN=90 ;; esac
NOW=$(date +%s)
echo $(( NOW + MIN * 60 )) > "$HOME/.claude/hooks/loop-mode"
