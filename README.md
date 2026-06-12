# MindFortress CC Config

The public [Claude Code](https://claude.com/claude-code) config we run at MindFortress. The centerpiece is **PRlaunch** — a pre-PR quality pipeline: three local gates (a deep code review, a secondary automated review, and a live outcome eval from the user's seat) run against your working tree *before* a PR is ever opened, and a hook mechanically blocks `gh pr create` until the exact bytes you're shipping have passed every gate.

Built and battle-tested running a multi-repo production shop with Claude Code doing most of the shipping. Every rule in here exists because its absence caused a real incident.

## Why three gates

Each gate catches a bug class the others can't see:

| Gate | Grades | Catches |
|------|--------|---------|
| **1. Deep review** ([deep-review.md](commands/deep-review.md), v6.8) | the **diff** | correctness, security, architecture — "will this overwrite the DB before the user accepts?" |
| **2. Secondary reviewer** (CodeRabbit CLI or similar) | the **repo** | style, nits, patterns |
| **3. Outcome eval** | the **running product, from the user's seat** | the "basic stuff that always gets missed": raw `**markdown**` shown to users, an LLM that asks for context it already has, a spinner that never resolves, a layout broken at the real viewport |

Gate 3 is the one most teams don't have. The failure it targets isn't *not testing* — it's **testing and grading the wrong signal**. It's dangerously easy to watch `POST … 200`, see "a bubble appeared", and write ✅ while the actual words on screen are wrong and the pixels show literal asterisks. The eval forces you to write PASS criteria *before* running anything, phrased as *what the user receives*, then quote the real output for every scenario. Transport (status codes, payloads, "element exists") is necessary, never sufficient.

## The re-gate rule

**Any code change in a later gate invalidates the earlier gates.** A gate's green is only valid for the exact code it ran against. Fixed something during the eval? That fix hasn't been deep-reviewed, linted, or re-tested. The pipeline loops until a full pass over the final committed tree produces zero new changes — and the hook enforces it: the gate marker stores the HEAD sha, so any commit after the gates pass invalidates the marker automatically.

## What's in the box

```
commands/PRlaunch.md             # the pipeline — 7 phases, gate order, disposition rules, guardrails
commands/deep-review.md          # Deep Review Process v6.8 — the full review methodology gate 1 runs
hooks/pr-gate.sh                 # PreToolUse hook that blocks `gh pr create` without a valid gate marker
skills/mf-frontend-design/       # bonus: the frontend-design skill that feeds gate 3's visual pass
```

### Deep Review v6.8 highlights

A 10-step review process with empirical validation at its core — *evidence over opinion: a finding without proof is not a finding*. Notable machinery:

- **Mandatory branch alignment** before any analysis (a review of stale code is a review of something that won't exist after merge)
- **Parallel security + quality agents** with calibrated detection prompts: duplicate logic, silent failure paths, unregistered integration points, and (new in v6.8) **structural regressions** — spaghetti-conditional growth, thin wrappers, type-boundary muddying, layer leaks, non-atomic updates
- **Finding origin classification** (IN-SCOPE / ADJACENT / OUT-OF-SCOPE) so PRs are never blocked for pre-existing debt — and pre-existing debt is never silently dropped either
- **Severity recalibration** as a standing function — review agents reliably inflate code-quality nits to HIGH; structural findings default to MEDIUM, never CRITICAL, and a missed simplification opportunity is a suggestion, never a blocker
- **The code-judo question** (v6.8, adapted from Cursor's [thermo-nuclear-code-quality-review](https://github.com/cursor/plugins/blob/main/cursor-team-kit/skills/thermo-nuclear-code-quality-review/SKILL.md) skill): one explicit pass per review asking whether a reframing would *delete* complexity rather than rearrange it
- **Empirical validation**: run the app, curl the endpoints, drive the UI, prove security fixes with before/after against a running instance

### Bonus: mf-frontend-design

[`skills/mf-frontend-design/SKILL.md`](skills/mf-frontend-design/SKILL.md) is the frontend-design skill we pair with this pipeline — tunable VARIANCE/MOTION/DENSITY dials, metric-based typography, color-calibration bans, RSC architecture rules, performance guardrails, and **screenshot-driven verification**. It's the natural companion to gate 3's visual pass: the skill makes the UI worth shipping; the eval proves a user actually receives it. Merged from Anthropic's `frontend-design` skill and Leonxlnx's `taste-skill` (MIT), with every rule from both preserved.

## Install

1. Copy the commands into your Claude Code config:

   ```bash
   cp commands/PRlaunch.md commands/deep-review.md ~/.claude/commands/
   ```

   And the skill (optional, for frontend work):

   ```bash
   mkdir -p ~/.claude/skills/mf-frontend-design
   cp skills/mf-frontend-design/SKILL.md ~/.claude/skills/mf-frontend-design/
   ```

2. (Recommended) Install the enforcement hook:

   ```bash
   mkdir -p ~/.claude/hooks
   cp hooks/pr-gate.sh ~/.claude/hooks/
   chmod +x ~/.claude/hooks/pr-gate.sh
   ```

   Then add to `~/.claude/settings.json` (merge with any existing hooks):

   ```json
   {
     "hooks": {
       "PreToolUse": [
         {
           "matcher": "Bash",
           "hooks": [
             { "type": "command", "command": "~/.claude/hooks/pr-gate.sh", "timeout": 10 }
           ]
         }
       ]
     }
   }
   ```

3. Adapt the stack-specific bits: the secondary reviewer command in PRlaunch phase 2 (we use the CodeRabbit CLI), the tracker references (we use ticket IDs like `XXX-123`), the infra checklists in deep-review Step 5d (written for FastAPI + Alembic + Celery + Docker — keep the categories, swap the specifics), and your team's merge policy in phase 5.

## Use

In a Claude Code session where you've built something:

```
/PRlaunch
```

The agent identifies the session's shippable units, confirms them with you, then runs each through: deep review → secondary review → outcome eval → re-gate checkpoint → push + PR (ready, not draft, with a Testing section reporting everything that actually ran) → wrapup with a full disposition report.

`/deep-review <pr-number>` also works standalone against any existing PR, including paired backend+frontend reviews.

## Design notes

- **Every finding gets a disposition** — fixed, ticketed, or waived-with-reason. "Out of scope" means *track it elsewhere*, never *discard it*. The wrapup phase audits the disposition list.
- **The hook is the enforcement, not the process.** The gate marker is written only after the re-gate checkpoint passes on the final tree. Writing it early to silence the hook defeats the entire mechanism.
- **Gate order matters.** Deep review first (nit fixes would mutate code it just judged), nits second, outcome eval last (it grades the experience, so the code should already be correct and clean — and its findings loop you back through the earlier gates).
- **Structure informs findings; it never gates merges.** v6.8's structural lens deliberately rejects its source material's blocking stance: working code with a missed elegance opportunity ships, with the better shape recorded as a suggestion.

## Credits

- Deep Review v6.8's structural-quality lens is adapted from Cursor's [thermo-nuclear-code-quality-review](https://github.com/cursor/plugins/blob/main/cursor-team-kit/skills/thermo-nuclear-code-quality-review/SKILL.md) skill (with its approval-blocking stance deliberately softened).
- The careful/freeze safety-hook pattern that inspired the pr-gate enforcement style comes from [garrytan/gstack](https://github.com/garrytan/gstack).
- Written with Claude Code, which also runs it.

## License

MIT
