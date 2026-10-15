# universal-session-log: managed
# shellcheck shell=bash
# Adapter-install machinery for bin/session-log: Codex's hooks.json entries, the one adapter a
# harness plugin does not register itself (see plugin_owns_hooks in bin/session-log). Its install
# lock, state-directory checks, runtime-marker and repair-report helpers serve every harness.
#
# Sourced by bin/session-log right after lib/common.sh. Runs in the same shell,
# so it shares bin's globals (HARNESS, HARNESS_LABEL, HARNESS_ROOT, STATE_DIR, FLAG_FILE,
# RUNTIME_FILE, INSTALL_LOCK, INSTALL_LOCK_OWNER_START, REPAIR_REPORTED) and its source_for.

native_hooks_current() {
  local config="$HARNESS_ROOT/hooks.json"
  require_command python3
  [[ -f "$config" && ! -L "$config" ]] || return 1
  python3 "$(source_for configurator)" check "$HARNESS" "$config"
}

# The marketplace Codex installs codex-goodies from: the "name" of .agents/plugins/marketplace.json,
# which CODEX_MARKETPLACE in adapters/native/install_hooks.py names too (tests check they agree).
readonly CODEX_MARKETPLACE="user538295"
# Where Codex caches the codex-goodies plugin, relative to ~/.codex; the CODEX_COMMAND of
# adapters/native/install_hooks.py runs the newest package found here.
readonly CODEX_PLUGIN_CACHE="plugins/cache/$CODEX_MARKETPLACE/codex-goodies"

# True when a codex-goodies plugin package, the only package Codex's hooks.json command runs, is installed.
codex_plugin_package_present() {
  local hook
  for hook in "$HARNESS_ROOT/$CODEX_PLUGIN_CACHE"/*/skills/session-log/adapters/native/session_log_hook.py; do
    [[ -f "$hook" ]] && return 0
  done
  return 1
}

# `on` without a codex-goodies plugin package would register hooks that never log. The marketplace
# comes from the GitHub repository user538295/harness-goodies (README.md, Codex plugin).
require_codex_plugin_package() {
  codex_plugin_package_present ||
    fail "Codex hooks run the codex-goodies plugin package; install it with 'codex plugin marketplace add user538295/harness-goodies' and 'codex plugin add codex-goodies@$CODEX_MARKETPLACE', then rerun on"
}

# Codex's hooks.json command picks the newest plugin package itself, so the adapter is current
# exactly when hooks.json holds that command for every lifecycle event.
adapter_current() {
  validate_state_dir
  native_hooks_current
}

validate_state_dir() {
  ensure_safe_parent "$STATE_DIR/adapter.version"
  [[ ! -L "$STATE_DIR" ]] ||
    fail "refusing to follow symlinked state directory: $STATE_DIR"
  [[ ! -e "$STATE_DIR" || -d "$STATE_DIR" ]] ||
    fail "refusing non-directory state path: $STATE_DIR"
}
preflight_enable_flag() {
  ensure_safe_parent "$FLAG_FILE"
  [[ ! -L "$FLAG_FILE" ]] ||
    fail "refusing to overwrite symlinked enable flag: $FLAG_FILE"
  [[ ! -d "$FLAG_FILE" ]] ||
    fail "refusing to overwrite directory enable flag: $FLAG_FILE"
}

preflight_state_file() {
  local file="$1"
  ensure_safe_parent "$file"
  [[ ! -L "$file" ]] ||
    fail "refusing to overwrite symlinked state file: $file"
  [[ ! -d "$file" ]] ||
    fail "refusing to overwrite state directory: $file"
}

preflight_install_lock() {
  local owner_pid owner_start actual_start child
  ensure_safe_parent "$INSTALL_LOCK/owner"
  [[ ! -L "$INSTALL_LOCK" ]] ||
    fail "refusing symlinked install lock: $INSTALL_LOCK"
  [[ ! -e "$INSTALL_LOCK" ]] && return
  [[ -d "$INSTALL_LOCK" ]] ||
    fail "refusing non-directory install lock: $INSTALL_LOCK"
  for child in "$INSTALL_LOCK"/* "$INSTALL_LOCK"/.[!.]* "$INSTALL_LOCK"/..?*; do
    [[ -e "$child" || -L "$child" ]] || continue
    [[ "$child" == "$INSTALL_LOCK/owner" ]] ||
      fail "refusing install lock containing unexpected file: $child"
  done
  [[ -e "$INSTALL_LOCK/owner" ]] || return
  [[ -f "$INSTALL_LOCK/owner" && ! -L "$INSTALL_LOCK/owner" ]] ||
    fail "refusing invalid install lock owner: $INSTALL_LOCK/owner"
  owner_pid="$(sed -n '1p' "$INSTALL_LOCK/owner")"
  owner_start="$(sed -n '2p' "$INSTALL_LOCK/owner" | sed 's/^ *//; s/[[:space:]]*$//')"
  [[ "$owner_pid" =~ ^[1-9][0-9]*$ && -n "$owner_start" ]] ||
    fail "refusing invalid install lock owner: $INSTALL_LOCK/owner"
  if kill -0 "$owner_pid" 2>/dev/null; then
    actual_start="$(ps -p "$owner_pid" -o lstart= 2>/dev/null | sed 's/^ *//; s/[[:space:]]*$//')"
    if [[ -n "$actual_start" && "$actual_start" == "$owner_start" ]]; then
      fail_install_lock_busy
    fi
  fi
  return 0
}

preflight_native_hooks() {
  local config="$HARNESS_ROOT/hooks.json"
  [[ -f "$(source_for configurator)" && ! -L "$(source_for configurator)" ]] ||
    fail "adapter asset is missing: $(source_for configurator)"
  ensure_safe_parent "$config"
  [[ ! -L "$config" ]] || fail "refusing to update symlinked hooks file: $config"
  [[ ! -d "$config" ]] || fail "refusing to update directory as hooks file: $config"
}

preflight_state_files() {
  validate_state_dir
  preflight_state_file "$RUNTIME_FILE"
  preflight_install_lock
}

preflight_adapter() {
  case "$HARNESS" in
    codex) preflight_native_hooks ;;
  esac
}

preflight_off() {
  preflight_enable_flag
  validate_state_dir
  preflight_install_lock
}

# EX_TEMPFAIL, LOCK_BUSY in lib/locking.py: a session-log command exits with it when, and only
# when, another live command holds the install lock, so a caller can tell "try later" from a failure.
readonly INSTALL_LOCK_BUSY=75

fail_install_lock_busy() {
  printf '%s: another session-log command is already in progress for %s\n' "$SESSION_LOG_LOG_PREFIX" "$HARNESS" >&2
  exit "$INSTALL_LOCK_BUSY"
}

# Per-harness install lock on lib/locking.py: the
# mkdir-owner-reclaim protocol (owner pid + `ps -o lstart=` liveness, stale
# reclaim after 5s) applied to the per-harness lock path "$INSTALL_LOCK".
_install_lock_operation() {
  local mode="$1"
  SESSION_LOG_LOCK_PATH="$INSTALL_LOCK" \
    SESSION_LOG_LOCK_PID="$$" SESSION_LOG_LOCK_START="$INSTALL_LOCK_OWNER_START" \
    python3 "$SESSION_LOG_LIB/locking.py" "$mode"
}

acquire_install_lock() {
  try_acquire_install_lock || fail_install_lock_busy
}

# acquire_install_lock that returns 1 instead of failing while another live command holds the lock;
# any other failure to take it fails the command.
try_acquire_install_lock() {
  local status=0
  validate_state_dir
  ensure_safe_parent "$INSTALL_LOCK/owner"
  [[ ! -L "$INSTALL_LOCK" ]] ||
    fail "refusing symlinked install lock: $INSTALL_LOCK"
  [[ ! -e "$INSTALL_LOCK" || -d "$INSTALL_LOCK" ]] ||
    fail "refusing non-directory install lock: $INSTALL_LOCK"
  ensure_private_directory "$STATE_DIR"
  INSTALL_LOCK_OWNER_START="$(ps -p "$$" -o lstart= 2>/dev/null | sed 's/^ *//; s/[[:space:]]*$//')"
  [[ -n "$INSTALL_LOCK_OWNER_START" ]] ||
    fail "cannot determine install lock owner"
  _install_lock_operation acquire || status=$?
  ((status != INSTALL_LOCK_BUSY)) || return 1
  ((status == 0)) || fail "cannot acquire install lock: $INSTALL_LOCK"
}

release_install_lock() {
  _install_lock_operation release || true
}

clear_runtime_markers() {
  local candidate
  ensure_safe_parent "$RUNTIME_FILE"
  for candidate in "$RUNTIME_FILE" "$STATE_DIR"/runtime.*.json; do
    [[ -e "$candidate" || -L "$candidate" ]] || continue
    [[ ! -L "$candidate" ]] || fail "refusing to remove symlinked runtime state: $candidate"
    [[ ! -d "$candidate" ]] || fail "refusing to remove runtime directory: $candidate"
    unlink "$candidate"
  done
}

# Prints "<harness>: <line>" for each line of a repair report and records that one was printed.
print_repair_report() {
  local line
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    printf '%s: %s\n' "$HARNESS_LABEL" "$line"
    # shellcheck disable=SC2034  # read by bin/session-log, which sources this file
    REPAIR_REPORTED=1
  done <<< "$1"
}

repair_native_hooks() {
  local config="$HARNESS_ROOT/hooks.json" report
  [[ -e "$config" || -L "$config" ]] || return 0
  require_command python3
  preflight_native_hooks
  report="$(python3 "$(source_for configurator)" repair "$HARNESS" "$config")"
  print_repair_report "$report"
}

# Collapse duplicate and stale session-log registrations to the one set the harness
# needs: hooks.json entries (Codex). Without a codex-goodies plugin package the Codex entries
# stay: the command that replaces them would run nothing, while an older release's entry may still
# run its package.
# Callers hold the install lock.
repair_registrations() {
  case "$HARNESS" in
    codex)
      if codex_plugin_package_present; then repair_native_hooks; fi
      ;;
  esac
}

install_adapter() {
  case "$HARNESS" in
    codex)
      python3 "$(source_for configurator)" install "$HARNESS" "$HARNESS_ROOT/hooks.json"
      ;;
  esac
  clear_runtime_markers
}

# Like `on`, the legacy cleanup comes before the adapter preflight: it removes the standalone
# adapter copies of earlier releases that sit where the adapter goes. Without a codex-goodies plugin
# package nothing is installed for Codex, as in repair_registrations.
repair_enabled_adapter() {
  [[ -f "$FLAG_FILE" && ! -L "$FLAG_FILE" ]] || return 0
  [[ "$HARNESS" != codex ]] || codex_plugin_package_present || return 0
  adapter_current && return 0
  preflight_enable_flag
  preflight_state_files
  acquire_install_lock
  trap release_install_lock EXIT
  legacy_cleanup
  preflight_adapter
  install_adapter
  release_install_lock
  trap - EXIT
}
