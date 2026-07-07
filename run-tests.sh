#!/usr/bin/env bash
# run-tests.sh — run the ~/.claude safety-hook test suite.
#
# The hooks/ directory is the safety spine for an autonomous coding agent
# (pr-gate, branch-name-gate, check-careful, linear-startwork, cleanup-sweep,
# reconcile-ticket, ...). This suite exercises each in an ISOLATED temp HOME so a
# regression can't silently reach production. Requires zero pip installs: prefers
# pytest, falls back to stdlib unittest.
#
# Exits nonzero if any test fails.
set -uo pipefail
cd "$(dirname "$0")"

# The suite needs no third-party pytest plugins; disabling autoload keeps a
# broken/unrelated plugin in the ambient environment from crashing collection.
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1

echo "==================================================================="
echo " .claude safety-hook test suite"
echo "==================================================================="

rc=0
if python3 -m pytest --version >/dev/null 2>&1; then
  echo "runner: pytest ($(python3 -m pytest --version 2>&1 | head -1))"
  echo "-------------------------------------------------------------------"
  python3 -m pytest -q tests/
  rc=$?
else
  echo "runner: unittest (pytest unavailable)"
  echo "-------------------------------------------------------------------"
  python3 -m unittest discover -s tests -p 'test_*.py' -v
  rc=$?
fi

echo "-------------------------------------------------------------------"
if [ "$rc" -eq 0 ]; then
  echo "RESULT: PASS"
else
  echo "RESULT: FAIL (exit $rc)"
fi
exit "$rc"
