#!/bin/bash
set -euo pipefail
# Totals one Claude session — every request plus every sub-agent transcript —
# with the same engine the session-log Stop hook uses, so the numbers here and
# the "est. used token:" lines in the log agree.
#
# Usage: prompt_log_usage.sh <session-id | transcript.jsonl | --latest> [--check]
umask 077

_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$_here/prompt_log_lib.sh"

usage() {
  printf 'usage: %s <session-id | transcript.jsonl | --latest> [--check]\n' \
    "$(basename "$0")"
}

target=""
check=0
for arg in "$@"; do
  case "$arg" in
    --check) check=1 ;;
    --latest) target="--latest" ;;
    -h|--help) usage; exit 0 ;;
    -*) usage >&2; exit 2 ;;
    *) target="$arg" ;;
  esac
done
if [ -z "$target" ]; then usage >&2; exit 2; fi

if [ "$target" = "--latest" ]; then
  # This machine's coreutils shadow ls/stat, so newest-first is decided with
  # bash's own -nt rather than by parsing a listing.
  project_dir="$HOME/.claude/projects/$(resolve_project_key "$PWD")"
  transcript=""
  for f in "$project_dir"/*.jsonl; do
    [ -f "$f" ] || continue
    if [ -z "$transcript" ] || [ "$f" -nt "$transcript" ]; then transcript="$f"; fi
  done
elif [ -f "$target" ]; then
  transcript="$target"
else
  transcript=""
  for f in "$HOME"/.claude/projects/*/"$target".jsonl; do
    if [ -f "$f" ]; then transcript="$f"; break; fi
  done
fi

if [ -z "$transcript" ] || [ ! -f "$transcript" ]; then
  printf 'no transcript found for: %s\n' "$target" >&2
  exit 1
fi

engine() { # mode; transcript content on stdin
  jq -n -R -r -f "$_here/prompt_log_usage.jq" --arg mode "$1" \
    --slurpfile P "$_here/prompt_log_prices.json"
}

session_id="$(basename "$transcript" .jsonl)"
subagent_dir="$(dirname "$transcript")/$session_id/subagents"

printf 'session: %s\n\n' "$transcript"

requests=0
total_work=0
while IFS=$'\037' read -r started dur head est; do
  requests=$((requests + 1))
  case "$dur" in ''|*[!0-9]*) dur=0 ;; esac
  total_work=$((total_work + dur))
  printf '%d. %s (working time %s) "%s"\n%s\n' \
    "$requests" "$started" "$(fmt_hms "$dur")" "$head" "$est"
done < <(engine segments < "$transcript")

# Recursive on purpose: workflow sub-agents live under subagents/workflows/**.
subagents=()
if [ -d "$subagent_dir" ]; then
  while IFS= read -r f; do
    subagents[${#subagents[@]}]="$f"
  done < <(find "$subagent_dir" -type f -name '*.jsonl' | sort)
fi

if [ "${#subagents[@]}" -gt 0 ]; then
  printf '\n'
  for f in "${subagents[@]}"; do
    meta="${f%.jsonl}.meta.json"
    agent_type="unknown"
    if [ -f "$meta" ]; then agent_type="$(jq -r '.agentType // "unknown"' "$meta")"; fi
    agent_id="$(basename "$f" .jsonl)"
    # Sub-agent transcripts have no prompt markers, so mode=last accumulates
    # the whole file and its start/end give the sub-agent's working time.
    s_start=""; s_end=""; s_est=""
    IFS=$'\037' read -r s_start s_end _ _ s_est < <(engine last < "$f") || true
    work=0
    case "$s_start" in ''|*[!0-9]*) s_start="" ;; esac
    case "$s_end" in ''|*[!0-9]*) s_end="" ;; esac
    if [ -n "$s_start" ] && [ -n "$s_end" ]; then work=$((s_end - s_start)); fi
    printf 'sub-agent: %s (%s), working time: %s, jsonl: %s\n%s\n' \
      "$agent_type" "$agent_id" "$(fmt_hms "$work")" "$f" "$s_est"
  done
fi

# Internal helper agents (SubagentStop with no transcript) leave no client-side
# record: SubagentStop carries no tokens, run time, or tool-call count for them,
# so the hook can only note that each one finished (one line per helper in
# <sid>.helpers). Only the count is reportable, and it never joins TOTAL.
helpers_file="$_CLAUDE_SESSION_MAP_DIR/${session_id}.helpers"
if [ -s "$helpers_file" ]; then
  h_n=$(grep -c . "$helpers_file" || true)
  printf '\ninternal helpers: %d finished (run time, tool calls, and token usage not recorded client-side; not added to TOTAL)\n' \
    "$h_n"
fi

printf '\nTOTAL (%d requests, %d sub-agents)\n' "$requests" "${#subagents[@]}"
# Sum of the request durations; parallel sub-agent time is not added.
printf 'working time: %s\n' "$(fmt_hms "$total_work")"
if [ "${#subagents[@]}" -gt 0 ]; then
  cat "$transcript" "${subagents[@]}" | engine merge
else
  engine merge < "$transcript"
fi

if [ "$check" -eq 1 ]; then
  if command -v ccusage >/dev/null 2>&1; then
    cc="$(ccusage session -i "$session_id" --json 2>/dev/null \
      | jq -r 'if .totalCost == null then empty
               else "$\(.totalCost * 100 | round / 100) / \(.totalTokens) tokens" end' 2>/dev/null || true)"
    if [ -z "$cc" ]; then
      printf 'check: ccusage returned no total for this session - skipped\n'
    else
      printf 'check: ccusage says %s; a large gap means skills/session-log/scripts/prompt_log_prices.json is stale\n' "$cc"
    fi
  else
    printf 'check: ccusage not installed - skipped\n'
  fi
fi
