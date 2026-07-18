#!/bin/zsh
# babysit-hourly-gate.sh <name> <timeout-s> <prompt...>
#
# Pre-gate for the hourly headless babysit (com.you.claude-babysit-hourly).
# The rule: don't run the background headless babysit when a terminal is already
# running babysit -- UNLESS there's no such terminal, or it's stuck. This is the
# automated form of the README "collision rule" (one driver per skill).
#
# Mechanism: hooks/babysit-heartbeat.py bumps ~/.claude/babysit-heartbeat every
# turn of a live /babysit-prs session. This gate reads that heartbeat's age:
#   fresh (< STALE_S)      -> a terminal is actively babysitting -> SKIP the backup
#   stale (>= STALE_S)     -> terminal stuck / blocked on a prompt -> run the backup
#   missing                -> no terminal running babysit          -> run the backup
# When it does run, HEADLESS_BABYSIT=1 stops the backup's own /babysit-prs from
# writing the heartbeat (which would otherwise look like a live interactive one).
set -u

HEARTBEAT="$HOME/.claude/babysit-heartbeat"
STALE_S=4200          # 70 min. A healthy hourly /loop bumps the
                      # heartbeat every sweep, well inside this window, so it
                      # always suppresses the backup; only a wedged or closed
                      # terminal goes quiet long enough to let the backup through.
LOG="$HOME/.claude/logs/headless-babysit.log"
mkdir -p "$(dirname "$LOG")"

if [ -f "$HEARTBEAT" ]; then
  now=$(date +%s)
  mt=$(stat -f%m "$HEARTBEAT" 2>/dev/null || echo 0)
  age=$(( now - mt ))
  if [ "$age" -lt "$STALE_S" ]; then
    echo "$(date '+%F %T') [babysit] SKIP: interactive babysit alive (heartbeat age ${age}s < ${STALE_S}s)" >>"$LOG"
    exit 0
  fi
  echo "$(date '+%F %T') [babysit] heartbeat stale (age ${age}s >= ${STALE_S}s) -> running backup" >>"$LOG"
else
  echo "$(date '+%F %T') [babysit] no heartbeat -> running backup" >>"$LOG"
fi

export HEADLESS_BABYSIT=1
exec /bin/zsh "$HOME/.claude/launchd/headless-skill.sh" "$@"
