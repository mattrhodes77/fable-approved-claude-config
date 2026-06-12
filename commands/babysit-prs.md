---
name: babysit-prs
description: Sweep ALL your open PRs across the org, address actionable automated-review findings, re-trigger reviews if needed, report status. Designed to recur hourly (self-arming cron) until the queue drains.
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

<!--
ADAPT BEFORE USE — three things are yours to configure:
1. ORG: replace `YOUR_ORG` with your GitHub org (or drop --owner for personal repos).
2. REVIEWER: written for CodeRabbit (cloud bot + `coderabbit` CLI). Another review bot
   works if it (a) posts inline review comments and (b) can be re-triggered by a comment.
3. MERGE-LANE POLICY: the clean-list table in Step 5 — which repos' clean PRs you'll
   merge yourself vs. which belong to teammates. Ours is redacted; write your own.
-->

<objective>
Cycle through ALL of your open PRs across the org. For each PR with new reviewer feedback, apply the fix and push. For PRs stuck in the review queue, re-trigger if appropriate. Report a tight status table at the end. Loop-safe — designed for hourly invocation; it arms its own cron and auto-stops when the queue drains.

Hard scope: NEVER merge PRs, NEVER push --force without --force-with-lease, NEVER touch DRAFT PRs.
</objective>

<repos>
Default: sweep the entire org for PRs you authored. No hardcoded repo list — uses GH search.

Optional argument: comma-separated repo NAMES to filter to a subset:
- `/babysit-prs api-service,web-app` — only sweep those two
- `/babysit-prs` (no arg) — sweep ALL repos in the org where you have open PRs
</repos>

<process>

## Step 0 — Load iteration state + auto-arm cron if missing

**0a. Load the queue state** from `/tmp/babysit-prs-state.json`:

```json
{ "pending_fingerprint": "ab12cd…", "no_progress_streak": 1, "pending_count": 7, "last_iter_at": "2026-05-20T07:13:00Z" }
```

Rules:
- If the file doesn't exist, treat as `no_progress_streak = 0`, `pending_fingerprint = ""`.
- If `last_iter_at` is more than 6h old, treat as a fresh session — reset the streak and ignore the stored fingerprint (the prior session ended; state is stale).
- Hold `no_progress_streak` and `pending_fingerprint` in mind; you'll recompute them in Step 6 and decide whether to auto-stop.

```bash
jq -r '"\(.no_progress_streak // 0)\t\(.pending_fingerprint // "")"' /tmp/babysit-prs-state.json 2>/dev/null || printf '0\t\n'
```
Do NOT skip it.

**0b. Silent auto-arm of the hourly cron.** This skill is always meant to recur hourly. Call `CronList` and check whether any job has `prompt == "/babysit-prs"` and is recurring.

- If one exists: do nothing (already armed).
- If none exists: call `CronCreate` with `cron: "7 * * * *"` (off the :00 minute mark to avoid fleet bunching), `prompt: "/babysit-prs"`, `recurring: true`. Note in the final report's opening line: "_Auto-armed hourly cron `<id>` — stays alive while the PR queue has pending work; auto-stops only when drained (or stalled)._"

Opt-out: if `$ARGUMENTS` is the literal string `no-loop`, skip 0b — the explicit one-shot escape hatch. Any other arg is treated as the repo filter (Step 1).

## Step 1 — Discover all open PRs you authored across the entire org

ONE call gives every open PR across every repo:
```bash
gh search prs --owner YOUR_ORG --author "@me" --state open \
  --json repository,number,title,url,isDraft,labels --limit 100
```

Note: `gh search prs` does NOT return `headRefName` or `mergeable`. When you need the branch name or mergeability for a specific PR, fetch with `gh -R <owner>/<repo> pr view <pr> --json headRefName,mergeable,mergeStateStatus`.

If $ARGUMENTS is non-empty, parse comma-separated repo names and filter the result set (compare `.repository.name` case-insensitively).

Skip ALL PRs where `isDraft == true`. Print the post-filter count at the top of the run.

## Step 2 — Per PR, classify reviewer state

Skip these BEFORE classification:
- `isDraft == true`
- Title starts with `[WIP`, `WIP:`, or contains "don't merge"
- Labels contain `do-not-merge`, `wip`, `dnr`, or similar

For each survivor, pull:
```bash
# Latest bot review timestamp
gh api repos/<owner>/<repo>/pulls/<pr>/reviews \
  --jq '[.[] | select(.user.login | test("coderabbit"; "i"))] | sort_by(.submitted_at) | last'

# Inline review comments
gh api repos/<owner>/<repo>/pulls/<pr>/comments \
  --jq '[.[] | select(.user.login | test("coderabbit"; "i"))]'

# Most recent issue-level bot comment (walkthrough / rate-limit / no-actionable msg)
gh api repos/<owner>/<repo>/issues/<pr>/comments \
  --jq '[.[] | select(.user.login | test("coderabbit"; "i"))] | sort_by(.created_at) | last'

# Branch name + last commit timestamp
gh -R <owner>/<repo> pr view <pr> --json headRefName,mergeable,mergeStateStatus
gh api repos/<owner>/<repo>/commits/<branch> --jq '.commit.committer.date'
```

Classify into ONE of these states:

- **CLEAN** — latest issue body contains "No actionable comments were generated" OR all inline comments are older than the last push on the branch (= already addressed)
- **HAS_ACTIONABLE** — inline review comments exist that are NEWER than the last push to the branch's head commit
- **RATE_LIMITED** — latest issue body contains "Rate limit exceeded"
- **NO_REVIEW_YET** — no review or inline comments at all
- **TRIGGERED_WAITING** — last issue body is "Review triggered" + older than 30 min + no inline comments since

## Step 3 — Act per state

### CLEAN
Report only. Note "ready to merge" if mergeable + checks green.

### HAS_ACTIONABLE
For each inline comment NEWER than the last push:

1. Find the right worktree. Use `git worktree list` in the repo's main checkout. If the branch isn't checked out, create a sibling worktree at `/tmp/<repo>-<branch-short>`.
2. Apply the suggested fix EXACTLY when it's mechanical (regex change, min/max bound, missing validation, etc.).
3. If the finding requires architectural judgment ("refactor X to Y", "rename Z"), skip — leave it for human review. Note in the report.
4. After applying all fixes — **a syntax check is NOT validation. If the fix changes runtime behavior of source-under-test, you MUST run the affected test suite, not just parse it.**
   - TypeScript: `npx tsc --noEmit` must pass.
   - Python: `python3 -c "import ast; ast.parse(open('<file>').read())"` per touched file is the syntax gate only.
   - **If the fix touches a test file OR a source file that has a sibling test module → RUN that test module.** A behavioral source change (new guard/branch/return contract) is exactly what a syntax check misses — a real incident: a one-line `if not sent: continue` fix parsed fine, broke 4 existing tests whose mock returned `None`, and shipped a red suite because only the syntax gate ran. Map source→test by convention; if unsure, run the whole nearest test dir.
   - **Sibling worktrees have NO venv and bare `python` may not be on PATH.** Run pytest via the main checkout's interpreter: `<main-checkout>/.venv/bin/python -m pytest <test_path> -q` (resolution order: worktree `.venv` → sibling main-checkout `.venv` → `python3`). If NONE resolves, you **cannot validate** — do NOT push a behavioral change; report "unvalidated, skipped".
   - If the suite has ANY failure (including ones your fix surfaced in pre-existing tests), revert and skip — never push a red suite.
5. Commit with message: `fix(CR PR #<N>): <one-line summary of what was addressed>`
6. Push: `git push origin <branch>` (NOT --force; if rebase needed, use --force-with-lease and document in commit message)
7. The bot will auto-re-review on the new commit (no manual trigger needed when commits land).

### RATE_LIMITED
Check the wait time mentioned in the body. If the wait has passed (= last issue comment > 1hr old), post `@coderabbitai review` to bump. Otherwise skip — too soon.

### NO_REVIEW_YET
Check PR age. If <30 min old, skip (the bot just hasn't run yet). If >30 min old, post `@coderabbitai review` to bump.

### TRIGGERED_WAITING
If the last "Review triggered" ack is >60 min old AND no new review/inline since, post one more `@coderabbitai review`. Otherwise skip (still in queue).

## Step 4 — Rate-limit awareness + liveness guard

Review bots hit hourly per-user ceilings. Don't post `@coderabbitai review` on more than 3 PRs per invocation. If more than 3 PRs need a re-trigger, queue the rest with a note "deferred to next loop iteration".

**Liveness re-check before every bump.** Your team's merger may merge in bursts — often mid-sweep — so a PR you classified minutes ago may already be merged/closed. Immediately before posting a bump, confirm the PR is still open:
```bash
gh -R <owner>/<repo> pr view <pr> --json state -q .state   # must be "OPEN"
```
If it's `MERGED`/`CLOSED`, skip the bump and note it dropped off. Same applies before starting a fix.

## Step 4.5 — Reviewer-CLI supplement (separate quota; runs only when the owner is quiet)

The cloud bot's quota and the local `coderabbit` CLI are SEPARATE buckets (~3/hr for the CLI). The CLI is the only way to get review on **stacked PRs the cloud bot rejects** ("Auto reviews disabled on base/target branches"). The loop uses the CLI as a supplement under two strict guards:

1. **Quiet guard.** Don't run the CLI when the owner is actively working — they use the same quota from their own terminal.
2. **Backgrounded.** Each CLI review takes 5–10 min, which would blow the <10 min sweep budget. Launch in background; harvest results in the *next* sweep.

### 4.5a — Quiet detection

```bash
is_owner_quiet() {
  # Always-quiet window: 02:00–09:00 local (adjust TZ to the owner's)
  local hour=$(TZ=America/Los_Angeles date +%H)
  if [ "$hour" -ge 2 ] && [ "$hour" -lt 9 ]; then echo "yes:off-hours"; return; fi

  # Any commit by the owner across local checkouts in the last 30 min?
  local cutoff=$(date -u -v-30M +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '30 minutes ago' +%Y-%m-%dT%H:%M:%SZ)
  for repo in ~/code/*/; do                      # adjust to your checkout root
    [ -d "$repo/.git" ] || continue
    if git -C "$repo" log --since="$cutoff" --author="$(git config user.email)" -1 --oneline 2>/dev/null | grep -q .; then
      echo "no:recent-commit-in-$(basename $repo)"; return
    fi
  done
  echo "yes:no-recent-activity"
}
```

If the first word is `no:...`, **skip this entire step**. (Some launches will still rate-limit if the owner happens to run the CLI concurrently — expected; handled in 4.5d.)

### 4.5b — HARVEST first: process completed CLI runs from prior sweeps

For each `/tmp/cli-*.pid` file: if the PID is no longer running AND the matching `/tmp/cli-*.out.json` contains a `{"type":"complete"}` line, the run is done. Record `repo=`/`pr=`/`branch=`/`wt=` in a `/tmp/cli-<id>.meta` sidecar at launch time rather than parsing them out of the filename (hyphenated repo names make filename parsing fragile).

### 4.5c — For each completed CLI run

1. **Re-check OPEN.** If the PR was merged mid-run, skip — don't comment on a merged PR.
2. **Parse findings** from the NDJSON: `jq -c 'select(.type=="finding")' "$out"` — each has `severity`, `file`, `line`, `title`, `description`.
3. **Apply mechanical fixes** per Step 3's HAS_ACTIONABLE rules (quick wins only; skip heavy-lift and judgment items). Use the sibling worktree created at launch time.
4. **Commit + push** if any fixes applied, message `fix(CR CLI #<N>): <summary>` so PR history reflects this came from the CLI, not cloud.
5. **Post a PR comment** with the full findings:

   ```markdown
   ## 🤖 Reviewer CLI run (local)

   _The cloud bot isn't reviewing this PR (auto-review disabled on non-default base). The babysit-prs loop ran the review CLI locally and surfaced these findings._

   **N findings** · M auto-fixed · K left for human review

   ### 🔴 Critical / Major
   - **`<file>:<line>`** — <title>
     <description>

   ### 🟡 Minor / Nitpick
   ...
   ```

### 4.5d — LAUNCH new CLI runs (up to 3 minus in-flight)

After harvesting, count in-flight (`ls /tmp/cli-*.pid 2>/dev/null | wc -l`). Launch up to `3 - in_flight` new runs.

**Target selection** (bottom-up by stack depth):
- ✅ Stacked PRs where the latest bot comment is "Auto reviews are disabled on base/target branches" AND inline count == 0 AND no pid file already exists.
- ❌ Skip default-base PRs — the cloud bot reviews those when its budget recovers; don't burn CLI quota.
- ❌ Skip PRs you've already fix-pushed this sweep — let cloud handle the re-review.

**Per launch** (background, non-blocking):

```bash
launch_cli_review() {
  local repo="$1" pr="$2"
  [ "$(gh -R YOUR_ORG/$repo pr view $pr --json state -q .state)" = "OPEN" ] || return
  local meta=$(gh -R YOUR_ORG/$repo pr view $pr --json headRefName,baseRefName)
  local head=$(echo "$meta" | jq -r .headRefName)
  local base=$(echo "$meta" | jq -r .baseRefName)

  cd ~/code/"$repo"                               # adjust to your checkout root
  git fetch origin "$head" "$base" --quiet 2>/dev/null
  local slug=$(echo "$head" | sed 's|[/.]|-|g')
  local wt="/tmp/$repo-$slug-cli"
  [ -d "$wt" ] || git worktree add "$wt" "origin/$head" --detach 2>&1 | tail -1

  local id="cli-$repo-$pr"
  local out="/tmp/$id.out.json"
  printf 'repo=%s\npr=%s\nbranch=%s\nbase=%s\nwt=%s\nstarted=%s\n' \
    "$repo" "$pr" "$head" "$base" "$wt" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "/tmp/$id.meta"

  # macOS has no GNU `timeout` by default — launch the CLI DIRECTLY so $! is the
  # actual child PID. Detect hangs on the next sweep via the meta started= age
  # (kill if older than 15 min). Use --base-commit with the explicit merge-base
  # (avoids stale-local-base mis-scoping).
  local mb=$(git -C "$wt" merge-base HEAD "origin/$base")
  ( cd "$wt" && coderabbit review --agent --base-commit "$mb" > "$out" 2>&1 ) &
  echo $! > "/tmp/$id.pid"
}
```

### 4.5e — Failure modes

- **CLI rate-limit**: output contains `"errorType":"rate_limit"` — bail this sweep's CLI work, don't launch more.
- **Auth expired**: output contains `"errorType":"auth"` — note in report ("CLI auth expired — run `coderabbit auth login`"). Skip CLI this sweep.
- **CLI hangs**: at harvest time, if the PID is alive AND `started=` is >15 min old, `kill -TERM` (then `-KILL` after 5s), discard outputs, report skipped. If the PID died but the output lacks `{"type":"complete"}`, discard the same way (mid-run crash).
- **Worktree creation fails**: skip that PR, don't block others.

### 4.5f — Budget interaction with cloud bumps

Cloud bumps and CLI runs draw from **separate buckets**. Per sweep: up to **3 cloud bumps** (Step 4) + up to **3 CLI launches** (this step, only when quiet). A fully-active sweep can advance 6 PRs; the CLI half is async — its effects land in the *next* sweep's harvest.

---

## Step 5 — Report

Print one compact markdown table:

```
| Repo | PR # | Branch | State Before | Action Taken | State After |
|---|---|---|---|---|---|
| web-app | #49 | feat/kinetic-text | HAS_ACTIONABLE (4) | Fixed all 4, pushed | Waiting for re-review |
| api-service | #226 | feat/kinetic-text | TRIGGERED_WAITING | Re-triggered (no new since 1h+) | Waiting |
| api-service | #335 | feat/inventory-publish | STACKED_BLOCKED | **CLI launched** (background) | CLI run in flight |
| web-app | #50 | feat/cinematic-color | CLEAN | None | Ready to merge |
```

Open the report with one line on the CLI bucket:
`_CLI: quiet=<yes/no:reason> · harvested=<N> · launched=<N> · in-flight=<N>_`

**Clean-list section (every sweep).** After the action table, list every CLEAN PR with a one-line blurb (title + ticket + one phrase of substance — enough to merge-judge without opening it). Then call out which ones the owner can LIKELY MERGE, per a standing per-repo policy table you define for your team. Ours distinguishes repos where all clean PRs are the owner's to merge, repos where only certain subsystems are (the rest belong to teammates' lanes), and repos where merging is always a case-by-case human call. Encode yours, for example:

| Repo | Likely-merge policy |
|---|---|
| `<repo-a>`, `<repo-b>` | ✅ all clean PRs |
| `<repo-c>` | ✅ subsystem X only — subsystem Y PRs are the team's lane |
| `<repo-d>` | ❌ never auto-suggest — release flow owned by a teammate |

Format: a "**Likely merge:**" line naming the qualifying PRs, and a "**Held back:**" line for clean-but-excluded ones with a one-word reason (lane / stacked-parent / CI-not-green). Annotate stack parents with the merge procedure: merge WITHOUT `--delete-branch` → retarget child to the new base → delete branch. (`gh pr merge --delete-branch` on a stack parent auto-closes the child irrecoverably.)

End with one of these summary lines:
- **`ACTIVE FIXES`** — N PRs got fixes this iteration. Pending work remains; loop continues.
- **`WAITING ON REVIEW`** — N PRs pending in the review queue. Loop continues to drain them.
- **`NEEDS HUMAN`** — N PRs have findings requiring the owner's judgment. Flag prominently. These are TERMINAL — they do NOT by themselves keep the loop alive.
- **`AUTO-STOPPED (queue drained)`** — every non-draft PR is TERMINAL (CLEAN or NEEDS_HUMAN). Cron cancelled (Step 6). Re-arm with `/babysit-prs` when new PRs or feedback land.
- **`AUTO-STOPPED (stalled)`** — pending PRs remain but the queue hasn't moved for `STALL_LIMIT` consecutive sweeps. Cron cancelled to avoid runaway polling.

## Step 6 — Update state + auto-stop ONLY when the queue is drained (or stalled)

The loop's job is to **drain the PR queue**. It stays armed as long as there's pending work it can advance, and stops only when there's genuinely nothing left — NOT after some count of "quiet" sweeps. A sweep where you only bumped (no fix) is still valuable if the queue is moving.

**6a. Classify every non-draft PR as PENDING or TERMINAL:**
- **PENDING**: `HAS_ACTIONABLE`, `RATE_LIMITED`, `NO_REVIEW_YET`, `TRIGGERED_WAITING`, or a re-review in-flight.
- **TERMINAL**: `CLEAN` or `NEEDS_HUMAN` (only the owner can act).

**6b. Build a progress fingerprint** — one line per non-draft PR, `repo#num:STATE:<latest-bot-activity-timestamp>`, sorted, hashed:

```bash
FINGERPRINT=$(printf '%s\n' "${PR_LINES[@]}" | LC_ALL=C sort | shasum | cut -d' ' -f1)
```

Any state change, new bot activity, added/removed PR, or pushed fix moves the fingerprint. A re-trigger (bump) is NOT itself progress — only its *effect* counts. This is what lets the loop ride out the bot's rate pacing: while PRs keep clearing, the fingerprint keeps moving and the loop stays alive.

**6c. Decide:**

```bash
STALL_LIMIT=12   # consecutive frozen sweeps before giving up (≈12h on the hourly cron)

if [ "$PENDING_COUNT" -eq 0 ]; then
  DECISION="DRAINED"; STREAK=0
elif [ "$FIXES_PUSHED_THIS_ITER" -gt 0 ] || [ "$FINGERPRINT" != "$PREV_FINGERPRINT" ]; then
  DECISION="PROGRESSING"; STREAK=0
else
  STREAK=$(($PREV_STREAK + 1))
  if [ "$STREAK" -ge "$STALL_LIMIT" ]; then DECISION="STALLED"; else DECISION="PROGRESSING"; fi
fi

NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "{\"pending_fingerprint\":\"$FINGERPRINT\",\"no_progress_streak\":$STREAK,\"pending_count\":$PENDING_COUNT,\"last_iter_at\":\"$NOW\"}" > /tmp/babysit-prs-state.json
```

**6d. Act on the decision:**

- **PROGRESSING** → leave the cron armed; print the normal summary line. **This is the default while any PR is pending — do NOT stop just because a sweep pushed no fix.**
- **DRAINED** or **STALLED** → AUTO-STOP: `CronList` → find the `/babysit-prs` job → `CronDelete` it → `rm -f /tmp/babysit-prs-state.json` → report the matching `AUTO-STOPPED` line. For DRAINED, list any NEEDS_HUMAN PRs so the owner knows what's waiting on them.

</process>

<hard_rules>
- NEVER `gh pr merge` — that's the owner's call
- NEVER `git push --force` (use --force-with-lease when rebasing)
- NEVER touch DRAFT PRs (intentionally not review-ready)
- NEVER skip pre-commit hooks (--no-verify) unless explicitly authorized this session
- NEVER apply a reviewer fix you don't understand — note + skip
- NEVER re-trigger review on a PR more than once per loop iteration
- NEVER cross repos for a single fix — if a finding asks for changes in repo B while you're working in repo A, file a note and stop
- If a worktree doesn't exist for a branch, create a sibling at `/tmp/<repo>-<branch-short>` rather than disrupting whatever's checked out in main
- Symlink node_modules from the main checkout when working in a sibling worktree. Python worktrees have NO venv — run pytest via the main checkout's interpreter.
- A behavioral source change MUST be validated by RUNNING the affected test suite — a syntax check does not catch it. If you cannot run the suite, do NOT push the change.
- If tests/tsc fail on a fix you applied — OR your fix surfaces a failure in a pre-existing test — revert that file and skip
- NEVER re-arm the cron yourself after AUTO-STOPPED — the owner must explicitly re-invoke. Auto-stop exists to prevent runaway polling; auto-restart would defeat the point.
- NEVER run the reviewer CLI outside the Step 4.5 quiet-guard — it burns the owner's quota and clashes with their terminal usage
- NEVER launch a CLI run on a PR that already has a live pid file — already in flight from a prior sweep
- NEVER post CLI findings on a `MERGED`/`CLOSED` PR — re-check state immediately before commenting
- NEVER use the CLI to "re-review" a PR the cloud bot already covers (default-base PRs) — CLI is for cloud-rejected stacked PRs only
</hard_rules>

<loop_safety>
This command self-arms an hourly cron on first invocation (Step 0b) — typing `/babysit-prs` once is enough to start the recurring sweep. Each invocation is stateless re: PR state (re-derive everything from `gh` + git) but persists a tiny queue-state file so it can detect convergence and auto-stop.

**Convergence rule (queue-drain, not quiet-count):** the loop stays armed as long as ≥1 non-draft PR is PENDING. It auto-stops in exactly two cases: the queue **drains** (every PR is CLEAN or NEEDS_HUMAN), or it **stalls** (`STALL_LIMIT` consecutive sweeps with zero queue movement — the only anti-runaway guard; it fires when the bot is genuinely down, never on a queue that's steadily clearing).

NEEDS_HUMAN PRs are TERMINAL — they don't keep the loop alive on their own. The point of NEEDS HUMAN is to escalate, not to spin.

Watch the clock: each sweep should finish in <10 min. If you're hitting rate-limit walls or repeated test failures, report status and let the next hourly sweep continue rather than thrashing within one run.
</loop_safety>

<context>
Repo override (if any): $ARGUMENTS
</context>
