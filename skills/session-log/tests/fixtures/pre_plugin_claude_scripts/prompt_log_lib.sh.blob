#!/bin/bash
_CLAUDE_SESSION_MAP_DIR="$HOME/.claude/session-maps"

# Path mangling Claude uses for ~/.claude/projects/<key>/<session-id>.jsonl.
resolve_project_key() {
  printf '%s\n' "$1" | sed 's|[/._]|-|g'
}

# Seconds -> HH:MM:SS. Anything that is not a plain number counts as zero, so a
# corrupt state file can never abort a hook mid-append.
fmt_hms() {
  local s="${1:-0}"
  case "$s" in ''|*[!0-9]*) s=0 ;; esac
  printf '%02d:%02d:%02d\n' "$((s / 3600))" "$((s % 3600 / 60))" "$((s % 60))"
}

# Prints the "switched:" line when the model or the effort changed since the
# previous turn, nothing otherwise. Each half needs both sides to be known: a
# .last written before the effort was known holds "model ", and "effort  → high"
# is not a transition. With neither side known there is no line at all.
switch_line() {
  local prev_model="$1" prev_effort="$2" model="$3" effort="$4" parts=""
  if [ -n "$prev_model" ] && [ -n "$model" ] && [ "$prev_model" != "$model" ]; then
    parts="model $prev_model → $model"
  fi
  if [ -n "$prev_effort" ] && [ -n "$effort" ] && [ "$prev_effort" != "$effort" ]; then
    if [ -n "$parts" ]; then parts="$parts, "; fi
    parts="${parts}effort $prev_effort → $effort"
  fi
  if [ -n "$parts" ]; then printf 'switched: %s\n' "$parts"; fi
}

create_session_file() {
  local session_id="$1"
  local cwd="$2"
  [[ "$session_id" =~ ^[A-Za-z0-9._-]+$ ]] || return 0
  local old_umask; old_umask=$(umask); umask 077
  local project_slug
  # Include parent dir to avoid collisions between same-named projects
  project_slug=$(echo "$cwd" | sed 's|.*/\([^/]*/[^/]*\)$|\1|' | tr '/' '-')
  local prompts_dir="$HOME/.claude/prompt-logs/$project_slug"
  mkdir -p "$prompts_dir"
  chmod 700 "$prompts_dir"
  chmod 700 "$HOME/.claude/prompt-logs"
  local timestamp
  timestamp=$(date '+%Y-%m-%d_%H-%M-%S')
  local session_file="$prompts_dir/session_${timestamp}_${session_id:0:8}.md"

  # Store session map in a private directory, not world-writable /tmp
  mkdir -p "$_CLAUDE_SESSION_MAP_DIR"
  chmod 700 "$_CLAUDE_SESSION_MAP_DIR"
  echo "$session_file" > "$_CLAUDE_SESSION_MAP_DIR/${session_id}"

  # Derive path to Claude's session JSONL file
  local project_key
  project_key=$(resolve_project_key "$cwd")
  local session_jsonl="$HOME/.claude/projects/${project_key}/${session_id}.jsonl"

  {
    printf '# Prompts — %s\n\n' "$(date '+%Y-%m-%d %H:%M:%S')"
    printf '**Session ID:** %s\n' "$session_id"
    printf '**Resume:** `claude --resume %s`\n' "$session_id"
    printf '**Session file:** `%s`\n\n' "$session_jsonl"
    printf '%s\n\n' '---'
  } > "$session_file"
  umask "$old_umask"
}
