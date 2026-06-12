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
skills/brainstorming/            # 1. BRAINSTORM — design before code (from obra/superpowers, MIT)
skills/mf-frontend-design/       # 2. BUILD — the frontend-design skill every FE surface goes through
commands/PRlaunch.md             # 3. SHIP — the pipeline: 7 phases, gate order, disposition rules
commands/deep-review.md          #    Deep Review Process v6.8 — the methodology gate 1 runs
commands/wrapup.md               # 4. WRAP UP — tracker sync, GitHub sync, branch hygiene, memory, report
hooks/pr-gate.sh                 # enforcement: blocks `gh pr create` without a valid gate marker
hooks/check-careful.sh           # guardrail: confirmation prompt on destructive bash commands
hooks/check-freeze.sh            # guardrail: hard-block edits outside a declared directory boundary
```

---

## 1. Brainstorm — design before code

[`skills/brainstorming/SKILL.md`](skills/brainstorming/SKILL.md) governs how work *enters* a session. It runs before any creative work: explore context, clarify intent one question at a time, propose 2–3 approaches with trade-offs, present a design, and **get approval before a single line of code** — with a hard gate against "this is too simple to need a design" (simple projects are exactly where unexamined assumptions burn the most work). Designs that go through this gate arrive at PRlaunch with their scope already agreed, which is most of why the review gates come back clean.

Vendored verbatim from Jesse Vincent's [superpowers](https://github.com/obra/superpowers) plugin (MIT, license included alongside). If you want the whole methodology suite — TDD, systematic debugging, plan writing/execution — install the full plugin; this copy is for people who want the single highest-leverage skill without adopting the rest.

## 2. Build — mf-frontend-design

[`skills/mf-frontend-design/SKILL.md`](skills/mf-frontend-design/SKILL.md) is how frontend work actually gets built here — every UI surface goes through it, not as an optional flourish but as the build-stage standard. Tunable VARIANCE/MOTION/DENSITY dials, metric-based typography, color-calibration bans, RSC architecture rules, performance guardrails, and **screenshot-driven verification** baked into the build loop itself. Zero AI slop is the bar.

It's also why PRlaunch's gate 3 works: the skill builds UI to a standard *and verifies it with screenshots as it goes*, so by the time the outcome eval grades what the user receives, it's confirming a discipline that ran during the build — not discovering taste problems for the first time. Merged from Anthropic's `frontend-design` skill and Leonxlnx's `taste-skill` (MIT), with every rule from both preserved.

(Backend work has no equivalent skill in this repo — its build-stage discipline lives in the deep-review checklists that gate it on the way out.)

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

---

## Cross-cutting: safety hooks

Two deterministic guardrails adapted from [garrytan/gstack](https://github.com/garrytan/gstack) (with a JSON-extraction bugfix — the originals' grep-based parsing missed commands containing escaped quotes, e.g. `psql -c "DROP TABLE …"` — and output modernized to the current `hookSpecificOutput` hook schema):

- **`check-careful.sh`** — forces a confirmation prompt on destructive bash: `rm -rf`, SQL `DROP`/`TRUNCATE`, `git push --force`, `git reset --hard`, `git checkout/restore .`, `kubectl delete`, `docker rm -f`/`system prune`. Build-artifact deletes (`node_modules`, `.next`, `dist`, `__pycache__`, …) pass silently. Especially worth having if you run autonomous loops.
- **`check-freeze.sh`** — dormant until you write a directory path to `~/.claude/hooks/freeze-dir.txt`, then **hard-blocks** any Edit/Write outside that boundary. It turns "stay in this repo" from an instruction the agent must remember into a rule the harness enforces. `rm ~/.claude/hooks/freeze-dir.txt` to unfreeze.

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
   cp commands/PRlaunch.md commands/deep-review.md commands/wrapup.md ~/.claude/commands/
   ```

   And the skills:

   ```bash
   mkdir -p ~/.claude/skills
   cp -R skills/mf-frontend-design skills/brainstorming ~/.claude/skills/
   ```

   (Skip `brainstorming` if you already run the [superpowers](https://github.com/obra/superpowers) plugin — it ships there.)

2. (Recommended) Install the hooks:

   ```bash
   mkdir -p ~/.claude/hooks
   cp hooks/*.sh ~/.claude/hooks/
   chmod +x ~/.claude/hooks/*.sh
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
             { "type": "command", "command": "~/.claude/hooks/check-careful.sh", "timeout": 10 }
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

## Credits

- Deep Review v6.8's structural-quality lens is adapted from Cursor's [thermo-nuclear-code-quality-review](https://github.com/cursor/plugins/blob/main/cursor-team-kit/skills/thermo-nuclear-code-quality-review/SKILL.md) skill (with its approval-blocking stance deliberately softened).
- The careful/freeze safety hooks are adapted from [garrytan/gstack](https://github.com/garrytan/gstack), which also inspired the pr-gate enforcement style.
- The brainstorming skill is vendored verbatim from Jesse Vincent's [superpowers](https://github.com/obra/superpowers) (MIT).
- The frontend-design skill is merged from Anthropic's `frontend-design` skill and Leonxlnx's `taste-skill` (MIT).
- Written with Claude Code, which also runs it.

## License

MIT
