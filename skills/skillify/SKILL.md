---
name: skillify
description: Turn a real repository into a project-specific skill library under <repo>/.claude/skills/ so zero-context sessions (Opus/Sonnet-class models, teammates, remote agents) start with senior-engineer operating knowledge. Use for "/skillify <repo>", "skillify acme-api", "build a skill library for this repo", "turn this repo's tribal knowledge into skills", "onboard this repo for a weaker model/agent". Ground-truth-only discover→map→author→review; never a README rewritten as skills.
---

# skillify — build a repo's operating knowledge into a skill library

A zero-context Sonnet-class model (or a new teammate, or a remote agent) dropped into `<repo>` should be able to answer **what is this / what must not break / how do I set up, run, test it / how do I debug a real failure / how do I prove a fix / which historical failures do I avoid** — using only the library you build. This skill mines ONE repo for that knowledge from **ground truth** (checked-out code, tests, CI, memory, the issue tracker, prod) and writes `<repo>/.claude/skills/`. It adapts the external "Repository Skill Library Builder" method (discover→map→author→review) with ecosystem upgrades: memory-store backfill, tracker-as-source-of-truth, and the `/flushdeployed` prod-verification method.

`taxonomy.md` (the per-skill reference sheet) lives next to this file. Read it before Phase 2.

## Entry point: `/skillify <repo> [--local-only] [--verify]`

- `<repo>` is fuzzy → resolves to `~/code/<repo>` (your checkout root) or an explicit path. Map your team's shorthands to on-disk names (e.g. "writer backend" → `acme-writer-api`). Ambiguous or no match → ask which repo (never guess a path).
- `--local-only` → write to the local overlay instead of the repo tree (see OUTPUT). Never both.
- `--verify` → **maintenance re-run**: for an existing library, execute each skill's one-line re-verification commands, diff against the stated expected observations, and PR the corrections. (The full maintenance loop is future scope; this flag is the thin entry point.)

## NON-NEGOTIABLES (adopt verbatim)

- **Discover before authoring.** No skill is written until Phase 1's census + system map exists. Thin map ⇒ keep discovering, don't start writing.
- **Write ONLY under `<repo>/.claude/skills/`** (or the local overlay with `--local-only`). The repo is otherwise **read-only** — no edits to source, config, or docs.
- **No mutating git** beyond the single branch + PR the skill itself opens at the very end. No checkout/pull/stash/commit/push in the target during discovery.
- **Never invent** commands, flags, paths, env vars, policies, or history. If it isn't in the code/tests/CI/scripts/memory/tracker/prod, it does not go in a skill.
- **Label inference as inference.** "Likely / appears to / inferred from X" — never stated as verified fact.
- **Volatile facts get a date stamp + a one-line re-verification command** so a later reader can re-check them in one shell line.
- **Do not route around the repo's change control.** The library documents the real merge lane; it never invents a shortcut.
- **Reject "README rewritten as skills."** A restated README is a failure, not an output. Skills carry operating knowledge a README does not: exact invocations, expected observations, traps, decision gates, settled failures.

## SOURCE PRIORITY (highest wins on conflict)

1. **Current checked-out code** (the working tree / `origin/<default-branch>`).
2. **Tests + fixtures** (what the repo asserts about itself).
3. **CI config** (`.github/workflows`, etc. — what actually gates merges).
4. **Build scripts / manifests** (`package.json`, `pyproject.toml`, `Makefile`, lockfiles).
5. **Runtime / deploy scripts** (deploy scripts, Dockerfiles, Procfile, `vercel.json`).
6. **Your persistent memory store** — `memory_search` the **FULL** memory per repo name + concern keywords. **Never trust a truncated injected memory preview**; weeks of per-repo session gotchas live in the full store. (Skip if you have no memory system.)
7. **The issue tracker** — search the *concern* (not just a ticket id). Tickets/epics are source of truth for what was built, what failed, what got reverted.
8. **Prod ground truth** — the `/flushdeployed` method, per-repo deploy model (build your own table like the example below). Merged ≠ live for manual-deploy repos.
9. **Official repo docs** (README, `docs/`, CLAUDE.md) — context, not authority; verify against 1–5.
10. **Git history / reverts** — `git log`, revert commits, blame on load-bearing lines.
11. **TODO / FIXME / open issues** — known gaps.
12. **Labeled inference** — last resort, always marked as such.

### Per-repo prod deploy model (example — build and verify your own)

| Repo | Deploy | LIVE basis | One-line verify |
|---|---|---|---|
| acme-web | Vercel auto-deploy `main` | `origin/main == LIVE` | `git -C ~/code/acme-web log -1 --format='%h %ci' origin/main` |
| acme-api | **Manual VM deploy**, box lags origin/main | live iff merge is ancestor of box HEAD | `ssh <deploy-box> "cd /srv/acme-api && git rev-parse HEAD"` then `git merge-base --is-ancestor <sha> <boxHEAD>` |
| acme-writer-api | PaaS, prod=`main` | `/health` `git_sha` | `curl -s <prod>/health \| python3 -c "import sys,json;print(json.load(sys.stdin).get('git_sha'))"` |
| acme-writer-web | Vercel, prod=`main` | `vercel ls --prod` | `vercel ls --prod` |
| other repos | **uncertain** | do NOT over-claim live | fetch + label deploy model as inferred |

If a repo's feature branches flow `feature→develop→main`, a merge on `develop` only is **NOT live**.

## PHASE 1 — Census + system map (read-only)

**1a. Census the repo** with read-only commands only:
```
git -C <repo> log --oneline -50
git -C <repo> branch -a --sort=-committerdate | head -30
rg -n 'TODO|FIXME|HACK|XXX|@deprecated' <repo> --stats
git -C <repo> log --oneline -i --grep='revert' -20
```
Read the manifests, CI, deploy scripts, test config, and top-level entry points. Note the default branch and whether the tree sits off it (some repos check out `main` locally while ACTIVE dev happens on `origin/develop` — verify per repo).

**1b. Memory backfill.** `memory_search` your full memory store on `<repo>` name + concern keywords (deploy, test, env, migration, the domain nouns). Dump every candidate gotcha into the census — this is where the tribal knowledge already lives.

**1c. Tracker concern sweep.** Search the product/epic concern (not the ticket id) for what shipped, failed, or was reverted. Feed settled battles into the `failure-archaeology` candidate list.

**1d. Build the system map**: domain, state model, actors, ownership boundaries, core workflow, top risks, tooling, success criteria. **Thin map ⇒ keep discovering** — do not proceed to authoring on a hollow map.

**1e. Ask the owner AT MOST 5 questions**, and only for what discovery genuinely could not answer: the hardest live problem, unwritten rules, the audience's biggest knowledge gap, the costliest past failures. Batch them in one `AskUserQuestion`; lead each with your best inference so they can confirm rather than write prose.

## PHASE 2 — Author via fan-out

Map the system to skills using `taxonomy.md` (CORE tier for product repos: **merge thin topics, split deep ones, target 8–12 skills**). Fan out **one agent per skill**; write each author agent's prompt as a six-section brief (`briefs` skill) and have it return a **schema-validated** object (skill name, sections present, source files cited, commands-with-expected-observations, open uncertainties).

**Every authored skill MUST carry the per-skill required sections listed in `taxonomy.md`** (purpose / when-NOT-to-use + sibling / source-of-truth files / procedure / commands-with-expected-observations / decision gates / known traps / evidence-of-success / provenance+maintenance).

**Authoring rules (give these to every agent):**
- **Audience = a zero-context mid-level engineer OR a Sonnet-class model.** Assume nothing about the repo.
- **Imperative runbook voice.** Steps, not essays.
- **Every command ships with its expected observation** ("`pytest -q` → `312 passed` in ~40s"). A command with no expected output is not done.
- **Define each jargon term once**, at first use.
- **when-NOT-to-use + a sibling pointer** in every skill (one home per fact; point elsewhere instead of duplicating).
- **Ground truth only.** Cite the source file:line. Label anything inferred.
- **End every skill with "Provenance and maintenance"**: date-stamp + the one-line re-verification command(s) for that skill's volatile claims.

**Known fan-out gotchas (tell every agent, if you use a workflow substrate):**
- `args` may arrive as a **string** → coerce: `const x = typeof args === 'string' ? JSON.parse(args) : args`.
- `export const meta` must be the **first statement** in the workflow module.
- zsh does not word-split `$var`; loop over a real array and quote, or you'll miss-parse multi-value inputs.

## PHASE 3 — Review (three parallel passes + adversarial refute)

Run all three in parallel over the drafted library, then a refute pass, then one fixer.

- **FACTUAL** — re-verify every flag/path/command against the repo, AND **execute a sample of each skill's read-only commands**, comparing actual output to the stated expected observation. A mismatch is a blocking finding.
- **DOCTRINE** — contradictions with the repo's own rules / change-control, contradictions between sibling skills, and overstated claims (anything asserted that discovery only inferred).
- **USABILITY** — trigger quality (would a zero-context agent load the right skill?), one-home-per-fact (no duplication), self-containedness, scannability.
- **Adversarial refute (deep-review v6.9 style)** — for a sample of load-bearing claims per skill, a refuter *tries to disprove* the claim against the repo. Refuted → the fixer either corrects it or relabels it as inference.

**One fixer** applies all blocking + important findings — **without introducing any new unverified claim**. If a fix needs a fact discovery didn't establish, it's flagged, not invented.

## OUTPUT + change control

**Default (repo tree):**
1. Branch `me/<prefix>-NNNN-skillify-<repo>` (a ticket-token branch flips the tracker ticket to In Progress via the `linear-startwork` hook, if you run it). If no ticket exists, file one first (right team, right project/labels).
2. Write the library under `<repo>/.claude/skills/`.
3. Run **`/PRlaunch`** (deep-review → CR CLI → outcome eval → PR → wrapup). Do not re-implement its gates here.
4. **Outcome eval = the acceptance test.** Spawn a **FRESH** subagent given ONLY the new skill library plus the quality-gate questions (what is this system / what must not break / set up + run + test / debug a real failure / prove a fix / avoid historical failures). It must succeed **from the library alone**. If it can't, the library is incomplete — loop back to Phase 2.
5. The repo **CLAUDE.md gets ONLY a 3-line pointer** to `.claude/skills/` (doc-pattern rule: CLAUDE.md stays slim). Do not dump the library into CLAUDE.md.

**`--local-only`** → write the same library to the user-level skills dir instead of the repo tree: `~/.claude/skills/<repo>-<skill-name>/SKILL.md` (the taxonomy names already carry the `<repo>-` prefix, and user-level skills load in every session — the trigger-rich descriptions scope them to work in that repo). Open no repo PR; commit to your config repo instead. Never write both places.

## FINAL REPORT (to the owner)

1. **Skill inventory table** — each skill + a one-line purpose.
2. **Verified spot-checks** — commands actually executed in review and their real output.
3. **Remaining uncertainty** — what's still labeled inference or unverified.
4. **Recommended first-load order:** architecture-contract → change-control → build-and-env → test-and-validate → debugging-playbook.
5. **Maintenance-soon list** — the most volatile facts and when to re-run `--verify`.

## Quality gate + red flags (reject a shallow library)

The bar: **a zero-context agent can operate the repo from the library alone.** If not, it fails — keep going.

| Red flag | Why it fails | Fix |
|---|---|---|
| Reads like a rephrased README | No operating knowledge added | Add invocations, expected outputs, traps, decision gates |
| Commands without expected observations | Reader can't tell success from failure | Attach the real output to every command |
| A fact in two skills | Drifts out of sync | One home + a sibling pointer |
| Uncited or invented flag/path | Violates ground-truth rule | Cite file:line or delete |
| Inference stated as fact | Misleads a weaker model | Relabel "inferred from X" |
| Volatile fact with no re-verify line | Rots silently | Add date + one-line re-verification command |
| `merged == live` for a manual-deploy repo | Wrong deploy model | Use the box-ancestor check |
| Skill named after a file, not a job | Won't trigger when needed | Name for the task a reader is trying to do |
| < ~8 skills for a product repo | Under-mapped | Re-check taxonomy coverage |

## Common mistakes

- **Authoring on a thin map.** Discovery is most of the work; skip it and you rewrite the README.
- **Trusting a truncated injected memory preview.** `memory_search` the full store.
- **Over-asking the owner.** Five questions max, only for what discovery can't answer.
- **Mutating the repo during discovery.** Read-only until the final branch/PR.
- **Skipping the fresh-subagent outcome eval.** It's the only proof the library actually works; a passing self-review is not enough.
