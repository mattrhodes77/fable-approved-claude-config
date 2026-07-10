export const meta = {
  name: 'flushdeployed-apply-done',
  description: 'Apply pre-computed flushdeployed Done-moves: one agent per ticket posts the ✅ evidence note then sets state=Done. No analysis, no new tickets.',
  phases: [{ title: 'Apply', detail: 'one agent per ticket: save_comment + save_issue(state=Done)' }],
}
// args = { items: [{id, note, state}], team, project }
const input = typeof args === 'string' ? JSON.parse(args) : args
const items = input.items

const SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['id', 'action_taken'],
  properties: {
    id: { type: 'string' },
    action_taken: { type: 'string', enum: ['moved_to_done', 'noted_todo', 'noted_only', 'write_failed', 'none'] },
    error: { type: 'string' },
  },
}

function prompt(it) {
  const stateLine = it.state
    ? `2. save_issue({id:"${it.id}", state:"${it.state}"}).`
    : `2. (no state change — comment only).`
  const taken = it.state === 'Done' ? 'moved_to_done' : (it.state === 'Todo' ? 'noted_todo' : 'noted_only')
  return `You are a Linear WRITER applying a pre-decided flushdeployed action to ONE ticket. Do NOT analyze, do NOT read the ticket, do NOT create any new ticket, do NOT touch any other issue id.

Load write tools: ToolSearch \`select:mcp__linear__save_comment,mcp__linear__save_issue\`.

Make EXACTLY these writes on issue ${it.id}:
1. save_comment({issueId:"${it.id}", body: <<<NOTE>>>}) where the note body is EXACTLY:
---
${it.note}
---
${stateLine}

Idempotency: if the comment call reports it already exists, don't duplicate. If a write throws, set action_taken="write_failed" and put the message in error; otherwise action_taken="${taken}".
Return the structured record only.`
}

phase('Apply')
const results = await parallel(
  items.map((it) => () => agent(prompt(it), { label: `done:${it.id}`, phase: 'Apply', schema: SCHEMA, effort: 'low' }))
)
return results.filter(Boolean)
