"""Tests for hooks/prlaunch-gate.sh — the PRlaunch per-gate evidence ledger."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import HookSandbox, add_commit, make_git_repo, run_hook_args


class PrlaunchGateTest(unittest.TestCase):
    def setUp(self):
        self.sbx = HookSandbox()
        self.repo = os.path.join(self.sbx.dir, "myrepo")
        self.branch = "me/eng-42-x"
        self.head = make_git_repo(self.repo, self.branch, self.sbx.env())
        self.repo_name = os.path.basename(self.repo)

    def tearDown(self):
        self.sbx.close()

    # -- helpers ----------------------------------------------------------
    def gate(self, *args):
        return run_hook_args(self.sbx, "prlaunch-gate.sh", ["--repo-dir", self.repo, *args])

    def _scenarios_file(self):
        path = os.path.join(self.sbx.dir, "scen.md")
        with open(path, "w") as fh:
            fh.write("scenario 1: user sees rendered bold, PASS if no literal **\n")
        return path

    def _ledger(self):
        path = self.sbx.ledger_path(self.repo_name, self.branch)
        with open(path) as fh:
            return json.load(fh)

    def _record_full_valid(self):
        """Register scenarios + record all four gates at the current HEAD."""
        self.assertEqual(self.gate("record", "deep_review")[0], 0)
        self.assertEqual(self.gate("record", "cr_cli")[0], 0)
        self.assertEqual(self.gate("record", "scenarios", self._scenarios_file())[0], 0)
        self.assertEqual(self.gate("record", "outcome_eval")[0], 0)
        self.assertEqual(self.gate("record", "tests", "--cmd", "pytest -q")[0], 0)

    # -- record happy paths ----------------------------------------------
    def test_record_each_gate_happy_path(self):
        self._record_full_valid()
        led = self._ledger()
        self.assertEqual(set(led["gates"]), {"deep_review", "cr_cli", "outcome_eval", "tests"})
        for name, entry in led["gates"].items():
            self.assertEqual(entry["sha"], self.head, "gate %s should stamp HEAD" % name)
            self.assertIn("ts", entry)
        self.assertEqual(led["gates"]["tests"]["cmd"], "pytest -q")
        self.assertEqual(led["scenarios"]["path"], self._scenarios_file())
        self.assertIn("sha256", led["scenarios"])

    def test_check_passes_when_all_recorded_at_head(self):
        self._record_full_valid()
        rc, out, err = self.gate("check")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("OK", out)

    # -- outcome_eval / scenarios rules ----------------------------------
    def test_outcome_eval_refused_before_scenarios(self):
        rc, out, err = self.gate("record", "outcome_eval")
        self.assertEqual(rc, 1)
        self.assertIn("no scenarios registered", out + err)
        # nothing for outcome_eval should have been written
        path = self.sbx.ledger_path(self.repo_name, self.branch)
        if os.path.exists(path):
            self.assertNotIn("outcome_eval", self._ledger().get("gates", {}))

    def test_outcome_eval_na_allowed_without_scenarios(self):
        rc, out, err = self.gate("record", "outcome_eval", "--na", "no user-facing surface")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(self._ledger()["gates"]["outcome_eval"]["na"], "no user-facing surface")

    # -- cr_cli skip -----------------------------------------------------
    def test_cr_cli_skipped_with_reason_passes_check(self):
        self.assertEqual(self.gate("record", "deep_review")[0], 0)
        self.assertEqual(self.gate("record", "cr_cli", "--skipped", "rate limit")[0], 0)
        self.assertEqual(self.gate("record", "outcome_eval", "--na", "plumbing only")[0], 0)
        self.assertEqual(self.gate("record", "tests")[0], 0)
        rc, out, err = self.gate("check")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(self._ledger()["gates"]["cr_cli"]["skipped"], "rate limit")

    # -- reason-required / flag-scope errors -----------------------------
    def test_skipped_without_reason_errors(self):
        rc, out, err = self.gate("record", "cr_cli", "--skipped")
        self.assertEqual(rc, 1)
        self.assertIn("--skipped requires a reason", out + err)

    def test_na_without_reason_errors(self):
        rc, out, err = self.gate("record", "outcome_eval", "--na")
        self.assertEqual(rc, 1)
        self.assertIn("--na requires a reason", out + err)

    def test_skipped_only_valid_for_cr_cli(self):
        rc, out, err = self.gate("record", "deep_review", "--skipped", "x")
        self.assertEqual(rc, 1)
        self.assertIn("only valid for cr_cli", out + err)

    def test_na_only_valid_for_outcome_eval(self):
        rc, out, err = self.gate("record", "cr_cli", "--na", "x")
        self.assertEqual(rc, 1)
        self.assertIn("only valid for outcome_eval", out + err)

    # -- check failure modes ---------------------------------------------
    def test_check_missing_gate_names_it(self):
        self.assertEqual(self.gate("record", "deep_review")[0], 0)
        rc, out, err = self.gate("check")
        self.assertEqual(rc, 1)
        self.assertIn("MISSING gate: cr_cli", out + err)

    def test_check_fails_naming_stale_gate_after_new_commit(self):
        self._record_full_valid()
        self.assertEqual(self.gate("check")[0], 0)
        new_head = add_commit(self.repo, self.sbx.env())
        self.assertNotEqual(new_head, self.head)
        rc, out, err = self.gate("check")
        self.assertEqual(rc, 1)
        self.assertIn("STALE gate: deep_review", out + err)
        self.assertIn(new_head[:8], out + err)  # prescriptive: re-run on new HEAD


if __name__ == "__main__":
    unittest.main()
