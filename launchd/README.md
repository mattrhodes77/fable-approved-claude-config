# Headless scheduled skills (no open session required)

Session-bound loops (`/loop 1h /babysit-prs`, in-session cron) die with the
session. This rail runs skills on a schedule with **no terminal open**:
launchd (macOS) fires `headless-skill.sh`, which runs `claude -p "<prompt>"`
— a fresh headless Claude Code session per fire that loads your full config
(skills, hooks, settings.json model), does the job, and exits.

## The wrapper handles the launchd gotchas
- launchd has no shell aliases and a minimal PATH — the wrapper calls the real
  `claude` binary with `--dangerously-skip-permissions` explicitly and sets PATH.
- Per-skill lock: overlapping fires of the same skill are skipped.
- Logs to `~/.claude/logs/headless-<name>.log`, rotated at ~1MB; timeout kills hung runs.
- OAuth-authenticated remote MCP servers may be absent headless — give skills a
  CLI/API fallback (see `hooks/reconcile-ticket.sh` for the tracker-API pattern).

## Install
1. Copy an example plist, replace `/Users/you` with your home dir and rename the label.
2. `cp <plist> ~/Library/LaunchAgents/ && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/<plist>`
3. Test a fire: `launchctl kickstart gui/$(id -u)/<label>`, then read the log.
4. Disable: `launchctl bootout gui/$(id -u)/<label>`

## The collision rule
The lock guards against overlapping **headless** fires only. Do not run a
skill's interactive `/loop` while its headless plist is loaded — two drivers
sweeping the same PRs/tickets means duplicate review bumps and worktrees.
One driver per skill.

## Caveats
- launchd only fires while the machine is awake; missed calendar fires coalesce
  to one run on wake. Fine for weekly reports; know this for hourly loops.
- Headless sessions bill like any session — pick intervals accordingly.
