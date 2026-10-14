# universal-session-log: managed
# Legacy cleanup for bin/session-log: removes, per harness, what older releases left behind.
#
# Sourced by bin/session-log after lib/adapter_install.sh, in the same shell, so it shares bin's
# globals (HARNESS, HARNESS_ROOT, STATE_DIR, HARNESS_LABEL, OWNER_MARKER_VALUE, ...). `on` and
# `repair` run it under the install lock. Claude Code's SessionStart hook and the OpenCode plugin's
# setup run it silently through `repair`, and so does a Cursor session start while logging is on and
# ~/.cursor/hooks.json still holds a legacy session-log entry; the OMP extension starts `repair` in
# the background when it loads. Claude Code, Cursor, OMP, and OpenCode `status` and `usage` never run
# it; in Codex, `status` and `usage` run it too when they repair an enabled adapter, so an unowned
# file at a legacy path fails them, as the old installer did before every command. It removes only
# what is proven to be session-log's and never clears runtime markers: a harness plugin's own hooks
# keep logging whatever duplicate registration goes. Claude Code, Cursor, and OpenCode removals of
# registrations, adapters, commands, scripts, and package copies, Codex package copies, and OMP
# removals of adapter links and package copies are reported through print_repair_report; obsolete
# state files, the OMP standalone extension copy, and the session-log-omp leftovers of pre-universal
# releases go silently.

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
# The adapter installer's version stamps and manifest that older Cursor, Codex, OMP, and OpenCode
# installs wrote; nothing reads them now (the Cursor, OMP, and OpenCode plugins own their hooks, and
# Codex's hooks.json command names no package).
readonly ADAPTER_OBSOLETE_STATE_FILES=(adapter.version adapter.manifest desired.version)

# The cleanup of the current harness: any harness whose older releases left something behind has
# its own legacy_cleanup_<harness> here, whether or not its plugin owns its hooks yet.
legacy_cleanup() {
  case "$HARNESS" in
    claude) legacy_cleanup_claude ;;
    codex) legacy_cleanup_codex ;;
    cursor) legacy_cleanup_cursor ;;
    opencode) legacy_cleanup_opencode ;;
    omp) legacy_cleanup_omp ;;
  esac
}

# Older releases registered the session-log hooks in ~/.cursor/hooks.json, pointing at the package
# copy they seeded into ~/.cursor/skills; the cursor-goodies plugin's .cursor-plugin/hooks.json
# registers them now, and Cursor runs both, so every entry install_hooks.py recognizes goes. Every
# other entry stays. An unsafe or invalid hooks.json fails the cleanup with nothing changed. The
# obsolete adapter state files go silently; runtime markers stay.
legacy_cleanup_cursor() {
  local config="$HARNESS_ROOT/hooks.json" report
  require_command python3
  if [[ -e "$config" || -L "$config" ]]; then
    preflight_native_hooks
    report="$(python3 "$(source_for configurator)" remove "$HARNESS" "$config")"
    print_repair_report "$report"
  fi
  remove_seeded_package "$HARNESS_ROOT/skills/session-log"
  validate_state_dir
  remove_obsolete_state_files "${ADAPTER_OBSOLETE_STATE_FILES[@]/#/"$STATE_DIR"/}"
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

# The package copy (or link) older install.sh releases seeded into ~/.codex/skills, and their
# adapter stamps. Their hooks.json entries, which name a package's hook by path, are replaced by
# repair_registrations or the adapter install that follows. Codex sessions keep running the hooks
# they loaded at start, and a hook whose script is gone blocks every prompt, so while hooks.json
# may still name the seeded copy's hook the copy stays; a run after the restart removes it. Without
# a codex-goodies plugin package repair_registrations leaves those entries, so only `on` (which
# needs the plugin) replaces them.
legacy_cleanup_codex() {
  local seeded="$HARNESS_ROOT/skills/session-log"
  require_command python3
  if [[ -e "$seeded" || -L "$seeded" ]] && codex_hooks_may_name_seeded_copy; then
    neutralize_seeded_package "$seeded"
    if codex_plugin_package_present; then
      print_repair_report "kept legacy package copy $seeded: Codex sessions started before session-log replaced its hooks.json entries may still run it; restart Codex, then run repair"
    else
      print_repair_report "kept legacy package copy $seeded: ~/.codex/hooks.json may still run it; install the codex-goodies plugin, then run on"
    fi
  else
    remove_seeded_package "$seeded"
  fi
  validate_state_dir
  remove_obsolete_state_files "${ADAPTER_OBSOLETE_STATE_FILES[@]/#/"$STATE_DIR"/}"
}

# False only when ~/.codex/hooks.json is absent, or is a JSON file none of whose strings names a
# .codex/skills/session-log package. It reads the file as Codex does, through a symlink, and
# compares decoded strings, so JSON escapes and a $HOME that is a symlink (entries name the copy by
# $HOME, HARNESS_ROOT is physical) still match. A file it cannot read or parse counts as naming
# it: removing a copy a running session's hook needs blocks every prompt.
codex_hooks_may_name_seeded_copy() {
  # The script's NOT_NAMED exit status; any other status keeps the copy.
  local config="$HARNESS_ROOT/hooks.json" status=0 not_named=1
  [[ -e "$config" || -L "$config" ]] || return 1
  CODEX_HOOKS_PATH="$config" python3 -I - <<'PY' || status=$?
import json
import os
import stat
import sys

SEEDED_PACKAGE = "/.codex/skills/session-log/"
NAMED, NOT_NAMED, UNKNOWN = 0, 1, 2


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, (dict, list)):
        for item in value.values() if isinstance(value, dict) else value:
            yield from strings(item)


def verdict():
    # O_NONBLOCK keeps a FIFO at the path from blocking the open.
    descriptor = os.open(os.environ["CODEX_HOOKS_PATH"], os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            return UNKNOWN
        document = json.loads(handle.read().decode("utf-8"))
    return NAMED if any(SEEDED_PACKAGE in text for text in strings(document)) else NOT_NAMED


try:
    result = verdict()
except Exception:
    result = UNKNOWN
sys.exit(result)
PY
  ((status != not_named))
}

# What releases before the opencode-goodies package registered session-log itself left in OpenCode's
# config root (the plugin runs this through `repair` once per installed package): the adapter and
# usage links, or marked standalone copies, `on` installed; the markdown /session-log
# command, which would override the package's; the seeded package copy; the adapter installer's
# state files; and the session-log-omp entrypoints of pre-universal releases. A foreign file at an
# adapter path stays; a foreign command stays too, but is named, as it hides the package's.
legacy_cleanup_opencode() {
  require_command python3
  # The command and the seeded copy first: they break the plugin's /session-log.
  remove_legacy_opencode_command
  remove_seeded_package "$HARNESS_ROOT/skills/session-log"
  remove_legacy_opencode_adapter plugins/session-log.js adapters/opencode/session-log.js
  remove_legacy_opencode_adapter scripts/session_log_usage.sh adapters/opencode/session_log_usage.sh
  validate_state_dir
  remove_obsolete_state_files "${ADAPTER_OBSOLETE_STATE_FILES[@]/#/"$STATE_DIR"/}"
  validate_managed_path "$HARNESS_ROOT/commands/session-log-omp.md"
  validate_managed_path "$HARNESS_ROOT/plugins/session-log-omp.js"
  remove_managed_path "$HARNESS_ROOT/commands/session-log-omp.md"
  remove_managed_path "$HARNESS_ROOT/plugins/session-log-omp.js"
}

# True when link destination $2 names adapter $1 (its path inside the package) where older releases
# linked it: inside any session-log package (a seeded copy, a plugin cache, a checkout) or the
# release store. A destination with a `.` or `..` component never does.
is_legacy_adapter_link() {
  local relative="$1" destination="$2"
  case "/$destination/" in */../*|*/./*) return 1 ;; esac
  [[ "$destination" == */skills/session-log/"$relative" || "$destination" == "$STORE/releases/"*/"$relative" ]]
}

# Removes the adapter at $HARNESS_ROOT/$1 when it is a link to adapter $2 inside a session-log
# package or the release store, or a copy carrying the ownership marker.
remove_legacy_opencode_adapter() {
  local path="$HARNESS_ROOT/$1" relative="$2" destination
  # Nothing to remove: a symlinked plugins/ or scripts/ directory (dotfiles) is no reason to fail.
  [[ -e "$path" || -L "$path" ]] || return 0
  ensure_safe_parent "$path"
  if [[ -L "$path" ]]; then
    destination="$(readlink "$path")"
    is_legacy_adapter_link "$relative" "$destination" || return 0
    unlink "$path"
    print_repair_report "removed legacy adapter link $path"
  elif has_exact_ownership_marker "$path"; then
    remove_checked_path "$path"
    print_repair_report "removed legacy adapter copy $path"
  fi
}

remove_legacy_opencode_command() {
  local command="$HARNESS_ROOT/commands/session-log.md"
  ensure_safe_parent "$command"
  [[ -e "$command" || -L "$command" ]] || return 0
  if has_exact_ownership_marker "$command"; then
    remove_checked_path "$command"
    print_repair_report "removed legacy command $command"
  else
    print_repair_report "kept $command: it is not session-log's and overrides the plugin's /session-log command; move it aside"
  fi
}

# What older releases left in the OMP root: the extension link (or standalone copy) `on` made, the
# package copy seeded into the skill root with its usage link inside, the adapter state files, and
# the session-log-omp skill and extension. omp loads a leftover extension beside the plugin's own.
# Every path is checked before the first removal. Only a seeded copy that is a real directory is
# entered for its usage link; the package-copy removal handles a symlinked one.
legacy_cleanup_omp() {
  local extension="$HARNESS_ROOT/extensions/session-log.js" seeded="$HARNESS_ROOT/skills/session-log"
  local usage="$HARNESS_ROOT/skills/session-log/scripts/session_log_usage.ts" has_usage_link=0
  if [[ ! -L "$seeded" && -L "$usage" ]]; then has_usage_link=1; fi
  validate_state_dir
  if [[ -L "$extension" ]]; then validate_legacy_omp_link extension "$extension"; else validate_managed_path "$extension"; fi
  if ((has_usage_link)); then validate_legacy_omp_link usage "$usage"; fi
  validate_managed_path "$HARNESS_ROOT/skills/session-log-omp" "$HARNESS_ROOT/skills/session-log-omp/SKILL.md"
  validate_managed_path "$HARNESS_ROOT/extensions/session-log-omp.js"
  if [[ -L "$extension" ]]; then remove_legacy_omp_link "$extension"; else remove_managed_path "$extension"; fi
  if ((has_usage_link)); then remove_legacy_omp_link "$usage"; fi
  remove_managed_path "$HARNESS_ROOT/skills/session-log-omp" "$HARNESS_ROOT/skills/session-log-omp/SKILL.md"
  remove_managed_path "$HARNESS_ROOT/extensions/session-log-omp.js"
  remove_seeded_package "$seeded"
  remove_obsolete_state_files "${ADAPTER_OBSOLETE_STATE_FILES[@]/#/"$STATE_DIR"/}"
}

# Fails unless symlink $2 points at OMP adapter kind $1 where older releases linked it: inside any
# session-log package (the seeded copy, a plugin cache, another checkout) or the release store.
validate_legacy_omp_link() {
  local kind="$1" link="$2" destination
  ensure_safe_parent "$link"
  destination="$(readlink "$link")"
  is_legacy_adapter_link "${ADAPTER_SOURCES[omp:$kind]}" "$destination" && return 0
  fail "refusing to remove unowned legacy path: $link"
}

# Callers run validate_legacy_omp_link on it first.
remove_legacy_omp_link() {
  remove_checked_path "$1"
  print_repair_report "removed legacy adapter link $1"
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
  error="$(SETTINGS_PATH="$settings" python3 -I - 2>&1 <<'PY'
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
  local target="$1" result line
  result="$(python3 "$SESSION_LOG_LIB/legacy_package.py" remove "$target")"
  while IFS= read -r line; do
    case "$line" in
      removed) print_repair_report "removed legacy package copy $target" ;;
      "removed link") print_repair_report "removed legacy package link $target" ;;
      neutralized) report_neutralized_package "$target" ;;
      kept:*) print_repair_report "kept legacy package copy $target:${line#kept:}; move it aside" ;;
    esac
  done <<< "$result"
}

# Removes the manifest-proven SKILL.md files of the seeded copy at $1 and keeps the rest, for a
# copy that running sessions' hooks may still run: no harness loads it as a skill any more, so its
# older code can no longer be invoked to register its hooks again.
neutralize_seeded_package() {
  local target="$1" result
  result="$(python3 "$SESSION_LOG_LIB/legacy_package.py" neutralize "$target")"
  [[ "$result" != neutralized ]] || report_neutralized_package "$target"
}

report_neutralized_package() {
  print_repair_report "removed the SKILL.md of legacy package copy $1, so it is no longer loaded as a skill"
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
    if [[ "$candidate" == "$STATE_DIR/plugin-root" ]] && [[ -e "$seeded" || -L "$seeded" ]]; then
      continue
    fi
    remove_obsolete_state_files "$candidate"
  done
}

# Removes each state file named; a path that is not a regular file stays. Callers run
# validate_state_dir first.
remove_obsolete_state_files() {
  local candidate
  for candidate in "$@"; do
    [[ -f "$candidate" && ! -L "$candidate" ]] || continue
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
      "$HOME_ROOT/.config/opencode/plugins/session-log-omp.js:$PACKAGE_ROOT/adapters/omp/session-log.js"|\
      "$HOME_ROOT/.config/opencode/plugins/session-log-omp.js:$HOME_ROOT/.omp/agent/skills/session-log/adapters/omp/session-log.js"|\
      "$HOME_ROOT/.config/opencode/plugins/session-log-omp.js:$STORE/releases/"*/adapters/omp/session-log.js|\
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
