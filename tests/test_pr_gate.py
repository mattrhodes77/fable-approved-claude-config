"""Tests for hooks/pr-gate.sh — the PR gate + Linear link gate.

pr-gate.sh validates a per-gate JSON ledger and only falls back
to the LEGACY plain-sha marker (with a migration warning) when no ledger exists.
These tests cover both paths plus the Linear link gate and escape hatches.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import HookSandbox, decision, load_json, make_git_repo, run_hook

# Built from fragments so the literal token never trips the live pr-gate hook
# while THIS test file is being edited by an agent. At runtime pytest just reads
# it as a Python string.
GHPR = "gh pr " + "create"


class PrGateTest(unittest.TestCase):
    def setUp(self):
        self.sbx = HookSandbox()
        self.repo = os.path.join(self.sbx.dir, "myrepo")
        self.branch = "me/eng-1234-x"
        self.head = make_git_repo(self.repo, self.branch, self.sbx.env())
        self.repo_name = os.path.basename(self.repo)

    def tearDown(self):
        self.sbx.close()

    def _run(self, cmd):
        return run_hook(
            self.sbx, "pr-gate.sh",
            {"tool_input": {"command": cmd}, "cwd": self.repo},
        )

    def _gate_entry(self, sha):
        return {"sha": sha, "ts": "2026-07-06T00:00:00Z"}

    def _valid_gates(self):
        return {g: self._gate_entry(self.head)
                for g in ("deep_review", "cr_cli", "outcome_eval", "tests")}

    def _reason(self, out):
        return load_json(out)["hookSpecificOutput"]["permissionDecisionReason"]

    # -- no evidence at all ----------------------------------------------
    def test_no_marker_denies(self):
        rc, out, _ = self._run(GHPR + " --fill")
        self.assertEqual(decision(out), "deny")
        self.assertIn("no PRlaunch gate record", self._reason(out))

    def test_no_ledger_no_legacy_denies(self):
        # Explicit: neither a JSON ledger nor a legacy marker file present.
        rc, out, _ = self._run(GHPR + " --fill")
        self.assertEqual(decision(out), "deny")
        self.assertIn("no PRlaunch gate record", self._reason(out))

    # -- JSON ledger (authoritative) -------------------------------------
    def test_valid_ledger_allows(self):
        self.sbx.write_ledger(self.repo_name, self.branch, self._valid_gates())
        rc, out, _ = self._run(GHPR + " --fill")
        self.assertEqual(rc, 0)
        self.assertIsNone(decision(out))  # silent allow

    def test_ledger_stale_deep_review_denies_naming_it(self):
        gates = self._valid_gates()
        gates["deep_review"] = self._gate_entry("deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")
        self.sbx.write_ledger(self.repo_name, self.branch, gates)
        rc, out, _ = self._run(GHPR + " --fill")
        self.assertEqual(decision(out), "deny")
        reason = self._reason(out)
        self.assertIn("deep_review", reason)
        self.assertIn("STALE", reason)

    def test_ledger_missing_gate_denies_naming_it(self):
        gates = self._valid_gates()
        del gates["cr_cli"]
        self.sbx.write_ledger(self.repo_name, self.branch, gates)
        rc, out, _ = self._run(GHPR + " --fill")
        self.assertEqual(decision(out), "deny")
        self.assertIn("cr_cli", self._reason(out))

    # -- LEGACY plain-sha marker (back-compat) ---------------------------
    def test_legacy_marker_matches_head_warns_and_allows(self):
        # No JSON ledger, only a legacy plain-sha marker at HEAD → allowed with
        # a migration warning (the systemMessage back-compat notice).
        self.sbx.write_marker(self.repo_name, self.branch, self.head)
        rc, out, _ = self._run(GHPR + " --fill")
        self.assertEqual(rc, 0)
        self.assertIsNone(decision(out))  # a bare notice, not a deny/allow decision
        self.assertIn("legacy PRlaunch marker accepted", load_json(out)["systemMessage"])

    def test_legacy_marker_mismatch_denies(self):
        self.sbx.write_marker(self.repo_name, self.branch, "deadbeefdeadbeef")
        rc, out, _ = self._run(GHPR + " --fill")
        self.assertEqual(decision(out), "deny")
        self.assertIn("changed since PRlaunch", self._reason(out))

    def test_ledger_beats_legacy_marker(self):
        # Both present: a VALID ledger wins over a stale legacy marker → allow.
        self.sbx.write_ledger(self.repo_name, self.branch, self._valid_gates())
        self.sbx.write_marker(self.repo_name, self.branch, "deadbeefdeadbeef")
        rc, out, _ = self._run(GHPR + " --fill")
        self.assertEqual(rc, 0)
        self.assertIsNone(decision(out))

    # -- escape hatches & link gate --------------------------------------
    def test_prlaunch_skip_allows(self):
        # No evidence at all, but the escape hatch bypasses everything.
        rc, out, _ = self._run("PRLAUNCH_SKIP=1 " + GHPR + " --fill")
        self.assertEqual(rc, 0)
        self.assertIsNone(decision(out))

    def test_link_gate_denies_without_dev_token(self):
        # Branch has no ticket token and the command carries no ticket id.
        repo = os.path.join(self.sbx.dir, "nolink")
        make_git_repo(repo, "feature/nolink", self.sbx.env())
        rc, out, _ = run_hook(
            self.sbx, "pr-gate.sh",
            {"tool_input": {"command": GHPR + " --fill"}, "cwd": repo},
        )
        self.assertEqual(decision(out), "deny")
        self.assertIn("won't link to a tracker ticket", self._reason(out))

    def test_linear_skip_bypasses_link_gate(self):
        # Un-linkable branch, but LINEAR_SKIP=1 clears the link gate; a valid
        # ledger then lets it through — proving the link gate was bypassed.
        repo = os.path.join(self.sbx.dir, "nolink2")
        head = make_git_repo(repo, "feature/nolink2", self.sbx.env())
        gates = {g: {"sha": head, "ts": "2026-07-06T00:00:00Z"}
                 for g in ("deep_review", "cr_cli", "outcome_eval", "tests")}
        self.sbx.write_ledger(os.path.basename(repo), "feature/nolink2", gates)
        rc, out, _ = run_hook(
            self.sbx, "pr-gate.sh",
            {"tool_input": {"command": "LINEAR_SKIP=1 " + GHPR + " --fill"}, "cwd": repo},
        )
        self.assertEqual(rc, 0)
        self.assertIsNone(decision(out))

    # -- repo dir resolution ---------------------------------------------
    def test_repo_dir_takes_the_last_cd_before_the_trigger(self):
        # `cd /elsewhere && cd <repo> && gh pr create` lands in <repo>, so the
        # gate must key the ledger off <repo>. Taking the FIRST cd consults the
        # decoy's (nonexistent) ledger and denies a properly gated PR.
        decoy = os.path.join(self.sbx.dir, "decoy")
        make_git_repo(decoy, "me/eng-9999-decoy", self.sbx.env())
        self.sbx.write_ledger(self.repo_name, self.branch, self._valid_gates())
        cmd = "cd " + decoy + " && cd " + self.repo + " && " + GHPR + " --fill"
        rc, out, _ = run_hook(
            self.sbx, "pr-gate.sh",
            {"tool_input": {"command": cmd}, "cwd": self.sbx.dir},
        )
        self.assertEqual(rc, 0)
        self.assertIsNone(decision(out), "should resolve the target repo, not the decoy")

    def test_repo_dir_prefers_the_last_cd_over_a_later_git_dash_c(self):
        # `git -C <dir>` runs one command elsewhere; it does NOT move the
        # shell. The PR is still created from the last cd, so a `git -C` on
        # another repo must not hijack the resolution.
        decoy = os.path.join(self.sbx.dir, "decoy3")
        make_git_repo(decoy, "me/eng-7777-decoy", self.sbx.env())
        self.sbx.write_ledger(self.repo_name, self.branch, self._valid_gates())
        cmd = ("cd " + self.repo + " && git -C " + decoy + " fetch && "
               + GHPR + " --fill")
        rc, out, _ = run_hook(
            self.sbx, "pr-gate.sh",
            {"tool_input": {"command": cmd}, "cwd": self.sbx.dir},
        )
        self.assertEqual(rc, 0)
        self.assertIsNone(decision(out), "cd wins over a later git -C")

    def test_repo_dir_falls_back_to_git_dash_c_when_there_is_no_cd(self):
        # With no cd at all, `git -C <dir>` is the only signal for which repo
        # the PR belongs to — keep honouring it.
        self.sbx.write_ledger(self.repo_name, self.branch, self._valid_gates())
        cmd = "git -C " + self.repo + " push && " + GHPR + " --fill"
        rc, out, _ = run_hook(
            self.sbx, "pr-gate.sh",
            {"tool_input": {"command": cmd}, "cwd": self.sbx.dir},
        )
        self.assertEqual(rc, 0)
        self.assertIsNone(decision(out))

    def test_repo_dir_ignores_a_cd_after_the_trigger(self):
        # A trailing `&& cd /elsewhere` runs only after the PR is created; it
        # must not decide which ledger is checked.
        decoy = os.path.join(self.sbx.dir, "decoy2")
        make_git_repo(decoy, "me/eng-8888-decoy", self.sbx.env())
        self.sbx.write_ledger(self.repo_name, self.branch, self._valid_gates())
        cmd = "cd " + self.repo + " && " + GHPR + " --fill && cd " + decoy
        rc, out, _ = run_hook(
            self.sbx, "pr-gate.sh",
            {"tool_input": {"command": cmd}, "cwd": self.sbx.dir},
        )
        self.assertEqual(rc, 0)
        self.assertIsNone(decision(out))

    def test_quoted_mention_does_not_trigger(self):
        # The trigger token appears only inside a single-quoted string, so the
        # gate must strip it and never fire (even though there's no marker).
        cmd = "echo 'remember to " + GHPR + " later'"
        rc, out, _ = self._run(cmd)
        self.assertEqual(rc, 0)
        self.assertIsNone(decision(out))


if __name__ == "__main__":
    unittest.main()
