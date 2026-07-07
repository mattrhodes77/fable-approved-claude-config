---
name: briefs
description: Use when writing ANY subagent/worker prompt from an orchestrator skill (bulldozer ticket workers, deep-review finder/refuter agents, babysit fixers, assign discovery agents, skillify authors) — the six-section brief contract every worker prompt must contain.
---

# briefs — the worker-brief contract for subagent prompts

**Worker quality is downstream of brief quality.** A fresh subagent knows nothing but its prompt: no session history, no working-tree state, no sense of what "the checkout" means. An ad-hoc brief varies run-to-run — one worker gets the branch, the next assumes `main`; one gets a return schema, the next free-writes prose the orchestrator can't parse. This skill fixes the shape: **every worker prompt an orchestrator emits carries the same six sections, in order, filled.** The structure converts judgment a strong model applies implicitly into fill-in slots a weaker worker can't silently skip.

Use this any time an orchestrator skill spawns a worker: `bulldozer` ticket workers, `deep-review` finder/refuter agents, `babysit-prs` fixers, `assign` discovery agents, `skillify` authors — or any new orchestrator that fans out.

## The six required sections

Every brief MUST contain all six, each labeled, in this order. A section that "doesn't apply" is a smell — say why explicitly rather than dropping it.

### 1. CONTEXT
State the repo, the **exact branch or commit ref the worker must trust** — never "the checkout", never "the current tree" (the worker has no shared filesystem view and the tree may be mid-edit by someone else). Give 2–3 sentences of situation: what's already been done, why this worker exists, what surrounds the task. When the worker must NOT trust working-tree state — because another agent owns those files, or the ref is a moving target — inline the actual file contents or a diff into the brief so the worker reads from the prompt, not from disk.

### 2. TASK
One outcome, stated as **the artifact to produce** — "return a JSON verdict on whether EX-3803 still reproduces", "open a PR fixing the null-guard in `api/x.py`". Not a topic ("look into the bug"), not a bundle of three things. If you're tempted to write "and also", it's two briefs.

### 3. CONSTRAINTS
The scope fences that keep the worker in its lane: never merge, never reassign a ticket, read-only, path allowlist, don't touch files another agent owns. Then paste the relevant **gotchas verbatim** — the zsh word-split trap, the "list_issues blows the token cap" warning, whatever bit the last run. Verbatim, because a paraphrased gotcha is a gotcha the worker will re-learn the hard way.

### 4. RETURN CONTRACT
The exact schema/format of the reply, **with a filled example**. Prefer JSON with named keys — the orchestrator parses it, so ambiguity here becomes a parse failure or a wrong write. Say what each key means and what an empty/failure value looks like. If the reply is prose, give the exact headings expected.

### 5. VERIFICATION REQUIREMENT
What the worker must **RUN** before claiming success — the test command, the build, the `git show` that proves the change landed, the reproduction that now passes. Evidence before assertion (global rule #1). Make explicit: **if the outcome can't be verified, the worker reports failure honestly** rather than asserting success it didn't observe.

### 6. STOP CONDITIONS
When to bail instead of thrashing, and **what to return when it bails** so the orchestrator can act. Mirror the global 3-strikes / two-dead-ends rule: after ~2 failed attempts at the same fact or fix, stop and report the blocker with what was tried — don't burn the budget grinding. Name the specific dead-ends for this task (premise contradicted, ref missing, tests already red on checkout) and the shape of the "I stopped" return.

## Worked example — a bulldozer-style ticket worker

```
CONTEXT: Repo is acme-api (a fictional repo for this example). Trust ONLY
origin/main at commit a1b2c3d (fetched this session) — do NOT read the live
working tree; another worker is editing it. The ticket EX-0000 claims
`send_reminder()` in api/notify.py double-sends when a user has two active
bookings. You are one isolated worker; the orchestrator is draining a queue
and will not see your context, only your returned JSON.

TASK: Produce an opened PR that makes `send_reminder()` send exactly one
reminder per user per booking-window, with a regression test that fails on
a1b2c3d and passes on your fix.

CONSTRAINTS:
- Work in an isolated worktree on branch me/ex-0000-dedupe-reminders. Never
  commit to main, never merge, never touch the ticket's Linear state yourself.
- Path allowlist: api/notify.py and tests/test_notify.py only.
- Gotcha (verbatim): pytest here needs `ENV=test` or it hits prod Postgres —
  `ENV=test uv run pytest tests/test_notify.py`.

RETURN CONTRACT: reply with exactly this JSON, no prose around it:
{
  "ticket": "EX-2991",
  "status": "pr_opened" | "blocked",
  "pr_url": "<url or null>",
  "test_cmd": "<exact command you ran>",
  "test_result": "<pass/fail summary you observed>",
  "notes": "<one line, or the blocker if status=blocked>"
}

VERIFICATION REQUIREMENT: before returning status "pr_opened" you MUST run the
regression test and observe it fail on a1b2c3d, then pass on your branch. Paste
the observed pass line into test_result. If you cannot make it fail-then-pass,
return status "blocked" — do not claim a fix you didn't watch work.

STOP CONDITIONS: if the double-send does NOT reproduce on a1b2c3d after 2
honest attempts (the premise is wrong), or the allowlisted files don't contain
`send_reminder`, stop and return status "blocked" with notes naming what you
found instead. Do not widen the path allowlist to go hunting.
```

## Adopters

These orchestrator skills each spawn workers and must reference this template when they build a worker prompt: **bulldozer** (per-ticket workers), **deep-review** (finder/refuter agents), **babysit-prs** (CodeRabbit-fix workers), **assign** (discovery agents), **skillify** (skill-author workers). Their edits to point at this contract land separately — this file is the canonical source they cite, not a change to those skills.
