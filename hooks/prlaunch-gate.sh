#!/bin/bash
# prlaunch-gate.sh -- the PRlaunch per-gate evidence ledger.
#
# The ledger lives at:
#   ~/.claude/prlaunch-ok/<repo>--<branch-slug>.json   (branch '/' -> '-')
# and records, per gate, the HEAD sha it ran against + a timestamp, so the
# pr-gate hook can mechanically prove all four PRlaunch quality gates ran on the
# EXACT bytes being shipped. Any commit after a gate ran changes HEAD and stales
# that gate's entry -- the re-gate rule, enforced as code instead of prose.
#
# Run from inside the target repo (it resolves repo/branch/HEAD itself), or pass
# --repo-dir <dir> anywhere on the command line.
#
# Subcommands:
#   record scenarios <path>
#       Register the outcome-eval scenario file BEFORE the eval runs.
#       Stores {path, sha256 of the file, ts}. Precondition for outcome_eval.
#   record deep_review|cr_cli|outcome_eval|tests [--skipped R] [--na R] [--cmd C]
#       Stamp {sha: current HEAD, ts} for that gate. Rules:
#         * outcome_eval is REFUSED unless scenarios were registered first AND
#           the registered file still hashes to the sha256 stored at
#           registration -- editing or deleting the scenarios after the fact is
#           drift and is refused. The verified hash is stamped onto the entry as
#           scenarios_sha256. UNLESS --na is given (no user-facing surface needs
#           no scenarios).
#         * --skipped is only valid for cr_cli and requires a reason.
#         * --na is only valid for outcome_eval and requires a reason.
#         * --cmd records the verify command (used for tests).
#   check
#       Exit 0 iff all four gate entries exist (skipped/na entries pass on
#       reason presence) AND every recorded gate sha == current HEAD. On failure
#       prints exactly which gate is missing/stale + the prescriptive fix, and
#       exits 1.

set -uo pipefail

GATES="deep_review cr_cli outcome_eval tests"

die() { printf 'prlaunch-gate: %s\n' "$1" >&2; exit 1; }

phase_of() {
  case "$1" in
    deep_review)  printf '1' ;;
    cr_cli)       printf '2' ;;
    outcome_eval) printf '3' ;;
    tests)        printf '4' ;;
    *)            printf '?' ;;
  esac
}

is_gate() {
  case " $GATES " in
    *" $1 "*) return 0 ;;
    *) return 1 ;;
  esac
}

# ---- pull --repo-dir out of the args (it may appear anywhere) --------------
repo_dir=""
rest=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-dir)
      [[ $# -ge 2 ]] || die "--repo-dir requires a directory"
      repo_dir="$2"; shift 2 ;;
    *) rest+=("$1"); shift ;;
  esac
done
if [[ ${#rest[@]} -gt 0 ]]; then set -- "${rest[@]}"; else set --; fi

subcmd="${1:-}"
[[ -n "$subcmd" ]] || die "usage: prlaunch-gate.sh record <gate> [...] | check  (see header)"

# ---- resolve repo / branch / HEAD -----------------------------------------
[[ -n "$repo_dir" ]] || repo_dir="$(pwd)"
repo_dir="${repo_dir/#\~/$HOME}"
toplevel=$(git -C "$repo_dir" rev-parse --show-toplevel 2>/dev/null) \
  || die "not a git repo: $repo_dir (run inside the repo, or pass --repo-dir <dir>)"
repo=$(basename "$toplevel")
branch=$(git -C "$repo_dir" branch --show-current 2>/dev/null)
head=$(git -C "$repo_dir" rev-parse HEAD 2>/dev/null) \
  || die "no commits in $repo_dir"
[[ -n "$branch" ]] || die "detached HEAD in $repo_dir -- checkout a branch first"
slug="${branch//\//-}"
okdir="$HOME/.claude/prlaunch-ok"
ledger="$okdir/${repo}--${slug}.json"
short="${head:0:8}"

ledger_read() { if [[ -f "$ledger" ]]; then cat "$ledger"; else printf '{}'; fi; }

atomic_write() {
  mkdir -p "$okdir"
  local tmp
  tmp=$(mktemp "${ledger}.XXXXXX") || die "mktemp failed in $okdir"
  if printf '%s\n' "$1" >"$tmp" && mv -f "$tmp" "$ledger"; then :; else
    rm -f "$tmp"; die "failed writing ledger $ledger"
  fi
}

file_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# ---------------------------------------------------------------------------
case "$subcmd" in
  record)
    gate="${2:-}"
    [[ -n "$gate" ]] || die "usage: prlaunch-gate.sh record <scenarios|deep_review|cr_cli|outcome_eval|tests> [...]"

    if [[ "$gate" == "scenarios" ]]; then
      path="${3:-}"
      [[ -n "$path" ]] || die "usage: prlaunch-gate.sh record scenarios <path>"
      path="${path/#\~/$HOME}"
      [[ -f "$path" ]] || die "scenarios file not found: $path"
      sha=$(file_sha256 "$path")
      ts=$(now)
      updated=$(ledger_read | jq \
        --arg repo "$repo" --arg branch "$branch" \
        --arg path "$path" --arg sha "$sha" --arg ts "$ts" \
        '.repo=$repo | .branch=$branch | .scenarios={path:$path, sha256:$sha, ts:$ts}') \
        || die "jq failed building the scenarios entry"
      atomic_write "$updated"
      printf 'prlaunch-gate: registered scenarios %s for %s/%s\n' "$path" "$repo" "$branch"
      exit 0
    fi

    is_gate "$gate" \
      || die "unknown gate '$gate' (scenarios|deep_review|cr_cli|outcome_eval|tests)"

    # ---- parse the gate flags ---------------------------------------------
    skipped="" ; na="" ; cmd="" ; scen_sha=""
    have_skipped=0 ; have_na=0
    shift 2
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --skipped)
          [[ $# -ge 2 ]] || die "--skipped requires a reason"
          have_skipped=1; skipped="$2"; shift 2 ;;
        --na)
          [[ $# -ge 2 ]] || die "--na requires a reason"
          have_na=1; na="$2"; shift 2 ;;
        --cmd)
          [[ $# -ge 2 ]] || die "--cmd requires a value"
          cmd="$2"; shift 2 ;;
        *) die "unknown flag '$1' for 'record $gate'" ;;
      esac
    done

    [[ $have_skipped -eq 0 || "$gate" == "cr_cli" ]] \
      || die "--skipped is only valid for cr_cli (got '$gate')"
    [[ $have_skipped -eq 0 || -n "$skipped" ]] \
      || die "--skipped requires a reason"
    [[ $have_na -eq 0 || "$gate" == "outcome_eval" ]] \
      || die "--na is only valid for outcome_eval (got '$gate')"
    [[ $have_na -eq 0 || -n "$na" ]] \
      || die "--na requires a reason"

    # Pre-registering scenarios only means anything if the file is still the one
    # that was registered: the point of the gate is that scenarios were written
    # BEFORE the eval ran. Re-hash here and refuse on drift, otherwise the
    # recorded sha256 is decorative and the eval can be graded against scenarios
    # rewritten to match whatever shipped.
    if [[ "$gate" == "outcome_eval" && $have_na -eq 0 ]]; then
      scen_path=$(ledger_read | jq -r '.scenarios.path // ""')
      [[ -n "$scen_path" ]] \
        || die "outcome_eval refused -- no scenarios registered. Run 'prlaunch-gate.sh record scenarios <path>' BEFORE the eval, or 'record outcome_eval --na \"<reason>\"' if there is no user-facing surface."
      [[ -f "$scen_path" ]] \
        || die "outcome_eval refused -- the registered scenarios file no longer exists: $scen_path. Re-register the scenarios you actually evaluated ('record scenarios <path>'), then re-run the eval."
      want_sha=$(ledger_read | jq -r '.scenarios.sha256 // ""')
      [[ -n "$want_sha" ]] \
        || die "outcome_eval refused -- the scenarios entry carries no sha256 (ledger written by an older prlaunch-gate). Re-register with 'record scenarios $scen_path'."
      scen_sha=$(file_sha256 "$scen_path")
      [[ "$scen_sha" == "$want_sha" ]] \
        || die "outcome_eval refused -- scenarios DRIFTED since they were registered ($scen_path: registered ${want_sha:0:12}, now ${scen_sha:0:12}). Scenarios must be written BEFORE the eval runs. Either re-run the eval against the scenarios as registered, or 'record scenarios $scen_path' again and re-run the eval on the new ones."
    fi

    ts=$(now)
    updated=$(ledger_read | jq \
      --arg repo "$repo" --arg branch "$branch" --arg gate "$gate" \
      --arg sha "$head" --arg ts "$ts" \
      --arg skipped "$skipped" --arg na "$na" --arg cmd "$cmd" \
      --arg hs "$have_skipped" --arg hn "$have_na" --arg scen "$scen_sha" \
      '
      .repo = $repo
      | .branch = $branch
      | .gates = (.gates // {})
      | .gates[$gate] = (
          {sha: $sha, ts: $ts}
          + (if $hs == "1" then {skipped: $skipped} else {} end)
          + (if $hn == "1" then {na: $na} else {} end)
          + (if $cmd != "" then {cmd: $cmd} else {} end)
          + (if $scen != "" then {scenarios_sha256: $scen} else {} end)
        )
      ') || die "jq failed building the '$gate' entry"
    atomic_write "$updated"

    note=""
    [[ $have_skipped -eq 1 ]] && note=" (skipped: $skipped)"
    [[ $have_na -eq 1 ]] && note=" (n/a: $na)"
    printf 'prlaunch-gate: recorded %s @ %s for %s/%s%s\n' "$gate" "$short" "$repo" "$branch" "$note"
    exit 0
    ;;

  check)
    data=$(ledger_read)
    jq -e . >/dev/null 2>&1 <<<"$data" \
      || die "ledger missing or corrupt for $repo/$branch -- re-run /PRlaunch to record the gates."
    problems=0
    for gate in $GATES; do
      phase=$(phase_of "$gate")
      info=$(jq -r --arg g "$gate" '
        (.gates[$g]) as $e
        | if $e == null then "missing"
          elif (($e|has("skipped")) and (($e.skipped // "") == "")) then "noreason:skipped"
          elif (($e|has("na")) and (($e.na // "") == "")) then "noreason:na"
          else "sha:" + ($e.sha // "")
          end' <<<"$data")
      case "$info" in
        missing)
          printf 'MISSING gate: %s -- re-run Phase %s on HEAD %s, then prlaunch-gate.sh record %s\n' \
            "$gate" "$phase" "$short" "$gate" >&2
          problems=1
          ;;
        noreason:skipped)
          printf 'INVALID gate: %s -- marked skipped with no reason. re-run Phase %s on HEAD %s, then prlaunch-gate.sh record %s --skipped "<reason>"\n' \
            "$gate" "$phase" "$short" "$gate" >&2
          problems=1
          ;;
        noreason:na)
          printf 'INVALID gate: %s -- marked n/a with no reason. re-run Phase %s on HEAD %s, then prlaunch-gate.sh record %s --na "<reason>"\n' \
            "$gate" "$phase" "$short" "$gate" >&2
          problems=1
          ;;
        sha:*)
          gsha="${info#sha:}"
          if [[ "$gsha" != "$head" ]]; then
            printf 'STALE gate: %s (recorded %s, HEAD %s) -- re-run Phase %s on HEAD %s, then prlaunch-gate.sh record %s\n' \
              "$gate" "${gsha:0:8}" "$short" "$phase" "$short" "$gate" >&2
            problems=1
          fi
          ;;
      esac
    done
    [[ $problems -eq 0 ]] || exit 1
    printf 'prlaunch-gate: OK -- all 4 gates recorded at HEAD %s for %s/%s\n' "$short" "$repo" "$branch"
    exit 0
    ;;

  *)
    die "unknown subcommand '$subcmd' (record|check)"
    ;;
esac
