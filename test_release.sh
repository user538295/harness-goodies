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
  [[ "$expected" == "$actual" ]] && ok "$desc" || nok "$desc" "$expected" "$actual"
}

assert_exits_0() {
  local desc="$1"; shift
  if "$@" 2>/dev/null; then ok "$desc"; else nok "$desc" "exit 0" "exit non-0"; fi
}

assert_exits_1() {
  local desc="$1"; shift
  if ! "$@" 2>/dev/null; then ok "$desc"; else nok "$desc" "exit non-0" "exit 0"; fi
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
assert_exits_0 "new command"    diff_has_new_items $'A\tcommands/cmd.md'
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
rm -f "$_tmp_json"

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
mv "$_tmp_a" "$_tmp_b" ~/.Trash/

# ── shipped versions agree ────────────────────────────────────────────────────
echo "=== shipped versions agree ==="
for _file in "${VERSION_FILES[@]}"; do
  assert_eq "${_file#"$SCRIPT_DIR"/} matches plugin.json" "$(current_version)" "$(version_in "$_file")"
done

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
printf "Results: %d passed, %d failed\n" "$PASS" "$FAIL"
[[ $FAIL -eq 0 ]]
