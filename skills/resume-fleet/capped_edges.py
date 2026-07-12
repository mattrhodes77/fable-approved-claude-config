#!/usr/bin/env python3
"""resume-fleet v0.2 — cheap disk edge-detector for fresh usage-limit events.

Scans recent Claude Code session transcripts for an assistant record whose text is a
usage-limit notice (any form), and reports the MOST RECENT such event across the fleet.
Edge-triggered: the daemon compares the returned max timestamp against the last one it
acted on, so a lingering/old limit line (a session that already resumed) does NOT keep
firing — only a genuinely new cap does.

Usage:
    capped_edges.py [--since ISO8601] [--projects DIR] [--self SESSION_ID]
Prints JSON: {"max_ts": "...|null", "fresh": N, "sessions": [[sid, ts, form], ...]}
`fresh` counts limit events strictly newer than --since (default: epoch → all).
Exit 0 if fresh>0 else 1 (so shell can gate on it).
"""
import os, re, json, glob, sys, argparse

LIMIT_RX = re.compile(
    r"(hit your .*?limit"
    r"|reached your .*?limit"
    r"|usage limit reached"
    r"|monthly spend limit"
    r"|Stop and wait for limit to reset"
    r"|Run /usage-credits"
    r"|/upgrade to increase your usage)",
    re.I,
)
# HARD = forms that halt the main loop on a blocking popup (validated: Esc+continue
# resumes them) -> always worth an immediate UI probe. Everything else (per-model
# "reached your Fable 5 limit … or switch") is SOFT and gets a cooldown in the daemon.
HARD_RX = re.compile(
    r"(monthly spend limit|usage limit reached|Claude usage limit"
    r"|Stop and wait for limit to reset|Add funds to continue)",
    re.I,
)

def last_limit_event(path):
    """Return (iso_ts, matched_form) of the most recent assistant limit-notice, or None."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2); size = f.tell()
            f.seek(max(0, size - 300000))
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    for line in reversed([l for l in data.splitlines() if l.strip()]):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") != "assistant":
            continue
        text = ""
        for c in (d.get("message", {}).get("content") or []):
            if isinstance(c, dict) and c.get("type") == "text":
                text = c.get("text", ""); break
        if not text:
            # an assistant tool-turn AFTER a limit means the session moved on; stop —
            # the most-recent assistant record is not a limit notice.
            return None
        m = LIMIT_RX.search(text)
        if m:
            return (d.get("timestamp", ""), m.group(1)[:40], bool(HARD_RX.search(text)))
        return None  # most-recent assistant text is not a limit → not currently capped
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="")
    # default: all Claude Code project transcript dirs for this user (host-agnostic)
    ap.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--self", dest="self_id", default="")
    ap.add_argument("--limit", type=int, default=24)
    a = ap.parse_args()

    pats = [os.path.join(a.projects, "*.jsonl"), os.path.join(a.projects, "*", "*.jsonl")]
    allf = [f for p in pats for f in glob.glob(p)]
    files = sorted(set(allf), key=os.path.getmtime, reverse=True)[:a.limit]
    sessions, max_ts, fresh, fresh_hard = [], "", 0, 0
    for f in files:
        sid = os.path.basename(f)[:-6]
        if a.self_id and sid == a.self_id:
            continue
        ev = last_limit_event(f)
        if not ev:
            continue
        ts, form, hard = ev
        sessions.append([sid[:8], ts, form, "hard" if hard else "soft"])
        if ts > max_ts:
            max_ts = ts
        if (a.since and ts > a.since) or not a.since:
            fresh += 1
            if hard:
                fresh_hard += 1
    print(json.dumps({"max_ts": max_ts or None, "fresh": fresh,
                      "fresh_hard": fresh_hard, "sessions": sessions}))
    sys.exit(0 if fresh > 0 else 1)

if __name__ == "__main__":
    main()
