#!/bin/bash
set -euo pipefail
# A Stop hook that exits 2 blocks the session from stopping, and anything it
# prints on stdout is fed back to the model — so this script never writes to
# stdout and always exits 0, whatever fails inside it.
trap 'exit 0' EXIT
umask 077
[ -f "$HOME/.claude/prompt-logs/.enabled" ] || exit 0
command -v jq >/dev/null 2>&1 || exit 0
_here="$(dirname "${BASH_SOURCE[0]}")"
source "$_here/prompt_log_lib.sh"

input=$(cat)
# agent_id is only present when a sub-agent stops; prompt_log_subagent.sh
# handles those, so the main hook must stay out of the way.
agent_id=$(printf '%s' "$input" | jq -r '.agent_id // ""')
[ -z "$agent_id" ] || exit 0

session_id=$(printf '%s' "$input" | jq -r '.session_id // ""')
[ -n "$session_id" ] || exit 0
session_map="$_CLAUDE_SESSION_MAP_DIR/${session_id}"
[ -f "$session_map" ] || exit 0
session_file=$(cat "$session_map")
[ -n "$session_file" ] || exit 0

transcript=$(printf '%s' "$input" | jq -r '.transcript_path // ""')
response=$(printf '%s' "$input" | jq -r '.last_assistant_message // ""')
effort=$(printf '%s' "$input" | jq -r '.effort.level // ""')

est=""; model=""; seg_start=""; seg_end=""
if [ -n "$transcript" ] && [ -r "$transcript" ]; then
  record=$(jq -n -R -r -f "$_here/prompt_log_usage.jq" --arg mode last \
    --slurpfile P "$_here/prompt_log_prices.json" < "$transcript" 2>/dev/null) || record=""
  if [ -n "$record" ]; then
    IFS=$'\037' read -r seg_start seg_end model transcript_effort est <<< "$record"
    if [ -n "$transcript_effort" ]; then effort="$transcript_effort"; fi
  fi
fi

# Working time runs from the prompt (recorded by prompt_log_save.sh). Without
# it — logging switched on mid-session, resumed session — the transcript's own
# first and last timestamps for this request are the best available answer.
pstart_file="${session_map}.pstart"
elapsed=0
if [ -s "$pstart_file" ]; then
  pstart=$(cat "$pstart_file")
  now=$(date +%s)
  case "$pstart" in ''|*[!0-9]*) pstart=$now ;; esac
  elapsed=$((now - pstart))
elif [ -n "$seg_start" ] && [ -n "$seg_end" ]; then
  elapsed=$((seg_end - seg_start))
fi

last_file="${session_map}.last"
prev_model=""; prev_effort=""; switched=""
if [ -f "$last_file" ]; then
  read -r prev_model prev_effort < "$last_file" || true
fi
if [ -n "$model" ]; then
  switched=$(switch_line "$prev_model" "$prev_effort" "$model" "$effort")
fi

{
  printf '### %s response\n\n' "$(date '+%H:%M:%S')"
  if [ -n "$response" ]; then printf '%s\n\n' "$response"; fi
  printf 'working time: %s\n' "$(fmt_hms "$elapsed")"
  if [ -n "$est" ]; then printf '%s\n' "$est"; fi
  if [ -n "$switched" ]; then printf '%s\n' "$switched"; fi
  printf '\n%s\n\n' '---'
} >> "$session_file"

if [ -n "$model" ]; then printf '%s %s\n' "$model" "$effort" > "$last_file"; fi
# .pstart is kept: continuation Stops of the same request (background-task
# notifications restart the turn) must still time from the prompt; the next
# real prompt overwrites it (prompt_log_save.sh).
