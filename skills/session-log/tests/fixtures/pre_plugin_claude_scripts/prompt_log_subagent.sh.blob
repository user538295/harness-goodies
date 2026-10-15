#!/bin/bash
set -euo pipefail
# Same contract as prompt_log_stop.sh: never write stdout, always exit 0.
trap 'exit 0' EXIT
umask 077
[ -f "$HOME/.claude/prompt-logs/.enabled" ] || exit 0
command -v jq >/dev/null 2>&1 || exit 0
_here="$(dirname "${BASH_SOURCE[0]}")"
source "$_here/prompt_log_lib.sh"

input=$(cat)
session_id=$(printf '%s' "$input" | jq -r '.session_id // ""')
agent_id=$(printf '%s' "$input" | jq -r '.agent_id // ""')
agent_type=$(printf '%s' "$input" | jq -r '.agent_type // ""')
transcript=$(printf '%s' "$input" | jq -r '.transcript_path // ""')
[ -n "$session_id" ] || exit 0
[ -n "$agent_id" ] || exit 0
[ -n "$transcript" ] || exit 0

session_map="$_CLAUDE_SESSION_MAP_DIR/${session_id}"
[ -f "$session_map" ] || exit 0
session_file=$(cat "$session_map")
[ -n "$session_file" ] || exit 0

# transcript_path is the parent session's; the sub-agent's own transcript sits
# beside it under <session-id>/subagents/.
jsonl="$(dirname "$transcript")/${session_id}/subagents/agent-${agent_id}.jsonl"

if [ ! -f "$jsonl" ]; then
  if [ -z "$agent_type" ]; then
    # No transcript and no type: an internal helper agent. SubagentStop delivers
    # neither tokens, run time, nor tool-call count for it, so only its
    # occurrence is knowable — append one line per helper for the aggregator to
    # count. No log line.
    printf 'helper\n' >> "${session_map}.helpers"
    exit 0
  fi
  # A typed agent without a transcript is a real anomaly: keep the marker.
  printf '%s sub-agent finished: %s (agent-%s), jsonl: %s (not found)\n\n' \
    "$(date '+%H:%M:%S')" "$agent_type" "$agent_id" "$jsonl" >> "$session_file"
  exit 0
fi

if [ -z "$agent_type" ]; then
  agent_type=$(jq -r '.agentType // "sub-agent"' "${jsonl%.jsonl}.meta.json" 2>/dev/null) || agent_type="sub-agent"
  [ -n "$agent_type" ] || agent_type="sub-agent"
fi

# Sub-agent transcripts carry no prompt markers, so mode=last accumulates the
# whole file: start/end span the run, the est-line totals it.
record=$(jq -n -R -r -f "$_here/prompt_log_usage.jq" --arg mode last \
  --slurpfile P "$_here/prompt_log_prices.json" < "$jsonl" 2>/dev/null) || record=""
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
  printf '%s sub-agent finished: %s (agent-%s), working time: %s, %s, jsonl: %s\n\n' \
    "$(date '+%H:%M:%S')" "$agent_type" "$agent_id" "$(fmt_hms "$elapsed")" "$est" "$jsonl" >> "$session_file"
else
  printf '%s sub-agent finished: %s (agent-%s), jsonl: %s\n\n' \
    "$(date '+%H:%M:%S')" "$agent_type" "$agent_id" "$jsonl" >> "$session_file"
fi
