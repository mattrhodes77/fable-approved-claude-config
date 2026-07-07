"""Tests for hooks/model-preamble.sh — the model-adaptive SessionStart preamble.

The hook injects a strict "operating mode" additionalContext block whenever the
active session model is weaker than Fable-class, and stays silent (exit 0, no
output) for Fable-class or when the model can't be resolved. Model resolution is
defensive and ordered: (a) .model in the SessionStart stdin payload, (b) the
$ANTHROPIC_MODEL env var, (c) .model in ~/.claude/settings.json (read through
$HOME so this sandbox can intercept it).

model-preamble.sh is deliberately NOT in harness.HOOK_FILES (it isn't part of the
safety spine), so each test symlinks it into the sandbox hooks/ dir itself. Every
run pins ANTHROPIC_MODEL explicitly so an ambient env var can't perturb the
resolution order under test.
"""
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import HookSandbox, REAL_HOOKS, load_json, run_hook

HOOK = "model-preamble.sh"

# The exact additionalContext block the hook must emit for a weaker-than-Fable
# model (with the resolved id substituted). Kept verbatim so the test enforces
# the "EXACTLY this block" requirement, not just a substring.
EXPECTED_TMPL = (
    "OPERATING MODE (model: {model}) — process-first execution:\n"
    "1. Skills are law. If a skill exists for the task, invoke it and follow it "
    "literally. Do not adapt or skip steps that \"seem unnecessary\"; only skip "
    "what a skill explicitly marks optional/N-A.\n"
    "2. Evidence before claims. Never state \"fixed / passing / deployed / "
    "verified\" without having run the command in THIS session and quoting its "
    "output.\n"
    "3. Checklists become todos. Any skill checklist → TodoWrite items; an item "
    "is done only when its evidence exists in the transcript.\n"
    "4. Delegate bulk reads. In orchestrator loops (bulldozer / babysit / "
    "flushdeployed), never pull >200-line files or large API dumps into the "
    "orchestrator context — subagent it, keep one-line results.\n"
    "5. 3 strikes → switch substrate (global rule #7). After 2 failed variations "
    "of the same probe, go look at the real surface.\n"
    "6. When a result surprises you, re-read the relevant skill section before "
    "improvising."
)


class ModelPreambleTest(unittest.TestCase):
    def setUp(self):
        self.sbx = HookSandbox()
        # model-preamble.sh isn't a HOOK_FILES entry — symlink the real script in.
        os.symlink(os.path.join(REAL_HOOKS, HOOK), self.sbx.hook_path(HOOK))

    def tearDown(self):
        self.sbx.close()

    # -- helpers ----------------------------------------------------------
    def _settings(self, model):
        """Write a settings.json into the sandbox HOME (model=None -> no field)."""
        obj = {} if model is None else {"model": model}
        with open(os.path.join(self.sbx.claude, "settings.json"), "w") as fh:
            json.dump(obj, fh)

    def _run(self, payload, model_env=""):
        """Run the hook via the harness with ANTHROPIC_MODEL pinned (default "")."""
        return run_hook(self.sbx, HOOK, payload,
                        extra_env={"ANTHROPIC_MODEL": model_env})

    def _run_raw(self, stdin_text, model_env=""):
        """Pipe RAW bytes on stdin (for the malformed-payload case)."""
        proc = subprocess.run(
            ["bash", self.sbx.hook_path(HOOK)],
            input=stdin_text, capture_output=True, text=True,
            env=self.sbx.env({"ANTHROPIC_MODEL": model_env}),
        )
        return proc.returncode, proc.stdout, proc.stderr

    def _assert_emits(self, rc, out, model):
        self.assertEqual(rc, 0)
        obj = load_json(out)
        self.assertIsNotNone(obj, "expected JSON output, got: %r" % out)
        hso = obj["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "SessionStart")
        ctx = hso["additionalContext"]
        self.assertIn("OPERATING MODE", ctx)
        self.assertIn(model, ctx)                       # resolved id substituted
        self.assertEqual(ctx, EXPECTED_TMPL.format(model=model))  # EXACTLY the block

    def _assert_silent(self, rc, out):
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")

    # -- required cases ---------------------------------------------------
    def test_fable_in_settings_is_silent(self):
        self._settings("claude-fable-5")
        rc, out, _ = self._run({"hook_event_name": "SessionStart", "source": "startup"})
        self._assert_silent(rc, out)

    def test_opus_in_settings_emits_preamble(self):
        self._settings("claude-opus-4-8")
        rc, out, _ = self._run({"hook_event_name": "SessionStart", "source": "startup"})
        self._assert_emits(rc, out, "claude-opus-4-8")

    def test_unresolvable_is_silent(self):
        # No stdin model, no env, no settings.json at all -> nothing to resolve.
        rc, out, _ = self._run({"hook_event_name": "SessionStart", "source": "startup"})
        self._assert_silent(rc, out)

    def test_malformed_stdin_never_crashes(self):
        # Not JSON, no env, no settings -> jq parse fails, hook swallows it.
        rc, out, err = self._run_raw("this is not json {{{ ]]]")
        self.assertEqual(rc, 0, "stderr: %s" % err)
        self.assertEqual(out.strip(), "")

    def test_empty_stdin_is_silent(self):
        rc, out, _ = self._run_raw("")
        self._assert_silent(rc, out)

    # -- resolution order (defensive fallback) ----------------------------
    def test_stdin_model_wins_over_settings(self):
        # Source (a) precedes source (c): stdin opus beats fable settings -> emit.
        self._settings("claude-fable-5")
        rc, out, _ = self._run({"model": "claude-opus-4-8", "source": "startup"})
        self._assert_emits(rc, out, "claude-opus-4-8")

    def test_stdin_fable_wins_over_settings_opus(self):
        # And a Fable stdin model silences even an opus settings.json.
        self._settings("claude-opus-4-8")
        rc, out, _ = self._run({"model": "claude-fable-5", "source": "startup"})
        self._assert_silent(rc, out)

    def test_env_wins_over_settings(self):
        # Source (b) precedes source (c): env opus beats fable settings -> emit.
        self._settings("claude-fable-5")
        rc, out, _ = self._run({"source": "startup"}, model_env="claude-opus-4-8")
        self._assert_emits(rc, out, "claude-opus-4-8")

    def test_settings_used_when_no_stdin_or_env(self):
        # Source (c) is the last resort when (a) and (b) are absent.
        self._settings("claude-sonnet-4-5")
        rc, out, _ = self._run({"source": "startup"})
        self._assert_emits(rc, out, "claude-sonnet-4-5")

    def test_fable_match_is_case_insensitive(self):
        rc, out, _ = self._run({"source": "startup"}, model_env="Claude-FABLE-5")
        self._assert_silent(rc, out)

    def test_settings_without_model_field_is_silent(self):
        # settings.json exists but has no .model, nothing else resolves -> silent.
        self._settings(None)
        rc, out, _ = self._run({"source": "startup"})
        self._assert_silent(rc, out)


if __name__ == "__main__":
    unittest.main()
