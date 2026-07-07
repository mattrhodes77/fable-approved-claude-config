"""Tests for hooks/ledger-append.sh — the automation-ledger validator.

The hook is a VALIDATOR (fail-loud), not a fail-open safety hook, so these assert
it rejects bad input with a nonzero exit and NEVER partial-writes.
"""
import json
import os
import re
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import HookSandbox, REAL_HOOKS

# ledger-append.sh is not in the harness's symlinked HOOK_FILES set, so tests run
# the real script directly under a sandbox HOME (HookSandbox.env() sets $HOME).
LEDGER_HOOK = os.path.join(REAL_HOOKS, "ledger-append.sh")
ISO8601 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class LedgerAppendHookTest(unittest.TestCase):
    def setUp(self):
        self.sbx = HookSandbox()
        self.ledger = os.path.join(self.sbx.claude, "automation-ledger.jsonl")

    def tearDown(self):
        self.sbx.close()

    def _run(self, *args):
        return subprocess.run(
            ["bash", LEDGER_HOOK, *args],
            capture_output=True, text=True, env=self.sbx.env(),
        )

    def _lines(self):
        if not os.path.exists(self.ledger):
            return []
        with open(self.ledger) as fh:
            return [ln for ln in fh.read().splitlines() if ln.strip()]

    def test_valid_object_appended_with_ts(self):
        p = self._run('{"skill":"test","event":"x"}')
        self.assertEqual(p.returncode, 0, p.stderr)
        lines = self._lines()
        self.assertEqual(len(lines), 1, "exactly one line appended")
        rec = json.loads(lines[0])  # must be valid JSON
        self.assertEqual(rec["skill"], "test")
        self.assertIn("ts", rec, "ts injected when absent")
        self.assertRegex(rec["ts"], ISO8601)

    def test_second_append_makes_two_lines(self):
        self.assertEqual(self._run('{"a":1}').returncode, 0)
        self.assertEqual(self._run('{"b":2}').returncode, 0)
        lines = self._lines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["a"], 1)
        self.assertEqual(json.loads(lines[1])["b"], 2)

    def test_existing_ts_preserved(self):
        self.assertEqual(self._run('{"ts":"2020-01-01T00:00:00Z","a":1}').returncode, 0)
        rec = json.loads(self._lines()[0])
        self.assertEqual(rec["ts"], "2020-01-01T00:00:00Z", "existing ts left untouched")

    def test_invalid_json_exits_nonzero_and_writes_nothing(self):
        p = self._run("not json")
        self.assertNotEqual(p.returncode, 0, "bad JSON must fail loudly")
        self.assertFalse(os.path.exists(self.ledger), "must not create the ledger on bad input")

    def test_non_object_json_rejected(self):
        for arg in ("[1,2]", "42", '"str"', "null"):
            p = self._run(arg)
            self.assertNotEqual(p.returncode, 0, "arg %r should be rejected" % arg)

    def test_missing_arg_exits_nonzero(self):
        self.assertNotEqual(self._run().returncode, 0)

    def test_extra_args_exit_nonzero(self):
        self.assertNotEqual(self._run('{"a":1}', '{"b":2}').returncode, 0)

    def test_rejected_append_leaves_prior_ledger_unchanged(self):
        self._run('{"a":1}')
        before = self._lines()
        self.assertNotEqual(self._run("not json").returncode, 0)
        self.assertEqual(self._lines(), before, "a rejected append must not alter the file")


if __name__ == "__main__":
    unittest.main()
