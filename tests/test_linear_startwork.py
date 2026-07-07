"""Tests for hooks/linear-startwork.sh — branch-creation -> take Linear ticket.

Focus: the branch-name EXTRACTION regressions this hook has had before, where a
trailing `2>&1` or a `git worktree add ... origin/develop` base ref got misread
as the branch name.

Observation trick: with the branch actually present, a Linear API key set, and
curl shimmed to return an empty node set, the hook emits
  "Note: created branch <newbranch> but couldn't find the matching ticket ..."
which surfaces the EXACT token the hook extracted — letting us assert it is the
real branch and NOT `2>&1` / `origin/develop`. (The sandbox pins
LINEAR_BRANCH_PREFIX=eng, so branches under test carry `eng-NNN` tokens.)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CURL_SHIM, HookSandbox, load_json, make_git_repo, run_hook


class LinearStartworkTest(unittest.TestCase):
    def setUp(self):
        self.sbx = HookSandbox(linear_key="lin_fake")
        self.sbx.add_shim("curl", CURL_SHIM)  # returns empty nodes -> "couldn't find"
        self.repo = os.path.join(self.sbx.dir, "repo")
        # The branch must exist for the hook to proceed past its rev-parse guard.
        make_git_repo(self.repo, "me/eng-9999-x", self.sbx.env())

    def tearDown(self):
        self.sbx.close()

    def _msg(self, cmd):
        rc, out, _ = run_hook(
            self.sbx, "linear-startwork.sh",
            {"tool_input": {"command": cmd}, "cwd": self.repo},
            shim_path=True,
        )
        self.assertEqual(rc, 0)
        obj = load_json(out)
        self.assertIsNotNone(obj, "expected a systemMessage, got: %r" % out)
        return obj.get("systemMessage", "")

    def test_redirect_suffix_not_read_as_branch(self):
        # Regression: `... 2>&1` must NOT become the branch name.
        msg = self._msg("git checkout -b me/eng-9999-x 2>&1")
        self.assertIn("me/eng-9999-x", msg)
        self.assertNotIn("2>&1", msg)
        self.assertIn("couldn't find", msg)

    def test_worktree_base_ref_not_read_as_branch(self):
        # Regression: `git worktree add -b <branch> <path> origin/develop` must
        # extract <branch>, not the `origin/develop` base ref or the path.
        msg = self._msg("git worktree add -b me/eng-9999-x /tmp/wt origin/develop")
        self.assertIn("me/eng-9999-x", msg)
        self.assertNotIn("origin/develop", msg)
        self.assertNotIn("/tmp/wt", msg)
        self.assertIn("couldn't find", msg)

    def test_branch_name_in_commit_message_is_not_creation(self):
        # A branch-create verb inside a quoted commit message is command DATA,
        # not structure -> the hook must stay silent (no branch creation).
        rc, out, _ = run_hook(
            self.sbx, "linear-startwork.sh",
            {"tool_input": {"command": 'git commit -m "git checkout -b me/eng-9999-x"'},
             "cwd": self.repo},
            shim_path=True,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")  # silent no-op


if __name__ == "__main__":
    unittest.main()
