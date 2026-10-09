#!/usr/bin/env bash
# Behavioral tests for the universal session-log package.
# Run: bash skills/session-log/tests/test_universal_session_log.sh
set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
INSTALL="$REPO/install-universal-session-log.sh"
PACKAGE_VERSION="$(tr -d '[:space:]' < "$REPO/skills/session-log/VERSION")"
FAIL=0
WORKROOT="$(mktemp -d)"
trap 'cd /; mv "$WORKROOT" "$HOME/.Trash/universal-session-log-test-$$" 2>/dev/null || true' EXIT

fail() { printf 'FAIL: %s\n' "$1"; FAIL=1; }
pass() { printf 'PASS: %s\n' "$1"; }
assert_contains() {
  local label="$1" needle="$2" haystack="$3"
  if [[ "$haystack" == *"$needle"* ]]; then pass "$label"; else fail "$label (missing: $needle; actual: $haystack)"; fi
}
assert_not_contains() {
  local label="$1" needle="$2" haystack="$3"
  if [[ "$haystack" != *"$needle"* ]]; then pass "$label"; else fail "$label (unexpected: $needle)"; fi
}
assert_exact() {
  local label="$1" expected="$2" actual="$3"
  [[ "$actual" == "$expected" ]] && pass "$label" || fail "$label (expected: $expected; actual: $actual)"
}
assert_file() { local label="$1" file="$2"; [[ -f "$file" ]] && pass "$label" || fail "$label (missing $file)"; }
assert_not_file() { local label="$1" file="$2"; [[ ! -e "$file" && ! -L "$file" ]] && pass "$label" || fail "$label (present $file)"; }
assert_mode() {
  local label="$1" expected="$2" file="$3" actual
  actual="$(stat -f '%Lp' "$file" 2>/dev/null || stat -c '%a' "$file" 2>/dev/null)"
  [[ "$actual" == "$expected" ]] && pass "$label" || fail "$label (expected $expected, got $actual)"
}
assert_link() {
  local label="$1" expected="$2" file="$3" actual
  actual="$(readlink "$file" 2>/dev/null || true)"
  [[ -L "$file" && "$actual" == "$expected" ]] && pass "$label" || fail "$label (expected link $expected, got $actual)"
}
package_root_for() {
  case "$1" in
    claude) printf '%s\n' "$TEST_HOME/.claude/skills/session-log" ;;
    codex) printf '%s\n' "$TEST_HOME/.codex/skills/session-log" ;;
    cursor) printf '%s\n' "$TEST_HOME/.cursor/skills/session-log" ;;
    opencode) printf '%s\n' "$TEST_HOME/.config/opencode/skills/session-log" ;;
    omp) printf '%s\n' "$TEST_HOME/.omp/agent/skills/session-log" ;;
    *) fail "unsupported test harness: $1" ;;
  esac
}
run_session_log() {
  HOME="$TEST_HOME" "$(package_root_for claude)/bin/session-log" "$@"
}
run_harness() {
  local harness="$1"
  shift
  HOME="$TEST_HOME" "$(package_root_for "$harness")/bin/session-log" \
    --entrypoint "$harness" --harness "$harness" "$@"
}
run_harness_at_home() {
  local home="$1"
  local harness="$2"
  shift 2
  HOME="$home" "$(package_root_for "$harness")/bin/session-log" \
    --entrypoint "$harness" --harness "$harness" "$@"
}
run_harness_at_home_and_dir() {
  local home="$1"
  local cwd="$2"
  local harness="$3"
  shift 3
  (
    cd "$cwd"
    HOME="$home" "$(package_root_for "$harness")/bin/session-log" \
      --entrypoint "$harness" --harness "$harness" "$@"
  )
}
run_skill_entrypoint() {
  local skill_file="$1"
  local home="$2"
  local harness="$3"
  local arguments="${4:-status}"
  local script skill_dir escaped_harness escaped_dir escaped_arguments line rewritten=""
  script="$(awk '/^```bash$/ { capture = 1; next } capture && /^```$/ { exit } capture { print }' "$skill_file")"
  skill_dir="$(cd "$(dirname "$skill_file")" && pwd -P)"
  printf -v escaped_harness '%q' "$harness"
  printf -v escaped_dir '%q' "$skill_dir"
  printf -v escaped_arguments '%q' "$arguments"
  while IFS= read -r line; do
    case "$line" in
      "harness='<claude|codex|cursor|opencode|omp: choose the current host>'") line="harness=$escaped_harness" ;;
      "BASE='<the skill directory announced by the current host, or empty>'") line="BASE=$escaped_dir" ;;
      "arguments='<exact invocation arguments, or status>'") line="arguments=$escaped_arguments" ;;
    esac
    rewritten+="${rewritten:+$'\n'}$line"
  done <<< "$script"
  script="$rewritten"
  HOME="$home" SESSION_LOG_HOME="${SESSION_LOG_HOME:-}" bash -c "$script"
}


TEST_HOME="$WORKROOT/home"
mkdir -p "$TEST_HOME/.Trash"

printf '=== install copies complete packages without enabling logging ===\n'
HOME="$TEST_HOME" bash "$INSTALL" >"$WORKROOT/install.out" 2>"$WORKROOT/install.err"
assert_file "Claude package entrypoint seeded" "$TEST_HOME/.claude/skills/session-log/SKILL.md"
assert_file "Claude package installer seeded" "$TEST_HOME/.claude/skills/session-log/install.sh"
assert_file "Claude package CLI seeded" "$TEST_HOME/.claude/skills/session-log/bin/session-log"
assert_file "Claude package hook seeded" "$TEST_HOME/.claude/skills/session-log/adapters/claude/claude_hook.sh"
assert_file "Codex package entrypoint seeded" "$TEST_HOME/.codex/skills/session-log/SKILL.md"
assert_file "Codex package native hook seeded" "$TEST_HOME/.codex/skills/session-log/adapters/native/session_log_hook.py"
assert_file "Cursor package entrypoint seeded" "$TEST_HOME/.cursor/skills/session-log/SKILL.md"
assert_file "Cursor package native hook seeded" "$TEST_HOME/.cursor/skills/session-log/adapters/native/session_log_hook.py"
assert_file "OpenCode package entrypoint seeded" "$TEST_HOME/.config/opencode/skills/session-log/SKILL.md"
assert_file "OpenCode package adapter seeded" "$TEST_HOME/.config/opencode/skills/session-log/adapters/opencode/session-log.js"
assert_file "OMP package entrypoint seeded" "$TEST_HOME/.omp/agent/skills/session-log/SKILL.md"
assert_file "OMP package adapter seeded" "$TEST_HOME/.omp/agent/skills/session-log/adapters/omp/session-log.js"
assert_file "OpenCode slash command seeded" "$TEST_HOME/.config/opencode/commands/session-log.md"
assert_not_file "global CLI wrapper is not installed" "$TEST_HOME/.local/bin/session-log"
assert_not_file "global runtime release is not installed" "$TEST_HOME/.local/share/universal-session-log"
assert_not_file "Claude logging remains disabled" "$TEST_HOME/.claude/prompt-logs/.enabled"
assert_not_file "Codex logging remains disabled" "$TEST_HOME/.codex/prompt-logs/.enabled"
assert_not_file "Cursor logging remains disabled" "$TEST_HOME/.cursor/prompt-logs/.enabled"
assert_not_file "OpenCode logging remains disabled" "$TEST_HOME/.config/opencode/prompt-logs/.enabled"
assert_not_file "OMP logging remains disabled" "$TEST_HOME/.omp/agent/prompt-logs/.enabled"
assert_contains "install reports all harnesses" "Claude Code" "$(cat "$WORKROOT/install.out")"
assert_contains "install reports all harnesses" "Codex" "$(cat "$WORKROOT/install.out")"
assert_contains "install reports all harnesses" "Cursor" "$(cat "$WORKROOT/install.out")"
assert_contains "install reports all harnesses" "OpenCode" "$(cat "$WORKROOT/install.out")"
assert_contains "install reports all harnesses" "OMP" "$(cat "$WORKROOT/install.out")"
for harness in claude codex cursor opencode omp; do
  package_root="$(package_root_for "$harness")"
  while IFS= read -r relative; do
    assert_file "$harness package asset installed: $relative" "$package_root/$relative"
  done <<'EOF'
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
done
initial_claude_status="$(run_harness claude status 2>&1)"
assert_exact "initial Claude status is exact" "Claude Code: off" "$initial_claude_status"

printf '=== source skill honors the current host harness ===\n'
for host in omp cursor codex; do
  case "$host" in
    omp)
      host_label="OMP"
      host_skill_parent=".omp/agent/skills"
      ;;
    cursor)
      host_label="Cursor"
      host_skill_parent=".cursor/skills"
      ;;
    codex)
      host_label="Codex"
      host_skill_parent=".codex/skills"
      ;;
  esac
  host_home="$WORKROOT/$host-source-home"
  host_skill_root="$host_home/$host_skill_parent/session-log"
  mkdir -p "$host_home/.Trash" "$host_home/$host_skill_parent"
  cp -R "$REPO/skills/session-log" "$host_skill_root"
  host_status="$(run_skill_entrypoint "$host_skill_root/SKILL.md" "$host_home" "$host" status 2>&1)"
  assert_exact "$host_label execution of source skill reports $host_label status" \
    "$host_label: off" "$host_status"
done

printf '=== source skill finds the newest omp marketplace package ===\n'
OMP_MARKET_HOME="$WORKROOT/omp-marketplace-home"
OMP_MARKET_CACHE="$OMP_MARKET_HOME/.omp/plugins/cache/plugins"
mkdir -p "$OMP_MARKET_HOME/.Trash" "$OMP_MARKET_HOME/announced"
for market_version in 1.9.0 1.10.0; do
  mkdir -p "$OMP_MARKET_CACHE/user538295___omp-goodies___$market_version/skills"
  cp -R "$REPO/skills/session-log" "$OMP_MARKET_CACHE/user538295___omp-goodies___$market_version/skills/session-log"
done
printf '#!/usr/bin/env bash\nprintf "stale package\\n"; exit 1\n' \
  > "$OMP_MARKET_CACHE/user538295___omp-goodies___1.9.0/skills/session-log/install.sh"
cp "$REPO/skills/session-log/SKILL.md" "$OMP_MARKET_HOME/announced/SKILL.md"
omp_market_status="$(cd "$OMP_MARKET_HOME" && run_skill_entrypoint "$OMP_MARKET_HOME/announced/SKILL.md" "$OMP_MARKET_HOME" omp status 2>&1)"
assert_exact "OMP skill falls back to the newest omp marketplace package" "OMP: off" "$omp_market_status"

printf '=== source skill finds the newest Codex marketplace package ===\n'
CODEX_MARKET_HOME="$WORKROOT/codex-marketplace-home"
CODEX_MARKET_CACHE="$CODEX_MARKET_HOME/.codex/plugins/cache/user538295/codex-goodies"
mkdir -p "$CODEX_MARKET_HOME/.Trash" "$CODEX_MARKET_HOME/announced"
for market_version in 1.9.0 1.10.0; do
  mkdir -p "$CODEX_MARKET_CACHE/$market_version/skills"
  cp -R "$REPO/skills/session-log" "$CODEX_MARKET_CACHE/$market_version/skills/session-log"
done
printf '#!/usr/bin/env bash\nprintf "stale package\\n"; exit 1\n' \
  > "$CODEX_MARKET_CACHE/1.9.0/skills/session-log/install.sh"
cp "$REPO/skills/session-log/SKILL.md" "$CODEX_MARKET_HOME/announced/SKILL.md"
codex_market_status="$(cd "$CODEX_MARKET_HOME" && run_skill_entrypoint "$CODEX_MARKET_HOME/announced/SKILL.md" "$CODEX_MARKET_HOME" codex status 2>&1)"
assert_exact "Codex skill falls back to the newest Codex marketplace package" "Codex: off" "$codex_market_status"

printf '=== source skill finds the most recently installed Cursor and OpenCode plugin packages ===\n'
for host in cursor opencode; do
  case "$host" in
    cursor)
      host_label="Cursor"
      market_root=".cursor/plugins/cache/user538295/cursor-goodies"
      stale_package="ffff/skills/session-log"
      fresh_package="0000/skills/session-log"
      ;;
    opencode)
      host_label="OpenCode"
      market_root=".cache/opencode/npm/git-harness-goodies-abc"
      stale_package="2/node_modules/opencode-goodies/skills/session-log"
      fresh_package="1/node_modules/opencode-goodies/skills/session-log"
      ;;
  esac
  market_home="$WORKROOT/$host-marketplace-home"
  mkdir -p "$market_home/.Trash" "$market_home/announced" \
    "$(dirname "$market_home/$market_root/$stale_package")" "$(dirname "$market_home/$market_root/$fresh_package")"
  cp -R "$REPO/skills/session-log" "$market_home/$market_root/$stale_package"
  printf '#!/usr/bin/env bash\nprintf "stale package\\n"; exit 1\n' > "$market_home/$market_root/$stale_package/install.sh"
  touch -t 202001010000 "$market_home/$market_root/$stale_package"
  cp -R "$REPO/skills/session-log" "$market_home/$market_root/$fresh_package"
  cp "$REPO/skills/session-log/SKILL.md" "$market_home/announced/SKILL.md"
  market_status="$(cd "$market_home" && run_skill_entrypoint "$market_home/announced/SKILL.md" "$market_home" "$host" status 2>&1)"
  assert_exact "$host_label skill falls back to the most recently installed plugin package" "$host_label: off" "$market_status"
done

printf '=== source installer repairs a previously enabled OpenCode entrypoint ===\n'
OPENCODE_RECOVERY_HOME="$WORKROOT/opencode-recovery-home"
OPENCODE_DEAD_PACKAGE="$OPENCODE_RECOVERY_HOME/.claude/plugins/cache/user538295/claude-goodies/1.12.1/skills/session-log"
mkdir -p "$OPENCODE_RECOVERY_HOME/.Trash" \
  "$OPENCODE_RECOVERY_HOME/.config/opencode/skills" \
  "$OPENCODE_RECOVERY_HOME/.config/opencode/plugins" \
  "$OPENCODE_RECOVERY_HOME/.config/opencode/prompt-logs"
ln -s "$OPENCODE_DEAD_PACKAGE" \
  "$OPENCODE_RECOVERY_HOME/.config/opencode/skills/session-log"
ln -s "$OPENCODE_DEAD_PACKAGE/adapters/opencode/session-log.js" \
  "$OPENCODE_RECOVERY_HOME/.config/opencode/plugins/session-log.js"
touch "$OPENCODE_RECOVERY_HOME/.config/opencode/prompt-logs/.enabled"
opencode_recovery_status="$(
  HOME="$OPENCODE_RECOVERY_HOME" \
    bash "$REPO/skills/session-log/install.sh" --harness opencode --arguments status 2>&1
)"
assert_exact "OpenCode source invocation restores an enabled adapter" \
  "OpenCode: on — restart required" "$opencode_recovery_status"
assert_file "OpenCode source invocation installs its package" \
  "$OPENCODE_RECOVERY_HOME/.config/opencode/skills/session-log/install.sh"
assert_link "OpenCode source invocation replaces the dead plugin link" \
  "$(cd "$OPENCODE_RECOVERY_HOME" && pwd -P)/.config/opencode/skills/session-log/adapters/opencode/session-log.js" \
  "$OPENCODE_RECOVERY_HOME/.config/opencode/plugins/session-log.js"
assert_file "OpenCode source invocation restores adapter.version" \
  "$OPENCODE_RECOVERY_HOME/.config/opencode/session-log/adapter.version"
assert_file "OpenCode source invocation restores adapter.manifest" \
  "$OPENCODE_RECOVERY_HOME/.config/opencode/session-log/adapter.manifest"

printf '=== identity is explicit and strict ===\n'
set +e
missing_identity="$(run_session_log status 2>&1)"
missing_rc=$?
unknown_identity="$(run_session_log --entrypoint unknown --harness unknown status 2>&1)"
unknown_rc=$?
set -e
[[ "$missing_rc" -ne 0 ]] && pass "missing harness identity fails" || fail "missing harness identity fails"
assert_contains "missing identity is actionable" "active harness identity is required" "$missing_identity"
[[ "$unknown_rc" -ne 0 ]] && pass "unknown harness identity fails" || fail "unknown harness identity fails"
assert_contains "unknown identity lists supported harnesses" "claude, codex, cursor, opencode, omp" "$unknown_identity"
set +e
trailing_status="$(run_harness claude status unexpected 2>&1)"
trailing_status_rc=$?
set -e
[[ "$trailing_status_rc" -ne 0 ]] && pass "direct status rejects trailing arguments" || fail "direct status rejects trailing arguments"
assert_contains "trailing argument error is explicit" "trailing arguments are not valid for status" "$trailing_status"
set +e
unknown_before_command="$(run_harness claude --bogus status 2>&1)"
unknown_before_command_rc=$?
set -e
[[ "$unknown_before_command_rc" -ne 0 ]] && pass "unknown CLI argument before command fails" || fail "unknown CLI argument before command fails"
assert_contains "unknown CLI argument error names the argument" "unknown argument: --bogus" "$unknown_before_command"



printf '=== each native adapter installs lazily ===\n'
OPENCODE_HOME="$WORKROOT/opencode-home"
OMP_HOME="$WORKROOT/omp-home"
mkdir -p "$OPENCODE_HOME/.Trash" "$OMP_HOME/.Trash"
opencode_on="$(run_harness_at_home "$OPENCODE_HOME" opencode on 2>&1)"
omp_on="$(run_harness_at_home "$OMP_HOME" omp on 2>&1)"
assert_exact "OpenCode on status is exact" "OpenCode: on — restart required" "$opencode_on"
assert_exact "OMP on status is exact" "OMP: on — restart required" "$omp_on"
assert_file "OpenCode flag created" "$OPENCODE_HOME/.config/opencode/prompt-logs/.enabled"
assert_file "OpenCode plugin installed" "$OPENCODE_HOME/.config/opencode/plugins/session-log.js"
assert_file "OMP flag created" "$OMP_HOME/.omp/agent/prompt-logs/.enabled"
assert_file "OMP extension installed" "$OMP_HOME/.omp/agent/extensions/session-log.js"
assert_file "OMP usage parser installed" "$OMP_HOME/.omp/agent/skills/session-log/scripts/session_log_usage.ts"
set +e
opencode_plugin_smoke="$(
  HOME="$OPENCODE_HOME" SESSION_LOG_PLUGIN="$OPENCODE_HOME/.config/opencode/plugins/session-log.js" \
    bun --eval 'const loaded = await import(process.env.SESSION_LOG_PLUGIN); const hooks = await loaded.SessionLogPlugin({ directory: "fixture" }); process.stdout.write(Object.keys(hooks).sort().join(","));' \
    2>&1
)"
opencode_plugin_smoke_rc=$?
omp_plugin_smoke="$(
  HOME="$OMP_HOME" OMP_SESSION_LOG_PLUGIN="$OMP_HOME/.omp/agent/extensions/session-log.js" \
    bun --eval 'const events = []; const pi = { registerCommand: (name) => events.push(`command:${name}`), on: (name) => events.push(name) }; const loaded = await import(process.env.OMP_SESSION_LOG_PLUGIN); loaded.default(pi); process.stdout.write(events.join(","));' \
    2>&1
)"
omp_plugin_smoke_rc=$?
set -e
[[ "$opencode_plugin_smoke_rc" -eq 0 ]] && pass "OpenCode plugin lifecycle API loads" || fail "OpenCode plugin lifecycle API loads (actual: $opencode_plugin_smoke)"
assert_exact "OpenCode plugin registers native handlers" "chat.message,event" "$opencode_plugin_smoke"
assert_file "OpenCode plugin lifecycle writes runtime state" "$OPENCODE_HOME/.config/opencode/session-log/runtime.json"
[[ "$omp_plugin_smoke_rc" -eq 0 ]] && pass "OMP extension lifecycle API loads" || fail "OMP extension lifecycle API loads (actual: $omp_plugin_smoke)"
assert_exact "OMP extension registers native handlers" "command:session-log,session_start,session_shutdown,before_agent_start,agent_end,session_stop" "$omp_plugin_smoke"
assert_file "OMP extension lifecycle writes runtime state" "$OMP_HOME/.omp/agent/session-log/runtime.json"

CURSOR_HOME="$WORKROOT/cursor-home"
CODEX_HOME="$WORKROOT/codex-home"
mkdir -p "$CURSOR_HOME/.Trash" "$CURSOR_HOME/.cursor" "$CODEX_HOME/.Trash" "$CODEX_HOME/.codex"
printf '%s\n' '{"version":1,"hooks":{"afterFileEdit":[{"command":"echo keep-cursor"}]}}' > "$CURSOR_HOME/.cursor/hooks.json"
printf '%s\n' '{"hooks":{"PostToolUse":[{"hooks":[{"type":"command","command":"echo keep-codex"}]}]}}' > "$CODEX_HOME/.codex/hooks.json"
cursor_on="$(HOME="$CURSOR_HOME" bash "$REPO/skills/session-log/install.sh" --harness cursor --arguments on 2>&1)"
codex_on="$(HOME="$CODEX_HOME" bash "$REPO/skills/session-log/install.sh" --harness codex --arguments on 2>&1)"
assert_exact "Cursor on status is exact" "Cursor: on — restart required" "$cursor_on"
assert_exact "Codex on reports trust review honestly" "Codex: on — trust review/restart required" "$codex_on"
assert_file "Cursor native hooks config installed" "$CURSOR_HOME/.cursor/hooks.json"
assert_file "Codex native hooks config installed" "$CODEX_HOME/.codex/hooks.json"
assert_contains "Cursor preserves user hooks" "keep-cursor" "$(cat "$CURSOR_HOME/.cursor/hooks.json")"
assert_contains "Codex preserves user hooks" "keep-codex" "$(cat "$CODEX_HOME/.codex/hooks.json")"
assert_contains "Cursor installs prompt hook" "beforeSubmitPrompt" "$(cat "$CURSOR_HOME/.cursor/hooks.json")"
assert_contains "Cursor installs response hook" "afterAgentResponse" "$(cat "$CURSOR_HOME/.cursor/hooks.json")"
assert_contains "Cursor installs subagent hook" "subagentStop" "$(cat "$CURSOR_HOME/.cursor/hooks.json")"
assert_contains "Codex installs prompt hook" "UserPromptSubmit" "$(cat "$CODEX_HOME/.codex/hooks.json")"
assert_contains "Codex installs stop hook" "\"Stop\"" "$(cat "$CODEX_HOME/.codex/hooks.json")"
assert_contains "Codex installs subagent hook" "SubagentStop" "$(cat "$CODEX_HOME/.codex/hooks.json")"
CURSOR_HOOK="$CURSOR_HOME/.cursor/skills/session-log/adapters/native/session_log_hook.py"
CODEX_HOOK="$CODEX_HOME/.codex/skills/session-log/adapters/native/session_log_hook.py"
set +e
unknown_native_event="$(
  printf '%s\n' '{"conversation_id":"cursor-session"}' |
    HOME="$CURSOR_HOME" python3 "$CURSOR_HOOK" cursor unknown-event 2>&1
)"
unknown_native_event_rc=$?
set -e
[[ "$unknown_native_event_rc" -ne 0 ]] && pass "native hook rejects unknown lifecycle events" || fail "native hook rejects unknown lifecycle events"
assert_contains "unknown native event error is explicit" "unknown cursor lifecycle event" "$unknown_native_event"
cursor_prompt_hook_output="$(
  printf '%s\n' '{"conversation_id":"cursor-session","generation_id":"cursor-generation","model":"fixture","workspace_roots":["/tmp/cursor-project"],"prompt":"cursor prompt"}' |
    HOME="$CURSOR_HOME" python3 "$CURSOR_HOOK" cursor user-prompt
)"
assert_exact "Cursor prompt hook explicitly continues submission" '{"continue":true}' "$cursor_prompt_hook_output"
printf '%s\n' '{"conversation_id":"cursor-session","generation_id":"cursor-generation","model":"fixture","workspace_roots":["/tmp/cursor-project"],"text":"cursor response"}' |
  HOME="$CURSOR_HOME" python3 "$CURSOR_HOOK" cursor assistant-response >/dev/null
printf '%s\n' '{"conversation_id":"cursor-session","generation_id":"cursor-generation","model":"fixture","workspace_roots":["/tmp/cursor-project"],"subagent_type":"explore","summary":"cursor child"}' |
  HOME="$CURSOR_HOME" python3 "$CURSOR_HOOK" cursor subagent-stop >/dev/null
printf '%s\n' '{"session_id":"codex-session","cwd":"/tmp/codex-project","model":"fixture","prompt":"codex prompt"}' |
  HOME="$CODEX_HOME" python3 "$CODEX_HOOK" codex user-prompt >/dev/null
printf '%s\n' '{"session_id":"codex-session","cwd":"/tmp/codex-project","model":"fixture","last_assistant_message":"codex response"}' |
  HOME="$CODEX_HOME" python3 "$CODEX_HOOK" codex stop >/dev/null
printf '%s\n' '{"session_id":"codex-session","cwd":"/tmp/codex-project","model":"fixture","agent_type":"explore","last_assistant_message":"codex child"}' |
  HOME="$CODEX_HOME" python3 "$CODEX_HOOK" codex subagent-stop >/dev/null
cursor_status="$(HOME="$CURSOR_HOME" bash "$CURSOR_HOME/.cursor/skills/session-log/install.sh" --harness cursor --arguments status 2>&1)"
codex_status="$(HOME="$CODEX_HOME" bash "$CODEX_HOME/.codex/skills/session-log/install.sh" --harness codex --arguments status 2>&1)"
assert_exact "Cursor status becomes on after native hook smoke" "Cursor: on" "$cursor_status"
assert_exact "Codex status becomes on after native hook smoke" "Codex: on" "$codex_status"
cursor_log_path=("$CURSOR_HOME/.cursor/prompt-logs/"*/session_cursor-session.md)
codex_log_path=("$CODEX_HOME/.codex/prompt-logs/"*/session_codex-session.md)
cursor_log="$(cat "${cursor_log_path[0]}")"
codex_log="$(cat "${codex_log_path[0]}")"
assert_contains "Cursor native hook logs prompts" "cursor prompt" "$cursor_log"
assert_contains "Cursor native hook logs responses" "cursor response" "$cursor_log"
assert_contains "Cursor native hook logs subagents" "cursor child" "$cursor_log"
assert_contains "Codex native hook logs prompts" "codex prompt" "$codex_log"
assert_contains "Codex native hook logs responses" "codex response" "$codex_log"
assert_contains "Codex native hook logs subagents" "codex child" "$codex_log"
printf '%s\n' '{"conversation_id":"cursor-one","workspace_roots":["/tmp/one/shared"],"prompt":"first shared project"}' |
  HOME="$CURSOR_HOME" python3 "$CURSOR_HOOK" cursor user-prompt >/dev/null
printf '%s\n' '{"conversation_id":"cursor-two","workspace_roots":["/tmp/two/shared"],"prompt":"second shared project"}' |
  HOME="$CURSOR_HOME" python3 "$CURSOR_HOOK" cursor user-prompt >/dev/null
cursor_one_path=("$CURSOR_HOME/.cursor/prompt-logs/"*/session_cursor-one.md)
cursor_two_path=("$CURSOR_HOME/.cursor/prompt-logs/"*/session_cursor-two.md)
[[ "${cursor_one_path[0]%/*}" != "${cursor_two_path[0]%/*}" ]] &&
  pass "native logs keep same-named projects separate" ||
  fail "native logs keep same-named projects separate"
long_cursor_id="$(printf '%0300d' 0 | tr '0' 'a')"
set +e
long_cursor_output="$(
  printf '{"conversation_id":"%s","workspace_roots":["/tmp/cursor-project"],"prompt":"invalid id"}\n' "$long_cursor_id" |
    HOME="$CURSOR_HOME" python3 "$CURSOR_HOOK" cursor user-prompt 2>&1
)"
long_cursor_rc=$?
set -e
[[ "$long_cursor_rc" -eq 0 ]] && pass "native hook ignores overlong session IDs safely" || fail "native hook ignores overlong session IDs safely"
assert_exact "overlong Cursor session still allows the prompt" '{"continue":true}' "$long_cursor_output"
assert_exact "overlong Cursor session creates no log" "" \
  "$(find "$CURSOR_HOME/.cursor/prompt-logs" -name "session_${long_cursor_id}.md" -print)"
set +e
home_unset_output="$(
  printf '%s\n' '{"session_id":"codex-session","cwd":"/tmp/codex-project","last_assistant_message":"leak"}' |
    HOME="" python3 "$CODEX_HOOK" codex stop 2>&1
)"
home_unset_rc=$?
set -e
[[ "$home_unset_rc" -ne 0 ]] && pass "native hook fails when HOME is unset" || fail "native hook fails when HOME is unset"
assert_contains "native hook HOME error is explicit" "HOME is not set" "$home_unset_output"
assert_exact "native hook writes no log under cwd when HOME unset" "" \
  "$(find "$PWD/.codex/prompt-logs" -name 'session_codex-session.md' -print 2>/dev/null)"
printf '{"conversation_id":"cursor-usage","generation_id":"cursor-usage-generation","workspace_roots":["%s"],"input_tokens":5,"output_tokens":2,"cache_read_tokens":3,"cache_write_tokens":1,"status":"completed"}\n' "$PWD" |
  HOME="$CURSOR_HOME" python3 "$CURSOR_HOOK" cursor stop >/dev/null
cursor_usage="$(run_skill_entrypoint "$CURSOR_HOME/.cursor/skills/session-log/SKILL.md" "$CURSOR_HOME" cursor 'usage --latest' 2>&1)"
assert_contains "source skill forwards usage --latest" "Cursor: usage" "$cursor_usage"
assert_not_contains "Cursor usage no longer claims tokens are unavailable" "native token usage unavailable" "$cursor_usage"
assert_contains "Cursor usage reports stop hook totals" "TOTAL (1 requests, 0 sub-agents)" "$cursor_usage"
assert_contains "Cursor usage uses the Claude est. line" \
  'est. used token: input: 1, output: 2, cache_create: 1, cache_read: 3, total_tokens: 7, price: $0.00, model: ?, effort: unknown' "$cursor_usage"
set +e
cursor_tokenless="$(run_harness_at_home "$CURSOR_HOME" cursor usage cursor-session 2>&1)"
cursor_tokenless_rc=$?
set -e
[[ "$cursor_tokenless_rc" -eq 0 ]] && pass "Cursor usage reports a prompt without stop tokens" || fail "Cursor usage reports a prompt without stop tokens"
assert_contains "Cursor tokenless turn has an empty est. line" \
  'est. used token: input: 0, output: 0, cache_create: 0, cache_read: 0, total_tokens: 0, price: $0.00, model: -, effort: -' "$cursor_tokenless"
set +e
codex_check="$(HOME="$CODEX_HOME" bash "$CODEX_HOME/.codex/skills/session-log/install.sh" --harness codex --arguments 'usage --check' 2>&1)"
codex_check_rc=$?
set -e
[[ "$codex_check_rc" -ne 0 ]] && pass "Codex usage rejects Claude-only --check" || fail "Codex usage rejects Claude-only --check"
assert_contains "Codex --check rejection is explicit" "--check is only valid for Claude Code" "$codex_check"


printf '=== first on installs only the selected adapter and requires restart ===\n'
claude_on="$(run_harness claude on 2>&1)"
assert_exact "Claude on status is exact" "Claude Code: on — restart required" "$claude_on"
assert_file "Claude flag created" "$TEST_HOME/.claude/prompt-logs/.enabled"
assert_mode "Claude flag is private" "600" "$TEST_HOME/.claude/prompt-logs/.enabled"
assert_file "Claude adapter state created" "$TEST_HOME/.claude/session-log/adapter.version"
assert_not_file "OpenCode adapter remains absent from Claude activation" "$TEST_HOME/.config/opencode/session-log/adapter.version"
assert_not_file "OMP adapter remains absent from Claude activation" "$TEST_HOME/.omp/agent/session-log/adapter.version"
assert_file "Claude hooks settings created" "$TEST_HOME/.claude/settings.json"
assert_contains "Claude settings preserve owned hook marker" "universal-session-log" "$(cat "$TEST_HOME/.claude/settings.json")"
CLAUDE_HOOK="$TEST_HOME/.claude/skills/session-log/adapters/claude/claude_hook.sh"
printf '%s\n' '{"session_id":"claude-runtime","cwd":"'"$PWD"'"}' | HOME="$TEST_HOME" bash "$CLAUDE_HOOK" session-start >/dev/null
claude_status="$(run_harness claude status 2>&1)"
assert_exact "Claude loaded status is exact" "Claude Code: on" "$claude_status"
assert_contains "Claude runtime records package version" "\"version\": \"$PACKAGE_VERSION\"" "$(cat "$TEST_HOME/.claude/session-log/runtime.json")"
printf '0.0.0\n' > "$TEST_HOME/.claude/session-log/adapter.version"
claude_update="$(run_harness claude on 2>&1)"
assert_exact "outdated Claude adapter status is exact" "Claude Code: on — restart required" "$claude_update"
assert_contains "outdated Claude adapter is updated" "$PACKAGE_VERSION" "$(cat "$TEST_HOME/.claude/session-log/adapter.version")"

printf '=== off is idempotent and does not install an absent adapter ===\n'
OFF_HOME="$WORKROOT/off-home"
mkdir -p "$OFF_HOME/.Trash"
off_output="$(HOME="$OFF_HOME" "$TEST_HOME/.config/opencode/skills/session-log/bin/session-log" --entrypoint opencode --harness opencode off 2>&1)"
assert_exact "off status is exact" "OpenCode: off" "$off_output"
assert_not_file "off does not install adapter" "$OFF_HOME/.config/opencode/session-log/adapter.version"

printf '=== unsupported relocated roots fail without mutation ===\n'
set +e
relocated="$(XDG_CONFIG_HOME="$WORKROOT/relocated" run_harness opencode on 2>&1)"
relocated_rc=$?
set -e
[[ "$relocated_rc" -ne 0 ]] && pass "relocated OpenCode root fails" || fail "relocated OpenCode root fails"
assert_contains "relocated root error is explicit" "relocated" "$relocated"
assert_not_file "relocated root is not mutated" "$WORKROOT/relocated/prompt-logs/.enabled"

INSTALL_ROOT_HOME="$WORKROOT/install-root-home"
mkdir -p "$INSTALL_ROOT_HOME/.Trash"
set +e
install_root_error="$(HOME="$INSTALL_ROOT_HOME" XDG_CONFIG_HOME="$WORKROOT/custom-config" bash "$INSTALL" 2>&1)"
install_root_rc=$?
set -e
[[ "$install_root_rc" -ne 0 ]] && pass "installer rejects relocated roots" || fail "installer rejects relocated roots"
assert_contains "installer relocation error is explicit" "custom roots are unsupported" "$install_root_error"
assert_not_file "installer relocation leaves package absent" "$INSTALL_ROOT_HOME/.config/opencode/skills/session-log/SKILL.md"

CODEX_ROOT_HOME="$WORKROOT/codex-root-home"
mkdir -p "$CODEX_ROOT_HOME/.Trash"
set +e
codex_root_error="$(
  HOME="$CODEX_ROOT_HOME" CODEX_HOME="$WORKROOT/custom-codex" \
    bash "$REPO/skills/session-log/install.sh" --install --harness codex 2>&1
)"
codex_root_rc=$?
set -e
[[ "$codex_root_rc" -ne 0 ]] && pass "installer rejects relocated Codex root" || fail "installer rejects relocated Codex root"
assert_contains "Codex relocation error is explicit" "custom roots are unsupported" "$codex_root_error"
assert_not_file "Codex relocation leaves package absent" "$CODEX_ROOT_HOME/.codex/skills/session-log/SKILL.md"

PREFLIGHT_HOME="$WORKROOT/preflight-home"
PREFLIGHT_DEAD_PACKAGE="$PREFLIGHT_HOME/.claude/plugins/cache/user538295/claude-goodies/1.12.1/skills/session-log"
mkdir -p "$PREFLIGHT_HOME/.Trash" "$PREFLIGHT_HOME/.claude/skills" \
  "$PREFLIGHT_HOME/.codex/skills/session-log"
ln -s "$PREFLIGHT_DEAD_PACKAGE" "$PREFLIGHT_HOME/.claude/skills/session-log"
printf 'user-owned Codex skill\n' > "$PREFLIGHT_HOME/.codex/skills/session-log/SKILL.md"
set +e
preflight_error="$(HOME="$PREFLIGHT_HOME" bash "$INSTALL" 2>&1)"
preflight_rc=$?
set -e
[[ "$preflight_rc" -ne 0 ]] && pass "all-harness preflight rejects an unowned package" || fail "all-harness preflight rejects an unowned package"
assert_contains "all-harness preflight names the unowned package" "unowned file" "$preflight_error"
assert_link "failed preflight preserves an earlier managed dangling link" \
  "$PREFLIGHT_DEAD_PACKAGE" "$PREFLIGHT_HOME/.claude/skills/session-log"


UNOWNED_HOME="$WORKROOT/unowned-home"
mkdir -p "$UNOWNED_HOME/.Trash" "$UNOWNED_HOME/.config/opencode/plugins"
printf 'user plugin\n' > "$UNOWNED_HOME/.config/opencode/plugins/session-log.js"
set +e
unowned_error="$(HOME="$UNOWNED_HOME" "$TEST_HOME/.config/opencode/skills/session-log/bin/session-log" --entrypoint opencode --harness opencode on 2>&1)"
unowned_rc=$?
set -e
[[ "$unowned_rc" -ne 0 ]] && pass "unowned adapter file is protected" || fail "unowned adapter file is protected"
assert_contains "unowned adapter error is explicit" "unowned adapter file" "$unowned_error"
assert_contains "unowned adapter remains unchanged" "user plugin" "$(cat "$UNOWNED_HOME/.config/opencode/plugins/session-log.js")"

SYMLINK_HOME="$WORKROOT/symlink-home"
mkdir -p "$SYMLINK_HOME/.Trash" "$SYMLINK_HOME/.omp/agent/prompt-logs"
printf 'sentinel\n' > "$WORKROOT/sentinel"
ln -s "$WORKROOT/sentinel" "$SYMLINK_HOME/.omp/agent/prompt-logs/.enabled"
set +e
symlink_error="$(HOME="$SYMLINK_HOME" "$TEST_HOME/.omp/agent/skills/session-log/bin/session-log" --entrypoint omp --harness omp on 2>&1)"
symlink_rc=$?
set -e
[[ "$symlink_rc" -ne 0 ]] && pass "symlinked enable flag is protected" || fail "symlinked enable flag is protected"
assert_contains "symlinked flag error is explicit" "symlinked enable flag" "$symlink_error"
assert_contains "symlink target remains unchanged" "sentinel" "$(cat "$WORKROOT/sentinel")"

MIGRATION_SYMLINK_HOME="$WORKROOT/migration-symlink-home"
mkdir -p "$MIGRATION_SYMLINK_HOME/.Trash" "$MIGRATION_SYMLINK_HOME/.claude"
printf 'settings sentinel\n' > "$WORKROOT/settings-sentinel"
ln -s "$WORKROOT/settings-sentinel" "$MIGRATION_SYMLINK_HOME/.claude/settings.json"
set +e
migration_symlink_error="$(HOME="$MIGRATION_SYMLINK_HOME" bash "$REPO/skills/session-log/install.sh" --install --harness claude 2>&1)"
migration_symlink_rc=$?
set -e
[[ "$migration_symlink_rc" -ne 0 ]] && pass "symlinked migration settings fail safely" || fail "symlinked migration settings fail safely"
assert_contains "symlinked migration error is explicit" "symlinked Claude settings" "$migration_symlink_error"
assert_link "symlinked migration settings remain unchanged" "$WORKROOT/settings-sentinel" "$MIGRATION_SYMLINK_HOME/.claude/settings.json"
assert_contains "symlinked migration target remains unchanged" "settings sentinel" "$(cat "$WORKROOT/settings-sentinel")"
assert_not_file "symlinked migration has no partial package" "$MIGRATION_SYMLINK_HOME/.claude/skills/session-log/SKILL.md"


printf '=== migration removes only known legacy files and preserves settings ===\n'
LEGACY_HOME="$WORKROOT/legacy-home"
mkdir -p "$LEGACY_HOME/.Trash" "$LEGACY_HOME/.omp/agent/skills/session-log-omp/scripts" "$LEGACY_HOME/.omp/agent/extensions" "$LEGACY_HOME/.config/opencode/commands" "$LEGACY_HOME/.config/opencode/plugins" "$LEGACY_HOME/.config/opencode/scripts" "$LEGACY_HOME/.claude/scripts" "$LEGACY_HOME/.claude/skills/session-log"
printf '# universal-session-log: managed\nlegacy\n' > "$LEGACY_HOME/.omp/agent/skills/session-log-omp/SKILL.md"
printf '# universal-session-log: managed\nlegacy\n' > "$LEGACY_HOME/.omp/agent/extensions/session-log-omp.js"
printf '# universal-session-log: managed\nlegacy\n' > "$LEGACY_HOME/.config/opencode/commands/session-log-omp.md"
printf '# universal-session-log: managed\nlegacy\n' > "$LEGACY_HOME/.config/opencode/plugins/session-log-omp.js"
printf '# universal-session-log: managed\nlegacy\n' > "$LEGACY_HOME/.config/opencode/plugins/session-log.js"
printf '# universal-session-log: managed\nlegacy\n' > "$LEGACY_HOME/.config/opencode/scripts/session_log_usage.sh"
for legacy_script in prompt_log_save.sh prompt_log_new_session.sh prompt_log_stop.sh prompt_log_subagent.sh prompt_log_lib.sh prompt_log_usage.sh prompt_log_usage.jq prompt_log_prices.json; do

  printf '# universal-session-log: managed\nlegacy\n' > "$LEGACY_HOME/.claude/scripts/$legacy_script"
done

SELECTIVE_HOME="$WORKROOT/selective-migration-home"
mkdir -p "$SELECTIVE_HOME/.Trash" "$SELECTIVE_HOME/.config/opencode/plugins" \
  "$SELECTIVE_HOME/.claude/scripts" "$SELECTIVE_HOME/.omp/agent/extensions"
printf '# universal-session-log: managed\nlegacy OpenCode\n' > "$SELECTIVE_HOME/.config/opencode/plugins/session-log.js"
printf 'unselected Claude legacy\n' > "$SELECTIVE_HOME/.claude/scripts/prompt_log_save.sh"
printf 'unselected OMP legacy\n' > "$SELECTIVE_HOME/.omp/agent/extensions/session-log-omp.js"
HOME="$SELECTIVE_HOME" bash "$REPO/skills/session-log/install.sh" --install --harness opencode >/dev/null 2>&1
assert_not_file "selective migration removes selected OpenCode legacy" "$SELECTIVE_HOME/.config/opencode/plugins/session-log.js"
assert_file "selective migration preserves unselected Claude legacy" "$SELECTIVE_HOME/.claude/scripts/prompt_log_save.sh"
assert_file "selective migration preserves unselected OMP legacy" "$SELECTIVE_HOME/.omp/agent/extensions/session-log-omp.js"
printf '# universal-session-log: managed\nlegacy prompt_log_usage.sh command\n' > "$LEGACY_HOME/.claude/skills/session-log/SKILL.md"
printf '%s\n' "{\"hooks\":{\"SessionEnd\":[{\"hooks\":[{\"type\":\"command\",\"command\":\"echo keep-me\"},{\"type\":\"command\",\"command\":\"bash $LEGACY_HOME/.claude/scripts/prompt_log_save.sh\"}]}]}}" > "$LEGACY_HOME/.claude/settings.json"
HOME="$LEGACY_HOME" bash "$INSTALL" >/dev/null 2>&1
assert_not_file "legacy OMP skill removed" "$LEGACY_HOME/.omp/agent/skills/session-log-omp/SKILL.md"
assert_not_file "legacy OMP extension removed" "$LEGACY_HOME/.omp/agent/extensions/session-log-omp.js"
assert_not_file "legacy OpenCode command removed" "$LEGACY_HOME/.config/opencode/commands/session-log-omp.md"
assert_not_file "legacy OpenCode plugin removed" "$LEGACY_HOME/.config/opencode/plugins/session-log-omp.js"
assert_not_file "legacy OpenCode standalone plugin removed" "$LEGACY_HOME/.config/opencode/plugins/session-log.js"
assert_not_file "legacy OpenCode standalone parser removed" "$LEGACY_HOME/.config/opencode/scripts/session_log_usage.sh"
assert_not_file "legacy Claude standalone scripts removed" "$LEGACY_HOME/.claude/scripts/prompt_log_save.sh"
assert_contains "unrelated settings preserved" "keep-me" "$(cat "$LEGACY_HOME/.claude/settings.json")"
assert_contains "legacy Claude skill replaced by managed entrypoint" "universal-session-log: managed" "$(cat "$LEGACY_HOME/.claude/skills/session-log/SKILL.md")"
assert_not_contains "known standalone hook removed" "$LEGACY_HOME/.claude/scripts/prompt_log_save.sh" "$(cat "$LEGACY_HOME/.claude/settings.json")"
UNOWNED_LEGACY_HOME="$WORKROOT/unowned-legacy-home"
mkdir -p "$UNOWNED_LEGACY_HOME/.Trash" "$UNOWNED_LEGACY_HOME/.config/opencode/plugins"
printf 'user plugin\n' > "$UNOWNED_LEGACY_HOME/.config/opencode/plugins/session-log.js"
set +e
unowned_legacy_error="$(HOME="$UNOWNED_LEGACY_HOME" bash "$INSTALL" 2>&1)"
unowned_legacy_rc=$?
set -e
[[ "$unowned_legacy_rc" -ne 0 ]] && pass "unowned legacy collision is protected" || fail "unowned legacy collision is protected"
assert_contains "unowned legacy error is explicit" "unowned legacy path" "$unowned_legacy_error"
assert_contains "unowned legacy file remains unchanged" "user plugin" "$(cat "$UNOWNED_LEGACY_HOME/.config/opencode/plugins/session-log.js")"
assert_not_file "unowned legacy collision leaves package absent" "$UNOWNED_LEGACY_HOME/.config/opencode/skills/session-log/SKILL.md"

HOOK_COLLISION_HOME="$WORKROOT/hook-collision-home"
mkdir -p "$HOOK_COLLISION_HOME/.Trash" "$HOOK_COLLISION_HOME/.claude"
printf '%s\n' '{"hooks":{"SessionEnd":[{"hooks":[{"type":"command","command":"echo prompt_log_save.sh"}]}]}}' > "$HOOK_COLLISION_HOME/.claude/settings.json"
HOME="$HOOK_COLLISION_HOME" bash "$INSTALL" >/dev/null 2>&1
assert_contains "unrelated hook text is preserved" "echo prompt_log_save.sh" "$(cat "$HOOK_COLLISION_HOME/.claude/settings.json")"


MALFORMED_HOME="$WORKROOT/malformed-home"
mkdir -p "$MALFORMED_HOME/.Trash" "$MALFORMED_HOME/.claude/scripts" "$MALFORMED_HOME/.claude"
printf 'legacy\n' > "$MALFORMED_HOME/.claude/scripts/prompt_log_save.sh"
printf '{not-json\n' > "$MALFORMED_HOME/.claude/settings.json"
set +e
malformed_error="$(HOME="$MALFORMED_HOME" bash "$INSTALL" 2>&1)"
malformed_rc=$?
set -e
[[ "$malformed_rc" -ne 0 ]] && pass "malformed settings fail before migration" || fail "malformed settings fail before migration"
assert_file "malformed settings preserve legacy files" "$MALFORMED_HOME/.claude/scripts/prompt_log_save.sh"
assert_contains "malformed settings error names JSON" "JSON" "$malformed_error"
assert_not_file "malformed settings leave package absent" "$MALFORMED_HOME/.claude/skills/session-log/SKILL.md"

printf '=== complete copied packages bootstrap locally ===\n'
PACKAGE_OPENCODE_HOME="$WORKROOT/package-opencode-home"
mkdir -p "$PACKAGE_OPENCODE_HOME/.Trash" "$PACKAGE_OPENCODE_HOME/.config/opencode/skills"
cp -R "$REPO/skills/session-log" "$PACKAGE_OPENCODE_HOME/.config/opencode/skills/session-log"
PACKAGE_OPENCODE_ROOT="$(cd "$PACKAGE_OPENCODE_HOME/.config/opencode/skills/session-log" && pwd -P)"
package_opencode_on="$(HOME="$PACKAGE_OPENCODE_HOME" bash "$PACKAGE_OPENCODE_HOME/.config/opencode/skills/session-log/install.sh" --harness opencode --arguments on 2>&1)"
assert_contains "copied OpenCode package enables logging" "OpenCode: on — restart required" "$package_opencode_on"
assert_link "copied OpenCode package installs its plugin" \
  "$PACKAGE_OPENCODE_ROOT/adapters/opencode/session-log.js" \
  "$PACKAGE_OPENCODE_HOME/.config/opencode/plugins/session-log.js"
assert_not_file "copied OpenCode package does not need global wrapper" "$PACKAGE_OPENCODE_HOME/.local/bin/session-log"
package_opencode_seed="$(HOME="$PACKAGE_OPENCODE_HOME" bash "$PACKAGE_OPENCODE_ROOT/install.sh" --install --harness opencode 2>&1)"
assert_contains "copied v1 package seeds its v2 ownership manifest" \
  "Universal session-log installed for opencode" "$package_opencode_seed"
assert_file "copied v2 package writes its ownership manifest" \
  "$PACKAGE_OPENCODE_ROOT/.universal-session-log.manifest"
mv "$PACKAGE_OPENCODE_ROOT/.universal-session-log.manifest" "$WORKROOT/v1-package.manifest"
package_opencode_reinstall="$(HOME="$PACKAGE_OPENCODE_HOME" bash "$PACKAGE_OPENCODE_ROOT/install.sh" --install --harness opencode 2>&1)"
assert_contains "v1 copied package can reinstall into the v2 manifest format" \
  "Universal session-log installed for opencode" "$package_opencode_reinstall"
assert_file "v1-to-v2 reinstall recreates the ownership manifest" \
  "$PACKAGE_OPENCODE_ROOT/.universal-session-log.manifest"
printf 'tampered package asset\n' > "$PACKAGE_OPENCODE_ROOT/VERSION"
set +e
tampered_package="$(HOME="$PACKAGE_OPENCODE_HOME" bash "$PACKAGE_OPENCODE_ROOT/install.sh" --install --harness opencode 2>&1)"
tampered_package_rc=$?
set -e
[[ "$tampered_package_rc" -ne 0 ]] && pass "tampered copied package asset is rejected" || fail "tampered copied package asset is rejected"
assert_contains "tampered package rejection names the asset" "VERSION" "$tampered_package"


PACKAGE_OMP_HOME="$WORKROOT/package-omp-home"
mkdir -p "$PACKAGE_OMP_HOME/.Trash" "$PACKAGE_OMP_HOME/.omp/agent/skills"
cp -R "$REPO/skills/session-log" "$PACKAGE_OMP_HOME/.omp/agent/skills/session-log"
PACKAGE_OMP_ROOT="$(cd "$PACKAGE_OMP_HOME/.omp/agent/skills/session-log" && pwd -P)"
package_omp_on="$(HOME="$PACKAGE_OMP_HOME" bash "$PACKAGE_OMP_HOME/.omp/agent/skills/session-log/install.sh" --harness omp --arguments on 2>&1)"
assert_contains "copied OMP package enables logging" "OMP: on — restart required" "$package_omp_on"
assert_link "copied OMP package installs its extension" \
  "$PACKAGE_OMP_ROOT/adapters/omp/session-log.js" \
  "$PACKAGE_OMP_HOME/.omp/agent/extensions/session-log.js"

OMP_HANDOFF_HOME="$WORKROOT/omp-handoff-home"
mkdir -p "$OMP_HANDOFF_HOME/.Trash" "$OMP_HANDOFF_HOME/.omp/agent/skills"
cp -R "$REPO/skills/session-log" "$OMP_HANDOFF_HOME/.omp/agent/skills/session-log"
OMP_HANDOFF_ROOT="$OMP_HANDOFF_HOME/.omp/agent/skills/session-log"
HOME="$OMP_HANDOFF_HOME" bash "$OMP_HANDOFF_ROOT/install.sh" --install --harness all >/dev/null
claude_handoff_status="$(
  run_skill_entrypoint "$OMP_HANDOFF_HOME/.claude/skills/session-log/SKILL.md" "$OMP_HANDOFF_HOME" claude status 2>&1
)"
assert_exact "OMP package all-install keeps Claude entrypoint on Claude" \
  "Claude Code: off" "$claude_handoff_status"
PACKAGE_CLAUDE_HOME="$WORKROOT/package-claude-home"
mkdir -p "$PACKAGE_CLAUDE_HOME/.Trash" "$PACKAGE_CLAUDE_HOME/.claude/skills"
cp -R "$REPO/skills/session-log" "$PACKAGE_CLAUDE_HOME/.claude/skills/session-log"
package_claude_on="$(HOME="$PACKAGE_CLAUDE_HOME" bash "$PACKAGE_CLAUDE_HOME/.claude/skills/session-log/install.sh" --harness claude --arguments on 2>&1)"
assert_contains "copied Claude package enables logging" "Claude Code: on — restart required" "$package_claude_on"
assert_file "copied Claude package installs native hooks" "$PACKAGE_CLAUDE_HOME/.claude/settings.json"

PACKAGE_PLUGIN_HOME="$WORKROOT/package-plugin-home"
mkdir -p "$PACKAGE_PLUGIN_HOME/.Trash" "$PACKAGE_PLUGIN_HOME/plugin/skills"
cp -R "$REPO/skills/session-log" "$PACKAGE_PLUGIN_HOME/plugin/skills/session-log"
package_plugin_on="$(HOME="$PACKAGE_PLUGIN_HOME" CLAUDE_PLUGIN_ROOT="$PACKAGE_PLUGIN_HOME/plugin" bash "$PACKAGE_PLUGIN_HOME/plugin/skills/session-log/install.sh" --harness claude --arguments on 2>&1)"
assert_contains "marketplace source bootstraps stable Claude package" "Claude Code: on — restart required" "$package_plugin_on"
assert_file "marketplace source installs the stable package" "$PACKAGE_PLUGIN_HOME/.claude/skills/session-log/install.sh"
assert_file "marketplace source installs stable native hooks" "$PACKAGE_PLUGIN_HOME/.claude/settings.json"
printf '%s\n' '{"session_id":"plugin-package-session","cwd":"'"$PWD"'"}' |
  HOME="$PACKAGE_PLUGIN_HOME" \
  bash "$PACKAGE_PLUGIN_HOME/.claude/skills/session-log/adapters/claude/claude_hook.sh" session-start >/dev/null
package_plugin_status="$(HOME="$PACKAGE_PLUGIN_HOME" bash "$PACKAGE_PLUGIN_HOME/.claude/skills/session-log/install.sh" --harness claude --arguments status 2>&1)"
assert_contains "stable package becomes loaded through stable hook" "Claude Code: on" "$package_plugin_status"

printf '=== native reports keep their raw payload after one harness label ===\n'
set +e
claude_missing="$(run_harness claude usage --latest 2>&1)"
claude_missing_rc=$?
set -e
[[ "$claude_missing_rc" -ne 0 ]] && pass "missing Claude transcript fails directly" || fail "missing Claude transcript fails directly"
assert_contains "Claude usage names harness on failure" "Claude Code: usage" "$claude_missing"

CLAUDE_KEY="$(printf '%s' "$PWD" | sed 's|[/._]|-|g')"
CLAUDE_PROJECT="$TEST_HOME/.claude/projects/$CLAUDE_KEY"
mkdir -p "$CLAUDE_PROJECT"
printf '%s\n' '{"type":"user","timestamp":"2026-08-28T10:00:00.000Z","promptSource":"typed","isMeta":false,"message":{"role":"user","content":"hello"}}' > "$CLAUDE_PROJECT/universal.jsonl"
printf '%s\n' '{"type":"assistant","timestamp":"2026-08-28T10:00:01.000Z","effort":"high","requestId":"req_1","message":{"id":"msg_1","model":"claude-haiku-4-5","role":"assistant","stop_reason":"end_turn","content":[{"type":"text","text":"done"}],"usage":{"input_tokens":1,"output_tokens":2,"cache_read_input_tokens":0,"cache_creation":{"ephemeral_5m_input_tokens":0,"ephemeral_1h_input_tokens":0},"speed":"standard"}}}' >> "$CLAUDE_PROJECT/universal.jsonl"
claude_usage="$(run_harness claude usage --latest 2>&1)"
assert_contains "Claude usage keeps native session output" "session: " "$claude_usage"
assert_contains "Claude usage keeps native total output" "TOTAL" "$claude_usage"
mkdir -p "$CLAUDE_PROJECT/relative"
cp "$CLAUDE_PROJECT/universal.jsonl" "$CLAUDE_PROJECT/relative/transcript.jsonl"
relative_claude_usage="$(run_harness_at_home_and_dir "$TEST_HOME" "$CLAUDE_PROJECT" claude usage relative/transcript.jsonl 2>&1)"
assert_contains "Claude usage accepts one relative path with a slash" "session: relative/transcript.jsonl" "$relative_claude_usage"
assert_contains "relative Claude usage keeps native total output" "TOTAL" "$relative_claude_usage"

set +e
multiple_usage="$(run_harness claude usage --latest "$CLAUDE_PROJECT/universal.jsonl" 2>&1)"
multiple_usage_rc=$?
set -e
[[ "$multiple_usage_rc" -ne 0 ]] && pass "usage rejects multiple transcript targets" || fail "usage rejects multiple transcript targets"
assert_contains "multiple usage target error is explicit" "at most one session transcript target" "$multiple_usage"


mkdir -p "$OPENCODE_HOME/.local/share/opencode"
sqlite3 "$OPENCODE_HOME/.local/share/opencode/opencode.db" <<'SQL'
CREATE TABLE session (id TEXT PRIMARY KEY, parent_id TEXT, time_updated INTEGER, time_created INTEGER, title TEXT);
CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT);
CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT);
INSERT INTO session VALUES ('oc-session', NULL, 2, 1, 'fixture');
INSERT INTO message VALUES ('user-1', 'oc-session', 1, 1, '{"role":"user","time":{"created":1000}}');
INSERT INTO message VALUES ('assistant-1', 'oc-session', 2, 2, '{"role":"assistant","time":{"created":1000,"completed":3000},"tokens":{"input":1,"output":2,"reasoning":0,"cache":{"read":0,"write":0}},"cost":0.01,"modelID":"fixture-model"}');
INSERT INTO part VALUES ('part-1', 'assistant-1', 'oc-session', 2, 2, '{"type":"text","text":"done"}');
SQL
opencode_usage="$(run_harness_at_home "$OPENCODE_HOME" opencode usage --latest 2>&1)"
assert_contains "OpenCode usage names harness" "OpenCode: usage" "$opencode_usage"
assert_contains "OpenCode usage keeps native total output" "TOTAL" "$opencode_usage"

OPENCODE_FAILURE_HOME="$WORKROOT/opencode-failure-home"
mkdir -p "$OPENCODE_FAILURE_HOME/.Trash" "$OPENCODE_FAILURE_HOME/.local/share/opencode"
printf 'not a SQLite database\n' > "$OPENCODE_FAILURE_HOME/.local/share/opencode/opencode.db"
set +e
opencode_failure="$(run_harness_at_home "$OPENCODE_FAILURE_HOME" opencode usage --latest 2>&1)"
opencode_failure_rc=$?
set -e
[[ "$opencode_failure_rc" -ne 0 ]] && pass "OpenCode SQLite failure returns nonzero" || fail "OpenCode SQLite failure returns nonzero"
assert_contains "OpenCode SQLite failure is explicit" "native OpenCode usage failed" "$opencode_failure"
assert_not_contains "OpenCode SQLite failure does not report totals" "TOTAL" "$opencode_failure"
sqlite3 "$OPENCODE_HOME/.local/share/opencode/opencode.db" <<'SQL'
INSERT OR REPLACE INTO part VALUES ('event-main-1', 'event-main-1', 'event-root', 1, 1, '{"type":"text","text":"main response"}');
INSERT OR REPLACE INTO part VALUES ('event-main-2', 'event-main-2', 'event-root', 2, 2, '{"type":"text","text":"main response after child deletion"}');
SQL
opencode_event_output="$(
  HOME="$OPENCODE_HOME" SESSION_LOG_PLUGIN="$OPENCODE_HOME/.config/opencode/plugins/session-log.js" \
    bun --eval '
      const loaded = await import(process.env.SESSION_LOG_PLUGIN);
      const hooks = await loaded.SessionLogPlugin({ directory: "event-fixture" });
      const event = hooks.event;
      await hooks["chat.message"]({ sessionID: "event-root" }, { parts: [{ type: "text", text: "main prompt" }] });
      await event({ event: { type: "session.created", properties: { info: { id: "event-child", parentID: "event-root" } } } });
      const child = { id: "event-child-message", sessionID: "event-child", role: "assistant", mode: "Explore", time: { created: 1000, completed: 3000 }, tokens: { input: 1, output: 2 } };
      await event({ event: { type: "message.updated", properties: { info: child } } });
      await event({ event: { type: "message.updated", properties: { info: child } } });
      const first = { id: "event-main-1", sessionID: "event-root", role: "assistant", modelID: "fixture-model", mode: "standard", time: { created: 1000, completed: 4000 }, tokens: { input: 2, output: 3 } };
      await event({ event: { type: "message.updated", properties: { info: first } } });
      await event({ event: { type: "message.updated", properties: { info: first } } });
      await event({ event: { type: "session.deleted", properties: { info: { id: "event-child" } } } });
      const second = { id: "event-main-2", sessionID: "event-root", role: "assistant", modelID: "fixture-model", mode: "standard", time: { created: 5000, completed: 7000 }, tokens: { input: 2, output: 3 } };
      await event({ event: { type: "message.updated", properties: { info: second } } });
      await event({ event: { type: "message.updated", properties: { info: second } } });
      const fs = await import("node:fs");
      const files = fs.readdirSync(process.env.HOME + "/.config/opencode/prompt-logs/event-fixture").filter(name => name.endsWith(".md"));
      process.stdout.write(fs.readFileSync(process.env.HOME + "/.config/opencode/prompt-logs/event-fixture/" + files[0], "utf8"));
    ' 2>"$WORKROOT/opencode-event.err"
)"
assert_contains "OpenCode logs the main prompt event" "main prompt" "$opencode_event_output"
assert_contains "OpenCode logs the main response event" "main response" "$opencode_event_output"
assert_contains "OpenCode logs the child response event" "sub-agent finished: Explore (event-child)" "$opencode_event_output"
assert_contains "OpenCode keeps main logging after child deletion" "main response after child deletion" "$opencode_event_output"
assert_exact "OpenCode suppresses duplicate main events" "2" \
  "$(printf '%s\n' "$opencode_event_output" | grep -c '^### .* response$')"
assert_exact "OpenCode suppresses duplicate child events" "1" \
  "$(printf '%s\n' "$opencode_event_output" | grep -c 'sub-agent finished: Explore (event-child)')"

set +e
opencode_invalid_id="$(
  HOME="$OPENCODE_HOME" SESSION_LOG_PLUGIN="$OPENCODE_HOME/.config/opencode/plugins/session-log.js" \
    bun --eval '
      const loaded = await import(process.env.SESSION_LOG_PLUGIN);
      const hooks = await loaded.SessionLogPlugin({ directory: "event-fixture" });
      await hooks.event({ event: { type: "message.updated", properties: { info: { id: "escape", sessionID: "../outside", role: "assistant", time: { created: 1, completed: 2 } } } } });
    ' 2>&1
)"
opencode_invalid_id_rc=$?
set -e
[[ "$opencode_invalid_id_rc" -eq 0 ]] && pass "OpenCode ignores invalid session IDs safely" || fail "OpenCode ignores invalid session IDs safely"
assert_not_file "OpenCode invalid session ID creates no escaped log" "$OPENCODE_HOME/.config/opencode/prompt-logs/outside/session_escape.md"

OPENCODE_SAFE_HOME="$WORKROOT/opencode-safe-home"
mkdir -p "$OPENCODE_SAFE_HOME/.Trash" "$OPENCODE_SAFE_HOME/.config/opencode/skills"
cp -R "$REPO/skills/session-log" "$OPENCODE_SAFE_HOME/.config/opencode/skills/session-log"
mkdir -p "$OPENCODE_SAFE_HOME/.config/opencode/prompt-logs"
touch "$OPENCODE_SAFE_HOME/.config/opencode/prompt-logs/.enabled"
mkdir -p "$WORKROOT/opencode-outside"
printf 'opencode sentinel\n' > "$WORKROOT/opencode-outside/sentinel"
ln -s "$WORKROOT/opencode-outside" "$OPENCODE_SAFE_HOME/.config/opencode/prompt-logs/safe-fixture"
set +e
opencode_symlink_log="$(
  HOME="$OPENCODE_SAFE_HOME" SESSION_LOG_PLUGIN="$OPENCODE_SAFE_HOME/.config/opencode/skills/session-log/adapters/opencode/session-log.js" \
    bun --eval '
      const loaded = await import(process.env.SESSION_LOG_PLUGIN);
      const hooks = await loaded.SessionLogPlugin({ directory: "safe-fixture" });
      await hooks["chat.message"]({ sessionID: "safe-root" }, { parts: [{ type: "text", text: "must not escape" }] });
    ' 2>&1
)"
opencode_symlink_log_rc=$?
set -e
[[ "$opencode_symlink_log_rc" -ne 0 ]] && pass "OpenCode rejects symlinked log paths" || fail "OpenCode rejects symlinked log paths"
assert_exact "OpenCode symlinked log target remains unchanged" "opencode sentinel" "$(cat "$WORKROOT/opencode-outside/sentinel")"

mkdir -p "$OPENCODE_SAFE_HOME/.config/opencode/session-log"
if [[ -e "$OPENCODE_SAFE_HOME/.config/opencode/session-log/runtime.json" ||
      -L "$OPENCODE_SAFE_HOME/.config/opencode/session-log/runtime.json" ]]; then
  mv "$OPENCODE_SAFE_HOME/.config/opencode/session-log/runtime.json" "$WORKROOT/opencode-runtime-existing"
fi

printf 'runtime sentinel\n' > "$WORKROOT/opencode-runtime-sentinel"
ln -s "$WORKROOT/opencode-runtime-sentinel" "$OPENCODE_SAFE_HOME/.config/opencode/session-log/runtime.json"
set +e
opencode_symlink_runtime="$(
  HOME="$OPENCODE_SAFE_HOME" SESSION_LOG_PLUGIN="$OPENCODE_SAFE_HOME/.config/opencode/skills/session-log/adapters/opencode/session-log.js" \
    bun --eval 'const loaded = await import(process.env.SESSION_LOG_PLUGIN); await loaded.SessionLogPlugin({ directory: "runtime-fixture" });' 2>&1
)"
opencode_symlink_runtime_rc=$?
set -e
[[ "$opencode_symlink_runtime_rc" -ne 0 ]] && pass "OpenCode rejects symlinked runtime state" || fail "OpenCode rejects symlinked runtime state"
assert_exact "OpenCode symlinked runtime target remains unchanged" "runtime sentinel" "$(cat "$WORKROOT/opencode-runtime-sentinel")"


mkdir -p "$CODEX_HOME/.codex/sessions/2026/09"
CODEX_ROLLOUT="$CODEX_HOME/.codex/sessions/2026/09/rollout fixture.jsonl"
printf '%s\n' \
  '{"type":"session_meta","payload":{"id":"codex-session","cwd":"'"$PWD"'"}}' \
  '{"type":"token_usage_record","data":{"input_tokens":3,"cached_input_tokens":2,"output_tokens":7,"reasoning_output_tokens":4,"total_tokens":10}}' \
  '{"type":"token_usage_record","payload":{"usage":{"input_tokens":2,"cached_input_tokens":1,"output_tokens":3,"reasoning_output_tokens":1}}}' \
  > "$CODEX_ROLLOUT"
codex_usage="$(run_skill_entrypoint "$CODEX_HOME/.codex/skills/session-log/SKILL.md" "$CODEX_HOME" codex "usage \"$CODEX_ROLLOUT\"" 2>&1)"
assert_contains "source skill preserves quoted usage target" "session: $CODEX_ROLLOUT" "$codex_usage"
assert_contains "Codex total excludes cached and reasoning subsets" "total_tokens: 15" "$codex_usage"
codex_id_usage="$(run_harness_at_home_and_dir "$CODEX_HOME" "$PWD" codex usage codex-session 2>&1)"
assert_contains "Codex usage resolves a native session id" "session: $CODEX_ROLLOUT" "$codex_id_usage"
CODEX_BACKSLASH_ROLLOUT="$CODEX_HOME/.codex/sessions/2026/09/rollout\\fixture.jsonl"
cp "$CODEX_ROLLOUT" "$CODEX_BACKSLASH_ROLLOUT"
touch -t 202001010101 "$CODEX_BACKSLASH_ROLLOUT"
codex_backslash_usage="$(
  run_skill_entrypoint "$CODEX_HOME/.codex/skills/session-log/SKILL.md" \
    "$CODEX_HOME" codex "usage \"$CODEX_BACKSLASH_ROLLOUT\"" 2>&1
)"
assert_contains "quoted usage preserves a literal backslash" \
  "session: $CODEX_BACKSLASH_ROLLOUT" "$codex_backslash_usage"
UNRELATED_CODEX_ROLLOUT="$CODEX_HOME/.codex/sessions/2026/09/rollout-unrelated.jsonl"
printf '%s\n' \
  '{"type":"session_meta","payload":{"id":"unrelated-session","cwd":"/tmp/unrelated-project"}}' \
  '{"type":"token_usage_record","data":{"input_tokens":999,"output_tokens":1,"total_tokens":1000}}' \
  > "$UNRELATED_CODEX_ROLLOUT"
touch -t 203001010101 "$UNRELATED_CODEX_ROLLOUT"
codex_latest_usage="$(run_harness_at_home_and_dir "$CODEX_HOME" "$PWD" codex usage --latest 2>&1)"
assert_contains "Codex latest stays in the current project" "session: $CODEX_ROLLOUT" "$codex_latest_usage"

mkdir -p "$OMP_HOME/.omp/agent/sessions"
printf '{"type":"session","id":"omp-session","cwd":"%s","timestamp":"2026-08-28T10:00:00.000Z"}\n{"type":"message","message":{"role":"user","content":"hello","timestamp":"2026-08-28T10:00:00.000Z"}}\n{"type":"message","message":{"role":"assistant","model":"fixture-model","timestamp":"2026-08-28T10:00:01.000Z","completedAt":"2026-08-28T10:00:03.000Z","usage":{"input":1,"output":2,"reasoning":3,"cacheRead":4,"cacheWrite":5,"cost":{"total":0.01}}}}\n' "$PWD" > "$OMP_HOME/.omp/agent/sessions/omp-session.jsonl"
omp_usage="$(run_harness_at_home "$OMP_HOME" omp usage --latest 2>&1)"
assert_contains "OMP usage names harness" "OMP: usage" "$omp_usage"
assert_contains "OMP usage keeps native total output" "TOTAL" "$omp_usage"
assert_contains "OMP usage sums all token components when totalTokens is absent" "total_tokens: 15" "$omp_usage"
OMP_SAFE_HOME="$WORKROOT/omp-safe-home"
mkdir -p "$OMP_SAFE_HOME/.Trash" "$OMP_SAFE_HOME/.omp/agent/skills"
cp -R "$REPO/skills/session-log" "$OMP_SAFE_HOME/.omp/agent/skills/session-log"
mkdir -p "$OMP_SAFE_HOME/.omp/agent/prompt-logs" "$OMP_SAFE_HOME/.omp/agent/sessions"
touch "$OMP_SAFE_HOME/.omp/agent/prompt-logs/.enabled"
printf '{"type":"session","id":"omp-safe","cwd":"omp-safe-fixture"}\n' \
  > "$OMP_SAFE_HOME/.omp/agent/sessions/omp-safe.jsonl"
set +e
omp_invalid_id="$(
  HOME="$OMP_SAFE_HOME" OMP_SESSION_LOG_PLUGIN="$OMP_SAFE_HOME/.omp/agent/skills/session-log/adapters/omp/session-log.js" \
    bun --eval '
      const loaded = await import(process.env.OMP_SESSION_LOG_PLUGIN);
      const handlers = {};
      loaded.default({ registerCommand() {}, on(name, handler) { handlers[name] = handler; } });
      const ctx = { cwd: "omp-invalid-fixture", sessionManager: { getSessionFile: () => "omp-safe.jsonl", getSessionId: () => "../escape", getHeader: () => ({ cwd: "omp-invalid-fixture" }), getEntries: () => [] }, model: { id: "fixture" } };
      await handlers.before_agent_start({ prompt: "must not escape" }, ctx);
    ' 2>&1
)"
omp_invalid_id_rc=$?
set -e
[[ "$omp_invalid_id_rc" -eq 0 ]] && pass "OMP ignores invalid session IDs safely" || fail "OMP ignores invalid session IDs safely"
assert_not_file "OMP invalid session ID creates no log" "$OMP_SAFE_HOME/.omp/agent/prompt-logs/omp-invalid-fixture/session_unknown-session.md"
printf '{"type":"session","id":"omp-safe","cwd":"omp-symlink-fixture"}\n' \
  > "$OMP_SAFE_HOME/.omp/agent/sessions/omp-safe.jsonl"


mkdir -p "$WORKROOT/omp-outside"
printf 'omp sentinel\n' > "$WORKROOT/omp-outside/sentinel"
ln -s "$WORKROOT/omp-outside" "$OMP_SAFE_HOME/.omp/agent/prompt-logs/omp-symlink-fixture"
set +e
omp_symlink_log="$(
  HOME="$OMP_SAFE_HOME" OMP_SESSION_LOG_PLUGIN="$OMP_SAFE_HOME/.omp/agent/skills/session-log/adapters/omp/session-log.js" \
    bun --eval '
      const loaded = await import(process.env.OMP_SESSION_LOG_PLUGIN);
      const handlers = {};
      loaded.default({ registerCommand() {}, on(name, handler) { handlers[name] = handler; } });
      const ctx = { cwd: "omp-symlink-fixture", sessionManager: { getSessionFile: () => "omp-safe.jsonl", getSessionId: () => "omp-safe", getHeader: () => ({ cwd: "omp-symlink-fixture" }), getEntries: () => [] }, model: { id: "fixture" } };
      await handlers.before_agent_start({ prompt: "must not escape" }, ctx);
    ' 2>&1
)"
omp_symlink_log_rc=$?
set -e
[[ "$omp_symlink_log_rc" -ne 0 ]] && pass "OMP rejects symlinked log paths" || fail "OMP rejects symlinked log paths"
assert_exact "OMP symlinked log target remains unchanged" "omp sentinel" "$(cat "$WORKROOT/omp-outside/sentinel")"

mkdir -p "$OMP_SAFE_HOME/.omp/agent/session-log"
if [[ -e "$OMP_SAFE_HOME/.omp/agent/session-log/runtime.json" ||
      -L "$OMP_SAFE_HOME/.omp/agent/session-log/runtime.json" ]]; then
  mv "$OMP_SAFE_HOME/.omp/agent/session-log/runtime.json" "$WORKROOT/omp-runtime-existing"
fi

printf 'omp runtime sentinel\n' > "$WORKROOT/omp-runtime-sentinel"
ln -s "$WORKROOT/omp-runtime-sentinel" "$OMP_SAFE_HOME/.omp/agent/session-log/runtime.json"
set +e
omp_symlink_runtime="$(
  HOME="$OMP_SAFE_HOME" OMP_SESSION_LOG_PLUGIN="$OMP_SAFE_HOME/.omp/agent/skills/session-log/adapters/omp/session-log.js" \
    bun --eval 'const loaded = await import(process.env.OMP_SESSION_LOG_PLUGIN); loaded.default({ registerCommand() {}, on() {} });' 2>&1
)"
omp_symlink_runtime_rc=$?
set -e
[[ "$omp_symlink_runtime_rc" -ne 0 ]] && pass "OMP rejects symlinked runtime state" || fail "OMP rejects symlinked runtime state"
assert_exact "OMP symlinked runtime target remains unchanged" "omp runtime sentinel" "$(cat "$WORKROOT/omp-runtime-sentinel")"


set +e
omp_no_session="$(run_harness_at_home "$OMP_HOME" omp usage --no-session 2>&1)"
omp_no_session_rc=$?
set -e
[[ "$omp_no_session_rc" -ne 0 ]] && pass "OMP no-session usage fails directly" || fail "OMP no-session usage fails directly"
assert_contains "OMP no-session error explains missing reconstruction" "cannot be reconstructed" "$omp_no_session"

printf '=== OMP and OpenCode usage --latest picks the newest user prompt ===\n'
OMP_PICK_HOME="$WORKROOT/omp-pick-home"
OMP_PICK_PROJECT="$WORKROOT/omp-pick-project"
mkdir -p "$OMP_PICK_HOME/.Trash" "$OMP_PICK_HOME/.omp/agent/sessions/project" "$OMP_PICK_PROJECT"
OMP_PICK_PROJECT="$(cd "$OMP_PICK_PROJECT" && pwd -P)"
OMP_PICK_SESSIONS="$(cd "$OMP_PICK_HOME/.omp/agent/sessions/project" && pwd -P)"
omp_pick_session() {
  local file="$1" id="$2" parent="$3" prompt_time="$4" mtime="$5"
  printf '{"type":"session","id":"%s","cwd":"%s","timestamp":"2026-08-28T09:00:00.000Z"%s}\n' \
    "$id" "$OMP_PICK_PROJECT" "${parent:+,\"parentSession\":\"$parent\"}" > "$file"
  if [[ -n "$prompt_time" ]]; then
    printf '{"type":"message","timestamp":"%s","message":{"role":"user","content":"prompt %s","timestamp":"%s"}}\n' \
      "$prompt_time" "$id" "$prompt_time" >> "$file"
  fi
  printf '{"type":"message","timestamp":"2026-08-28T10:20:00.000Z","message":{"role":"assistant","model":"fixture-model","timestamp":"2026-08-28T10:20:00.000Z","usage":{"input":1,"output":1}}}\n' >> "$file"
  touch -t "$mtime" "$file"
}
OMP_PICK_A="$OMP_PICK_SESSIONS/omp-a.jsonl"
OMP_PICK_B="$OMP_PICK_SESSIONS/omp b's session.jsonl"
omp_pick_session "$OMP_PICK_A" omp-a "" "2026-08-28T10:05:00.000Z" 202001010101
omp_pick_session "$OMP_PICK_B" omp-b "" "2026-08-28T10:00:00.000Z" 202501010101
omp_single_usage="$(run_harness_at_home_and_dir "$OMP_PICK_HOME" "$OMP_PICK_PROJECT" omp usage 2>&1)"
assert_contains "OMP latest picks the session with the newest user prompt over a newer mtime" \
  "session: $OMP_PICK_A" "$omp_single_usage"
omp_pick_session "$OMP_PICK_SESSIONS/omp-c.jsonl" omp-c "" "" 202601010101
omp_pick_session "$OMP_PICK_SESSIONS/omp-d.jsonl" omp-d omp-b "2026-08-28T10:30:00.000Z" 202701010101
omp_pick_usage="$(run_harness_at_home_and_dir "$OMP_PICK_HOME" "$OMP_PICK_PROJECT" omp usage --latest 2>&1)"
assert_contains "OMP latest ignores promptless sessions and newer child prompts" \
  "session: $OMP_PICK_A" "$omp_pick_usage"
OMP_ALONE_HOME="$WORKROOT/omp-alone-home"
mkdir -p "$OMP_ALONE_HOME/.omp/agent/sessions/project"
OMP_ALONE_FILE="$(cd "$OMP_ALONE_HOME/.omp/agent/sessions/project" && pwd -P)/omp-alone.jsonl"
omp_pick_session "$OMP_ALONE_FILE" omp-alone "" "2026-08-28T10:00:00.000Z" 202001010101
assert_contains "OMP latest still reports a single session" "session: $OMP_ALONE_FILE" \
  "$(run_harness_at_home_and_dir "$OMP_ALONE_HOME" "$OMP_PICK_PROJECT" omp usage --latest 2>&1)"
omp_command_usage="$(
  cd "$OMP_PICK_PROJECT" &&
  HOME="$OMP_PICK_HOME" OMP_SESSION_LOG_PLUGIN="$(package_root_for omp)/adapters/omp/session-log.js" OMP_CURRENT_SESSION="$OMP_PICK_B" \
    bun --eval '
      const loaded = await import(process.env.OMP_SESSION_LOG_PLUGIN);
      let command;
      loaded.default({ registerCommand(name, definition) { command = definition; }, on() {} });
      const ctx = { mode: "print", cwd: process.cwd(), sessionManager: { getSessionFile: () => process.env.OMP_CURRENT_SESSION, getSessionId: () => "omp-b", getHeader: () => ({}) } };
      await command.handler("usage", ctx);
    ' 2>&1
)"
assert_contains "OMP /session-log usage reports the exact current session" \
  "session: $OMP_PICK_B" "$omp_command_usage"

OPENCODE_PICK_HOME="$WORKROOT/opencode-pick-home"
OPENCODE_PICK_DATA="$OPENCODE_PICK_HOME/.local/share/opencode"
mkdir -p "$OPENCODE_PICK_HOME/.Trash" "$OPENCODE_PICK_DATA"
sqlite3 "$OPENCODE_PICK_DATA/opencode.db" <<'SQL'
CREATE TABLE session (id TEXT PRIMARY KEY, parent_id TEXT, time_updated INTEGER, time_created INTEGER, title TEXT);
CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT);
CREATE TABLE session_message (id TEXT PRIMARY KEY, session_id TEXT, type TEXT, seq INTEGER, time_created INTEGER, time_updated INTEGER, data TEXT);
INSERT INTO session VALUES ('oc-a', NULL, 100, 1, 'A');
INSERT INTO session VALUES ('oc-b', NULL, 900, 1, 'B');
INSERT INTO message VALUES ('a-user', 'oc-a', 5000, 5000, '{"role":"user","time":{"created":5000}}');
INSERT INTO message VALUES ('a-assistant', 'oc-a', 5001, 5001, '{"role":"assistant","time":{"created":5001,"completed":5002}}');
INSERT INTO session_message VALUES ('b-user', 'oc-b', 'user', 1, 4000, 4000, '{"text":"prompt","time":{"created":4000}}');
INSERT INTO session_message VALUES ('b-assistant', 'oc-b', 'assistant', 2, 9000, 9000, '{"time":{"created":9000,"completed":9500}}');
SQL
assert_contains "OpenCode latest picks the session with the newest user prompt over a newer update" \
  "session: oc-a" "$(run_harness_at_home "$OPENCODE_PICK_HOME" opencode usage 2>&1)"
sqlite3 "$OPENCODE_PICK_DATA/opencode.db" <<'SQL'
INSERT INTO session VALUES ('oc-c', NULL, 1000, 1, 'C');
INSERT INTO session_message VALUES ('c-assistant', 'oc-c', 'assistant', 1, 9900, 9900, '{"time":{"created":9900,"completed":9950}}');
INSERT INTO session VALUES ('oc-d', 'oc-b', 2000, 1, 'D');
INSERT INTO session_message VALUES ('d-user', 'oc-d', 'user', 1, 8000, 8000, '{"text":"child","time":{"created":8000}}');
SQL
assert_contains "OpenCode latest ignores promptless sessions and newer child prompts" \
  "session: oc-a" "$(run_harness_at_home "$OPENCODE_PICK_HOME" opencode usage --latest 2>&1)"
sqlite3 "$OPENCODE_PICK_DATA/opencode.db" "INSERT INTO session VALUES ('oc-e', NULL, 50, 1, 'E');"
mkdir -p "$OPENCODE_PICK_DATA/storage/message/oc-e"
printf '{"role":"user","time":{"created":6000}}\n' > "$OPENCODE_PICK_DATA/storage/message/oc-e/e-user.json"
assert_contains "OpenCode latest reads user prompts from legacy disk storage" \
  "session: oc-e" "$(run_harness_at_home "$OPENCODE_PICK_HOME" opencode usage --latest 2>&1)"
OPENCODE_ALONE_HOME="$WORKROOT/opencode-alone-home"
mkdir -p "$OPENCODE_ALONE_HOME/.local/share/opencode"
sqlite3 "$OPENCODE_ALONE_HOME/.local/share/opencode/opencode.db" <<'SQL'
CREATE TABLE session (id TEXT PRIMARY KEY, parent_id TEXT, time_updated INTEGER, time_created INTEGER, title TEXT);
CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT);
INSERT INTO session VALUES ('oc-alone', NULL, 100, 1, 'Alone');
INSERT INTO message VALUES ('alone-user', 'oc-alone', 1000, 1000, '{"role":"user","time":{"created":1000}}');
SQL
assert_contains "OpenCode latest still reports a single session" \
  "session: oc-alone" "$(run_harness_at_home "$OPENCODE_ALONE_HOME" opencode usage --latest 2>&1)"

printf '=== CLI argument parsing covers quoting, conflicts, and relocation guards ===\n'
sq_status="$(run_harness cursor --arguments "'status'" 2>&1)"
assert_exact "single-quoted command token is preserved literally" "Cursor: off" "$sq_status"
set +e
dq_dollar="$(run_harness cursor --arguments '"\$"' 2>&1)"
set -e
assert_contains "double-quoted backslash-dollar keeps the dollar" 'unknown command: $' "$dq_dollar"
set +e
dq_literal="$(run_harness cursor --arguments '"a\zb"' 2>&1)"
set -e
assert_contains "double-quoted backslash before a normal char stays literal" 'unknown command: a\zb' "$dq_literal"
set +e
dq_backslash="$(run_harness cursor --arguments '"\\"' 2>&1)"
set -e
assert_contains "double-quoted double backslash collapses to one" 'unknown command: \' "$dq_backslash"
set +e
plain_escape="$(run_harness cursor --arguments 'unknown\ cmd' 2>&1)"
set -e
assert_contains "plain backslash escapes the following space" 'unknown command: unknown cmd' "$plain_escape"
set +e
unterminated_quote="$(run_harness cursor --arguments "'oops" 2>&1)"
unterminated_quote_rc=$?
set -e
[[ "$unterminated_quote_rc" -ne 0 ]] && pass "unterminated quote fails" || fail "unterminated quote fails"
assert_contains "unterminated quote error is explicit" "unterminated quote in command arguments" "$unterminated_quote"
set +e
unterminated_escape="$(run_harness cursor --arguments 'oops\' 2>&1)"
unterminated_escape_rc=$?
set -e
[[ "$unterminated_escape_rc" -ne 0 ]] && pass "unterminated escape fails" || fail "unterminated escape fails"
assert_contains "unterminated escape error is explicit" "unterminated escape in command arguments" "$unterminated_escape"
set +e
empty_arguments="$(run_harness cursor --arguments "" 2>&1)"
empty_arguments_rc=$?
set -e
[[ "$empty_arguments_rc" -ne 0 ]] && pass "empty arguments fail" || fail "empty arguments fail"
assert_contains "empty arguments require a command" "session-log command is required" "$empty_arguments"
set +e
unknown_arguments="$(run_harness cursor --arguments bogus 2>&1)"
unknown_arguments_rc=$?
set -e
[[ "$unknown_arguments_rc" -ne 0 ]] && pass "unknown command via arguments fails" || fail "unknown command via arguments fails"
assert_contains "unknown command via arguments is explicit" "unknown command: bogus" "$unknown_arguments"
set +e
entry_mismatch="$(run_session_log --entrypoint claude --harness codex status 2>&1)"
entry_mismatch_rc=$?
set -e
[[ "$entry_mismatch_rc" -ne 0 ]] && pass "entrypoint and harness mismatch fails" || fail "entrypoint and harness mismatch fails"
assert_contains "entrypoint/harness mismatch is explicit" "conflicting native entrypoint and harness identities" "$entry_mismatch"
set +e
dup_harness="$(run_session_log --entrypoint cursor --harness cursor --harness cursor status 2>&1)"
dup_harness_rc=$?
set -e
[[ "$dup_harness_rc" -ne 0 ]] && pass "duplicate harness flag fails" || fail "duplicate harness flag fails"
assert_contains "duplicate harness flag is explicit" "conflicting harness identity claims" "$dup_harness"
set +e
dup_entry="$(run_session_log --entrypoint cursor --entrypoint cursor --harness cursor status 2>&1)"
dup_entry_rc=$?
set -e
[[ "$dup_entry_rc" -ne 0 ]] && pass "duplicate entrypoint flag fails" || fail "duplicate entrypoint flag fails"
assert_contains "duplicate entrypoint flag is explicit" "conflicting native entrypoint claims" "$dup_entry"
set +e
command_and_arguments="$(run_harness cursor --arguments foo status 2>&1)"
command_and_arguments_rc=$?
set -e
[[ "$command_and_arguments_rc" -ne 0 ]] && pass "command combined with --arguments fails" || fail "command combined with --arguments fails"
assert_contains "command with --arguments conflict is explicit" "cannot combine a command with --arguments" "$command_and_arguments"
set +e
codex_reloc="$(HOME="$TEST_HOME" CODEX_HOME="$WORKROOT/relocated-codex" "$(package_root_for codex)/bin/session-log" --entrypoint codex --harness codex status 2>&1)"
codex_reloc_rc=$?
set -e
[[ "$codex_reloc_rc" -ne 0 ]] && pass "relocated Codex root is refused" || fail "relocated Codex root is refused"
assert_contains "relocated Codex root message is explicit" "Codex root is relocated" "$codex_reloc"
set +e
opencode_reloc="$(HOME="$TEST_HOME" XDG_DATA_HOME="$WORKROOT/relocated-data" "$(package_root_for opencode)/bin/session-log" --entrypoint opencode --harness opencode status 2>&1)"
opencode_reloc_rc=$?
set -e
[[ "$opencode_reloc_rc" -ne 0 ]] && pass "relocated OpenCode data root is refused" || fail "relocated OpenCode data root is refused"
assert_contains "relocated OpenCode data root message is explicit" "OpenCode data root is relocated" "$opencode_reloc"
set +e
omp_reloc="$(HOME="$TEST_HOME" PI_CODING_AGENT_DIR="$WORKROOT/relocated-omp" "$(package_root_for omp)/bin/session-log" --entrypoint omp --harness omp status 2>&1)"
omp_reloc_rc=$?
set -e
[[ "$omp_reloc_rc" -ne 0 ]] && pass "relocated OMP root is refused" || fail "relocated OMP root is refused"
assert_contains "relocated OMP root message is explicit" "OMP root is relocated" "$omp_reloc"

printf '=== native hooks honor the enabled flag and reject malformed payloads ===\n'
NATIVE_OFF_HOME="$WORKROOT/native-off-home"
mkdir -p "$NATIVE_OFF_HOME/.Trash" "$NATIVE_OFF_HOME/.codex/skills" "$NATIVE_OFF_HOME/.codex/session-log"
cp -R "$REPO/skills/session-log" "$NATIVE_OFF_HOME/.codex/skills/session-log"
NATIVE_OFF_HOOK="$NATIVE_OFF_HOME/.codex/skills/session-log/adapters/native/session_log_hook.py"
printf '{"stale":true}\n' > "$NATIVE_OFF_HOME/.codex/session-log/runtime.json"
set +e
native_off_output="$(
  printf '%s\n' '{"session_id":"off-session","cwd":"/tmp/off-project","last_assistant_message":"must not log"}' |
    HOME="$NATIVE_OFF_HOME" python3 "$NATIVE_OFF_HOOK" codex stop 2>&1
)"
native_off_rc=$?
set -e
[[ "$native_off_rc" -eq 0 ]] && pass "disabled native hook exits cleanly" || fail "disabled native hook exits cleanly (actual: $native_off_output)"
assert_not_file "disabled native hook removes stale runtime state" "$NATIVE_OFF_HOME/.codex/session-log/runtime.json"
assert_exact "disabled native hook writes no prompt log" "" \
  "$(find "$NATIVE_OFF_HOME/.codex/prompt-logs" -name 'session_off-session.md' -print 2>/dev/null)"
set +e
native_invalid="$(printf '%s\n' 'not-json' | HOME="$CURSOR_HOME" python3 "$CURSOR_HOOK" cursor user-prompt 2>&1)"
native_invalid_rc=$?
set -e
[[ "$native_invalid_rc" -ne 0 ]] && pass "invalid native payload fails" || fail "invalid native payload fails"
assert_contains "invalid native payload error is explicit" "invalid cursor hook payload" "$native_invalid"
set +e
native_missing_id="$(printf '%s\n' '{"model":"fixture","prompt":"orphan prompt"}' | HOME="$CURSOR_HOME" python3 "$CURSOR_HOOK" cursor user-prompt 2>&1)"
native_missing_id_rc=$?
set -e
[[ "$native_missing_id_rc" -eq 0 ]] && pass "native hook without a session id continues" || fail "native hook without a session id continues"
assert_exact "session-less native prompt still continues submission" '{"continue":true}' "$native_missing_id"

printf '=== Codex usage surfaces resolution failures ===\n'
set +e
codex_no_rollout="$(run_harness_at_home_and_dir "$CODEX_HOME" "$WORKROOT" codex usage --latest 2>&1)"
codex_no_rollout_rc=$?
set -e
[[ "$codex_no_rollout_rc" -ne 0 ]] && pass "Codex usage without a project rollout fails" || fail "Codex usage without a project rollout fails"
assert_contains "missing Codex rollout error is explicit" "no Codex rollout transcript found" "$codex_no_rollout"
set +e
codex_missing_session="$(run_harness_at_home_and_dir "$CODEX_HOME" "$PWD" codex usage nonexistent-session 2>&1)"
codex_missing_session_rc=$?
set -e
[[ "$codex_missing_session_rc" -ne 0 ]] && pass "Codex usage for an unknown session fails" || fail "Codex usage for an unknown session fails"
assert_contains "unknown Codex session error is explicit" "Codex session does not exist" "$codex_missing_session"
set +e
codex_invalid_session="$(run_harness_at_home_and_dir "$CODEX_HOME" "$PWD" codex usage 'bad!id' 2>&1)"
codex_invalid_session_rc=$?
set -e
[[ "$codex_invalid_session_rc" -ne 0 ]] && pass "Codex usage rejects invalid session ids" || fail "Codex usage rejects invalid session ids"
assert_contains "invalid Codex session id error is explicit" "invalid Codex session id" "$codex_invalid_session"
# sha256_file shasum->sha256sum fallback intentionally not covered: exercising it requires
# removing shasum from PATH globally, which would break unrelated hashing across the suite.


printf '=== pathsafe and locked-writer modules reject unsafe inputs ===\n'
PSLIB="$(package_root_for claude)/lib"
PSROOT="$WORKROOT/pathsafe-units"
mkdir -p "$PSROOT"
PSROOT="$(cd "$PSROOT" && pwd -P)"
set +e

# open_directory refuses '..' traversal
trav_out="$(SESSION_LOG_FILE="$PSROOT/a/../b" SESSION_LOG_CONTENT=x python3 "$PSLIB/pathsafe.py" write-file 2>&1)"
trav_rc=$?
assert_exact "pathsafe write-file rejects .. traversal (rc)" "1" "$trav_rc"
assert_contains "pathsafe .. traversal message" "refusing parent traversal" "$trav_out"

# open_directory refuses a symlinked intermediate component
mkdir -p "$PSROOT/real"
ln -s "$PSROOT/real" "$PSROOT/link"
sym_out="$(SESSION_LOG_FILE="$PSROOT/link/f" SESSION_LOG_CONTENT=x python3 "$PSLIB/pathsafe.py" write-file 2>&1)"
sym_rc=$?
[[ "$sym_rc" -ne 0 ]] && pass "pathsafe refuses symlinked component" || fail "pathsafe refuses symlinked component (rc=$sym_rc)"
assert_not_file "pathsafe did not write through symlinked component" "$PSROOT/real/f"

# write_file refuses a symlinked target file
: > "$PSROOT/realfile"
ln -s "$PSROOT/realfile" "$PSROOT/slink"
wf_out="$(SESSION_LOG_FILE="$PSROOT/slink" SESSION_LOG_CONTENT=x python3 "$PSLIB/pathsafe.py" write-file 2>&1)"
assert_contains "pathsafe write-file refuses symlinked target" "refusing to overwrite unsafe private file" "$wf_out"

printf 'src\n' > "$PSROOT/src.txt"
# copy_file refuses a symlink target
: > "$PSROOT/copyreal"
ln -s "$PSROOT/copyreal" "$PSROOT/copylink"
cs_out="$(SESSION_LOG_SOURCE="$PSROOT/src.txt" SESSION_LOG_TARGET="$PSROOT/copylink" SESSION_LOG_PRESERVE_MODE=0 SESSION_LOG_MANIFEST_OWNED=1 python3 "$PSLIB/pathsafe.py" copy-file 2>&1)"
assert_contains "pathsafe copy-file refuses symlink target" "refusing to overwrite symlink" "$cs_out"

# copy_file refuses a non-file (directory) target
mkdir -p "$PSROOT/dirtarget"
cn_out="$(SESSION_LOG_SOURCE="$PSROOT/src.txt" SESSION_LOG_TARGET="$PSROOT/dirtarget" SESSION_LOG_PRESERVE_MODE=0 SESSION_LOG_MANIFEST_OWNED=1 python3 "$PSLIB/pathsafe.py" copy-file 2>&1)"
assert_contains "pathsafe copy-file refuses non-file target" "refusing to overwrite non-file" "$cn_out"

# copy_file refuses an unowned file (manifest_owned=0, differing content)
printf 'different\n' > "$PSROOT/unowned.txt"
cu_out="$(SESSION_LOG_SOURCE="$PSROOT/src.txt" SESSION_LOG_TARGET="$PSROOT/unowned.txt" SESSION_LOG_PRESERVE_MODE=0 SESSION_LOG_MANIFEST_OWNED=0 python3 "$PSLIB/pathsafe.py" copy-file 2>&1)"
assert_contains "pathsafe copy-file refuses unowned file" "refusing to overwrite unowned file" "$cu_out"

# copy_file restores the original when a target appears during replacement
RACE_DIR="$PSROOT/copy-race"
mkdir -p "$RACE_DIR"
printf 'package source\n' > "$RACE_DIR/source"
printf 'original target\n' > "$RACE_DIR/target"
SESSION_LOG_SOURCE="$RACE_DIR/source" \
  SESSION_LOG_TARGET="$RACE_DIR/target" \
  SESSION_LOG_PRESERVE_MODE=0 \
  SESSION_LOG_MANIFEST_OWNED=1 \
  python3 - "$PSLIB" >/dev/null 2>&1 <<'PYRACE'
import os
import sys

sys.path.insert(0, sys.argv[1])
import pathsafe
target = os.environ["SESSION_LOG_TARGET"]
original_link = os.link
pathsafe.random.randrange = lambda *_args: 0
backup_zero = os.path.join(
    os.path.dirname(target),
    f".session-log-backup.{os.getpid()}.00000000.0",
)
conflict_zero = f"{backup_zero[:-2]}.1.conflict.0"
with open(backup_zero, "x", encoding="utf-8") as handle:
    handle.write("occupied backup target\n")
with open(conflict_zero, "x", encoding="utf-8") as handle:
    handle.write("occupied conflict target\n")

def create_racing_target(source_name, target_name, **options):
    if target_name == os.path.basename(target) and source_name.startswith(".session-log."):
        with open(target, "x", encoding="utf-8") as handle:
            handle.write("concurrent target\n")
    return original_link(source_name, target_name, **options)

os.link = create_racing_target
try:
    pathsafe.main(["copy-file"])
except SystemExit as error:
    if str(error) != f"package target changed during copy: {target}":
        raise
else:
    raise SystemExit("copy-file accepted concurrent target")
PYRACE
race_rc=$?
assert_exact "copy-file rejects a target created during replacement" "0" "$race_rc"
assert_exact "copy-file restores the original target after a race" "original target" "$(cat "$RACE_DIR/target")"
assert_exact "copy-file preserves the occupied conflict path" "occupied conflict target" "$(cat "$RACE_DIR"/.session-log-backup.*.conflict.0)"
assert_exact "copy-file preserves the occupied backup path" "occupied backup target" "$(cat "$RACE_DIR"/.session-log-backup.*.00000000.0)"
assert_exact "copy-file preserves the racing target as a conflict" "concurrent target" "$(find "$RACE_DIR" -type f -name '*.conflict.1' -exec cat {} \;)"

# copy_file does not overwrite a target created during backup restoration
RESTORE_RACE_DIR="$PSROOT/copy-restore-race"
mkdir -p "$RESTORE_RACE_DIR"
printf 'package source\n' > "$RESTORE_RACE_DIR/source"
printf 'original target\n' > "$RESTORE_RACE_DIR/target"
SESSION_LOG_SOURCE="$RESTORE_RACE_DIR/source" \
  SESSION_LOG_TARGET="$RESTORE_RACE_DIR/target" \
  SESSION_LOG_PRESERVE_MODE=0 \
  SESSION_LOG_MANIFEST_OWNED=1 \
  python3 - "$PSLIB" >/dev/null 2>&1 <<'PYRESTORERACE'
import os
import sys

sys.path.insert(0, sys.argv[1])
import pathsafe

target = os.environ["SESSION_LOG_TARGET"]
original_link = os.link
original_stat = os.stat
replacement_collision_created = [False]

def create_replacement_collision(source_name, target_name, **options):
    if (
        target_name == os.path.basename(target)
        and source_name.startswith(".session-log.")
        and not replacement_collision_created[0]
    ):
        replacement_collision_created[0] = True
        with open(target, "x", encoding="utf-8") as handle:
            handle.write("concurrent target\n")
    return original_link(source_name, target_name, **options)

def create_restore_collision(path, *args, **options):
    if path == os.path.basename(target):
        try:
            return original_stat(path, *args, **options)
        except FileNotFoundError:
            with open(target, "x", encoding="utf-8") as handle:
                handle.write("restore collision target\n")
            raise
    return original_stat(path, *args, **options)

os.link = create_replacement_collision
os.stat = create_restore_collision
try:
    pathsafe.main(["copy-file"])
except SystemExit as error:
    if str(error) != f"package target changed during copy: {target}":
        raise
else:
    raise SystemExit("copy-file accepted concurrent target")
PYRESTORERACE
restore_race_rc=$?
assert_exact "copy-file rejects a collision during restore" "0" "$restore_race_rc"
assert_exact "copy-file preserves the restore-race target" "restore collision target" "$(cat "$RESTORE_RACE_DIR/target")"
assert_exact "copy-file preserves the original target backup" "original target" "$(find "$RESTORE_RACE_DIR" -type f -name '.session-log-backup.*' ! -name '*.conflict.*' -exec cat {} \;)"
assert_exact "copy-file preserves the earlier racing target" "concurrent target" "$(find "$RESTORE_RACE_DIR" -type f -name '*.conflict.0' -exec cat {} \;)"

# Claude settings restoration preserves a target created during rollback
CLAUDE_RESTORE_RACE_DIR="$PSROOT/claude-restore-race"
mkdir -p "$CLAUDE_RESTORE_RACE_DIR"
printf '{"retained":"original"}\n' > "$CLAUDE_RESTORE_RACE_DIR/settings.json"
SESSION_LOG_OWNER_MARKER=restore-owner \
  SETTINGS_PATH="$CLAUDE_RESTORE_RACE_DIR/settings.json" \
  HOOK_PATH="$CLAUDE_RESTORE_RACE_DIR/hook.sh" \
  python3 - "$PSLIB" >/dev/null 2>&1 <<'PYCLAUDERACE'
import os
import sys

sys.path.insert(0, sys.argv[1])
import claude_settings

settings = os.environ["SETTINGS_PATH"]
name = os.path.basename(settings)
original_link = os.link
original_rename = claude_settings.pathsafe._rename_no_replace

def create_settings_races(source_name, target_name, **options):
    if target_name == name:
        with open(settings, "x", encoding="utf-8") as handle:
            handle.write("initial settings collision\n")
    return original_link(source_name, target_name, **options)

def create_restore_collision(dir_fd, source_name, target_name):
    if target_name == name and source_name.startswith(".session-log-backup."):
        with open(settings, "x", encoding="utf-8") as handle:
            handle.write("restore collision settings\n")
    return original_rename(dir_fd, source_name, target_name)

os.link = create_settings_races
claude_settings.pathsafe._rename_no_replace = create_restore_collision
try:
    claude_settings.main(["install"])
except SystemExit as error:
    if str(error) != "Claude settings changed during update; retry installation":
        raise
else:
    raise SystemExit("settings install accepted concurrent target")
PYCLAUDERACE
claude_race_rc=$?
assert_exact "Claude install rejects a collision during restore" "0" "$claude_race_rc"
assert_exact "Claude install preserves the restore-race target" "restore collision settings" "$(cat "$CLAUDE_RESTORE_RACE_DIR/settings.json")"
assert_exact "Claude install preserves the original settings backup" '{"retained":"original"}' "$(find "$CLAUDE_RESTORE_RACE_DIR" -type f -name '.session-log-backup.*' ! -name '*.conflict.*' -exec cat {} \;)"
assert_exact "Claude install preserves the earlier racing settings" "initial settings collision" "$(find "$CLAUDE_RESTORE_RACE_DIR" -type f -name '*.conflict.0' -exec cat {} \;)"


# Claude install and migration cleanup preserve destinations recreated before restore
CLAUDE_CLEANUP_RACE_DIR="$PSROOT/claude-cleanup-race"
mkdir -p "$CLAUDE_CLEANUP_RACE_DIR"
SESSION_LOG_LIB="$PSLIB" CLAUDE_CLEANUP_RACE_DIR="$CLAUDE_CLEANUP_RACE_DIR" \
  python3 - >/dev/null 2>&1 <<'PYCLAUDECLEANUP'
import json
import os
import sys

sys.path.insert(0, os.environ["SESSION_LOG_LIB"])
import claude_settings

root = os.environ["CLAUDE_CLEANUP_RACE_DIR"]
original_link = os.link
original_stat = os.stat

def fail_update_link(source_name, target_name, **options):
    if target_name == os.path.basename(os.environ["SETTINGS_PATH"]) and source_name.startswith(
        (".session-log.", ".session-log-migrate.")
    ):
        raise OSError("injected update link failure")
    return original_link(source_name, target_name, **options)

def create_cleanup_collision(path, *args, **options):
    settings = os.environ.get("SETTINGS_PATH")
    if settings is not None and path == os.path.basename(settings):
        try:
            return original_stat(path, *args, **options)
        except FileNotFoundError:
            with open(settings, "x", encoding="utf-8") as handle:
                handle.write("cleanup collision target\n")
            raise
    return original_stat(path, *args, **options)

os.link = fail_update_link
os.stat = create_cleanup_collision
try:
    for operation in ("install", "migrate"):
        directory = os.path.join(root, operation)
        os.makedirs(directory)
        settings = os.path.join(directory, "settings.json")
        legacy = os.path.join(directory, "scripts")
        document = {"retained": f"{operation} original"}
        if operation == "migrate":
            document["hooks"] = {
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"bash {legacy}/prompt_log_stop.sh",
                            }
                        ]
                    }
                ]
            }
        original_contents = json.dumps(document) + "\n"
        with open(settings, "w", encoding="utf-8") as handle:
            handle.write(original_contents)
        os.environ.update(
            SETTINGS_PATH=settings,
            HOOK_PATH=os.path.join(directory, "hook.sh"),
            SESSION_LOG_OWNER_MARKER="cleanup-owner",
            CLAUDE_SCRIPTS_DIR=legacy,
        )
        try:
            claude_settings.main([operation])
        except OSError as error:
            if str(error) != "injected update link failure":
                raise
        else:
            raise SystemExit(f"{operation} accepted injected link failure")
        with open(settings, encoding="utf-8") as handle:
            assert handle.read() == "cleanup collision target\n"
        backups = [
            name
            for name in os.listdir(directory)
            if name.startswith(".session-log-backup.")
            and ".conflict." not in name
        ]
        assert len(backups) == 1
        with open(os.path.join(directory, backups[0]), encoding="utf-8") as handle:
            assert handle.read() == original_contents
finally:
    os.link = original_link
    os.stat = original_stat
PYCLAUDECLEANUP
claude_cleanup_race_rc=$?
assert_exact "Claude cleanup rollback preserves recreated targets" "0" "$claude_cleanup_race_rc"
# adapter-link rollback preserves a target created while restoring a symlink
ADAPTER_RESTORE_RACE_DIR="$PSROOT/adapter-restore-race"
mkdir -p "$ADAPTER_RESTORE_RACE_DIR/store/releases/v1" "$ADAPTER_RESTORE_RACE_DIR/store/releases/v2" "$ADAPTER_RESTORE_RACE_DIR/harness"
printf 'old adapter\n' > "$ADAPTER_RESTORE_RACE_DIR/store/releases/v1/adapter.js"
printf 'new adapter\n' > "$ADAPTER_RESTORE_RACE_DIR/store/releases/v2/adapter.js"
ln -s "$ADAPTER_RESTORE_RACE_DIR/store/releases/v1/adapter.js" "$ADAPTER_RESTORE_RACE_DIR/harness/adapter.js"
SESSION_LOG_SOURCE="$ADAPTER_RESTORE_RACE_DIR/store/releases/v2/adapter.js" \
  SESSION_LOG_TARGET="$ADAPTER_RESTORE_RACE_DIR/harness/adapter.js" \
  SESSION_LOG_STORE="$ADAPTER_RESTORE_RACE_DIR/store" \
  python3 - "$PSLIB" >/dev/null 2>&1 <<'PYADAPTERRACE'
import os
import sys

sys.path.insert(0, sys.argv[1])
import pathsafe

target = os.environ["SESSION_LOG_TARGET"]
name = os.path.basename(target)
original_rename = pathsafe._rename_no_replace
original_symlink = os.symlink
pathsafe.random.randrange = lambda *_args: 0
conflict_zero = os.path.join(
    os.path.dirname(target),
    f".session-log-backup.{os.getpid()}.00000000.0.conflict.0",
)
with open(conflict_zero, "x", encoding="utf-8") as handle:
    handle.write("occupied adapter conflict\n")

def create_restore_collision(dir_fd, source_name, target_name):
    if target_name == name and source_name.startswith(".session-log-backup."):
        with open(target, "x", encoding="utf-8") as handle:
            handle.write("adapter restore collision\n")
    return original_rename(dir_fd, source_name, target_name)

def create_adapter_collision(source, target_name, **options):
    if target_name == name:
        with open(target, "x", encoding="utf-8") as handle:
            handle.write("adapter target collision\n")
    return original_symlink(source, target_name, **options)

pathsafe._rename_no_replace = create_restore_collision
os.symlink = create_adapter_collision
try:
    pathsafe.main(["link-adapter"])
except SystemExit as error:
    if str(error) != f"adapter target changed during update: {target}":
        raise
else:
    raise SystemExit("link-adapter accepted concurrent target")
PYADAPTERRACE
adapter_race_rc=$?
assert_exact "link-adapter rejects a collision during restore" "0" "$adapter_race_rc"
assert_exact "link-adapter preserves the restore-race target" "adapter restore collision" "$(cat "$ADAPTER_RESTORE_RACE_DIR/harness/adapter.js")"
assert_exact "link-adapter preserves the original symlink backup" "$ADAPTER_RESTORE_RACE_DIR/store/releases/v1/adapter.js" "$(find "$ADAPTER_RESTORE_RACE_DIR/harness" -maxdepth 1 -type l -name '.session-log-backup.*' ! -name '*.conflict.*' -exec readlink {} \;)"
assert_exact "link-adapter preserves the occupied conflict path" "occupied adapter conflict" "$(cat "$ADAPTER_RESTORE_RACE_DIR/harness"/.session-log-backup.*.conflict.0)"
assert_exact "link-adapter preserves the earlier racing target" "adapter target collision" "$(find "$ADAPTER_RESTORE_RACE_DIR/harness" -maxdepth 1 -type f -name '*.conflict.1' -exec cat {} \;)"

# link_adapter refuses an unowned link (points outside store/releases and differs from source)
mkdir -p "$PSROOT/store/releases/v1"
printf 'asset\n' > "$PSROOT/asset.js"
printf 'x\n' > "$PSROOT/elsewhere"
ln -sfn "$PSROOT/elsewhere" "$PSROOT/otherlink"
lu_out="$(SESSION_LOG_SOURCE="$PSROOT/asset.js" SESSION_LOG_TARGET="$PSROOT/otherlink" SESSION_LOG_STORE="$PSROOT/store" python3 "$PSLIB/pathsafe.py" link-adapter 2>&1)"
assert_contains "pathsafe link-adapter refuses unowned link" "refusing to overwrite unowned adapter link" "$lu_out"

# link_adapter is idempotent when the link already points at the source
ln -sfn "$PSROOT/asset.js" "$PSROOT/ownedlink"
SESSION_LOG_SOURCE="$PSROOT/asset.js" SESSION_LOG_TARGET="$PSROOT/ownedlink" SESSION_LOG_STORE="$PSROOT/store" python3 "$PSLIB/pathsafe.py" link-adapter
li_rc=$?
assert_exact "pathsafe link-adapter idempotent exit 0" "0" "$li_rc"
assert_link "pathsafe link unchanged after idempotent relink" "$PSROOT/asset.js" "$PSROOT/ownedlink"

# locking acquire/release round-trip and stale-owner reclaim
LK="$PSROOT/lockroot/install.lock"
mkdir -p "$PSROOT/lockroot"
LK_START="$(ps -p "$$" -o lstart= 2>/dev/null | sed 's/^ *//; s/[[:space:]]*$//')"
SESSION_LOG_LOCK_PATH="$LK" SESSION_LOG_LOCK_PID="$$" SESSION_LOG_LOCK_START="$LK_START" python3 "$PSLIB/locking.py" acquire
lk_rc=$?
assert_exact "locking acquire succeeds" "0" "$lk_rc"
assert_file "locking records owner" "$LK/owner"
SESSION_LOG_LOCK_PATH="$LK" SESSION_LOG_LOCK_PID="$$" SESSION_LOG_LOCK_START="$LK_START" python3 "$PSLIB/locking.py" acquire
lk2_rc=$?
assert_exact "locking blocks while held by a live owner" "1" "$lk2_rc"
SESSION_LOG_LOCK_PATH="$LK" SESSION_LOG_LOCK_PID="$$" SESSION_LOG_LOCK_START="$LK_START" python3 "$PSLIB/locking.py" release
assert_not_file "locking release removes the lock" "$LK"
mkdir -p "$LK"
printf '%s\nStale Owner Start\n' "$$" > "$LK/owner"
python3 - "$LK" <<'PYUTIL'
import os, sys, time
past = time.time() - 3600
os.utime(sys.argv[1], (past, past))
PYUTIL
SESSION_LOG_LOCK_PATH="$LK" SESSION_LOG_LOCK_PID="$$" SESSION_LOG_LOCK_START="$LK_START" python3 "$PSLIB/locking.py" acquire
lk3_rc=$?
assert_exact "locking reclaims a stale (start-mismatched) lock" "0" "$lk3_rc"

# enable_flag on/off round-trip
mkdir -p "$PSROOT/enable"
EN_FLAG="$PSROOT/enable/.enabled"
EN_LOCK="$PSROOT/enable/.enable.lock"
SESSION_LOG_ENABLE_LOCK="$EN_LOCK" SESSION_LOG_ENABLE_FLAG="$EN_FLAG" SESSION_LOG_ENABLE_ACTION=on python3 "$PSLIB/enable_flag.py"
en_rc=$?
assert_exact "enable_flag on succeeds" "0" "$en_rc"
assert_file "enable_flag on creates the flag" "$EN_FLAG"
assert_contains "enable_flag writes an enabled token" "enabled:" "$(cat "$EN_FLAG")"
assert_mode "enable_flag creates a private flag" "600" "$EN_FLAG"
SESSION_LOG_ENABLE_LOCK="$EN_LOCK" SESSION_LOG_ENABLE_FLAG="$EN_FLAG" SESSION_LOG_ENABLE_ACTION=off python3 "$PSLIB/enable_flag.py"
assert_not_file "enable_flag off removes the flag" "$EN_FLAG"
SESSION_LOG_ENABLE_LOCK="$EN_LOCK" SESSION_LOG_ENABLE_FLAG="$EN_FLAG" SESSION_LOG_ENABLE_ACTION=off python3 "$PSLIB/enable_flag.py"
en_off_rc=$?
assert_exact "enable_flag off on a missing flag is a clean no-op" "0" "$en_off_rc"
assert_not_file "enable_flag off leaves no flag behind" "$EN_FLAG"

# claude_settings install injects the four hook events, is idempotent, and refuses symlinks
mkdir -p "$PSROOT/claude-settings"
CS_SET="$PSROOT/claude-settings/settings.json"
CS_HOOK="$PSROOT/claude-settings/hook.sh"
: > "$CS_HOOK"
SETTINGS_PATH="$CS_SET" HOOK_PATH="$CS_HOOK" SESSION_LOG_OWNER_MARKER="ownerX" python3 "$PSLIB/claude_settings.py" install
cs_rc=$?
assert_exact "claude_settings install succeeds on a fresh file" "0" "$cs_rc"
cs_json="$(cat "$CS_SET")"
assert_contains "claude_settings injects SessionStart" "SessionStart" "$cs_json"
assert_contains "claude_settings injects UserPromptSubmit" "UserPromptSubmit" "$cs_json"
assert_contains "claude_settings injects Stop" "\"Stop\"" "$cs_json"
assert_contains "claude_settings injects SubagentStop" "SubagentStop" "$cs_json"
SETTINGS_PATH="$CS_SET" HOOK_PATH="$CS_HOOK" SESSION_LOG_OWNER_MARKER="ownerX" python3 "$PSLIB/claude_settings.py" install
cs_count="$(grep -c "SESSION_LOG_OWNER=ownerX" "$CS_SET")"
assert_exact "claude_settings install is idempotent (4 owned hooks)" "4" "$cs_count"
ln -s "$CS_SET" "$PSROOT/claude-settings/settings-link.json"
SETTINGS_PATH="$PSROOT/claude-settings/settings-link.json" HOOK_PATH="$CS_HOOK" SESSION_LOG_OWNER_MARKER="ownerX" python3 "$PSLIB/claude_settings.py" install >/dev/null 2>&1
csl_rc=$?
[[ "$csl_rc" -ne 0 ]] && pass "claude_settings refuses symlinked settings" || fail "claude_settings refuses symlinked settings (rc=$csl_rc)"

# claude_settings migrate refuses a symlinked settings parent directory
MIG_REAL="$PSROOT/mig-real"
MIG_LEGACY="$PSROOT/mig-legacy"
mkdir -p "$MIG_REAL"
printf '{\n  "hooks": {\n    "Stop": [\n      {\n        "hooks": [\n          {"type": "command", "command": "bash %s/prompt_log_stop.sh"}\n        ]\n      }\n    ]\n  }\n}\n' "$MIG_LEGACY" > "$MIG_REAL/settings.json"
mig_before="$(cat "$MIG_REAL/settings.json")"
ln -s "$MIG_REAL" "$PSROOT/mig-link"
SETTINGS_PATH="$PSROOT/mig-link/settings.json" CLAUDE_SCRIPTS_DIR="$MIG_LEGACY" python3 "$PSLIB/claude_settings.py" migrate >/dev/null 2>&1
migp_rc=$?
[[ "$migp_rc" -ne 0 ]] && pass "claude_settings migrate refuses symlinked settings parent" || fail "claude_settings migrate refuses symlinked settings parent (rc=$migp_rc)"
assert_exact "claude_settings migrate leaves symlinked-parent settings unchanged" "$mig_before" "$(cat "$MIG_REAL/settings.json")"
MIG_BAD="$PSROOT/mig-bad"
mkdir -p "$MIG_BAD"
printf '{not-json\n' > "$MIG_BAD/settings.json"
mig_bad_error="$(SETTINGS_PATH="$MIG_BAD/settings.json" CLAUDE_SCRIPTS_DIR="$MIG_LEGACY" python3 "$PSLIB/claude_settings.py" migrate 2>&1)"
mig_bad_rc=$?
[[ "$mig_bad_rc" -ne 0 ]] && pass "claude_settings migrate rejects malformed JSON" || fail "claude_settings migrate rejects malformed JSON (rc=$mig_bad_rc)"
assert_contains "claude_settings migrate reports malformed JSON cleanly" "cannot update Claude settings:" "$mig_bad_error"
assert_not_contains "claude_settings migrate emits no raw traceback" "Traceback" "$mig_bad_error"
set -e

printf '=== CLI preserves hard-linked lock files and respects installation locks ===\n'
HARDLINK_HOME="$WORKROOT/hardlink-home"
HARDLINK_SENTINEL="$WORKROOT/hardlink-lock-sentinel"
mkdir -p "$HARDLINK_HOME/.Trash" "$HARDLINK_HOME/.claude/prompt-logs"
printf 'external lock sentinel\n' > "$HARDLINK_SENTINEL"
chmod 644 "$HARDLINK_SENTINEL"
ln "$HARDLINK_SENTINEL" "$HARDLINK_HOME/.claude/prompt-logs/.enabled.lock"
set +e
hardlink_lock_error="$(
  HOME="$HARDLINK_HOME" "$TEST_HOME/.claude/skills/session-log/bin/session-log" \
    --entrypoint claude --harness claude on 2>&1
)"
hardlink_lock_rc=$?
set -e
[[ "$hardlink_lock_rc" -ne 0 ]] && pass "CLI rejects hard-linked enable locks" || fail "CLI rejects hard-linked enable locks"
assert_contains "hard-linked enable lock failure is explicit" "unsafe session-log enable lock" "$hardlink_lock_error"
assert_exact "hard-linked enable lock target remains unchanged" "external lock sentinel" "$(cat "$HARDLINK_SENTINEL")"
assert_mode "hard-linked enable lock keeps external permissions" "644" "$HARDLINK_SENTINEL"
[[ "$HARDLINK_HOME/.claude/prompt-logs/.enabled.lock" -ef "$HARDLINK_SENTINEL" ]] &&
  pass "hard-linked enable lock remains linked" ||
  fail "hard-linked enable lock remains linked"

FLAG_HARDLINK_HOME="$WORKROOT/flag-hardlink-home"
FLAG_HARDLINK_SENTINEL="$WORKROOT/flag-hardlink-sentinel"
mkdir -p "$FLAG_HARDLINK_HOME/.Trash" "$FLAG_HARDLINK_HOME/.claude/prompt-logs"
printf 'external flag sentinel\n' > "$FLAG_HARDLINK_SENTINEL"
chmod 644 "$FLAG_HARDLINK_SENTINEL"
ln "$FLAG_HARDLINK_SENTINEL" "$FLAG_HARDLINK_HOME/.claude/prompt-logs/.enabled"
set +e
hardlink_flag_error="$(
  HOME="$FLAG_HARDLINK_HOME" "$TEST_HOME/.claude/skills/session-log/bin/session-log" \
    --entrypoint claude --harness claude off 2>&1
)"
hardlink_flag_rc=$?
set -e
[[ "$hardlink_flag_rc" -ne 0 ]] && pass "CLI rejects hard-linked enable flags" || fail "CLI rejects hard-linked enable flags"
assert_contains "hard-linked flag failure is explicit" "unsafe session-log enable flag" "$hardlink_flag_error"
assert_exact "hard-linked enable flag target remains unchanged" "external flag sentinel" "$(cat "$FLAG_HARDLINK_SENTINEL")"
assert_mode "hard-linked enable flag keeps external permissions" "644" "$FLAG_HARDLINK_SENTINEL"
[[ "$FLAG_HARDLINK_HOME/.claude/prompt-logs/.enabled" -ef "$FLAG_HARDLINK_SENTINEL" ]] &&
  pass "hard-linked enable flag remains linked" ||
  fail "hard-linked enable flag remains linked"

CLAUDE_ATOMIC_HOME="$WORKROOT/claude-atomic-home"
CLAUDE_SETTINGS_SENTINEL="$WORKROOT/claude-settings-sentinel"
mkdir -p "$CLAUDE_ATOMIC_HOME/.Trash" "$CLAUDE_ATOMIC_HOME/.claude"
printf '{"retained":"external settings"}\n' > "$CLAUDE_SETTINGS_SENTINEL"
chmod 640 "$CLAUDE_SETTINGS_SENTINEL"
ln "$CLAUDE_SETTINGS_SENTINEL" "$CLAUDE_ATOMIC_HOME/.claude/settings.json"
set +e
claude_atomic_output="$(
  HOME="$CLAUDE_ATOMIC_HOME" bash "$REPO/skills/session-log/install.sh" \
    --harness claude --arguments on 2>&1
)"
claude_atomic_rc=$?
set -e
[[ "$claude_atomic_rc" -eq 0 ]] && pass "Claude installer atomically updates hard-linked settings" || fail "Claude installer atomically updates hard-linked settings (actual: $claude_atomic_output)"
assert_exact "Claude installer keeps activation output" "Claude Code: on — restart required" "$claude_atomic_output"
assert_exact "settings hard-link target remains unchanged" '{"retained":"external settings"}' "$(cat "$CLAUDE_SETTINGS_SENTINEL")"
assert_mode "updated Claude settings preserve mode" "640" "$CLAUDE_ATOMIC_HOME/.claude/settings.json"
[[ ! "$CLAUDE_ATOMIC_HOME/.claude/settings.json" -ef "$CLAUDE_SETTINGS_SENTINEL" ]] &&
  pass "Claude settings update replaces rather than mutates hard links" ||
  fail "Claude settings update replaces rather than mutates hard links"
assert_contains "updated Claude settings preserve existing values" "external settings" "$(cat "$CLAUDE_ATOMIC_HOME/.claude/settings.json")"

PACKAGE_LOCK_HOME="$WORKROOT/package-lock-home"
PACKAGE_LOCK="$PACKAGE_LOCK_HOME/.local/share/universal-session-log/.install.lock"
mkdir -p "$PACKAGE_LOCK_HOME/.Trash" "$PACKAGE_LOCK"
PACKAGE_LOCK_START="$(ps -p "$$" -o lstart= 2>/dev/null | sed 's/^ *//; s/[[:space:]]*$//')"
printf '%s\n%s\n' "$$" "$PACKAGE_LOCK_START" > "$PACKAGE_LOCK/owner"
PACKAGE_LOCK_OWNER="$(cat "$PACKAGE_LOCK/owner")"
set +e
package_lock_error="$(
  HOME="$PACKAGE_LOCK_HOME" bash "$REPO/skills/session-log/install.sh" --install --harness omp 2>&1
)"
package_lock_rc=$?
set -e
[[ "$package_lock_rc" -ne 0 ]] && pass "package installer waits for a live installation lock" || fail "package installer waits for a live installation lock"
assert_contains "package lock failure is explicit" "installation is already in progress" "$package_lock_error"
assert_not_file "package lock prevents partial package installation" "$PACKAGE_LOCK_HOME/.omp/agent/skills/session-log/SKILL.md"
assert_exact "package lock owner remains unchanged" "$PACKAGE_LOCK_OWNER" "$(cat "$PACKAGE_LOCK/owner")"

printf '\nResults: %s\n' "$([[ "$FAIL" -eq 0 ]] && echo passed || echo FAILED)"
exit "$FAIL"
