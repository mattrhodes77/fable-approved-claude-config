#!/usr/bin/env python3
"""Cleanup-queue helper for check-careful.sh's deferred deletes.

Reads ~/.claude/cleanup-needed.log (JSON-lines: {ts,cwd,cmd,reason}) — written
by the careful hook when an unrecognized delete is deferred during an
unattended loop. Shared by the cleanup sweep:
  - /babysit-prs (unattended)        -> `--count` / default report (surface only)
  - /cleanup, /wrapup, /PRlaunch     -> `--json` to iterate + re-run, `--remove`
                                        to drop each handled entry.

  cleanup-sweep.py            human-readable summary (default)
  cleanup-sweep.py --count    just the number of pending entries
  cleanup-sweep.py --json     one entry per line with an 'i' index (for resolve)
  cleanup-sweep.py --remove N drop entry index N and rewrite the log
"""
import sys
import os
import json

LOG = os.path.expanduser("~/.claude/cleanup-needed.log")


def load():
    out = []
    try:
        with open(LOG) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    out.append({"cmd": line, "cwd": "", "reason": "", "ts": 0})
    except FileNotFoundError:
        pass
    return out


def save(entries):
    if not entries:
        try:
            os.remove(LOG)
        except FileNotFoundError:
            pass
        return
    with open(LOG, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def main():
    args = sys.argv[1:]
    entries = load()

    if args and args[0] == "--count":
        print(len(entries))
        return
    if args and args[0] == "--json":
        for i, e in enumerate(entries):
            e = dict(e)
            e["i"] = i
            print(json.dumps(e))
        return
    if args and args[0] == "--remove":
        try:
            n = int(args[1])
        except (IndexError, ValueError):
            print("usage: cleanup-sweep.py --remove N", file=sys.stderr)
            sys.exit(2)
        if 0 <= n < len(entries):
            entries.pop(n)
            save(entries)
        return

    # default: human-readable report
    if not entries:
        print("🧹 No cleanups pending.")
        return
    print(f"🧹 {len(entries)} cleanup(s) pending (deferred during unattended runs):")
    for i, e in enumerate(entries):
        print(f"\n[{i}] in {e.get('cwd') or '?'}")
        print(f"    $ {e.get('cmd', '')}")
        for ln in (e.get("reason", "") or "").splitlines():
            print(f"    {ln}")


if __name__ == "__main__":
    main()
