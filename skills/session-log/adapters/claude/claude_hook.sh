#!/usr/bin/env bash
# universal-session-log: managed
set -euo pipefail

readonly PACKAGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
readonly VERSION="$(tr -d '[:space:]' < "$PACKAGE_ROOT/VERSION")"
readonly CLAUDE_HOME="$(cd "$HOME" 2>/dev/null && pwd -P || printf '%s' "$HOME")"
readonly CLAUDE_ROOT="$CLAUDE_HOME/.claude"
readonly STATE_DIR="$CLAUDE_ROOT/session-log"
readonly SCRIPT_DIR="$PACKAGE_ROOT/adapters/claude/scripts"
check_default_root_alias() {
  local raw_home="$HOME" raw_root="$HOME/.claude" raw_real
  if [[ -L "$raw_home" ]]; then
    raw_real="$(cd "$raw_home" 2>/dev/null && pwd -P)" || return 1
    [[ "$raw_real" == "$CLAUDE_HOME" ]] || return 1
  fi
  if [[ -L "$raw_root" ]]; then
    raw_real="$(cd "$raw_root" 2>/dev/null && pwd -P)" || return 1
    [[ "$raw_real" == "$CLAUDE_ROOT" ]] || return 1
  fi
}
check_default_root_alias || {
  printf 'session-log: unsafe Claude root alias\n' >&2
  exit 1
}

if [[ -n "${CLAUDE_CONFIG_DIR:-}" ]]; then
  config_real="$(cd "$CLAUDE_CONFIG_DIR" 2>/dev/null && pwd -P || true)"
  [[ "$config_real" == "$CLAUDE_ROOT" ]] || {
    printf 'session-log: custom CLAUDE_CONFIG_DIR is unsupported\n' >&2
    exit 1
  }
fi

check_path_components() {
  local target="$1" current="/" component
  local IFS='/'
  read -r -a components <<< "${target#/}"
  for component in "${components[@]}"; do
    [[ -n "$component" ]] || continue
    current="${current%/}/$component"
    [[ ! -L "$current" ]] || return 1
    if [[ -e "$current" && ! -d "$current" ]]; then return 1; fi
  done
}

ensure_directory() {
  local target="$1" current="/" component
  local IFS='/'
  read -r -a components <<< "${target#/}"
  for component in "${components[@]}"; do
    [[ -n "$component" ]] || continue
    current="${current%/}/$component"
    if [[ -L "$current" ]]; then return 1; fi
    if [[ ! -e "$current" ]]; then mkdir "$current" || return 1; fi
    [[ -d "$current" ]] || return 1
  done
}

check_path_components "$CLAUDE_ROOT" || { printf 'session-log: unsafe Claude root\n' >&2; exit 1; }
command -v jq >/dev/null 2>&1 || { printf 'session-log: missing dependency: jq\n' >&2; exit 1; }

check_path_components "$CLAUDE_ROOT/prompt-logs" || { printf 'session-log: unsafe Claude log root\n' >&2; exit 1; }
check_path_components "$STATE_DIR" || { printf 'session-log: unsafe Claude runtime root\n' >&2; exit 1; }

# A plugin-cache copy runs from the plugin's hooks/hooks.json, so every owned
# settings.json hook is a duplicate, often a dead versioned path left by an
# older release. Remove them at session start, logging on or off. Best effort:
# this cleanup never fails the hook, and the grep keeps the clean case cheap.
remove_owned_settings_hooks() {
  local settings="$CLAUDE_ROOT/settings.json" owner="universal-session-log"
  case "$PACKAGE_ROOT" in
    "$CLAUDE_HOME/.claude/plugins/cache/"*/claude-goodies/*/skills/session-log) ;;
    *) return 0 ;;
  esac
  [[ -f "$settings" && ! -L "$settings" ]] || return 0
  grep -qF "SESSION_LOG_OWNER=$owner " "$settings" || return 0
  PLUGIN_ROOT="${PACKAGE_ROOT%/skills/session-log}" \
    python3 "$PACKAGE_ROOT/lib/claude_settings.py" plugin-hooks-current || return 0
  SETTINGS_PATH="$settings" SESSION_LOG_OWNER_MARKER="$owner" \
    python3 "$PACKAGE_ROOT/lib/claude_settings.py" remove
}
if [[ "${1:-}" == session-start ]]; then
  remove_owned_settings_hooks </dev/null >/dev/null 2>&1 || true
fi

input="$(cat)"
enabled_file="$CLAUDE_ROOT/prompt-logs/.enabled"
runtime="$STATE_DIR/runtime.json"
process_runtime="$STATE_DIR/runtime.${PPID}.json"
if [[ -L "$runtime" || ( -e "$runtime" && ! -f "$runtime" ) ||
      -L "$process_runtime" || ( -e "$process_runtime" && ! -f "$process_runtime" ) ]]; then
  printf 'session-log: unsafe Claude runtime file\n' >&2
  exit 1
fi
if [[ ! -f "$enabled_file" || -L "$enabled_file" ]]; then
  [[ -e "$runtime" ]] && unlink "$runtime"
  [[ -e "$process_runtime" ]] && unlink "$process_runtime"
  exit 0
fi
ensure_directory "$STATE_DIR" || { printf 'session-log: unsafe Claude runtime root\n' >&2; exit 1; }
session_id="$(printf '%s' "$input" | jq -r '.session_id // .sessionId // empty' 2>/dev/null || true)"
if [[ -n "$session_id" ]]; then
  nonce="$(printf '%s' "$session_id" | jq -Rr @uri)"
  temp="$STATE_DIR/.runtime.$$.${RANDOM}.tmp"
  process_temp="$STATE_DIR/.runtime.$$.${RANDOM}.process.tmp"
  if [[ -e "$temp" || -L "$temp" || -e "$process_temp" || -L "$process_temp" ]]; then
    printf 'session-log: runtime temp already exists\n' >&2
    exit 1
  fi
  trap 'for file in "${temp:-}" "${process_temp:-}"; do [[ -z "$file" || -L "$file" ]] || unlink "$file" 2>/dev/null || true; done' EXIT
  write_runtime_temp() {
    local target="$1"
    (umask 077; set -C; jq -n \
      --arg harness claude \
      --arg version "$VERSION" \
      --arg session_id "$session_id" \
      --arg nonce "$nonce" \
      --arg loaded_at "$(date +%s)" \
      --arg pid "$PPID" \
      --arg process_start "$(ps -p "$PPID" -o lstart= 2>/dev/null | sed 's/^ *//')" \
      '{harness:$harness,version:$version,session_id:$session_id,nonce:$nonce,loaded_at:$loaded_at,pid:$pid,process_start:$process_start}' > "$target")
  }
  write_runtime_temp "$temp"
  write_runtime_temp "$process_temp"
  mv -f "$temp" "$runtime"
  mv -f "$process_temp" "$process_runtime"
else
  :
fi

case "${1:-}" in
  session-start) script="$SCRIPT_DIR/prompt_log_new_session.sh" ;;
  user-prompt) script="$SCRIPT_DIR/prompt_log_save.sh" ;;
  stop) script="$SCRIPT_DIR/prompt_log_stop.sh" ;;
  subagent-stop) script="$SCRIPT_DIR/prompt_log_subagent.sh" ;;
  *) printf 'session-log: unknown Claude lifecycle event\n' >&2; exit 2 ;;
esac

[[ -f "$script" ]] || { printf 'session-log: Claude lifecycle adapter is incomplete\n' >&2; exit 1; }
printf '%s' "$input" | bash "$script"
