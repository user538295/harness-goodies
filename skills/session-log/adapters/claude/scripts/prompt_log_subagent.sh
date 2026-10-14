#!/bin/bash
# shellcheck source-path=SCRIPTDIR
set -euo pipefail
# Same contract as prompt_log_stop.sh: never write stdout, always exit 0.
jsonl_snapshot=""
meta_snapshot=""
# shellcheck disable=SC2154  # snapshot is the loop variable inside the trap body, which shellcheck does not track
trap 'for snapshot in "${jsonl_snapshot:-}" "${meta_snapshot:-}"; do [ -n "$snapshot" ] && unlink "$snapshot" 2>/dev/null || true; done; exit 0' EXIT
umask 077
[ -f "$HOME/.claude/prompt-logs/.enabled" ] || exit 0
command -v jq >/dev/null 2>&1 || exit 0
_here="$(dirname "${BASH_SOURCE[0]}")"
source "$_here/prompt_log_lib.sh"
_claude_default_root_selected || exit 0


input=$(cat)
session_id=$(printf '%s' "$input" | jq -r '.session_id // ""')
agent_id=$(printf '%s' "$input" | jq -r '.agent_id // ""')
agent_type=$(printf '%s' "$input" | jq -r '.agent_type // ""')
transcript=$(printf '%s' "$input" | jq -r '.transcript_path // ""')
[[ "$agent_id" =~ ^[A-Za-z0-9_-]{1,128}$ ]] || exit 0
[ -n "$session_id" ] || exit 0
[ -n "$transcript" ] || exit 0
transcript=$(_claude_transcript_path_is_safe "$transcript") || exit 0

session_file=$(_claude_read_session_file "$session_id") || exit 0
[ -n "$session_file" ] || exit 0
helpers_file=$(_claude_state_file_path "$session_id" ".helpers") || exit 0

jsonl="$(dirname "$transcript")/${session_id}/subagents/agent-${agent_id}.jsonl"
jsonl=$(_claude_transcript_path_is_safe "$jsonl") || exit 0
if [ -e "$jsonl" ] || [ -L "$jsonl" ]; then
  jsonl=$(_claude_transcript_file_is_safe "$jsonl") || exit 0
fi
display_jsonl=$(_claude_display_path "$jsonl")
if [ -f "$jsonl" ]; then
  jsonl_snapshot=$(_claude_safe_snapshot "$jsonl") || exit 0
fi


duration_ms="$(printf '%s' "$input" | jq -r 'def n: (tonumber? // 0) | if . < 0 then 0 else floor end; [(.duration_ms // 0 | n), (.tool_calls_count // 0 | n)] | map(tostring) | join(" ")' 2>/dev/null)" || duration_ms="0 0"
if [ ! -f "$jsonl" ]; then
  if [ -z "$agent_type" ]; then
    # No transcript and no type: an internal helper agent. SubagentStop delivers
    # neither tokens, run time, nor tool-call count for it, so only its
    # occurrence is knowable — append one line per helper for the aggregator to
    # count. No log line.
    _claude_append_state_record "$helpers_file" "$(printf '%s\n' "$duration_ms")"
    exit 0
  fi
  # A typed agent without a transcript is a real anomaly: keep the marker.
  _claude_append_record "$session_file" "$(printf '%s sub-agent finished: %s (agent-%s), jsonl: %s (not found)\n\n' \
    "$(date '+%H:%M:%S')" "$agent_type" "$agent_id" "$display_jsonl")"
  exit 0
fi

if [ -z "$agent_type" ]; then
  meta="${jsonl%.jsonl}.meta.json"
  meta=$(_claude_transcript_path_is_safe "$meta") || meta=""
  if [ -n "$meta" ] && { [ -e "$meta" ] || [ -L "$meta" ]; }; then
    meta=$(_claude_transcript_file_is_safe "$meta") || meta=""
  fi
  if [ -n "$meta" ] && [ -f "$meta" ]; then
    meta_snapshot=$(_claude_safe_snapshot "$meta") || meta=""
  fi
  if [ -n "$meta_snapshot" ] && [ -f "$meta_snapshot" ]; then
    agent_type=$(jq -r '.agentType // "sub-agent"' "$meta_snapshot" 2>/dev/null) || agent_type="sub-agent"
  else
    agent_type="sub-agent"
  fi
  [ -n "$agent_type" ] || agent_type="sub-agent"
fi

# Sub-agent transcripts carry no prompt markers, so mode=last accumulates the
# whole file: start/end span the run, the est-line totals it.
record=$(jq -n -R -r -f "$_here/prompt_log_usage.jq" --arg mode last \
  --slurpfile P "$_here/prompt_log_prices.json" < "$jsonl_snapshot" 2>/dev/null) || record=""
seg_start=""; seg_end=""; est=""
if [ -n "$record" ]; then
  IFS=$'\037' read -r seg_start seg_end _ _ est <<< "$record" || true
fi
# A transcript with no usage entries renders "model: -": nothing to report.
case "$est" in ''|*"model: -"*) est="" ;; esac

if [ -n "$est" ]; then
  case "$seg_start" in ''|*[!0-9]*) seg_start="" ;; esac
  case "$seg_end" in ''|*[!0-9]*) seg_end="" ;; esac
  elapsed=0
  if [ -n "$seg_start" ] && [ -n "$seg_end" ]; then elapsed=$((seg_end - seg_start)); fi
  _claude_append_record "$session_file" "$(printf '%s sub-agent finished: %s (agent-%s), working time: %s, %s, jsonl: %s\n\n' \
    "$(date '+%H:%M:%S')" "$agent_type" "$agent_id" "$(fmt_hms "$elapsed")" "$est" "$display_jsonl")"
else
  _claude_append_record "$session_file" "$(printf '%s sub-agent finished: %s (agent-%s), jsonl: %s\n\n' \
    "$(date '+%H:%M:%S')" "$agent_type" "$agent_id" "$display_jsonl")"
fi
