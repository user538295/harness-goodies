#!/bin/bash
# shellcheck source-path=SCRIPTDIR
set -euo pipefail
# A Stop hook that exits 2 blocks the session from stopping, and anything it
# prints on stdout is fed back to the model — so this script never writes to
# stdout and always exits 0, whatever fails inside it.
transcript_snapshot=""
trap 'if [ -n "${transcript_snapshot:-}" ]; then unlink "$transcript_snapshot" 2>/dev/null || true; fi; exit 0' EXIT
umask 077
[ -f "$HOME/.claude/prompt-logs/.enabled" ] || exit 0
command -v jq >/dev/null 2>&1 || exit 0
_here="$(dirname "${BASH_SOURCE[0]}")"
source "$_here/prompt_log_lib.sh"
_claude_default_root_selected || exit 0


input=$(cat)
# agent_id is only present when a sub-agent stops; prompt_log_subagent.sh
# handles those, so the main hook must stay out of the way.
agent_id=$(printf '%s' "$input" | jq -r '.agent_id // ""')
[ -z "$agent_id" ] || exit 0

session_id=$(printf '%s' "$input" | jq -r '.session_id // ""')
[ -n "$session_id" ] || exit 0
session_file=$(_claude_read_session_file "$session_id") || exit 0
[ -n "$session_file" ] || exit 0
pstart_file=$(_claude_state_file_path "$session_id" ".pstart") || exit 0
last_file=$(_claude_state_file_path "$session_id" ".last") || exit 0
transcript=$(printf '%s' "$input" | jq -r '.transcript_path // ""')
seg_start=""; seg_end=""; model=""; est=""
if [ -n "$transcript" ]; then
  transcript=$(_claude_transcript_file_is_safe "$transcript") || transcript=""
  if [ -n "$transcript" ]; then
    transcript_snapshot=$(_claude_safe_snapshot "$transcript") || transcript=""
  fi
fi
response=$(printf '%s' "$input" | jq -r '.last_assistant_message // ""')
effort=$(printf '%s' "$input" | jq -r '.effort.level // ""')
if [ -n "$transcript_snapshot" ] && [ -r "$transcript_snapshot" ]; then
  record=$(jq -n -R -r -f "$_here/prompt_log_usage.jq" --arg mode last \
    --slurpfile P "$_here/prompt_log_prices.json" < "$transcript_snapshot" 2>/dev/null) || record=""
  if [ -n "$record" ]; then
    IFS=$'\037' read -r seg_start seg_end model transcript_effort est <<< "$record"
    if [ -n "$transcript_effort" ]; then effort="$transcript_effort"; fi
  fi
fi

# Working time runs from the prompt (recorded by prompt_log_save.sh). Without
# it — logging switched on mid-session, resumed session — the transcript's own
# first and last timestamps for this request are the best available answer.
elapsed=0
if [ -s "$pstart_file" ]; then
  pstart=$(cat "$pstart_file")
  now=$(date +%s)
  case "$pstart" in ''|*[!0-9]*) pstart=$now ;; esac
  elapsed=$((now - pstart))
elif [ -n "$seg_start" ] && [ -n "$seg_end" ]; then
  elapsed=$((seg_end - seg_start))
fi

prev_model=""; prev_effort=""; switched=""
if [ -f "$last_file" ]; then
  read -r prev_model prev_effort < "$last_file" || true
fi
if [ -n "$model" ]; then
  switched=$(switch_line "$prev_model" "$prev_effort" "$model" "$effort")
fi

record=$( {
  printf '### %s response\n\n' "$(date '+%H:%M:%S')"
  if [ -n "$response" ]; then printf '%s\n\n' "$response"; fi
  printf 'working time: %s\n' "$(fmt_hms "$elapsed")"
  if [ -n "$est" ]; then printf '%s\n' "$est"; fi
  if [ -n "$switched" ]; then printf '%s\n' "$switched"; fi
  printf '\n%s\n\n' '---'
} )
append_status=0
_claude_append_record "$session_file" "$record" || append_status=$?
[ "$append_status" -eq 0 ] || exit 0

if [ -n "$model" ]; then _claude_atomic_state_write "$last_file" "$model $effort"; fi
# .pstart is kept: continuation Stops of the same request (background-task
# notifications restart the turn) must still time from the prompt; the next
# real prompt overwrites it (prompt_log_save.sh).
