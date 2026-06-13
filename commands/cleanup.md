---
name: cleanup
description: Resolve cleanup debt — re-run the deletes that the careful hook deferred during unattended /loop or /goal runs, approving each with a human present. Reads ~/.claude/cleanup-needed.log.
argument-hint: "(no args) — review and clear the cleanup queue"
allowed-tools:
  - Bash
  - Read
  - AskUserQuestion
---

<objective>
Clear the cleanup debt accrued during unattended loops. While `loop-mode` is armed, the careful hook (`check-careful.sh`) does NOT run an unrecognized `rm -r` — it defers it to `~/.claude/cleanup-needed.log` so the loop never wedges. This command reviews that queue now that a human is present, runs the approved deletes, and clears them.

The queue holds only ⚠ *unrecognized* deletes — anything the classifier recognized as routine (virtualenvs, caches, test DBs) was already done silently during the loop and never queued.
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

## Step 3 — Confirm
Summarize the pending deletes (command + the ⚠ items) and ask which to run — offer **all / select / none**. Never bulk-run without confirmation; these are the deletes that were held back precisely because they weren't recognized.

## Step 4 — Run the approved deletes
For each approved entry, re-run its exact `cmd` (it usually contains its own `cd`; otherwise `cd` to its `cwd` first). loop-mode is OFF now (you're attended), so the careful hook will silently allow anything it recognizes and plainly prompt you on anything still ⚠.
On success, drop that entry:
```bash
python3 ~/.claude/hooks/cleanup-sweep.py --remove <i>
```
**Remove handled entries in DESCENDING index order** so indices don't shift under you. Leave declined entries in the queue.

## Step 5 — Report
State what was cleaned and what (if anything) remains pending.

</process>
