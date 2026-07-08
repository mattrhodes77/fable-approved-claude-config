---
name: assign
description: Roster-driven ticket discovery + assignment for the team. Use when finding/filing new Linear tickets for a teammate based on their existing workstream (e.g. "find more tickets for bob and alice", "/assign alice bob", "what should carol work on", "give carol a batch"). Keeps each engineer's latest in-progress workstream cached locally (SQLite + roster.json). BACKLOG-FIRST: claims existing in-lane unassigned tickets before minting new ones; only mines their repos for fresh well-constrained candidates when their relevant backlog is dry. Dedups and files/assigns. Supports up to ~15 swappable employees.
---

# assign — find & file tickets for the team

Turns "find more tickets for <people>" into verified, assigned Linear tickets. Roster-driven and reusable: each person's repos, active branches, and themes live in `roster.json`; their live ticket queue is cached in `workstreams.db` so you always work from their *current* workstream, not a guess.

`assign.py` lives next to this file. Run it with the repo python or system python3 (stdlib only, no deps):
`python3 ~/.claude/skills/assign/assign.py <cmd>`

## Entry point: `/assign <names...>`

`/assign alice bob` means **run the full pipeline for Alice and Bob** (fuzzy-matched).

**BACKLOG-FIRST is the rule:** there is almost always a pile of already-filed, unassigned, in-lane tickets. **Claim those before you mint anything new.** Discovery (mining repos for fresh candidates) is the *fallback* — you only run it for a person whose relevant backlog is empty/thin. Don't create new work when unclaimed in-lane work already exists.

1. **Sync** the cache so workstreams are current:
   `python3 ~/.claude/skills/assign/assign.py sync`
   (Add `--seed` to also print team members not yet in the roster.)

2. **Resolve targets + get their pipeline inputs** (repos, branches, dedup list) — these are the inputs for the discovery *fallback* in step 4:
   `python3 ~/.claude/skills/assign/assign.py targets alice bob`
   This prints, per person: the repos to mine + the **active branch to verify against**, their top projects/labels, and the OPEN + recently-shipped titles you must NOT re-file.

3. **BACKLOG-FIRST — claim existing in-lane tickets.** This is the primary source of work:
   `python3 ~/.claude/skills/assign/assign.py backlog alice bob`
   It ranks the team's UNASSIGNED open backlog (Backlog/Todo, no assignee) by each person's **lane fit** — primary-project centrality (their #1 project = full weight) + specific product labels + theme keywords; ubiquitous cross-cutting labels (`platform`/`Bug Fix`/…) are ignored so they don't inflate. For each named person:
   - Review the ranked in-lane candidates and pick the ones that fit their current focus (respect lane fit even when names were given — a ticket in Bob's lane → Bob).
   - Assign each existing ticket via **`assign.py claim ENG-NNNN --assignee alice`** (→ sets assignee, moves Backlog→Todo). `--dry-run` first; `--force` only to reassign an already-owned ticket; `--state keep` to leave its state.
   - A person whose ranked list is healthy is **done here — do NOT run discovery for them.** The `backlog` command prints "fall through to DISCOVERY for this person" when a lane is genuinely empty.

4. **Discovery — the FALLBACK, only for a person whose in-lane backlog is empty/thin** (step 3 said so, or you judge the ranked list too thin/stale for the batch size). Skip this entirely for anyone already filled from step 3. For each such person, run **one read-only Explore/general-purpose agent per repo** (from step 2's repo list; brief them via the `briefs` skill) to mine *well-constrained, verifiable* candidates (each ≈0.5–1 day):
   - TODO/FIXME/stub handlers, no-op buttons (onClick that only toasts/alerts/console.warns), `raise NotImplementedError`, `pass  #`.
   - Crash bugs: unguarded `.map`/index/attr access, `JSON.parse` without try/catch, missing/await-on-sync, bare `except: pass` swallowing real errors.
   - Missing NOT-NULL on inserts, FK/CASCADE gaps, Alembic `create_table` drift.
   - Hardcoded values that should be dynamic (literal branch/id/quota), unwired features, setState-in-effect lint fails.
   - **EXCLUDE**: large refactors, vague polish, and **dead code with zero importers** (unreachable ≠ live bug — note separately, don't file).

5. **Verify every discovery finding on the active branch** — the working tree may sit on a different branch. Per repo, read via the branch ref, never the checkout:
   `git -C <repo> fetch origin --quiet`
   `git -C <repo> --no-pager show <active_branch>:<path>`
   `git -C <repo> grep -n '<pattern>' <active_branch> -- '<glob>'`
   Discard anything that doesn't exist / is already fixed on that branch.

6. **Dedup** discovery candidates against the person's OPEN queue and recently-shipped titles (from step 2) **AND against the backlog surfaced in step 3** — never file a new ticket that duplicates an existing unassigned backlog item (claim that one instead).

7. **TRIAGE new candidates through bulldozer FIRST — don't waste a human on an LLM-doable ticket.** Apply bulldozer's eligibility (see `bulldozer-triage` below) to every surviving discovery candidate and split into two buckets:
   - **Bulldozer-eligible (LLM ships it)** — small + mechanical + well-specified + unblocked + on the active branch: a one-line guard, wire-an-existing-handler, mirror-a-sibling, derive-from-existing-config. → file UNASSIGNED so the `/bulldozer` heartbeat drains it.
   - **Human-needed** — anything that needs design, a build-vs-remove/product decision, judgment about intent, multi-file/cross-cutting work, a migration/CI/prod-op, or domain context the ticket can't fully specify. → step 8.

8. **Match the human bucket to the best human** — `assign.py match "<title>" --repo … --project … --labels …` ranks the roster by repo + project + label + theme overlap (= ability + current workstream + repo). Assign each to the top scorer. When `/assign alice bob` named specific people, prefer them but still respect lane fit (a bug in Bob's lane → Bob even if Alice was named).

9. **Present the routing for approval** — columns: *Backlog-claims (existing, per person)* / *Bulldozer (new)* / *Alice (new)* / *Bob (new)*. Backlog-claim rows = `ENG-NNNN` + title + why-their-lane. New-ticket rows = title + file:line + 1-line fix + priority. Both claiming and filing are mutations; get the go-ahead.

10. **Execute** (all `--dry-run` first):
   - **Claim existing** (step 3 picks): `assign.py claim ENG-NNNN --assignee alice`.
   - **File new human** ticket: `assign.py new --assignee alice --title "[BE] …" --project ProductA --priority 2 --labels "Bug Fix" --body-file BODY.md` (→ Todo, assigned).
   - **File new bulldozer** ticket: `assign.py new --bulldozer --title "[BE] …" --project ProductA --priority 3 --labels "Bug Fix" --body-file BODY.md` (→ Backlog, unassigned).
   New-ticket body = the spec: symptom, file:line evidence (as it exists on the active branch), root cause, concrete fix, reachability. Reply with the claimed + filed Linear URLs grouped by bucket.

11. **(optional) Drain the bulldozer bucket now** — offer to run `/bulldozer <N>` so the LLM-doable tickets ship immediately instead of waiting for the hourly heartbeat. They're already filed unassigned in Backlog, so bulldozer's queue scan finds them.

## bulldozer-triage — the LLM-vs-human discriminator

`/bulldozer` (`~/.claude/commands/bulldozer.md`) is a heartbeat that drains EASY tickets: one fresh subagent per ticket confirms it still stands, fixes it in a worktree, runs PRlaunch, opens a PR. It only picks tickets that are **unassigned or yours, recent, unblocked, well-specified, and small-scope**. Mirror that bar here so the right tickets flow to it.

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

When unsure, prefer human for High-priority / state-integrity / outcome-sensitive work (best-outcome rule), and bulldozer for the clear one-liners. A "wire-it OR remove-it" item is human (the decision is the work) unless you has pre-decided.

## Other commands

- `assign.py backlog <who...> [--min-score 2.5 --top 15 --team Dev]` — **the backlog-first pass.** Ranks the team's unassigned open backlog by each person's lane fit; prints "fall through to DISCOVERY" for a lane with nothing. Raise `--min-score` to tighten, lower it to widen.
- `assign.py claim ENG-NNNN --assignee <who> [--state Todo|keep --priority N --force --dry-run]` — assign an EXISTING backlog ticket to a human (moves Backlog→Todo). The backlog-first counterpart of `new`; refuses an already-assigned ticket unless `--force`.
- `assign.py roster` — show the roster.
- `assign.py profile <who>` — one person's derived workstream: top projects/labels, repos, OPEN queue, recently shipped.
- `assign.py match "<candidate title>" [--repo X --project Y --labels a,b]` — when you have a candidate and don't know whose lane it is, rank the roster by repo+project+label+theme overlap.
- `assign.py sync --seed` — suggest roster rows for Dev-team members not yet listed (paste-ready JSON).

## Adding / swapping employees

Edit `roster.json` (the swappable part). Each member: `email`, `linear_user_id`, `name`, `active`, `repos:[{path, active_branch}]`, `projects[]`, `labels[]`, `themes`. Get `linear_user_id`s from `sync --seed`. `themes`/`projects`/`labels` are augmentation — even a member with empty themes gets a useful auto-derived profile from their synced tickets after one `sync`. Set `active:false` to bench someone without deleting them.

## Conventions & gotchas (locked)

- **Team = Dev**, default state **Todo**, project = the person's product. All one team, so moves never renumber the ticket ids.
- **Branch rules matter**: a repo's working tree often sits on `main` while ACTIVE dev happens on `origin/develop` (verify per repo). The roster's `active_branch` per repo is the source of truth — pass it to every discovery agent.
- Linear `list_issues` for 50+ tickets overflows the MCP token cap and auto-saves to a file — that's why this skill caches via the **GraphQL API** (key from `$LINEAR_API_KEY`, or a JSON file named by `$LINEAR_KEY_FILE`) instead.
- "Deployed" is a started-type state here but means **shipped** — the CLI treats Deployed/Done as shipped for dedup.
- Tell discovery subagents explicitly: **read-only, no git state changes** (no checkout/pull/stash/commit).
- `new`/`claim` resolve project/state/label by NAME from the cache — if it errors "not found", run `sync` (the cache may be stale) or check spelling.
- **Label scope**: some labels are **team-scoped** (e.g. `Bug Fix`/`New Feature`), others are **workspace-scoped** (shared across teams). `sync` fetches BOTH via a dedicated paginated `issueLabels` query — the old nested `team.labels` connection capped at 50 and silently dropped team labels. If the same label name exists in two teams, the sync inserts workspace-scoped first then team-scoped, so the one belonging to your team wins. If a label ever errors "not found", re-run `sync`.
- **Backlog-first, don't over-file**: the default is to `claim` existing in-lane tickets; only fall to discovery when a lane is dry. The unassigned pool can be large (hundreds team-wide) — `backlog`'s lane weighting narrows it to each person's real project(s).
- `claim` takes an **identifier** (`ENG-1896`) and resolves it to the UUID itself; it never creates a ticket, only reassigns/moves one.
