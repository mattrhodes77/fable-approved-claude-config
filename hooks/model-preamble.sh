#!/usr/bin/env bash
# model-preamble.sh — SessionStart hook.
#
# Injects a strict "operating mode" preamble whenever the active session model is
# weaker than Fable-class. Fable-class sessions get nothing. The hook is FAIL-OPEN
# and SILENT: any resolution failure, malformed stdin, or missing tool exits 0
# with no output, so it can never break or delay session start.
#
# Active-model resolution (first non-empty source wins):
#   (a) .model from the SessionStart stdin payload — a top-level string. The CC
#       docs note it can be OMITTED (e.g. after /clear or conversation recovery),
#       so we probe for it rather than assume it; when absent jq yields empty and
#       we fall through.
#   (b) $ANTHROPIC_MODEL env var, if set.
#   (c) .model from ~/.claude/settings.json — read through $HOME (never a
#       hardcoded path) so a test sandbox can point HOME at its own settings.json.
#
# Emission contract (confirmed against the CC hooks docs):
#   {"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":...}}
#   honored only on exit 0.
set -euo pipefail

INPUT=$(cat)

# Resolve the active model id defensively; echoes the id or nothing.
resolve_model() {
  local m=""

  # (a) stdin payload (.model is a top-level string; may be absent).
  m=$(printf '%s' "$INPUT" | jq -r '.model // empty' 2>/dev/null || true)
  if [ -n "$m" ]; then printf '%s' "$m"; return 0; fi

  # (b) env override.
  if [ -n "${ANTHROPIC_MODEL:-}" ]; then printf '%s' "$ANTHROPIC_MODEL"; return 0; fi

  # (c) settings.json, via $HOME so the sandbox can intercept it.
  local settings="$HOME/.claude/settings.json"
  if [ -f "$settings" ]; then
    m=$(jq -r '.model // empty' "$settings" 2>/dev/null || true)
    if [ -n "$m" ]; then printf '%s' "$m"; return 0; fi
  fi

  return 0
}

MODEL=$(resolve_model)

# Unresolvable → say nothing, exit clean. Never break session start.
[ -n "$MODEL" ] || exit 0

# Fable-class → no preamble (case-insensitive substring match on "fable").
case "$(printf '%s' "$MODEL" | tr '[:upper:]' '[:lower:]')" in
  *fable*) exit 0 ;;
esac

# Weaker-than-Fable → inject the process-first operating mode. `read -d ''` (not a
# heredoc inside $(), which stock macOS bash 3.2 mis-parses) slurps the whole
# block; we then trim the single trailing newline so additionalContext is EXACTLY
# the block below.
IFS= read -r -d '' PREAMBLE <<EOF || true
OPERATING MODE (model: ${MODEL}) — process-first execution:
1. Skills are law. If a skill exists for the task, invoke it and follow it literally. Do not adapt or skip steps that "seem unnecessary"; only skip what a skill explicitly marks optional/N-A.
2. Evidence before claims. Never state "fixed / passing / deployed / verified" without having run the command in THIS session and quoting its output.
3. Checklists become todos. Any skill checklist → TodoWrite items; an item is done only when its evidence exists in the transcript.
4. Delegate bulk reads. In orchestrator loops (bulldozer / babysit / flushdeployed), never pull >200-line files or large API dumps into the orchestrator context — subagent it, keep one-line results.
5. 3 strikes → switch substrate (global rule #7). After 2 failed variations of the same probe, go look at the real surface.
6. When a result surprises you, re-read the relevant skill section before improvising.
EOF
PREAMBLE=${PREAMBLE%$'\n'}

# jq -n JSON-encodes the preamble (quotes, em-dashes, arrows, newlines) safely.
# Fail-open: if the emit itself fails, stay silent and exit 0.
jq -n --arg ctx "$PREAMBLE" \
  '{hookSpecificOutput:{hookEventName:"SessionStart",additionalContext:$ctx}}' \
  || exit 0
