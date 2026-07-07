"""Tests for hooks/check-careful.sh (+ careful-rm.py) — destructive-command gate.

Also verifies HOME isolation: the deferred-delete path appends to the SANDBOX
cleanup log, and the REAL ~/.claude/cleanup-needed.log must not grow.
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (
    REAL_CLEANUP_LOG, HookSandbox, decision, load_json, run_hook,
)


class CheckCarefulTest(unittest.TestCase):
    def setUp(self):
        self.sbx = HookSandbox()

    def tearDown(self):
        self.sbx.close()

    def _run(self, cmd, cwd="/tmp"):
        return run_hook(
            self.sbx, "check-careful.sh",
            {"tool_input": {"command": cmd}, "cwd": cwd},
        )

    def test_routine_rm_allows_silently(self):
        rc, out, _ = self._run("rm -rf .venv")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "{}")
        self.assertIsNone(decision(out))

    def test_unrecognized_rm_defers_and_logs(self):
        real_before = os.path.getsize(REAL_CLEANUP_LOG) if os.path.exists(REAL_CLEANUP_LOG) else 0
        self.assertFalse(os.path.exists(self.sbx.cleanup_log))

        rc, out, _ = self._run("rm -rf /Users/x/some-project", cwd="/Users/x")
        self.assertEqual(decision(out), "deny")
        reason = load_json(out)["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("Deferred this delete", reason)

        # A line was appended to the SANDBOX cleanup log...
        self.assertTrue(os.path.exists(self.sbx.cleanup_log))
        with open(self.sbx.cleanup_log) as fh:
            lines = [ln for ln in fh if ln.strip()]
        self.assertEqual(len(lines), 1)

        # ...and the REAL cleanup log was untouched (isolation guarantee).
        real_after = os.path.getsize(REAL_CLEANUP_LOG) if os.path.exists(REAL_CLEANUP_LOG) else 0
        self.assertEqual(real_before, real_after, "REAL cleanup-needed.log must not grow")

    def test_force_push_asks(self):
        rc, out, _ = self._run("git push --force origin main")
        self.assertEqual(decision(out), "ask")
        self.assertIn("force-push", load_json(out)["hookSpecificOutput"]["permissionDecisionReason"].lower())

    def test_force_with_lease_does_not_warn(self):
        rc, out, _ = self._run("git push --force-with-lease origin main")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "{}")

    def test_loop_mode_future_epoch_auto_proceeds(self):
        self.sbx.arm_loop_mode(int(time.time()) + 3600)
        rc, out, _ = self._run("git push --force origin main")
        self.assertEqual(decision(out), "allow")
        self.assertIn("loop-mode", load_json(out)["hookSpecificOutput"]["permissionDecisionReason"])
        # armed file survives (still in the future)
        self.assertTrue(os.path.exists(self.sbx.loop_mode))

    def test_loop_mode_past_epoch_self_disarms_and_asks(self):
        self.sbx.arm_loop_mode(100)  # long past
        rc, out, _ = self._run("git push --force origin main")
        self.assertEqual(decision(out), "ask")
        # expired loop-mode file self-disarmed (deleted) so it can't poison later
        self.assertFalse(os.path.exists(self.sbx.loop_mode))


if __name__ == "__main__":
    unittest.main()
