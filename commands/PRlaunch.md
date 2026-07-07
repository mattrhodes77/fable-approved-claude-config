---
description: Ship session work — local deep-review → local CR CLI → live outcome-eval → open PR → wrapup
---

# PR Launch

Take everything worked on in this session through three local quality gates (no PR yet), then open the PR, then wrap up.

**All three quality gates run LOCALLY on the working tree / branch.** No draft PRs, no cloud CodeRabbit in the inner loop. The PR is only opened once the local gates are clean. ("CR CLI" below = the CodeRabbit CLI — substitute your secondary automated reviewer.)

The three gates catch different bug classes and are NOT redundant:
- **Deep review** grades the *diff* — correctness, security, architecture. ("Will this overwrite the DB before the user accepts?")
- **CR CLI** grades the *repo* — style, nits, patterns.
- **Outcome eval** grades the *running product from the user's seat* — does the feature actually do the useful thing, and does the output look right? This is the gate that catches the "basic stuff that always gets missed": raw markdown shown to users, an LLM that asks for context it already has, a spinner that never resolves, a layout that breaks at the real viewport. None of those are visible in a diff. You only find them by running the thing and **reading what the user gets**.

> ### The re-gate rule (non-negotiable — this is what "all green" means)
> **Any code change in a later gate invalidates the earlier gates.** A gate's green is only valid for the exact code it ran against. If gate N (or the eval, or a fix you make anywhere) edits code, every earlier gate's result is stale — re-run the gates that the change could affect, on the new code, before finalizing.
> - Fixed a deep-review finding during CR CLI? → re-run deep-review's relevant check on the new diff.
> - The outcome eval found a bug and you changed code to fix it? → that code hasn't been deep-reviewed *or* CR-linted *or* re-tested. Run those on the fix, then re-run the eval scenario.
> - Trimmed a log line, fixed a lint nit, anything → at minimum re-run tests + lint on the final tree.
>
> **You cannot finalize (push/PR) until the FINAL committed tree — the exact bytes you're shipping — has passed every applicable gate.** "It was green earlier" is not "the final version is green." Match the re-run to the change: a logging-only or type-alias delta needs tests+lint+a smoke confirmation, not a full multi-agent re-review; a logic change needs the real gate. Proportionate, but never skipped. Phase 4 is the explicit checkpoint that enforces this before any push.

Use TodoWrite to track all 7 phases. Mark each done as you go.

---

## 0. Identify session work

Before anything else, list what we're shipping:

- Walk the conversation for repos + branches touched.
- Per repo: `git status --short`, `git log @{u}..HEAD --oneline` (or `git log origin/main..HEAD` if no upstream), `git branch --show-current`.
- Group changes into shippable units. Usually = one branch = one PR. If a branch spans multiple tickets, ask the owner whether to split.
- Make sure changes are committed to a feature branch (not sitting uncommitted, not on `main`). If uncommitted, commit them now — HEREDOC message, match repo style via `git log --oneline -5`, Co-Authored-By: Claude.
- Report the list back to the owner and confirm before proceeding: "I'm about to ship these N units through PRlaunch — confirm?"

**Ticket state at start-of-work is automatic if you run the tracker hooks.** The `linear-startwork.sh` PostToolUse hook flips a ticket to In Progress + assigns you the moment its ticket-token branch is created (and alerts instead of stealing if someone else holds it). So by the time you're here, each unit's ticket *should* already be In Progress + assigned. Per unit: confirm it is; if a unit's branch never carried a ticket token (so the hook never fired), the `pr-gate` link check (Phase 5) will block its PR anyway — locate/file the ticket now and either rename the branch or put `Closes <TICKET-ID>` in the body.

If there's no shippable work, stop and say so.

---

## 1. Deep review loop — LOCAL

For each unit, run the deep-review *process* (`~/.claude/commands/deep-review.md`) against the **local diff**:

```bash
git diff origin/main...HEAD       # what we're about to ship
git log origin/main..HEAD         # commits we're about to push
```

Apply the full deep-review checklist (correctness, security, runtime validation, empirical verification of claims). No PR number needed — the process is the process; you just point it at the local diff instead of a GitHub PR.

1. Categorize findings: severity (CRITICAL / HIGH / MEDIUM / LOW) **and origin** (IN-SCOPE / ADJACENT / OUT-OF-SCOPE — see the deep-review doc's origin table). Structural findings (v6.8 lens) default to MEDIUM and follow the same disposition flow; a missed-simplification suggestion is never blocking.
   - **Fix-placement check (one-brain rule):** if the diff fixes shared-substrate behavior on ONE surface/lane, that's a HIGH structural finding. Tripwires: (a) a service importing from a routers/BFF layer to borrow logic; (b) a new comment saying "mirror"/"parity"/"same as <other path>" — that's a copy of an existing implementation; (c) the fix description scoped "on the <connector/X> path" when other lanes share the bug class (we shipped a per-lane fix once and a sibling lane kept the bug). Disposition: move the fix to the shared layer, or — if extraction is genuinely too big for this PR — keep the copy AND file a consolidation ticket referenced in the PR body. A parity comment with no ticket is an unrecorded disposition; phase 6 should catch it.
2. Fix every IN-SCOPE CRITICAL + HIGH in the working tree. Discuss MEDIUM with the owner — fix or defer.
3. **Anything not fixed gets a recorded disposition — never silently dropped:** OUT-OF-SCOPE or deferred findings → **file a tracker ticket** (or batch into one), or waive with a one-line reason. Add each to the running disposition list (finding → fixed | ticket <id> | waived: reason) that phase 6 verifies. ADJACENT findings: fix opportunistically only if trivial (<5 lines, no risk), else ticket.
4. Commit fixes (separate commit, don't amend — easier to reason about).
5. Re-run the deep-review pass on the new diff.
6. Loop until CRITICAL + HIGH are zero or explicitly waived by the owner.
7. **Stamp the gate** — once deep-review is clean, record it against the current HEAD (run from inside the repo):

   ```bash
   ~/.claude/hooks/prlaunch-gate.sh record deep_review
   ```

   This writes the gate's HEAD sha into the ledger the `pr-gate` hook enforces; any commit after this stales the entry and you re-record it here (the re-gate rule, as code).

**Do not proceed to phase 2 until deep-review is clean.**

---

## 2. CR CLI loop — LOCAL

Local CodeRabbit CLI (it bypasses the cloud credit block, but its own budget is ~3 runs/hour):

```bash
cd <repo>
coderabbit review --base main --plain
```

(If repo's default branch isn't `main`, swap it. Check via `gh repo view --json defaultBranchRef -q .defaultBranchRef.name`.)

1. **Classify every finding by origin FIRST** (CR CLI reviews the WHOLE repo, not just your diff, so it surfaces pre-existing main-branch findings on files you never touched). Anchor on:
   ```bash
   git diff --name-only origin/main..HEAD    # the files actually in scope
   ```
   - **IN-SCOPE** — finding is in a file your diff changed → this PR owns it.
   - **OUT-OF-SCOPE** — finding is in a file your diff did NOT touch → pre-existing; never bundle the fix into this feature PR.
2. Filter known CR junk (build your own list; ours includes a `dependency_overrides try/finally` suggestion that's known-buggy, and opaque char-count flags on YAML tone fields):
   - Don't blindly apply CR's suggested patches; verify them first. CR's own severity/origin labels are unreliable (it has called `user_id == None` a CRITICAL — a false positive; SQLAlchemy compiles `== None` to `IS NULL`).
3. **Every finding gets an explicit disposition — nothing is silently dropped:**
   - IN-SCOPE actionable → fix in the working tree, commit.
   - IN-SCOPE nit → fix if trivial, else file a follow-up ticket.
   - OUT-OF-SCOPE but legitimate → **file a tracker ticket** (batch related findings into one ticket; assign the owner; link the source PR + the finding's `file:line`). This is the step that's easy to skip — don't. "Out of scope" means *track it elsewhere*, not *discard it*.
   - Junk / not-a-real-issue → waive with a one-line reason (recorded in the wrapup report).
   Maintain a running disposition list (finding → fixed | ticket <id> | waived: reason). Phase 6 verifies it.
4. Commit fixes.
5. Re-run `coderabbit review --base main --plain`.
6. Loop until every IN-SCOPE finding is fixed-or-ticketed and every OUT-OF-SCOPE finding is ticketed-or-waived.
7. **Limit-blown skip (authorized):** if CR is rate-limited or credit-blocked (explicit `rate_limit` error from `--agent` mode, or the dashboard shows credits exhausted — check BEFORE burning retries; also `pgrep -f 'coderabbit review'` and kill stale hung runs first — a hung CLI holds the slot), **skip this gate entirely**. Don't wait out long timers, don't retry more than once. Record `CR CLI: skipped — <rate limit|credits exhausted>` in the disposition list, say so in the PR body's Testing section, and let cloud CR on the pushed PR + `/babysit-prs` be the backstop. The other two gates still run in full. Record the skip in the gate ledger so the `pr-gate` hook's `cr_cli` entry is satisfied with a reason:

   ```bash
   ~/.claude/hooks/prlaunch-gate.sh record cr_cli --skipped "<rate limit|credits exhausted>"
   ```
8. **Stamp the gate** — on the clean (non-skip) path, once every finding is fixed/ticketed/waived, record it against the current HEAD:

   ```bash
   ~/.claude/hooks/prlaunch-gate.sh record cr_cli
   ```

**Do not proceed to phase 3 until CR CLI is clean *and* every finding has a recorded disposition — or the gate is recorded as limit-skipped.**

---

## 3. Outcome eval — LIVE, from the user's seat

The two gates above read code. This one **runs the product and grades what the user actually gets.** It exists because of a specific, repeated failure: the diff is perfect, both reviews pass, and the shipped feature still does something dumb the moment a human uses it — shows raw `**markdown**`, an LLM asks "which chapter?" when the chapter is open on screen, a spinner never resolves, the layout breaks at the real viewport width. **These are never visible in a diff.** They are only visible when you run the thing and read the output as the user.

> **Why this is a separate gate and not "just test it":** the failure mode isn't *not running* the feature — it's **running it and grading the wrong signal.** It is dangerously easy to watch `POST … 200`, check `db md5 unchanged`, see "a bubble appeared", and write ✅ — while the actual words on screen say "Which chapter are you working on?" (the bug) and the actual pixels show literal asterisks. Grading transport (status codes, payloads, DB hashes, "an element exists") is not grading outcome (the words/pixels a human receives). This gate forces outcome-grading.

### When it applies
Required when the unit touches a **user-facing surface**: UI, an AI/LLM response a human reads, an API response rendered to a user, copy, layout, or a multi-step flow. **A unit that produces or moves a binary artifact a human or a downstream render consumes — an image, video, PDF, generated file, an uploaded/mirrored asset — IS a user-facing surface even when the code reads like plumbing:** the artifact is the output, so you open it and look (step 5), you do not grade its checksum. If the unit is pure internal plumbing / test-only / config with **no human-readable or viewable output at all**, write one line — "no user-facing surface — outcome eval N/A" — and move on (phase 4 still applies) — and record the N/A in the ledger with a reason so the `pr-gate` hook's `outcome_eval` entry is satisfied (no scenarios needed): `~/.claude/hooks/prlaunch-gate.sh record outcome_eval --na "no user-facing surface"`. When in doubt, it applies — and "it's plumbing" is not a licence to skip the look when there's a file to look at.

### The method (in order — do not reorder)

1. **Write the scenarios and PASS criteria BEFORE running anything.** One scenario per user-facing behavior the PR adds or changes. Each criterion must be phrased as *what the user receives*, from their seat — not what the system did internally. Save them to the scenarios sidecar next to the ledger, then register it — this is the `pr-gate` precondition for recording `outcome_eval`:

   ```bash
   SCEN=~/.claude/prlaunch-ok/"$(basename "$(git rev-parse --show-toplevel)")--$(git branch --show-current | tr '/' '-')".scenarios.md
   # write your scenarios + PASS criteria into "$SCEN" (one per user-facing behavior), then:
   ~/.claude/hooks/prlaunch-gate.sh record scenarios "$SCEN"
   ```

   Writing them first is the whole point: it removes the post-hoc rationalization that lets a failing result get graded ✅.
   - ✅ "The assistant's reply names the open chapter or its content and does NOT ask which chapter."
   - ✅ "The chat bubble shows real bold; zero literal `**` or `#` in the rendered text."
   - ❌ "POST returns 200." ❌ "ui_context payload contains active_scene_id." ❌ "a bubble exists." ❌ "the mirrored file is byte-identical / `200` + `image/jpeg`." (All transport — necessary, not sufficient. A checksum or content-type match proves the bytes moved, not that the artifact is the *right* artifact.)
   - **Use a REAL representative input, not synthetic filler.** Eval a real brand / real document / real user record when one is reasonably obtainable — `picsum`, `lorem ipsum`, `foo@test.com`, an all-zeros blob can pass an eval the real input would fail (wrong format, size, encoding, content), and they leave junk in real buckets/DBs. If only synthetic input is feasible, say so and why.

2. **Run each scenario on the live local rig** (start the dev servers per the project's run skill; don't conflict with the owner's ports). Drive the real UI (Playwright/MCP) or hit the real endpoint.

3. **Read the actual artifact and judge it against the criterion — out loud, in your report.** For each scenario quote the real output: the words the LLM said, the rendered text content, what the screenshot shows. Then state PASS/FAIL with the evidence. "I read the reply: '…Which chapter are you rewriting?' → FAIL, it should know it's the open one." A scenario you can't quote the output for is not graded.

4. **For AI / LLM features, grade the response itself** (this is the recurring miss — LLM outputs are emergent from prompt+data and invisible to both code gates):
   - **Context awareness** — did it use the context it was given (the open scene, the selection, prior turns), or ask for something it already had?
   - **On-target** — did it address the actual thing (the selected passage, the named character), or answer generically?
   - **Voice/format** — does it read like the product's voice and render correctly (formatted, not raw markdown; no `#` headers dumped into a chat bubble)?
   - **No dead ends** — did the turn actually resolve in the UI (no stuck "thinking…" after the server returned), and is the result persisted/applied where the user expects?

5. **Visual pass — actually look.** Take screenshots (or read rendered `textContent`, not your own boolean asserts) of every changed surface. Look for: raw markdown, overlapping/stuck overlays, broken layout, truncated/garbled text. **Check the real viewport** — if the product is used at desktop width, eval at desktop width (a mobile-width harness silently turned a side panel into a full-screen dialog once and hid the surface under test). **This applies to any file/binary the change produces or moves, too** — open the image (Read it), play the clip, open the PDF, and describe what it actually *depicts*; a hash/size/`Content-Type` check is not a look. (Real incident: a brand-image mirror was graded ✅ on `200` + bytes-match over a random `picsum` filler photo nobody had opened — the round-trip was sound, the artifact was junk.)

6. **The annoyance test.** For each surface ask: *if I were the user and hit this, would I be annoyed or confused?* That question catches the "basic stuff" class better than any assertion. If yes → it's a finding, even if every status code was 200.

### Disposition
Every FAIL is a finding with the same disposition rules as phases 1–2: fix IN-SCOPE in the working tree and re-run the scenario; or ticket; or waive with a reason. **Loop until every scenario PASSES or is explicitly waived by the owner.** Add results to the running disposition list. A bug found here that you fix → commit it and, if it changed code the earlier gates judged, re-run the relevant gate on the new diff.

### Data safety
If the rig writes to a real local DB, snapshot the rows you'll touch first and restore + md5-verify after. Never leave eval residue in the owner's data.

Once every scenario PASSES (or is waived), **stamp the gate** against the current HEAD:

```bash
~/.claude/hooks/prlaunch-gate.sh record outcome_eval
```

**Do not proceed to phase 4 until every applicable scenario PASSES (or is waived) and you have quoted the real output for each.**

---

## 4. Re-gate checkpoint — confirm the FINAL tree is green

The gates ran in sequence, but each fix mutated the tree *after* an earlier gate judged it. Before pushing, prove the **exact bytes you're about to ship** are green — not "green at some point during the run." This phase is short when nothing changed late, and essential when the eval (or a CR fix) sent you back into code.

1. **List everything that changed since each gate last ran.** `git diff` the tree; look at every commit made during phases 1–3 plus any uncommitted working-tree edits. For each change, name which gate(s) it could invalidate.
2. **Re-run those gates on the final code, proportionate to the change** (per the re-gate rule above):
   - logic / behavior change → re-run the real gate (deep-review check, and/or the eval scenario it affects).
   - logging / types / comments / lint-nits → re-run **tests + lint** at minimum, plus a smoke confirmation of any path involved.
   - Always end with: tests green, lint/type-check clean, on the final tree.
3. **If the change touched a user-facing path, re-confirm the affected outcome-eval scenario** on the final code — quote the real output again. (A fix made "to satisfy the eval" is itself untested code; the eval isn't passed until it passes on the fixed version.)
4. **Confirm the tree is the tree you'll ship:** `git status --short` clean except intentional never-commit files; everything you intend to push is committed.

Once tests + lint are green on the final tree, **stamp the tests gate** with the exact command you ran:

```bash
~/.claude/hooks/prlaunch-gate.sh record tests --cmd "<the actual test command run>"
```

**Do not proceed to phase 5 until tests + lint are green on the final committed tree and every code change since each gate has been re-gated.** If this checkpoint surfaces a new fix, you've changed code again — loop back through the affected gate, then return here. Finalize only when a full pass produces zero new changes.

---

## 5. Push + open PR

For each unit:

1. **Pre-flight: verify the gate ledger** (the global `pr-gate.sh` hook BLOCKS `gh pr create` unless all four gates — deep_review, cr_cli, outcome_eval, tests — are recorded in the ledger at the current HEAD). You stamped each at its phase boundary; this confirms the final tree is fully gated:

   ```bash
   ~/.claude/hooks/prlaunch-gate.sh check
   ```

   If `check` fails it names the exact missing or stale gate and the HEAD to re-run it on — go re-run that phase, `record` it again, and re-check. Any commit after a gate ran stales that gate's entry (the re-gate rule, enforced by the hook), so a late fix means re-recording the affected gate here before `check` goes green. Do NOT hand-write ledger entries to satisfy the hook — that defeats the entire gate. (Emergency owner-authorized bypass: `PRLAUNCH_SKIP=1` in the command.)

2. `git push -u origin <branch>` (if no upstream) or `git push`.
3. Open the PR — **ready, not draft** (local gates are already clean). **The PR must link to its tracker ticket** — the `pr-gate` hook blocks `gh pr create` unless the branch carries a ticket token or the body carries the ticket id (e.g. `Closes <TICKET-ID>`), so the tracker auto-attaches it. (`LINEAR_SKIP=1` only for genuinely ticket-less PRs like config/infra.) The body MUST include a **Testing** section reporting everything actually run and passed before submission — past tense, with results, including the logic/outcome scenarios. This is the evidence trail for reviewers; an empty or future-tense ("- [ ] should test X") Testing section means Phase 5 isn't done:

   ```bash
   gh pr create --title "<short title>" --body "$(cat <<'EOF'
   ## Summary
   <1-3 bullets — focus on the why>

   ## Testing
   All gates run locally on the final tree (HEAD <short-sha>) before this PR was opened:

   - **Tests:** <suite> — N passed, 0 failed (`<command used>`)
   - **Lint/types:** clean (`<command used>`)
   - **Deep review (diff):** N findings → all fixed/ticketed/waived; M iterations to clean
   - **CR CLI (repo):** clean after N findings dispositioned — or: skipped, rate-limit/credits (cloud CR on this PR is the backstop)
   - **Outcome eval (live, user's seat):** M scenarios PASS — one line per scenario with the observed result, e.g. "rewrite request → reply addressed the selected passage, knew the open chapter, rendered formatted (no raw markdown)"
     - or: N/A — no user-facing surface
   - **Re-gate:** final tree re-verified after last code change (tests + lint + <affected gate/scenario>)

   ## Test plan (reviewer)
   - [ ] <anything a human reviewer should still verify>

   Closes <TICKET-ID>

   🤖 Generated with [Claude Code](https://claude.com/claude-code)
   EOF
   )"
   ```

4. Cloud CodeRabbit will auto-run on the ready PR — that's a confirmation pass, not the gate. The gate was already met locally.
5. Report the PR URL back to the owner.

**Never merge.** The owner's team merges.

---

## 6. Wrapup

Same flow as `/wrapup`:

1. **Tracker** — update tickets to "In Review", then **verify the PR attachment actually landed**: `mcp__linear__get_issue <id>` and confirm the PR URL is in `attachments`/links. If it's missing (the `Closes`/branch auto-link didn't fire), attach it explicitly (`mcp__linear__save_issue` with `links: [{url, title}]`) — don't assume the magic word stuck (tickets are systematically under-linked). Comment on session progress.
2. **Disposition gate** — walk the running disposition list from phases 1+2+3. Every finding must be `fixed`, `ticket <id>`, or `waived: <reason>`. **If any OUT-OF-SCOPE-but-legit finding has no ticket yet, file it now** (batch related ones; assign the owner; link source PR + `file:line`). A finding with no disposition is a bug in the wrapup — resolve it before reporting.

   **Durable per-unit record (guarded no-op if the helper is absent).** For EACH shipped unit, append one `prlaunch`/`unit` event to `~/.claude/automation-ledger.jsonl` (a durable quality record a weekly scorecard can aggregate). Pull `cr_cli` (clean or its skip-reason) and `outcome_eval` (`na` or scenario count) straight from this unit's gate ledger; supply the deep-review CRITICAL+HIGH count, `regate` done, and whether `PRLAUNCH_SKIP` was used:
   ```bash
   if [ -x ~/.claude/hooks/ledger-append.sh ]; then
     L=~/.claude/prlaunch-ok/<repo>--<branch-slug>.json        # this unit's gate ledger
     SKIP=$([ -n "${PRLAUNCH_SKIP:-}" ] && echo true || echo false)
     CR=$(jq -r '.gates.cr_cli.skipped // "clean"' "$L" 2>/dev/null)   # skip-reason, else "clean"
     # outcome_eval: "na" if the gate ledger recorded --na, else your real scenario count
     if [ -n "$(jq -r '.gates.outcome_eval.na // ""' "$L" 2>/dev/null)" ]; then OE="na"; else OE="<N> scenarios"; fi
     U=$(jq -n -c --arg repo "<repo>" --argjson pr <PR#> \
         --argjson dr <deep-review CRITICAL+HIGH count> \
         --arg cr "$CR" --arg oe "$OE" \
         --argjson regate true --argjson skip "$SKIP" \
       '{skill:"prlaunch", event:"unit", repo:$repo, pr:$pr,
         gates:{deep_review_findings:$dr, cr_cli:$cr, outcome_eval:$oe, regate:$regate, prlaunch_skip:$skip}}')
     ~/.claude/hooks/ledger-append.sh "$U"
   fi
   ```
3. **GitHub** — verify all PRs from this run show correctly; note any other open PRs touched this session.
4. **Branches** — confirm no leftover dirty/unpushed state in any repo touched.
5. **Memory** — capture anything non-obvious from this run (gotchas, decisions, unexpected deep-review / CR CLI / outcome-eval findings worth remembering). Check for existing entries to UPDATE before creating new ones.
6. **Cleanup queue** — `python3 ~/.claude/hooks/cleanup-sweep.py --count`. If `>0`, run the `/cleanup` sweep (show queued deletes, confirm with the owner, then `cleanup-sweep.py --run <i>` each approved entry — or `--run-all` — which deletes the parsed targets directly; do NOT re-run the queued `cmd`. `--remove <i>` only for declined entries). Report cleared vs. left; `0` → "Cleanup: nothing pending".
7. **Report** — one consolidated message:

```
## PRlaunch complete

**PRs opened (ready for review)**
- repo-a #123 — <ticket> — <one-line summary> — <url>
- repo-b #456 — <ticket> — <one-line summary> — <url>

**Deep-review (local)**
- #123: N CRITICAL/HIGH fixed across M iterations
- #456: clean on first pass

**CR CLI (local)**
- #123: N in-scope findings fixed
- #456: 1 finding waived (reason)

**Outcome eval (live, user's seat)**
- #123: M scenarios, all PASS — e.g. "critique reply addressed the selected passage, knew the open chapter, rendered formatted"
- #456: 1 scenario FAILED → fixed (raw markdown in chat bubble) → re-ran PASS
- N/A for <unit> — no user-facing surface

**Out-of-scope findings tracked** (deep-review + CR CLI + outcome eval)
- <ticket> — <what> (filed from #123's CR sweep)
- waived: <finding> — <reason>

**Tracker**
- <ticket> → In Review
- <ticket> → In Review

**Branches**
- all clean

**Memory**
- Saved: <slug> — <why> (or: nothing new)

**Open follow-ups**
- <thing>
```

---

## Guardrails

- **Unstick rule (eval/browser rigs): 3 strikes → change the substrate, not the parameters.** If the same tool call fails or times out 3 times (screenshot, click, generate), STOP retrying variations of it. Escalate in order: (1) tear down and recreate the surface (close the Playwright page and renavigate — this cleared a permanently wedged page.screenshot once; restart the dev server), (2) switch instrument (read textContent/DOM state instead of pixels; hit the API instead of the UI), (3) switch driver entirely (a vision-capable remote agent that can run MCPs and loop — use it when local Playwright keeps fighting back). Log what unstuck it. Burning 10+ minutes re-trying one wedged call is a process failure, not persistence.
- Confirm with the owner before: first push of any new branch, opening a PR, applying fixes flagged as questionable by deep-review or CR CLI.
- Never merge.
- **Gate order matters: deep-review → CR CLI → outcome eval, in that order.** Deep-review = architecture/correctness (do it first; nit fixes mutate code it just judged). CR CLI = style/nits. Outcome eval LAST, because it grades the running product and you want the code already correct + clean before you judge the experience — and because a bug it finds may send you back to fix code the earlier gates will need to re-judge.
- The three gates are complementary, not redundant: **diff, repo, running product.** A clean diff that ships a broken experience has passed two gates and failed the one that matters to the user.
- **Outcome eval grades outcomes, not transport.** 200s, payloads, DB hashes, byte-match/`Content-Type` on a file, and "an element exists" are necessary but never sufficient. Read the words. Look at the pixels. **If the output is a file/image/video, open it and say what it depicts** — a checksum is not a look. Eval on **real** input, not `picsum`/`lorem` filler. Ask "would this annoy me?" If you didn't quote-or-view the real output, you didn't grade it.
- **Re-gate on every change. "Green earlier" ≠ "the final version is green."** Any fix made in or after a gate (including fixes the eval prompts) invalidates the gates that ran before it — re-run them, proportionate to the change, on the final committed tree. Phase 4 is the checkpoint; you cannot push until a full pass over the shipping bytes produces zero new changes.
- Both code gates run LOCALLY before the PR is opened. Cloud CodeRabbit fires when the PR is created — a free confirmation pass, not the gate we waited for. Exception: CR CLI may be skipped when rate-limit/credit-blocked (phase 2 step 7) — recorded, never silent.
- **The pr-gate hook is the enforcement, not the process.** `gh pr create` is globally blocked unless the per-gate ledger `~/.claude/prlaunch-ok/<repo>--<branch-slug>.json` records all four gates (deep_review, cr_cli, outcome_eval, tests) at the current HEAD — each stamped by `~/.claude/hooks/prlaunch-gate.sh record <gate>` at its phase boundary, verified by `prlaunch-gate.sh check` in phase 5. `outcome_eval` also requires a registered scenarios file (or `--na "<reason>"`); `cr_cli` may be `--skipped "<reason>"`. Any commit after a gate ran stales its entry (the re-gate rule, mechanical) — re-record it. Never hand-write ledger entries to make the hook happy — that defeats the entire gate. (Legacy plain-sha markers are still accepted with a migration warning.) `PRLAUNCH_SKIP=1` exists for owner-authorized emergencies only.
- **Out-of-scope ≠ discard.** Every finding (all three gates) gets a disposition: fixed, ticketed, or waived-with-reason. The phase-6 disposition gate enforces this.
- **One brain, no lane-fixes.** A fix that patches shared behavior on one surface (with a "parity"/"mirror" comment, a routers→service import, or an "on the X path" scope) ships duplication that behavioral gates can't catch — the copies pass identically until they diverge. Phase 1's fix-placement check is the tripwire; the escape hatch is copy + referenced consolidation ticket, never copy + comment.
