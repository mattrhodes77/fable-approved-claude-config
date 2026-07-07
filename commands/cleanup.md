---
name: cleanup
description: Resolve cleanup debt — delete the targets the careful hook deferred during unattended /loop or /goal runs, approving each with a human present. Reads ~/.claude/cleanup-needed.log.
argument-hint: "(no args) — review and clear the cleanup queue"
allowed-tools:
  - Bash
  - Read
  - AskUserQuestion
---

<objective>
Clear the cleanup debt accrued during unattended loops. The careful hook (`check-careful.sh`) NEVER runs an unrecognized `rm -r` — for deletes it can't tell loop from attended, so it always defers them to `~/.claude/cleanup-needed.log` rather than risk wedging a loop. This command reviews that queue now that a human is present, deletes the approved targets, and clears them.

The queue holds only ⚠ *unrecognized* deletes — anything the classifier recognized as routine (virtualenvs, caches, test DBs) was already done silently and never queued.

**Do NOT re-run the queued `cmd` to clean up.** The hook would just re-defer it, AND a queued `cmd` is the *original* command that happened to contain the rm (e.g. a `git worktree add` or `git clone` whose `rm -rf` was incidental) — replaying it RECREATES the scratch. Use `--run`, which parses the command for its actual delete targets and deletes only those, via `shutil`/`os` (no re-defer, no side effects).
</objective>

<process>

## Step 1 — Show the queue
```bash
python3 ~/.claude/hooks/cleanup-sweep.py
```
If it prints "No cleanups pending," report that and STOP — nothing to do.

## Step 2 — Load the entries
```bash
python3 ~/.claude/hooks/cleanup-sweep.py --json
```
Each line is `{i, ts, cwd, cmd, reason}`. `reason` already itemizes every target with ✓ (routine) / ⚠ (please check) — use it to brief the user.

## Step 3 — Confirm with the user
Summarize the pending deletes (command + the ⚠ items) and ask which to run — offer **all / select / none**. Never bulk-run without confirmation; these are the deletes that were held back precisely because they weren't recognized.

## Step 4 — Delete the approved targets
`--run <i>` deletes the parsed targets for entry `<i>` (cd- and `VAR=`-aware, glob-expanded, relative to the entry's cwd) and drops the entry on success:
```bash
python3 ~/.claude/hooks/cleanup-sweep.py --run <i>
```
It prints each target as `✓ deleted` / `· already gone` / `⚠ skipped` (unresolved shell var or refused catastrophic path). If anything is skipped or errors, the entry is KEPT for manual handling. To action the whole queue at once (descending, so indices stay valid):
```bash
python3 ~/.claude/hooks/cleanup-sweep.py --run-all
```
For an entry the user **declines**, drop it without deleting: `--remove <i>` (descending order). Leave anything unresolved in the queue.

## Step 5 — Report
State what was cleaned and what (if anything) remains pending.

</process>
