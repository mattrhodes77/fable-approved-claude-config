#!/usr/bin/env python3
"""Analyze a shell command (on stdin) for risky `rm -r` deletes.

Prints a plain-English, itemized WARN to stdout when an `rm -r` targets
anything that isn't routine/regenerable. Prints NOTHING when every target is
safe (virtualenv / build cache / test DB / temp / log) or when there's no
gated recursive rm. Quote-, comment-, newline-, and redirection-aware, so
`rm` mentioned inside a quoted argument or after a `#` comment is not treated
as a real delete.

Used by check-careful.sh. Kept as a real parser because a bash word-loop
mis-reads comments, newlines, and quotes.
"""
import sys
import shlex


def segments(s):
    """Split into top-level command segments — quote-aware, comments stripped,
    breaking on unquoted ; & | && || and newlines."""
    segs, buf = [], []
    i, n, q = 0, len(s), None
    while i < n:
        c = s[i]
        if q:                         # inside a quote: copy until it closes
            buf.append(c)
            if c == q:
                q = None
            i += 1
            continue
        if c in ("'", '"'):
            q = c
            buf.append(c)
            i += 1
            continue
        if c == '#':                  # unquoted comment -> skip to end of line
            while i < n and s[i] != '\n':
                i += 1
            continue
        if c in ';&|\n':              # command separator
            segs.append(''.join(buf))
            buf = []
            if c != '\n' and i + 1 < n and s[i + 1] == c:   # && or ||
                i += 2
            else:
                i += 1
            continue
        buf.append(c)
        i += 1
    segs.append(''.join(buf))
    return segs


def classify(t):
    """Return (is_safe, plain_label) for one rm target."""
    base = t.rsplit('/', 1)[-1]
    low = t.lower()
    if t.startswith(('/tmp/', '/private/tmp/', '/var/folders/')):
        return True, "temp file"
    if base in ('venv', 'virtualenv') or base.startswith('.venv'):
        return True, "Python virtualenv (regenerable)"
    artifacts = {'node_modules', '.next', 'dist', 'build', '__pycache__',
                 '.cache', '.turbo', 'coverage', '.pytest_cache',
                 '.mypy_cache', '.ruff_cache'}
    if base in artifacts or base.endswith('.egg-info'):
        return True, "build/cache artifact (regenerable)"
    db_suffixes = ('.db', '.db-shm', '.db-wal', '.db-journal',
                   '.sqlite', '.sqlite3', '.sqlite-shm', '.sqlite-wal')
    if low.endswith(db_suffixes):
        if 'test' in low or base.startswith('.'):
            return True, "local test database (regenerable)"
        return False, "a database file — NOT clearly a test DB"
    if low.endswith(('.log', '.tmp', '.pyc', '.pyo', '.swp')):
        return True, "log / temp file"
    return False, "not a recognized build/temp artifact"


def is_recursive(args):
    for a in args:
        if a == '--recursive':
            return True
        if a.startswith('-') and not a.startswith('--') and 'r' in a:
            return True
    return False


def rm_targets(args):
    """Yield real delete targets, skipping flags and redirections."""
    skip_next = False
    for a in args:
        if skip_next:
            skip_next = False
            continue
        if a.startswith('-'):
            continue
        if '>' in a or '<' in a:               # a redirection, not a target
            if a.endswith(('>', '<')):         # bare operator -> file is next token
                skip_next = True
            continue
        yield a


def main():
    cmd = sys.stdin.read()
    lines, any_unsafe = [], False
    for seg in segments(cmd):
        try:
            toks = shlex.split(seg, posix=True)
        except ValueError:
            toks = seg.split()
        if not toks:
            continue
        # skip leading VAR=val environment assignments
        j = 0
        while (j < len(toks) and '=' in toks[j] and not toks[j].startswith('-')
               and '/' not in toks[j].split('=', 1)[0]):
            j += 1
        if j >= len(toks) or toks[j] != 'rm':
            continue
        args = toks[j + 1:]
        if not is_recursive(args):
            continue
        for t in rm_targets(args):
            safe, label = classify(t)
            any_unsafe = any_unsafe or not safe
            lines.append(f"  {'✓' if safe else '⚠'} {t} — {label}")
    if any_unsafe:
        sys.stdout.write(
            "This 'rm' permanently deletes the items below. "
            "✓ = routine/regenerable, ⚠ = please check before approving:\n"
            + "\n".join(lines))


if __name__ == '__main__':
    main()
