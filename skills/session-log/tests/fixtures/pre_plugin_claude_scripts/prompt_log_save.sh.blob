#!/bin/bash
set -euo pipefail
# UserPromptSubmit hooks that exit 2 erase the user's typed prompt; normalize to 1 (non-blocking).
# Other non-zero exits surface as hook error banners — intentional for diagnostics.
trap 'code=$?; [ "$code" -eq 2 ] && exit 1; exit "$code"' EXIT
umask 077
[ -f "$HOME/.claude/prompt-logs/.enabled" ] || exit 0
command -v jq >/dev/null 2>&1 || exit 0
source "$(dirname "${BASH_SOURCE[0]}")/prompt_log_lib.sh"

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

session_map="$_CLAUDE_SESSION_MAP_DIR/${session_id}"
[ ! -f "$session_map" ] && create_session_file "$session_id" "$cwd"

session_file=$(cat "$session_map") || { echo "ERROR: failed to read session map for ${session_id}" >&2; exit 1; }
[ -z "$session_file" ] && { echo "ERROR: session map is empty for ${session_id}" >&2; exit 1; }

timestamp=$(date '+%H:%M:%S')

{
  printf '## %s\n\n' "$timestamp"
  printf '%s\n' "$prompt"
  printf '\n%s\n\n' '---'
} >> "$session_file"

# Start of the working time the Stop hook reports; it consumes this file.
date +%s > "${session_map}.pstart"
