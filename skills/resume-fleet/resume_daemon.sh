#!/bin/bash
# resume-fleet v0.2 daemon tick — run periodically by launchd (StartInterval ~120s).
# Edge-triggered + bounded active window, so it steals focus ONLY around real caps:
#   1. cheap disk scan (capped_edges.py) for a FRESH usage-limit event
#   2. if fresh edge OR still inside the retry window -> run ONE UI probe (resume_fleet)
#   3. the UI probe is the arbiter: if it finds a real popup, act + open a 15-min retry
#      window (keep polling to catch the reset); a soft/non-blocking edge just probes once
#   4. macOS notification whenever it actually continues a session
#
# Toggle off:  touch ~/.claude/resume-fleet.disabled   (rm to re-enable)
#
# CONFIG (env, usually set in the launchd plist):
#   RF_DIR, STATE, LOG, MIN_GAP, RETRY_WINDOW, RF_SELF (session id to skip in edge scan),
#   plus resume_fleet.sh's EDITOR_APP / EDITOR_PROC / NTABS.

RF_DIR="${RF_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
STATE="${STATE:-$HOME/.claude/resume-fleet-daemon.json}"
LOG="${LOG:-$HOME/.claude/resume-fleet-daemon.log}"
FLEET_LOG="${FLEET_LOG:-$HOME/.claude/resume_fleet.log}"
MIN_GAP="${MIN_GAP:-120}"
RETRY_WINDOW="${RETRY_WINDOW:-900}"
SOFT_COOLDOWN="${SOFT_COOLDOWN:-1200}"   # after an empty probe, dampen SOFT-edge re-probes
DISABLE_FLAG="${DISABLE_FLAG:-$HOME/.claude/resume-fleet.disabled}"

log(){ echo "[$(date '+%F %H:%M:%S')] $*" >> "$LOG"; }
notify(){ osascript -e "display notification \"$1\" with title \"resume-fleet\"" >/dev/null 2>&1; }

[ -f "$DISABLE_FLAG" ] && exit 0

now=$(date +%s)

# --- read state (last_edge_ts / retry_until / last_ui_run) ---
read_state(){ python3 - "$STATE" <<'PY'
import json,sys,os
p=sys.argv[1]; d={"last_edge_ts":"","retry_until":0,"last_ui_run":0,"soft_cd_until":0}
if os.path.exists(p):
    try: d.update(json.load(open(p)))
    except Exception: pass
print(f"{d['last_edge_ts']}\t{d['retry_until']}\t{d['last_ui_run']}\t{d['soft_cd_until']}")
PY
}
write_state(){ python3 - "$STATE" "$1" "$2" "$3" "$4" <<'PY'
import json,sys
json.dump({"last_edge_ts":sys.argv[2],"retry_until":int(sys.argv[3]),
           "last_ui_run":int(sys.argv[4]),"soft_cd_until":int(sys.argv[5])}, open(sys.argv[1],"w"))
PY
}

IFS=$'\t' read -r last_edge_ts retry_until last_ui_run soft_cd_until <<<"$(read_state)"
retry_until="${retry_until:-0}"; last_ui_run="${last_ui_run:-0}"; soft_cd_until="${soft_cd_until:-0}"

# --- 1. cheap disk edge scan ---
SELF_ARG=(); [ -n "$RF_SELF" ] && SELF_ARG=(--self "$RF_SELF")
edges="$(python3 "$RF_DIR/capped_edges.py" --since "$last_edge_ts" "${SELF_ARG[@]}" 2>/dev/null)"
get(){ printf '%s' "$edges" | python3 -c "import json,sys;print(json.load(sys.stdin).get('$1') or '$2')" 2>/dev/null || echo "$2"; }
fresh="$(get fresh 0)"; fresh_hard="$(get fresh_hard 0)"; max_ts="$(get max_ts '')"

# run the UI probe when: a HARD cap edge appeared (always) OR a SOFT edge appeared and
# its cooldown has passed OR we're still inside a real-block retry window.
run_ui=0; why=""
if [ "${fresh_hard:-0}" -gt 0 ]; then run_ui=1; why="hard-edge($fresh_hard)"; fi
if [ "$run_ui" -eq 0 ] && [ "${fresh:-0}" -gt 0 ] && [ "$now" -ge "$soft_cd_until" ]; then run_ui=1; why="soft-edge($fresh)"; fi
if [ "$now" -lt "$retry_until" ]; then run_ui=1; why="${why:+$why,}retry-window"; fi
[ "${fresh:-0}" -gt 0 ] && log "edges: fresh=$fresh hard=$fresh_hard max_ts=$max_ts -> ${why:-suppressed(soft-cooldown)}"
[ -n "$max_ts" ] && [[ "$max_ts" > "$last_edge_ts" ]] && last_edge_ts="$max_ts"

# --- 2. throttled UI probe (the arbiter) ---
if [ "$run_ui" -eq 1 ] && [ $(( now - last_ui_run )) -ge "$MIN_GAP" ]; then
  blocked="$(MODE=act LOG="$FLEET_LOG" bash "$RF_DIR/resume_fleet.sh" 2>/dev/null | tail -n1)"
  last_ui_run=$(date +%s)
  acted="$(grep -E '=== done' "$FLEET_LOG" | tail -n1 | sed -n 's/.*acted=\([0-9]*\).*/\1/p')"
  log "UI probe ($why): blocked=${blocked:-?} acted=${acted:-0}"
  if [ "${blocked:-0}" -gt 0 ]; then
    retry_until=$(( $(date +%s) + RETRY_WINDOW ))          # keep polling to catch the reset
  else
    soft_cd_until=$(( $(date +%s) + SOFT_COOLDOWN ))       # empty probe -> dampen soft re-probes
  fi
  if [ "${acted:-0}" -gt 0 ]; then
    notify "continued ${acted} capped session(s)"
    log "NOTIFIED: continued ${acted} session(s)"
  fi
fi

write_state "$last_edge_ts" "$retry_until" "$last_ui_run" "$soft_cd_until"