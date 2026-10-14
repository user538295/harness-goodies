# universal-session-log: managed
# Legacy cleanup for bin/session-log: removes, per harness, what older releases left behind.
#
# Sourced by bin/session-log after lib/adapter_install.sh, in the same shell, so it shares bin's
# globals (HARNESS, HARNESS_ROOT, STATE_DIR, HARNESS_LABEL, OWNER_MARKER_VALUE, ...). `on` and
# `repair` run it under the install lock; Claude Code's SessionStart hook runs it silently through
# `repair`. It removes only what is proven to be session-log's, prints one line per removal
# through print_repair_report, and never clears runtime markers: a harness plugin's own hooks
# keep logging whatever duplicate registration goes.

# Claude Code state files that older releases wrote and nothing reads any more: the adapter
# installer's version stamps and manifest, and the plugin-root marker standalone copies read.
# remove_claude_obsolete_state also removes their per-copy settings-repair.*.stamp files.
readonly CLAUDE_OBSOLETE_STATE_FILES=(adapter.version adapter.manifest desired.version plugin-root)

# The cleanup of the current harness. A harness whose plugin takes over its session-log hooks
# adds its own legacy_cleanup_<harness> here.
legacy_cleanup() {
  case "$HARNESS" in
    claude) legacy_cleanup_claude ;;
    codex|cursor|opencode|omp) ;;
  esac
}

legacy_cleanup_claude() {
  remove_claude_settings_registrations
  remove_claude_obsolete_state
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

# Older releases registered the session-log hooks in settings.json; the plugin's hooks/hooks.json
# registers them now, so every settings.json registration claude_settings.py recognizes goes.
remove_claude_settings_registrations() {
  local settings="$HARNESS_ROOT/settings.json" report
  [[ -e "$settings" || -L "$settings" ]] || return 0
  require_command python3
  preflight_claude_settings
  report="$(SETTINGS_PATH="$settings" SESSION_LOG_OWNER_MARKER="$OWNER_MARKER_VALUE" \
    python3 "$SESSION_LOG_LIB/claude_settings.py" remove)"
  print_repair_report "$report"
}

remove_claude_obsolete_state() {
  local candidate
  validate_state_dir
  for candidate in "${CLAUDE_OBSOLETE_STATE_FILES[@]/#/$STATE_DIR/}" "$STATE_DIR"/settings-repair.*.stamp; do
    [[ -f "$candidate" && ! -L "$candidate" ]] || continue
    unlink "$candidate"
  done
}
