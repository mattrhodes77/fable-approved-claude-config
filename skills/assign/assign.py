#!/usr/bin/env python3
"""
assign.py — roster-driven workstream cache + ticket matcher/filer over Linear.

Local-only tooling. No third-party deps (urllib + sqlite3 + json only).
Linear API key is read from $LINEAR_API_KEY, or a JSON file named by $LINEAR_KEY_FILE
(holding .env.LINEAR_API_KEY).

Commands
  sync [--seed]              Refresh the local cache: pull each active roster member's
                             Linear issues into workstreams.db, and cache Dev-team
                             lookups (states/projects/labels). --seed also prints
                             Dev-team members who are NOT yet in roster.json.
  roster                     Print the roster (people + their configured streams).
  profile <who>             Show a person's derived workstream: top projects/labels,
                             repos, OPEN queue, and recently shipped. <who> = email,
                             display name, or substring.
  targets <who> [who...]     Fuzzy-resolve one or more names (e.g. `alice bob`) to roster
                             members and emit, per person, the repos+active branches,
                             projects/labels, and the OPEN queue to dedup against. This is
                             the entry point for `/assign alice bob` — it hands the
                             pipeline everything it needs to mine each person's repos.
  match "<text>" [opts]      Rank roster members by fit for a candidate ticket.
        --repo PATH_OR_NAME  candidate's repo (basename match ok)
        --project NAME       candidate's project
        --labels a,b,c       candidate's labels
  new   [opts]               File a new Linear issue (issueCreate) and print its URL.
        Exactly one routing flag is required:
        --assignee WHO       HUMAN bucket: email/name/substring; defaults state Todo.
        --bulldozer          LLM bucket: file UNASSIGNED so the /bulldozer heartbeat
                             drains it; defaults state Backlog (where bulldozer scans).
        --title  "..."       (required)
        --project NAME       resolved against the cached Dev-team projects
        --priority N         0 none /1 urgent /2 high /3 med /4 low   (default 3)
        --labels a,b         label names (resolved against cache)
        --state NAME         override the state (default Todo for human, Backlog for bulldozer)
        --team NAME          team name (default "Dev")
        --body  "..."        markdown description, OR
        --body-file PATH     read the markdown description from a file
        --dry-run            print the resolved payload, do not create

TRIAGE (your rule): run candidates through bulldozer's eligibility FIRST. A candidate
is bulldozer-eligible (--bulldozer) when the fix is small + mechanical + well-specified +
unblocked + on the active branch (single-function / guard / wire-an-existing-handler /
mirror-a-sibling). Anything needing design, a product/build-vs-remove decision, judgment,
multi-file/cross-cutting work, a migration, or domain context goes to a HUMAN (--assignee),
matched by ability/workstream/repo via `match`. Don't burn a human on an LLM-doable ticket.

The roster (roster.json next to this file) is the swappable, up-to-N-people config.
A person's themes/repos there are *augmentation*; the live profile is always derived
from the freshly-synced tickets, so the cache reflects their latest real workstream.
"""

import json
import os
import sqlite3
import sys
import urllib.request
import urllib.error
from collections import Counter
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "workstreams.db")
ROSTER_PATH = os.path.join(HERE, "roster.json")
GRAPHQL_URL = "https://api.linear.app/graphql"

OPEN_STATE_TYPES = {"backlog", "unstarted", "started"}  # vs completed/canceled
# "Deployed" is a started-type state in this workspace but means shipped; treat as shipped.
SHIPPED_STATE_NAMES = {"deployed", "done", "merged", "released"}


# ---------- credentials ----------

def linear_key():
    k = os.environ.get("LINEAR_API_KEY")
    if k:
        return k
    kf = os.environ.get("LINEAR_KEY_FILE")
    if kf:
        try:
            with open(os.path.expanduser(kf)) as f:
                return json.load(f).get("env", {}).get("LINEAR_API_KEY")
        except Exception:
            return None
    return None


def gql(query, variables=None):
    key = linear_key()
    if not key:
        die("No LINEAR_API_KEY (checked $LINEAR_API_KEY and $LINEAR_KEY_FILE).")
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        GRAPHQL_URL,
        data=body,
        headers={"Authorization": key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            payload = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        die(f"Linear HTTP {e.code}: {e.read().decode()[:500]}")
    except urllib.error.URLError as e:
        die(f"Linear network error: {e}")
    if payload.get("errors"):
        die("Linear GraphQL errors: " + json.dumps(payload["errors"])[:800])
    return payload["data"]


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


# ---------- db ----------

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS employees(
            email TEXT PRIMARY KEY, linear_user_id TEXT, name TEXT, active INTEGER,
            repos_json TEXT, projects_json TEXT, labels_json TEXT, themes TEXT, synced_at TEXT);
        CREATE TABLE IF NOT EXISTS tickets(
            id TEXT PRIMARY KEY, identifier TEXT, title TEXT, state TEXT, state_type TEXT,
            priority INTEGER, project TEXT, labels_json TEXT, assignee_email TEXT,
            updated_at TEXT, created_at TEXT, url TEXT);
        CREATE TABLE IF NOT EXISTS lookups(kind TEXT, name TEXT, id TEXT, PRIMARY KEY(kind,name));
        CREATE INDEX IF NOT EXISTS ix_tickets_assignee ON tickets(assignee_email);
        """
    )
    return conn


def load_roster():
    if not os.path.exists(ROSTER_PATH):
        die(f"No roster at {ROSTER_PATH}")
    with open(ROSTER_PATH) as f:
        return json.load(f)


# ---------- sync ----------

ISSUES_QUERY = """
query Issues($uid: ID!, $after: String) {
  issues(filter: {assignee: {id: {eq: $uid}}}, first: 100, after: $after,
         orderBy: updatedAt) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id identifier title priority url updatedAt createdAt
      state { name type }
      project { name }
      assignee { email }
      labels { nodes { name } }
    }
  }
}
"""


def fetch_issues(uid):
    out, after = [], None
    while True:
        data = gql(ISSUES_QUERY, {"uid": uid, "after": after})
        blk = data["issues"]
        out.extend(blk["nodes"])
        if not blk["pageInfo"]["hasNextPage"]:
            break
        after = blk["pageInfo"]["endCursor"]
    return out


def cmd_sync(args):
    roster = load_roster()
    conn = db()
    now = datetime.now(timezone.utc).isoformat()
    members = [m for m in roster["members"] if m.get("active", True)]
    for m in members:
        uid = m.get("linear_user_id")
        if not uid:
            print(f"  ! {m.get('email')} has no linear_user_id — skipping (run sync --seed)")
            continue
        nodes = fetch_issues(uid)
        conn.execute("DELETE FROM tickets WHERE assignee_email=?", (m["email"],))
        for n in nodes:
            conn.execute(
                "INSERT OR REPLACE INTO tickets VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    n["id"], n["identifier"], n["title"],
                    (n["state"] or {}).get("name"), (n["state"] or {}).get("type"),
                    n.get("priority"), (n.get("project") or {}).get("name"),
                    json.dumps([l["name"] for l in (n["labels"]["nodes"])]),
                    (n.get("assignee") or {}).get("email") or m["email"],
                    n.get("updatedAt"), n.get("createdAt"), n.get("url"),
                ),
            )
        conn.execute(
            "INSERT OR REPLACE INTO employees VALUES (?,?,?,?,?,?,?,?,?)",
            (
                m["email"], uid, m.get("name", m["email"]), 1,
                json.dumps(m.get("repos", [])), json.dumps(m.get("projects", [])),
                json.dumps(m.get("labels", [])), m.get("themes", ""), now,
            ),
        )
        conn.commit()
        print(f"  synced {m['email']}: {len(nodes)} issues")
    sync_lookups(conn, roster.get("default_team", "Dev"))
    if getattr(args, "seed", False):
        seed_suggest(conn, roster)
    conn.close()
    print("done.")


LOOKUPS_QUERY = """
query Lookups($team: String!) {
  teams(filter: {name: {eq: $team}}) { nodes {
    id name
    states { nodes { id name type } }
    projects { nodes { id name } }
  } }
}
"""

# Labels are fetched separately: team.labels is a single unpaginated connection that
# silently caps at 50 AND mixes workspace-level (org-wide) labels with team labels, so
# team-specific labels like "Bug Fix"/"New Feature" get crowded off the first page.
# This dedicated query pulls both scopes (team=$team OR workspace/null) explicitly, and
# is a flat top-level connection so it stays under Linear's query-complexity cap.
LABELS_QUERY = """
query Labels($team: String!, $after: String) {
  issueLabels(first: 250, after: $after,
    filter: { or: [ {team: {name: {eq: $team}}}, {team: {null: true}} ] }) {
    nodes { id name team { name } }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def sync_lookups(conn, team_name):
    data = gql(LOOKUPS_QUERY, {"team": team_name})
    nodes = data["teams"]["nodes"]
    if not nodes:
        print(f"  ! team '{team_name}' not found; lookups not cached")
        return
    t = nodes[0]
    conn.execute("DELETE FROM lookups")
    conn.execute("INSERT OR REPLACE INTO lookups VALUES ('team',?,?)", (t["name"], t["id"]))
    for s in t["states"]["nodes"]:
        conn.execute("INSERT OR REPLACE INTO lookups VALUES ('state',?,?)", (s["name"], s["id"]))
    for p in t["projects"]["nodes"]:
        conn.execute("INSERT OR REPLACE INTO lookups VALUES ('project',?,?)", (p["name"], p["id"]))

    # Labels: paginate the dedicated query, insert workspace-scoped first then team-scoped
    # so that on a name collision (e.g. a "Bug Fix" exists in multiple teams) the label
    # belonging to THIS team wins.
    workspace, teamlabels = [], []
    after = None
    while True:
        ld = gql(LABELS_QUERY, {"team": team_name, "after": after})
        page = ld["issueLabels"]
        for lb in page["nodes"]:
            (teamlabels if (lb.get("team") and lb["team"]["name"] == team_name)
             else workspace).append(lb)
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]
    for lb in workspace + teamlabels:
        conn.execute("INSERT OR REPLACE INTO lookups VALUES ('label',?,?)", (lb["name"], lb["id"]))

    conn.commit()
    print(f"  cached lookups for team '{t['name']}': "
          f"{len(t['states']['nodes'])} states, {len(t['projects']['nodes'])} projects, "
          f"{len(workspace) + len(teamlabels)} labels")


ALLMEMBERS_QUERY = """
query Members($team: String!) {
  teams(filter: {name: {eq: $team}}) { nodes { members { nodes {
    id name email active guest } } } }
}
"""


def seed_suggest(conn, roster):
    team = roster.get("default_team", "Dev")
    data = gql(ALLMEMBERS_QUERY, {"team": team})
    nodes = data["teams"]["nodes"]
    if not nodes:
        return
    have = {m["email"] for m in roster["members"]}
    missing = [u for u in nodes[0]["members"]["nodes"]
               if u.get("active") and not u.get("guest") and u["email"] not in have]
    if not missing:
        print("\nseed: roster already covers all active Dev-team members.")
        return
    print("\nseed: Dev-team members NOT in roster.json (add the ones you staff):")
    for u in missing:
        print(json.dumps({"email": u["email"], "linear_user_id": u["id"],
                          "name": u.get("name") or u["email"], "active": True,
                          "repos": [], "projects": [], "labels": [], "themes": ""}))


# ---------- profile / match helpers ----------

def resolve_person(conn, who):
    who_l = who.lower()
    rows = conn.execute("SELECT * FROM employees").fetchall()
    for r in rows:  # exact email/name first
        if who_l in (r["email"].lower(), (r["name"] or "").lower()):
            return r
    for r in rows:  # then substring
        if who_l in r["email"].lower() or who_l in (r["name"] or "").lower():
            return r
    die(f"no roster member matches '{who}' (have you run sync?)")


def is_shipped(state, state_type):
    return state_type == "completed" or (state or "").lower() in SHIPPED_STATE_NAMES


def is_open(state, state_type):
    if is_shipped(state, state_type):
        return False
    return state_type in OPEN_STATE_TYPES


PRIO = {0: "—", 1: "Urgent", 2: "High", 3: "Med", 4: "Low"}


def cmd_profile(args):
    conn = db()
    r = resolve_person(conn, args.who)
    tickets = conn.execute(
        "SELECT * FROM tickets WHERE assignee_email=? ORDER BY updated_at DESC", (r["email"],)
    ).fetchall()
    projects = Counter(t["project"] for t in tickets if t["project"])
    labels = Counter(l for t in tickets for l in json.loads(t["labels_json"]))
    open_q = [t for t in tickets if is_open(t["state"], t["state_type"])]
    shipped = [t for t in tickets if is_shipped(t["state"], t["state_type"])]
    print(f"\n# {r['name']}  <{r['email']}>")
    if r["themes"]:
        print(f"themes: {r['themes']}")
    repos = json.loads(r["repos_json"] or "[]")
    if repos:
        print("repos: " + ", ".join(
            f"{x.get('path','?')}@{x.get('active_branch','?')}" if isinstance(x, dict) else str(x)
            for x in repos))
    print(f"tickets cached: {len(tickets)}  (open {len(open_q)} / shipped {len(shipped)})")
    print("top projects: " + ", ".join(f"{k}×{v}" for k, v in projects.most_common(6)))
    print("top labels:   " + ", ".join(f"{k}×{v}" for k, v in labels.most_common(8)))
    print("\nOPEN QUEUE:")
    for t in sorted(open_q, key=lambda t: (t["priority"] or 9, t["updated_at"]), reverse=False):
        print(f"  [{t['state']:<11}] {t['identifier']:<9} P:{PRIO.get(t['priority'],'?'):<6} "
              f"{(t['project'] or '-'):<16} {t['title']}")
    print(f"\nRECENTLY SHIPPED (last {min(8,len(shipped))}):")
    for t in shipped[:8]:
        print(f"  [{t['state']:<11}] {t['identifier']:<9} {t['title']}")
    conn.close()


def keywords(text):
    return {w for w in "".join(c.lower() if c.isalnum() else " " for c in (text or "")).split()
            if len(w) > 3}


def cmd_match(args):
    conn = db()
    cand_repo = (args.repo or "").lower()
    cand_repo_base = os.path.basename(cand_repo.rstrip("/")) if cand_repo else ""
    cand_proj = (args.project or "").lower()
    cand_labels = {x.strip().lower() for x in (args.labels or "").split(",") if x.strip()}
    cand_kw = keywords(args.text)
    rows = conn.execute("SELECT * FROM employees WHERE active=1").fetchall()
    scored = []
    for r in rows:
        tickets = conn.execute(
            "SELECT * FROM tickets WHERE assignee_email=?", (r["email"],)).fetchall()
        emp_projects = {(t["project"] or "").lower() for t in tickets}
        emp_labels = Counter(l.lower() for t in tickets for l in json.loads(t["labels_json"]))
        emp_repos = set()
        for x in json.loads(r["repos_json"] or "[]"):
            p = x.get("path", "") if isinstance(x, dict) else str(x)
            emp_repos.add(os.path.basename(p.rstrip("/")).lower())
        emp_kw = keywords(r["themes"]) | {w for t in tickets for w in keywords(t["title"])}
        score, why = 0.0, []
        if cand_repo_base and cand_repo_base in emp_repos:
            score += 3; why.append(f"repo:{cand_repo_base}")
        if cand_proj and (cand_proj in emp_projects
                          or cand_proj in {p.lower() for p in json.loads(r["projects_json"] or "[]")}):
            score += 2; why.append(f"project:{args.project}")
        lab_hit = cand_labels & set(emp_labels)
        if lab_hit:
            score += len(lab_hit); why.append("labels:" + ",".join(sorted(lab_hit)))
        kw_hit = cand_kw & emp_kw
        if kw_hit:
            score += 0.5 * min(len(kw_hit), 6)
            why.append("kw:" + ",".join(sorted(list(kw_hit))[:6]))
        scored.append((score, r, why))
    scored.sort(key=lambda x: x[0], reverse=True)
    print(f'\nmatch for: "{args.text}"'
          + (f"  [repo={args.repo}]" if args.repo else "")
          + (f"  [project={args.project}]" if args.project else "")
          + (f"  [labels={args.labels}]" if args.labels else ""))
    for score, r, why in scored:
        if score <= 0:
            continue
        print(f"  {score:5.1f}  {r['name']:<22} {('; '.join(why)) or '(themes only)'}")
    if not any(s > 0 for s, _, _ in scored):
        print("  (no positive matches — check repo/project/label spelling or sync first)")
    conn.close()


# ---------- backlog (claim existing in-lane tickets before minting new) ----------

# Unassigned, still-open (Backlog/Todo-type) team tickets — the pool to CLAIM from
# before discovery mints brand-new ones. Assignee-null keeps it to genuinely
# up-for-grabs work (tickets you own yourself are left alone unless you say otherwise).
BACKLOG_QUERY = """
query Backlog($team: String!, $after: String) {
  issues(
    filter: { team: {name: {eq: $team}}, assignee: {null: true},
              state: {type: {in: ["backlog", "unstarted"]}} },
    first: 100, after: $after, orderBy: updatedAt) {
    pageInfo { hasNextPage endCursor }
    nodes {
      identifier title priority url
      state { name type } project { name } labels { nodes { name } }
    }
  }
}
"""


def fetch_unassigned_backlog(team):
    out, after, pages = [], None, 0
    while pages < 8:  # backstop: 800 tickets is far more than any real lane
        data = gql(BACKLOG_QUERY, {"team": team, "after": after})
        blk = data["issues"]
        out.extend(blk["nodes"])
        pages += 1
        if not blk["pageInfo"]["hasNextPage"]:
            break
        after = blk["pageInfo"]["endCursor"]
    return out


# Cross-cutting labels that sit on nearly every ticket — they signal work TYPE, not
# lane, so they must not inflate lane-fit (a platform-wide label alone matched almost
# every ticket in the pool). Tune this set to your workspace's ubiquitous labels.
CROSS_CUTTING_LABELS = {"platform", "bug fix", "new feature", "polish",
                        "tech debt", "infra", "chore"}


def lane_signals(conn, r):
    """A person's current-workstream fingerprint. Projects carry a WEIGHT (how central
    the project is to them: their #1 project = 1.0), so 'lane' is dominated by where they
    actually work, not any project they've ever touched once."""
    tickets = conn.execute(
        "SELECT * FROM tickets WHERE assignee_email=?", (r["email"],)).fetchall()
    proj_counts = Counter((t["project"] or "").lower() for t in tickets if t["project"])
    maxc = max(proj_counts.values()) if proj_counts else 1
    proj_weight = {p: c / maxc for p, c in proj_counts.items()}
    for p in json.loads(r["projects_json"] or "[]"):  # roster-declared lane = full weight
        proj_weight[p.lower()] = max(proj_weight.get(p.lower(), 0.0), 1.0)
    labels = {l.lower() for t in tickets for l in json.loads(t["labels_json"])}
    labels |= {l.lower() for l in json.loads(r["labels_json"] or "[]")}
    labels -= CROSS_CUTTING_LABELS
    kw = keywords(r["themes"]) | {w for t in tickets for w in keywords(t["title"])}
    return {"proj_weight": proj_weight, "labels": labels, "kw": kw}


def score_against_lane(sig, project, labels, title):
    """Lane fit: project centrality dominates (0–3), specific product labels add a little
    (≤2), title-keyword overlap is a weak tiebreaker (≤1). Cross-cutting labels excluded."""
    score, why = 0.0, []
    pw = sig["proj_weight"].get((project or "").lower(), 0.0)
    if pw > 0:
        score += 3 * pw
        why.append(f"project:{project}({pw:.0%})")
    lab_hit = ({l.lower() for l in labels} - CROSS_CUTTING_LABELS) & sig["labels"]
    if lab_hit:
        score += min(len(lab_hit), 2)
        why.append("labels:" + ",".join(sorted(lab_hit)))
    kw_hit = keywords(title) & sig["kw"]
    if kw_hit:
        score += 0.25 * min(len(kw_hit), 4)
        why.append("kw:" + ",".join(sorted(list(kw_hit))[:4]))
    return score, why


def cmd_backlog(args):
    """BACKLOG-FIRST: rank the existing unassigned in-lane backlog per person, so we
    claim already-filed tickets before discovery mints new ones. A person with an empty
    list here is the signal to fall through to the discovery sweep for that person."""
    conn = db()
    team = args.team
    people, seen = [], set()
    for who in args.who:
        r = resolve_person(conn, who)
        if r["email"] in seen:
            continue
        seen.add(r["email"]); people.append((who, r))
    pool = fetch_unassigned_backlog(team)
    min_score, top_n = float(args.min_score), int(args.top)
    print(f"\nUnassigned OPEN backlog in team '{team}': {len(pool)} tickets "
          f"(Backlog/Todo, no assignee).\nCLAIM in-lane ones below before running discovery.\n")
    for who, r in people:
        sig = lane_signals(conn, r)
        scored = []
        for t in pool:
            s, why = score_against_lane(
                sig, (t.get("project") or {}).get("name"),
                [l["name"] for l in t["labels"]["nodes"]], t["title"])
            if s >= min_score:
                scored.append((s, t, why))
        scored.sort(key=lambda x: (x[0], -(x[1].get("priority") or 9)), reverse=True)
        print(f"=== {r['name']}  <{r['email']}>  (matched '{who}') ===")
        if not scored:
            print(f"  (no in-lane unassigned backlog scoring ≥{min_score:g} — "
                  f"fall through to DISCOVERY for this person)\n")
            continue
        print(f"  {len(scored)} in-lane candidate(s); top {min(top_n, len(scored))}:")
        for s, t, why in scored[:top_n]:
            proj = (t.get("project") or {}).get("name") or "-"
            print(f"  {s:5.1f}  {t['identifier']:<9} P:{PRIO.get(t.get('priority'), '?'):<6} "
                  f"{proj:<16} {t['title']}")
            print(f"         {t['url']}   [{'; '.join(why)}]")
        print()
    conn.close()


# ---------- claim (assign an EXISTING backlog ticket to a human) ----------

ISSUE_UUID_QUERY = "query($id: String!){ issue(id: $id){ id identifier assignee { name } } }"
UPDATE_MUT = """
mutation Claim($id: String!, $input: IssueUpdateInput!) {
  issueUpdate(id: $id, input: $input) {
    success issue { identifier url state { name } assignee { name } }
  }
}
"""


def cmd_claim(args):
    """Assign an already-filed backlog ticket to a human (the backlog-first counterpart of
    `new`: no ticket is created, an existing one is claimed and moved Backlog→Todo)."""
    conn = db()
    person = resolve_person(conn, args.assignee)
    look = gql(ISSUE_UUID_QUERY, {"id": args.id})
    iss0 = look.get("issue")
    if not iss0:
        die(f"issue '{args.id}' not found")
    if iss0.get("assignee") and not args.force:
        die(f"{iss0['identifier']} is already assigned to {iss0['assignee']['name']} "
            f"— pass --force to reassign")
    inp = {"assigneeId": person["linear_user_id"]}
    state_name = args.state or "Todo"
    if state_name.lower() != "keep":
        sid = lookup(conn, "state", state_name)
        if not sid:
            die(f"state '{state_name}' not found (run sync)")
        inp["stateId"] = sid
    if args.priority is not None:
        inp["priority"] = int(args.priority)
    if args.dry_run:
        print(f"DRY RUN issueUpdate {iss0['identifier']} -> assignee {person['name']}, "
              f"state {state_name}"
              + (f", priority {args.priority}" if args.priority is not None else ""))
        conn.close()
        return
    data = gql(UPDATE_MUT, {"id": iss0["id"], "input": inp})
    res = data["issueUpdate"]
    if not res["success"]:
        die("issueUpdate returned success=false")
    iss = res["issue"]
    print(f"CLAIMED {iss['identifier']} -> {iss['assignee']['name']} "
          f"[{iss['state']['name']}]  {iss['url']}")
    conn.close()


# ---------- new (file a ticket) ----------

CREATE_MUT = """
mutation Create($input: IssueCreateInput!) {
  issueCreate(input: $input) { success issue { identifier url } }
}
"""


def lookup(conn, kind, name):
    if not name:
        return None
    row = conn.execute("SELECT id FROM lookups WHERE kind=? AND lower(name)=lower(?)",
                       (kind, name)).fetchone()
    return row["id"] if row else None


def cmd_new(args):
    conn = db()
    if not args.title:
        die("new requires --title")
    if bool(args.assignee) == bool(args.bulldozer):
        die("new requires exactly one of --assignee (human) or --bulldozer (LLM queue)")
    person = resolve_person(conn, args.assignee) if args.assignee else None
    state_name = args.state or ("Backlog" if args.bulldozer else "Todo")
    team_id = lookup(conn, "team", args.team)
    if not team_id:
        die(f"team '{args.team}' not in cache — run sync first")
    state_id = lookup(conn, "state", state_name)
    if not state_id:
        die(f"state '{state_name}' not found for team {args.team} (run sync)")
    body = args.body or ""
    if args.body_file:
        with open(os.path.expanduser(args.body_file)) as f:
            body = f.read()
    label_ids = []
    for nm in [x.strip() for x in (args.labels or "").split(",") if x.strip()]:
        lid = lookup(conn, "label", nm)
        if not lid:
            die(f"label '{nm}' not found in cache (run sync; check spelling)")
        label_ids.append(lid)
    project_id = lookup(conn, "project", args.project) if args.project else None
    if args.project and not project_id:
        die(f"project '{args.project}' not found in cache (run sync; check spelling)")
    inp = {
        "teamId": team_id, "title": args.title, "description": body,
        "stateId": state_id, "priority": int(args.priority),
    }
    if person:
        inp["assigneeId"] = person["linear_user_id"]
    if project_id:
        inp["projectId"] = project_id
    if label_ids:
        inp["labelIds"] = label_ids
    bucket = f"assignee {person['name']}" if person else "BULLDOZER queue (unassigned)"
    if args.dry_run:
        red = dict(inp); red["description"] = f"<{len(body)} chars>"
        print("DRY RUN issueCreate input:\n" + json.dumps(red, indent=2))
        print(f"-> {bucket}  [state {state_name}]")
        conn.close()
        return
    data = gql(CREATE_MUT, {"input": inp})
    res = data["issueCreate"]
    if not res["success"]:
        die("issueCreate returned success=false")
    iss = res["issue"]
    print(f"FILED {iss['identifier']}  ->  {iss['url']}  ({bucket}, {state_name})")
    conn.close()


def cmd_targets(args):
    """Fuzzy-resolve a list of names and emit the pipeline inputs for each."""
    conn = db()
    seen = set()
    for who in args.who:
        r = resolve_person(conn, who)
        if r["email"] in seen:
            continue
        seen.add(r["email"])
        tickets = conn.execute(
            "SELECT * FROM tickets WHERE assignee_email=?", (r["email"],)).fetchall()
        open_q = [t for t in tickets if is_open(t["state"], t["state_type"])]
        shipped = [t for t in tickets if is_shipped(t["state"], t["state_type"])]
        repos = json.loads(r["repos_json"] or "[]")
        projects = Counter(t["project"] for t in tickets if t["project"])
        labels = Counter(l for t in tickets for l in json.loads(t["labels_json"]))
        print(f"\n=== {r['name']}  <{r['email']}>  (matched '{who}') ===")
        if r["themes"]:
            print(f"themes: {r['themes']}")
        print("repos to mine:")
        for x in repos:
            if isinstance(x, dict):
                print(f"  - {x.get('path','?')}   verify-branch: {x.get('active_branch','?')}")
            else:
                print(f"  - {x}")
        if not repos:
            print("  (none configured — add repos to roster.json for this member)")
        print("top projects: " + ", ".join(f"{k}×{v}" for k, v in projects.most_common(5)))
        print("top labels:   " + ", ".join(f"{k}×{v}" for k, v in labels.most_common(6)))
        print(f"DEDUP — do NOT re-file these {len(open_q)} OPEN + recent-shipped titles:")
        for t in sorted(open_q, key=lambda t: t["state"]):
            print(f"  OPEN    {t['identifier']:<9} [{t['state']}] {t['title']}")
        for t in shipped[:12]:
            print(f"  SHIPPED {t['identifier']:<9} {t['title']}")
    conn.close()


def cmd_roster(args):
    roster = load_roster()
    print(f"team: {roster.get('default_team','Dev')}   members: {len(roster['members'])}")
    for m in roster["members"]:
        flag = "" if m.get("active", True) else " (inactive)"
        repos = ", ".join(
            (x.get("path", "?") if isinstance(x, dict) else str(x)) for x in m.get("repos", []))
        print(f"  {m.get('name', m['email']):<20} {m['email']:<28}{flag}")
        if m.get("themes"):
            print(f"      themes: {m['themes']}")
        if repos:
            print(f"      repos:  {repos}")


# ---------- argparse ----------

def main():
    import argparse
    p = argparse.ArgumentParser(prog="assign.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sync"); s.add_argument("--seed", action="store_true")
    s.set_defaults(fn=cmd_sync)

    s = sub.add_parser("roster"); s.set_defaults(fn=cmd_roster)

    s = sub.add_parser("profile"); s.add_argument("who"); s.set_defaults(fn=cmd_profile)

    s = sub.add_parser("targets"); s.add_argument("who", nargs="+"); s.set_defaults(fn=cmd_targets)

    s = sub.add_parser("backlog")
    s.add_argument("who", nargs="+")
    s.add_argument("--team", default="Dev")
    s.add_argument("--min-score", dest="min_score", default="2.5",
                   help="min lane-fit score to surface (default 2.5 ~= a strong primary-project match)")
    s.add_argument("--top", default="15", help="max candidates to show per person")
    s.set_defaults(fn=cmd_backlog)

    s = sub.add_parser("claim")
    s.add_argument("id", help="issue identifier to assign, e.g. ENG-1896")
    s.add_argument("--assignee", required=True)
    s.add_argument("--state", default="Todo", help="target state (default Todo; 'keep' to leave as-is)")
    s.add_argument("--priority", default=None)
    s.add_argument("--force", action="store_true", help="reassign even if already assigned")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_claim)

    s = sub.add_parser("match")
    s.add_argument("text")
    s.add_argument("--repo"); s.add_argument("--project"); s.add_argument("--labels")
    s.set_defaults(fn=cmd_match)

    s = sub.add_parser("new")
    s.add_argument("--assignee"); s.add_argument("--bulldozer", action="store_true")
    s.add_argument("--title", required=True)
    s.add_argument("--project"); s.add_argument("--priority", default="3")
    s.add_argument("--labels"); s.add_argument("--state", default=None)
    s.add_argument("--team", default="Dev")
    s.add_argument("--body"); s.add_argument("--body-file", dest="body_file")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_new)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
