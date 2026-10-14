#!/usr/bin/env bash
# universal-session-log: managed
set -euo pipefail
# A hook never reports an error: every failure below exits 0 without output.

readonly PACKAGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
readonly VERSION="$(tr -d '[:space:]' 2>/dev/null < "$PACKAGE_ROOT/VERSION")"
[[ -n "$VERSION" ]] || exit 0  # incomplete package
readonly HOME_ROOT="$(cd "$HOME" 2>/dev/null && pwd -P || printf '%s' "$HOME")"
readonly CLAUDE_ROOT="$HOME_ROOT/.claude"
readonly STATE_DIR="$CLAUDE_ROOT/session-log"
readonly SCRIPT_DIR="$PACKAGE_ROOT/adapters/claude/scripts"
check_default_root_alias() {
  local raw_home="$HOME" raw_root="$HOME/.claude" raw_real
  if [[ -L "$raw_home" ]]; then
    raw_real="$(cd "$raw_home" 2>/dev/null && pwd -P)" || return 1
    [[ "$raw_real" == "$HOME_ROOT" ]] || return 1
  fi
  if [[ -L "$raw_root" ]]; then
    raw_real="$(cd "$raw_root" 2>/dev/null && pwd -P)" || return 1
    [[ "$raw_real" == "$CLAUDE_ROOT" ]] || return 1
  fi
}
check_default_root_alias || exit 0

if [[ -n "${CLAUDE_CONFIG_DIR:-}" ]]; then
  config_real="$(cd "$CLAUDE_CONFIG_DIR" 2>/dev/null && pwd -P || true)"
  [[ "$config_real" == "$CLAUDE_ROOT" ]] || exit 0  # a custom CLAUDE_CONFIG_DIR is unsupported
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
    if [[ ! -e "$current" ]]; then (umask 077; mkdir "$current") 2>/dev/null || return 1; fi
    [[ -d "$current" ]] || return 1
  done
}

check_path_components "$CLAUDE_ROOT" || exit 0
check_path_components "$CLAUDE_ROOT/prompt-logs" || exit 0
check_path_components "$STATE_DIR" || exit 0


# Atomically replace state file $1 with content $2. Best effort: refuses a symlinked or
# directory target and never leaves its temporary file behind.
write_state_file() {
  local target="$1" temp="$STATE_DIR/.${1##*/}.$$.tmp"
  [[ ! -L "$target" && ! -d "$target" ]] || return 0
  ensure_directory "$STATE_DIR" || return 0
  (umask 077; set -C; printf '%s' "$2" > "$temp") 2>/dev/null || return 0
  mv -f "$temp" "$target" 2>/dev/null || unlink "$temp" 2>/dev/null || true
}

# Whether older releases may have left session-log registrations in settings.json.
legacy_leftovers_present() {
  local settings="$CLAUDE_ROOT/settings.json"
  [[ -f "$settings" && ! -L "$settings" ]] && grep -qE 'claude_hook\.sh|SESSION_LOG_OWNER=' "$settings"
}

# What the cleanup depends on: settings.json and this package.
cleanup_fingerprint() {
  local settings="$CLAUDE_ROOT/settings.json"
  if [[ -f "$settings" && ! -L "$settings" ]]; then cksum < "$settings" || return 1; fi
  printf '%s\n%s\n' "$PACKAGE_ROOT" "$VERSION"
}

# Session start runs the legacy cleanup (`repair`), logging on or off. Best effort: it never
# fails the hook and all output is discarded. A stamp of the last successful cleanup's
# fingerprint skips it until that fingerprint changes; with no leftover it costs one grep.
cleanup_legacy_leftovers() {
  local stamp="$STATE_DIR/legacy-cleanup.stamp" fingerprint
  legacy_leftovers_present || return 0
  fingerprint="$(cleanup_fingerprint)" || return 0
  if [[ -f "$stamp" && ! -L "$stamp" && "$(< "$stamp")" == "$fingerprint" ]]; then
    return 0
  fi
  bash "$PACKAGE_ROOT/bin/session-log" --entrypoint claude --harness claude repair || return 0
  # Benign race: a write landing between the cleanup and this fingerprint is stamped uncleaned;
  # the next change to the fingerprint triggers the cleanup again.
  fingerprint="$(cleanup_fingerprint)" || return 0
  write_state_file "$stamp" "$fingerprint"
}
if [[ "${1:-}" == session-start ]]; then
  cleanup_legacy_leftovers </dev/null >/dev/null 2>&1 || true
fi

input="$(cat)"
enabled_file="$CLAUDE_ROOT/prompt-logs/.enabled"
runtime="$STATE_DIR/runtime.json"
process_runtime="$STATE_DIR/runtime.${PPID}.json"
if [[ -L "$runtime" || ( -e "$runtime" && ! -f "$runtime" ) ||
      -L "$process_runtime" || ( -e "$process_runtime" && ! -f "$process_runtime" ) ]]; then
  exit 0  # unsafe runtime file
fi
if [[ ! -f "$enabled_file" || -L "$enabled_file" ]]; then
  [[ ! -e "$runtime" ]] || unlink "$runtime" 2>/dev/null || true
  [[ ! -e "$process_runtime" ]] || unlink "$process_runtime" 2>/dev/null || true
  exit 0
fi
# jq is needed only while logging is on: Git for Windows ships Git Bash without it.
command -v jq >/dev/null 2>&1 || exit 0
ensure_directory "$STATE_DIR" || exit 0
session_id="$(printf '%s' "$input" | jq -r '.session_id // .sessionId // empty' 2>/dev/null || true)"
if [[ -n "$session_id" ]]; then
  nonce="$(printf '%s' "$session_id" | jq -Rr @uri 2>/dev/null)" || exit 0
  temp="$STATE_DIR/.runtime.$$.${RANDOM}.tmp"
  process_temp="$STATE_DIR/.runtime.$$.${RANDOM}.process.tmp"
  if [[ -e "$temp" || -L "$temp" || -e "$process_temp" || -L "$process_temp" ]]; then
    exit 0  # runtime temp already exists
  fi
  remove_runtime_temps() {
    local file
    for file in "${temp:-}" "${process_temp:-}"; do
      [[ -z "$file" || -L "$file" ]] || unlink "$file" 2>/dev/null || true
    done
  }
  trap remove_runtime_temps EXIT
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
      '{harness:$harness,version:$version,session_id:$session_id,nonce:$nonce,
        loaded_at:$loaded_at,pid:$pid,process_start:$process_start}' > "$target")
  }
  write_runtime_temp "$temp" 2>/dev/null || exit 0
  write_runtime_temp "$process_temp" 2>/dev/null || exit 0
  mv -f "$temp" "$runtime" 2>/dev/null || exit 0
  mv -f "$process_temp" "$process_runtime" 2>/dev/null || exit 0
else
  :
fi

case "${1:-}" in
  session-start) script="$SCRIPT_DIR/prompt_log_new_session.sh" ;;
  user-prompt) script="$SCRIPT_DIR/prompt_log_save.sh" ;;
  stop) script="$SCRIPT_DIR/prompt_log_stop.sh" ;;
  subagent-stop) script="$SCRIPT_DIR/prompt_log_subagent.sh" ;;
  *) exit 0 ;;  # unknown Claude lifecycle event
esac

[[ -f "$script" ]] || exit 0  # Claude lifecycle adapter is incomplete
# Hook stdout is meaningful to Claude Code and passes through; errors never surface.
printf '%s' "$input" | bash "$script" 2>/dev/null || true
