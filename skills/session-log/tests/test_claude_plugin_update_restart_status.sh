#!/usr/bin/env bash
# Claude Code restart status with plugin-owned hooks: the claude-goodies plugin's hooks/hooks.json
# always registers the session-log hooks, which stay no-ops until the enable flag exists. `on` runs
# the legacy cleanup and sets that flag, `off` removes it and clears the runtime markers, `status`
# only reads, and removing a legacy settings.json registration never clears the runtime marker of a
# session the plugin's hooks keep logging.
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
readonly LIVE_SESSION_ID="live-session"
readonly EXPECTED_OFF_STATUS="Claude Code: off"
readonly EXPECTED_ON_STATUS="Claude Code: on"
readonly EXPECTED_RESTART_STATUS="Claude Code: on — restart required"
readonly EXPECTED_FIRST_ON_STATUS="Claude Code: on — logging starts with the next prompt"
# Older releases registered one settings.json hook per event: SessionStart, UserPromptSubmit, Stop, SubagentStop.
readonly LEGACY_REGISTRATIONS=4
readonly LEGACY_HOOK_RELATIVE=".claude/skills/session-log/adapters/claude/claude_hook.sh"
readonly SEEDED_COPY_RELATIVE=".claude/skills/session-log"
readonly SEEDED_MANIFEST=".universal-session-log.manifest"
# State files the adapter installer of older releases wrote; plugin-owned hooks need none of them.
readonly ADAPTER_STATE_FILES="adapter.version adapter.manifest desired.version"
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

flag_file() { printf '%s\n' "$TEST_HOME/.claude/prompt-logs/.enabled"; }

plugin_skill_for() {
  printf '%s\n' "$TEST_HOME/.claude/plugins/cache/$MARKETPLACE/claude-goodies/$1/skills/session-log"
}

make_plugin_cache() {
  local skill plugin_root
  skill="$(plugin_skill_for "$1")"
  plugin_root="${skill%/skills/session-log}"
  mkdir -p "$plugin_root/skills" "$plugin_root/hooks"
  cp -R "$REPO/skills/session-log" "$skill"
  cp "$REPO/hooks/hooks.json" "$plugin_root/hooks/hooks.json"
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

# `on` prints any cleanup report before its status line, so only that last line is compared.
assert_on_status() {
  local label="$1" skill="$2" expected="$3" actual
  actual="$(run_skill "$skill" on)"
  [[ "${actual##*$'\n'}" == "$expected" ]] || fail "$label (expected last line: $expected; actual: $actual)"
}

# The report a command prints after removing $1 legacy settings.json registrations.
legacy_removed_report() {
  printf 'Claude Code: removed %s legacy session-log hook registration(s) from %s\n' "$1" "$(settings_file)"
}

# What older releases left in settings.json: one owned registration per event for the copy
# they seeded under ~/.claude/skills, beside a hook that is not session-log's.
write_legacy_registrations() {
  local settings hook
  settings="$(settings_file)"
  hook="$TEST_HOME/$LEGACY_HOOK_RELATIVE"
  [[ -f "$settings" ]] || printf '{}\n' > "$settings"
  jq --arg hook "$hook" '
    def owned($name): {type: "command", command: ("SESSION_LOG_OWNER=universal-session-log bash " + $hook + " " + $name)};
    .hooks.SessionStart = [{matcher: "startup|resume", hooks: [owned("session-start")]}]
    | .hooks.UserPromptSubmit = [{hooks: [owned("user-prompt")]}]
    | .hooks.Stop = [{hooks: [owned("stop"), {type: "command", command: "cc-status stop"}]}]
    | .hooks.SubagentStop = [{hooks: [owned("subagent-stop")]}]
  ' "$settings" > "$settings.new"
  mv "$settings.new" "$settings"
}

seeded_copy() { printf '%s\n' "$TEST_HOME/$SEEDED_COPY_RELATIVE"; }

# What older install.sh releases left under ~/.claude/skills: a copy of the package whose ownership
# manifest lists the SHA-256 of every file in it.
seed_legacy_copy() {
  local copy
  copy="$(seeded_copy)"
  mkdir -p "${copy%/session-log}"
  cp -R "$REPO/skills/session-log" "$copy"
  python3 - "$copy" "$SEEDED_MANIFEST" <<'PY'
import hashlib
import os
import sys

root, manifest = sys.argv[1:]
lines = ["format=1", "owner=universal-session-log", "version=2.0.0"]
for directory, _, names in sorted(os.walk(root)):
    for name in sorted(names):
        path = os.path.join(directory, name)
        with open(path, "rb") as handle:
            lines += [f"asset={os.path.relpath(path, root)}", f"sha256={hashlib.sha256(handle.read()).hexdigest()}"]
with open(os.path.join(root, manifest), "w", encoding="utf-8") as handle:
    handle.write("\n".join(lines) + "\n")
PY
}

assert_legacy_registrations_removed() {
  ! grep -q "SESSION_LOG_OWNER=" "$(settings_file)" || fail "$1: a legacy settings.json registration was kept"
  grep -q "cc-status stop" "$(settings_file)" || fail "$1: a hook that is not session-log's was removed"
}

assert_no_adapter_state() {
  local name
  for name in $ADAPTER_STATE_FILES; do
    [[ ! -e "$(state_file "$name")" ]] || fail "$1: wrote adapter state $name"
  done
}

# A fingerprint of everything a read-only command must leave alone: settings.json and the state directory.
claude_state_fingerprint() {
  {
    cat "$(settings_file)" 2>/dev/null || printf 'no settings.json\n'
    find "$TEST_HOME/.claude/session-log" -type f -exec cksum {} + 2>/dev/null | sort || true
  }
}

label="on flips only the enable flag"
new_sandbox on-flag-only
make_plugin_cache "$CURRENT_PLUGIN_VERSION"
plugin="$(plugin_skill_for "$CURRENT_PLUGIN_VERSION")"
printf '{"model": "opus"}\n' > "$(settings_file)"
cp "$(settings_file)" "$WORKROOT_REAL/on-flag-only.settings"
assert_skill_output "$label: first on" "$plugin" on "$EXPECTED_FIRST_ON_STATUS"
[[ -f "$(flag_file)" ]] || fail "$label: on did not create the enable flag"
cmp -s "$WORKROOT_REAL/on-flag-only.settings" "$(settings_file)" || fail "$label: on changed settings.json"
assert_no_adapter_state "$label"
# The flag was already on, yet no hook of a running session has written its marker: the
# plugin's hooks are not loaded in this session.
assert_skill_output "$label: repeated on without a live session" "$plugin" on "$EXPECTED_RESTART_STATUS"
printf 'PASS: %s\n' "$label"

label="on writes no settings.json when there is none"
new_sandbox on-without-settings
make_plugin_cache "$CURRENT_PLUGIN_VERSION"
assert_skill_output "$label" "$(plugin_skill_for "$CURRENT_PLUGIN_VERSION")" on "$EXPECTED_FIRST_ON_STATUS"
[[ ! -e "$(settings_file)" ]] || fail "$label: on created settings.json"
printf 'PASS: %s\n' "$label"

label="status only reads"
new_sandbox status-read-only
make_plugin_cache "$CURRENT_PLUGIN_VERSION"
plugin="$(plugin_skill_for "$CURRENT_PLUGIN_VERSION")"
assert_status "$label: before on" "$plugin" "$EXPECTED_OFF_STATUS"
[[ ! -e "$TEST_HOME/.claude/session-log" ]] || fail "$label: status created the state directory"
run_skill "$plugin" on >/dev/null
start_live_session "$plugin" "$LIVE_SESSION_ID"
before="$(claude_state_fingerprint)"
assert_status "$label: with a live session" "$plugin" "$EXPECTED_ON_STATUS"
[[ "$(claude_state_fingerprint)" == "$before" ]] || fail "$label: status changed settings.json or the state directory"
assert_no_adapter_state "$label"
printf 'PASS: %s\n' "$label"

label="on reports on while a hook of a running session has written its marker"
assert_on_status "$label" "$plugin" "$EXPECTED_ON_STATUS"
printf 'PASS: %s\n' "$label"

label="off turns logging off and clears runtime markers"
before="$(cat "$(settings_file)" 2>/dev/null || printf 'no settings.json\n')"
assert_skill_output "$label" "$plugin" off "$EXPECTED_OFF_STATUS"
[[ ! -e "$(flag_file)" ]] || fail "$label: off kept the enable flag"
[[ "$(cat "$(settings_file)" 2>/dev/null || printf 'no settings.json\n')" == "$before" ]] ||
  fail "$label: off changed settings.json"
for marker in "$TEST_HOME/.claude/session-log"/runtime*.json; do
  [[ ! -e "$marker" ]] || fail "$label: off kept runtime marker $marker"
done
assert_status "$label: status after off" "$plugin" "$EXPECTED_OFF_STATUS"
# Within the same live session the hooks rewrite their marker at the next prompt.
assert_skill_output "$label: on again in the live session" "$plugin" on "$EXPECTED_FIRST_ON_STATUS"
printf 'PASS: %s\n' "$label"

# Bug: removing a duplicate settings.json registration cleared the runtime markers although the
# plugin's own hooks kept logging, so status claimed a restart was required.
for command in repair on; do
  label="$command removes legacy settings.json registrations and keeps the live session's marker"
  new_sandbox "legacy-registrations-$command"
  make_plugin_cache "$CURRENT_PLUGIN_VERSION"
  plugin="$(plugin_skill_for "$CURRENT_PLUGIN_VERSION")"
  run_skill "$plugin" on >/dev/null
  start_live_session "$plugin" "$LIVE_SESSION_ID"
  write_legacy_registrations
  if [[ "$command" == repair ]]; then
    assert_skill_output "$label: repair report" "$plugin" repair "$(legacy_removed_report "$LEGACY_REGISTRATIONS")"
  else
    actual="$(run_skill "$plugin" on)"
    [[ "$actual" == "$(legacy_removed_report "$LEGACY_REGISTRATIONS")"$'\n'"$EXPECTED_ON_STATUS" ]] ||
      fail "$label: on output (actual: $actual)"
  fi
  assert_legacy_registrations_removed "$label"
  [[ -f "$(state_file "runtime.$LIVE_SESSION_PID.json")" ]] || fail "$label: the live session's runtime marker was cleared"
  assert_status "$label" "$plugin" "$EXPECTED_ON_STATUS"
  printf 'PASS: %s\n' "$label"
done

label="session start removes legacy settings.json registrations and keeps other sessions' markers"
new_sandbox legacy-registrations-session-start
make_plugin_cache "$CURRENT_PLUGIN_VERSION"
plugin="$(plugin_skill_for "$CURRENT_PLUGIN_VERSION")"
run_skill "$plugin" on >/dev/null
start_live_session "$plugin" "$LIVE_SESSION_ID"
write_legacy_registrations
start_session "$plugin" "$RESTARTED_SESSION_ID"
assert_legacy_registrations_removed "$label"
[[ -f "$(state_file "runtime.$LIVE_SESSION_PID.json")" ]] || fail "$label: the older live session's runtime marker was cleared"
assert_last_session "$RESTARTED_SESSION_ID"
assert_status "$label" "$plugin" "$EXPECTED_ON_STATUS"
printf 'PASS: %s\n' "$label"

# Bug: older releases seeded a copy under ~/.claude/skills that went stale after a plugin update; its
# own on/status re-registered settings.json hooks that the plugin's session start removed again.
label="plugin session start removes the stale seeded copy and its registrations"
new_sandbox seeded-copy-session-start
make_plugin_cache "$CURRENT_PLUGIN_VERSION"
plugin="$(plugin_skill_for "$CURRENT_PLUGIN_VERSION")"
run_skill "$plugin" on >/dev/null
seed_legacy_copy
write_legacy_registrations
start_session "$plugin" "$RESTARTED_SESSION_ID"
[[ ! -e "$(seeded_copy)" ]] || fail "$label: the seeded copy was kept"
assert_legacy_registrations_removed "$label"
assert_status "$label" "$plugin" "$EXPECTED_ON_STATUS"
printf 'PASS: %s\n' "$label"

label="plugin session start removes a stale seeded copy that left no registration"
new_sandbox seeded-copy-without-registrations
make_plugin_cache "$CURRENT_PLUGIN_VERSION"
seed_legacy_copy
start_session "$(plugin_skill_for "$CURRENT_PLUGIN_VERSION")" "$RESTARTED_SESSION_ID"
[[ ! -e "$(seeded_copy)" ]] || fail "$label: the seeded copy was kept"
printf 'PASS: %s\n' "$label"

label="on removes the stale seeded copy and reports it"
new_sandbox seeded-copy-on
make_plugin_cache "$CURRENT_PLUGIN_VERSION"
seed_legacy_copy
actual="$(run_skill "$(plugin_skill_for "$CURRENT_PLUGIN_VERSION")" on)"
[[ "$actual" == "Claude Code: removed legacy package copy $(seeded_copy)"$'\n'"$EXPECTED_FIRST_ON_STATUS" ]] ||
  fail "$label (actual: $actual)"
[[ ! -e "$(seeded_copy)" ]] || fail "$label: the seeded copy was kept"
printf 'PASS: %s\n' "$label"

label="repair keeps a seeded copy holding a changed file and says so"
new_sandbox seeded-copy-changed
make_plugin_cache "$CURRENT_PLUGIN_VERSION"
seed_legacy_copy
printf '# changed by the user\n' >> "$(seeded_copy)/SKILL.md"
assert_skill_output "$label" "$(plugin_skill_for "$CURRENT_PLUGIN_VERSION")" repair \
  "Claude Code: kept legacy package copy $(seeded_copy): SKILL.md was changed; move it aside"
[[ -f "$(seeded_copy)/SKILL.md" ]] || fail "$label: the changed copy was removed"
printf 'PASS: %s\n' "$label"

# The previous release ships a claude_hook.sh whose bytes differ from the current one under the
# same session-log VERSION. After Claude Code updates the plugin and is fully restarted, only the
# restarted session is alive, and its marker is all status needs.
label="restart after a same-VERSION plugin update reports on"
new_sandbox plugin-same-version
make_plugin_cache "$PREVIOUS_PLUGIN_VERSION"
previous="$(plugin_skill_for "$PREVIOUS_PLUGIN_VERSION")"
printf '%s\n' "$PREVIOUS_RELEASE_HOOK_LINE" >> "$previous/adapters/claude/claude_hook.sh"
run_skill "$previous" on >/dev/null
start_exited_session "$previous" "$PREVIOUS_SESSION_ID"
make_plugin_cache "$CURRENT_PLUGIN_VERSION"
current="$(plugin_skill_for "$CURRENT_PLUGIN_VERSION")"
start_session "$current" "$RESTARTED_SESSION_ID"
assert_last_session "$RESTARTED_SESSION_ID"
assert_status "$label" "$current" "$EXPECTED_ON_STATUS"
printf 'PASS: %s\n' "$label"

# A plugin update that bumps VERSION while the pre-update session still runs the old hooks.
label="plugin update with VERSION bump before restart reports restart required"
new_sandbox plugin-version-bump
make_plugin_cache "$PREVIOUS_PLUGIN_VERSION"
run_skill "$(plugin_skill_for "$PREVIOUS_PLUGIN_VERSION")" on >/dev/null
start_session "$(plugin_skill_for "$PREVIOUS_PLUGIN_VERSION")" "$PREVIOUS_SESSION_ID"
assert_last_session "$PREVIOUS_SESSION_ID"
assert_status "$label: status before the update" "$(plugin_skill_for "$PREVIOUS_PLUGIN_VERSION")" "$EXPECTED_ON_STATUS"
make_plugin_cache "$CURRENT_PLUGIN_VERSION"
printf '%s\n' "$BUMPED_SESSION_LOG_VERSION" > "$(plugin_skill_for "$CURRENT_PLUGIN_VERSION")/VERSION"
assert_status "$label" "$(plugin_skill_for "$CURRENT_PLUGIN_VERSION")" "$EXPECTED_RESTART_STATUS"
printf 'PASS: %s\n' "$label"
