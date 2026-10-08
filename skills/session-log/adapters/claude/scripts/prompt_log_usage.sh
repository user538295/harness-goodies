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
_claude_default_root_selected || exit 0


usage() {
  printf 'usage: %s <session-id | transcript.jsonl | --latest> [--check]\n' \
    "$(basename "$0")"
}
display_path() {
  local file="$1"
  case "$file" in
    "$_CLAUDE_HOME"/*) printf '%s/%s\n' "$HOME" "${file#"$_CLAUDE_HOME/"}" ;;
    *) printf '%s\n' "$file" ;;
  esac
}


target=""
check=0
explicit_path=0
display_transcript=""
for arg in "$@"; do
  case "$arg" in
    --check) check=1 ;;
    --latest)
      [ -z "$target" ] || { usage >&2; exit 2; }
      target="--latest"
      ;;
    -h|--help) usage; exit 0 ;;
    -*) usage >&2; exit 2 ;;
    *)
      [ -z "$target" ] || { usage >&2; exit 2; }
      target="$arg"
      ;;
  esac
done
if [ -z "$target" ]; then usage >&2; exit 2; fi

engine() { # mode; transcript content on stdin
  jq -n -R -r -f "$_here/prompt_log_usage.jq" --arg mode "$1" \
    --slurpfile P "$_here/prompt_log_prices.json"
}

if [ "$target" = "--latest" ]; then
  # The session the user is typing in is the one with the newest user prompt;
  # the newest mtime alone picks any other session that is still writing
  # output. Prompt-less sessions rank last, ties keep the mtime order. A prompt
  # is written before its file's mtime, so the newest-first scan stops once no
  # older file can hold a newer prompt — a project holds thousands of them, so
  # each is checked only when the scan reaches it.
  project_dir="$_CLAUDE_HOME/.claude/projects/$(resolve_project_key "$PWD")"
  # GNU stat is probed first: GNU `stat -f` would print file-system info.
  if stat -c %Y / >/dev/null 2>&1; then mtime_fmt=(-c $'%Y\t%n'); else mtime_fmt=(-f $'%m\t%N'); fi
  transcript=""
  best_prompt=""
  while IFS=$'\t' read -r mtime f; do
    if [ -n "$best_prompt" ] && [ "$mtime" -le "$best_prompt" ]; then break; fi
    f=$(_claude_transcript_file_is_safe "$f" 2>/dev/null) || continue
    if [ -z "$transcript" ]; then transcript="$f"; fi
    prompt=$(engine last_prompt < "$f")
    if [ -n "$prompt" ] && { [ -z "$best_prompt" ] || [ "$prompt" -gt "$best_prompt" ]; }; then
      best_prompt="$prompt"
      transcript="$f"
    fi
  done < <(
    find "$project_dir" -maxdepth 1 -name '*.jsonl' ! -name '.*' \
      -exec stat "${mtime_fmt[@]}" {} + 2>/dev/null \
      | sort -t $'\t' -k1,1nr -k2,2
  )
elif [ -e "$target" ] || [ -L "$target" ]; then
  explicit_path=1
  display_transcript="$target"
  transcript_target="$target"
  if [ "${target#/}" = "$target" ]; then transcript_target="$(pwd -P)/$target"; fi
  transcript=$(_claude_transcript_file_is_safe "$transcript_target" 2>/dev/null) || transcript=""
else
  transcript=""
  for f in "$_CLAUDE_HOME"/.claude/projects/*/"$target".jsonl; do
    safe_f=$(_claude_transcript_file_is_safe "$f" 2>/dev/null) || continue
    transcript="$safe_f"
    break
  done
fi
if [ -n "$transcript" ] && [ -z "$display_transcript" ]; then display_transcript="$(_claude_display_path "$transcript")"; fi
if [ -z "$transcript" ] || [ ! -f "$transcript" ]; then
  printf 'no transcript found for: %s\n' "$target" >&2
  exit 1
fi

printf 'session: %s\n\n' "$display_transcript"
session_id="$(basename "$transcript" .jsonl)"
subagent_dir="$(dirname "$transcript")/$session_id/subagents"

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
    safe_f=$(_claude_transcript_file_is_safe "$f" 2>/dev/null) || continue
    subagents[${#subagents[@]}]="$safe_f"
  done < <(find "$subagent_dir" -type f -name '*.jsonl' | sort)
fi

if [ "${#subagents[@]}" -gt 0 ]; then
  printf '\n'

  for f in "${subagents[@]}"; do

    meta="${f%.jsonl}.meta.json"
    meta=$(_claude_transcript_path_is_safe "$meta" 2>/dev/null) || meta=""
    agent_type="unknown"
    if [ -n "$meta" ] && [ -f "$meta" ] && [ ! -L "$meta" ]; then agent_type="$(jq -r '.agentType // "unknown"' "$meta")"; fi
    agent_id="$(basename "$f" .jsonl)"
    # Sub-agent transcripts have no prompt markers, so mode=last accumulates
    # the whole file and its start/end give the sub-agent's working time.
    s_start=""; s_end=""; s_est=""
    IFS=$'\037' read -r s_start s_end _ _ s_est < <(engine last < "$f") || true
    work=0
    case "$s_start" in ''|*[!0-9]*) s_start="" ;; esac
    case "$s_end" in ''|*[!0-9]*) s_end="" ;; esac
    if [ -n "$s_start" ] && [ -n "$s_end" ]; then work=$((s_end - s_start)); fi
    display_f="$(display_path "$f")"
    printf 'sub-agent: %s (%s), working time: %s, jsonl: %s\n%s\n' \
      "$agent_type" "$agent_id" "$(fmt_hms "$work")" "$display_f" "$s_est"
  done
fi

# Internal helper agents (SubagentStop with no transcript) leave no client-side
# record: SubagentStop carries no tokens, run time, or tool-call count for them,
# so the hook can only note that each one finished (one line per helper in
# <sid>.helpers). Only the count is reportable, and it never joins TOTAL.
helpers_file=$(_claude_state_file_path "$session_id" ".helpers" 2>/dev/null || true)
if [ -s "$helpers_file" ]; then
  read -r h_n h_ms h_calls <<<"$(awk 'NF {n++; ms += ($1 ~ /^[0-9]+$/ ? $1 : 0); calls += ($2 ~ /^[0-9]+$/ ? $2 : 0)} END {printf "%d %d %d", n, ms, calls}' "$helpers_file")"
  printf '\ninternal helpers: %d finished, cumulative run time %s, %d tool calls (not added to TOTAL; token usage not recorded client-side)\n' \
    "$h_n" "$(fmt_hms "$((h_ms / 1000))")" "$h_calls"
fi

printf '\nTOTAL (%d requests, %d sub-agents)\n' "$requests" "${#subagents[@]}"
# Sum of the request durations; parallel sub-agent time is not added.
printf 'working time: %s\n' "$(fmt_hms "$total_work")"
if [ "${#subagents[@]}" -gt 0 ]; then
  {
    cat "$transcript"
    printf '\n'
    for f in "${subagents[@]}"; do
      cat "$f"
      printf '\n'
    done
  } | engine merge
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
      printf 'check: ccusage says %s; a large gap means skills/session-log/adapters/claude/scripts/prompt_log_prices.json is stale\n' "$cc"
    fi
  else
    printf 'check: ccusage not installed - skipped\n'
  fi
fi
