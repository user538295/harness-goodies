#!/bin/bash
# shellcheck source-path=SCRIPTDIR
set -euo pipefail
# UserPromptSubmit hooks that exit 2 erase the user's typed prompt; normalize to 1 (non-blocking).
# Other non-zero exits surface as hook error banners — intentional for diagnostics.
# shellcheck disable=SC2154  # code is assigned inside the trap body, which shellcheck does not track
trap 'code=$?; [ "$code" -eq 2 ] && exit 1; exit "$code"' EXIT
umask 077
[ -f "$HOME/.claude/prompt-logs/.enabled" ] || exit 0
command -v jq >/dev/null 2>&1 || exit 0
source "$(dirname "${BASH_SOURCE[0]}")/prompt_log_lib.sh"
_claude_default_root_selected || exit 0


input=$(cat)
session_id=$(echo "$input" | jq -r '.session_id // ""')
prompt=$(echo "$input" | jq -r '.prompt // ""')
cwd=$(echo "$input" | jq -r '.cwd // ""')

[ -z "$session_id" ] && exit 0
[ -z "$prompt" ] && exit 0
[ -z "$cwd" ] && exit 0

# Background-task notifications reach this hook as if they were prompts. They
# are not, so they get no log entry and no working-time start. Matched on the
# full opening tag: real prompts do sometimes start with "<".
case "$prompt" in '<task-notification>'*) exit 0 ;; esac

session_map=$(_claude_session_map_path "$session_id") || exit 0
[ -f "$session_map" ] || create_session_file "$session_id" "$cwd"
session_file=$(_claude_read_session_file "$session_id") || { echo "ERROR: invalid session map for ${session_id}" >&2; exit 1; }
[ -n "$session_file" ] || { echo "ERROR: session map is empty for ${session_id}" >&2; exit 1; }
start_file=$(_claude_state_file_path "$session_id" ".pstart") || exit 0


timestamp=$(date '+%H:%M:%S')
record=$(printf '## %s\n\n%s\n\n---\n' "$timestamp" "$prompt")
# Start the working-time clock before the append so a concurrent Stop hook
# never observes a completed prompt without its start time.
_claude_atomic_state_write "$start_file" "$(date +%s)"
_claude_append_record "$session_file" "$record"

