"""Static / integrity checks over the repo.

  * shellcheck over hooks/*.sh at --severity=error (SKIPPED if shellcheck absent)
  * python3 -m compileall hooks/
  * settings.json is valid JSON (via jq if present, else json.load) — SKIPPED
    when the repo carries no settings.json (this public repo documents the hook
    wiring in the README instead)
  * every hook script referenced in settings.json exists in hooks/ and (for .sh)
    is executable — same skip rule
  * every commands/*.md and skills/*/SKILL.md has parseable YAML frontmatter with
    the keys its format requires (commands: description; skills: name+description)
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import REPO_ROOT

HOOKS_DIR = os.path.join(REPO_ROOT, "hooks")
SETTINGS = os.path.join(REPO_ROOT, "settings.json")


def _parse_frontmatter(text):
    """Zero-dependency frontmatter parse.

    Returns the set of top-level keys in the leading `---`...`---` block, or
    raises ValueError if there is no well-formed frontmatter block.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing opening '---'")
    body = []
    closed = False
    for ln in lines[1:]:
        if ln.strip() == "---":
            closed = True
            break
        body.append(ln)
    if not closed:
        raise ValueError("missing closing '---'")
    keys = set()
    for ln in body:
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        # Top-level key: no leading whitespace, matches `key:`
        m = re.match(r"^([A-Za-z0-9_-]+):", ln)
        if m:
            keys.add(m.group(1))
    return keys


class StaticChecksTest(unittest.TestCase):
    def test_shellcheck_errors(self):
        if not shutil.which("shellcheck"):
            self.skipTest("shellcheck not installed — SKIPPED")
        scripts = sorted(glob.glob(os.path.join(HOOKS_DIR, "*.sh")))
        self.assertTrue(scripts, "no hook shell scripts found")
        proc = subprocess.run(
            ["shellcheck", "--severity=error", *scripts],
            capture_output=True, text=True,
        )
        self.assertEqual(
            proc.returncode, 0,
            "shellcheck found error-level issues:\n%s%s" % (proc.stdout, proc.stderr),
        )

    def test_compileall_hooks(self):
        proc = subprocess.run(
            [sys.executable, "-m", "compileall", "-q", HOOKS_DIR],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_settings_is_valid_json(self):
        if not os.path.exists(SETTINGS):
            self.skipTest("no settings.json in this repo — hook wiring lives in the README")
        if shutil.which("jq"):
            proc = subprocess.run(["jq", "empty", SETTINGS], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, "jq rejected settings.json:\n" + proc.stderr)
        with open(SETTINGS) as fh:
            json.load(fh)  # also parse in-process

    def test_referenced_hook_scripts_exist_and_executable(self):
        if not os.path.exists(SETTINGS):
            self.skipTest("no settings.json in this repo — hook wiring lives in the README")
        with open(SETTINGS) as fh:
            settings = json.load(fh)
        commands = []

        def walk(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    if k == "command" and isinstance(v, str):
                        commands.append(v)
                    else:
                        walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(settings.get("hooks", {}))
        # Match hook scripts referenced under a .claude/hooks/ path. Validate by
        # basename against THIS repo's hooks/ dir so the check is machine-portable
        # (settings.json hardcodes an absolute /Users/... path).
        referenced = set()
        for cmd in commands:
            for m in re.finditer(r"/hooks/([A-Za-z0-9._-]+\.(?:sh|py))", cmd):
                referenced.add(m.group(1))
        self.assertIn("pr-gate.sh", referenced, "sanity: expected pr-gate.sh to be referenced")
        for name in sorted(referenced):
            path = os.path.join(HOOKS_DIR, name)
            self.assertTrue(os.path.isfile(path), "referenced hook missing: %s" % name)
            if name.endswith(".sh"):
                self.assertTrue(os.access(path, os.X_OK), "referenced hook not executable: %s" % name)

    def test_command_frontmatter(self):
        files = sorted(glob.glob(os.path.join(REPO_ROOT, "commands", "*.md")))
        self.assertTrue(files, "no command files found")
        for path in files:
            with open(path) as fh:
                keys = _parse_frontmatter(fh.read())
            self.assertIn("description", keys, "%s: frontmatter needs description" % os.path.basename(path))

    def test_skill_frontmatter(self):
        files = sorted(glob.glob(os.path.join(REPO_ROOT, "skills", "*", "SKILL.md")))
        self.assertTrue(files, "no SKILL.md files found")
        for path in files:
            with open(path) as fh:
                keys = _parse_frontmatter(fh.read())
            rel = os.path.relpath(path, REPO_ROOT)
            self.assertIn("name", keys, "%s: frontmatter needs name" % rel)
            self.assertIn("description", keys, "%s: frontmatter needs description" % rel)


if __name__ == "__main__":
    unittest.main()
