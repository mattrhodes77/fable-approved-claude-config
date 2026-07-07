"""Tests for hooks/cleanup-sweep.py — the deferred-delete queue helper."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import HookSandbox, run_python_hook


class CleanupSweepTest(unittest.TestCase):
    def setUp(self):
        self.sbx = HookSandbox()

    def tearDown(self):
        self.sbx.close()

    def _seed(self, entry):
        with open(self.sbx.cleanup_log, "a") as fh:
            fh.write(json.dumps(entry) + "\n")

    def _sweep(self, *args):
        return run_python_hook(self.sbx, "cleanup-sweep.py", args)

    def test_count_empty_is_zero(self):
        rc, out, _ = self._sweep("--count")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "0")

    def test_json_parses_seeded_entry(self):
        target = os.path.join(self.sbx.dir, "victim")
        os.makedirs(target)
        self._seed({"ts": 1, "cwd": "/tmp", "cmd": "rm -rf %s" % target, "reason": "unrecognized"})
        rc, out, _ = self._sweep("--json")
        self.assertEqual(rc, 0)
        rows = [json.loads(ln) for ln in out.splitlines() if ln.strip()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["i"], 0)
        self.assertIn("victim", rows[0]["cmd"])

    def test_run_deletes_target_and_drops_entry(self):
        target = os.path.join(self.sbx.dir, "victim")
        os.makedirs(target)
        self._seed({"ts": 1, "cwd": "/tmp", "cmd": "rm -rf %s" % target, "reason": "unrecognized"})

        rc, out, _ = self._sweep("--run", "0")
        self.assertEqual(rc, 0)
        self.assertIn("deleted", out)
        self.assertFalse(os.path.exists(target), "target should be gone")

        rc, out, _ = self._sweep("--count")
        self.assertEqual(out.strip(), "0", "entry should be dropped after successful run")

    def test_catastrophic_path_refused(self):
        # A parser slip pointing at "/" must be refused, and the entry kept.
        self._seed({"ts": 1, "cwd": "/", "cmd": "rm -rf /", "reason": "x"})
        rc, out, _ = self._sweep("--run", "0")
        self.assertEqual(rc, 0)
        self.assertIn("refused", out.lower())
        self.assertTrue(os.path.isdir("/"), "root must still exist")
        rc, out, _ = self._sweep("--count")
        self.assertEqual(out.strip(), "1", "refused entry must remain queued")


if __name__ == "__main__":
    unittest.main()
