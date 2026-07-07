# skillify taxonomy — the per-repo skill reference sheet

Wire this to YOUR ecosystem's realities (the bracketed examples show the level of specificity to aim for). For product repos: **merge thin topics, split deep ones, target 8–12 skills.** `<repo>` = the resolved repo name (e.g. `acme-api`).

## Per-skill REQUIRED sections (checklist — every authored skill)

1. **Purpose** — the one job this skill does.
2. **When NOT to use + sibling pointer** — and which skill to load instead.
3. **Source-of-truth files** — the file:line(s) this skill is derived from.
4. **Procedure** — imperative, ordered steps.
5. **Commands with expected observations** — each command + what a success looks like.
6. **Decision gates** — the "if X then Y else Z" forks a reader will hit.
7. **Known traps** — the gotchas that bite (from memory/tracker/git).
8. **Evidence of success** — how the reader proves the outcome is real.
9. **Provenance and maintenance** — date stamp + one-line re-verification command(s).

## CORE skills (product repos)

### `<repo>-architecture-contract`
- Domain, state model, actors, ownership boundaries — what the system IS and what invariants MUST NOT break.
- The core workflow end-to-end (request → state → output), with the load-bearing files.
- The 3–5 "if you break this, prod breaks" contracts.

### `<repo>-change-control`
- Branch naming (e.g. `me/<prefix>-NNNN-slug` — a ticket-token branch flips the tracker ticket to In Progress via the `linear-startwork` hook) + the tracker's canonical branch names.
- **PR base branch per repo**: state the repo's actual base (some repos merge to `main`; others flow `feature→develop→main`). Say which one THIS repo uses.
- **Merge lanes / who merges — never self-merge.** Note any team carve-outs and protected-branch rules.

### `<repo>-build-and-env`
- Worktree recipe + `.env` copy from the primary checkout (never commit secrets).
- Canonical `.venv` / `node_modules` handling: [example: FE — never symlink `node_modules` if your bundler rejects a symlinked module dir; use `cp -R` from the primary or a fresh install. BE — reuse the main-checkout `.venv` path rather than rebuilding per worktree.]
- Exact bootstrap invocations + their expected first-run output.

### `<repo>-test-and-validate`
- Exact test invocations incl. env [example: a service that runs tests with `DATABASE_URL=sqlite`; plugin/conftest opt-outs when a test plugin gets in the way].
- **What CI actually gates vs doesn't** [example: a repo with NO test gate in CI; a linter that runs but is non-gating]. Say what a green check really proves.
- Each command + expected pass line (`NNN passed in Ns`), and the fastest pre-PR smoke subset.

### `<repo>-run-and-operate`
- The repo's deploy model (from SKILL.md's per-repo table) + the prod-verification recipe (merged ≠ live for manual-deploy repos; box-ancestor check).
- How to confirm a change is actually live (`/health` git_sha, `vercel ls --prod`, box HEAD ancestor).
- Restart / rollback path and who's allowed to run it.

### `<repo>-debugging-playbook`
- Symptom → triage table (top recurring failures → first probe → likely cause).
- Where the logs / traces / dashboards are and the exact command to tail them.
- The fastest repro for each common failure class.

### `<repo>-failure-archaeology`
- Settled battles mined from the tracker + memory + git: **symptom → root cause → evidence (commit/ticket) → status (fixed/reverted/won't-fix).**
- The reverts and "we tried X, it broke Y" that a weaker model would otherwise re-attempt.
- Each entry cites its evidence; nothing here is inferred without a label.

### `<repo>-config-and-flags`
- Feature flags, env vars, and config knobs that change behavior — name, default, effect, where read.
- Which flags are prod-live vs dev-only; the blast radius of flipping each.
- Secrets locations (names only, never values) and how they're injected.

## OPTIONAL — research-repo tier (usually N/A for product repos)

Include ONLY for a research/experiment repo; mark N/A and skip for product repos.

- **`<repo>-research-frontier`** — open questions, hypotheses in flight, what's been ruled out.
- **`<repo>-proof-and-analysis`** — how results/metrics are computed and validated; how to reproduce a figure.
- **`<repo>-methodology`** — the experimental protocol, datasets/fixtures, and what makes a run valid.

## Merge / split guidance

- **Merge** two candidates when they share one source-of-truth file and one reader would load both together (e.g. tiny config + flags → one skill).
- **Split** when a skill would exceed a single scannable procedure or mixes two distinct reader jobs (e.g. build vs test → two skills).
- A skill exists because a reader has a **task** ("get tests green", "prove it's live"), never because a file exists. Name it for the task.
