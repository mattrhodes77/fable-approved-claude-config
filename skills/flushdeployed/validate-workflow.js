export const meta = {
  name: 'flushdeployed-validate',
  description: 'Validate "Deployed" Linear tickets against prod code. Read-only (propose) by default; apply mode lets each agent perform its own Linear write.',
  phases: [{ title: 'Validate', detail: 'one agent per ticket: merge-in-main + change-present + live-vs-pending' }],
}

// args = {
//   apply?: bool,                         // false => propose only; true => each agent writes its own Linear change
//   linear?: {team, project, label, done, todo, deployed},   // required when apply=true
//   refs: { "<repo>": {path, main, deploy, live_box?, box_date?}, ... },
//   tickets: [{id,title,branch}, ...]
// }
// deploy: "vercel-auto" (main==live) | "ec2-manual" (live iff merge ancestor of live_box) | "unknown"
const input = typeof args === 'string' ? JSON.parse(args) : args
const refs = input.refs
const tickets = input.tickets
const apply = !!input.apply
const L = input.linear || {}

const SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['id', 'verdict', 'proposed_action', 'action_taken', 'confidence', 'evidence', 'proposed_note'],
  properties: {
    id: { type: 'string' },
    title: { type: 'string' },
    repos: { type: 'array', items: { type: 'string' } },
    merge_evidence: { type: 'string' },
    change_present: { type: 'string', enum: ['yes', 'partial', 'no', 'na'] },
    live: { type: 'string', enum: ['live', 'pending', 'na'] },
    verdict: { type: 'string', enum: ['DEPLOYED_LIVE', 'MERGED_PENDING_DEPLOY', 'PARTIAL', 'WRONG_NOT_DEPLOYED', 'UNCERTAIN'] },
    proposed_action: { type: 'string', enum: ['move_to_done', 'note_keep_deployed', 'split_then_done', 'note_move_to_todo', 'note_only_manual'] },
    action_taken: { type: 'string', enum: ['moved_to_done', 'noted_kept_deployed', 'split_done', 'noted_todo', 'noted_manual', 'write_failed', 'none'] },
    new_ticket_id: { type: 'string', description: 'id of the Todo created for a split, else ""' },
    proposed_note: { type: 'string' },
    split_remainder: { type: 'string' },
    evidence: { type: 'string', description: 'concise: commit hashes + file:line proving present/absent' },
    confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
  },
}

function refsBlock() {
  const lines = Object.entries(refs).map(([repo, r]) => {
    if (r.deploy === 'vercel-auto') return `- ${repo} (${r.path}): origin/main = ${r.main}. Auto-deploys to Vercel on merge → origin/main == LIVE.`
    if (r.deploy === 'ec2-manual') return `- ${repo} (${r.path}): origin/main = ${r.main}. MANUAL deploy. LIVE box = ${r.live_box} (${r.box_date}). A change is LIVE only if its merge commit is an ANCESTOR of ${r.live_box}; in main but not ancestor = MERGED-NOT-DEPLOYED.`
    return `- ${repo} (${r.path}): origin/main = ${r.main}. Deploy model uncertain — report "merged to main", set live=na, do NOT over-claim LIVE.`
  })
  return `## Prod ground truth (objects ALREADY fetched; read ONLY via origin/main — DO NOT git fetch/checkout/pull or touch any working tree/index)\n${lines.join('\n')}`
}

function applyBlock() {
  if (!apply) return `## This is READ-ONLY. Do NOT write to Linear. Only return the proposed action + note; the orchestrator applies it.`
  return `## APPLY the result yourself in Linear (statuses: done="${L.done}", todo="${L.todo}"; team="${L.team}"; project="${L.project}"; label="${L.label}").
Load write tools: ToolSearch \`select:mcp__linear__save_issue,mcp__linear__save_comment\`. Then:
- move_to_done -> save_comment({issueId:id, body:<✅ note>}); save_issue({id, state:"${L.done}"}); action_taken="moved_to_done".
- note_keep_deployed -> save_comment only; action_taken="noted_kept_deployed".
- split_then_done -> save_issue({title:"[split from "+id+"] <remainder summary>", team:"${L.team}", project:"${L.project}", state:"${L.todo}", labels:["${L.label}"], parentId:id, description:<remainder>}); capture new_ticket_id; save_comment({issueId:id, body:<✂️ note referencing new_ticket_id>}); save_issue({id, state:"${L.done}"}); action_taken="split_done".
- note_move_to_todo -> save_comment({issueId:id, body:<⚠️ note>}); save_issue({id, state:"${L.todo}"}); action_taken="noted_todo".
- note_only_manual -> save_comment only; action_taken="noted_manual".
If any write throws, set action_taken="write_failed" and put the error in evidence. Idempotency: if a prior flushdeployed comment already exists on the ticket and the status already matches, do NOT duplicate — set action_taken="none".`
}

function prompt(t) {
  return `You are validating whether a Linear ticket marked **Deployed** is GENUINELY shipped to PROD code. Treat "Deployed" as a CLAIM to verify, not trust.

TICKET: ${t.id} — ${t.title}
Linear git branch: ${t.branch || '(none)'}

${refsBlock()}

## Steps
1. Read the full ticket: ToolSearch \`select:mcp__linear__get_issue,mcp__linear__list_comments\`; get_issue({id:"${t.id}", includeRelations:true}) (description cites the intended change + evidence file:line; check linked PR attachments) and list_comments({issueId:"${t.id}"}) (PR links, prior flushdeployed notes, earlier splits). If Linear is unreachable, note it and proceed from title + git.
2. Find the merge in prod. For each repo: git -C <repo.path> log origin/main -i --grep="${t.id}" --oneline | head -20 ; also grep the branch slug and the bare dev number. Record merge commit + PR#.
3. Confirm the actual CHANGE is present (guard reverts/no-ops/scope-gaps): git -C <repo.path> show origin/main:<path> and/or git -C <repo.path> grep -n "<distinctive string>" origin/main -- <path>. A merge commit alone is NOT sufficient.
4. ec2-manual repos: git -C <repo.path> merge-base --is-ancestor <merge> <live_box> && echo LIVE || echo PENDING.
5. Verdict (CONSERVATIVE):
   - DEPLOYED_LIVE — merge found + change present + (vercel-auto, OR ec2-manual merge ⊑ live_box).
   - MERGED_PENDING_DEPLOY — ec2-manual change in origin/main but NOT ancestor of live_box.
   - PARTIAL — only some described scope in prod; a concrete remainder is unshipped.
   - WRONG_NOT_DEPLOYED — use ONLY when you POSITIVELY confirmed the change is ABSENT from origin/main (inspected the cited file/path; the fix is not there / was reverted). A failed merge-search ALONE is NOT enough.
   - UNCERTAIN — cannot conclude: no merge found AND you can't confirm absence; ops-only ticket needing a live host check; ambiguous scope. (A ticket comment documenting a VERIFIED manual prod action with proof can upgrade an ops ticket to DEPLOYED_LIVE.) **When unsure between WRONG and UNCERTAIN, pick UNCERTAIN — never bounce a ticket to Todo on a failed search alone.**

Note bodies (concise, EVIDENCE-DENSE — commit hash, PR#, file:line):
- ✅ "flushdeployed verified — <repo merge/PR#>, <file:line in origin/main>, <live basis>."
- ⏳ "flushdeployed — merged to <repo> origin/main (<commit/PR#>) but NOT yet on the box (<live_box>, <box_date>). Live on next deploy."
- ✂️ "flushdeployed split — shipped part verified live (<evidence>); remainder split to <new id>: <one-liner>."
- ⚠️ "flushdeployed — NOT verified in prod. Checked <repos/paths>; <what is absent>. Moving back to Todo."
- 🔎 "flushdeployed — could not auto-verify: <why>. Needs manual check: <what>."

${applyBlock()}

Return the structured record. Set confidence honestly.`
}

phase('Validate')
const results = await parallel(
  tickets.map((t) => () => agent(prompt(t), { label: `${apply ? 'apply' : 'check'}:${t.id}`, phase: 'Validate', schema: SCHEMA, effort: 'high' }))
)
return results.filter(Boolean)
