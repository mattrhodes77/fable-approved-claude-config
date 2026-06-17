---
name: babysit-prs
description: Sweep ALL Matt-authored open PRs across the MindFortressInc org, address actionable CodeRabbit findings, re-trigger CR if needed, report status. Designed to be wrapped in /loop for hourly recurring runs until all CRs satisfied.
argument-hint: "[repo1,repo2,...] or 'no-loop' (default: arm hourly cron + sweep all org repos)"
allowed-tools:
  - Bash
  - Read
  - Edit
  - Write
  - Glob
  - Grep
  - AskUserQuestion
  - CronList
  - CronCreate
  - CronDelete
  - ToolSearch
---

<objective>
Cycle through ALL of Matt's open PRs across the MindFortressInc org. For each PR with new CodeRabbit feedback, apply the fix and push. For PRs stuck in CR queue, re-trigger if appropriate. Report a tight status table at the end. Loop-safe — designed for hourly invocation via `/loop 1h /babysit-prs`.

Hard scope: NEVER merge PRs, NEVER push --force without --force-with-lease, NEVER touch DRAFT PRs.
</objective>

<repos>
Default: sweep the entire MindFortressInc org for PRs Matt authored. No hardcoded repo list — uses GH search.

Optional argument: comma-separated repo NAMES (not full owner/name) to filter to a subset. Examples:
- `/babysit-prs reeve-services,reeve-frontend` — only sweep those two
- `/babysit-prs` (no arg) — sweep ALL repos in the org where Matt has open PRs
</repos>

<process>

## Step 0 — Load iteration state + auto-arm cron if missing

**0a. Load the queue state** from `/tmp/babysit-prs-state.json`. Format:

```json
{ "pending_fingerprint": "ab12cd…", "no_progress_streak": 1, "pending_count": 7, "last_iter_at": "2026-05-20T07:13:00Z" }
```

Rules:
- If the file doesn't exist, treat as `no_progress_streak = 0`, `pending_fingerprint = ""`.
- If `last_iter_at` is more than 6h old, treat as a fresh session — reset `no_progress_streak = 0` and ignore the stored fingerprint (the prior session ended; state is stale).
- Hold `no_progress_streak` and `pending_fingerprint` in mind; you'll recompute them in Step 6 and decide whether to auto-stop.

This is a tiny shell read:
```bash
jq -r '"\(.no_progress_streak // 0)\t\(.pending_fingerprint // "")"' /tmp/babysit-prs-state.json 2>/dev/null || printf '0\t\n'
```
Do NOT skip it.

**0b. Silent auto-arm of the hourly cron.** This skill is always meant to recur hourly. Call `CronList` and check whether any job has `prompt == "/babysit-prs"` and is recurring.

- If one exists: do nothing (already armed).
- If none exists: call `CronCreate` with `cron: "7 * * * *"` (off the :00 minute mark to avoid fleet bunching), `prompt: "/babysit-prs"`, `recurring: true`. Note in the final report's opening line: "_Auto-armed hourly cron `<id>` — stays alive while the PR queue has pending work; auto-stops only when drained (or stalled)._"

Opt-out: if `$ARGUMENTS` is the literal string `no-loop`, skip 0b — that's the explicit one-shot escape hatch. Any other arg is treated as the repo filter (Step 1). Plain `/babysit-prs` (no arg) always arms.

**0c. Arm careful-hook loop-mode (skip if `no-loop`, same gating as 0b).** This lets routine destructive cleanup (worktree / `.venv*` / `test_*.db` teardown not on the careful-hook safe-list) auto-proceed and get logged instead of wedging the unattended loop on a confirmation prompt. The window self-expires in 90 min and is re-armed every iteration, so it disarms ~90 min after the loop stops re-arming it:

```bash
~/.claude/hooks/loop-mode-arm.sh 90 2>/dev/null || true
```

Anything auto-proceeded is recorded in `~/.claude/cleanup-needed.log` — if it's non-empty at the end of the run, surface a one-line "cleanup pending" note in the final report.

## Step 1 — Discover all open PRs Matt authored across the entire org

ONE call gives us every open PR across every repo:
```bash
gh search prs --owner MindFortressInc --author "@me" --state open \
  --json repository,number,title,url,isDraft,labels --limit 100
```

Note: `gh search prs` does NOT return `headRefName` or `mergeable` (Available fields are: assignees, author, authorAssociation, body, closedAt, commentsCount, createdAt, id, isDraft, isLocked, isPullRequest, labels, number, repository, state, title, updatedAt, url). When you need the branch name or mergeability for a specific PR, fetch with `gh -R <owner>/<repo> pr view <pr> --json headRefName,mergeable,mergeStateStatus`.

If $ARGUMENTS is non-empty, parse comma-separated repo names and filter the result set to only those repos (compare `.repository.name` case-insensitively).

Skip ALL PRs where `isDraft == true`. Print the post-filter count at the top of the run.

## Step 2 — Per PR, classify CR state

Skip these BEFORE classification:
- `isDraft == true`
- Title starts with `[WIP`, `WIP:`, `[wip`, or contains "don't merge"
- Labels contain `do-not-merge`, `wip`, `dnr`, or similar

For each survivor, pull:
```bash
# Latest CR review timestamp
gh api repos/<owner>/<repo>/pulls/<pr>/reviews \
  --jq '[.[] | select(.user.login | test("coderabbit"; "i"))] | sort_by(.submitted_at) | last'

# Inline review comments
gh api repos/<owner>/<repo>/pulls/<pr>/comments \
  --jq '[.[] | select(.user.login | test("coderabbit"; "i"))]'

# Most recent issue-level CR comment (walkthrough / rate-limit / no-actionable msg)
gh api repos/<owner>/<repo>/issues/<pr>/comments \
  --jq '[.[] | select(.user.login | test("coderabbit"; "i"))] | sort_by(.created_at) | last'

# Branch name + last commit timestamp (gh search prs didn't return headRefName)
gh -R <owner>/<repo> pr view <pr> --json headRefName,mergeable,mergeStateStatus
gh api repos/<owner>/<repo>/commits/<branch> --jq '.commit.committer.date'
```

(Owner is always `MindFortressInc` in our default scope; substitute the actual owner from `.repository.nameWithOwner` when reading the search response.)

Classify into ONE of these states:

- **CLEAN** — latest issue body contains "No actionable comments were generated" OR all inline comments are older than the last push on the branch (= already addressed)
- **HAS_ACTIONABLE** — there exist inline review comments NEWER than the last push to the branch's head commit
- **RATE_LIMITED** — latest issue body contains "Rate limit exceeded"
- **NO_REVIEW_YET** — no review or inline comments at all
- **TRIGGERED_WAITING** — last issue body is "Review triggered" + older than 30 min + no inline comments since

## Step 3 — Act per state

### CLEAN
Report only. Note "ready to merge" if mergeable + checks green.

### HAS_ACTIONABLE
For each inline comment NEWER than the last push:

1. Find the right worktree. Use `git worktree list` in the repo's main checkout. If the branch isn't checked out, create a sibling worktree at `/tmp/<repo>-<branch-short>`.
2. Apply the CR's suggested fix EXACTLY when it's mechanical (regex change, min/max bound, missing validation, etc.).
3. If the finding requires architectural judgment ("refactor X to Y", "rename Z"), skip — leave it for human review. Note in the report.
4. After applying all fixes — **`ast.parse` is syntax-only; it does NOT catch a behavioral break. If the fix changes runtime behavior of source-under-test, you MUST run the affected test suite, not just parse it.**
   - For Remotion: `npx tsc --noEmit` must pass.
   - For services: `python3 -c "import ast; ast.parse(open('<file>').read())"` per touched file (syntax gate only).
   - **If the fix touches a test file OR a source file that has a sibling test module → RUN that test module.** A behavioral source change (new guard/branch/return contract) is exactly what `ast.parse` misses — e.g. PR #419 added `if not sent: continue`, broke 4 existing tests whose spy returned `None`, and shipped red because only `ast.parse` ran. Map source→test by convention (`api/services/planning/date_triggers.py` → `tests/planning/test_date_due_soon.py`); if unsure, run the whole nearest test dir.
   - **Worktree has NO venv and bare `python` is NOT on PATH** (`command not found: python`). Run pytest via the main checkout's interpreter:
     `/Users/mattrhodes/Coding_MM/<repo>/.venv/bin/python -m pytest <test_path> -q`
     (resolution order: worktree `.venv` → sibling main-checkout `.venv` → `python3`). If NONE resolves, you **cannot validate** — do NOT push a behavioral change; report "unvalidated, skipped" and leave it for the next sweep / human.
   - If the suite has ANY failure (including ones your fix surfaced in pre-existing tests), revert and skip — never push a red suite.
5. Commit with message: `fix(CR PR #<N>): <one-line summary of what was addressed>`
6. Push: `git push origin <branch>` (NOT --force; if rebase needed, use --force-with-lease and document in commit message)
7. CR will auto-re-review on the new commit (NO manual trigger needed when commits land).

### RATE_LIMITED
Check the wait time mentioned in the body. If wait has passed (= last issue comment > 1hr old), post `@coderabbitai review` to bump. Otherwise skip — too soon.

### NO_REVIEW_YET
Check PR age. If <30 min old, skip (CR just hasn't run yet). If >30 min old, post `@coderabbitai review` to bump.

### TRIGGERED_WAITING
If last "Review triggered" ack is >60 min old AND no new review/inline since, post one more `@coderabbitai review`. Otherwise skip (still in queue).

## Step 4 — Rate-limit awareness + liveness guard

Per [[coderabbit-pro-rate-limit]] memory: Pro tier hits hourly per-user ceilings. Don't post `@coderabbitai review` on more than 3 PRs per invocation. If more than 3 PRs need a re-trigger, queue the rest with a note "deferred to next loop iteration".

**Liveness re-check before every bump.** Jesus ([[jesus_sr_engineer_merger]], `jesusroncal94`) is the main reviewer/merger and merges in bursts — often mid-sweep — so a PR you classified minutes ago may already be merged/closed. Immediately before posting `@coderabbitai review`, confirm the PR is still open:
```bash
gh -R <owner>/<repo> pr view <pr> --json state -q .state   # must be "OPEN"
```
If it's `MERGED`/`CLOSED`, skip the bump (CR ignores merged PRs — it's wasted) and note it dropped off. Same applies before starting a fix: re-confirm `OPEN` so you don't fix a PR Jesus just merged.

## Step 4.5 — CR CLI supplement (uses Matt's separate ~3/hr quota; runs only when Matt is quiet)

The CR cloud bucket is one quota; the CR CLI ([[coderabbit_cli_bypasses_cloud_credit_block]]) is a SEPARATE ~3/hr bucket Matt can use locally. It's the only way to get CR review on **stacked PRs cloud rejects** (the "Auto reviews disabled on base/target branches" message). The loop uses CLI as a supplement under two strict guards:

1. **Quiet guard.** Don't run the CLI when Matt is actively working — he uses it himself, so we'd burn his quota or clash with his runs.
2. **Backgrounded.** Each CLI review takes 5–10 min, which would blow the <10 min sweep budget. Launch in background; harvest results in the *next* sweep.

### 4.5a — Quiet detection

```bash
is_matt_quiet() {
  # Always-quiet window: 02:00–09:00 America/Los_Angeles (DST-aware via TZ=)
  local hour=$(TZ=America/Los_Angeles date +%H)
  if [ "$hour" -ge 2 ] && [ "$hour" -lt 9 ]; then echo "yes:off-hours"; return; fi

  # Matt explicitly said: don't block on his own live CLI — "if your 3rd gets blocked, so be it."
  # Some launches will rate-limit if he's running CLI; that's expected and handled in 4.5d.
  # So the only "active work" signal we use is recent commits.
  # Any commit by Matt across ~/Coding_MM/* in last 30 min?
  local cutoff=$(date -u -v-30M +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '30 minutes ago' +%Y-%m-%dT%H:%M:%SZ)
  for repo in /Users/mattrhodes/Coding_MM/*/; do
    [ -d "$repo/.git" ] || continue
    if git -C "$repo" log --since="$cutoff" --author=mattrhodes77 --author=mindfortress -1 --oneline 2>/dev/null | grep -q .; then
      echo "no:recent-commit-in-$(basename $repo)"; return
    fi
  done
  echo "yes:no-recent-activity"
}
```

If first word is `no:...`, **skip this entire step**.

### 4.5b — HARVEST first: process completed CLI runs from prior sweeps

For each `/tmp/cli-*.pid` file: if the PID is no longer running AND the matching `/tmp/cli-*.out.json` contains a `{"type":"complete"}` line, the CLI is done.

```bash
harvest_cli_runs() {
  for pid_file in /tmp/cli-*.pid; do
    [ -f "$pid_file" ] || continue
    local pid=$(cat "$pid_file")
    if kill -0 "$pid" 2>/dev/null; then continue; fi  # still running
    local base=$(basename "$pid_file" .pid)          # cli-<repo>-<pr>
    local out="/tmp/${base}.out.json"
    local repo=$(echo "$base" | cut -d- -f2-3)        # repo name (handle reeve-services, agentpik-backend, etc.)
    local pr=$(echo "$base" | rev | cut -d- -f1 | rev)
    # Parse + render + comment + apply
    # ... (see 4.5c for what to do with each finding)
    rm -f "$pid_file" "$out"
  done
}
```

(Implementation detail: parsing the `base` for `repo` is fragile for repos with hyphens — record `repo` and `pr` in a `/tmp/cli-<id>.meta` sidecar at launch time instead, with `repo=...` and `pr=...` lines.)

### 4.5c — For each completed CLI run

1. **Re-check OPEN.** If Jesus merged the PR mid-run, skip — don't comment on a merged PR.
2. **Parse findings** from the NDJSON: `jq -c 'select(.type=="finding")' "$out"`. Each finding has at minimum `severity`, `file`, `line`, `title`, `description`, and (often) `prompt_for_ai_agents`.
3. **Apply mechanical fixes** per Step 3's HAS_ACTIONABLE rules (quick wins / minor findings only; skip 🏗️ heavy-lift and judgment items). Use a sibling worktree at `/tmp/<repo>-<branch>-cli` (reuse the one created at launch time).
4. **Commit + push** if any fixes applied, message `fix(CR CLI #<N>): <summary>` so PR history reflects this came from CLI not cloud.
5. **Post a PR comment** with the full findings — agreed format per session decision (apply fixes + post full findings):

   ```markdown
   ## 🤖 CodeRabbit CLI review (local)

   _Cloud CR isn't reviewing this PR (auto-review disabled on non-default base). The babysit-prs loop ran the CR CLI locally and surfaced these findings._

   **N findings** · M auto-fixed · K left for human review

   ### 🔴 Critical / Major
   - **`<file>:<line>`** — <title>
     <description>

   ### 🟡 Minor / Nitpick
   ...

   ---
   _CR CLI usage is a separate quota from cloud. Posted by babysit-prs CLI supplement._
   ```

### 4.5d — LAUNCH new CLI runs (up to 3 minus in-flight)

After harvesting, count remaining in-flight (`ls /tmp/cli-*.pid 2>/dev/null | wc -l`). Launch up to `3 - in_flight` new runs.

Some launches may rate-limit (per-user CLI cap is shared with Matt's own CLI usage — if he had a run going at the time, that occupies one of the slots). When a launch errors out with `"errorType":"rate_limit"`, clean up that PR's files silently and move on; harvest the rest. Don't reduce future-sweep budget for it.

**Target selection** (bottom-up by stack depth):
- ✅ Stacked PRs where the latest CR issue comment is "Auto reviews are disabled on base/target branches" AND inline count == 0 AND no `/tmp/cli-<repo>-<pr>.pid` already exists.
- ❌ Skip base-`main` PRs — cloud CR auto-reviews those when its budget recovers; don't burn CLI quota.
- ❌ Skip PRs you've already fix-pushed this sweep — let cloud handle the re-review on the new commit.

**Per launch** (background, non-blocking):

```bash
launch_cli_review() {
  local repo="$1" pr="$2"
  [ "$(gh -R MindFortressInc/$repo pr view $pr --json state -q .state)" = "OPEN" ] || return
  local meta=$(gh -R MindFortressInc/$repo pr view $pr --json headRefName,baseRefName)
  local head=$(echo "$meta" | jq -r .headRefName)
  local base=$(echo "$meta" | jq -r .baseRefName)

  cd "/Users/mattrhodes/Coding_MM/$repo"
  git fetch origin "$head" "$base" --quiet 2>/dev/null
  local slug=$(echo "$head" | sed 's|[/.]|-|g')
  local wt="/tmp/$repo-$slug-cli"
  [ -d "$wt" ] || git worktree add "$wt" "origin/$head" --detach 2>&1 | tail -1

  local id="cli-$repo-$pr"
  local out="/tmp/$id.out.json"
  local meta_f="/tmp/$id.meta"
  printf 'repo=%s\npr=%s\nbranch=%s\nbase=%s\nwt=%s\n' "$repo" "$pr" "$head" "$base" "$wt" > "$meta_f"

  # Launch coderabbit DIRECTLY (don't wrap in `timeout`) so $! is the actual child
  # PID, not a wrapper subshell that exits immediately. Detect hangs on the next
  # sweep via started= age in meta_f (kill if older than 15 min).
  # NOTE: --no-color is NOT a valid flag (0.6.0 prints usage and exits 0 — silent no-op).
  # Use --base-commit with the explicit merge-base (avoids stale-local-base mis-scoping).
  local mb=$(git -C "$wt" merge-base HEAD "origin/$base")
  ( cd "$wt" && coderabbit review --agent --base-commit "$mb" > "$out" 2>&1 ) &
  local pid=$!
  echo "$pid" > "/tmp/$id.pid"
  disown "$pid" 2>/dev/null
}
```

Track `CLI_LAUNCHED_THIS_ITER` for the report.

### 4.5e — Failure modes

- **CLI rate-limit**: output contains `"errorType":"rate_limit"` or `"message":".*rate limit.*"` — bail this sweep's CLI work, don't launch more.
- **Auth expired**: output contains `"errorType":"auth"` — note in report ("CR CLI auth expired — run `coderabbit auth login`"). Skip CLI for this sweep.
- **CLI hangs**: at harvest time, if the PID is alive AND the meta_f `started=` timestamp is >15 min ago, `kill -TERM` the PID (and `kill -KILL` after 5s if still alive), discard outputs, report skipped. Also at harvest: if PID died but `out.json` lacks `{"type":"complete"}`, discard same way (mid-run crash).
- **Worktree creation fails** (branch missing from origin etc.): skip that PR, don't block others.

### 4.5f — Budget interaction with cloud bumps

CLI runs and cloud `@coderabbitai review` bumps draw from **separate buckets**. Per sweep:
- Up to **3 cloud bumps** (Step 4 rule, unchanged)
- Up to **3 CLI launches** (this step, only if `is_matt_quiet` returns `yes:...`)

So a fully-active sweep with Matt quiet can advance **6 PRs**, 3 via cloud + 3 via CLI (one may rate-limit if Matt's also running CLI — that's fine, drop it and harvest the other 2). The CLI half is async — its effects land in the *next* sweep's harvest.

---

## Step 4.6 — Red-CI triage (otherwise-clean PRs blocked ONLY by a failing check)

A PR that is CR-CLEAN (no actionable inline) but whose merge is blocked by a **failing CI check** never shows up in GREENS and otherwise just sits forever. This step attempts a **bounded** auto-fix for the mechanical cases and flags everything else as NEEDS_HUMAN. It NEVER guess-patches a real logic failure to make it pass — wrong-but-green is worse than red.

**Scope + budget.** Only triage PRs in Matt's merge lane (the GREENS ✅-lane repos) — never Studio / Freya→main. Cap **2 red-CI triages per sweep** (each costs a log-pull + worktree + test run). Re-confirm `OPEN` before touching.

**Eligibility (from Step 2's `mergeable`/`mergeStateStatus`):** CR state CLEAN/REVIEWED, `mergeable == MERGEABLE`, and `mergeStateStatus == UNSTABLE` or `BLOCKED` (a required check is failing). **Skip `DIRTY`/`CONFLICTING`** — that's a merge conflict, a different remedy (report "needs rebase", don't CI-fix). **Skip `BEHIND`** — it re-greens when the branch updates.

**Triage decision tree:**
1. Latest CI run on the branch: `gh run list -R <owner>/<repo> --branch <head> --workflow CI --limit 1 --json databaseId,status,conclusion`.
2. **Startup-failure / infra signature** — job `failure` in <30s with empty steps + empty `--log`, OR the check-run annotation mentions billing/spend-limit (`gh api repos/<o>/<r>/commits/<sha>/check-runs` → annotations: "recent account payments have failed / spending limit"). This is NOT a code bug: if it looks transient, `gh run rerun <id> --failed` **once** and move on; if it's billing, flag NEEDS_HUMAN ("GH Actions spend cap — Matt's billing action"). Never code-fix this.
3. **Real test/build failure** — `gh run view <id> --log-failed`; extract the `FAILED <test>` line(s) + assertion. **Auto-fix ONLY these bounded, mechanical patterns** (fix in a worktree → VALIDATE by running the affected suite per Step 3's rule → `fix(CI #<N>): <summary>` → push):
   - **Collection/import error** (`ModuleNotFoundError` / `ImportError` / error at collection) — fix the bad import/path.
   - **Stale snapshot / golden** — regenerate via the test's OWN documented mechanism; confirm the diff is only the intended change (like the #592 OpenAPI snapshot).
   - **Clock/cron flake** (asserts two time-derived values differ; passes on re-run) — freeze the clock via monkeypatch, per the repo's pattern.
   - **Lint / format / codegen-parity gate** (ruff/black/eslint/prettier/`make codegen`) — run the formatter/codegen, commit the result.
4. **Everything else → NEEDS_HUMAN.** Real assertion failures, >1 unrelated failing test, anything needing product/logic judgment, anything you don't fully understand, or anything you **can't validate locally** (no interpreter resolves). Flag in the report with the failing test name + a one-line reason. Do NOT patch a logic test just to turn it green.

Track `CI_FIXED_THIS_ITER` + `CI_FLAGGED_HUMAN` for the report. A red-CI fix push counts as a fix for Step 6 (→ PROGRESSING). This is the automation of the #592 case — but bounded: mechanical→fix, ambiguous→escalate, never thrash.

---

## Step 4.7 — Auto-rebase (DIRTY/BEHIND in-lane PRs — bringing a branch current with main is mechanical, NOT a human gate)

Keeping a branch up to date with its base is **automatic** — there is no product judgment in it. So babysit OWNS it for Matt's-lane PRs; never report "needs rebase, your call." (Only a genuinely *semantic* conflict — same line changed two different ways — needs Matt, and the validation below catches exactly those.)

**Scope + budget.** Matt's-lane repos only (GREENS ✅-lane; never Studio / Freya→main). Cap **3 rebases per sweep**. Re-confirm `OPEN`.

**Eligibility (from Step 2's `mergeStateStatus`):** `BEHIND` (stale, no conflict) or `DIRTY`/`CONFLICTING` (conflict with base).

**Procedure:**
1. **First try the cheap path:** `gh pr update-branch <pr>` (merges base in, no force-push). Succeeds for `BEHIND` and any stale-but-clean `DIRTY` → done (CI re-runs; PR re-greens). Errors "Cannot update PR branch due to conflicts" → real conflict, go to 2.
2. **Worktree merge + union-resolve:** sibling worktree at `origin/<head>` → `git merge origin/<base> --no-edit`. On conflict, **union-strip the markers** (`<<<<<<<`/`=======`/`>>>>>>>`) from every conflicted file — correct for the overwhelmingly common case (both sides ADDED different lines in the same block: a router `include_router`, an `__init__.py`/`migrations/env.py` import/export, a model registration).
3. **HARD-VALIDATE before pushing** (this is the safety net that distinguishes additive from semantic):
   - `ast.parse` every touched `.py` (a modify/modify conflict union-stripped → usually a syntax break → caught here).
   - App import (`from api.main import app`) for service/router/wiring changes.
   - Run any touched `tests/*` file, plus the nearest test dir for a touched source module with a sibling suite (e.g. `adbuyer/persona.py` → `tests/reeve_chat/`).
   - **If ANY validation fails → `git merge --abort`, do NOT push, flag NEEDS_HUMAN "semantic rebase conflict in `<file>`".** This is the only case that needs Matt.
4. **Clean validation → commit the merge (`--no-edit`) + plain `git push`** (merge-commit, NO force — works on both Reeve merge-commit and Freya squash flows). CI re-runs; the PR re-greens next sweep.

Track `REBASED_THIS_ITER` + `REBASE_FLAGGED` for the report. A rebase push counts as progress for Step 6. Proven outcome: of 6 conflicting PRs, 4 were additive (auto-resolved+validated+pushed) and 2 were genuinely semantic (`calendar_seed.py`, `persona.py` — union-strip broke syntax → aborted + flagged). Additive→auto, semantic→flag, never ship a wrong merge.

---

## Step 5 — Report

Print one compact markdown table:

```
| Repo | PR # | Branch | State Before | Action Taken | State After |
|---|---|---|---|---|---|
| reeve-remotion | #49 | feat/DEV-900-kinetic-text | HAS_ACTIONABLE (4) | Fixed all 4, pushed | Waiting for CR re-review |
| reeve-services | #226 | feat/DEV-900-kinetic-text | TRIGGERED_WAITING | Re-triggered (no new since 1h+) | Waiting |
| reeve-services | #335 | feat/DEV-1261-inventory-publish | STACKED_BLOCKED | **CLI launched** (background) | CLI run in flight |
| reeve-services | #347 | feat/DEV-1261-publish-wire | STACKED_BLOCKED | **CLI harvested**: 2 fixes pushed + findings posted | Waiting for human merge |
| reeve-remotion | #50 | feat/DEV-904-cinematic-color | CLEAN | None | Ready to merge |
```

Open the report with one line on the CLI bucket:
- `_CLI: quiet=<yes/no:reason> · harvested=<N> · launched=<N> · in-flight=<N>_`

(If `quiet=no:...`, the CLI section is fully skipped this sweep — report harvested=0 launched=0, in-flight may still be >0 from prior sweeps.)

If any red-CI triage ran (Step 4.6), add a second line: `_CI-triage: fixed=<N> · flagged-human=<N> · reran=<N>_`. Put auto-fixed PRs in the action table (`RED_CI → fixed + pushed`) and flagged ones under NEEDS_HUMAN with the failing test name.

**GREENS block — LEAD the report with this, EVERY sweep (right after the CLI line).** Publish the merge-ready set so Matt sees what's mergeable at a glance without opening anything. Two green tiers (the classifier already pulls `mergeable`/`mergeStateStatus`):
- **🟢 Strict-green** = CR-CLEAN **and** `mergeable == MERGEABLE` **and** `mss == CLEAN`.
- **🟡 Mergeable-with-non-required-red** = CR-CLEAN **and** `mergeable == MERGEABLE` **and** `mss == UNSTABLE` **AND every failing check is on the cosmetic allowlist** (see the MANDATORY gate below). `UNSTABLE + MERGEABLE` does NOT by itself mean cosmetic — a repo that doesn't mark its test workflow *required* will show a **real pytest/CI failure as `UNSTABLE`, not `BLOCKED`**. You MUST inspect which check failed; never infer "cosmetic" from `mss` alone.

  **🟡 GATE (mandatory — never skip; this is not optional prose):** for EVERY `UNSTABLE` PR, pull the failing check names:
  ```bash
  gh -R <owner>/<repo> pr view <pr> --json statusCheckRollup \
    -q '[.statusCheckRollup[]? | select((.conclusion // .state // "")|test("FAILURE|ERROR|TIMED_OUT|CANCELLED";"i")) | (.name // .context)] | join(",")'
  ```
  Classify each failing name:
  - **COSMETIC** (→ may be 🟡, mergeable): ONLY known non-gating deploy checks — Vercel embed/preview deploys (`reeve-chat-embed`, `reeve-sign-embed`, `vercel`), and the like. This is an explicit allowlist — when unsure, it is NOT cosmetic.
  - **RED — genuinely failing** (→ NOT 🟡): anything matching `pytest|CI|test|build|lint|ruff|black|eslint|codegen|mypy|tsc|check` — i.e. the test/build/codegen workflow itself. A PR with ANY such failing check is **NOT a green of any tier**. Route it: if it's behind its base → **Step 4.7 auto-rebase** (a stale branch inherits guard/allowlist/snapshot failures main has since fixed — rebase is usually the whole fix; this was the 2026-06-16 incident: 7 reeve-services PRs 44-51 commits behind main, all red on `test_dev_v1_routes_are_mounted` / allowlist guards purely from staleness). Otherwise → **Step 4.6 red-CI triage** or NEEDS_HUMAN. Surface it in the report under a **🔴 RED-CI** line, never under greens.

  A PR is 🟡 only if it has ≥1 failing check AND **all** of them are cosmetic-allowlisted. If the failing-check list is empty (all green/pending-only), it's effectively 🟢-pending — re-poll, don't call it 🟡.

Out of greens (handled elsewhere, not skipped): `DIRTY`/`CONFLICTING`/`BEHIND` → **auto-rebase in Step 4.7** (in-lane; mechanical, no human gate). `UNKNOWN` → GitHub still computing, re-poll once. Bucket BOTH green tiers by merge-lane (tag the UNSTABLE ones 🟡 + their failing check):
- **✅ Your lane (merge now)** — agentpik-backend/frontend, reeve-agents, reeve-remotion (all clean PRs), + reeve-services / reeve-frontend **substrate only** (exclude Studio-labeled or `studio_*`-touching), + your own utility repos (Matt-Sandbox, buildwithreeve-*, freya-monitor).
- **⛔ Studio (green but team's lane)** — reeve-services/reeve-frontend greens that ARE Studio. List so Matt knows they're ready, but they're the team's to merge.
- **◽ Freya — your call** — writing-partner-* greens targeting `develop` (case-by-case merges).
- **🔑 Cohort unblockers** — writing-partner-* greens targeting `main` (stack roots whose merge retargets+unblocks children; Jesus's lane, but flag prominently since they gate the whole cohort).

Annotate any stack-parent green with the merge procedure (merge WITHOUT `--delete-branch` → retarget child → delete branch). If the greens set is empty this sweep, say "no greens this sweep." This block REPLACES the old "Likely merge / Held back" lines as the report's lead — the clean-list below is now just the per-PR blurbs.

**Cleanup debt (surface only — this sweep is unattended, do NOT resolve it).** The careful hook defers unrecognized `rm -r` deletes to a queue during unattended runs. Check the count and surface it so Matt clears it when he's back (via `/cleanup`, `/wrapup`, or `/PRlaunch`):
```bash
python3 ~/.claude/hooks/cleanup-sweep.py --count
```
If `>0`, add a line to the report: `_🧹 Cleanup: N delete(s) pending — run /cleanup to clear._` Never run the deletes here (no human to approve ⚠ items).

**Clean-list section (every sweep).** After the action table, list every CLEAN PR with a one-line blurb of what it is (title + ticket + one phrase of substance — enough for Matt to merge-judge without opening it). Then call out which ones Matt can LIKELY MERGE, per standing repo policy:

| Repo | Likely-merge policy |
|---|---|
| agentpik-backend / agentpik-frontend | ✅ all clean PRs |
| reeve-agents | ✅ all clean PRs |
| reeve-remotion | ✅ all clean PRs |
| reeve-services | ✅ substrate only — **no Studio** (Studio-labeled/`studio_*`-touching PRs are the team's lane) |
| reeve-frontend | ✅ substrate only (same Studio carve-out) |
| writing-partner-* (Freya) | ❌ not in the likely-merge list by default — feature→develop merges are Matt's call case-by-case; never feature→main (Release PRs are Jesus's) |

Format: a "**Likely merge:**" line naming the qualifying PRs, and a "**Held back:**" line naming clean-but-excluded ones with the one-word reason (studio / freya / stacked-parent / CI-not-green). Annotate stack parents with the merge procedure (merge WITHOUT `--delete-branch` → retarget child → delete branch).

End with one of these summary lines:
- **`ACTIVE FIXES`** — N PRs got fixes this iteration. Pending work remains; loop continues.
- **`WAITING ON CR`** — N PRs pending in the CR queue (rate-limited / triggered / awaiting review). Loop continues to drain them.
- **`NEEDS HUMAN`** — N PRs have findings requiring Matt's judgment (architectural / product / declined false-positive). Flag prominently. These are TERMINAL — they do NOT by themselves keep the loop alive.
- **`AUTO-STOPPED (queue drained)`** — every non-draft PR is now TERMINAL (CLEAN or NEEDS_HUMAN); nothing left to bump or fix. Cron cancelled (see Step 6). Re-arm with `/babysit-prs` when new PRs or CR feedback land.
- **`AUTO-STOPPED (stalled)`** — the queue still has pending PRs but hasn't moved for `STALL_LIMIT` consecutive sweeps (CR likely down / hard-rate-limited). Cron cancelled to avoid runaway polling; re-arm once CR is moving again.

## Step 6 — Update state + auto-stop ONLY when the queue is drained (or stalled)

The loop's job is to **drain the PR queue**. It stays armed as long as there's pending work it can advance (fix or bump), and stops only when there's genuinely nothing left to do — NOT after some count of "quiet" sweeps. A sweep where you only bumped (no fix) is still valuable if the queue is moving.

**6a. Classify every non-draft PR from Step 2 as PENDING or TERMINAL:**
- **PENDING** (the loop can still advance it): `HAS_ACTIONABLE`, `RATE_LIMITED`, `NO_REVIEW_YET`, `TRIGGERED_WAITING`, or a re-review in-flight (you pushed a fix or bumped it this session and CR hasn't responded yet).
- **TERMINAL** (the loop can't advance it further): `CLEAN` (reviewed, resolved / ready-to-merge) or `NEEDS_HUMAN` (only Matt can act — architectural finding, product decision, or a false-positive you've verified + declined).

`PENDING_COUNT` = number of pending PRs this sweep.

**6b. Build a progress fingerprint** — a stable signature of the queue, so "did anything move?" is objective. One line per non-draft PR, `repo#num:STATE:<latest-CR-activity-timestamp>`, sorted, hashed:

```bash
# PR_LINES = array of "repo#num:STATE:<latest CR review-or-issue-comment ISO ts>" you built during the sweep
FINGERPRINT=$(printf '%s\n' "${PR_LINES[@]}" | LC_ALL=C sort | shasum | cut -d' ' -f1)
```

Any state change, any new CR activity (review/comment), any added or removed PR, or any fix you pushed moves the fingerprint. A re-trigger (bump) is NOT itself progress — only its *effect* (a state change or new CR activity that moves the fingerprint) counts. This is what lets the loop ride out CR's 3/hr pacing: while PRs keep clearing or new ones arrive, the fingerprint keeps moving and the loop stays alive to drain them.

**6c. Decide:**

```bash
STALL_LIMIT=12   # consecutive sweeps with a frozen queue before giving up (≈12h on the hourly cron)

if [ "$PENDING_COUNT" -eq 0 ]; then
  DECISION="DRAINED"                       # every PR is CLEAN or NEEDS_HUMAN → converged
  STREAK=0
elif [ "$FIXES_PUSHED_THIS_ITER" -gt 0 ] || [ "$FINGERPRINT" != "$PREV_FINGERPRINT" ]; then
  DECISION="PROGRESSING"; STREAK=0         # queue moved this sweep → stay armed, reset stall counter
else
  STREAK=$(($PREV_STREAK + 1))             # frozen: same queue, no new CR activity, no fix
  if [ "$STREAK" -ge "$STALL_LIMIT" ]; then DECISION="STALLED"; else DECISION="PROGRESSING"; fi
fi

NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "{\"pending_fingerprint\":\"$FINGERPRINT\",\"no_progress_streak\":$STREAK,\"pending_count\":$PENDING_COUNT,\"last_iter_at\":\"$NOW\"}" > /tmp/babysit-prs-state.json
```

**6d. Act on the decision:**

- **PROGRESSING** → leave the cron armed; print the normal summary line (`ACTIVE FIXES` / `WAITING ON CR` / `NEEDS HUMAN`). The loop fires again next hour and keeps draining. **This is the default while any PR is pending — do NOT stop just because a sweep pushed no fix.**
- **DRAINED** or **STALLED** → AUTO-STOP:
  1. `CronList` → find the job whose prompt is exactly `/babysit-prs`.
  2. If found, `CronDelete` it. If none found (manual invocation), note "no recurring cron found — invocation was manual."
  3. `rm -f /tmp/babysit-prs-state.json` so a future re-arm starts fresh.
  4. Report the matching line:
     - **DRAINED** → `AUTO-STOPPED (queue drained)` — "Every non-draft PR is CLEAN or NEEDS_HUMAN; nothing left to bump or fix. Re-arm with `/babysit-prs` when new PRs or CR feedback land." List any NEEDS_HUMAN PRs so Matt knows what's waiting on him.
     - **STALLED** → `AUTO-STOPPED (stalled)` — "$STREAK consecutive sweeps with zero queue movement (CR likely rate-limited/down or PRs frozen). $PENDING_COUNT still pending: <list>. Paused to avoid runaway polling; re-arm with `/babysit-prs` once CR is moving again."

The only ways the loop stops: the queue drains, or it freezes for `STALL_LIMIT` sweeps. A backlog that's steadily clearing at CR's 3/hr — or one Matt keeps adding to — keeps the loop alive, which is the point: truly progress the queue.

</process>

<hard_rules>
- NEVER `gh pr merge` — that's Matt's call
- NEVER `git push --force` (use --force-with-lease when rebasing)
- NEVER touch DRAFT PRs (intentionally not CR-ready)
- NEVER skip pre-commit hooks (--no-verify) unless Matt has explicitly authorized in this session
- NEVER apply a CR fix you don't understand — note + skip
- NEVER re-trigger CR on a PR more than once per loop iteration
- NEVER cross repos for a single fix — if a CR finding asks for changes in repo B while you're working in repo A, file a note and stop
- If a worktree doesn't exist for a branch, create a sibling at `/tmp/<repo>-<branch-short>` rather than disrupting whatever's checked out in main
- Symlink node_modules from the main checkout when working in a sibling worktree (saves install time). For Python repos there is NO venv in the worktree and bare `python` is not on PATH — run pytest via `/Users/mattrhodes/Coding_MM/<repo>/.venv/bin/python -m pytest` (the main checkout's interpreter).
- A behavioral source change (new guard/branch/return-contract) MUST be validated by RUNNING the affected test suite — `ast.parse` (syntax-only) does NOT catch it. If you cannot run the suite (no interpreter resolves), do NOT push the change; report "unvalidated, skipped". This is how PR #419 shipped a red suite — ast.parse passed, the suite never ran.
- If pytest / tsc fails on a fix you applied — OR your fix surfaces a failure in a pre-existing test — revert that file and skip — don't ship broken fixes
- NEVER re-arm the cron yourself after AUTO-STOPPED — Matt must explicitly re-invoke `/loop 1h /babysit-prs`. Auto-stop exists to prevent runaway polling; auto-restart would defeat the point.
- NEVER run `coderabbit review` (the CLI) outside the Step 4.5 quiet-guard — it burns Matt's separate quota and clashes with his terminal usage. If `is_matt_quiet` returns `no:...`, the entire CLI step is skipped.
- NEVER launch a CLI run on a PR that already has a live `/tmp/cli-<repo>-<pr>.pid` — that's already in flight from a prior sweep. Wait for it to complete and harvest next sweep.
- NEVER post a CLI-findings PR comment on a `MERGED` or `CLOSED` PR — re-check `gh pr view --json state` immediately before commenting (Jesus may have merged mid-CLI-run).
- NEVER use the CLI to "re-review" a PR cloud already covers (base `main`/`develop`) — burns quota Matt could use on his own work. CLI is for cloud-rejected stacked PRs only.
- NEVER report a PR as a green (🟢 OR 🟡 / mergeable) without inspecting its failing checks. `mss == UNSTABLE` is NOT a license to call it cosmetic — a non-required test workflow fails as `UNSTABLE`, not `BLOCKED`. Run the 🟡 GATE: if any failing check matches `pytest|CI|test|build|lint|codegen|tsc|mypy|check`, it is RED — exclude from greens, route to Step 4.7 (rebase if stale) / 4.6 / NEEDS_HUMAN, and list it under 🔴 RED-CI. (2026-06-16: 7 reeve-services PRs sat silently broken for a full day because the greens classifier bucketed UNSTABLE+pytest-red as cosmetic 🟡 — root cause was they were 44-51 commits behind main; `gh pr update-branch` greened them.)
- Every sweep MUST surface a 🔴 RED-CI count (PRs with a genuinely-failing test/build check). If >0, that's PROGRESSING work (rebase/triage), never silently folded into greens or ignored.
- The per-PR classify helpers MUST retry-on-empty. Under the parallel `xargs -P` burst, `gh pr view`/`gh api` transiently return `""` — and an empty response silently collapses every field to `"?"` (drops the PR from greens/gate) or mis-tags a CLEAN PR as `NO_CR` (feeds wrong bump targets). `babysit-one.sh`/`classify_full.sh` retry each gh call up to 3× and only trust an empty result after retries confirm it (a successful `gh api` prints at least `[]`; a transient failure prints nothing — that distinction is what tells real-NO_CR from a miss; on total failure emit `FETCH_FAIL`/`?`, NEVER `NO_CR`/green). (2026-06-17: the burst under-reported 24 greens as 2 and hid #700's pytest FAILURE for a sweep.) BELT-AND-SUSPENDERS: compute the GREENS block from an **authoritative low-concurrency recompute** (`-P 5`, per-PR `state,mergeable,mergeStateStatus,baseRefName` + statusCheckRollup incl. the `CodeRabbit` check = SUCCESS for CR-clean) rather than trusting the fast burst's TSV for anything you report as mergeable.
</hard_rules>

<loop_safety>
This command self-arms an hourly cron on first invocation (see Step 0b) — just typing `/babysit-prs` is enough to start the recurring sweep. Each invocation is stateless re: PR state (re-derive everything from `gh` API + git) but persists a tiny queue-state file at `/tmp/babysit-prs-state.json` (pending fingerprint + no-progress streak) so it can detect convergence and auto-stop (see Step 0 + Step 6).

To opt out of auto-arming and just run once, invoke as `/babysit-prs no-loop` (or any single-arg variant that doesn't match a repo-list pattern; the skill detects the opt-out flag in Step 0b).

**Convergence rule (queue-drain, not quiet-count):** the loop stays armed as long as ≥1 non-draft PR is PENDING (`HAS_ACTIONABLE` / `RATE_LIMITED` / `NO_REVIEW_YET` / `TRIGGERED_WAITING` / in-flight re-review) — because there's still queue to progress. It auto-stops in exactly two cases:
1. **Drained** — every non-draft PR is TERMINAL (`CLEAN` or `NEEDS_HUMAN`). Nothing left to bump or fix.
2. **Stalled** — pending PRs remain but the queue fingerprint hasn't moved for `STALL_LIMIT` (12 ≈ 12h on the hourly cron) consecutive sweeps (no fix pushed, no state change, no new CR activity). This is the only anti-runaway guard; it fires when CR is genuinely down/hard-blocked, never on a queue that's steadily clearing.

A sweep that only bumps (no fix-commit) does NOT count against the loop — bumping IS how the queue drains under CR's 3/hr pacing. Only a *frozen* queue (the stall-guard) or a *drained* one stops it.

When you see `NEEDS HUMAN`: flag prominently. NEEDS_HUMAN PRs are TERMINAL — they don't keep the loop alive on their own (if every other PR is CLEAN, the queue counts as drained and the loop stops, leaving the human items for Matt). The point of NEEDS HUMAN is to escalate, not to spin.

Watch the clock: each sweep should finish in <10 min. If you're hitting rate-limit walls or repeated test failures, report status and let the next hourly sweep continue rather than thrashing within one run.
</loop_safety>

<context>
Repo override (if any): $ARGUMENTS

Active session memory:
- `coderabbit-pro-rate-limit` — CR Pro tier hits per-hour ceilings; pace re-triggers
- `wait_for_coderabbit` — leave PRs open 5-10 min for inline review before declaring "no findings"
- `feedback_specs_live_in_linear` — don't write spec docs to docs/superpowers/specs/ — Linear only
</context>
