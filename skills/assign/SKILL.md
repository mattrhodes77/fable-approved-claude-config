---
name: assign
description: Roster-driven ticket discovery + assignment for the team. Use when finding/filing new Linear tickets for a teammate based on their existing workstream (e.g. "find more tickets for ian and john", "/assign john ian", "what should bill work on", "give jesus a batch"). Keeps each engineer's latest in-progress workstream cached locally (SQLite + roster.json), mines their repos for well-constrained candidates, dedups, and files assigned tickets. Supports up to ~15 swappable employees.
---

# assign — find & file tickets for the team

Turns "find more tickets for <people>" into verified, assigned Linear tickets. Roster-driven and reusable: each person's repos, active branches, and themes live in `roster.json`; their live ticket queue is cached in `workstreams.db` so you always work from their *current* workstream, not a guess.

`assign.py` lives next to this file. Run it with the repo python or system python3 (stdlib only, no deps):
`python3 ~/.claude/skills/assign/assign.py <cmd>`

## Entry point: `/assign <names...>`

`/assign john ian` means **run the full pipeline for John and Ian** (fuzzy-matched). Steps:

1. **Sync** the cache so workstreams are current:
   `python3 ~/.claude/skills/assign/assign.py sync`
   (Add `--seed` to also print Dev-team members not yet in the roster.)

2. **Resolve targets + get their pipeline inputs** (repos, branches, dedup list):
   `python3 ~/.claude/skills/assign/assign.py targets john ian`
   This prints, per person: the repos to mine + the **active branch to verify against**, their top projects/labels, and the OPEN + recently-shipped titles you must NOT re-file.

3. **Discovery pass — one read-only Explore/general-purpose agent per repo.** Mine for *well-constrained, verifiable* candidates (each ≈0.5–1 day):
   - TODO/FIXME/stub handlers, no-op buttons (onClick that only toasts/alerts/console.warns), `raise NotImplementedError`, `pass  #`.
   - Crash bugs: unguarded `.map`/index/attr access, `JSON.parse` without try/catch, missing/await-on-sync, bare `except: pass` swallowing real errors.
   - Missing NOT-NULL on inserts, FK/CASCADE gaps, Alembic `create_table` drift.
   - Hardcoded values that should be dynamic (literal branch/id/quota), unwired features, setState-in-effect lint fails.
   - **EXCLUDE**: large refactors, vague polish, and **dead code with zero importers** (unreachable ≠ live bug — note separately, don't file).

4. **Verify every finding on the active branch** — the working tree may sit on a different branch. Per repo, read via the branch ref, never the checkout:
   `git -C <repo> fetch origin --quiet`
   `git -C <repo> --no-pager show <active_branch>:<path>`
   `git -C <repo> grep -n '<pattern>' <active_branch> -- '<glob>'`
   Discard anything that doesn't exist / is already fixed on that branch.

5. **Dedup** against the person's OPEN queue and recently-shipped titles (from step 2). Don't refile shipped work or open dupes.

6. **TRIAGE through bulldozer FIRST — don't waste a human on an LLM-doable ticket.** Apply bulldozer's eligibility (see `bulldozer-triage` below) to every surviving candidate and split into two buckets:
   - **Bulldozer-eligible (LLM ships it)** — small + mechanical + well-specified + unblocked + on the active branch: a one-line guard, wire-an-existing-handler, mirror-a-sibling, derive-from-existing-config. → file UNASSIGNED so the `/bulldozer` heartbeat drains it.
   - **Human-needed** — anything that needs design, a build-vs-remove/product decision, judgment about intent, multi-file/cross-cutting work, a migration/CI/prod-op, or domain context the ticket can't fully specify. → step 7.

7. **Match the human bucket to the best human** — `assign.py match "<title>" --repo … --project … --labels …` ranks the roster by repo + project + label + theme overlap (= ability + current workstream + repo). Assign each to the top scorer. When `/assign john ian` named specific people, prefer them but still respect lane fit (a Studio bug → Ian even if John was named).

8. **Present the routing to Matt for approval** — three columns: *Bulldozer (N)* / *John (M)* / *Ian (K)*, each row = title + file:line + 1-line fix + priority. Filing is a mutation; get the go-ahead.

9. **File**, one per candidate, via the CLI (auto-resolves team/state/project/label IDs from the cache). Use `--dry-run` first.
   - Human:  `assign.py new --assignee john --title "[BE] …" --project Freya --priority 2 --labels "Bug Fix" --body-file BODY.md`   (→ Todo, assigned)
   - Bulldozer: `assign.py new --bulldozer --title "[BE] …" --project Freya --priority 3 --labels "Bug Fix" --body-file BODY.md`   (→ Backlog, unassigned)
   Body = the ticket spec: symptom, file:line evidence (as it exists on the active branch), root cause, concrete fix, reachability. Reply to Matt with the filed Linear URLs grouped by bucket.

10. **(optional) Drain the bulldozer bucket now** — offer to run `/bulldozer <N>` so the LLM-doable tickets ship immediately instead of waiting for the hourly heartbeat. They're already filed unassigned in Backlog, so bulldozer's queue scan finds them.

## bulldozer-triage — the LLM-vs-human discriminator

`/bulldozer` (`~/.claude/commands/bulldozer.md`) is a heartbeat that drains EASY tickets: one fresh subagent per ticket confirms it still stands, fixes it in a worktree, runs PRlaunch, opens a PR. It only picks tickets that are **unassigned or Matt's, recent, unblocked, well-specified, and small-scope**. Mirror that bar here so the right tickets flow to it.

**Bulldozer-eligible (file `--bulldozer`, unassigned)** — ALL must hold:
- **Small + mechanical**: single-function / one-file / a guard (`x?.y`) / wiring an *existing* handler or hook / mirroring a sibling / deriving from existing config. The discovery already names the exact fix.
- **Well-specified**: exact file:line + an unambiguous fix (no "design a …", no "decide whether to …").
- **Unblocked**, on the **active/default branch**, no product/UX decision, no new feature to invent, verifiable by a subagent.

**Human-needed (→ `match` → `--assignee`)** — ANY of these:
- Needs **design** or a **build-vs-remove / product decision** ("implement a comparison view OR remove the button" — someone must choose).
- **Feature-completion**, not a wiring fix (e.g. "implement scene splitting", "inject + implement a real AI call").
- **Multi-file / cross-cutting / architectural**, or needs **judgment about intent** (is this stopgap deliberate?).
- A **migration / CI-workflow / prod-op** (bulldozer explicitly excludes these).
- Needs **domain context** the ticket can't fully carry.

When unsure, prefer human for High-priority / state-integrity / outcome-sensitive work (best-outcome rule), and bulldozer for the clear one-liners. A "wire-it OR remove-it" item is human (the decision is the work) unless Matt has pre-decided.

## Other commands

- `assign.py roster` — show the roster.
- `assign.py profile <who>` — one person's derived workstream: top projects/labels, repos, OPEN queue, recently shipped.
- `assign.py match "<candidate title>" [--repo X --project Y --labels a,b]` — when you have a candidate and don't know whose lane it is, rank the roster by repo+project+label+theme overlap.
- `assign.py sync --seed` — suggest roster rows for Dev-team members not yet listed (paste-ready JSON).

## Adding / swapping employees

Edit `roster.json` (the swappable part). Each member: `email`, `linear_user_id`, `name`, `active`, `repos:[{path, active_branch}]`, `projects[]`, `labels[]`, `themes`. Get `linear_user_id`s from `sync --seed`. `themes`/`projects`/`labels` are augmentation — even a member with empty themes gets a useful auto-derived profile from their synced tickets after one `sync`. Set `active:false` to bench someone without deleting them.

## Conventions & gotchas (locked)

- **Team = Dev**, default state **Todo**, project = the person's product (Freya / Reeve Studio / Reeve Substrate / …). All Dev-team, so moves never renumber `DEV-NNN`.
- **Branch rules matter**: `writing-partner-backend` working tree often sits on `main` but ACTIVE dev = `origin/develop` (verify there). `writing-partner-frontend` = `origin/develop`. `reeve-frontend` / `reeve-services` = `origin/main`. The roster's `active_branch` per repo is the source of truth — pass it to every discovery agent.
- Linear `list_issues` for 50+ tickets overflows the MCP token cap and auto-saves to a file — that's why this skill caches via the **GraphQL API** (key in `~/.reeve/reeve.json` env.LINEAR_API_KEY) instead.
- "Deployed" is a started-type state here but means **shipped** — the CLI treats Deployed/Done as shipped for dedup.
- Tell discovery subagents explicitly: **read-only, no git state changes** (no checkout/pull/stash/commit).
- `new` resolves project/state/label by NAME from the cache — if it errors "not found", run `sync` (the cache may be stale) or check spelling.
