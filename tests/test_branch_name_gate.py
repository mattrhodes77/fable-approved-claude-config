"""Tests for hooks/branch-name-gate.sh — branch-creation Linear link gate.

The gate (read from the script):
  * a branch-creation verb (checkout -b/-B, switch -c/-C, worktree add -b,
    bare `git branch <new>`) with NO ticket token   -> DENY (hard floor).
  * LINEAR_SKIP=1                                    -> ALLOW (early exit).
  * token present, canonical name matches / API down / key missing -> ALLOW
    (fail-open — never blocks real work on a missing dep or API error).
  * token present but != Linear's canonical branchName -> DENY (off-slug).

The sandbox pins LINEAR_BRANCH_PREFIX=eng (see harness._LINEAR_ENV), so the
token under test is `eng-NNN`.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CURL_SHIM, HookSandbox, decision, load_json, run_hook


class BranchNameGateTest(unittest.TestCase):
    def tearDown(self):
        self.sbx.close()

    def _run(self, cmd, extra_env=None, shim=False):
        return run_hook(
            self.sbx, "branch-name-gate.sh",
            {"tool_input": {"command": cmd}},
            extra_env=extra_env, shim_path=shim,
        )

    def test_no_ticket_token_denies(self):
        # No API key configured -> the token floor is still enforced.
        self.sbx = HookSandbox()
        rc, out, _ = self._run("git checkout -b me/2242-image-fix")
        self.assertEqual(decision(out), "deny")
        self.assertIn("no eng-NNN token", load_json(out)["hookSpecificOutput"]["permissionDecisionReason"])

    def test_linear_skip_allows(self):
        self.sbx = HookSandbox()
        rc, out, _ = self._run("git checkout -b me/anything LINEAR_SKIP=1")
        self.assertEqual(rc, 0)
        self.assertIsNone(decision(out))

    def test_api_failure_fails_open(self):
        # token floor passes; the Linear query errors -> hook must NOT block.
        self.sbx = HookSandbox(linear_key="lin_fake")
        self.sbx.add_shim("curl", CURL_SHIM)
        rc, out, _ = self._run(
            "git checkout -b me/eng-2242-x",
            extra_env={"FAKE_MODE": "error"}, shim=True,
        )
        self.assertEqual(rc, 0)
        self.assertIsNone(decision(out))

    def test_canonical_match_allows(self):
        # token present and equals Linear's canonical name -> allow.
        self.sbx = HookSandbox(linear_key="lin_fake")
        self.sbx.add_shim("curl", CURL_SHIM)
        rc, out, _ = self._run(
            "git checkout -b me/eng-2242-image-fix",
            extra_env={"FAKE_CANONICAL": "me/eng-2242-image-fix", "FAKE_NUM": "2242"},
            shim=True,
        )
        self.assertEqual(rc, 0)
        self.assertIsNone(decision(out))

    def test_off_slug_denies(self):
        # token present but the name differs from Linear's canonical -> deny.
        self.sbx = HookSandbox(linear_key="lin_fake")
        self.sbx.add_shim("curl", CURL_SHIM)
        rc, out, _ = self._run(
            "git checkout -b me/eng-2242-quickfix",
            extra_env={"FAKE_CANONICAL": "me/eng-2242-image-fix", "FAKE_NUM": "2242"},
            shim=True,
        )
        self.assertEqual(decision(out), "deny")
        self.assertIn("exact branch name", load_json(out)["hookSpecificOutput"]["permissionDecisionReason"])

    def test_no_key_fails_open_with_ticket_token(self):
        # token present, no API key at all -> fail-open allow (can't verify).
        self.sbx = HookSandbox()  # no key file
        rc, out, _ = self._run("git checkout -b me/eng-2242-x")
        self.assertEqual(rc, 0)
        self.assertIsNone(decision(out))


if __name__ == "__main__":
    unittest.main()
