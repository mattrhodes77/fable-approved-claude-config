---
description: Standardized PR review with empirical validation (Deep Review Process v6.8)
argument-hint: <pr-url-or-number> [paired-pr-url-or-number]
---

# Deep Review Process v6.8

Standardized process for PR reviews with empirical validation. Calibrated over dozens of real reviews; the case references (PR #66, #101, #113/#80, …) are the actual incidents that produced each rule.

**v6.7 → v6.8:** structural-quality lens, adapted from Cursor's `thermo-nuclear-code-quality-review` skill. Adds: file-size threshold check (Step 1), structural regression detection prompt (Step 3), structural severity calibration + the code-judo question (Step 4), anti-nit-flooding publish rule (Step 8). Core stance: **structure informs findings; it never gates merges.** A missed simplification opportunity is a suggestion, not a blocker.

**Terminology:** "LT" = the lead/tech reviewer running this process (you, or the agent acting for you). Target branch examples use `develop`; substitute `main` if that's your trunk. The infra checklists reflect one real stack (FastAPI + Alembic + Celery + Docker + Vercel/Railway + Auth0/Stripe) — adapt the specifics to yours; the *categories* are the point.

**Arguments:** `$ARGUMENTS` — one or two PR identifiers (links or numbers). If two are given, treat as a paired multi-repo review (backend + frontend).

---

## 0. Input

Receive PR link(s). Identify if there are related PRs (e.g., backend + frontend for the same feature).

### Determine review iteration

| Iteration | Trigger | Scope |
| -- | -- | -- |
| **First review** | New PR or first time reviewing | Full process (steps 1-9) |
| **Re-review (Nth)** | Owner pushed fixes after previous review | Focused process (see below) |

### Re-review process (2nd+ iteration)

1. **Load prior context** — read the previous review document (`DEEP_REVIEW_*.md`) and GitHub review comments
2. **Diff since last review** — only analyze commits pushed after the last review (`git log --since="<last review date>"` or compare commit SHAs)
3. **Verify HIGH+ fixes** — for each HIGH/CRITICAL finding from the previous round:
   * Is the fix correct and complete?
   * Does it introduce new issues?
   * Mark as: RESOLVED / PARTIALLY RESOLVED / NOT ADDRESSED
4. **Spot-check MEDIUM fixes** — verify a sample, not necessarily all
5. **Scan for regressions** — new code in the fix commits could introduce new problems; run a quick security + quality pass on the delta only
   * **New findings require empirical validation** — if the delta introduces new functionality (scope expansion), any new finding must be validated with grep/code trace, not just observed from the diff. The re-review shortcut does not exempt from evidence standards.
6. **Re-validate runtime** if the fix touches critical paths (payments, auth, data integrity)
7. **Update the review document** — add a "Re-review" section with date, findings status, and new findings if any
8. **Post follow-up on GitHub** — concise comment referencing the original review, marking which findings are resolved
9. **Approve or request another round** — if all HIGH+ resolved, approve; otherwise request changes again

### Key differences from first review

* **No full agent sweep** — only run agents on the delta (new commits), not the entire PR
* **No full triage** — depth classification carries over from the first review
* **Faster** — focus is verification, not discovery
* **Document continuity** — append to the existing review document, don't create a new one

---

## 1. Triage

### Gather context

* Read: title, description, **existing comments/reviews**, **CI/checks status**
* Check files changed, additions/deletions, commit count
* Check merge state: `gh pr view <N> --json mergeable,mergeStateStatus`

### CI / Checks status

* If CI is **red from the PR's own code** → stop, owner must fix before review proceeds
* If CI is **red from merged code** (e.g., develop merge brought in another PR's bug):
  1. **Investigate first**: who introduced it? Is there already a PR fixing it? How recent is the merge?
  2. Only after understanding the context, note in review. LT may fix directly if trivial (see Step 10)
  3. **Never fix blindly** — the fix may already exist in develop or in another open PR
* If CI is **red from infrastructure** (e.g., deprecated GitHub Actions, missing Docker image, build platform config):
  1. **Always check actual build logs** before classifying — use `vercel inspect <url> --logs`, `gh api .../jobs/{id}/logs`, or equivalent. Do NOT assume root cause from prior patterns (e.g., "build platform fails = permissions" was wrong once — actual cause was a TypeScript error).
  2. If fixable (workflow files, migration guards, type fixes): fix directly at Step 10, CI must be green before approval.
  3. If permissions/external (platform team roles, expired tokens): note as non-blocking, inform team separately.
  4. **Expect cascading failures** when fixing CI from scratch — each fix may reveal the next layer (e.g., artifact version → Docker image → migration heads → migration enum). Budget 3-4 iterations.
  5. **CI fix ≠ production fix.** When fixing infrastructure issues (e.g., `alembic upgrade heads`), grep ALL entrypoints — not just CI workflows. Production startup scripts (`start.sh` etc.), Dockerfiles, and Procfiles may have the same bug. In one real deploy, the migration command was fixed in GitHub Actions but missed in the production startup script → deploy failure + 8 min downtime.

### Classify depth

| Level | Criteria | Agents | Validation |
| -- | -- | -- | -- |
| **Deep** | Payments, auth, data sensitive, infrastructure, multi-PR features | 4 (security + quality × each PR) | Full: code + runtime + E2E + infra |
| **Standard** | UI features, incremental improvements, refactors | 2 (security + quality) | Code + runtime |
| **Quick** | Typos, docs, config, dependency bumps | 0 (direct review) | Code only |

### File-size threshold (mechanical, v6.8)

Check whether the PR pushes any file from under 1,000 lines to over:

```bash
for f in $(git diff --name-only origin/develop...HEAD); do
  before=$(git show origin/develop:"$f" 2>/dev/null | wc -l)
  after=$(git show HEAD:"$f" 2>/dev/null | wc -l)
  [ "$before" -lt 1000 ] && [ "$after" -ge 1000 ] && echo "CROSSED 1k: $f ($before → $after)"
done
```

Any crossing → record a MEDIUM structural finding: ask for decomposition (extract helpers, subcomponents, modules) or accept a one-line justification if the file remains clearly organized. Never block solely on this.

### Large PRs (>10K lines)

When `gh pr diff` fails with HTTP 406 ("diff exceeded maximum number of lines"), the GitHub API cannot serve the full diff. Fallback to local git:

```bash
# Full diff (may be very large)
git diff origin/develop...HEAD > /tmp/pr_full_diff.txt

# Focused diff on critical areas (preferred for agents)
git diff origin/develop...HEAD -- app/api/ app/routers/ app/services/ > /tmp/pr_critical_diff.txt
```

For agent prompts on large PRs, save focused diffs to temp files and include them in the prompt rather than passing the full 30K+ line diff. Split by area (routers, services, models, migrations) if needed.

### Check cross-PR conflicts

* Other open PRs targeting the same branch?
* Shared files at risk: migrations, config, schemas, shared services
* Migrations: do multiple PRs branch from the same `down_revision`?

---

## 2. Branch Alignment (mandatory)

**Before any analysis, ensure the PR branch reflects what will actually be merged.**

A review performed against stale code is a review of something that won't exist after merge. Findings may be redundant (already fixed in develop), misleading (caused by code develop already changed), or wrong (conflicting with changes the reviewer can't see).

### Process

1. **Checkout PR branch** and fetch latest:

   ```
   git checkout <pr-branch>
   git fetch origin develop
   ```
2. **Check divergence** from target branch:

   ```
   git log --oneline origin/develop..HEAD   # PR commits not in develop
   git log --oneline HEAD..origin/develop   # develop commits not in PR
   ```
3. **If develop has new commits**, rebase:

   ```
   git rebase origin/develop
   ```
4. **If conflicts arise**, evaluate:

   | Conflict type | Action |
   | -- | -- |
   | Trivial (imports, formatting, non-overlapping) | Resolve and continue |
   | Substantive (same logic modified by both sides) | **Stop.** Send back to owner to resolve — they have the domain context |
   | CI contamination fix already in develop | **Drop the redundant fix** — develop's version wins |
   | Squash-merge ancestry (see below) | Skip duplicate commits, verify survivors |
   | Orthogonal divergence (see below) | Skip rebase, note divergence, proceed |
5. **After rebase, verify surviving commits:**
   * Does every surviving commit belong to the PR author?
   * Are there residual commits from a parent branch that was squash-merged separately?
   * Are there non-code artifacts (plan docs, design files) that hitchhiked from the parent branch?
   * Remove anything that doesn't belong to this PR's scope.
6. **After rebase**, verify the build compiles before proceeding:
   * **Backend:** `docker compose run --rm --no-deps app bash -c "flake8 --count ..."` (or your linter)
   * **Frontend:** `npx tsc --noEmit`
7. **Do NOT push yet** — the rebase is for the reviewer's analysis. Push only after fixes are applied (Step 10).

### Multi-repo reviews: directory verification

When reviewing paired PRs (backend + frontend), every `git` and `gh` command must target the correct repository. **Do not rely on implicit working directory** — verify explicitly before executing.

In one paired review, `gh pr merge <N>` (frontend) was executed from the backend directory. It "succeeded" silently against the wrong repo, and the frontend PR remained OPEN. The error was only caught because a human checked GitHub manually.

**Rule: for multi-repo reviews, prefix every** `gh`/`git` command with an explicit `cd` to the correct repo, or verify the remote URL before executing.

### Squash-merge ancestry

When a developer branches from another feature branch (not develop), and that parent branch is later squash-merged into develop, the individual commits from the parent become duplicates that conflict during rebase. This is expected.

**Pattern:** one PR was branched from another's branch. When the parent was squash-merged into develop, the child inherited 18 duplicate commits. During rebase, all 18 conflicted and had to be skipped. Only the PR author's own commits survived.

**Rule: after rebasing a branch with squash-merge ancestry, verify that only the PR author's commits survive. Watch for residual commits and non-code artifacts (plan docs, design files) from the parent branch.**

### Orthogonal divergence

When develop has new commits but the rebase produces substantive conflicts, check whether the missing commits touch any files in the PR:

```bash
# Files changed in develop since PR branched
git diff --name-only HEAD..origin/develop

# Files changed in the PR
git diff --name-only origin/develop...HEAD
```

If there is **no overlap** between the two file sets, the divergence is orthogonal — the missing develop commits and the PR are changing completely different parts of the codebase. In this case:

1. **Abort the rebase** (`git rebase --abort`)
2. **Note the divergence** in the review doc (what's missing, why it's safe to skip)
3. **Proceed with the review** — the PR code is valid for analysis even without the latest develop
4. **The owner must still rebase before merge** — this skip is for review purposes only

### Why this step exists

In one review, a fix was written for a bug only to discover during a late rebase that develop had already resolved the same bug differently. The fix was redundant work, and the resulting merge conflict had to be resolved manually. Rebasing first would have revealed that the issue was already solved and eliminated an entire finding from the review.

**Rule: context before action. Understand what develop already contains before analyzing what the PR changes.**

---

## 3. Static Analysis (parallel agents)

Launch agents in parallel with calibrated prompts.

### Severity criteria (include in every prompt)

* **CRITICAL** — Data loss, security breach, financial impact, user data exposure
* **HIGH** — Must fix before merge (broken functionality, auth bypass, payment errors)
* **MEDIUM** — Should fix (DRY, consistency, deprecated patterns, missing validation)
* **LOW** — Nice to have (naming, style, minor optimizations)
* **INFO** — Observations, no action needed

### Agent types

* **Security reviewer** — Vulnerabilities, auth, data exposure, input validation, OWASP
* **Quality reviewer** — SOLID, DRY, code smells, test coverage, patterns

### Quality reviewer: detection prompts

Include these instructions in every quality reviewer prompt:

**Duplicate detection:**

> "For each function the PR modifies or calls, check whether an equivalent function exists in the same module or sibling modules that performs the same operation differently. Report divergences in behavior (e.g., one strips HTML before counting words, the other doesn't). Also flag any import from the API/route layer inside the service layer — services must never import from routes."

**Silent failure paths:**

> "Identify code paths that fail silently — producing incorrect results without raising errors. Examples: a file format accepted by UI but not processed correctly (falls through to wrong handler), an endpoint that exists in code but is never registered (returns 404 without any indication the code is dead), a feature flag that accepts a value but ignores it. These are higher priority than loud failures because they go undetected."

**Integration point registration:**

> "For each new file (router, component, hook, service), verify it is registered/imported at its entry point. Backend: new routers must be registered at the app entry point. Frontend: new file types in a dropzone/uploader must have a processing branch. New hooks must be imported where used. Flag any new file that appears unconnected to the application."

**Structural regression (v6.8):**

> "Evaluate whether the diff degrades the structure of the code it touches. Flag: (1) new ad-hoc conditionals or special-case branches inserted into unrelated or shared flows; (2) one-off booleans, nullable modes, or flags that complicate existing control flow; (3) thin wrappers or identity abstractions that add indirection without buying clarity; (4) casts, `any`/`unknown`, or unnecessary optionality that obscure the real contract where a clearer type boundary could exist; (5) feature-specific logic leaking into general-purpose/shared modules, or logic placed in the wrong layer when a canonical home exists; (6) bespoke helpers that near-duplicate an existing canonical utility; (7) multi-step updates that can leave state half-applied when a more atomic structure is available, and unnecessary sequential orchestration of independent work. Report these as STRUCTURAL findings — they default to MEDIUM (consolidation recalibrates; see Step 4)."

### Multi-PR same-repo: branch contamination

When reviewing multiple PRs that target the same repo, the working tree can only be on one branch at a time. Agents launched in parallel will all read files from whichever branch is checked out, producing false positives for the other PRs.

**Rule: for multi-PR reviews in the same repo, either:**

1. **Include the diff in the agent prompt** instead of telling the agent to read the file (preferred — avoids branch dependency entirely)
2. **Sequence agent launches** — checkout PR A's branch, launch its agents, then checkout PR B's branch, launch those agents
3. **Tell each agent explicitly which branch to checkout** before reading files

Option 1 is strongly preferred because it eliminates the failure mode entirely and allows full parallel execution.

---

## 4. Consolidation

* Deduplicate findings across agents (same issue from security + quality → one finding)
* Assign unified IDs: `B1-Bn` (backend), `F1-Fn` (frontend)
* **Recalibrate severities** — this is a core LT function, not an occasional correction. Agents consistently inflate code quality issues (SRP violations, magic numbers, verbose mappings) to BLOCKING/HIGH. The LT must always evaluate relative severity: a DRY violation is not HIGH when the same PR has an endpoint returning 404. Recalibration happens every time, for every review.
* Incorporate context from existing PR comments (don't duplicate what others found)
* Generate consolidated document with consistent format

### Classify finding origin

For each finding, classify its origin. This determines the expected action:

| Origin | Definition | Action |
| -- | -- | -- |
| **IN-SCOPE** | Bug or issue introduced by this PR's code changes | Must fix if MEDIUM+. This is the PR author's responsibility. |
| **ADJACENT** | Pre-existing issue in a file the PR touches | Fix opportunistically if trivial (< 5 lines, no risk). Otherwise track as follow-up. |
| **OUT-OF-SCOPE** | Pre-existing issue in a file the PR does NOT touch | Track as a separate issue. Never block the PR for this. |

**Why this matters:** in one review, the most impactful finding (XSS via `javascript:` URIs) was in a file the PR didn't modify. Without origin classification, it's tempting to either block the PR unfairly or ignore the finding entirely. The correct action: fix it opportunistically since the PR touched adjacent components, but don't hold the PR hostage for pre-existing debt.

### Structural severity calibration (v6.8)

Structural findings (spaghetti growth, thin wrappers, boundary muddying, layer leaks, file-size crossings) follow stricter severity rules than functional ones:

* Default severity: **MEDIUM**.
* **HIGH** only when the PR materially tangles a *shared* path AND the cleaner structure is obvious and scoped (the fix doesn't require a redesign).
* **Never CRITICAL.**
* A *missed simplification opportunity* — code that works but could be dramatically simpler — is a **suggestion (INFO)**, never a blocker. Don't gut working complexity; recommend the reframing and move on.

### The code-judo question (v6.8)

After consolidating findings, ask once, explicitly: **is there a reframing of this change that deletes whole branches, helpers, modes, or layers while preserving behavior?** Refactors that move complexity around without reducing the number of concepts a reader must hold are the target. If a reframing exists, record it as an IN-SCOPE suggestion (INFO) with a concrete sketch of the simpler shape. This question informs the review — it does not gate the merge.

---

## 5. Empirical Validation

### 5a. Setup

* Branch should already be rebased from Step 2
* **Backend:** `docker compose up -d --build` or your dev server — verify health endpoint responds
  * **All backend commands (linting, tests, imports) run inside Docker.** Never install dependencies locally. Use: `docker compose run --rm --no-deps app bash -c "..."`
* **Frontend:** `npm run dev` — verify dev server compiles and serves

### 5b. Code validation

* Verify each finding against actual source: file:line must match
* Correct agent imprecisions (wrong line numbers, incorrect code snippets)
* Mark: CONFIRMED / PARTIALLY CONFIRMED / NOT CONFIRMED

### 5c. Runtime validation

* **API endpoints:** `curl` to test auth, error handling, status codes, edge cases
* **Frontend UI:** Playwright (or similar) to test navigation, auth bypass, rendering, user flows
* **Concurrency:** Parallel requests to test blocking, race conditions, idempotency
* **Failure scenarios:** Missing env vars, service down, invalid input
* **Happy path E2E** (if applicable): Full user flow from start to finish
* **Security fixes:** Validate the vulnerability is closed with before/after comparison (e.g., endpoint returned 200 before fix, returns 401 after). Test against a running instance, not just code reading. Lesson: a header-trust auth bypass (`X-User-ID` honored without verification) was in one codebase for months — only confirmed exploitable when tested against production with `curl`.
* **Migration fixes:** Validate from scratch (`docker compose down -v && docker compose up -d --build`) — not just incremental migration on existing DB.

### 5d. Infrastructure validation

(Example stack — adapt the specifics; keep the categories.)

#### Migrations (Alembic or equivalent)

- [ ] `down_revision` doesn't conflict with other open PRs
- [ ] Both `upgrade()` and `downgrade()` exist and are correct
- [ ] Column changes are safe: `nullable=True` for new columns (no table lock)
- [ ] `DateTime(timezone=True)` if storing timezone-aware values
- [ ] No data migration needed (or included if needed)
- [ ] Auto-migration on startup won't break

#### Docker / Deployment

- [ ] `docker-compose.yml` changes don't break existing services
- [ ] New services have health checks
- [ ] `Procfile` updated if new process types added
- [ ] Volume mounts don't expose sensitive paths

#### Environment & Config

- [ ] New env vars added to `.env.example` / `.env.local`
- [ ] New env vars added to the production environment
- [ ] Production settings validation updated if new required vars
- [ ] Startup fails fast if critical config is missing (not runtime error)
- [ ] No secrets hardcoded in code

#### Integration Point Registration

- [ ] New backend routers registered at the app entry point
- [ ] New frontend file types in uploaders have processing branches (not just MIME acceptance)
- [ ] New background tasks registered in the task runner's discovery list
- [ ] New React hooks/contexts imported and used where intended
- [ ] No dead code: every new file is reachable from the application entry point

#### Rate Limiting

- [ ] New endpoints have appropriate rate-limit decorators
- [ ] AI-backed endpoints: 5-30/hour depending on cost
- [ ] Auth endpoints: 10/minute
- [ ] Read endpoints: 60-120/minute

#### Background Tasks (Celery or equivalent)

- [ ] New tasks registered in the autodiscover list
- [ ] Tasks have appropriate time limits
- [ ] Task idempotency handled (can retry safely)
- [ ] Schedule updated if periodic task added

#### Database

- [ ] No raw SQL — use the ORM with bound parameters
- [ ] Session management via the standard dependency
- [ ] No N+1 queries introduced
- [ ] Transactions committed explicitly where needed
- [ ] Pool settings appropriate (no connection leaks)

#### External Services

- [ ] Payment provider: products/prices exist in target environment
- [ ] Auth provider: scopes/permissions configured
- [ ] API keys present in all environments
- [ ] Webhook endpoints handle errors correctly (re-raise for retry)

### 5e. Update document

* Mark each finding's validation status
* Add evidence table with method and result

---

## 6. Plan Mode (deep review only)

* Define: scope, agents to launch, what to validate empirically, expected risks
* LT approves before executing
* Not needed for standard/quick — the overhead doesn't justify the value

---

## 7. LT Review

* Review document before publishing
* Adjust: tone, severity, focus, wording
* Verify cross-references between related PRs are correct
* **Check that finding origins (IN-SCOPE / ADJACENT / OUT-OF-SCOPE) are correctly classified** — misclassification leads to unfair blocking or missed improvements

---

## 8. Publish

### GitHub reviews

* One review per PR, concise
* Each finding: severity + origin + `file:line` + code snippet + suggested fix
* Cross-reference between related PRs
* Verdict: REQUEST CHANGES / APPROVE WITH CHANGES / APPROVE
* **Anti-nit-flooding (v6.8):** prefer a small number of high-conviction comments over a long cosmetic list. If structural or functional issues exist, don't bury them under LOW/style nits — fold nits into one collapsed section or drop them.

### Multi-repo publish discipline

**For paired PRs, every** `gh pr review` and `gh pr view` must be run from the correct repo directory. `gh` resolves the target repo from the local `.git` remote — running it from the wrong directory silently posts to the wrong repo's PR number.

**Pattern:** in one paired review, `gh pr review <N>` was run from the backend directory. It posted the review on the backend's PR with that number (an old, already-merged PR) instead of the frontend's. The command succeeded silently — the error was only caught because a human checked GitHub.

**Rule: prefix every** `gh` publish command with an explicit `cd` to the target repo:

```bash
cd /path/to/backend-repo && gh pr review 113 --comment --body "..."
cd /path/to/frontend-repo && gh pr review 80 --comment --body "..."
```

### Communication

* Message to PR owner with top 3 findings and links
* Alerts to team if cross-PR conflicts detected (e.g., migration collisions)

---

## 9. Re-review

When the owner pushes fixes, follow the **Re-review process** defined in Step 0. Key points:

* Analyze only the delta (new commits since last review)
* Verify HIGH+ findings are resolved, spot-check MEDIUMs
* Scan fix commits for regressions
* Update the existing review document (don't create a new one)
* Decide next step:

| Outcome | Action |
| -- | -- |
| All HIGH+ resolved, MEDIUMs acceptable | **Approve** (Step 8) |
| Minor residual items, non-blocking | **LT direct fix** (Step 10) → Approve |
| HIGH+ still open or new issues | **Request changes** again |

---

## 10. LT Direct Fix (optional)

When re-review reveals **residual non-blocking items** that the LT can fix faster than another review round. Also used for first reviews where findings are small enough to resolve directly.

### When to use

* Remaining items are MEDIUM or LOW severity
* Fixes are small and well-scoped (< 20 lines per file)
* No architectural decisions needed — the fix pattern already exists in the codebase
* Time-sensitive: another round-trip with the owner would delay the merge unnecessarily
* CI is red from **cross-PR contamination** (merged code from another branch)
* **Early CI fix (Step 1):** CI is red from syntax errors or import issues in the PR's own code, and the fix is trivial (missing import, type annotation). Apply at Step 1 to unblock the review rather than waiting for Step 10. Document in the review doc as "LT Direct Fix (Step 10 applied early)."

### When NOT to use

* HIGH/CRITICAL items still open — send back to owner
* Fix requires design decisions or the owner's domain knowledge
* Multiple files with complex interdependencies

### Process

 1. **Rebase onto develop first** (if not already done in Step 2) — ensures fixes are applied on top of the latest base. Never fix code that develop has already changed.
 2. **Investigate before fixing** — for each finding, ask: does a fix already exist elsewhere? Is someone else working on this? Is the finding still valid after rebase?
 3. **Assess viability** — for each residual item, classify as viable / not viable now / deferred
 4. **Get LT approval** — confirm scope before coding ("we'll fix X, Y, Z; defer W")
 5. **Implement fixes** on the PR branch — small, targeted changes
 6. **Validate each fix**:
    * **Backend:** Always via Docker. `docker compose run --rm --no-deps app bash -c "..."` for linting, tests, import checks.
    * **Frontend:** `npx tsc --noEmit`, dev server compilation, production build if applicable.
    * Same rigor as Step 5 (runtime, browser automation as applicable).
 7. **Commit with clear attribution** — commit message references finding IDs (e.g., "fix(review): B1+B2+B3 — centralize word count logic")
 8. **Document what's deferred** — commit message and PR comment list remaining items with justification
 9. **Push with** `--force-with-lease` — never `--force`. This protects against overwriting concurrent pushes from the PR owner.
10. **Verify CI is green** — wait for all checks to pass before approving
11. **Confirm merge state** — `gh pr view <N> --json mergeable,mergeStateStatus` must show `CLEAN` + `MERGEABLE`
12. **Approve** — post review approval + updated PR comment with resolved/deferred summary
13. **Communicate** — message to owner with what was fixed, what's deferred, and that PR is approved

### Key rules

* **Verify working directory before every command.** In multi-repo reviews, `gh pr merge`, `git push`, and `git commit` must target the correct repo. A command run from the wrong directory can succeed silently against the wrong PR. Always `cd` explicitly or check `git remote -v` before executing.
* **CI must be green before approval.** If CI fails after your fix commits, diagnose and resolve — don't approve with red checks.
* **Always** `--force-with-lease`. If it fails, someone else pushed — investigate before retrying.
* **Confirm CLEAN + MERGEABLE after push.** GitHub may take a few seconds to recalculate merge state after a force push.

---

## Principles

### On judgment

1. **Context before action** — before fixing anything, understand why it's broken. If CI is red on a file the PR doesn't touch, investigate who introduced it and whether a fix already exists. The cost of investigating for 2 minutes is always lower than the cost of writing a redundant fix that creates a merge conflict later.
2. **The process is a framework, not a script** — follow the steps, but apply judgment at every one. If a step doesn't make sense for the situation, adapt. A reviewer who follows the process blindly and misses an obvious rebase gap is less effective than one who skips a step but catches the right problem. No checklist replaces engineering intuition.
3. **Exhaustive is not the same as effective** — finding 10 issues with 0 false positives sounds thorough, but if one of those fixes is redundant because you didn't check develop first, the thoroughness was misallocated. Prioritize: state of the branch > analysis of the code > volume of findings.

### On evidence

4. **Evidence over opinion** — a finding without proof is not a finding
5. **Validate real, not theoretical** — run the app, test the endpoints, prove it
6. **Calibrate severity** — DRY in tests ≠ security in payments
7. **Technical severity ≠ impact severity** — evaluate the full error path, not just the point of failure. A MEDIUM code smell is non-blocking if the downstream system catches the failure (e.g., a fragile client-side race condition where the backend enforces auth anyway). Conversely, a LOW-looking silent failure can be HIGH if nothing downstream catches it.

### On collaboration

 8. **Don't duplicate** — check existing reviews/comments before posting
 9. **LT reviews before publishing** — tone and focus matter
10. **Owner fixes first, LT fixes last** — default: send back to owner. Exception: residual non-blocking items where LT direct fix (Step 10) is faster than another round-trip

### On safety

11. **Cross-PR awareness** — PRs don't exist in isolation; check for conflicts and CI contamination from merged branches
12. **Green CI before approval** — never approve with failing checks, even if the failure isn't from the PR's own code
13. **Rebase before review** — always analyze code that reflects the actual state of the target branch. A review against stale code is a review of something that won't exist after merge.
14. **Silent failures over loud ones** — a 500 error is better than a 200 with corrupt data. Prioritize finding code paths that appear to work but produce wrong results: formats accepted but not processed, endpoints defined but not registered, features enabled but ignored. These go undetected in testing and production.
15. **Multi-repo discipline** — every `git`/`gh` command targets a specific repo. In paired reviews, never assume the shell is in the right directory. Verify before executing. A command that succeeds silently against the wrong repo is worse than one that fails loudly.

---

# Begin

Now execute the process for `$ARGUMENTS`. Start at Step 0 (Input) — identify whether it's a single PR or a paired multi-repo review and whether this is a first review or re-review. Then proceed through the steps with judgment.
