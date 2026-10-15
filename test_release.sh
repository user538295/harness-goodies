#!/usr/bin/env bash
# Tests for release.sh pure functions. Run: bash test_release.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=release.sh
source "$SCRIPT_DIR/release.sh"

PASS=0
FAIL=0

ok()   { printf "  PASS: %s\n" "$1"; PASS=$((PASS + 1)); }
nok()  { printf "  FAIL: %s\n    expected: %s\n    actual:   %s\n" "$1" "$2" "$3"; FAIL=$((FAIL + 1)); }

assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then ok "$desc"; else nok "$desc" "$expected" "$actual"; fi
}

assert_exits_0() {
  local desc="$1"; shift
  if "$@" 2>/dev/null; then ok "$desc"; else nok "$desc" "exit 0" "exit non-0"; fi
}

assert_exits_1() {
  local desc="$1"; shift
  if ! "$@" 2>/dev/null; then ok "$desc"; else nok "$desc" "exit non-0" "exit 0"; fi
}

# discard <path>... → moves test leftovers to the trash; without one they stay in the temp directory
discard() {
  if command -v trash >/dev/null; then
    trash "$@"
  elif [[ -d ~/.Trash ]]; then
    mv "$@" ~/.Trash/
  fi
}

# ── next_version ──────────────────────────────────────────────────────────────
echo "=== next_version ==="
assert_eq "patch bump"      "1.0.5"  "$(next_version 1.0.4 false)"
assert_eq "minor bump"      "1.1.0"  "$(next_version 1.0.4 true)"
assert_eq "patch overflow"  "1.2.10" "$(next_version 1.2.9 false)"
assert_eq "minor from .9"   "1.3.0"  "$(next_version 1.2.9 true)"
assert_eq "from zero"       "0.1.0"  "$(next_version 0.0.9 true)"

# ── diff_has_new_items ────────────────────────────────────────────────────────
echo "=== diff_has_new_items ==="
assert_exits_0 "new agent"      diff_has_new_items $'A\tagents/foo.md'
assert_exits_0 "new skill"      diff_has_new_items $'A\tskills/bar/skill.md'
assert_exits_1 "commands/ not shipped" diff_has_new_items $'A\tcommands/cmd.md'
assert_exits_0 "new script"     diff_has_new_items $'A\tscripts/run.sh'
assert_exits_0 "mix add+modify" diff_has_new_items $'M\tREADME.md\nA\tagents/new.md'
assert_exits_1 "empty diff"     diff_has_new_items ""
assert_exits_1 "modified only"  diff_has_new_items $'M\tagents/foo.md'
assert_exits_1 "deleted only"   diff_has_new_items $'D\tagents/foo.md'
assert_exits_1 "added README"   diff_has_new_items $'A\tREADME.md'
assert_exits_1 "added docs"     diff_has_new_items $'A\tdocs/guide.md'

# ── filter_valid_tags ─────────────────────────────────────────────────────────
echo "=== filter_valid_tags ==="
assert_eq "first valid"    "v1.0.3" "$(filter_valid_tags $'claude-goodies--v1.0.4\nv1.0.3\nv1.0.2')"
assert_eq "ignores bad"    "v1.0.2" "$(filter_valid_tags $'bad-tag\nv1.0.2')"
assert_eq "already sorted" "v1.0.3" "$(filter_valid_tags $'v1.0.3\nv1.0.2')"
assert_eq "empty → empty"  ""       "$(filter_valid_tags '')"

# ── check_on_main ─────────────────────────────────────────────────────────────
echo "=== check_on_main ==="
assert_exits_0 "main"           check_on_main "main"
assert_exits_1 "feature branch" check_on_main "feature/foo"
assert_exits_1 "develop"        check_on_main "develop"
assert_exits_1 "empty"          check_on_main ""

# ── check_clean_tree ──────────────────────────────────────────────────────────
echo "=== check_clean_tree ==="
assert_exits_0 "clean tree"    check_clean_tree ""
assert_exits_1 "modified file" check_clean_tree " M file.txt"
assert_exits_1 "untracked"     check_clean_tree "?? newfile.txt"
assert_exits_1 "staged"        check_clean_tree "A  staged.txt"

# ── current_version ───────────────────────────────────────────────────────────
echo "=== current_version ==="
_orig_json="$PLUGIN_JSON"
_tmp_json="$(mktemp)"
printf '{\n  "name": "test",\n  "version": "2.3.4"\n}\n' > "$_tmp_json"
PLUGIN_JSON="$_tmp_json"
assert_eq "reads version" "2.3.4" "$(current_version)"
PLUGIN_JSON="$_orig_json"
discard "$_tmp_json"

# ── write_version ─────────────────────────────────────────────────────────────
echo "=== write_version ==="
_orig_files=("${VERSION_FILES[@]}")
_tmp_a="$(mktemp)"
_tmp_b="$(mktemp)"
printf '{\n  "version": "1.0.0"\n}\n' > "$_tmp_a"
printf '{\n  "plugins": [\n    { "name": "p", "version": "1.0.0" }\n  ]\n}\n' > "$_tmp_b"
VERSION_FILES=("$_tmp_a" "$_tmp_b")
write_version 3.1.4
assert_eq "bumps the plugin manifest"  "3.1.4" "$(version_in "$_tmp_a")"
assert_eq "bumps the catalog entry"    "3.1.4" "$(version_in "$_tmp_b")"
VERSION_FILES=("${_orig_files[@]}")
discard "$_tmp_a" "$_tmp_b"

# ── run_update_commands ───────────────────────────────────────────────────────
echo "=== run_update_commands ==="
_orig_cmds=("${UPDATE_COMMANDS[@]}")
_log="$(mktemp)"
UPDATE_COMMANDS=("echo one >> $_log" "echo two >> $_log")
assert_exits_0 "all succeed → exit 0" run_update_commands 0 >/dev/null
assert_eq "runs commands in order" "one two" "$(paste -sd' ' "$_log")"
: > "$_log"
UPDATE_COMMANDS=("false" "echo after >> $_log")
assert_exits_1 "a failing command → exit 1" run_update_commands 0 >/dev/null
assert_eq "continues after a failure" "after" "$(cat "$_log")"
_start=$SECONDS
UPDATE_COMMANDS=("true")
run_update_commands 2 >/dev/null
assert_eq "waits the given delay first" "true" "$([[ $((SECONDS - _start)) -ge 2 ]] && echo true || echo false)"
UPDATE_COMMANDS=("${_orig_cmds[@]}")
discard "$_log"
assert_eq "covers claude, omp, codex, opencode" "4" "$(printf '%s\n' "${UPDATE_COMMANDS[@]}" | grep -cE '^(claude|omp|codex|opencode) ')"

# ── session_log_version_stale ─────────────────────────────────────────────────
echo "=== session_log_version_stale ==="
assert_exits_0 "package changed, VERSION not" session_log_version_stale $'skills/session-log/bin/session-log'
assert_exits_1 "package and VERSION changed"  session_log_version_stale $'skills/session-log/bin/session-log\nskills/session-log/VERSION'
assert_exits_1 "only VERSION changed"         session_log_version_stale $'skills/session-log/VERSION'
assert_exits_1 "nothing changed"              session_log_version_stale ""
assert_exits_1 "another skill changed"        session_log_version_stale $'skills/commit/SKILL.md'
assert_exits_1 "a lookalike directory"        session_log_version_stale $'skills/session-logger/SKILL.md'
for _file in "${SESSION_LOG_WIRING_FILES[@]}" "$OMP_EXTENSIONS_CHANGE"; do
  assert_exits_0 "$_file changed, VERSION not" session_log_version_stale "$_file"
  assert_exits_1 "$_file and VERSION changed"  session_log_version_stale "$_file"$'\nskills/session-log/VERSION'
done
assert_exits_1 "a lookalike hook manifest"    session_log_version_stale $'other/hooks/hooks.json'
assert_exits_1 "package.json changed only"    session_log_version_stale $'package.json'

# ── assert_session_log_version_bumped ─────────────────────────────────────────
echo "=== assert_session_log_version_bumped ==="
# The fixture repository must not run the developer's commit signing, hooks, or other global settings.
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
fixture_git() { git -C "$REPO_ROOT" -c user.name=test -c user.email=test@example.com "$@"; }
# write_package_json <version> <omp extension> → the fixture's package.json
write_package_json() {
  printf '{\n  "version": "%s",\n  "omp": {\n    "extensions": [\n      "%s"\n    ]\n  }\n}\n' "$1" "$2" \
    > "$REPO_ROOT/package.json"
}
_orig_root="$REPO_ROOT"
REPO_ROOT="$(mktemp -d)"
fixture_git init -q
fixture_git commit -q --allow-empty -m base
mkdir -p "$REPO_ROOT/skills/session-log" "$REPO_ROOT/hooks"
printf '1.0.0\n' > "$REPO_ROOT/skills/session-log/VERSION"
printf 'one\n' > "$REPO_ROOT/skills/session-log/SKILL.md"
printf '{}\n' > "$REPO_ROOT/hooks/hooks.json"
write_package_json 1.0.0 ./one.js
fixture_git add -A
fixture_git commit -q -m release
fixture_git tag v1.0.0
assert_exits_0 "nothing changed since the tag" assert_session_log_version_bumped v1.0.0
printf 'two\n' > "$REPO_ROOT/skills/session-log/SKILL.md"
fixture_git commit -q -am change
assert_exits_1 "package changed without a VERSION bump" assert_session_log_version_bumped v1.0.0
assert_eq "names VERSION in the refusal" "1" \
  "$(assert_session_log_version_bumped v1.0.0 2>&1 >/dev/null | grep -c 'skills/session-log/VERSION')"
printf '1.0.1\n' > "$REPO_ROOT/skills/session-log/VERSION"
fixture_git commit -q -am bump
assert_exits_0 "package changed with a VERSION bump" assert_session_log_version_bumped v1.0.0
fixture_git tag v1.0.1
printf '{"hooks": {}}\n' > "$REPO_ROOT/hooks/hooks.json"
fixture_git commit -q -am hooks
assert_exits_1 "hook manifest changed without a VERSION bump" assert_session_log_version_bumped v1.0.1
printf '1.0.2\n' > "$REPO_ROOT/skills/session-log/VERSION"
fixture_git commit -q -am bump
fixture_git tag v1.0.2
write_package_json 2.0.0 ./one.js
fixture_git commit -q -am release
assert_exits_0 "only the release version of package.json changed" assert_session_log_version_bumped v1.0.2
write_package_json 2.0.0 ./two.js
fixture_git commit -q -am extension
assert_exits_1 "omp.extensions changed without a VERSION bump" assert_session_log_version_bumped v1.0.2
discard "$REPO_ROOT"
REPO_ROOT="$_orig_root"

# ── main ──────────────────────────────────────────────────────────────────────
echo "=== main ==="
assert_eq "stops on the session-log guard, checked against the last tag" "guard v9.9.9" "$(
  set -e
  assert_on_main() { :; }
  assert_clean_tree() { :; }
  find_last_valid_tag() { echo v9.9.9; }
  assert_session_log_version_bumped() { echo "guard $1"; return 1; }
  main --dry-run 2>/dev/null
)"

# ── shipped versions agree ────────────────────────────────────────────────────
echo "=== shipped versions agree ==="
for _file in "${VERSION_FILES[@]}"; do
  assert_eq "${_file#"$SCRIPT_DIR"/} matches plugin.json" "$(current_version)" "$(version_in "$_file")"
done

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
printf "Results: %d passed, %d failed\n" "$PASS" "$FAIL"
[[ $FAIL -eq 0 ]]
