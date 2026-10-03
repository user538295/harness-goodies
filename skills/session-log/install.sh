#!/usr/bin/env bash
set -euo pipefail

readonly PACKAGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SOURCE_ROOT="$PACKAGE_ROOT"
readonly VERSION="$(tr -d '[:space:]' < "$SOURCE_ROOT/VERSION")"
readonly HOME_ROOT="$(cd "${HOME:?HOME is required}" && pwd -P)"
readonly STORE="$HOME_ROOT/.local/share/universal-session-log"
readonly OWNERSHIP_MARKER="universal-session-log: managed"
readonly PACKAGE_MANIFEST=".universal-session-log.manifest"
readonly INSTALL_LOCK="$STORE/.install.lock"
INSTALL_LOCK_OWNER_START=""
INSTALL_STORE_CREATED=0

SESSION_LOG_LOG_PREFIX="universal-session-log"
# shellcheck source=lib/common.sh
source "$PACKAGE_ROOT/lib/common.sh"

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

HARNESS_SELECTION=""
INSTALL_MODE=0
LOCAL_ARGUMENTS=""
LOCAL_ARGUMENTS_SET=0

selected_harness() {
  [[ "$HARNESS_SELECTION" == all || "$HARNESS_SELECTION" == "$1" ]]
}

package_root_for() {
  case "$1" in
    claude) printf '%s\n' "$HOME_ROOT/.claude/skills/session-log" ;;
    codex) printf '%s\n' "$HOME_ROOT/.codex/skills/session-log" ;;
    cursor) printf '%s\n' "$HOME_ROOT/.cursor/skills/session-log" ;;
    opencode) printf '%s\n' "$HOME_ROOT/.config/opencode/skills/session-log" ;;
    omp) printf '%s\n' "$HOME_ROOT/.omp/agent/skills/session-log" ;;
    *) fail "unknown harness: $1" ;;
  esac
}

parse_args() {
  local arg
  while (($#)); do
    arg="$1"
    case "$arg" in
      --install)
        INSTALL_MODE=1
        shift
        ;;
      --harness)
        (($# >= 2)) || fail "--harness requires a value"
        [[ -z "$HARNESS_SELECTION" ]] || fail "only one --harness value is allowed"
        HARNESS_SELECTION="$2"
        shift 2
        ;;
      --harness=*)
        [[ -z "$HARNESS_SELECTION" ]] || fail "only one --harness value is allowed"
        HARNESS_SELECTION="${arg#*=}"
        shift
        ;;
      --arguments)
        (($# >= 2)) || fail "--arguments requires a value"
        [[ "$LOCAL_ARGUMENTS_SET" == 0 ]] || fail "only one --arguments value is allowed"
        LOCAL_ARGUMENTS="$2"
        LOCAL_ARGUMENTS_SET=1
        shift 2
        ;;
      --arguments=*)
        [[ "$LOCAL_ARGUMENTS_SET" == 0 ]] || fail "only one --arguments value is allowed"
        LOCAL_ARGUMENTS="${arg#*=}"
        LOCAL_ARGUMENTS_SET=1
        shift
        ;;
      --help|-h)
        printf 'Usage: install.sh --harness <claude|codex|cursor|opencode|omp|all> [--arguments "<command and native args>"]\n'
        printf '       install.sh --install --harness <claude|codex|cursor|opencode|omp|all>\n'
        exit 0
        ;;
      *)
        fail "unknown installer argument: $arg"
        ;;
    esac
  done
  [[ -n "$HARNESS_SELECTION" ]] || fail "--harness is required"
  case "$HARNESS_SELECTION" in
    claude|codex|cursor|opencode|omp|all) ;;
    *) fail "unknown harness: $HARNESS_SELECTION (use claude, codex, cursor, opencode, omp, or all)" ;;
  esac
  if ((INSTALL_MODE == 0)); then
    [[ "$LOCAL_ARGUMENTS_SET" == 1 ]] || LOCAL_ARGUMENTS="status"
  elif ((LOCAL_ARGUMENTS_SET)); then
    fail "--arguments cannot be used with --install"
  fi
}

cleanup() {
  local path="$1"
  [[ -n "$path" ]] || return 0
  ensure_safe_parent "$path"
  if [[ -d "$path" && ! -L "$path" ]]; then
    safe_remove_path "$path" || fail "refusing to recursively remove non-empty directory: $path"
  elif [[ -e "$path" || -L "$path" ]]; then
    safe_remove_path "$path" || fail "refusing to remove path: $path"
  fi
}
_package_lock_operation() {
  local mode="$1"
  SESSION_LOG_LOCK_PATH="$INSTALL_LOCK" \
    SESSION_LOG_LOCK_PID="$$" SESSION_LOG_LOCK_START="$INSTALL_LOCK_OWNER_START" \
    python3 "$SESSION_LOG_LIB/locking.py" "$mode"
}
acquire_package_lock() {
  [[ ! -L "$STORE" ]] || fail "refusing symlinked package store: $STORE"
  [[ ! -e "$STORE" || -d "$STORE" ]] || fail "refusing non-directory package store: $STORE"
  if [[ ! -e "$STORE" ]]; then INSTALL_STORE_CREATED=1; fi
  ensure_private_directory "$STORE"
  INSTALL_LOCK_OWNER_START="$(ps -p "$$" -o lstart= 2>/dev/null | sed 's/^ *//; s/[[:space:]]*$//')"
  [[ -n "$INSTALL_LOCK_OWNER_START" ]] || fail "cannot determine package lock owner"
  _package_lock_operation acquire ||
    fail "universal session-log installation is already in progress"
}
release_package_lock() {
  _package_lock_operation release || true
  if ((INSTALL_STORE_CREATED)); then
    rmdir "$STORE" 2>/dev/null || true
  fi
}

require_file() {
  [[ -f "$1" && ! -L "$1" ]] || fail "package source is incomplete: $1"
}

atomic_copy() {
  local source="$1" target="$2" preserve_mode="${3:-0}" manifest_owned="${4:-0}"
  require_file "$source"
  ensure_safe_parent "$target"
  SESSION_LOG_SOURCE="$source" SESSION_LOG_TARGET="$target" SESSION_LOG_PRESERVE_MODE="$preserve_mode" \
    SESSION_LOG_MANIFEST_OWNED="$manifest_owned" \
    python3 "$SESSION_LOG_LIB/pathsafe.py" copy-file ||
    fail "cannot safely copy package file: $target"
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
  cleanup "$path"
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
      "$HOME_ROOT/.config/opencode/plugins/session-log.js:$SOURCE_ROOT/adapters/opencode/session-log.js"|\
      "$HOME_ROOT/.config/opencode/plugins/session-log.js:$HOME_ROOT/.config/opencode/skills/session-log/adapters/opencode/session-log.js"|\
      "$HOME_ROOT/.config/opencode/plugins/session-log.js:$STORE/releases/"*/adapters/opencode/session-log.js|\
      "$HOME_ROOT/.config/opencode/plugins/session-log.js:$HOME_ROOT/.claude/plugins/cache/"*/claude-goodies/*/skills/session-log/adapters/opencode/session-log.js|\
      "$HOME_ROOT/.config/opencode/plugins/session-log.js:$HOME/.claude/plugins/cache/"*/claude-goodies/*/skills/session-log/adapters/opencode/session-log.js|\
      "$HOME_ROOT/.config/opencode/plugins/session-log-omp.js:$SOURCE_ROOT/adapters/omp/session-log.js"|\
      "$HOME_ROOT/.config/opencode/plugins/session-log-omp.js:$HOME_ROOT/.omp/agent/skills/session-log/adapters/omp/session-log.js"|\
      "$HOME_ROOT/.config/opencode/plugins/session-log-omp.js:$STORE/releases/"*/adapters/omp/session-log.js|\
      "$HOME_ROOT/.config/opencode/scripts/session_log_usage.sh:$SOURCE_ROOT/adapters/opencode/session_log_usage.sh"|\
      "$HOME_ROOT/.config/opencode/scripts/session_log_usage.sh:$HOME_ROOT/.config/opencode/skills/session-log/adapters/opencode/session_log_usage.sh"|\
      "$HOME_ROOT/.config/opencode/scripts/session_log_usage.sh:$STORE/releases/"*/adapters/opencode/session_log_usage.sh|\
      "$HOME_ROOT/.omp/agent/extensions/session-log.js:$SOURCE_ROOT/adapters/omp/session-log.js"|\
      "$HOME_ROOT/.omp/agent/extensions/session-log.js:$HOME_ROOT/.omp/agent/skills/session-log/adapters/omp/session-log.js"|\
      "$HOME_ROOT/.omp/agent/extensions/session-log.js:$STORE/releases/"*/adapters/omp/session-log.js|\
      "$HOME_ROOT/.omp/agent/extensions/session-log-omp.js:$SOURCE_ROOT/adapters/omp/session-log.js"|\
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

remove_legacy_file() {
  remove_managed_path "$1"
}

check_default_roots() {
  if selected_harness claude; then
    guard_env CLAUDE_CONFIG_DIR "$HOME_ROOT/.claude" \
      "CLAUDE_CONFIG_DIR is relocated; custom roots are unsupported; install this package explicitly for that location"
  fi
  if selected_harness codex; then
    guard_env CODEX_HOME "$HOME_ROOT/.codex" \
      "CODEX_HOME is relocated; custom roots are unsupported; install this package explicitly for that location"
  fi
  if selected_harness opencode; then
    guard_env OPENCODE_CONFIG_DIR "$HOME_ROOT/.config/opencode" \
      "OPENCODE_CONFIG_DIR is relocated; custom roots are unsupported; install this package explicitly for that location"
    guard_env XDG_CONFIG_HOME "$HOME_ROOT/.config" \
      "XDG_CONFIG_HOME is relocated; custom roots are unsupported; install this package explicitly for that location"
    guard_env XDG_DATA_HOME "$HOME_ROOT/.local/share" \
      "XDG_DATA_HOME is relocated; custom roots are unsupported; install this package explicitly for that location"
  fi
  if selected_harness omp; then
    guard_env PI_CODING_AGENT_DIR "$HOME_ROOT/.omp/agent" \
      "PI_CODING_AGENT_DIR is relocated; custom roots are unsupported; install this package explicitly for that location"
    guard_env OMP_PROMPT_LOG_DIR "$HOME_ROOT/.omp/agent/prompt-logs" \
      "OMP_PROMPT_LOG_DIR is relocated; custom roots are unsupported; install this package explicitly for that location"
  fi
}

validate_claude_settings() {
  local settings="$HOME_ROOT/.claude/settings.json"
  [[ -e "$settings" ]] || return 0
  ensure_safe_parent "$settings"
  [[ -L "$settings" ]] && fail "refusing to migrate symlinked Claude settings: $settings"
  SETTINGS_PATH="$settings" python3 - <<'PY'
import json
import os
import tempfile
settings_path = os.environ["SETTINGS_PATH"]
with open(settings_path, encoding="utf-8") as handle:
    document = json.load(handle)
if not isinstance(document, dict):
    raise SystemExit("cannot migrate Claude settings: root must be an object")
hooks = document.get("hooks")
if hooks is not None and not isinstance(hooks, dict):
    raise SystemExit("cannot migrate Claude settings: hooks must be an object")
if isinstance(hooks, dict):
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            raise SystemExit(f"cannot migrate Claude settings: hooks.{event} must be an array")
parent = os.path.dirname(settings_path)
fd, temporary = tempfile.mkstemp(prefix=".session-log-check.", dir=parent, text=True)
os.close(fd)
os.unlink(temporary)
PY
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

validate_claude_legacy_scripts() {
  local name path
  for name in prompt_log_save.sh prompt_log_new_session.sh prompt_log_stop.sh prompt_log_subagent.sh prompt_log_lib.sh prompt_log_usage.sh prompt_log_usage.jq prompt_log_prices.json; do
    path="$HOME_ROOT/.claude/scripts/$name"
    [[ -e "$path" || -L "$path" ]] || continue
    legacy_script_is_tracked "$name" "$path" ||
      fail "refusing to remove unrecognized legacy Claude script: $path"
  done
}

remove_claude_legacy_scripts() {
  local name path
  validate_claude_legacy_scripts
  for name in prompt_log_save.sh prompt_log_new_session.sh prompt_log_stop.sh prompt_log_subagent.sh prompt_log_lib.sh prompt_log_usage.sh prompt_log_usage.jq prompt_log_prices.json; do
    path="$HOME_ROOT/.claude/scripts/$name"
    [[ -e "$path" || -L "$path" ]] || continue
    remove_known_path "$path"
  done
}

current_adapter_link() {
  local path="$1" target
  [[ -L "$path" ]] || return 1
  target="$(readlink "$path")"
  case "$path:$target" in
    "$HOME_ROOT/.config/opencode/plugins/session-log.js:$SOURCE_ROOT/adapters/opencode/session-log.js"|\
    "$HOME_ROOT/.config/opencode/plugins/session-log.js:$HOME_ROOT/.config/opencode/skills/session-log/adapters/opencode/session-log.js"|\
    "$HOME_ROOT/.config/opencode/plugins/session-log.js:$STORE/releases/"*/adapters/opencode/session-log.js|\
    "$HOME_ROOT/.config/opencode/plugins/session-log.js:$HOME_ROOT/.claude/plugins/cache/"*/claude-goodies/*/skills/session-log/adapters/opencode/session-log.js|\
    "$HOME_ROOT/.config/opencode/plugins/session-log.js:$HOME/.claude/plugins/cache/"*/claude-goodies/*/skills/session-log/adapters/opencode/session-log.js|\
    "$HOME_ROOT/.config/opencode/scripts/session_log_usage.sh:$SOURCE_ROOT/adapters/opencode/session_log_usage.sh"|\
    "$HOME_ROOT/.config/opencode/scripts/session_log_usage.sh:$HOME_ROOT/.config/opencode/skills/session-log/adapters/opencode/session_log_usage.sh"|\
    "$HOME_ROOT/.config/opencode/scripts/session_log_usage.sh:$STORE/releases/"*/adapters/opencode/session_log_usage.sh|\
    "$HOME_ROOT/.omp/agent/extensions/session-log.js:$SOURCE_ROOT/adapters/omp/session-log.js"|\
    "$HOME_ROOT/.omp/agent/extensions/session-log.js:$HOME_ROOT/.omp/agent/skills/session-log/adapters/omp/session-log.js"|\
    "$HOME_ROOT/.omp/agent/extensions/session-log.js:$STORE/releases/"*/adapters/omp/session-log.js)
      return 0
      ;;
  esac
  return 1
}

remove_standalone_adapter_files() {
  if selected_harness opencode; then
    current_adapter_link "$HOME_ROOT/.config/opencode/plugins/session-log.js" ||
      remove_legacy_file "$HOME_ROOT/.config/opencode/plugins/session-log.js"
    current_adapter_link "$HOME_ROOT/.config/opencode/scripts/session_log_usage.sh" ||
      remove_legacy_file "$HOME_ROOT/.config/opencode/scripts/session_log_usage.sh"
  fi
  if selected_harness omp; then
    current_adapter_link "$HOME_ROOT/.omp/agent/extensions/session-log.js" ||
      remove_legacy_file "$HOME_ROOT/.omp/agent/extensions/session-log.js"
  fi
}

remove_legacy_entrypoints() {
  local path
  for path in \
    "$HOME_ROOT/.claude/skills/session-log/SKILL.md" \
    "$HOME_ROOT/.config/opencode/skills/session-log/SKILL.md" \
    "$HOME_ROOT/.config/opencode/commands/session-log.md" \
    "$HOME_ROOT/.omp/agent/skills/session-log/SKILL.md"; do
    case "$path" in
      "$HOME_ROOT/.claude/"*) selected_harness claude || continue ;;
      "$HOME_ROOT/.config/opencode/"*) selected_harness opencode || continue ;;
      "$HOME_ROOT/.omp/"*) selected_harness omp || continue ;;
    esac
    case "$path" in
      "$HOME_ROOT/.claude/skills/session-log/SKILL.md"|\
      "$HOME_ROOT/.config/opencode/skills/session-log/SKILL.md"|\
      "$HOME_ROOT/.config/opencode/commands/session-log.md"|\
      "$HOME_ROOT/.omp/agent/skills/session-log/SKILL.md")
      continue
      ;;
    esac
    if [[ -f "$path" && ! -L "$path" ]] && has_exact_ownership_marker "$path"; then
      remove_managed_path "$path"
    fi
  done
}

validate_legacy_entrypoints() {
  local path
  for path in \
    "$HOME_ROOT/.claude/skills/session-log/SKILL.md" \
    "$HOME_ROOT/.config/opencode/skills/session-log/SKILL.md" \
    "$HOME_ROOT/.config/opencode/commands/session-log.md" \
    "$HOME_ROOT/.omp/agent/skills/session-log/SKILL.md"; do
    case "$path" in
      "$HOME_ROOT/.claude/"*) selected_harness claude || continue ;;
      "$HOME_ROOT/.config/opencode/"*) selected_harness opencode || continue ;;
      "$HOME_ROOT/.omp/"*) selected_harness omp || continue ;;
    esac
    if [[ -f "$path" && ! -L "$path" ]] && has_exact_ownership_marker "$path"; then
      validate_managed_path "$path"
    fi
  done
}

validate_migration() {
  selected_harness claude && {
    validate_claude_settings
    validate_claude_legacy_scripts
  }
  validate_legacy_entrypoints
  if selected_harness opencode; then
    validate_managed_path "$HOME_ROOT/.config/opencode/plugins/session-log.js"
    validate_managed_path "$HOME_ROOT/.config/opencode/scripts/session_log_usage.sh"
    validate_managed_path "$HOME_ROOT/.config/opencode/commands/session-log-omp.md"
    validate_managed_path "$HOME_ROOT/.config/opencode/plugins/session-log-omp.js"
  fi
  if selected_harness omp; then
    validate_managed_path "$HOME_ROOT/.omp/agent/extensions/session-log.js"
    validate_managed_path "$HOME_ROOT/.omp/agent/skills/session-log-omp" \
      "$HOME_ROOT/.omp/agent/skills/session-log-omp/SKILL.md"
    validate_managed_path "$HOME_ROOT/.omp/agent/extensions/session-log-omp.js"
  fi
}

migrate_legacy_files() {
  local settings="$HOME_ROOT/.claude/settings.json"
  if selected_harness claude && [[ -f "$settings" ]]; then
    SETTINGS_PATH="$settings" CLAUDE_SCRIPTS_DIR="$HOME_ROOT/.claude/scripts" \
      python3 "$SESSION_LOG_LIB/claude_settings.py" migrate
  fi
  selected_harness claude && validate_claude_settings
  remove_legacy_entrypoints
  selected_harness claude && remove_claude_legacy_scripts
  remove_standalone_adapter_files
  if selected_harness omp; then
    remove_managed_path "$HOME_ROOT/.omp/agent/skills/session-log-omp" \
      "$HOME_ROOT/.omp/agent/skills/session-log-omp/SKILL.md"
    remove_managed_path "$HOME_ROOT/.omp/agent/extensions/session-log-omp.js"
  fi
  if selected_harness opencode; then
    remove_managed_path "$HOME_ROOT/.config/opencode/commands/session-log-omp.md"
    remove_managed_path "$HOME_ROOT/.config/opencode/plugins/session-log-omp.js"
  fi

}

package_files() {
  cat <<'EOF'
SKILL.md
VERSION
install.sh
bin/session-log
lib/common.sh
lib/pathsafe.py
lib/claude_settings.py
lib/enable_flag.py
lib/locking.py
lib/adapter_install.sh
adapters/claude/claude_hook.sh
adapters/claude/scripts/prompt_log_lib.sh
adapters/claude/scripts/prompt_log_new_session.sh
adapters/claude/scripts/prompt_log_prices.json
adapters/claude/scripts/prompt_log_save.sh
adapters/claude/scripts/prompt_log_stop.sh
adapters/claude/scripts/prompt_log_subagent.sh
adapters/claude/scripts/prompt_log_usage.jq
adapters/claude/scripts/prompt_log_usage.sh
adapters/codex/session_log_usage.py
adapters/cursor/session_log_usage.py
adapters/native/session_log_hook.py
adapters/native/install_hooks.py
adapters/opencode/session-log.js
adapters/opencode/session_log_usage.sh
adapters/omp/session-log.js
adapters/omp/session_log_usage.ts
templates/opencode/command.md
EOF
}

package_manifest_hash() {
  local manifest="$1" relative="$2"
  awk -v wanted="asset=$relative" '
    $0 == wanted {
      if (getline && $0 ~ /^sha256=[0-9a-f]{64}$/) {
        sub(/^sha256=/, "")
        print
        exit
      }
    }
  ' "$manifest" 2>/dev/null
}

validate_package_manifest() {
  local target="$1" skill_source="$2"
  local manifest="$target/$PACKAGE_MANIFEST"
  local relative expected source_hash target_hash manifest_version
  [[ -e "$manifest" || -L "$manifest" ]] || return 0
  [[ -f "$manifest" && ! -L "$manifest" ]] ||
    fail "refusing to use invalid package ownership manifest: $manifest"
  if ! SESSION_LOG_MANIFEST="$manifest" SESSION_LOG_PACKAGE_ASSETS="$(package_files)" python3 - <<'PY'
import os
import re

manifest = os.environ["SESSION_LOG_MANIFEST"]
allowed = set(os.environ["SESSION_LOG_PACKAGE_ASSETS"].splitlines())
allowed.update({"templates/opencode/SKILL.md", "templates/omp/SKILL.md"})
with open(manifest, encoding="utf-8") as handle:
    lines = [line.rstrip("\n") for line in handle]
if len(lines) < 5 or lines[0] != "format=1" or lines[1] != "owner=universal-session-log":
    raise SystemExit(1)
if not re.fullmatch(r"version=[0-9]+\.[0-9]+\.[0-9]+", lines[2]):
    raise SystemExit(1)
seen = set()
if (len(lines) - 3) % 2:
    raise SystemExit(1)
for index in range(3, len(lines), 2):
    asset = lines[index][len("asset="):] if lines[index].startswith("asset=") else ""
    digest = lines[index + 1]
    if not asset or asset not in allowed or asset in seen:
        raise SystemExit(1)
    if not re.fullmatch(r"sha256=[0-9a-f]{64}", digest):
        raise SystemExit(1)
    seen.add(asset)
PY
  then
    fail "refusing to use invalid package ownership manifest: $manifest"
  fi
  while IFS= read -r relative; do
    [[ "$relative" == "SKILL.md" ]] && continue
    expected="$(package_manifest_hash "$manifest" "$relative")"
    [[ -z "$expected" ]] && continue
    [[ "$expected" =~ ^[0-9a-f]{64}$ ]] ||
      fail "refusing to use invalid package ownership manifest: $manifest"
    [[ -f "$target/$relative" && ! -L "$target/$relative" ]] ||
      fail "managed package asset is missing: $target/$relative"
    target_hash="$(sha256_file "$target/$relative")"
    source_hash="$(sha256_file "$SOURCE_ROOT/$relative")"
    [[ "$target_hash" == "$expected" || "$target_hash" == "$source_hash" ]] ||
      fail "managed package asset was modified: $target/$relative"
  done < <(package_files)
  for relative in templates/opencode/SKILL.md templates/omp/SKILL.md; do
    expected="$(package_manifest_hash "$manifest" "$relative")"
    [[ -z "$expected" ]] && continue
    [[ -f "$target/$relative" && ! -L "$target/$relative" ]] ||
      fail "managed package asset is missing: $target/$relative"
    [[ "$(sha256_file "$target/$relative")" == "$expected" ]] ||
      fail "managed package asset was modified: $target/$relative"
  done
  expected="$(package_manifest_hash "$manifest" SKILL.md)"
  [[ "$expected" =~ ^[0-9a-f]{64}$ ]] ||
    fail "refusing to use invalid package ownership manifest: $manifest"
  [[ -f "$target/SKILL.md" && ! -L "$target/SKILL.md" ]] ||
    fail "managed package asset is missing: $target/SKILL.md"
  target_hash="$(sha256_file "$target/SKILL.md")"
  source_hash="$(sha256_file "$skill_source")"
  [[ "$target_hash" == "$expected" || "$target_hash" == "$source_hash" ]] ||
    fail "managed package asset was modified: $target/SKILL.md"
}

package_manifest_owns() {
  local target="$1" relative="$2" expected
  [[ -f "$target/$PACKAGE_MANIFEST" && ! -L "$target/$PACKAGE_MANIFEST" ]] || return 1
  expected="$(package_manifest_hash "$target/$PACKAGE_MANIFEST" "$relative")"
  [[ "$expected" =~ ^[0-9a-f]{64}$ ]] || return 1
  [[ -f "$target/$relative" && ! -L "$target/$relative" ]] || return 1
  [[ "$(sha256_file "$target/$relative")" == "$expected" ]]
}

remove_retired_package_assets() {
  local target="$1" relative
  for relative in templates/opencode/SKILL.md templates/omp/SKILL.md; do
    if package_manifest_owns "$target" "$relative"; then
      unlink "$target/$relative"
    fi
  done
  rmdir "$target/templates/opencode" "$target/templates/omp" 2>/dev/null || true
}
package_skill_owned() {
  local target="$1"
  package_manifest_owns "$target" SKILL.md
}
manifestless_package_owned() {
  local target="$1" version
  [[ ! -e "$target/$PACKAGE_MANIFEST" && ! -L "$target/$PACKAGE_MANIFEST" ]] || return 1
  [[ -f "$target/SKILL.md" && ! -L "$target/SKILL.md" ]] || return 1
  has_exact_ownership_marker "$target/SKILL.md" || return 1
  [[ -f "$target/VERSION" && ! -L "$target/VERSION" ]] || return 1
  version="$(cat "$target/VERSION")" || return 1
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || return 1
  [[ -f "$target/install.sh" && ! -L "$target/install.sh" ]] || return 1
  [[ -f "$target/bin/session-log" && ! -L "$target/bin/session-log" ]] || return 1
}

write_package_manifest() {
  local target="$1" manifest="$1/$PACKAGE_MANIFEST" temp relative
  ensure_safe_parent "$manifest"
  [[ ! -L "$target" && -d "$target" ]] || fail "invalid package target: $target"
  [[ ! -L "$manifest" && ! -d "$manifest" ]] ||
    fail "refusing to overwrite package ownership manifest: $manifest"
  temp="$(mktemp "${TMPDIR:-/tmp}/session-log-manifest.XXXXXX")" ||
    fail "cannot create package manifest temporary file"
  chmod 600 "$temp"
  {
    printf 'format=1\n'
    printf 'owner=universal-session-log\n'
    printf 'version=%s\n' "$VERSION"
    while IFS= read -r relative; do
      [[ "$relative" == "SKILL.md" ]] && continue
      printf 'asset=%s\nsha256=%s\n' "$relative" "$(sha256_file "$target/$relative")"
    done < <(package_files)
    printf 'asset=SKILL.md\nsha256=%s\n' "$(sha256_file "$target/SKILL.md")"
  } > "$temp"
  if ! atomic_copy "$temp" "$manifest" 0 1; then
    unlink "$temp"
    fail "cannot safely write package ownership manifest: $manifest"
  fi
  unlink "$temp"
}

is_known_entrypoint_target() {
  case "$1" in
    "$HOME_ROOT/.claude/skills/session-log/SKILL.md"|\
    "$HOME_ROOT/.codex/skills/session-log/SKILL.md"|\
    "$HOME_ROOT/.cursor/skills/session-log/SKILL.md"|\
    "$HOME_ROOT/.config/opencode/skills/session-log/SKILL.md"|\
    "$HOME_ROOT/.config/opencode/commands/session-log.md"|\
    "$HOME_ROOT/.omp/agent/skills/session-log/SKILL.md") return 0 ;;
  esac
  return 1
}

validate_copy_target() {
  local source="$1" target="$2" allow_legacy_marker="${3:-0}" manifest_owned="${4:-0}"
  require_file "$source"
  ensure_safe_parent "$target"
  [[ ! -L "$target" ]] || fail "refusing to overwrite symlink: $target"
  [[ ! -d "$target" ]] || fail "refusing to overwrite directory: $target"
  if [[ -e "$target" ]]; then
    [[ -f "$target" && ! -L "$target" ]] ||
      fail "refusing to overwrite non-file: $target"
    if ! cmp -s "$source" "$target"; then
      if ((manifest_owned)); then
        :
      elif ((allow_legacy_marker)) && has_exact_ownership_marker "$target"; then
        :
      else
        fail "refusing to overwrite unowned file: $target"
      fi
    fi
  fi
}
validate_package_targets() {
  local target="$1" skill_source="$2" relative allow_legacy_marker=0 manifest_owned=0 legacy_owned=0
  validate_package_manifest "$target" "$skill_source"
  if manifestless_package_owned "$target"; then
    legacy_owned=1
  fi
  while IFS= read -r relative; do
    [[ "$relative" == "SKILL.md" ]] && continue
    manifest_owned=$legacy_owned
    if [[ -e "$target/$relative" ]] && package_manifest_owns "$target" "$relative"; then
      if cmp -s "$SOURCE_ROOT/$relative" "$target/$relative"; then
        continue
      fi
      manifest_owned=1
    fi
    validate_copy_target "$SOURCE_ROOT/$relative" "$target/$relative" 0 "$manifest_owned"
  done < <(package_files)
  manifest_owned=$legacy_owned
  if package_skill_owned "$target"; then
    if cmp -s "$skill_source" "$target/SKILL.md"; then
      return 0
    fi
    manifest_owned=1
  fi
  is_known_entrypoint_target "$target/SKILL.md" && allow_legacy_marker=1
  validate_copy_target "$skill_source" "$target/SKILL.md" "$allow_legacy_marker" "$manifest_owned"
}

copy_package() {
  local target="$1" skill_source="$2" relative
  validate_package_targets "$target" "$skill_source"
  while IFS= read -r relative; do
    [[ "$relative" == "SKILL.md" ]] && continue
    if [[ -e "$target/$relative" ]] &&
      package_manifest_owns "$target" "$relative" &&
      cmp -s "$SOURCE_ROOT/$relative" "$target/$relative"; then
      continue
    fi
    atomic_copy "$SOURCE_ROOT/$relative" "$target/$relative" 1 1
  done < <(package_files)
  if ! package_skill_owned "$target" ||
    ! cmp -s "$skill_source" "$target/SKILL.md"; then
    atomic_copy "$skill_source" "$target/SKILL.md" 1 1
  fi
  remove_retired_package_assets "$target"
  write_package_manifest "$target"
}

recognized_dangling_package_link() {
  local target="$1" destination
  [[ -L "$target" && ! -e "$target" ]] || return 1
  destination="$(readlink "$target")"
  case "$destination" in
    "$SOURCE_ROOT"|\
    "$STORE/releases/"*/skills/session-log|\
    "$HOME_ROOT/.claude/plugins/cache/"*/claude-goodies/*/skills/session-log|\
    "$HOME/.claude/plugins/cache/"*/claude-goodies/*/skills/session-log) return 0 ;;
  esac
  return 1
}

prepare_package_target() {
  local target="$1"
  if [[ -L "$target" ]]; then
    recognized_dangling_package_link "$target" ||
      fail "refusing to replace unowned package link: $target"
    unlink "$target"
  fi
}

validate_seed_entrypoints() {
  local command_target="$HOME_ROOT/.config/opencode/commands/session-log.md"
  local command_allow_legacy_marker=0 harness target
  for harness in claude codex cursor opencode omp; do
    selected_harness "$harness" || continue
    target="$(package_root_for "$harness")"
    if ! recognized_dangling_package_link "$target"; then
      validate_package_targets "$target" "$SOURCE_ROOT/SKILL.md"
    fi
  done
  if selected_harness opencode; then
    is_known_entrypoint_target "$command_target" && command_allow_legacy_marker=1
    validate_copy_target "$SOURCE_ROOT/templates/opencode/command.md" \
      "$command_target" "$command_allow_legacy_marker"
  fi
}

seed_entrypoints() {
  local command_target="$HOME_ROOT/.config/opencode/commands/session-log.md"
  local harness target
  for harness in claude codex cursor opencode omp; do
    selected_harness "$harness" || continue
    target="$(package_root_for "$harness")"
    prepare_package_target "$target"
    if [[ "$SOURCE_ROOT" != "$target" ]]; then
      copy_package "$target" "$SOURCE_ROOT/SKILL.md"
    else
      remove_retired_package_assets "$target"
      write_package_manifest "$target"
    fi
  done
  if selected_harness opencode; then
    atomic_copy "$SOURCE_ROOT/templates/opencode/command.md" "$command_target" 1 1
  fi
}

validate_package() {
  local relative
  [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] ||
    fail "package source is invalid: $SOURCE_ROOT/VERSION"
  while IFS= read -r relative; do
    case "$relative" in
      ""|/*|../*|*/../*) fail "invalid package asset path: $relative" ;;
    esac
    require_file "$SOURCE_ROOT/$relative"
  done < <(package_files)
  if [[ -e "$SOURCE_ROOT/$PACKAGE_MANIFEST" || -L "$SOURCE_ROOT/$PACKAGE_MANIFEST" ]]; then
    validate_package_manifest "$SOURCE_ROOT" "$SOURCE_ROOT/SKILL.md"
  fi
}

main() {
  local dispatch_root
  parse_args "$@"
  validate_package
  command -v python3 >/dev/null 2>&1 || fail "missing dependency: python3"
  check_default_roots
  acquire_package_lock
  trap release_package_lock EXIT
  validate_migration
  validate_seed_entrypoints
  seed_entrypoints
  migrate_legacy_files
  release_package_lock
  trap - EXIT

  if ((INSTALL_MODE == 0)); then
    [[ "$HARNESS_SELECTION" != all ]] || fail "--harness all requires --install"
    dispatch_root="$(package_root_for "$HARNESS_SELECTION")"
    bash "$dispatch_root/bin/session-log" \
      --entrypoint "$HARNESS_SELECTION" \
      --harness "$HARNESS_SELECTION" \
      --arguments "$LOCAL_ARGUMENTS"
    return
  fi

  printf 'Universal session-log installed for %s.\n' \
    "$([[ "$HARNESS_SELECTION" == all ]] && printf 'Claude Code, Codex, Cursor, OpenCode, and OMP' || printf '%s' "$HARNESS_SELECTION")"
  printf 'Run /session-log on in the current harness; restart it when activation requires it.\n'
  printf 'Logging remains off until explicitly enabled per harness.\n'
}

main "$@"
