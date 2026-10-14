# universal-session-log: managed
# Legacy cleanup for bin/session-log: removes, per harness, what older releases left behind.
#
# Sourced by bin/session-log after lib/adapter_install.sh, in the same shell, so it shares bin's
# globals (HARNESS, HARNESS_ROOT, STATE_DIR, HARNESS_LABEL, OWNER_MARKER_VALUE, ...). `on` and
# `repair` run it under the install lock; Claude Code's SessionStart hook runs it silently through
# `repair`; outside Claude Code, `status` and `usage` run it too when they repair an enabled adapter,
# so an unowned file at a legacy path fails them, as the old installer did before every command.
# It removes only what is proven to be session-log's and never clears runtime markers: a harness
# plugin's own hooks keep logging whatever duplicate registration goes. Claude Code removals of
# registrations, scripts, and package copies are reported through print_repair_report; obsolete
# state files and the OpenCode/OMP leftovers of pre-universal releases go silently.

readonly OWNERSHIP_MARKER="universal-session-log: managed"
# The legacy Claude Code scripts the pre-plugin install copied into ~/.claude/scripts.
readonly CLAUDE_LEGACY_SCRIPTS=(prompt_log_save.sh prompt_log_new_session.sh prompt_log_stop.sh prompt_log_subagent.sh
  prompt_log_lib.sh prompt_log_usage.sh prompt_log_usage.jq prompt_log_prices.json)
# Claude Code state files that older releases wrote and nothing reads any more: the adapter
# installer's version stamps and manifest, and the plugin-root marker standalone copies read.
# remove_claude_obsolete_state also removes their per-copy settings-repair.*.stamp files, but keeps
# plugin-root, pointed at the running plugin package, while a seeded copy the cleanup had to keep
# still reads it.
readonly CLAUDE_OBSOLETE_STATE_FILES=(adapter.version adapter.manifest desired.version plugin-root)

# The cleanup of the current harness: any harness whose older releases left something behind has
# its own legacy_cleanup_<harness> here, whether or not its plugin owns its hooks yet.
legacy_cleanup() {
  case "$HARNESS" in
    claude) legacy_cleanup_claude ;;
    codex) ;;
    cursor) legacy_cleanup_cursor ;;
    opencode) legacy_cleanup_opencode ;;
    omp) legacy_cleanup_omp ;;
  esac
}

# Older releases registered the session-log hooks in ~/.cursor/hooks.json, pointing at the package
# copy they seeded into ~/.cursor/skills; the cursor-goodies plugin's .cursor-plugin/hooks.json
# registers them now, and Cursor runs both, so every entry install_hooks.py recognizes goes. Every
# other entry stays. An unsafe or invalid hooks.json fails the cleanup with nothing changed.
legacy_cleanup_cursor() {
  local config="$HARNESS_ROOT/hooks.json" report
  require_command python3
  if [[ -e "$config" || -L "$config" ]]; then
    preflight_native_hooks
    report="$(python3 "$(source_for configurator)" remove "$HARNESS" "$config" "$(source_for hook)")"
    print_repair_report "$report"
  fi
  remove_seeded_package "$HARNESS_ROOT/skills/session-log"
}

# Unsafe settings.json fails the command with nothing changed. An unrecognized legacy script keeps
# the legacy scripts and their prompt_log hooks but not the rest of the cleanup, which then fails
# naming that script.
legacy_cleanup_claude() {
  local settings="$HARNESS_ROOT/settings.json" report unrecognized
  require_command python3
  preflight_claude_settings
  unrecognized="$(unrecognized_claude_legacy_script)" || unrecognized=""
  if [[ -z "$unrecognized" && -f "$settings" ]]; then
    report="$(SETTINGS_PATH="$settings" CLAUDE_SCRIPTS_DIR="$HARNESS_ROOT/scripts" \
      python3 "$SESSION_LOG_LIB/claude_settings.py" migrate)"
    print_repair_report "$report"
  fi
  remove_claude_settings_registrations
  [[ -n "$unrecognized" ]] || remove_claude_legacy_scripts
  remove_seeded_package "$HARNESS_ROOT/skills/session-log"
  remove_claude_obsolete_state
  [[ -z "$unrecognized" ]] || fail "refusing to remove unrecognized legacy Claude script: $unrecognized"
}

# The standalone adapter copies (regular files) and the session-log-omp entrypoints of earlier
# releases. A symlink at a standalone copy's path is the current adapter link, which the adapter
# install and repair_registrations manage, so this cleanup leaves it alone.
legacy_cleanup_opencode() {
  local plugin="$HARNESS_ROOT/plugins/session-log.js" usage="$HARNESS_ROOT/scripts/session_log_usage.sh"
  [[ -L "$plugin" ]] || validate_managed_path "$plugin"
  [[ -L "$usage" ]] || validate_managed_path "$usage"
  validate_managed_path "$HARNESS_ROOT/commands/session-log-omp.md"
  validate_managed_path "$HARNESS_ROOT/plugins/session-log-omp.js"
  [[ -L "$plugin" ]] || remove_managed_path "$plugin"
  [[ -L "$usage" ]] || remove_managed_path "$usage"
  remove_managed_path "$HARNESS_ROOT/commands/session-log-omp.md"
  remove_managed_path "$HARNESS_ROOT/plugins/session-log-omp.js"
}

# The standalone extension copy and the session-log-omp skill and extension of earlier releases.
legacy_cleanup_omp() {
  local extension="$HARNESS_ROOT/extensions/session-log.js"
  [[ -L "$extension" ]] || validate_managed_path "$extension"
  validate_managed_path "$HARNESS_ROOT/skills/session-log-omp" "$HARNESS_ROOT/skills/session-log-omp/SKILL.md"
  validate_managed_path "$HARNESS_ROOT/extensions/session-log-omp.js"
  [[ -L "$extension" ]] || remove_managed_path "$extension"
  remove_managed_path "$HARNESS_ROOT/skills/session-log-omp" "$HARNESS_ROOT/skills/session-log-omp/SKILL.md"
  remove_managed_path "$HARNESS_ROOT/extensions/session-log-omp.js"
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
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            raise SystemExit(f"Claude settings hooks.{event} must be an array")
PY
)" || fail "$error"
}

# Older releases registered the session-log hooks in settings.json; the plugin's hooks/hooks.json
# registers them now, so every settings.json registration claude_settings.py recognizes goes.
# Callers run preflight_claude_settings first.
remove_claude_settings_registrations() {
  local settings="$HARNESS_ROOT/settings.json" report
  [[ -e "$settings" ]] || return 0
  report="$(SETTINGS_PATH="$settings" SESSION_LOG_OWNER_MARKER="$OWNER_MARKER_VALUE" \
    python3 "$SESSION_LOG_LIB/claude_settings.py" remove)"
  print_repair_report "$report"
}

# The package copy older install.sh releases seeded into a harness skill root ($1), or the symlink
# they put there. lib/legacy_package.py removes it only when proven session-log's, and reports a
# copy that carries session-log's ownership marks but holds a changed or foreign file.
remove_seeded_package() {
  local target="$1" result
  result="$(python3 "$SESSION_LOG_LIB/legacy_package.py" remove "$target")"
  case "$result" in
    removed) print_repair_report "removed legacy package copy $target" ;;
    "removed link") print_repair_report "removed legacy package link $target" ;;
    kept:*) print_repair_report "kept legacy package copy $target:${result#kept:}; move it aside" ;;
  esac
}

remove_claude_obsolete_state() {
  local candidate seeded="$HARNESS_ROOT/skills/session-log"
  validate_state_dir
  # A kept seeded copy runs older code (package VERSION 2.0.0, shipped with plugin 1.19.0) that
  # registers its own settings.json hooks again unless plugin-root names a live plugin package, and
  # Claude Code orphans the version that wrote it at the next plugin update.
  if [[ -e "$seeded" || -L "$seeded" ]]; then
    record_running_plugin_root
  fi
  for candidate in "${CLAUDE_OBSOLETE_STATE_FILES[@]/#/$STATE_DIR/}" "$STATE_DIR"/settings-repair.*.stamp; do
    [[ -f "$candidate" && ! -L "$candidate" ]] || continue
    if [[ "$candidate" == "$STATE_DIR/plugin-root" ]] && [[ -e "$seeded" || -L "$seeded" ]]; then
      continue
    fi
    # A concurrent cleanup may have removed it already.
    unlink "$candidate" 2>/dev/null || [[ ! -e "$candidate" ]] ||
      fail "cannot remove obsolete state file: $candidate"
  done
}

# Points plugin-root at this package when it is a claude-goodies plugin-cache package; any other
# package leaves the marker as it is.
record_running_plugin_root() {
  local marker="$STATE_DIR/plugin-root"
  case "$PACKAGE_ROOT" in
    "$HOME_ROOT/.claude/plugins/cache/"*/claude-goodies/*/skills/session-log) ;;
    *) return 0 ;;
  esac
  [[ ! -L "$marker" && ! -d "$marker" ]] || return 0
  [[ -f "$marker" && "$(< "$marker")" == "$PACKAGE_ROOT" ]] && return 0
  write_private_file "$marker" "$PACKAGE_ROOT"
}

has_exact_ownership_marker() {
  local file="$1"
  [[ -f "$file" && ! -L "$file" ]] || return 1
  awk -v marker="$OWNERSHIP_MARKER" '
    function is_marker(line) {
      return line == marker ||
        line == "# " marker ||
        line == "// " marker ||
        line == "<!-- " marker " -->"
    }
    NR == 1 {
      first = $0
      if (is_marker($0)) found = 1
      if ($0 == "---") in_frontmatter = 1
      next
    }
    NR == 2 && first ~ /^#!/ && is_marker($0) { found = 1 }
    in_frontmatter {
      if ($0 == "---") {
        in_frontmatter = 0
        marker_after_frontmatter = 1
      }
      next
    }
    marker_after_frontmatter {
      if (is_marker($0)) found = 1
      marker_after_frontmatter = 0
    }
    END { exit(found ? 0 : 1) }
  ' "$file"
}

remove_checked_path() {
  local path="$1"
  [[ -n "$path" ]] || return 0
  ensure_safe_parent "$path"
  if [[ -d "$path" && ! -L "$path" ]]; then
    safe_remove_path "$path" || fail "refusing to recursively remove non-empty directory: $path"
  elif [[ -e "$path" || -L "$path" ]]; then
    safe_remove_path "$path" || fail "refusing to remove path: $path"
  fi
}
remove_known_path() {
  local path="$1" name
  [[ -e "$path" || -L "$path" ]] || return 0
  if [[ -L "$path" ]]; then
    validate_managed_path "$path"
  elif [[ -f "$path" ]]; then
    name="${path##*/}"
    if ! has_exact_ownership_marker "$path"; then
      legacy_script_is_tracked "$name" "$path" ||
        fail "refusing to remove changed managed path: $path"
    fi
  else
    fail "refusing to remove changed managed path: $path"
  fi
  remove_checked_path "$path"
}
legacy_omp_script_is_managed() {
  local child="$1"
  [[ "$child" == "$HOME_ROOT/.omp/agent/skills/session-log-omp/scripts/session_log_usage.ts" ]] &&
    has_exact_ownership_marker "$child"
}

validate_managed_path() {
  local path="$1" marker_path="${2:-$1}" target child scripts_dir=""
  [[ -e "$path" || -L "$path" ]] || return 0
  ensure_safe_parent "$path"
  if [[ -L "$path" ]]; then
    target="$(readlink "$path")"
    case "$path:$target" in
      "$HOME_ROOT/.config/opencode/plugins/session-log.js:$PACKAGE_ROOT/adapters/opencode/session-log.js"|\
      "$HOME_ROOT/.config/opencode/plugins/session-log.js:$HOME_ROOT/.config/opencode/skills/session-log/adapters/opencode/session-log.js"|\
      "$HOME_ROOT/.config/opencode/plugins/session-log.js:$STORE/releases/"*/adapters/opencode/session-log.js|\
      "$HOME_ROOT/.config/opencode/plugins/session-log.js:$HOME_ROOT/.claude/plugins/cache/"*/claude-goodies/*/skills/session-log/adapters/opencode/session-log.js|\
      "$HOME_ROOT/.config/opencode/plugins/session-log.js:$HOME/.claude/plugins/cache/"*/claude-goodies/*/skills/session-log/adapters/opencode/session-log.js|\
      "$HOME_ROOT/.config/opencode/plugins/session-log-omp.js:$PACKAGE_ROOT/adapters/omp/session-log.js"|\
      "$HOME_ROOT/.config/opencode/plugins/session-log-omp.js:$HOME_ROOT/.omp/agent/skills/session-log/adapters/omp/session-log.js"|\
      "$HOME_ROOT/.config/opencode/plugins/session-log-omp.js:$STORE/releases/"*/adapters/omp/session-log.js|\
      "$HOME_ROOT/.config/opencode/scripts/session_log_usage.sh:$PACKAGE_ROOT/adapters/opencode/session_log_usage.sh"|\
      "$HOME_ROOT/.config/opencode/scripts/session_log_usage.sh:$HOME_ROOT/.config/opencode/skills/session-log/adapters/opencode/session_log_usage.sh"|\
      "$HOME_ROOT/.config/opencode/scripts/session_log_usage.sh:$STORE/releases/"*/adapters/opencode/session_log_usage.sh|\
      "$HOME_ROOT/.omp/agent/extensions/session-log.js:$PACKAGE_ROOT/adapters/omp/session-log.js"|\
      "$HOME_ROOT/.omp/agent/extensions/session-log.js:$HOME_ROOT/.omp/agent/skills/session-log/adapters/omp/session-log.js"|\
      "$HOME_ROOT/.omp/agent/extensions/session-log.js:$STORE/releases/"*/adapters/omp/session-log.js|\
      "$HOME_ROOT/.omp/agent/extensions/session-log-omp.js:$PACKAGE_ROOT/adapters/omp/session-log.js"|\
      "$HOME_ROOT/.omp/agent/extensions/session-log-omp.js:$HOME_ROOT/.omp/agent/skills/session-log/adapters/omp/session-log.js"|\
      "$HOME_ROOT/.omp/agent/extensions/session-log-omp.js:$STORE/releases/"*/adapters/omp/session-log.js)
        return 0
        ;;
    esac
    fail "refusing to remove unowned legacy path: $path"
  fi
  [[ ! -d "$path" ]] ||
    { [[ "$marker_path" != "$path" && -f "$marker_path" ]] ||
      fail "refusing to remove unowned legacy path: $path"; }
  has_exact_ownership_marker "$marker_path" ||
    fail "refusing to remove unowned legacy path: $path"
  if [[ -d "$path" && ! -L "$path" ]]; then
    if [[ "$path" == "$HOME_ROOT/.omp/agent/skills/session-log-omp" &&
          -d "$path/scripts" && ! -L "$path/scripts" ]]; then
      scripts_dir="$path/scripts"
      for child in "$scripts_dir"/* "$scripts_dir"/.[!.]* "$scripts_dir"/..?*; do
        [[ -e "$child" || -L "$child" ]] || continue
        legacy_omp_script_is_managed "$child" && continue
        fail "refusing to remove managed directory containing user files: $scripts_dir"
      done
    fi
    for child in "$path"/* "$path"/.[!.]* "$path"/..?*; do
      [[ -e "$child" || -L "$child" ]] || continue
      [[ "$child" == "$marker_path" || "$child" == "$scripts_dir" ]] && continue
      fail "refusing to remove managed directory containing user files: $path"
    done
  fi
}

remove_managed_path() {
  local path="$1" marker_path="${2:-$1}" child scripts_dir=""
  validate_managed_path "$path" "$marker_path"
  [[ -e "$path" || -L "$path" ]] || return 0
  if [[ -d "$path" && ! -L "$path" ]]; then
    if [[ "$path" == "$HOME_ROOT/.omp/agent/skills/session-log-omp" &&
          -d "$path/scripts" && ! -L "$path/scripts" ]]; then
      scripts_dir="$path/scripts"
      for child in "$scripts_dir"/* "$scripts_dir"/.[!.]* "$scripts_dir"/..?*; do
        [[ -e "$child" || -L "$child" ]] || continue
        if legacy_omp_script_is_managed "$child"; then
          remove_known_path "$child"
          continue
        fi
        fail "refusing to remove managed directory containing user files: $scripts_dir"
      done
    fi
    for child in "$path"/* "$path"/.[!.]* "$path"/..?*; do
      [[ -e "$child" || -L "$child" ]] || continue
      [[ "$child" == "$marker_path" || "$child" == "$scripts_dir" ]] && continue
      fail "refusing to remove managed directory containing user files: $path"
    done
    [[ -z "$scripts_dir" ]] || safe_remove_path "$scripts_dir" ||
      fail "refusing to remove managed directory containing user files: $scripts_dir"
    remove_known_path "$marker_path"
    safe_remove_path "$path" ||
      fail "refusing to remove managed directory containing user files: $path"
  else
    remove_known_path "$path"
  fi
}

legacy_script_is_tracked() {
  local name="$1" path="$2" hash
  [[ -f "$path" && ! -L "$path" ]] || return 1
  has_exact_ownership_marker "$path" && return 0
  hash="$(sha256_file "$path")"
  case "$name:$hash" in
    prompt_log_save.sh:81b0c9a3c1e5dc4a66387a61ef537634413b4a8a5aa51c2baba09d32f1f21c99|\
    prompt_log_new_session.sh:be3eab61ee895e97cdaa5ab624c707f00e188718efd23b4d1253f998350cba27|\
    prompt_log_stop.sh:43d402842f91c0a66dc6c3e0978eaac84a7288616a2e2816a3c5e6c5f577e91f|\
    prompt_log_subagent.sh:8b723c301764cdf858d629c60f194dcda6f0b52c2283d1014286e88a23c3661a|\
    prompt_log_lib.sh:921318ea4984f569d281916259f6e6cac0a199ea29d3ea9fadd56b8b514a8a45|\
    prompt_log_usage.sh:63fa36cfab6e6496a6ca06da2f1daa99013160145e2d62ab6ce5aecde33dd86a|\
    prompt_log_usage.jq:7a615cb3917c324d1638c4c0b7905681e5eb9eb14866f9b453415cb691f6ff1d|\
    prompt_log_prices.json:48aa786070d547d3d3a4552d13eeb403685660f0cf825b9797aaaee1bb15733d)
      return 0
      ;;
  esac
  return 1
}

# Prints the first legacy script that is neither session-log-marked nor a known release's copy.
unrecognized_claude_legacy_script() {
  local name path
  for name in "${CLAUDE_LEGACY_SCRIPTS[@]}"; do
    path="$HARNESS_ROOT/scripts/$name"
    [[ -e "$path" || -L "$path" ]] || continue
    legacy_script_is_tracked "$name" "$path" && continue
    printf '%s\n' "$path"
    return 0
  done
  return 1
}

# legacy_cleanup_claude calls it only when unrecognized_claude_legacy_script finds none; remove_known_path
# checks each one again.
remove_claude_legacy_scripts() {
  local name path
  for name in "${CLAUDE_LEGACY_SCRIPTS[@]}"; do
    path="$HARNESS_ROOT/scripts/$name"
    [[ -e "$path" || -L "$path" ]] || continue
    remove_known_path "$path"
    print_repair_report "removed legacy script $path"
  done
}
