---
name: flushdeployed
description: Use when auditing/"flushing" the Deployed column of a Linear project against ACTUAL prod code — confirming each shipped ticket is truly live, splitting partially-shipped tickets into done+todo, and moving wrongly-marked ones back to Todo. Triggers "/flushdeployed <project>", "flush the deployed tickets for X", "validate the deployed tickets", "are these really deployed". A fuzzy project name resolves to the Linear project.
---

# flushdeployed — validate the Deployed column against prod

"Deployed" is a **claim to verify, not trust.** Linear auto-advances tickets to Deployed on PR merge, but: (a) a manually-deployed service ships by hand so merged ≠ live; (b) tickets get marked Deployed when only *part* of their scope shipped; (c) some are reverted/wrong. This skill validates each Deployed ticket against `origin/main` + the live box, then **moves the truly-done to Done, splits the partials, and bounces the wrong ones to Todo.**

`validate-workflow.js` (the fan-out validator) and `apply-done-workflow.js` (the write helper) live next to this file.

## Entry point: `/flushdeployed <project>`

`<project>` is fuzzy (e.g. `studio` → your "Studio"-named project). No arg → ask which project.

## Defaults — JUST RUN IT, don't prompt
This skill has fixed defaults. When invoked, proceed with them silently — do NOT open a preamble of clarifying questions:
- Verified-live ticket → **move to Done with an evidence note**.
- Run size → **all Deployed tickets in one sweep** (not a pilot).
Only stop to ask if the project name is ambiguous/unresolvable, or the user explicitly names a different mode in their message (read-only audit, pilot batch, keep-Deployed-instead-of-Done). Otherwise run the whole pipeline end-to-end and report.

## Pipeline

**1. Resolve the project + status names.**
`mcp__linear__list_projects {query:"<fuzzy>"}` → project name + team. `mcp__linear__list_issue_statuses {team:"<team>"}` → confirm the `Deployed`, `Done`, `Todo` names (Deployed is a *started*-type column here, not terminal).

**2. Pull every Deployed ticket.** `mcp__linear__list_issues {project, state:"Deployed", limit:100}` — paginate via `cursor` until `hasNextPage:false`. ⚠️ The result blows the tool token cap and is saved to a file; parse it with python (`json.loads`), don't Read it. The `id` field IS the identifier; `gitBranchName` gives the branch. Sort by `updatedAt` desc if you need a pilot batch.

**3. Establish prod ground truth (do this ONCE, in the canonical clones — not throwaway checkouts).** For each repo a ticket might touch:
```
git -C ~/code/<repo> fetch origin main --quiet
git -C ~/code/<repo> log -1 --format='%h %ci' origin/main
```
- **Auto-deploy repos** (e.g. Vercel/PaaS deploying `main`) → `origin/main == LIVE` (`deploy:"vercel-auto"`). Confirm via a `/health` git_sha or `vercel ls --prod`.
- **Manually-deployed repos** → get the live box HEAD (the deploy cutoff):
  `ssh <deploy-box> "cd /srv/<repo> && git rev-parse HEAD && git log -1 --format='%ci' HEAD"`
  A change is **live only if its merge commit is an ancestor of that box HEAD.** The box routinely lags `origin/main` by hours/commits (`deploy:"ec2-manual"`).
- **Several manual repos can share ONE box** — get each repo's own box HEAD (`ssh <box> "cd /srv/<repo> && git rev-parse HEAD"`). If `box HEAD == origin/main HEAD` that repo is caught up → merged == live; use `deploy:"ec2-manual"` with its own `live_box`.
- **Template repos** (a repo instantiated per-tenant, not a running site) → merged-to-main == shipped; use `deploy:"template"`.
- **Repos with no deploy target found** (not on the box, no PaaS config like `vercel.json`) → `deploy:"unknown"` → agents report `live=na`, tickets **stay Deployed** (don't over-claim live, don't bounce).
- **Container-image deploys** (built to a registry, loaded on the host — no git on the box) → not ancestry-provable; `live=na`, keep Deployed with a note unless you can inspect the running artifact.
- Quick recon of what's actually deployed on a shared box: `ssh <box> "ls -1d /srv/*; ps aux | grep -Ei 'uvicorn|gunicorn|node' | grep -oE '<repo-prefix>-[a-z]+' | sort -u"`.

**4. Fan out one validator agent per ticket** via the supporting workflow (read-only; agents propose, they don't write Linear):
```
Workflow({ scriptPath: "~/.claude/skills/flushdeployed/validate-workflow.js",
  args: { refs: {<repo>: {path, main, deploy, live_box?, box_date?}}, tickets: [{id,title,branch}] } })
```
Each agent: finds the merge in `origin/main` (`git log origin/main -i --grep="<ticket-id>"`), confirms the **actual change is present** (`git show origin/main:<path>` / `git grep`, not just a merge commit), checks live-ness (`git merge-base --is-ancestor <merge> <box>`), and returns a structured verdict.

Give **every** agent **all** repo refs (tickets cross repos). Big speed/accuracy win: **pre-route + pre-compute liveness before fan-out** — dump each repo's `git log origin/main --since=<project-start> --format='%H|%h|%ci|%s'` once, map every ticket→repo+merge in python by grepping the bare ticket-number in commit subjects, and pre-run `merge-base --is-ancestor <commit> <box>` for manual-deploy commits to know LIVE vs PENDING up front. Inject the result as a `⟪routing: …⟫` hint appended to each ticket's **title** in the workflow args (the validator builds its prompt from id/title/branch only, so title is the injection point). Agents still confirm change-present, but aim instantly.

**5. Review verdicts, then re-verify the decisive claim yourself** before any write: for every manual-deploy→Done ticket, independently run `git -C ~/code/<repo> merge-base --is-ancestor <merge> <box> && echo LIVE`. (zsh does NOT word-split unquoted vars — loop over a real array `arr=(a b c)`, not a string.)

**6. Apply Linear actions.** Map each verdict → action. **Policy override:** the default is *verified-live → Done*, so `DEPLOYED_LIVE` with `live=="live"` → **Done**; `DEPLOYED_LIVE` with `live=="na"` (deploy target unconfirmed) → keep Deployed + ✅ note (agents often propose `note_keep_deployed` for genuinely-live tickets too — override those to Done).

| Verdict | Meaning | Action |
|---|---|---|
| DEPLOYED_LIVE + live=live | merge + change present + (auto-deploy/template merged, OR manual merge⊑box) | `save_comment` ✅ evidence → `save_issue {state:"Done"}` |
| DEPLOYED_LIVE + live=na | change on main but deploy target unconfirmed | `save_comment` ✅ → **leave Deployed** |
| MERGED_PENDING_DEPLOY | in main, not on box | `save_comment` ⏳ → **leave Deployed** |
| PARTIAL | only part of scope shipped | remainder **Todo** (`parentId`, project, ticket's own labels) → `save_comment` ✂️ on original → original `{state:"Done"}` |
| WRONG_NOT_DEPLOYED | no merge / absent / reverted | `save_comment` ⚠️ → `save_issue {state:"Todo"}` |
| UNCERTAIN | ops-only / ambiguous | `save_comment` 🔎, no status change, flag for human |

**How to write** (orchestrator keeps context lean by delegating trivial writes, but does the split by hand):
- **Do every PARTIAL split YOURSELF** (orchestrator, direct `save_issue`/`save_comment`) — never hand a split to an agent (split-write hazard, see Gotchas). Sequence: create remainder Todo → capture new id → ✂️ comment on original → original `{state:"Done"}`.
- **Delegate the mechanical Done/note writes** to `apply-done-workflow.js` (next to this file): `Workflow({scriptPath:"~/.claude/skills/flushdeployed/apply-done-workflow.js", args:{items:[{id, note, state}]}})`. `state:"Done"` for verified-live, **omit `state`** for keep-Deployed notes (⏳/✅-na/🔎). One trivial agent per ticket, `effort:low`, no analysis, no new tickets.
- **⚠️ AFTER applying, re-pull the Deployed column and ASSERT `count == expected-kept-set`** (`list_issues {state:"Deployed"}`; small enough to not blow the cap). Apply-agents freelance status — a note-only agent may silently also flip the ticket to Done and *lie* in `action_taken`. If the count is off, `get_issue` the missing ids and revert. **Do not trust agent-reported `action_taken`.**

**7. Report** a verdict table + counts. Note the deploy lag: if pending tickets exist, a single manual deploy flushes them all to live at once.

## Gotchas (learned the hard way)
- **Workflow `args` arrives as a STRING** in the script — coerce: `const x = typeof args === 'string' ? JSON.parse(args) : args`.
- **`list_issues` exceeds the token cap** → it auto-saves to a file; parse with python, never Read.
- **zsh won't word-split `$var`** in `for` loops → `^{commit}` also trips extended-glob; use arrays + quote, or test with `git cat-file -t`.
- **Don't trust a merge commit alone** — confirm the fixed code is actually in `origin/main` (catches reverts/no-ops/scope-gaps → the PARTIAL cases).
- **Ops/infra tickets** (e.g. "install X on the box") aren't git-provable; look for a ticket comment documenting a verified manual prod action before calling them live, else UNCERTAIN.
- **Verified case moves to Done with an evidence note** (commit/PR# + file:line + live-basis) so the audit is auditable.
- **Split-write hazard (agents self-applying):** in `split_then_done`, agents reliably create the remainder ticket as Todo but then mis-apply the FINAL `state:Done` write to the NEW ticket instead of the original — seen 11/12 in one real run. **Fix: do the split in the orchestrator by hand (§6), never in an agent.** After any split, re-pull the remainder and confirm it's still `Todo`.
- **Apply-agents freelance STATUS even when told "note only":** a keep-Deployed agent has posted its comment correctly but ALSO silently moved the ticket to Done, then falsely reported `action_taken:"noted_kept_deployed"`. Caught only because the post-apply Deployed count came back one short. **ALWAYS re-pull the Deployed column after applying and assert `count == expected kept-set`; never trust `action_taken`.** (§6.)
- **Read-only validate agents mis-fill `action_taken` too:** in propose mode (`apply=false`) most agents return non-`none` values like `noted_kept_deployed` despite writing nothing. Before applying, confirm no writes actually happened (ticket `updatedAt` predates the run / no flushdeployed comment via `list_comments`). Drive apply decisions off `verdict`+`live`, not `action_taken`/`proposed_action`.
- **NO-HIT tickets** (ticket-id absent from every commit subject) are usually repo-standup or ops tickets, or squash-merges that reference a sibling id (e.g. `ABC-101/102` fails an `abc-102` regex). Hand these a manual hint; don't bounce them to Todo on a failed grep.
- **Workflow syntax check:** `node --check` flags a script's top-level `return`/`await` as "Illegal return" — that's expected (the harness wraps the body in an async fn). Verify by wrapping the body (minus `export const meta`) in `async function __w(){…}` before `node --check`.
- **Know each repo's deploy model — auto-deploy ≠ manual box.** A PaaS repo whose prod tracks `main` (confirm via a `/health` git_sha or `vercel ls --prod`) is zero-lag; a manual-deploy box lags. Tickets can cross repos with different models — give every agent all repo refs. And if a repo's branches flow `feature→develop→main`, a merge on `develop` only is NOT live (→ WRONG_NOT_DEPLOYED).
