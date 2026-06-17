---
name: bulldozer
description: Hourly loop that clears EASY backlog tickets ONE PER SWEEP — find one simple, unblocked, well-spec'd, recent, un-assigned-to-others ticket; confirm it still outstands; fix it in an isolated worktree; run a full ship/self-review flow; update the tracker; save a memory; record state; exit. Self-arms an hourly session-cron and runs until a target count of PRs ships (or the simple queue drains). One ticket per invocation so context stays small and resets between tickets.
argument-hint: "[N target PRs, default 10] [days, default 7] or 'no-loop' / 'stop'"
allowed-tools:
  - Bash
  - Read
  - Edit
  - Write
  - Glob
  - Grep
  - Agent
  - AskUserQuestion
  - Skill
  - TaskCreate
  - TaskUpdate
  - TaskList
  - ToolSearch
  - CronList
  - CronCreate
  - CronDelete
---

<!--
ADAPT BEFORE USE — five things are yours to configure:
1. ISSUE TRACKER: written for Linear via its MCP (list_issues / get_issue / save_issue /
   save_comment). Swap in your tracker's API/MCP if different. "DEV-NNNN" is just Linear's
   issue-key shape — use yours.
2. CHECKOUT ROOT: replace `~/code/` with wherever your repo clones live. The skill creates
   throwaway worktrees under `/tmp/<repo>-<id>` off each repo's canonical clone.
3. ASSIGNEE: "assign yourself" = the tracker user the loop may take tickets for. NEVER
   takes a ticket assigned to someone else.
4. SHIP FLOW: invokes a `PRlaunch` skill (deep-review → reviewer CLI → outcome eval → open
   PR). Swap in your own ship/review command; if you don't have one, inline: test + lint +
   open a PR.
5. MEMORY (optional): "save a memory" assumes a persistent-memory MCP (e.g. a `memory_write`
   tool). If you have none, skip Step 6 — the state file still carries dedup across sweeps.
-->

<objective>
Drain a backlog of EASY tickets, **ONE ticket per invocation**, on an hourly loop. Each sweep: pick the single best simple/unblocked/well-spec'd/recent/un-stolen ticket you haven't actioned yet, confirm it still outstands, fix it, ship it, update the tracker, **persist a memory**, record it in the state file, and **exit**. The hourly cron re-fires with FRESH context (it only re-reads the tiny state file), so you never carry N unrelated tickets in one context. Repeat until `target` PRs have shipped or the simple queue is drained.

Why one-per-sweep: doing many tickets in one context bloats it to hundreds of thousands of tokens of unrelated detail. One ticket per sweep keeps each run lean and lets context reset between them — same shape as `/babysit-prs`.

Hard scope: NEVER merge. NEVER start a ticket assigned to someone else. NEVER pick blocked / epic / research / needs-design / drifted / stacked-on-unmerged tickets. One ticket = one branch = one worktree = one PR per sweep.
</objective>

<arguments>
- A number = target **shipped-PR** count (default **10**). A second number = recency window in days (default **7**).
- **`target` counts shipped PRs only.** A resolve-no-PR (Step 2 confirm-resolved) advances the queue + resets the stall streak but does NOT increment `shipped` — so the loop keeps sweeping for the Nth *shippable* PR. The 3-empty/no-action-sweep stall guard stops it when the simple queue is genuinely dry (only already-fixed/drifted tickets remain).
- `no-loop` = run ONE sweep, don't arm the cron. `stop` = CronDelete the bulldozer job + delete the state file + report + exit.
- Bare `/bulldozer` = arm the hourly cron + run one sweep toward 10.
</arguments>

<process>

## Step 0 — Load state + self-arm hourly session-cron

**0a. Load `/tmp/bulldozer-state.json`.** Shape:
```json
{ "target": 10, "shipped": 3, "resolved_no_pr": 2, "days": 7,
  "actioned": { "DEV-101": "api-service#41", "DEV-118": "already-fixed-Done" },
  "no_progress_streak": 0, "last_iter_at": "2026-06-17T08:00:00Z" }
```
```bash
jq -c . /tmp/bulldozer-state.json 2>/dev/null || echo '{"target":10,"shipped":0,"resolved_no_pr":0,"days":7,"actioned":{},"no_progress_streak":0}'
```
- No file → fresh state from `$ARGUMENTS` (target/days) or defaults.
- `last_iter_at` > 6h old → stale prior session; keep `actioned`/`shipped` (real progress) but reset `no_progress_streak`.
- `$ARGUMENTS == "stop"` → CronDelete the bulldozer job, `rm -f /tmp/bulldozer-state.json`, report totals, **exit**.
- **If `shipped >= target` → AUTO-STOP (target met):** CronDelete the bulldozer job, report the run summary, **exit**. Do not pick another ticket.

**0b. Self-arm the hourly cron (skip if `no-loop`/`stop`).** `CronList`; if no job has `prompt == "/bulldozer"`, `CronCreate(cron: "13 * * * *", prompt: "/bulldozer", recurring: true, durable: false)`. `durable: false` = session-only → it runs every hour **for as long as the terminal is open** and dies when you close it (note the cron's own 7-day auto-expire). Note the armed cron id in the report's first line.

**0c. Arm careful-hook loop-mode** so routine worktree/`.venv`/`test_*.db` teardown auto-proceeds + logs instead of wedging the unattended loop on a confirm prompt (self-expires 90 min; re-armed each sweep):
```bash
~/.claude/hooks/loop-mode-arm.sh 90 2>/dev/null || true
```

## Step 1 — Select ONE eligible ticket (not already actioned)

Query your tracker for backlog tickets created in the last `days` (Linear: `list_issues` state:backlog, createdAt:-P<days>D, orderBy:createdAt, generous limit). The result is large — **delegate the parse to a subagent** (it slices the saved tool-result file and returns a ranked shortlist) so the dump never enters your context. Tell it to EXCLUDE every identifier already in `state.actioned`, and to apply `<selection-criteria>`.

From the shortlist, take the **single best** candidate. Build a dedup set of in-flight refs first and skip any match (the `prlaunch-ok` markers are written by the PRlaunch ship flow; `~/code/` is your checkout root):
```bash
(ls ~/.claude/prlaunch-ok/ 2>/dev/null; for r in ~/code/*/; do git -C "$r" worktree list 2>/dev/null; done) \
  | grep -oiE "dev-?[0-9]+" | tr 'A-Z' 'a-z' | sed 's/dev-*/dev-/' | sort -u
```

**If no eligible ticket remains this sweep:** the recent simple queue is momentarily empty — but new tickets may land next hour, so DON'T kill the loop yet. `no_progress_streak += 1`, save state, report `no eligible ticket this sweep (streak <n>/3)`, leave the cron armed, **exit**. Only the Step-7 stall guard (3 consecutive empty/no-action sweeps) actually CronDeletes — so a temporarily-dry queue keeps the hourly sweeper alive, and a genuinely-dry one stops after ~3h.

## Step 2 — Confirm it still outstands (the gate — do not skip)

Pull the full ticket (capture the tracker's exact branch-name hint if it provides one). Open the REAL code on the repo's **default branch** and verify the described bug/condition still exists AND can be fixed off the default branch. **Common drift outcomes that mean DON'T fix:**
- Already fixed on the default branch (e.g. the fix landed with the parent PR).
- The named symbols/files don't exist (ticket drifted from a since-refactored codebase).
- The target code only exists on an **unmerged** PR branch (stacked — branching from the default branch can't reach it). Do not stack on an in-review base unattended.
- Assignee changed to someone else since the shortlist → skip (never steal).

If it does NOT cleanly outstand: post a precise diagnostic comment on the ticket (what you found + what's needed), set its state if warranted (already-fixed → Done), record `actioned[DEV] = "resolved:<reason>"`, `resolved_no_pr += 1`, save state, report this sweep's outcome, and **exit** (next hour's sweep picks the next ticket). Confirming-and-resolving is a valid sweep action.

## Step 3 — Fix it (isolated worktree off the DEFAULT branch)

- `DEF=$(gh repo view <owner>/<repo> --json defaultBranchRef -q .defaultBranchRef.name)` — **NOT always `main`** (many repos default to `develop`).
- Refresh the LOCAL base ref so a later reviewer-CLI diff is clean (stale local ref → "Too many files"): `git -C <canonical-clone> fetch origin "$DEF":"$DEF"` (harmless if `$DEF` is checked out).
- `git -C <canonical-clone> worktree add -b <branch> /tmp/<repo>-<id> origin/$DEF` (use the tracker's branch-name hint if it gives one, so the PR auto-links).
- Make the smallest correct change. Match existing precedent (grep how the codebase already solves the class before inventing). Apply the **one-brain rule** — fix the whole bug class or fix + file a consolidation ticket, never a silent lane-fix.

## Step 4 — Ship / self-review

Invoke your ship flow (this repo's `PRlaunch` skill) for the worktree and follow it: deep-review (diff) → reviewer CLI (refresh local ref first; **skip-and-record if rate-limited/timing out** — it's ~3/hr and slow; cloud review on the PR is the backstop) → outcome eval (grade what the USER receives, not transport; N/A for pure internal plumbing) → re-gate the final tree → push → open a **ready** PR (`--base $DEF`, a past-tense Testing section, link the ticket). Never merge. (No ship skill? Inline: run tests + lint, push, open the PR.)

## Step 5 — Update the tracker

Set the ticket **In Progress + assign yourself** (only if unassigned/already-yours) at branch-create, and **In Review** on PR open. Then verify the PR attachment actually landed (re-fetch the ticket; attach the PR URL explicitly if the auto-link didn't fire — links are systematically under-attached).

## Step 6 — Save a memory (every shipped PR; skip if you have no memory tool)

Persist one memory via your persistent-memory MCP so the learning survives the per-sweep context reset:
- a stable id like `bulldozer-dev-<NNNN>`, title `Bulldozer: DEV-<NNNN> <short> → <repo>#<PR>`.
- text: the ticket, repo + PR # + branch, the actual fix (file:line + what changed), how it was VERIFIED (the empirical check — test/repro/outcome), and any non-obvious gotcha hit this sweep. 3–6 tight sentences — enough that a future agent (or the next sweep) needs zero re-derivation.

## Step 7 — Record state + report + EXIT

Update `/tmp/bulldozer-state.json`: `actioned[DEV] = "<repo>#<PR>"`, `shipped += 1`, `no_progress_streak = 0` (or `+1` if this sweep neither shipped nor resolved anything), `last_iter_at = now`. Then print a tight one-sweep report: `DEV-NNNN → <repo>#<PR> — <one-line> | shipped <s>/<target>, resolved-no-pr <r>`. Then:
- **`shipped >= target`** → AUTO-STOP (target met): CronDelete the job, final summary, exit.
- **`no_progress_streak >= 3`** → AUTO-STOP (stalled): 3 straight sweeps actioned nothing (queue dry or all-drifted). CronDelete, report `AUTO-STOPPED (stalled)`, exit.
- otherwise → leave the cron armed; the next hourly sweep continues with fresh context. **EXIT now — do NOT pick a second ticket this sweep.**

</process>

<selection-criteria>
ELIGIBLE only if ALL hold (AND-ed — **any single failed criterion disqualifies, no matter how strong the others are**):
1. **Recent** — created within `days` (default 7). Lower drift.
2. **Unblocked** — no open blockers.
3. **Assignee-safe** — UNASSIGNED or already yours. Assigned to anyone else → SKIP, never reassign/steal.
4. **Well-specified** — concrete description with a clear fix or acceptance criteria. Vague one-liner is NOT eligible.
5. **Small scope** — bug fix, config/one-liner, single-function change, guard, missing schedule/registry/rate-card entry, a small endpoint mirroring a sibling. NOT: epics, parent issues, spikes/research, "needs design", prod-ops/migrations/CI-workflow/branch-protection changes, or anything whose target code isn't on the default branch.
6. **Not already actioned** (`state.actioned`) and **not in-flight** (ship-flow markers / existing worktrees dedup set).

Prefer the SMALLER ticket when unsure. Better to ship a trivially-correct PR than to stall on a medium one.
</selection-criteria>

<gotchas>
- **Default branch ≠ main.** Branch off + PR into the repo's actual default (often `develop`).
- **Refresh the local base ref before the reviewer CLI** (`git fetch origin <base>:<base>`) or it reports phantom "Too many files".
- **The reviewer CLI is ~3/hr and slow** — one attempt, then skip-and-record (cloud review on the pushed PR is the backstop). Kill stale CLI processes first.
- **Frontend verify without a full install:** symlink the canonical clone's `node_modules` into the `/tmp` worktree (`ln -s ~/code/<repo>/node_modules …`), then run `eslint`/`vitest run <file>`/`tsc --noEmit`. Python repos: run via the canonical clone's `.venv/bin/python` with dummy env vars the config requires (`DATABASE_URL`, secrets, etc.).
- **Pre-existing failures are noise** — env-only (no local DB, container-only paths), or pre-existing red tests/type errors in unrelated files are OUT OF SCOPE; verify only YOUR change is green.
- **Grade outcomes, not transport** — read the words/pixels/emitted markup the user gets; a throwaway render/assertion or a sqlite repro of the exact rows is a real outcome check; a 200/DB-hash is not.
- **The careful hook defers destructive deletes** during unattended runs — don't depend on `rm`; `/tmp` worktrees + untracked scratch are fine to leave.
- **Recent tickets spun off in-flight reviews drift fast** — the still-stands gate (Step 2) catches already-fixed / stacked-on-unmerged / renamed-symbols. Expect to resolve-without-PR a meaningful fraction; that's the gate working, not a failure.
</gotchas>

<guardrails>
- ONE ticket per sweep — pick it, ship-or-resolve it, save memory, record state, EXIT. Never batch.
- NEVER merge. NEVER reassign/start someone else's ticket. NEVER widen a ticket into an epic.
- NEVER stack on an unmerged in-review base unattended (drift risk) — resolve-no-PR with a note instead.
- NEVER re-arm the cron after AUTO-STOP — re-invoke `/bulldozer` (or `/loop 1h /bulldozer`) yourself.
- If you can't verify a fix actually works, say what you verified and what you didn't in the PR Testing section; don't claim it works.
- Stay in-lane: a fix needing another repo → note it, resolve-no-PR, don't hop repos silently.
- Each sweep should finish well under the hour; if the ship flow stalls (wedged CLI, flaky rig), record what you did and let the next sweep continue rather than thrashing.
</guardrails>

<context>
Args (target / days / no-loop / stop): $ARGUMENTS
</context>
