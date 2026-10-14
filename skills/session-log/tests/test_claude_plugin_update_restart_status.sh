#!/usr/bin/env bash
# Claude Code restart status across claude-goodies plugin updates: a refresh that changes no
# registration a running session loaded keeps that session's runtime marker, while a VERSION
# bump or a removed settings.json registration still reports "restart required".
# Run: bash skills/session-log/tests/test_claude_plugin_update_restart_status.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
# An arbitrary marketplace id, the same one test_universal_session_log.sh uses.
readonly MARKETPLACE="user538295"
readonly PREVIOUS_PLUGIN_VERSION="1.0.0"
readonly CURRENT_PLUGIN_VERSION="1.0.1"
readonly BUMPED_SESSION_LOG_VERSION="99.0.0"
readonly PREVIOUS_RELEASE_HOOK_LINE="# previous release"
readonly PREVIOUS_SESSION_ID="session-before-plugin-update"
readonly RESTARTED_SESSION_ID="restarted-after-plugin-update"
readonly STANDALONE_SESSION_ID="standalone-session"
readonly OWNER_MARKER="SESSION_LOG_OWNER="
readonly EXPECTED_ON_STATUS="Claude Code: on"
readonly EXPECTED_RESTART_STATUS="Claude Code: on — restart required"
# A standalone copy registers one hook per event: SessionStart, UserPromptSubmit, Stop, SubagentStop.
readonly STANDALONE_REGISTRATIONS=4
readonly DEAD_HOOK_RELATIVE="removed-copy/skills/session-log/adapters/claude/claude_hook.sh"
# A live session's stand-in process outlives the test; cleanup stops it.
readonly LIVE_SESSION_SECONDS=300
readonly MARKER_POLL_ATTEMPTS=100
readonly MARKER_POLL_INTERVAL_SECONDS=0.1
readonly MIN_BASH_MAJOR=4

fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }

for dependency in jq python3; do
  command -v "$dependency" >/dev/null 2>&1 || fail "missing dependency: $dependency"
done
bash_major="$(bash -c 'printf %s "${BASH_VERSINFO[0]}"')"
((bash_major >= MIN_BASH_MAJOR)) ||
  fail "the bash on PATH must be version $MIN_BASH_MAJOR or newer (found $bash_major)"

WORKROOT="$(mktemp -d)"
LIVE_SESSION_PIDS=""
cleanup() {
  local pid
  for pid in $LIVE_SESSION_PIDS; do
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true  # reaps it without bash 3.2's "Terminated" job notice
  done
  if command -v trash >/dev/null 2>&1; then
    trash "$WORKROOT"
  else
    mkdir -p "$HOME/.Trash"
    mv "$WORKROOT" "$HOME/.Trash/session-log-claude-plugin-update-test-$$"
  fi
}
trap cleanup EXIT
WORKROOT_REAL="$(cd "$WORKROOT" && pwd -P)"
TEST_HOME=""
LIVE_SESSION_PID=""

# Each scenario runs in its own fresh HOME.
new_sandbox() {
  TEST_HOME="$WORKROOT_REAL/$1"
  mkdir -p "$TEST_HOME/.claude"
}

state_file() { printf '%s\n' "$TEST_HOME/.claude/session-log/$1"; }

settings_file() { printf '%s\n' "$TEST_HOME/.claude/settings.json"; }

plugin_skill_for() {
  printf '%s\n' "$TEST_HOME/.claude/plugins/cache/$MARKETPLACE/claude-goodies/$1/skills/session-log"
}

standalone_skill() { printf '%s\n' "$TEST_HOME/.claude/skills/session-log"; }

make_plugin_cache() {
  local skill plugin_root
  skill="$(plugin_skill_for "$1")"
  plugin_root="${skill%/skills/session-log}"
  mkdir -p "$plugin_root/skills" "$plugin_root/hooks"
  cp -R "$REPO/skills/session-log" "$skill"
  cp "$REPO/hooks/hooks.json" "$plugin_root/hooks/hooks.json"
}

# Enables the claude-goodies plugin at user scope, so its hooks/hooks.json owns the hooks.
enable_plugin_at_user_scope() {
  local settings
  settings="$(settings_file)"
  [[ -f "$settings" ]] || printf '{}\n' > "$settings"
  jq --arg key "claude-goodies@$MARKETPLACE" '.enabledPlugins[$key] = true' "$settings" > "$settings.new"
  mv "$settings.new" "$settings"
}

# The plugin copy resolves "plugin" mode, not "plugin-dead-only", only while this key is true.
assert_plugin_enabled_at_user_scope() {
  jq -e --arg key "claude-goodies@$MARKETPLACE" '.enabledPlugins[$key] == true' "$(settings_file)" >/dev/null ||
    fail "$1: the plugin is not enabled at user scope"
}

# The skill's shell gets no CLAUDE_* variables; it runs install.sh from the announced BASE.
run_skill() {
  local skill="$1" arguments="$2"
  env -u CLAUDE_PLUGIN_ROOT -u CLAUDE_CONFIG_DIR HOME="$TEST_HOME" \
    bash "$skill/install.sh" --harness claude --arguments "$arguments"
}

# Runs the SessionStart hook of session-log copy $1 for session $2 as Claude Code does. Claude
# Code also exports CLAUDE_PLUGIN_ROOT to a plugin's hook, but claude_hook.sh locates its package
# from its own path, so the test leaves it unset. The hook's parent process, the calling shell,
# stands in for the Claude Code session, so its marker stays live while that shell runs.
start_session() {
  local skill="$1" session_id="$2"
  jq -n --arg session_id "$session_id" --arg cwd "$PWD" '{session_id: $session_id, cwd: $cwd}' |
    env -u CLAUDE_CONFIG_DIR -u CLAUDE_PLUGIN_ROOT HOME="$TEST_HOME" \
      bash "$skill/adapters/claude/claude_hook.sh" session-start >/dev/null
}

assert_last_session() {
  local actual
  actual="$(jq -r .session_id "$(state_file runtime.json)" 2>/dev/null || true)"
  [[ "$actual" == "$1" ]] || fail "last runtime marker (expected session: $1; actual: $actual)"
}

# A session whose Claude Code process has since exited: the hook's parent is a subshell that exits.
start_exited_session() {
  local pid
  (start_session "$@")
  assert_last_session "$2"
  pid="$(jq -r .pid "$(state_file runtime.json)")"
  ! kill -0 "$pid" 2>/dev/null || fail "exited session's process $pid is still running"
}

# A session that stays live in its own process (LIVE_SESSION_PID), so its runtime.<pid>.json
# marker is told apart from that of a session the test shell starts later.
start_live_session() {
  local attempt
  (start_session "$@"; exec sleep "$LIVE_SESSION_SECONDS") >/dev/null &
  LIVE_SESSION_PID=$!
  LIVE_SESSION_PIDS="$LIVE_SESSION_PIDS $LIVE_SESSION_PID"
  for ((attempt = 0; attempt < MARKER_POLL_ATTEMPTS; attempt++)); do
    [[ ! -f "$(state_file "runtime.$LIVE_SESSION_PID.json")" ]] || return 0
    sleep "$MARKER_POLL_INTERVAL_SECONDS"
  done
  fail "live session $2 wrote no runtime marker"
}

assert_skill_output() {
  local label="$1" skill="$2" arguments="$3" expected="$4" actual
  actual="$(run_skill "$skill" "$arguments")"
  [[ "$actual" == "$expected" ]] || fail "$label (expected: $expected; actual: $actual)"
}

assert_status() { assert_skill_output "$1" "$2" status "$3"; }

# `on` prints any repair report before its status line, so only that last line is compared.
assert_on_status() {
  local label="$1" skill="$2" expected="$3" actual
  actual="$(run_skill "$skill" on)"
  [[ "${actual##*$'\n'}" == "$expected" ]] || fail "$label (expected last line: $expected; actual: $actual)"
}

# The report `repair` prints after removing $1 settings.json registrations.
repair_removed_report() {
  printf 'Claude Code: removed %s duplicate or stale hook registration(s) from %s\n' "$1" "$(settings_file)"
}

# A removed settings.json registration stops the session that loaded it, so its markers must go.
assert_runtime_markers_cleared() {
  local marker
  for marker in "$TEST_HOME/.claude/session-log"/runtime*.json; do
    [[ ! -e "$marker" ]] || fail "$1: runtime marker kept: $marker"
  done
}

sha256_of() {
  python3 -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$1"
}

# Parses the manifest itself rather than reusing lib/adapter_install.sh's manifest_asset_hash, so a
# bug there cannot hide from this check.
manifest_hook_hash() {
  awk '$0 == "asset=hook" { if (getline && $0 ~ /^sha256=/) { sub(/^sha256=/, ""); print; exit } }' \
    "$(state_file adapter.manifest)"
}

manifest_version() {
  awk -F= '$1 == "version" { print $2; exit }' "$(state_file adapter.manifest)"
}

# True while settings.json registers the standalone copy's hook.
standalone_registered() {
  grep -qF "$(standalone_skill)/adapters/claude/claude_hook.sh" "$(settings_file)"
}

# Installs a standalone copy.
copy_standalone() {
  mkdir -p "$TEST_HOME/.claude/skills"
  cp -R "$REPO/skills/session-log" "$(standalone_skill)"
}

# Turns logging on through the standalone copy, which registers its settings.json hooks.
activate_standalone() {
  assert_skill_output "$1: standalone activation" "$(standalone_skill)" on "$EXPECTED_RESTART_STATUS"
  standalone_registered || fail "$1: standalone on did not register settings.json hooks"
}

# A live session (LIVE_SESSION_PID) loaded the standalone copy's settings.json hooks; then Claude
# Code installed the plugin and enabled it at user scope, so the plugin copy removes those hooks.
live_standalone_session_then_plugin_enabled() {
  local label="$1"
  copy_standalone
  activate_standalone "$label"
  start_live_session "$(standalone_skill)" "$STANDALONE_SESSION_ID"
  assert_status "$label: standalone status with a live session" "$(standalone_skill)" "$EXPECTED_ON_STATUS"
  make_plugin_cache "$CURRENT_PLUGIN_VERSION"
  enable_plugin_at_user_scope
}

# A plugin copy's command removed the live standalone registration, so the running session
# stopped logging and its runtime markers must be gone.
assert_live_registration_removed() {
  local label="$1"
  ! grep -q "$OWNER_MARKER" "$(settings_file)" ||
    fail "$label: the standalone settings.json hooks were kept"
  assert_runtime_markers_cleared "$label"
}

# The previous release ships a claude_hook.sh whose bytes differ from the current one under the
# same session-log VERSION (as v1.18.2 -> v1.18.3 did). After Claude Code updates the plugin and
# is fully restarted, only the restarted session is alive, so status reports "on" only if the
# adapter refresh it triggers keeps that session's marker.
assert_restart_after_same_version_hook_change() {
  local label="$1" previous current
  previous="$(plugin_skill_for "$PREVIOUS_PLUGIN_VERSION")"
  current="$(plugin_skill_for "$CURRENT_PLUGIN_VERSION")"
  make_plugin_cache "$PREVIOUS_PLUGIN_VERSION"
  printf '%s\n' "$PREVIOUS_RELEASE_HOOK_LINE" >> "$previous/adapters/claude/claude_hook.sh"
  assert_skill_output "$label: activation" "$previous" on "$EXPECTED_RESTART_STATUS"
  start_exited_session "$previous" "$PREVIOUS_SESSION_ID"

  make_plugin_cache "$CURRENT_PLUGIN_VERSION"
  start_session "$current" "$RESTARTED_SESSION_ID"
  assert_last_session "$RESTARTED_SESSION_ID"
  [[ "$(manifest_hook_hash)" != "$(sha256_of "$current/adapters/claude/claude_hook.sh")" ]] ||
    fail "$label: manifest already matches the updated hook before status"

  assert_status "$label: post-restart status after plugin update" "$current" "$EXPECTED_ON_STATUS"
  [[ "$(manifest_hook_hash)" == "$(sha256_of "$current/adapters/claude/claude_hook.sh")" ]] ||
    fail "$label: status did not refresh the adapter manifest to the updated hook"
}

label="plugin mode: restart after same-VERSION hook change reports on"
new_sandbox plugin-same-version
enable_plugin_at_user_scope
assert_restart_after_same_version_hook_change "$label"
assert_plugin_enabled_at_user_scope "$label"
printf 'PASS: %s\n' "$label"

# A plugin copy enabled only at project, local, or managed scope (not in user enabledPlugins): a
# live standalone registration still serves other projects, so it is neither removable nor a
# reason to clear the restarted session's marker.
label="plugin-dead-only mode: restart after same-VERSION hook change reports on"
new_sandbox plugin-dead-only-same-version
copy_standalone
activate_standalone "$label"
assert_restart_after_same_version_hook_change "$label"
standalone_registered || fail "$label: the live standalone settings.json registration was removed"
printf 'PASS: %s\n' "$label"

# A plugin update that bumps VERSION while the pre-update session still runs the old hooks.
label="plugin update with VERSION bump before restart reports restart required"
new_sandbox plugin-version-bump
enable_plugin_at_user_scope
make_plugin_cache "$PREVIOUS_PLUGIN_VERSION"
run_skill "$(plugin_skill_for "$PREVIOUS_PLUGIN_VERSION")" on >/dev/null
start_session "$(plugin_skill_for "$PREVIOUS_PLUGIN_VERSION")" "$PREVIOUS_SESSION_ID"
assert_last_session "$PREVIOUS_SESSION_ID"
assert_status "$label: status before the update" "$(plugin_skill_for "$PREVIOUS_PLUGIN_VERSION")" "$EXPECTED_ON_STATUS"
make_plugin_cache "$CURRENT_PLUGIN_VERSION"
printf '%s\n' "$BUMPED_SESSION_LOG_VERSION" > "$(plugin_skill_for "$CURRENT_PLUGIN_VERSION")/VERSION"
assert_status "$label" "$(plugin_skill_for "$CURRENT_PLUGIN_VERSION")" "$EXPECTED_RESTART_STATUS"
[[ "$(manifest_version)" == "$BUMPED_SESSION_LOG_VERSION" ]] ||
  fail "$label: status did not refresh the adapter manifest to the bumped VERSION"
printf 'PASS: %s\n' "$label"

# The plugin copy's status refreshes the adapter (install_adapter), whose removal of the live
# registration clears the markers.
label="plugin status after removing a live settings.json registration reports restart required"
new_sandbox standalone-to-plugin
live_standalone_session_then_plugin_enabled "$label"
assert_status "$label" "$(plugin_skill_for "$CURRENT_PLUGIN_VERSION")" "$EXPECTED_RESTART_STATUS"
assert_live_registration_removed "$label"
printf 'PASS: %s\n' "$label"

# The plugin copy's `on` removes the live registration in repair_registrations, which clears the
# markers, before install_adapter refreshes the stale manifest (the plugin's hook differs from
# the standalone copy's under the same VERSION) and finds nothing left to remove.
label="plugin on after removing a live settings.json registration reports restart required"
new_sandbox plugin-on
live_standalone_session_then_plugin_enabled "$label"
printf '%s\n' "$PREVIOUS_RELEASE_HOOK_LINE" >> "$(plugin_skill_for "$CURRENT_PLUGIN_VERSION")/adapters/claude/claude_hook.sh"
[[ "$(manifest_hook_hash)" != "$(sha256_of "$(plugin_skill_for "$CURRENT_PLUGIN_VERSION")/adapters/claude/claude_hook.sh")" ]] ||
  fail "$label: manifest already matches the plugin's hook before on"
assert_on_status "$label" "$(plugin_skill_for "$CURRENT_PLUGIN_VERSION")" "$EXPECTED_RESTART_STATUS"
assert_live_registration_removed "$label"
printf 'PASS: %s\n' "$label"

# The plugin copy's `repair` removes the live registration; the next status must report it.
label="status after plugin repair removed a live settings.json registration reports restart required"
new_sandbox plugin-repair
live_standalone_session_then_plugin_enabled "$label"
assert_skill_output "$label: repair report" "$(plugin_skill_for "$CURRENT_PLUGIN_VERSION")" repair \
  "$(repair_removed_report "$STANDALONE_REGISTRATIONS")"
assert_live_registration_removed "$label"
assert_status "$label" "$(plugin_skill_for "$CURRENT_PLUGIN_VERSION")" "$EXPECTED_RESTART_STATUS"
printf 'PASS: %s\n' "$label"

# A dead registration (its hook script is gone) cannot have kept a session logging, yet removing
# it clears the markers too: the deliberate conservative choice.
for command in status repair; do
  label="plugin $command after removing only a dead settings.json registration reports restart required"
  new_sandbox "plugin-dead-registration-$command"
  enable_plugin_at_user_scope
  make_plugin_cache "$CURRENT_PLUGIN_VERSION"
  plugin="$(plugin_skill_for "$CURRENT_PLUGIN_VERSION")"
  run_skill "$plugin" on >/dev/null
  start_session "$plugin" "$RESTARTED_SESSION_ID"
  assert_status "$label: status with a live session" "$plugin" "$EXPECTED_ON_STATUS"
  dead_hook="$TEST_HOME/$DEAD_HOOK_RELATIVE"
  jq --arg command "bash $dead_hook stop" '.hooks.Stop = [{hooks: [{type: "command", command: $command}]}]' \
    "$(settings_file)" > "$(settings_file).new"
  mv "$(settings_file).new" "$(settings_file)"
  if [[ "$command" == repair ]]; then
    assert_skill_output "$label: repair report" "$plugin" repair "$(repair_removed_report 1)"
  else
    assert_status "$label: status" "$plugin" "$EXPECTED_RESTART_STATUS"
  fi
  ! grep -qF "$dead_hook" "$(settings_file)" || fail "$label: the dead registration was kept"
  assert_runtime_markers_cleared "$label"
  assert_status "$label" "$plugin" "$EXPECTED_RESTART_STATUS"
  printf 'PASS: %s\n' "$label"
done

# A new session starts through the plugin copy's SessionStart hook, whose repair removes the live
# standalone registration and clears the older session's markers before the hook writes the new
# session's marker, so status reports the new session as current.
label="plugin session start after removing a live settings.json registration reports on"
new_sandbox plugin-session-start
live_standalone_session_then_plugin_enabled "$label"
start_session "$(plugin_skill_for "$CURRENT_PLUGIN_VERSION")" "$RESTARTED_SESSION_ID"
! standalone_registered || fail "$label: the standalone settings.json hooks were kept"
[[ ! -e "$(state_file "runtime.$LIVE_SESSION_PID.json")" ]] || fail "$label: the older session's runtime marker was kept"
assert_last_session "$RESTARTED_SESSION_ID"
assert_status "$label" "$(plugin_skill_for "$CURRENT_PLUGIN_VERSION")" "$EXPECTED_ON_STATUS"
printf 'PASS: %s\n' "$label"
