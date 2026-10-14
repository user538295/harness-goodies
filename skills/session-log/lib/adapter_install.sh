# universal-session-log: managed
# Adapter-install machinery for bin/session-log.
#
# Sourced by bin/session-log right after lib/common.sh. Runs in the same shell,
# so it shares bin's globals (HARNESS, HARNESS_ROOT, STATE_DIR, INSTALL_LOCK,
# INSTALL_LOCK_OWNER_START, REPAIR_REPORTED, CLAUDE_SETTINGS_MODE, VERSION, STORE, HOME_ROOT,
# PACKAGE_ROOT, OWNER_MARKER_VALUE, ...) and the harness descriptor tables declared in bin.

adapter_asset_current() {
  local kind source target
  kind="$1"
  source="$(source_for "$kind")"
  target="$(target_for "$kind")"
  [[ -L "$target" ]] || return 1
  [[ "$(readlink "$target")" == "$source" ]]
}

native_hooks_current() {
  local config="$HARNESS_ROOT/hooks.json"
  require_command python3
  [[ -f "$config" && ! -L "$config" ]] || return 1
  python3 "$(source_for configurator)" check "$HARNESS" "$config" "$(source_for hook)"
}

# Sets CLAUDE_SETTINGS_MODE, how this copy treats settings.json session-log hooks (Claude
# Code), once per command: the first check that needs it resolves it (after the install lock
# where the command takes one), so a session start rewriting the plugin marker mid-command
# cannot change it. The plugin's hooks/hooks.json registers the hooks in both plugin modes:
#   plugin            settings.json keeps none (a plugin copy enabled at user scope, or a
#                     standalone copy deferring to that plugin);
#   plugin-dead-only  a plugin copy enabled only at project, local, or managed scope: a live
#                     standalone copy's hooks still serve other projects, so only dead
#                     registrations go and none is ever added;
#   standalone        settings.json holds exactly this copy's managed set.
resolve_claude_settings_mode() {
  [[ "$HARNESS" == claude && -z "$CLAUDE_SETTINGS_MODE" ]] || return 0
  if claude_plugin_package "$PACKAGE_ROOT"; then
    if claude_plugin_user_enabled "$PACKAGE_ROOT"; then
      CLAUDE_SETTINGS_MODE=plugin
    else
      CLAUDE_SETTINGS_MODE=plugin-dead-only
    fi
  elif claude_plugin_active; then
    CLAUDE_SETTINGS_MODE=plugin
  else
    CLAUDE_SETTINGS_MODE=standalone
  fi
}

# True when this Claude copy registers no settings.json hooks of its own (either plugin mode);
# CLAUDE_SETTINGS_MODE says which registrations it removes.
claude_registers_no_settings_hooks() {
  [[ "$HARNESS" == claude ]] || return 1
  resolve_claude_settings_mode
  [[ "$CLAUDE_SETTINGS_MODE" != standalone ]]
}

# True when user-scope enabledPlugins sets the claude-goodies plugin of plugin-cache
# package root $1 to true; unreadable settings count as not enabled.
claude_plugin_user_enabled() {
  local marketplace="${1#"$HOME_ROOT/.claude/plugins/cache/"}"
  SETTINGS_PATH="$HARNESS_ROOT/settings.json" PLUGIN_KEY="claude-goodies@${marketplace%%/*}" \
    python3 "$SESSION_LOG_LIB/claude_settings.py" plugin-enabled
}

# Prints the plugin-cache package root the plugin's own SessionStart hook recorded, provided
# Claude Code has not orphaned (replaced or uninstalled) that version directory.
recorded_plugin_root() {
  local marker="$STATE_DIR/$PLUGIN_ROOT_MARKER_NAME" root plugin_root orphaned
  [[ -f "$marker" && ! -L "$marker" ]] || return 1
  root="$(cat "$marker")"
  case "$root" in */../*|*/./*) return 1 ;; esac
  claude_plugin_package_path "$root" || return 1
  plugin_root="${root%/skills/session-log}"
  orphaned="$plugin_root/.orphaned_at"
  [[ -d "$plugin_root" && ! -e "$orphaned" && ! -L "$orphaned" ]] || return 1
  printf '%s\n' "$root"
}

# Standalone copy: the plugin's hooks are active when its recorded package is live and
# enabled at user scope. Limit: a plugin enabled only at project, local, or managed scope
# does not count, so this copy keeps its own settings.json hooks.
claude_plugin_active() {
  local root
  root="$(recorded_plugin_root)" || return 1
  claude_plugin_user_enabled "$root"
}

# Plugin modes: the plugin's hooks/hooks.json is the adapter, so it is current only
# while settings.json holds no registration that mode removes.
claude_settings_has_adapter() {
  if claude_registers_no_settings_hooks; then
    ! SETTINGS_PATH="$HARNESS_ROOT/settings.json" SESSION_LOG_OWNER_MARKER="$OWNER_MARKER_VALUE" \
      python3 "$SESSION_LOG_LIB/claude_settings.py" has-removable "$CLAUDE_SETTINGS_MODE"
    return
  fi
  [[ -f "$HARNESS_ROOT/settings.json" ]] || return 1
  [[ ! -L "$HARNESS_ROOT/settings.json" ]] ||
    fail "refusing to follow symlinked Claude settings: $HARNESS_ROOT/settings.json"
  SETTINGS_PATH="$HARNESS_ROOT/settings.json" \
  HOOK_PATH="$(source_for hook)" \
  SESSION_LOG_OWNER_MARKER="$OWNER_MARKER_VALUE" \
    python3 - <<'PY'
import json
import os
import shlex

try:
    with open(os.environ["SETTINGS_PATH"], encoding="utf-8") as handle:
        document = json.load(handle)
except (OSError, json.JSONDecodeError):
    raise SystemExit(1)
hooks = document.get("hooks")
if not isinstance(hooks, dict):
    raise SystemExit(1)
owner = os.environ["SESSION_LOG_OWNER_MARKER"]
hook_path = os.environ["HOOK_PATH"]
events = {
    "SessionStart": ("startup|resume", "session-start"),
    "UserPromptSubmit": (None, "user-prompt"),
    "Stop": (None, "stop"),
    "SubagentStop": (None, "subagent-stop"),
}
for event, (matcher, name) in events.items():
    expected = (
        f"SESSION_LOG_OWNER={shlex.quote(owner)} "
        f"bash {shlex.quote(hook_path)} {name}"
    )
    entries = hooks.get(event)
    if not isinstance(entries, list):
        raise SystemExit(1)
    if not any(
        isinstance(entry, dict)
        and (
            (matcher is None and "matcher" not in entry)
            or entry.get("matcher") == matcher
        )
        and isinstance(entry.get("hooks"), list)
        and any(
            isinstance(item, dict)
            and item.get("type") == "command"
            and item.get("command") == expected
            for item in entry["hooks"]
        )
        for entry in entries
    ):
        raise SystemExit(1)
PY
}

manifest_asset_hash() {
  local asset="$1"
  awk -v wanted="asset=$asset" '
    $0 == wanted { if (getline && $0 ~ /^sha256=/) { sub(/^sha256=/, ""); print; exit } }
  ' "$STATE_DIR/adapter.manifest" 2>/dev/null
}

manifest_current() {
  local kind source expected version
  [[ -f "$STATE_DIR/adapter.manifest" && ! -L "$STATE_DIR/adapter.manifest" ]] || return 1
  version="$(awk -F= '
    $1 == "version" { value=$2; count++ }
    END { if (count != 1) exit 1; print value }
  ' "$STATE_DIR/adapter.manifest")" || return 1
  [[ "$version" == "$VERSION" ]] || return 1
  for kind in ${HARNESS_KINDS[$HARNESS]}; do
    source="$(source_for "$kind")"
    expected="$(sha256_file "$source")"
    [[ "$(manifest_asset_hash "$kind")" == "$expected" ]] || return 1
  done
}

adapter_current() {
  validate_state_dir
  [[ -f "$STATE_DIR/desired.version" && ! -L "$STATE_DIR/desired.version" ]] || return 1
  [[ "$(cat "$STATE_DIR/desired.version")" == "$VERSION" ]] || return 1
  [[ -f "$STATE_DIR/adapter.version" && ! -L "$STATE_DIR/adapter.version" ]] || return 1
  [[ "$(cat "$STATE_DIR/adapter.version")" == "$VERSION" ]] || return 1
  manifest_current || return 1
  case "$HARNESS" in
    claude) claude_settings_has_adapter ;;
    codex|cursor) native_hooks_current ;;
    opencode) adapter_asset_current plugin && adapter_asset_current usage ;;
    omp) adapter_asset_current extension && adapter_asset_current usage ;;
  esac
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
      fail "adapter installation is already in progress for $HARNESS"
    fi
  fi
  return 0
}

# True when "harness:kind:linktarget" ($1) points at a claude-goodies plugin-cache adapter.
is_plugin_cache_adapter_link() {
  case "$1" in
    opencode:plugin:"$HOME_ROOT/.claude/plugins/cache/"*/claude-goodies/*/skills/session-log/adapters/opencode/session-log.js|\
    opencode:usage:"$HOME_ROOT/.claude/plugins/cache/"*/claude-goodies/*/skills/session-log/adapters/opencode/session_log_usage.sh|\
    omp:extension:"$HOME_ROOT/.claude/plugins/cache/"*/claude-goodies/*/skills/session-log/adapters/omp/session-log.js|\
    omp:usage:"$HOME_ROOT/.claude/plugins/cache/"*/claude-goodies/*/skills/session-log/adapters/omp/session_log_usage.ts|\
    opencode:plugin:"$HOME/.claude/plugins/cache/"*/claude-goodies/*/skills/session-log/adapters/opencode/session-log.js|\
    opencode:usage:"$HOME/.claude/plugins/cache/"*/claude-goodies/*/skills/session-log/adapters/opencode/session_log_usage.sh|\
    omp:extension:"$HOME/.claude/plugins/cache/"*/claude-goodies/*/skills/session-log/adapters/omp/session-log.js|\
    omp:usage:"$HOME/.claude/plugins/cache/"*/claude-goodies/*/skills/session-log/adapters/omp/session_log_usage.ts)
      return 0 ;;
    *) return 1 ;;
  esac
}

preflight_adapter_target() {
  local kind="$1" source target existing
  source="$(source_for "$kind")"
  target="$(target_for "$kind")"
  [[ -f "$source" && ! -L "$source" ]] ||
    fail "adapter asset is missing: $source"
  ensure_safe_parent "$target"
  if [[ -L "$target" ]]; then
    existing="$(readlink "$target")"
    if [[ ! -e "$target" ]] && is_plugin_cache_adapter_link "$HARNESS:$kind:$existing"; then
      :
    else
      case "$HARNESS:$kind:$existing" in
        "opencode:plugin:$source"|\
        opencode:plugin:"$STORE/releases/"*/adapters/opencode/session-log.js|\
        "opencode:usage:$source"|\
        opencode:usage:"$STORE/releases/"*/adapters/opencode/session_log_usage.sh|\
        "omp:extension:$source"|\
        omp:extension:"$STORE/releases/"*/adapters/omp/session-log.js|\
        "omp:usage:$source"|\
        omp:usage:"$STORE/releases/"*/adapters/omp/session_log_usage.ts) ;;
        *) fail "refusing to overwrite unowned adapter link: $target" ;;
      esac
    fi
  elif [[ -e "$target" ]]; then
    [[ -f "$target" && ! -L "$target" ]] ||
      fail "refusing to overwrite non-file adapter target: $target"
    cmp -s "$source" "$target" ||
      fail "refusing to overwrite unowned adapter file: $target"
  fi
}

preflight_adapter_targets() {
  local kind
  for kind in ${HARNESS_KINDS[$HARNESS]}; do
    preflight_adapter_target "$kind"
  done
}

preflight_native_hooks() {
  local config="$HARNESS_ROOT/hooks.json"
  [[ -f "$(source_for hook)" && ! -L "$(source_for hook)" ]] ||
    fail "adapter asset is missing: $(source_for hook)"
  [[ -f "$(source_for configurator)" && ! -L "$(source_for configurator)" ]] ||
    fail "adapter asset is missing: $(source_for configurator)"
  ensure_safe_parent "$config"
  [[ ! -L "$config" ]] || fail "refusing to update symlinked hooks file: $config"
  [[ ! -d "$config" ]] || fail "refusing to update directory as hooks file: $config"
}

preflight_claude_settings() {
  local settings="$HARNESS_ROOT/settings.json"
  ensure_safe_parent "$settings"
  [[ ! -L "$settings" ]] ||
    fail "refusing to update symlinked Claude settings: $settings"
  [[ ! -d "$settings" ]] ||
    fail "refusing to update directory as Claude settings: $settings"
  [[ ! -e "$settings" ]] && return
  [[ -f "$settings" ]] ||
    fail "refusing to update non-file Claude settings: $settings"
  local error
  error="$(SETTINGS_PATH="$settings" python3 - 2>&1 <<'PY'
import json
import os

try:
    with open(os.environ["SETTINGS_PATH"], encoding="utf-8") as handle:
        document = json.load(handle)
except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
    raise SystemExit(f"cannot read Claude settings {os.environ['SETTINGS_PATH']}: {error}")
if not isinstance(document, dict):
    raise SystemExit("Claude settings root must be an object")
hooks = document.get("hooks")
if hooks is not None and not isinstance(hooks, dict):
    raise SystemExit("Claude settings hooks must be an object")
if isinstance(hooks, dict):
    for event in ("SessionStart", "UserPromptSubmit", "Stop", "SubagentStop"):
        entries = hooks.get(event)
        if entries is not None and not isinstance(entries, list):
            raise SystemExit(f"Claude settings hooks.{event} must be an array")
PY
)" || fail "$error"
}

preflight_state_files() {
  validate_state_dir
  preflight_state_file "$STATE_DIR/desired.version"
  preflight_state_file "$STATE_DIR/adapter.version"
  preflight_state_file "$STATE_DIR/adapter.manifest"
  preflight_state_file "$RUNTIME_FILE"
  preflight_install_lock
}

preflight_on() {
  preflight_enable_flag
  preflight_state_files
  case "$HARNESS" in
    claude) preflight_claude_settings ;;
    codex|cursor) preflight_native_hooks ;;
    opencode|omp) preflight_adapter_targets ;;
  esac
}

preflight_off() {
  preflight_enable_flag
  validate_state_dir
  preflight_install_lock
}

write_manifest() {
  local content kind source target
  ensure_safe_parent "$STATE_DIR/adapter.manifest"
  [[ ! -L "$STATE_DIR/adapter.manifest" ]] ||
    fail "refusing to overwrite symlink: $STATE_DIR/adapter.manifest"
  [[ ! -d "$STATE_DIR/adapter.manifest" ]] ||
    fail "refusing to overwrite directory: $STATE_DIR/adapter.manifest"
  content="$(
    printf 'version=%s\n' "$VERSION"
    for kind in ${HARNESS_KINDS[$HARNESS]}; do
      printf 'asset=%s\n' "$kind"
      printf 'sha256=%s\n' "$(sha256_file "$(source_for "$kind")")"
      if [[ -n "${ADAPTER_TARGETS[$HARNESS:$kind]:-}" ]]; then
        printf 'target=%s\n' "$(target_for "$kind")"
      fi
    done
  )"
  write_private_file "$STATE_DIR/adapter.manifest" "$content"
}

# Per-harness install lock, unified with install.sh on lib/locking.py: the same
# mkdir-owner-reclaim protocol (owner pid + `ps -o lstart=` liveness, stale
# reclaim after 5s) applied to the per-harness lock path "$INSTALL_LOCK".
_install_lock_operation() {
  local mode="$1"
  SESSION_LOG_LOCK_PATH="$INSTALL_LOCK" \
    SESSION_LOG_LOCK_PID="$$" SESSION_LOG_LOCK_START="$INSTALL_LOCK_OWNER_START" \
    python3 "$SESSION_LOG_LIB/locking.py" "$mode"
}

acquire_install_lock() {
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
  _install_lock_operation acquire ||
    fail "adapter installation is already in progress for $HARNESS"
}

release_install_lock() {
  _install_lock_operation release || true
}

atomic_link() {
  local source="$1" target="$2"
  [[ -f "$source" && ! -L "$source" ]] || fail "adapter asset is missing: $source"
  ensure_safe_parent "$target"
  SESSION_LOG_SOURCE="$source" SESSION_LOG_TARGET="$target" SESSION_LOG_STORE="$STORE" \
    python3 "$SESSION_LOG_LIB/pathsafe.py" link-adapter ||
    fail "cannot safely install adapter link: $target"
}
install_claude_settings() {
  require_command python3
  local settings="$HARNESS_ROOT/settings.json"
  local hook
  hook="$(source_for hook)"
  # Never point settings.json at a versioned plugin-cache copy, which Claude Code deletes later.
  ! claude_plugin_package_path "$PACKAGE_ROOT" ||
    fail "refusing to register a versioned plugin-cache copy in Claude settings: $hook"
  ensure_safe_parent "$settings"
  [[ ! -L "$settings" ]] || fail "refusing to update symlinked Claude settings: $settings"
  [[ ! -d "$settings" ]] || fail "refusing to update directory as Claude settings: $settings"
  SETTINGS_PATH="$settings" HOOK_PATH="$hook" SESSION_LOG_OWNER_MARKER="$OWNER_MARKER_VALUE" \
    python3 "$SESSION_LOG_LIB/claude_settings.py" install
}

# Plugin modes: drop the settings.json hooks CLAUDE_SETTINGS_MODE makes removable; the
# plugin registers its own. Removing any of them clears the runtime markers, as
# repair_claude_settings does: Claude Code's file watcher normally applies settings.json hook
# edits to running sessions, so a session logging through a removed registration stops logging.
# Dead registrations clear them too (conservatively). The removal report is not printed.
remove_claude_settings_hooks() {
  require_command python3
  local settings="$HARNESS_ROOT/settings.json" removed
  ensure_safe_parent "$settings"
  [[ ! -L "$settings" ]] || fail "refusing to update symlinked Claude settings: $settings"
  [[ ! -d "$settings" ]] || fail "refusing to update directory as Claude settings: $settings"
  removed="$(SETTINGS_PATH="$settings" SESSION_LOG_OWNER_MARKER="$OWNER_MARKER_VALUE" \
    python3 "$SESSION_LOG_LIB/claude_settings.py" remove "$CLAUDE_SETTINGS_MODE")"
  if [[ -n "$removed" ]]; then clear_runtime_markers; fi
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

remove_recognized_dangling_adapter_link() {
  local kind="$1" target destination
  target="$(target_for "$kind")"
  [[ -L "$target" && ! -e "$target" ]] || return 0
  destination="$(readlink "$target")"
  if is_plugin_cache_adapter_link "$HARNESS:$kind:$destination"; then
    unlink "$target"
  fi
}

repair_dangling_adapter_link() {
  local kind="$1" target
  target="$(target_for "$kind")"
  [[ -L "$target" ]] || return 0
  remove_recognized_dangling_adapter_link "$kind"
  [[ ! -L "$target" ]] || return 0
  printf '%s: removed dangling adapter link %s\n' "$HARNESS_LABEL" "$target"
  REPAIR_REPORTED=1
}

# Prints "<harness>: <line>" for each line of a repair report and records that one was printed.
print_repair_report() {
  local line
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    printf '%s: %s\n' "$HARNESS_LABEL" "$line"
    REPAIR_REPORTED=1
  done <<< "$1"
}

repair_claude_settings() {
  local settings="$HARNESS_ROOT/settings.json" hook="" report
  [[ -e "$settings" || -L "$settings" ]] || return 0
  require_command python3
  preflight_claude_settings
  if ! claude_registers_no_settings_hooks; then
    if claude_plugin_package_path "$PACKAGE_ROOT"; then
      # Never point settings.json at a versioned plugin-cache copy.
      printf '%s: repair skipped: this plugin copy'"'"'s hooks/hooks.json does not register the session-log hooks\n' "$HARNESS_LABEL"
      REPAIR_REPORTED=1
      return 0
    fi
    hook="$(source_for hook)"
    [[ -f "$hook" && ! -L "$hook" ]] || fail "adapter asset is missing: $hook"
  fi
  report="$(SETTINGS_PATH="$settings" HOOK_PATH="$hook" SESSION_LOG_OWNER_MARKER="$OWNER_MARKER_VALUE" \
    python3 "$SESSION_LOG_LIB/claude_settings.py" repair "$CLAUDE_SETTINGS_MODE")"
  print_repair_report "$report"
  # Plugin modes only remove, so any report means a registration was removed; see
  # remove_claude_settings_hooks for why that clears the runtime markers.
  if claude_registers_no_settings_hooks && [[ -n "$report" ]]; then clear_runtime_markers; fi
}

repair_native_hooks() {
  local config="$HARNESS_ROOT/hooks.json" report
  [[ -e "$config" || -L "$config" ]] || return 0
  require_command python3
  preflight_native_hooks
  report="$(python3 "$(source_for configurator)" repair "$HARNESS" "$config" "$(source_for hook)")"
  print_repair_report "$report"
}

# Collapse duplicate and stale session-log registrations to the one set the harness
# needs: settings.json hooks (Claude Code; none while the plugin owns the hooks, only dead
# ones removed from a plugin copy not enabled at user scope),
# hooks.json entries (Codex, Cursor), dangling plugin-cache adapter links (OpenCode,
# OMP; install.sh removes their legacy session-log-omp adapters before every command).
# Removing a Claude settings.json registration in a plugin mode clears the runtime markers.
# Callers hold the install lock.
repair_registrations() {
  case "$HARNESS" in
    claude) repair_claude_settings ;;
    codex|cursor) repair_native_hooks ;;
    opencode)
      repair_dangling_adapter_link plugin
      repair_dangling_adapter_link usage
      ;;
    omp)
      repair_dangling_adapter_link extension
      repair_dangling_adapter_link usage
      ;;
  esac
}

# Clears the runtime markers, except in Claude plugin modes, where remove_claude_settings_hooks
# clears them only when it removes a registration.
install_adapter() {
  local markers_stale=1
  case "$HARNESS" in
    claude)
      if claude_registers_no_settings_hooks; then
        markers_stale=0
        remove_claude_settings_hooks
      else
        install_claude_settings
      fi
      ;;
    codex|cursor)
      python3 "$(source_for configurator)" install "$HARNESS" \
        "$HARNESS_ROOT/hooks.json" "$(source_for hook)"
      ;;
    opencode)
      remove_recognized_dangling_adapter_link plugin
      remove_recognized_dangling_adapter_link usage
      atomic_link "$(source_for plugin)" "$(target_for plugin)"
      atomic_link "$(source_for usage)" "$(target_for usage)"
      ;;
    omp)
      remove_recognized_dangling_adapter_link extension
      remove_recognized_dangling_adapter_link usage
      atomic_link "$(source_for extension)" "$(target_for extension)"
      atomic_link "$(source_for usage)" "$(target_for usage)"
      ;;
  esac
  write_private_file "$STATE_DIR/desired.version" "$VERSION"
  write_private_file "$STATE_DIR/adapter.version" "$VERSION"
  write_manifest
  if ((markers_stale)); then clear_runtime_markers; fi
}

repair_enabled_adapter() {
  [[ -f "$FLAG_FILE" && ! -L "$FLAG_FILE" ]] || return 0
  adapter_current && return 0
  preflight_on
  acquire_install_lock
  trap release_install_lock EXIT
  install_adapter
  release_install_lock
  trap - EXIT
}
