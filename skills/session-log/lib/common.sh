# universal-session-log: managed
# shellcheck shell=bash
# Shared helpers sourced by bin/session-log and install.sh.
# Callers set SESSION_LOG_LOG_PREFIX before sourcing to control the fail prefix.

SESSION_LOG_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
: "${SESSION_LOG_LOG_PREFIX:=session-log}"

fail() {
  printf '%s: %s\n' "$SESSION_LOG_LOG_PREFIX" "$1" >&2
  exit 1
}

canonical_path() {
  local path="$1" resolved
  if [[ -d "$path" ]]; then
    resolved="$(cd "$path" 2>/dev/null && pwd -P)" || return 1
    printf '%s\n' "$resolved"
  else
    printf '%s\n' "$path"
  fi
}

paths_equivalent() {
  local left right
  left="$(canonical_path "$1")" || return 1
  right="$(canonical_path "$2")" || return 1
  [[ "$left" == "$right" ]]
}

# Fail if the environment variable named by $1 is set and does not resolve to $2.
guard_env() {
  local expected="$2" message="$3" value="${!1:-}"
  [[ -z "$value" ]] || paths_equivalent "$value" "$expected" || fail "$message"
}

# This shell's process id in the form lib/process_identity.py takes: Git Bash numbers its own processes, so on
# Windows it is the Windows process id.
own_process_id() {
  if [[ -r "/proc/$$/winpid" ]]; then cat "/proc/$$/winpid"; else printf '%s\n' "$$"; fi
}

# The start time of process $1 (on Windows a Windows process id, as own_process_id gives); nothing when it
# is gone.
process_start() {
  python3 "$SESSION_LOG_LIB/process_identity.py" start "$1"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "missing dependency: $1"
}

sha256_file() {
  local file="$1"
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$file" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file" | awk '{print $1}'
  else
    fail "missing dependency: shasum or sha256sum"
  fi
}

ensure_safe_parent() {
  local path="$1" current component
  local -a components=()
  IFS='/' read -r -a components <<< "$(dirname "$path")"
  current=""
  for component in "${components[@]}"; do
    [[ -n "$component" ]] || continue
    current="$current/$component"
    [[ -L "$current" ]] && fail "refusing to follow symlinked parent: $current"
    [[ -e "$current" && ! -d "$current" ]] &&
      fail "refusing non-directory parent: $current"
  done
  return 0
}

ensure_private_directory() {
  SESSION_LOG_DIRECTORY="$1" python3 "$SESSION_LOG_LIB/pathsafe.py" ensure-dir ||
    fail "cannot safely prepare private directory: $1"
}

write_private_file() {
  SESSION_LOG_FILE="$1" SESSION_LOG_CONTENT="$2" python3 "$SESSION_LOG_LIB/pathsafe.py" write-file ||
    fail "cannot safely write private file: $1"
}

safe_remove_path() {
  SESSION_LOG_REMOVE_PATH="$1" python3 "$SESSION_LOG_LIB/pathsafe.py" remove
}

# True when lib/pathsafe.py does not trust directory $1 (not sticky, and group- or other-writable or not
# this user's), so it leaves its backup there after every file it replaces.
pathsafe_keeps_backups_in() {
  SESSION_LOG_DIRECTORY="$1" python3 "$SESSION_LOG_LIB/pathsafe.py" is-untrusted-dir 2>/dev/null
}
