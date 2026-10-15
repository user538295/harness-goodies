#!/usr/bin/env bash
# universal-session-log: managed
set -euo pipefail
# A hook never reports an error: every failure below exits 0 without output.

PACKAGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)" || exit 0
readonly PACKAGE_ROOT
VERSION="$(tr -d '[:space:]' 2>/dev/null < "$PACKAGE_ROOT/VERSION")" || VERSION=""
readonly VERSION
[[ -n "$VERSION" ]] || exit 0  # incomplete package
HOME_ROOT="$(cd "$HOME" 2>/dev/null && pwd -P || printf '%s' "$HOME")"
readonly HOME_ROOT
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

readonly SEEDED_COPY="$CLAUDE_ROOT/skills/session-log"
readonly LEGACY_SCRIPTS_GLOB="$CLAUDE_ROOT/scripts/prompt_log_*"
readonly LEGACY_CLEANUP_STAMP="$STATE_DIR/legacy-cleanup.stamp"

# Whether older releases may have left something behind: session-log registrations in
# settings.json, the package copy they seeded under ~/.claude/skills, or their ~/.claude/scripts.
# Their obsolete state files do not count: they are harmless, and on/repair remove them.
legacy_leftovers_present() {
  local settings="$CLAUDE_ROOT/settings.json"
  [[ -e "$SEEDED_COPY/.universal-session-log.manifest" || -L "$SEEDED_COPY" ]] && return 0
  compgen -G "$LEGACY_SCRIPTS_GLOB" >/dev/null && return 0
  # A cheap superset of the registrations claude_settings.py recognizes as session-log's (owner
  # marker, claude_hook.sh commands, pre-plugin prompt_log_* scripts); keep the two in step.
  [[ -f "$settings" && ! -L "$settings" ]] && grep -qE 'claude_hook\.sh|SESSION_LOG_OWNER=|prompt_log_' "$settings"
}

# What the cleanup depends on: settings.json, this package, and whether a seeded copy or legacy
# scripts remain.
cleanup_fingerprint() {
  local settings="$CLAUDE_ROOT/settings.json"
  if [[ -f "$settings" && ! -L "$settings" ]]; then cksum < "$settings" || return 1; fi
  printf '%s\n%s\n' "$PACKAGE_ROOT" "$VERSION"
  if [[ -e "$SEEDED_COPY" || -L "$SEEDED_COPY" ]]; then printf 'seeded copy\n'; fi
  if compgen -G "$LEGACY_SCRIPTS_GLOB" >/dev/null; then printf 'legacy scripts\n'; fi
}

# The exit status of a session-log command that stopped on something transient: another one holds the
# install lock, or settings.json changed during the cleanup with no version of it left beside it. The step
# that hit it left everything as it found it, so a retry is safe; reports printed by earlier steps stand
# (TRANSIENT_FAILURE_EXIT_CODE in lib/adapter_install.sh).
readonly TRANSIENT_FAILURE_EXIT_CODE=75
# Bash reports a command a signal ended with an exit status above this: 128 + the signal number.
readonly SIGNAL_EXIT_STATUS_BASE=128

# Session start runs the legacy cleanup (`repair`), logging on or off. Best effort: it never
# fails the hook and all output is discarded. A stamp of the fingerprint after the run skips it
# until settings.json, this package, or whether a seeded copy or legacy scripts exist changes. Only
# a transient stop (TRANSIENT_FAILURE_EXIT_CODE) or a repair a signal ended before it finished leaves no
# stamp, so the next session start tries again; any other failure (say, an edited legacy script) is
# stamped like a success and runs again only when one of those changes, and `on` and `repair`
# report why it failed; `status` and `repair` name any version of settings.json it kept aside.
# With no leftover it costs one grep.
cleanup_legacy_leftovers() {
  local fingerprint status=0
  legacy_leftovers_present || return 0
  fingerprint="$(cleanup_fingerprint)" || return 0
  if [[ -f "$LEGACY_CLEANUP_STAMP" && ! -L "$LEGACY_CLEANUP_STAMP" && "$(< "$LEGACY_CLEANUP_STAMP")" == "$fingerprint" ]]; then
    return 0
  fi
  bash "$PACKAGE_ROOT/bin/session-log" --entrypoint claude --harness claude repair || status=$?
  ((status != TRANSIENT_FAILURE_EXIT_CODE && status <= SIGNAL_EXIT_STATUS_BASE)) || return 0
  # The fingerprint is taken again after the cleanup. If something else changes settings.json
  # in between, that change is stamped without a cleanup; the next change triggers one.
  fingerprint="$(cleanup_fingerprint)" || return 0
  write_state_file "$LEGACY_CLEANUP_STAMP" "$fingerprint"
}
if [[ "${1:-}" == session-start ]]; then
  cleanup_legacy_leftovers </dev/null >/dev/null 2>&1 || true
fi

input="$(cat)" || exit 0
# Claude Code passes hook input as JSON on stdin (https://code.claude.com/docs/en/hooks). Cursor 3.23.12
# also runs enabled Claude Code plugins' hooks: its workbench sends JSON carrying "cursor_version" (a
# prompt that merely mentions it arrives JSON-escaped), and its agent-host daemon's heredoc binds to the
# hook command's final `exit 0`, leaving this script no input. This detection relies on that Cursor
# behaviour. Past the legacy cleanup above, Cursor's runs neither log nor touch Claude's runtime markers.
[[ -n "$input" && "$input" != *'"cursor_version":'* ]] || exit 0
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
  # The per-process markers of Claude Code processes that are gone: status never counts them. A live
  # process this user may not signal (kill -0 fails, ps still lists it) keeps its marker, as in the
  # omp and OpenCode adapters.
  prune_dead_runtime_markers() {
    local marker pid
    for marker in "$STATE_DIR"/runtime.*.json; do
      pid="${marker##*/runtime.}"
      pid="${pid%.json}"
      [[ "$pid" =~ ^[1-9][0-9]*$ && -f "$marker" && ! -L "$marker" ]] || continue
      kill -0 "$pid" 2>/dev/null || ps -p "$pid" >/dev/null 2>&1 || unlink "$marker" 2>/dev/null || true
    done
  }
  write_runtime_temp "$temp" 2>/dev/null || exit 0
  write_runtime_temp "$process_temp" 2>/dev/null || exit 0
  mv -f "$temp" "$runtime" 2>/dev/null || exit 0
  mv -f "$process_temp" "$process_runtime" 2>/dev/null || exit 0
  [[ "${1:-}" != session-start ]] || prune_dead_runtime_markers
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
