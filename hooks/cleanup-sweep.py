#!/usr/bin/env python3
"""Cleanup-queue helper for check-careful.sh's deferred deletes.

Reads ~/.claude/cleanup-needed.log (JSON-lines: {ts,cwd,cmd,reason}) — written
by the careful hook when an unrecognized delete is deferred during an
unattended loop. Shared by the cleanup sweep:
  - /babysit-prs (unattended)        -> `--count` / default report (surface only)
  - /cleanup, /wrapup, /PRlaunch     -> `--run` to action, `--remove` to decline.

  cleanup-sweep.py            human-readable summary (default)
  cleanup-sweep.py --count    just the number of pending entries
  cleanup-sweep.py --json     one entry per line with an 'i' index (for resolve)
  cleanup-sweep.py --run N    DELETE entry N's parsed targets, then drop entry N
  cleanup-sweep.py --run-all  --run every entry (descending, so indices hold)
  cleanup-sweep.py --remove N drop entry index N WITHOUT deleting (declined)

Why `--run` instead of re-running the queued command:
  The careful hook (check-careful.sh) defers ANY unrecognized `rm -r` — so
  replaying the stored `cmd` just re-defers it. Worse, a queued `cmd` is the
  *original* command that happened to contain the rm (e.g. `git worktree add`
  after an `rm -rf`, or a `git clone` into a scratch dir) — re-running it would
  RECREATE the scratch, not clear it. So `--run` parses the command for its
  actual delete targets (cd- and VAR=-aware, glob-expanded, relative to the
  entry's cwd), reusing careful-rm.py's parser, and deletes ONLY those, via
  shutil/os — no side effects, no re-defer.
"""
import sys
import os
import re
import glob
import shutil
import shlex
import json
import importlib.util

LOG = os.path.expanduser("~/.claude/cleanup-needed.log")

# Reuse the rm parser (segments / rm_targets) so target extraction matches the
# exact logic the careful hook used to defer the delete in the first place.
_CR = None


def _careful_rm():
    global _CR
    if _CR is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "careful-rm.py")
        spec = importlib.util.spec_from_file_location("careful_rm", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _CR = mod
    return _CR


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


_VAR = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)')


def _expand(s, local_vars):
    """Expand $VAR / ${VAR} from local assignments, then the real environment.
    Unknown variables are left intact (so resolve() can detect + skip them)."""
    def repl(m):
        name = m.group(1) or m.group(2)
        if name in local_vars:
            return local_vars[name]
        return os.environ.get(name, m.group(0))
    return _VAR.sub(repl, s)


def _resolve(t, cwd, local_vars):
    t = os.path.expanduser(_expand(t, local_vars))
    if not os.path.isabs(t):
        t = os.path.join(cwd, t)
    return os.path.normpath(t)


_ASSIGN = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')


def extract_targets(cmd, base_cwd):
    """Parse a queued command into its real delete targets.

    Tracks `cd` (so a target relative to a mid-command cd resolves correctly)
    and simple NAME=value assignments (so `WT=/tmp/x; rm -rf "$WT"` resolves).
    Returns resolved absolute path patterns (globs preserved for delete()).
    """
    cr = _careful_rm()
    cwd = os.path.expanduser(base_cwd) if base_cwd else os.path.expanduser("~")
    local_vars, targets = {}, []
    for seg in cr.segments(cmd):
        try:
            toks = shlex.split(seg, posix=True)
        except ValueError:
            toks = seg.split()
        if not toks:
            continue
        k = 0
        while k < len(toks) and _ASSIGN.match(toks[k]):
            name, val = toks[k].split('=', 1)
            local_vars[name] = os.path.expanduser(_expand(val, local_vars))
            k += 1
        rest = toks[k:]
        if not rest:
            continue
        if rest[0] == 'cd' and len(rest) > 1:
            cwd = _resolve(rest[1], cwd, local_vars)
        elif rest[0] == 'rm':
            for t in cr.rm_targets(rest[1:]):
                targets.append(_resolve(t, cwd, local_vars))
    return targets


# Paths we refuse to delete even when approved — a parser slip must never nuke
# the home dir or the filesystem root.
def _is_catastrophic(ap):
    home = os.path.expanduser("~")
    return ap in ("", "/", home, os.path.dirname(home))


def delete_targets(targets):
    cr = _careful_rm()
    deleted, missing, errors, skipped = [], [], [], []
    for p in targets:
        if "$" in p:
            skipped.append((p, "unresolved shell variable"))
            continue
        ap = os.path.abspath(p)
        if _is_catastrophic(ap):
            skipped.append((ap, "refused: catastrophic path"))
            continue
        hits = glob.glob(ap)
        if not hits:
            missing.append(ap)
            continue
        for h in hits:
            _, label = cr.classify(h)
            try:
                if os.path.isdir(h) and not os.path.islink(h):
                    shutil.rmtree(h)
                else:
                    os.remove(h)
                deleted.append((h, label))
            except OSError as e:
                errors.append((h, str(e)))
    return deleted, missing, errors, skipped


def run_entry(entries, n):
    """Delete entry n's parsed targets; drop the entry iff fully resolved.
    Returns True if the entry was dropped."""
    e = entries[n]
    targets = extract_targets(e.get("cmd", ""), e.get("cwd", ""))
    print(f"\n[{n}] in {e.get('cwd') or '?'}")
    if not targets:
        print("    no delete targets parsed (nothing to do) — left in queue for manual review")
        return False
    deleted, missing, errors, skipped = delete_targets(targets)
    for h, label in deleted:
        print(f"    ✓ deleted: {h} — {label}")
    for ap in missing:
        print(f"    · already gone: {ap}")
    for h, msg in errors:
        print(f"    ✗ error: {h} — {msg}")
    for p, why in skipped:
        print(f"    ⚠ skipped: {p} — {why}")
    if errors or skipped:
        print(f"    → kept entry [{n}] (unresolved targets above)")
        return False
    entries.pop(n)
    print(f"    → cleared entry [{n}]")
    return True


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
    if args and args[0] == "--run":
        try:
            n = int(args[1])
        except (IndexError, ValueError):
            print("usage: cleanup-sweep.py --run N", file=sys.stderr)
            sys.exit(2)
        if not (0 <= n < len(entries)):
            print(f"no entry [{n}] (queue has {len(entries)})", file=sys.stderr)
            sys.exit(2)
        run_entry(entries, n)
        save(entries)
        return
    if args and args[0] == "--run-all":
        if not entries:
            print("🧹 No cleanups pending.")
            return
        for n in range(len(entries) - 1, -1, -1):  # descending: indices stay valid
            run_entry(entries, n)
        save(entries)
        print(f"\n=== {len(entries)} entr{'y' if len(entries) == 1 else 'ies'} remaining ===")
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
