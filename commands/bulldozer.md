---
name: bulldozer
description: Self-arming hourly heartbeat that clears EASY backlog tickets by DRAINING the eligible queue back-to-back each wake — one FRESH SUBAGENT per ticket (so the orchestrator context stays lean and each ticket gets isolated context) that confirms it still outstands, fixes it in an isolated worktree, runs a full ship/self-review flow, updates the tracker, and saves a memory. Keeps going ticket-after-ticket (a single ticket never stalls the chain for an hour); the hourly cron only re-wakes to catch newly-filed tickets. Runs until a target count of PRs ships, or the simple queue is dry.
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
   save_comment). Swap in your tracker. "DEV-NNNN" is just Linear's issue-key shape.
2. CHECKOUT ROOT: replace `~/code/` with where your repo clones live. The skill creates
   throwaway worktrees under `/tmp/<repo>-<id>` off each repo's canonical clone.
3. ASSIGNEE: "assign yourself" = the tracker user the loop may take tickets for. It NEVER
   takes a ticket assigned to someone else.
4. SHIP FLOW: each subagent invokes a `PRlaunch` skill (deep-review → reviewer CLI →
   outcome eval → open PR). Swap in your own; no ship skill → inline test + lint + open PR.
5. MEMORY (optional): the subagent saves a memory via a persistent-memory MCP (e.g. a
   `memory_write` tool). No memory tool → drop that step; the state file still dedups.
-->

<objective>
Drain a backlog of EASY tickets. Each wake (an hourly cron fire, or a manual `/bulldozer`) runs a **drain loop**: keep picking the next eligible simple/unblocked/well-spec'd/recent/un-stolen ticket and clearing it **back-to-back** until `target` PRs have shipped or the eligible queue is dry — DON'T stop after one and DON'T wait an hour between tickets.

**Each ticket runs in a FRESH SUBAGENT** (the Agent tool), which does the whole thing end-to-end — confirm-still-outstands → fix in an isolated worktree → ship flow → update the tracker → save a memory → return a one-line result. The orchestrator only accumulates the one-liners + the state file, so its context stays lean no matter how many tickets clear; the per-ticket detail lives in the disposable subagent and is discarded when it returns. This gives all three properties at once: **per-ticket context isolation**, **back-to-back progress** (no hourly idle between tickets), and **resilience** (a subagent that fails returns an error → record it and move to the next ticket; one bad ticket never kills the chain).

The hourly cron is a **heartbeat**, not the per-ticket driver: it re-wakes the loop to catch tickets filed since the last drain (and to resume if a wake was interrupted). It runs while the terminal is open and dies when you close it.

Hard scope: NEVER merge. NEVER start a ticket assigned to someone else. NEVER pick blocked / epic / research / needs-design / drifted / stacked-on-unmerged tickets. One ticket = one subagent = one branch = one worktree = one PR.
</objective>

<arguments>
- A number = target **shipped-PR** count (default **10**). A second number = recency window in days (default **7**).
- **`target` counts shipped PRs only.** A resolve-no-PR (a ticket the subagent finds already-fixed/drifted) advances the queue + resets the stall streak but does NOT increment `shipped` — the loop keeps draining toward the Nth *shippable* PR. The stall guard stops it when the queue is genuinely dry.
- `no-loop` = run ONE drain pass, don't arm the heartbeat. `stop` = CronDelete the bulldozer job + delete the state file + report + exit.
- Bare `/bulldozer` = arm the heartbeat + drain toward 10. **Re-invoking after a finished (or >6h stale) batch starts a FRESH batch**: the per-batch `shipped` counter resets to 0 while the `actioned` dedup ledger carries over (already-shipped tickets are skipped, never re-PR'd).
</arguments>

<process>

## Step 0 — Load state + self-arm the hourly heartbeat

**0a. Load `/tmp/bulldozer-state.json`:**
```json
{ "target": 10, "shipped": 3, "resolved_no_pr": 2, "days": 7,
  "actioned": { "DEV-101": "api-service#41", "DEV-118": "resolved:already-fixed-Done" },
  "no_progress_streak": 0, "last_iter_at": "2026-06-17T08:00:00Z" }
```
```bash
jq -c . /tmp/bulldozer-state.json 2>/dev/null || echo '{"target":10,"shipped":0,"resolved_no_pr":0,"days":7,"actioned":{},"no_progress_streak":0}'
```
- No file → fresh state from `$ARGUMENTS` (target/days) or defaults (`shipped=0`, `actioned={}`).
- `$ARGUMENTS == "stop"` → CronDelete the bulldozer job, `rm -f /tmp/bulldozer-state.json`, report totals, **exit**.
- **`actioned` is the dedup ledger** (every DEV ref ever shipped/resolved/failed); it carries across batches and is ONLY wiped by `stop`. `shipped` / `resolved_no_pr` / `no_progress_streak` are **per-batch** counters. **Ledger value conventions** (these govern whether a ticket is re-pickable): `<repo>#<PR>` = shipped → permanent skip; `resolved:<reason>` = resolved → permanent skip; `failed:<reason>` = failed ONCE → **retry-eligible** (gets exactly one more attempt on a later wake); `failedx2:<reason>` = failed twice → permanent skip, never retried again.
- **NEW-BATCH RESET — re-invoking `/bulldozer` after a finished batch starts a FRESH drain (the common case).** If the loaded state has `shipped >= target` (a prior batch already met its target → Step 3 AUTO-STOP deleted its heartbeat, so reaching Step 0a in this state can ONLY be a fresh manual re-invocation) **OR** `last_iter_at` is > 6h old (prior batch stale/abandoned): treat this as a NEW batch → set `shipped=0`, `resolved_no_pr=0`, `no_progress_streak=0`, **KEEP `actioned` as-is** (so already-done tickets are never re-picked → no duplicate PRs), apply any new `target`/`days` from `$ARGUMENTS`, then proceed to drain. **Do NOT auto-stop at load time** — the only AUTO-STOP is in Step 3, after a *live* drain hits target.
- **Otherwise** (recent `last_iter_at`, `shipped < target`) → RESUME the in-flight batch: keep all counters and keep draining toward `target`.

**0b. Self-arm the heartbeat cron (skip if `no-loop`/`stop`).** `CronList`; if no job has `prompt == "/bulldozer"`, `CronCreate(cron: "13 * * * *", prompt: "/bulldozer", recurring: true, durable: false)`. `durable: false` = session-only → runs hourly **while the terminal is open**, dies on close (7-day auto-expire). Note the cron id in the report.

**0c. Arm careful-hook loop-mode** (auto-proceed routine worktree/`.venv`/`test_*.db` teardown instead of wedging on a confirm prompt; self-expires 90 min, re-armed each wake):
```bash
~/.claude/hooks/loop-mode-arm.sh 90 2>/dev/null || true
```

## Step 1 — Build the dedup set + the candidate shortlist (once per wake)

Dedup set of in-flight refs (skip these — already being worked; `prlaunch-ok` markers are written by the ship flow, `~/code/` is your checkout root):
```bash
(ls ~/.claude/prlaunch-ok/ 2>/dev/null; for r in ~/code/*/; do git -C "$r" worktree list 2>/dev/null; done) \
  | grep -oiE "dev-?[0-9]+" | tr 'A-Z' 'a-z' | sed 's/dev-*/dev-/' | sort -u
```

Query your tracker for backlog tickets created in the last `days`. The dump is large — **delegate the parse to a subagent**: have it EXCLUDE every identifier in `state.actioned` + the dedup set — **EXCEPT** entries whose `actioned` value starts with `failed:` (a single prior failure → retry-eligible; include these as lower-ranked retry candidates). `failedx2:` / shipped / `resolved:` entries stay excluded. Apply `<selection-criteria>`, and return a RANKED shortlist of the best simple candidates (identifier + repo + one-line fix + assignee). Keep that shortlist in hand for the drain loop.

## Step 2 — DRAIN LOOP (the core — one fresh subagent per ticket, back-to-back)

Loop over the shortlist, top-ranked first:

1. **Stop conditions (check before each ticket):** `shipped >= target` → done (Step 3 auto-stop, target met). No more shortlist candidates → done (queue dry this wake). A per-wake safety cap of `2 × target` subagents spawned → done (anti-runaway; the heartbeat resumes next hour).
2. **Skip** any candidate now in the dedup set, or in `state.actioned` UNLESS its value is a single `failed:` (retry-eligible — let it through for its one retry). (State may have advanced mid-wake.)
3. **Spawn ONE fresh subagent** (Agent tool, `subagent_type: general-purpose`) with the `<ticket-subagent-prompt>` below, parameterized for this ticket. It does the entire ticket in its own context and returns a STRICT one-line result.
4. **Record the result** into `/tmp/bulldozer-state.json` immediately (so a crash mid-drain doesn't lose progress and the next wake dedups correctly):
   - `shipped <repo>#<PR>` → `actioned[DEV]="<repo>#<PR>"`, `shipped += 1`.
   - `resolved:<reason>` → `actioned[DEV]="resolved:<reason>"`, `resolved_no_pr += 1`.
   - `failed:<reason>` or the subagent returned null/errored → **escalate by prior state:** if `actioned[DEV]` already starts with `failed:` (this WAS its one retry) → set `actioned[DEV]="failedx2:<reason>"` (now permanent, never retried again); otherwise → set `actioned[DEV]="failed:<reason>"` (first failure → eligible for exactly one retry on a later wake). Log it for the report. **Do NOT abort the loop — continue to the next ticket.** One bad ticket never kills the chain.
5. Print a one-line progress note (`DEV-NNNN → <result> | shipped <s>/<target>`) and **continue to the next ticket**.

Run subagents **sequentially** (one ticket at a time) — they push branches, share the reviewer-CLI's hourly quota, and create worktrees; parallel would collide and blow the quota. (If you ever parallelize, cap at 2 and only on different repos.)

<ticket-subagent-prompt>
Spawn with this instruction (fill in DEV-NNNN, repo, the one-line fix, and the canonical clone path):

"You are a bulldozer worker. Ship EXACTLY this one ticket end-to-end, then return a single-line result. Ticket: DEV-NNNN — <title/one-line fix>. Repo: <repo> (canonical clone ~/code/<repo>).

1. Pull the full ticket (capture its branch-name hint). CONFIRM IT STILL OUTSTANDS: read the real code on the repo's DEFAULT branch (`gh repo view <owner>/<repo> --json defaultBranchRef` — NOT always main). If it's already fixed on the default branch, or the named symbols/files don't exist (drifted), or the target code only lives on an unmerged PR branch (stacked) → post a precise diagnostic comment on the ticket, set state if warranted (already-fixed → Done), and RETURN `resolved:<short reason>`. Do not fix it.
2. Otherwise fix it: refresh the local base ref (`git -C ~/code/<repo> fetch origin <DEF>:<DEF>`), create a worktree off origin/<DEF> with the ticket's branch-name hint (`git worktree add -b <branch> /tmp/<repo>-dev<NNNN> origin/<DEF>`), make the smallest correct change matching existing precedent (one-brain: fix the whole class, no lane-fix).
3. Run the **PRlaunch** ship flow for that worktree (deep-review → reviewer CLI with `--base <DEF>`, skip-and-record if rate-limited/slow → outcome eval grading what the USER receives, N/A for pure internal plumbing → re-gate → push → open a READY PR with `--base <DEF>`, past-tense Testing section, link the ticket). NEVER merge.
4. Update the tracker: In Progress + assign yourself on branch-create (only if unassigned/already-yours), In Review on PR open, then verify the PR attachment landed (attach explicitly if missing).
5. Save a memory via your persistent-memory MCP (id `bulldozer-dev-<NNNN>`): the ticket, repo+PR#+branch, the actual fix (file:line + what changed), how it was VERIFIED (the empirical check), and any non-obvious gotcha. 3-6 tight sentences. (Skip if you have no memory tool.)
6. RETURN exactly one line, nothing else: `shipped <repo>#<PR>` OR `resolved:<reason>` OR `failed:<reason>` (what blocked you).

Gotchas: default branch ≠ main (often develop); refresh the local base ref before the reviewer CLI or it sees phantom 'Too many files'; the reviewer CLI is ~3/hr + slow → one try then skip-and-record; FE verify by symlinking the canonical clone's node_modules into the /tmp worktree then eslint/vitest/tsc; Python verify via the canonical .venv/bin/python with dummy env vars the config requires; pre-existing red tests/env-only failures are out of scope — verify only YOUR change; grade outcomes not transport."
</ticket-subagent-prompt>

## Step 3 — After the drain: update state, report, decide

Set `no_progress_streak = 0` if this wake shipped or resolved ≥1 ticket, else `+= 1`. Write `last_iter_at = now`, persist state. Print a compact batch report:
```
Bulldozer wake — armed cron <id>
| DEV-NNNN | <repo>#<PR> / resolved:… / failed:… | <one-line> |
...
shipped <s>/<target> · resolved-no-pr <r> · this wake: <k> shipped, <m> resolved, <f> failed
```
Then:
- **`shipped >= target`** → AUTO-STOP (target met): CronDelete the `/bulldozer` job, final summary, exit.
- **`no_progress_streak >= 3`** → AUTO-STOP (stalled): 3 consecutive wakes cleared nothing (queue dry or all-drifted). CronDelete, `AUTO-STOPPED (stalled)`, exit.
- **otherwise** (queue dry this wake but target not met, or work remains) → leave the heartbeat armed; the next hourly fire re-drains with newly-filed tickets. Report `WAITING (heartbeat armed)` and exit.

</process>

<selection-criteria>
ELIGIBLE only if ALL hold (AND-ed — **any single failed criterion disqualifies, no matter how strong the others are**):
1. **Recent** — created within `days` (default 7). Lower drift.
2. **Unblocked** — no open blockers.
3. **Assignee-safe** — UNASSIGNED or already yours. Anyone else → SKIP, never reassign/steal.
4. **Well-specified** — concrete description with a clear fix or acceptance criteria. Vague one-liner is NOT eligible.
5. **Small scope** — bug fix, config/one-liner, single-function change, guard, missing schedule/registry/rate-card entry, a small endpoint mirroring a sibling. NOT: epics, parent issues, spikes/research, "needs design", prod-ops/migrations/CI-workflow/branch-protection changes, or anything whose target code isn't on the default branch.
6. **Not already actioned** (`state.actioned`) and **not in-flight** (ship-flow markers / worktrees dedup set).

Prefer the SMALLER ticket when unsure.
</selection-criteria>

<gotchas>
- **Default branch ≠ main** (often `develop`) — the subagent branches off + PRs into the repo's actual default.
- **Refresh the local base ref before the reviewer CLI** or it reports phantom "Too many files".
- **The reviewer CLI is ~3/hr and slow** — one attempt, skip-and-record (cloud review on the pushed PR is the backstop). Because subagents run sequentially and each may spend one CLI slot, a long drain exhausts the hourly CLI quota — expected; later tickets skip-and-record and rely on cloud review.
- **FE verify:** symlink the canonical clone's node_modules into the /tmp worktree; **Python verify:** canonical `.venv/bin/python` + dummy env. **Pre-existing failures are out of scope.**
- **Grade outcomes, not transport.**
- **The careful hook defers destructive deletes** in unattended runs — don't depend on `rm`.
- **Recent tickets drift fast** (spun off in-flight reviews) — the subagent's still-stands gate resolves a meaningful fraction without a PR; that's the gate working.
</gotchas>

<guardrails>
- The DRAIN LOOP keeps going ticket-after-ticket within a wake — a single ticket (slow, failed, or resolved-no-PR) NEVER stalls the chain or makes it wait an hour. Only `shipped >= target`, a dry queue + 3-wake stall, or the per-wake safety cap stops it.
- One ticket per SUBAGENT (fresh isolated context); the orchestrator only holds one-liners + state, so it stays lean across an arbitrarily long drain.
- A subagent that errors / returns null → record `failed`, continue. Never abort the loop on one ticket.
- NEVER merge. NEVER reassign/start someone else's ticket. NEVER widen into an epic. NEVER stack on an unmerged base (resolve-no-PR instead).
- NEVER re-arm the cron after AUTO-STOP — re-invoke `/bulldozer` yourself (which starts a FRESH batch: `shipped` resets to 0, `actioned` carries over so nothing is re-PR'd).
- Run subagents sequentially (shared reviewer-CLI quota + worktree/push collisions).
</guardrails>

<context>
Args (target / days / no-loop / stop): $ARGUMENTS
</context>
