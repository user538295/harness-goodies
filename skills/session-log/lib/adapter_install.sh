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

# Fails while another live command holds the install lock (busy) or the lock is unsafe. A lock whose
# owner file is missing or incomplete is left to lib/locking.py, which `on` takes the lock with too: its
# holder may still be creating it (the lock, then the owner file, then its content) or removing it (in
# reverse), so locking.py reports it busy while fresh and reclaims it once stale.
preflight_install_lock() {
  local owner_pid owner_start actual_start child
  ensure_safe_parent "$INSTALL_LOCK/owner"
  [[ ! -L "$INSTALL_LOCK" ]] ||
    fail "refusing symlinked install lock: $INSTALL_LOCK"
  [[ ! -e "$INSTALL_LOCK" ]] && return
  [[ -d "$INSTALL_LOCK" || ! -e "$INSTALL_LOCK" ]] ||
    fail "refusing non-directory install lock: $INSTALL_LOCK"
  for child in "$INSTALL_LOCK"/* "$INSTALL_LOCK"/.[!.]* "$INSTALL_LOCK"/..?*; do
    [[ -e "$child" || -L "$child" ]] || continue
    [[ "$child" == "$INSTALL_LOCK/owner" ]] ||
      fail "refusing install lock containing unexpected file: $child"
  done
  [[ -e "$INSTALL_LOCK/owner" ]] || return 0
  [[ ! -L "$INSTALL_LOCK/owner" && ( -f "$INSTALL_LOCK/owner" || ! -e "$INSTALL_LOCK/owner" ) ]] ||
    fail "refusing invalid install lock owner: $INSTALL_LOCK/owner"
  owner_pid="$(sed -n '1p' "$INSTALL_LOCK/owner" 2>/dev/null)" || [[ ! -e "$INSTALL_LOCK/owner" ]] ||
    fail "cannot read install lock owner: $INSTALL_LOCK/owner"
  owner_start="$(sed -n '2p' "$INSTALL_LOCK/owner" 2>/dev/null | sed 's/^ *//; s/[[:space:]]*$//')" || true
  [[ "$owner_pid" =~ ^[1-9][0-9]*$ && -n "$owner_start" ]] || return 0
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

preflight_off() {
  preflight_enable_flag
  validate_state_dir
  preflight_install_lock
}

# EX_TEMPFAIL, as in lib/locking.py and lib/claude_settings.py: a session-log command exits with it when,
# and only when, it stopped on something transient with nothing decided, so a caller can tell "try later"
# from a failure: another live command holds the install lock, or settings.json changed before the legacy
# cleanup moved any version of it aside (a change found later is a failure naming the version kept).
readonly TRANSIENT_FAILURE_EXIT_CODE=75

fail_install_lock_busy() {
  printf '%s: another session-log command is already in progress for %s\n' "$SESSION_LOG_LOG_PREFIX" "$HARNESS" >&2
  exit "$TRANSIENT_FAILURE_EXIT_CODE"
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
  ((status != TRANSIENT_FAILURE_EXIT_CODE)) || fail_install_lock_busy
  # locking.py has already said what failed.
  ((status == 0)) || exit 1
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
    # A hook may have removed it already: plugin-owned `off` takes no install lock.
    unlink "$candidate" 2>/dev/null || [[ ! -e "$candidate" && ! -L "$candidate" ]] ||
      fail "cannot remove runtime state: $candidate"
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

# Codex's hooks.json entries, the only adapter session-log installs.
install_adapter() {
  python3 "$(source_for configurator)" install "$HARNESS" "$HARNESS_ROOT/hooks.json"
  clear_runtime_markers
}

# Codex's `status` and `usage` reinstall its adapter while logging is on. Like `on`, the legacy
# cleanup comes before the adapter preflight: it removes the standalone adapter copies of earlier
# releases that sit where the adapter goes. Without a codex-goodies plugin package nothing is
# installed, as in repair_registrations.
repair_enabled_adapter() {
  [[ -f "$FLAG_FILE" && ! -L "$FLAG_FILE" ]] || return 0
  codex_plugin_package_present || return 0
  adapter_current && return 0
  preflight_enable_flag
  preflight_state_files
  acquire_install_lock
  trap release_install_lock EXIT
  legacy_cleanup
  preflight_native_hooks
  install_adapter
  release_install_lock
  trap - EXIT
}
