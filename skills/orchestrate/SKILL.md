---
name: orchestrate
description: Use when the owner names a PROJECT (fuzzy — "knowledge", "comms", "onboarder", a tracker project URL) rather than specific tickets, and wants the next group of work found and shipped — "/orchestrate <project>", "find next group of tickets in <project> to execute with cheap-model subagents you orchestrate + validate", "next chunk of <project>". Use /execute when the owner names the exact tickets or epic themself; /bulldozer for unattended easy-queue draining; /assign when the work is for a teammate.
---

# orchestrate — project → pick the group → cheap-model builders → validate → PRs

The owner's core shipping workflow, codified from proven runs across projects of different shapes — a 6-unit knowledge-base batch, a 9-unit CRM batch, a 7-unit onboarding batch, a 3-unit audit batch, and iterative same-epic ticket series. The orchestrator (the session model) is the **senior guider**: it resolves the project, picks the group, validates premises and finished work, answers forks — and **never builds inline**. Every build runs in a fresh **sonnet** subagent so orchestrator context stays lean and tokens burn at worker rates.

**This is /execute's batch pipeline with two deltas:** (1) the orchestrator PICKS the work-list (in /execute the list is the owner's, verbatim); (2) subagent-per-unit is mandatory at ANY list size, not just ≳4. Everything else — phase semantics, premise tables, fork rules, worktrees, PRlaunch — defers to the `execute` skill. Worker prompts defer to the `briefs` six-section contract.

**REQUIRED SUB-SKILLS:** `execute` (phases 1–8 per unit), `briefs` (every worker prompt), `/PRlaunch` (run BY each builder, never by the orchestrator).

## Pipeline

### 1. Resolve the project (fuzzy)
`mcp__linear__list_projects {query: <token>}` — try the given word, then obvious synonyms ("knowledge" → your knowledge-base project). A pasted project URL wins outright. If two projects both plausibly match, ask ONE immediate AskUserQuestion — this is the only pre-work ask allowed; everything else batches later.

### 2. Pick the group  ⟵ *the phase that makes /orchestrate different*
- **Priority/value first, then coherence.** Sort the candidate pool by tracker priority (Urgent→High→Medium→Low) and product value BEFORE you cluster — the group must LEAD with the highest-priority *shippable* work. This skill's natural pull is the opposite: "cleanest to ship" biases hard toward old, small, self-contained tickets (they're the ones that *are* clean), while the highest-value work is usually bigger and epic-shaped — so a cleanliness-first heuristic silently skips it. Guard against that. Drop to a lower-priority coherent cluster ONLY when every higher-priority item is genuinely blocked, already in-flight, or needs decomposition first — and name that reason, per item, in the announcement. (Phase-3 validation may still kill a high-priority pick — fine; it dies with evidence, it wasn't skipped.)
- **Epics first.** The group should normally BE an existing open session epic (the `epicbuilder` skill is this step's front half — it structures a loose backlog into ~5-ticket session epics). If the project's next work is sitting loose in Todo/Backlog with no epic covering it, run `/epicbuilder` on the project first, then pick from its output.
- Pull the project's open tickets (Todo/Backlog + Epics parents, priority-ordered, sub-issues included). For a big project, delegate the sweep to ONE read-only sonnet scout (six-section brief, JSON return: candidate groups with ticket ids, states, deps, collision flags) instead of paging it through your own context.
- **Collision check is mandatory.** A ticket is OUT of the group if it's already in flight elsewhere: `me/dev-NNNN-*` exists on origin (`git ls-remote --heads`), an open PR references it (`gh pr list --search`), or a live worktree `<worktrees-dir>/<repo>.dev-NNNN` exists. Don't cross another session's lane.
- **Group shape:** a coherent related cluster — an epic's open children, or same-concern tickets that ship together. Target ~3–8 units. Order by blocked-by deps; same-file units are planned as a stacked PR chain from the start (no dep between them → the more foundational change is the base; record merge order bottom-up).
- **Announce the pick with a one-line rationale, then GO.** Name any collision-excluded tickets in the announcement — a silent omission reads as narrowing the group. The owner delegated the choice — do not ask them to bless it. Only fold a group choice into the batched ask when two groups are equally next AND the tiebreak is product priority you cannot derive.

### 3. Validate everything before building anything
Run /execute phases 1–2 for EVERY unit up front: fresh **read-only** sonnet validator per unit, launched in parallel, pinned to an `origin/main` SHA you fetched THIS session. Each returns the premise-evidence table (the /execute format) via JSON contract. Expect kills: validation regularly closes 1–3 units as already-shipped or premise-contradicted before a line is built (one 9-unit CRM-project batch saw 2 of 9 die here — that's the win, not a failure). Close those with evidence comments; they still appear in the final report.

### 4. One batched ask
Collect ALL true forks across all units (plus any group tiebreak) into a single AskUserQuestion. No drip. No forks → straight to build.

### 5. Build — fresh sonnet subagent per unit, always
- `Agent` tool, `model: "sonnet"`, one builder per ticket, six-section brief per the `briefs` skill — with the gotcha pack below pasted **verbatim** into CONSTRAINTS.
- Independent units → parallel (one message, multiple Agent calls). Same-file/dependent units → sequential, stacked PRs, each brief pinned to the previous branch's tip SHA.
- The builder owns the whole unit: worktree + `me/dev-NNNN-slug` branch, TDD build, real tests, full `/PRlaunch`, tracker update. It returns a JSON contract (ticket, status, pr_url, test_cmd, test_result, notes).
- **The orchestrator never builds inline.** Sole exception: finishing a dead builder's work (recovery table below).

### 6. Validate the work  ⟵ *the senior-guider half*
A worker's "shipped" is a claim, not a fact. Before accepting each return: `gh pr view` the PR exists and diff scope matches the ticket, spot-check the claimed test evidence (re-run its `test_cmd` on the worktree tip, or read the PRlaunch gate ledger). Claim without evidence → SendMessage the worker to produce it, or finish/verify it yourself.

### 7. Report + persist
- Per-unit table: `unit → PR link / closed:<evidence> / halted:<reason> / failed:<reason>`, plus merge-order notes for stacks and any post-merge ops steps for the owner. **TEAM MERGES — never self-merge.**
- If you maintain a session-memory store (this repo's own convention — see "Cross-cutting: how we do memory" in the README, `memory_write`), persist the batch record (units→PRs, premise corrections, new gotchas, ops steps) — every past run did; it's how the next run gets smarter. If you don't run one, the per-unit report table above is the persisted record. Sweep any tracker stragglers the workers missed.

## Worker gotcha pack — paste VERBATIM into every builder brief

```
- FOREGROUND ONLY: never run_in_background, never park "waiting for a
  background/monitor notification" — it will NOT re-invoke you. Poll builds
  and tests to completion in-turn with long Bash timeouts.
- Worktrees have no .venv: run <main-tree>/.venv/bin/python -m pytest from the
  worktree root (cwd-first sys.path makes worktree code win).
- Next.js worktrees: copy .env.local from the main tree BEFORE build, or it
  fails at "Collecting page data" on an auth route looking like an unrelated
  provider bug; remove any stale .next first.
- NEVER broad-kill processes (pkill -f 'next dev' etc.) — parallel sibling
  workers share this machine. Kill by specific PID/port only.
- CR CLI rate-limited (shared plan quota, parallel workers): take PRlaunch's
  authorized skip-with-ledger-note; cloud CodeRabbit is the backstop. Do NOT
  sit waiting for the quota window.
- Stacked unit: fetch your base branch at spawn AND again before finishing —
  mid-batch merges of the base are real; rebase/retarget accordingly.
```

## Recovery playbook (orchestrator side)

| Symptom | Action |
|---|---|
| Worker idle "waiting for background/monitor" | SendMessage: "run everything FOREGROUND, poll to completion in-turn, no run_in_background" — this alone recovered 4+ stalls per batch |
| Worker dead on session cap | Inspect its worktree (`git status`/`diff`, judge coherence), verify yourself (build/lint/tests), commit, record gates via `~/.claude/hooks/prlaunch-gate.sh` (`deep_review`/`cr_cli --skipped`, `outcome_eval --na`, `tests --cmd`; then `check`), `gh pr create` — the finish-a-dead-builder pattern |
| Worker silent, no return | Check its output/transcript; resume via SendMessage with "reconcile with git status first" |
| Base merged mid-flight under a stacked worker | Tell it to rebase onto main and retarget the PR |

## Common mistakes

- **Building inline "because it's a small fix".** Defeats the skill's entire point (lean orchestrator context, worker-rate tokens). Spawn the builder.
- **Picking the cleanest batch over the highest-priority one.** Coherent + collision-free + one-PR-each is necessary, not sufficient. If a higher-priority ticket or epic is shippable, it leads the group; sorting by shippability instead of priority is how this skill quietly regresses into low-value busywork — the tell is a batch of old Low/Medium tickets while a High epic sits untouched. Validation kills a *wrong* high-priority pick; a low-priority pick was never validated as *best*, only as *easy*.
- **Narrowing the announced group.** Once announced, every unit runs to a terminal state — PR, closed-with-evidence, or halted-with-reason. Silently dropping one is the same scope violation as in /execute.
- **Accepting worker claims unverified.** "You orchestrate + validate" — a green report you didn't check is a hallucination vector.
- **Asking the owner to approve the pick.** They delegated it; announce and go. (Forks still batch-ask, per /execute.)
- **Skipping the collision check** and colliding with a live worktree or another session's in-flight branch.
- **Re-specifying /execute or /briefs content here.** Defer; this skill only owns project entry, group selection, the always-subagent posture, and work validation.
