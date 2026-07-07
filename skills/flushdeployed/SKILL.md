---
name: flushdeployed
description: Use when auditing/"flushing" the Deployed column of a Linear project against ACTUAL prod code — confirming each shipped ticket is truly live, splitting partially-shipped tickets into done+todo, and moving wrongly-marked ones back to Todo. Triggers "/flushdeployed <project>", "flush the deployed tickets for X", "validate the deployed tickets", "are these really deployed". A fuzzy project name resolves to the Linear project.
---

# flushdeployed — validate the Deployed column against prod

"Deployed" is a **claim to verify, not trust.** Linear auto-advances tickets to Deployed on PR merge, but: (a) a manually-deployed service ships by hand so merged ≠ live; (b) tickets get marked Deployed when only *part* of their scope shipped; (c) some are reverted/wrong. This skill validates each Deployed ticket against `origin/main` + the live box, then **moves the truly-done to Done, splits the partials, and bounces the wrong ones to Todo.**

`validate-workflow.js` (the fan-out validator) lives next to this file.

## Entry point: `/flushdeployed <project>`

`<project>` is fuzzy (e.g. `studio` → your "Studio"-named project). No arg → ask which project.

## Decisions to confirm once (defaults in **bold**)
- Verified-live ticket → **move to Done** (with an evidence note) | keep Deployed + note | leave untouched.
- Run size → **pilot ~15 most-recently-updated, apply, show table, then continue** | all at once | read-only audit.

## Pipeline

**1. Resolve the project + status names.**
`mcp__linear__list_projects {query:"<fuzzy>"}` → project name + team. `mcp__linear__list_issue_statuses {team:"<team>"}` → confirm the `Deployed`, `Done`, `Todo` names (Deployed is a *started*-type column here, not terminal).

**2. Pull every Deployed ticket.** `mcp__linear__list_issues {project, state:"Deployed", limit:100}` — paginate via `cursor` until `hasNextPage:false`. ⚠️ The result blows the tool token cap and is saved to a file; parse it with python (`json.loads`), don't Read it. The `id` field IS the identifier; `gitBranchName` gives the branch. Sort by `updatedAt` desc for the pilot batch.

**3. Establish prod ground truth (do this ONCE, in the canonical clones — not throwaway checkouts).** For each repo a ticket might touch:
```
git -C ~/code/<repo> fetch origin main --quiet
git -C ~/code/<repo> log -1 --format='%h %ci' origin/main
```
- **Auto-deploy repos** (e.g. Vercel deploying `main`) → `origin/main == LIVE`.
- **Manually-deployed repos** → get the live box HEAD (the deploy cutoff):
  `ssh <deploy-box> "cd /srv/<repo> && git rev-parse HEAD && git log -1 --format='%ci' HEAD"`
  A change is **live only if its merge commit is an ancestor of that box HEAD.** The box routinely lags `origin/main` by hours/commits.
- **Repos with an uncertain deploy model** → fetch too; don't over-claim live.

**4. Fan out one validator agent per ticket** via the supporting workflow (read-only; agents propose, they don't write Linear):
```
Workflow({ scriptPath: "~/.claude/skills/flushdeployed/validate-workflow.js",
  args: { refs: {<repo>: {path, main, deploy, live_box?, box_date?}}, tickets: [{id,title,branch}] } })
```
Each agent: finds the merge in `origin/main` (`git log origin/main -i --grep="<ticket-id>"`), confirms the **actual change is present** (`git show origin/main:<path>` / `git grep`, not just a merge commit), checks live-ness (`git merge-base --is-ancestor <merge> <box>`), and returns a structured verdict.

**5. Review verdicts, then re-verify the decisive claim yourself** before any write: for every manual-deploy→Done ticket, independently run `git -C ~/code/<repo> merge-base --is-ancestor <merge> <box> && echo LIVE`. (zsh does NOT word-split unquoted vars — loop over a real array `arr=(a b c)`, not a string.)

**6. Apply Linear actions** (orchestrator writes, in batches):

| Verdict | Meaning | Action |
|---|---|---|
| DEPLOYED_LIVE | merge + change present + (auto-deploy OR manual⊑box) | `save_comment` ✅ evidence → `save_issue {state:"Done"}` |
| MERGED_PENDING_DEPLOY | in main, not on box | `save_comment` ⏳ → **leave Deployed** |
| PARTIAL | only part of scope shipped | `save_issue` new **Todo** for remainder (`parentId`, project, labels) → `save_comment` ✂️ on original → original `{state:"Done"}` |
| WRONG_NOT_DEPLOYED | no merge / absent / reverted | `save_comment` ⚠️ → `save_issue {state:"Todo"}` |
| UNCERTAIN | ops-only / ambiguous | `save_comment` 🔎, no status change, flag for human |

**7. Report** a verdict table + counts. Note the deploy lag: if pending tickets exist, a single manual deploy flushes them all to live at once.

## Gotchas (learned the hard way)
- **Workflow `args` arrives as a STRING** in the script — coerce: `const x = typeof args === 'string' ? JSON.parse(args) : args`.
- **`list_issues` exceeds the token cap** → it auto-saves to a file; parse with python, never Read.
- **zsh won't word-split `$var`** in `for` loops → `^{commit}` also trips extended-glob; use arrays + quote, or test with `git cat-file -t`.
- **Don't trust a merge commit alone** — confirm the fixed code is actually in `origin/main` (catches reverts/no-ops/scope-gaps → the PARTIAL cases).
- **Ops/infra tickets** (e.g. "install X on the box") aren't git-provable; look for a ticket comment documenting a verified manual prod action before calling them live, else UNCERTAIN.
- **Verified case moves to Done with an evidence note** (commit/PR# + file:line + live-basis) so the audit is auditable.
- **Split-write hazard (agents self-applying):** in `split_then_done`, agents reliably create the remainder ticket as Todo but then mis-apply the FINAL `state:Done` write to the NEW ticket instead of the original — seen 11/12 in one real run. After any split-heavy run, ALWAYS re-pull the created remainder tickets and confirm each is still `Todo`; bounce any that leaked to `Done`. (validate-workflow.js now warns the agent explicitly, but verify anyway.)
- **Know each repo's deploy model — auto-deploy ≠ manual box.** A PaaS repo whose prod tracks `main` (confirm via a `/health` git_sha or `vercel ls --prod`) is zero-lag; a manual-deploy box lags. Tickets can cross repos with different models — give every agent all repo refs. And if a repo's branches flow `feature→develop→main`, a merge on `develop` only is NOT live (→ WRONG_NOT_DEPLOYED).
