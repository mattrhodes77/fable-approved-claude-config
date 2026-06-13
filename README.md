# MindFortress CC Config

The public [Claude Code](https://claude.com/claude-code) config we run at MindFortress. It covers the full lifecycle of a unit of work:

```
brainstorm  →  build  →  ship (PRlaunch)  →  wrap up
 design          mf-frontend-design   three gates + hook      tracker/memory/report
 before code     for frontend work    before any PR exists
```

The centerpiece is **PRlaunch** — a pre-PR quality pipeline: three local gates (a deep code review, a secondary automated review, and a live outcome eval from the user's seat) run against your working tree *before* a PR is ever opened, and a hook mechanically blocks `gh pr create` until the exact bytes you're shipping have passed every gate.

Built and battle-tested running a multi-repo production shop with Claude Code doing most of the shipping. Every rule in here exists because its absence caused a real incident.

## What's in the box

```
skills/brainstorming/            # 1. BRAINSTORM — design before code           (obra/superpowers, MIT)
skills/writing-plans/            #    …then write the implementation plan        (obra/superpowers, MIT)
skills/executing-plans/          #    …then execute it with review checkpoints   (obra/superpowers, MIT)
skills/mf-frontend-design/       # 2. BUILD — the frontend-design skill every FE surface goes through
skills/test-driven-development/  #    build-stage discipline                     (obra/superpowers, MIT)
skills/systematic-debugging/     #    when something breaks                      (obra/superpowers, MIT)
commands/PRlaunch.md             # 3. SHIP — the pipeline: 7 phases, gate order, disposition rules
commands/deep-review.md          #    Deep Review Process v6.8 — the methodology gate 1 runs
commands/wrapup.md               # 4. WRAP UP — tracker sync, GitHub sync, branch hygiene, memory, cleanup, report
commands/babysit-prs.md          # 5. AFTER — hourly self-arming sweep of open PRs until reviews drain
commands/cleanup.md              #    resolve cleanup debt — deletes the careful hook deferred during loops
hooks/pr-gate.sh                 # enforcement: blocks `gh pr create` without a valid gate marker
hooks/check-careful.sh           # guardrail: plain-English prompt on destructive bash; silent on routine cleanup (loop-mode aware)
hooks/careful-rm.py              # parser behind check-careful: classifies rm -r targets (quote/comment/newline aware)
hooks/cleanup-sweep.py           # helper: read/resolve the deferred-delete cleanup queue (used by cleanup/wrapup/PRlaunch/babysit)
hooks/check-freeze.sh            # guardrail: hard-block edits outside a declared directory boundary
hooks/check-worktree.sh          # guardrail: deny `git commit` in a primary clone — work in worktrees
hooks/loop-mode-arm.sh           # helper: time-box check-careful's loop-mode so unattended /loop runs don't wedge
skills/…                         # + the supporting superpowers set: using-superpowers (skill dispatch),
                                 #   using-git-worktrees, verification-before-completion,
                                 #   subagent-driven-development, finishing-a-development-branch,
                                 #   requesting-code-review, receiving-code-review
```

---

## 1. Brainstorm — design before code, then plan, then execute

[`skills/brainstorming/SKILL.md`](skills/brainstorming/SKILL.md) governs how work *enters* a session. It runs before any creative work: explore context, clarify intent one question at a time, propose 2–3 approaches with trade-offs, present a design, and **get approval before a single line of code** — with a hard gate against "this is too simple to need a design" (simple projects are exactly where unexamined assumptions burn the most work). Designs that go through this gate arrive at PRlaunch with their scope already agreed, which is most of why the review gates come back clean.

Brainstorming doesn't stand alone — it hands off to [`writing-plans`](skills/writing-plans/SKILL.md) (turn the approved design into a step-by-step implementation plan) and [`executing-plans`](skills/executing-plans/SKILL.md) (work the plan with review checkpoints). Every repo we work in has a `docs/superpowers/` directory of dated specs and plans this loop produced. The supporting cast is vendored too: [`using-superpowers`](skills/using-superpowers/SKILL.md) (the skill-dispatch discipline — injected at session start so skills actually fire), [`using-git-worktrees`](skills/using-git-worktrees/SKILL.md) (why our worktree-per-ticket pattern exists — mechanically enforced by [`hooks/check-worktree.sh`](hooks/check-worktree.sh)), [`subagent-driven-development`](skills/subagent-driven-development/SKILL.md), [`finishing-a-development-branch`](skills/finishing-a-development-branch/SKILL.md), and the [`requesting-`](skills/requesting-code-review/SKILL.md)/[`receiving-code-review`](skills/receiving-code-review/SKILL.md) pair.

All of it is vendored verbatim from Jesse Vincent's [superpowers](https://github.com/obra/superpowers) plugin (MIT, license included in each skill directory). **We run the full plugin in production, daily** — this is the actively-used subset, snapshotted so this repo is complete on its own. For the latest versions and the rest of the suite, install the live plugin; upstream keeps evolving and these copies don't auto-update.

## 2. Build — mf-frontend-design

[`skills/mf-frontend-design/SKILL.md`](skills/mf-frontend-design/SKILL.md) is how frontend work actually gets built here — every UI surface goes through it, not as an optional flourish but as the build-stage standard. Tunable VARIANCE/MOTION/DENSITY dials, metric-based typography, color-calibration bans, RSC architecture rules, performance guardrails, and **screenshot-driven verification** baked into the build loop itself. Zero AI slop is the bar.

It's also why PRlaunch's gate 3 works: the skill builds UI to a standard *and verifies it with screenshots as it goes*, so by the time the outcome eval grades what the user receives, it's confirming a discipline that ran during the build — not discovering taste problems for the first time. Merged from Anthropic's `frontend-design` skill and Leonxlnx's `taste-skill` (MIT), with every rule from both preserved.

Build-stage discipline that isn't frontend-specific is superpowers territory: [`test-driven-development`](skills/test-driven-development/SKILL.md) for any feature or bugfix, [`systematic-debugging`](skills/systematic-debugging/SKILL.md) when something breaks (root-cause before fixes, with its reference docs on tracing and defense-in-depth), and [`verification-before-completion`](skills/verification-before-completion/SKILL.md) — evidence before any "it works" claim, which is the same ethos PRlaunch's gates enforce at ship time.

## 3. Ship — PRlaunch

### Why three gates

Each gate catches a bug class the others can't see:

| Gate | Grades | Catches |
|------|--------|---------|
| **1. Deep review** ([deep-review.md](commands/deep-review.md), v6.8) | the **diff** | correctness, security, architecture — "will this overwrite the DB before the user accepts?" |
| **2. Secondary reviewer** (CodeRabbit CLI or similar) | the **repo** | style, nits, patterns |
| **3. Outcome eval** | the **running product, from the user's seat** | the "basic stuff that always gets missed": raw `**markdown**` shown to users, an LLM that asks for context it already has, a spinner that never resolves, a layout broken at the real viewport |

Gate 3 is the one most teams don't have. The failure it targets isn't *not testing* — it's **testing and grading the wrong signal**. It's dangerously easy to watch `POST … 200`, see "a bubble appeared", and write ✅ while the actual words on screen are wrong and the pixels show literal asterisks. The eval forces you to write PASS criteria *before* running anything, phrased as *what the user receives*, then quote the real output for every scenario. Transport (status codes, payloads, "element exists") is necessary, never sufficient.

### The re-gate rule

**Any code change in a later gate invalidates the earlier gates.** A gate's green is only valid for the exact code it ran against. Fixed something during the eval? That fix hasn't been deep-reviewed, linted, or re-tested. The pipeline loops until a full pass over the final committed tree produces zero new changes — and the hook enforces it: the gate marker stores the HEAD sha, so any commit after the gates pass invalidates the marker automatically.

### Deep Review v6.8 highlights

A 10-step review process with empirical validation at its core — *evidence over opinion: a finding without proof is not a finding*. Notable machinery:

- **Mandatory branch alignment** before any analysis (a review of stale code is a review of something that won't exist after merge)
- **Parallel security + quality agents** with calibrated detection prompts: duplicate logic, silent failure paths, unregistered integration points, and (new in v6.8) **structural regressions** — spaghetti-conditional growth, thin wrappers, type-boundary muddying, layer leaks, non-atomic updates
- **Finding origin classification** (IN-SCOPE / ADJACENT / OUT-OF-SCOPE) so PRs are never blocked for pre-existing debt — and pre-existing debt is never silently dropped either
- **Severity recalibration** as a standing function — review agents reliably inflate code-quality nits to HIGH; structural findings default to MEDIUM, never CRITICAL, and a missed simplification opportunity is a suggestion, never a blocker
- **The code-judo question** (v6.8, adapted from Cursor's [thermo-nuclear-code-quality-review](https://github.com/cursor/plugins/blob/main/cursor-team-kit/skills/thermo-nuclear-code-quality-review/SKILL.md) skill): one explicit pass per review asking whether a reframing would *delete* complexity rather than rearrange it
- **Empirical validation**: run the app, curl the endpoints, drive the UI, prove security fixes with before/after against a running instance

## 4. Wrap up

[`commands/wrapup.md`](commands/wrapup.md) is the session-end discipline PRlaunch's phase 6 runs, usable standalone as `/wrapup`: sync every touched ticket to reality, put every PR in the right state, leave zero dirty/unpushed branches, persist what future sessions need to know, commit your config repo, and report it all in one scannable message. The core rule is the same as PRlaunch's: **every finding and follow-up gets a disposition** — filed, fixed, or waived with a reason — never silently dropped.

## 5. After the PR — babysit-prs

Opening a PR isn't the end: automated reviewers post findings on their own schedule, rate limits stall queues, and stacked PRs get skipped by cloud bots entirely. [`commands/babysit-prs.md`](commands/babysit-prs.md) is a **self-arming hourly sweep** of every open PR you've authored across the org: it classifies each PR's review state, applies mechanical reviewer fixes (with a hard rule that behavioral changes must pass the *actual test suite*, not just a syntax check — learned from a one-line fix that parsed clean and shipped a red suite), re-triggers stalled reviews within rate-limit budgets, and runs the reviewer's CLI in the background for stacked PRs the cloud bot refuses — but only when you're not at the keyboard, so it never burns your quota.

The interesting machinery is the **convergence rule**: every sweep fingerprints the queue (PR × state × latest-bot-activity, hashed) and the loop stays armed *as long as the queue is moving* — then auto-stops in exactly two cases: drained (every PR is CLEAN or NEEDS-HUMAN) or stalled (12 frozen sweeps ≈ the bot is down). No runaway polling, no babysitting the babysitter. It never merges; the report ends with a clean-list and a "likely merge" call-out driven by a per-repo policy table you define for your team (ours is redacted — write your own).

---

## Cross-cutting: safety hooks

Three deterministic guardrails. The first two are adapted from [garrytan/gstack](https://github.com/garrytan/gstack) (with a JSON-extraction bugfix — the originals' grep-based parsing missed commands containing escaped quotes, e.g. `psql -c "DROP TABLE …"` — and output modernized to the current `hookSpecificOutput` hook schema); the third is ours:

- **`check-careful.sh`** (+ **`careful-rm.py`**) — gates rare-but-catastrophic bash, and works hard to ask in plain English *only* when it matters. For `rm -r`, every delete target is classified by `careful-rm.py` — a real quote/comment/newline/redirection-aware parser, because a bash word-loop mis-reads all four (it parsed `rm` inside a quoted argument and `#` comments as real deletes, and leaked tokens across newlines — we hit exactly this). If **every** target is routine/regenerable — virtualenvs (incl. suffixed `.venv-*`), build/cache dirs (`node_modules`, `.next`, `dist`, `__pycache__`, `.pytest_cache`, …), local test DBs (`*test*.db`/`-shm`/`-wal`), `/tmp` paths, logs — the command is **allowed silently**. Otherwise the prompt lists each item with ✓ (routine) or ⚠ (please check) and a plain label, so a human can answer at a glance instead of decoding a regex verdict like *"recursive delete of a non-temp, non-build path."* Other gated commands stay narrow with plain-English warnings: true `git push --force` (`--force-with-lease` passes), SQL `DROP`/`TRUNCATE` *via a database client*, `kubectl delete`, `docker rm -f`/`system prune`. The principle: a guardrail that prompts on routine commands — or asks a question you can't answer — trains you to click through, which is worse than no guardrail. Gate only what's rare *and* catastrophic, and explain it.

  **Loop-mode** (for unattended `/loop` or `/goal` runs): a confirmation prompt that no one is there to answer wedges the whole loop. When `~/.claude/hooks/loop-mode` exists and is unexpired, the gate stops wedging — but it doesn't blindly auto-delete either. An unrecognized ⚠ delete is **deferred**: it is *not run*, and is appended as JSON to `~/.claude/cleanup-needed.log` for a later **cleanup sweep**, while the loop continues. (Recognized-safe deletes already pass silently via the classifier, so they never reach this path; non-delete flagged commands auto-proceed.) Deletes are uniquely safe to defer — you can always delete later, never un-delete — so this accrues cleanup as reviewable debt instead of either blocking or silently destroying. The debt is resolved when a human is back: [`hooks/cleanup-sweep.py`](hooks/cleanup-sweep.py) reads the queue, and [`commands/cleanup.md`](commands/cleanup.md) (`/cleanup`) re-runs each deferred delete with you approving per ⚠ item; `wrapup` and `PRlaunch` run the same sweep at their end, and `babysit-prs` (unattended) just surfaces the pending count. Arm loop-mode with [`hooks/loop-mode-arm.sh [minutes]`](hooks/loop-mode-arm.sh) (self-expiring epoch, default 90 min; re-armed each iteration, disarms after the window so a leftover never poisons a later **interactive** session); an empty file (`touch`) arms indefinitely until you `rm` it. `babysit-prs` arms it in Step 0c (skipped on `no-loop`). With the file absent — the default, i.e. at the keyboard — it prompts exactly as before, now in plain English.
- **`check-freeze.sh`** — dormant until you write a directory path to `~/.claude/hooks/freeze-dir.txt`, then **hard-blocks** any Edit/Write outside that boundary. It turns "stay in this repo" from an instruction the agent must remember into a rule the harness enforces. `rm ~/.claude/hooks/freeze-dir.txt` to unfreeze.
- **`check-worktree.sh`** — **denies `git commit` in a primary clone** (`.git` is a directory) and allows it in linked worktrees (`.git` is a gitfile). This is the mechanical enforcement of the worktree-per-ticket pattern that [`using-git-worktrees`](skills/using-git-worktrees/SKILL.md) teaches, and it exists for the same reason every rule here does: humans and agents work the same repos in parallel, and an agent commit in a shared primary clone can be silently clobbered or buried in reflog by a human rebase/amend/branch-rename. We lost commits this way before making it a rule — and the rule still depended on the model remembering it, so now it's a hook. It resolves the repo the commit actually targets (`git -C <path>` first, then the last `cd` in the command, then the session cwd), so compound commands and subagent cwd-drift don't slip past it. Scratch clones under `/tmp` are exempt, and you can exempt a repo deliberately: `echo /path/to/repo >> ~/.claude/hooks/worktree-exempt.txt`. Reading and exploring in a primary clone stays unrestricted — only mutation needs isolation.

## Cross-cutting: how we do memory

Not shippable in this repo (it's wired to our internal platform), but worth describing because it changes what an agent can do across sessions. We replaced Claude Code's native file-based memory (which truncates: first ~200 lines / 25 KB of `MEMORY.md`) with **retrieval-backed memory served over MCP**:

- An **MCP server** exposes two tools — `memory_search` and `memory_write` — backed by a memory service with namespaces for prescriptive rules vs. facts.
- A **UserPromptSubmit hook** runs a relevance query against the store on every prompt and injects the top-ranked memories into context — so the right gotchas, decisions, and runbooks surface *for the task at hand* instead of whatever fit in the first 25 KB.
- A **SessionStart hook** injects standing rules (the prescriptive namespace) at boot.
- **`/wrapup` step 4** is the write path: end of session, dedupe against existing entries, update rather than duplicate, skip anything derivable from the repo.

The effect compounds: deploy gotchas, reviewer false-positive lists, infra quirks, and per-repo policies recorded once get injected exactly when relevant, sessions later. If you build your own, the architecture above is the whole trick — ranked retrieval per prompt beats a static file the moment your memory outgrows the truncation window.

---

## Install

1. Copy the commands into your Claude Code config:

   ```bash
   cp commands/*.md ~/.claude/commands/
   ```

   And the skills:

   ```bash
   mkdir -p ~/.claude/skills
   cp -R skills/* ~/.claude/skills/
   ```

   (Skip the superpowers-derived skills if you already run the [superpowers](https://github.com/obra/superpowers) plugin — they ship there, and the live plugin auto-updates while these snapshots don't. `mf-frontend-design` is ours and only lives here.)

2. (Recommended) Install the hooks:

   ```bash
   mkdir -p ~/.claude/hooks
   cp hooks/*.sh hooks/*.py ~/.claude/hooks/
   chmod +x ~/.claude/hooks/*.sh ~/.claude/hooks/*.py
   ```

   Then add to `~/.claude/settings.json` (merge with any existing hooks):

   ```json
   {
     "hooks": {
       "PreToolUse": [
         {
           "matcher": "Bash",
           "hooks": [
             { "type": "command", "command": "~/.claude/hooks/pr-gate.sh", "timeout": 10 },
             { "type": "command", "command": "~/.claude/hooks/check-careful.sh", "timeout": 10 },
             { "type": "command", "command": "~/.claude/hooks/check-worktree.sh", "timeout": 10 }
           ]
         },
         {
           "matcher": "Edit|Write",
           "hooks": [
             { "type": "command", "command": "~/.claude/hooks/check-freeze.sh", "timeout": 10 }
           ]
         }
       ]
     }
   }
   ```

3. Adapt the stack-specific bits: the secondary reviewer command in PRlaunch phase 2 (we use the CodeRabbit CLI), the tracker references (we use ticket IDs like `XXX-123`), the infra checklists in deep-review Step 5d (written for FastAPI + Alembic + Celery + Docker — keep the categories, swap the specifics), and your team's merge policy in phase 5.

## Use

A full unit of work, end to end:

1. **`Skill(brainstorming)`** fires before any creative work — design agreed, approaches weighed, scope locked.
2. **Build.** Frontend surfaces go through `mf-frontend-design` (the skill self-verifies with screenshots as it builds).
3. **`/PRlaunch`** — the agent identifies the session's shippable units, confirms them with you, then runs each through: deep review → secondary review → outcome eval → re-gate checkpoint → push + PR (ready, not draft, with a Testing section reporting everything that actually ran).
4. **`/wrapup`** closes the loop (PRlaunch runs it as its final phase automatically): tracker synced, branches clean, memory written, one report.

`/deep-review <pr-number>` also works standalone against any existing PR, including paired backend+frontend reviews.

## Design notes

- **Every finding gets a disposition** — fixed, ticketed, or waived-with-reason. "Out of scope" means *track it elsewhere*, never *discard it*. The wrapup phase audits the disposition list.
- **The hook is the enforcement, not the process.** The gate marker is written only after the re-gate checkpoint passes on the final tree. Writing it early to silence the hook defeats the entire mechanism.
- **Gate order matters.** Deep review first (nit fixes would mutate code it just judged), nits second, outcome eval last (it grades the experience, so the code should already be correct and clean — and its findings loop you back through the earlier gates).
- **Structure informs findings; it never gates merges.** v6.8's structural lens deliberately rejects its source material's blocking stance: working code with a missed elegance opportunity ships, with the better shape recorded as a suggestion.

## Influences — and where we differ

Two projects shaped this config enough to deserve more than a credit line. Both are worth studying directly; both take approaches we deliberately didn't.

### Cursor's thermo-nuclear-code-quality-review

[The skill](https://github.com/cursor/plugins/blob/main/cursor-team-kit/skills/thermo-nuclear-code-quality-review/SKILL.md) is a pure structural-maintainability lens: hunt for "code judo" moves (reframings that delete whole branches/layers while preserving behavior), flag files crossing 1,000 lines, treat ad-hoc conditionals bolted onto unrelated flows as design problems, and be suspicious of thin wrappers, casts, and optionality that obscure the real contract.

**What we took:** the entire detection lens. It became Deep Review v6.8's structural regression prompt, the file-size threshold check, and the code-judo question — it covered a genuine blind spot (our reviewers detected duplicate logic, silent failures, and unregistered integration points, but nothing structural).

**Where we differ:** thermo-nuclear treats structural issues as *presumptive blockers* — its approval bar refuses working code that missed a visible simplification, and it pushes the reviewer to "be ambitious about restructuring." We inverted that: structural findings default to MEDIUM, never CRITICAL, and a missed simplification is a recorded suggestion, never a blocker. In a shop optimizing for merge velocity with multiple agents shipping in parallel, a reviewer that restructures ambitiously — or holds working code hostage to elegance — creates more risk than the mess it prevents. Structure informs findings; it never gates merges.

### garrytan/gstack

[gstack](https://github.com/garrytan/gstack) is a complete parallel AI-software-factory: ~80 skills spanning plan→build→review→QA→ship→retro, its own Playwright browser binary, a semantic memory layer (gbrain), event-sourced decision stores, per-question preference hooks, telemetry, and multi-host adapters (Claude Code, Codex, Cursor, …). It's the maximalist take — a whole operating layer installed on top of the agent.

**What we took:** the two most portable guardrails — `check-careful.sh` and `check-freeze.sh` (improved: a JSON-extraction bugfix and the current hook output schema) — and its core enforcement philosophy, which pr-gate.sh applies to shipping: **safety rules belong in deterministic hooks the harness executes, not in instructions the model is asked to remember.** A prompt can be rationalized around; a PreToolUse hook can't.

**Where we differ:** gstack builds its own infrastructure for nearly everything — custom browser, custom memory, custom state directories, custom analytics. We stay harness-native: plain markdown commands and skills in `~/.claude`, standard hooks in `settings.json`, MCP for memory, the stock browser tooling. That keeps every piece independently adoptable (you can take one file from this repo and use it today) and means there's no parallel ecosystem to maintain or upgrade. If you want the integrated-factory experience, gstack is the best version of it; if you want composable pieces on the stock harness, that's this repo.

(A third influence, Jesse Vincent's [superpowers](https://github.com/obra/superpowers), is different in kind: it's not just an influence — we run the full plugin in production alongside this config. Brainstorm → write plan → execute plan is the superpowers loop; this repo picks up where that loop ends, at shipping. The actively-used subset is vendored in `skills/` so this repo stands alone, but the live plugin is the better install — upstream keeps evolving and snapshots don't.)

## Credits

- Deep Review v6.8's structural-quality lens is adapted from Cursor's [thermo-nuclear-code-quality-review](https://github.com/cursor/plugins/blob/main/cursor-team-kit/skills/thermo-nuclear-code-quality-review/SKILL.md) skill (with its approval-blocking stance deliberately softened).
- The careful/freeze safety hooks are adapted from [garrytan/gstack](https://github.com/garrytan/gstack), which also inspired the pr-gate enforcement style.
- The brainstorming, planning, TDD, debugging, worktree, verification, and code-review skills are vendored verbatim from Jesse Vincent's [superpowers](https://github.com/obra/superpowers) (MIT, license included in each directory).
- The frontend-design skill is merged from Anthropic's `frontend-design` skill and Leonxlnx's `taste-skill` (MIT).
- Written with Claude Code, which also runs it.

## License

MIT
