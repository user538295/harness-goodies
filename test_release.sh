#!/usr/bin/env bash
# Tests for release.sh: its pure functions and a --dry-run of main. Run: bash test_release.sh
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

# ── main ──────────────────────────────────────────────────────────────────────
echo "=== main ==="
# test_release_dry_run_without_version_guard_prints_plan_exits_0_pushes_nothing_runs_no_update_command (S35):
# --dry-run in a /tmp fixture repo with a bare origin prints the plan, exits 0, pushes nothing and runs no
# update command. The fixture repositories must not run the developer's commit signing, hooks, or other
# global settings.
_isolated_git() { GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 git "$@"; }
_fixture="$(mktemp -d /tmp/release-fixture.XXXXXX)"
_origin="$(mktemp -d /tmp/release-origin.XXXXXX)"
_fixture_git() { _isolated_git -C "$_fixture" -c user.name=test -c user.email=test@example.com "$@"; }
_git_calls="$(mktemp)"
_update_marker="$(mktemp)"
_isolated_git init -q --bare "$_origin"
_fixture_git init -q
_fixture_git remote add origin "$_origin"
mkdir -p "$_fixture/.claude-plugin"
printf '{\n  "version": "1.2.3"\n}\n' > "$_fixture/.claude-plugin/plugin.json"
_fixture_git add -A
_fixture_git commit -q -m release
_fixture_git tag v1.2.3
mkdir -p "$_fixture/skills/new"
printf 'new\n' > "$_fixture/skills/new/SKILL.md"
_fixture_git add -A
_fixture_git commit -q -m "new skill"
_head_before="$(_fixture_git rev-parse HEAD)"
_rc=0
_out=$(
  set -e
  REPO_ROOT="$_fixture"
  PLUGIN_JSON="$_fixture/.claude-plugin/plugin.json"
  # shellcheck disable=SC2030 # the fixture's file list must stay inside this subshell
  VERSION_FILES=("$PLUGIN_JSON")
  UPDATE_COMMANDS=("echo updated >> $_update_marker")
  git() { printf '%s\n' "$*" >> "$_git_calls"; command git "$@"; }
  assert_on_main() { :; }
  assert_clean_tree() { :; }
  find_last_valid_tag() { echo v1.2.3; }
  main --dry-run -y 2>&1 < /dev/null
) || _rc=$?
assert_eq "dry-run exits 0" "0" "$_rc"
assert_eq "dry-run names the current version" "1" "$(grep -c '^Current version *: 1\.2\.3$' <<< "$_out")"
assert_eq "dry-run names the last tag" "1" "$(grep -c '^Last tag *: v1\.2\.3$' <<< "$_out")"
assert_eq "dry-run names the next version (a new skill bumps the minor)" "1" "$(grep -c '^Next version *: 1\.3\.0$' <<< "$_out")"
assert_eq "dry-run prints the release plan" "1 7" "$(grep -c '^\[dry-run\] Would execute:' <<< "$_out") $(grep -cE '^  [1-7]\. ' <<< "$_out")"
assert_eq "dry-run plans the tag and both pushes" "3" "$(grep -cE '^  (4\. git tag -a v1\.3\.0 |5\. git push origin main$|6\. git push origin v1\.3\.0$)' <<< "$_out")"
assert_eq "dry-run lists the update command" "1" "$(grep -c 'echo updated' <<< "$_out")"
assert_eq "the git stub saw the diff against the last tag" "1" "$(grep -c -- 'diff --name-status v1.2.3..HEAD' "$_git_calls")"
assert_eq "dry-run runs no git push" "0" "$(grep -cE '(^| )push( |$)' "$_git_calls")"
assert_eq "dry-run leaves the origin empty" "" "$(_isolated_git -C "$_origin" for-each-ref)"
assert_eq "dry-run runs no update command" "0" "$(wc -l < "$_update_marker" | tr -d ' ')"
assert_eq "dry-run leaves HEAD and tags alone" "$_head_before v1.2.3" "$(_fixture_git rev-parse HEAD) $(_fixture_git tag)"
assert_eq "dry-run leaves the plugin version alone" "1.2.3" "$(version_in "$_fixture/.claude-plugin/plugin.json")"
discard "$_fixture" "$_origin" "$_git_calls" "$_update_marker"

# ── shipped versions agree ────────────────────────────────────────────────────
echo "=== shipped versions agree ==="
# shellcheck disable=SC2031 # reads the real list; the fixture's override above lived only in its subshell
for _file in "${VERSION_FILES[@]}"; do
  assert_eq "${_file#"$SCRIPT_DIR"/} matches plugin.json" "$(current_version)" "$(version_in "$_file")"
done

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
printf "Results: %d passed, %d failed\n" "$PASS" "$FAIL"
[[ $FAIL -eq 0 ]]
