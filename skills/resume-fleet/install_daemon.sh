#!/bin/bash
# resume-fleet v0.2 — install/manage the launchd daemon that auto-continues capped
# Claude Code sessions. It's a LaunchAgent (per-user, GUI session) so it can drive
# System Events keystrokes. Idempotent.
#
#   install_daemon.sh install     write plist + load + start (default)
#   install_daemon.sh status      is it loaded? recent log
#   install_daemon.sh disable     stop acting (touch the disable flag) — stays loaded
#   install_daemon.sh enable      remove the disable flag
#   install_daemon.sh tick        run ONE tick now (for testing)
#   install_daemon.sh uninstall   unload + remove the plist
#
# CONFIG (env): EDITOR_APP/EDITOR_PROC (VS Code default; Cursor="Cursor"),
#   RF_SELF (a session id to skip in the edge scan), INTERVAL (default 120s).

set -uo pipefail
RF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.resume-fleet.daemon"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_="$(id -u)"
EDITOR_APP="${EDITOR_APP:-Visual Studio Code}"
EDITOR_PROC="${EDITOR_PROC:-Code}"
INTERVAL="${INTERVAL:-120}"
RF_SELF="${RF_SELF:-}"
CMD="${1:-install}"

write_plist(){
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$RF_DIR/resume_daemon.sh</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>RF_DIR</key><string>$RF_DIR</string>
    <key>EDITOR_APP</key><string>$EDITOR_APP</string>
    <key>EDITOR_PROC</key><string>$EDITOR_PROC</string>
    <key>RF_SELF</key><string>$RF_SELF</string>
    <key>PATH</key><string>/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin</string>
  </dict>
  <key>StartInterval</key><integer>$INTERVAL</integer>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$HOME/.claude/resume-fleet-daemon.out</string>
  <key>StandardErrorPath</key><string>$HOME/.claude/resume-fleet-daemon.err</string>
</dict>
</plist>
PL
}

case "$CMD" in
  install)
    write_plist
    launchctl bootout "gui/$UID_/$LABEL" 2>/dev/null
    launchctl bootstrap "gui/$UID_" "$PLIST" 2>/dev/null || launchctl load "$PLIST" 2>/dev/null
    launchctl enable "gui/$UID_/$LABEL" 2>/dev/null
    launchctl kickstart -k "gui/$UID_/$LABEL" 2>/dev/null
    echo "installed + loaded: $LABEL (every ${INTERVAL}s, editor=$EDITOR_PROC)"
    echo "plist: $PLIST"
    echo "NOTE: first run needs Accessibility permission for the launchd job —"
    echo "  System Settings > Privacy & Security > Accessibility (approve if prompted)."
    ;;
  status)
    if launchctl print "gui/$UID_/$LABEL" >/dev/null 2>&1; then echo "LOADED: $LABEL"; else echo "NOT loaded"; fi
    [ -f "$HOME/.claude/resume-fleet.disabled" ] && echo "STATE: DISABLED (flag present)" || echo "STATE: enabled"
    echo "--- recent daemon log ---"; tail -n 8 "$HOME/.claude/resume-fleet-daemon.log" 2>/dev/null || echo "(no log yet)"
    ;;
  disable) touch "$HOME/.claude/resume-fleet.disabled"; echo "disabled (daemon stays loaded but will not act)";;
  enable)  rm -f "$HOME/.claude/resume-fleet.disabled"; echo "enabled";;
  tick)    RF_DIR="$RF_DIR" EDITOR_APP="$EDITOR_APP" EDITOR_PROC="$EDITOR_PROC" RF_SELF="$RF_SELF" bash "$RF_DIR/resume_daemon.sh"; echo "tick done — see ~/.claude/resume-fleet-daemon.log";;
  uninstall)
    launchctl bootout "gui/$UID_/$LABEL" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null
    rm -f "$PLIST"; echo "uninstalled: $LABEL";;
  *) echo "usage: install_daemon.sh {install|status|disable|enable|tick|uninstall}"; exit 1;;
esac