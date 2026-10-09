#!/usr/bin/env bash
# Tests for the harness-agnostic skill locator in SKILL.md — run: bash tests/test_locator.sh
#
# SKILL.md carries a one-line fallback locator that resolves this skill's own
# directory ($BASE) across every supported harness (Claude Code, Cursor, OpenCode,
# omp, Codex) without Claude-Code-only artifacts. These tests EXTRACT that line
# verbatim from SKILL.md and run it against fixture layouts under a sandbox $HOME,
# so the doc and the code under test cannot drift apart.
set -u

SKILL="md-reviewer"
OVERRIDE_VAR="MD_REVIEWER_HOME"
MARKER="scripts/init_review.py"

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKROOT="$(mktemp -d)"
RESULTS="$(mktemp)"
CURRENT=""
OVERRIDE=""

t()   { CURRENT="$1"; }
ok()  { echo "PASS" >> "$RESULTS"; }
bad() { echo "FAIL: $CURRENT — $1" >> "$RESULTS"; echo "FAIL: $CURRENT — $1" >&2; }

assert_eq()      { [ "$1" = "$2" ] && ok || bad "expected [$2], got [$1]"; }
assert_match()   { echo "$1" | grep -qE -- "$2" && ok || bad "[$1] should match /$2/"; }
assert_nomatch() { echo "$1" | grep -qE -- "$2" && bad "[$1] should NOT match /$2/" || ok; }

# The single fallback locator line — extracted verbatim from SKILL.md (drift guard).
SNIPPET="$(grep -E '^[[:space:]]*BASE=""; for d in' "$SKILL_DIR/SKILL.md" | sed 's/^[[:space:]]*//')"
LOCATOR_LINES="$(grep -cE '^[[:space:]]*BASE=""; for d in' "$SKILL_DIR/SKILL.md")"

# A minimal skill install: <dir>/<MARKER> is the file the locator probes.
mkskill() { mkdir -p "$(dirname "$1/$MARKER")"; printf 'x\n' > "$1/$MARKER"; }

# Run the snippet with HOME sandboxed to $1 and cwd $2 (default: an empty project).
run() {
  local home="$1" cwd="${2:-}"
  [ -n "$cwd" ] || { cwd="$(mktemp -d)"; }
  OUT="$(cd "$cwd" && env HOME="$home" "$OVERRIDE_VAR=${OVERRIDE:-}" bash -c "$SNIPPET" 2>"$WORKROOT/stderr")"; RC=$?
  ERR="$(cat "$WORKROOT/stderr")"
}

# ---------------------------------------------------------------- guards on the snippet itself

( t "SKILL.md carries exactly one fallback locator line, and it is non-empty"
  assert_eq "$LOCATOR_LINES" "1"
  [ -n "$SNIPPET" ] && ok || bad "locator snippet should not be empty" )

( t "snippet carries no Claude Code-only artifacts and no jq dependency"
  assert_nomatch "$SNIPPET" 'installed_plugins\.json'
  assert_nomatch "$SNIPPET" 'CLAUDE_PLUGIN_ROOT|CLAUDE_SKILL_DIR'
  assert_nomatch "$SNIPPET" '(^|[^a-z])jq( |$)' )

( t "snippet probes every supported harness's skills root"
  for root in '\.agents/skills' '\.claude/skills' '\.cursor/skills' '\.codex/skills' '\.config/opencode/skills' '\.omp/agent/skills' 'plugins/cache'; do
    assert_match "$SNIPPET" "$root"
  done )

# ---------------------------------------------------------------- resolution per layout

( t "user-level ~/.agents/skills install (Cursor / OpenCode / omp shared root) resolves"
  H="$(mktemp -d)"; mkskill "$H/.agents/skills/$SKILL"
  run "$H"
  assert_eq "$RC" "0"; assert_eq "$OUT" "$H/.agents/skills/$SKILL" )

( t "project-level .agents/skills (cwd) beats a user-level copy"
  H="$(mktemp -d)"; mkskill "$H/.agents/skills/$SKILL"
  P="$(mktemp -d)"; mkskill "$P/.agents/skills/$SKILL"
  run "$H" "$P"
  assert_eq "$RC" "0"; assert_eq "$OUT" ".agents/skills/$SKILL" )

( t "$OVERRIDE_VAR override beats every discovered copy"
  H="$(mktemp -d)"; mkskill "$H/.agents/skills/$SKILL"
  O="$(mktemp -d)/custom-$SKILL"; mkskill "$O"
  OVERRIDE="$O" run "$H"
  assert_eq "$RC" "0"; assert_eq "$OUT" "$O" )

( t "$OVERRIDE_VAR pointing at a dir without the marker is ignored, not trusted"
  H="$(mktemp -d)"; mkskill "$H/.agents/skills/$SKILL"
  OVERRIDE="$(mktemp -d)" run "$H"
  assert_eq "$RC" "0"; assert_eq "$OUT" "$H/.agents/skills/$SKILL" )

( t "dangling ~/.claude/skills symlink (stale plugin cache) is skipped, falls to the live cache"
  H="$(mktemp -d)"; mkdir -p "$H/.claude/skills"
  ln -s "$H/.claude/plugins/cache/u/claude-goodies/1.12.1/skills/$SKILL" "$H/.claude/skills/$SKILL"   # target never created
  mkskill "$H/.claude/plugins/cache/u/claude-goodies/1.13.0/skills/$SKILL"
  run "$H"
  assert_eq "$RC" "0"; assert_eq "$OUT" "$H/.claude/plugins/cache/u/claude-goodies/1.13.0/skills/$SKILL" )

( t "valid ~/.claude/skills symlink is followed"
  H="$(mktemp -d)"; mkskill "$H/real/$SKILL"; mkdir -p "$H/.claude/skills"
  ln -s "$H/real/$SKILL" "$H/.claude/skills/$SKILL"
  run "$H"
  assert_eq "$RC" "0"; assert_eq "$OUT" "$H/.claude/skills/$SKILL" )

( t "plugin cache with several versions picks the newest by version sort, not lexically"
  H="$(mktemp -d)"
  mkskill "$H/.claude/plugins/cache/u/claude-goodies/1.9.0/skills/$SKILL"
  mkskill "$H/.claude/plugins/cache/u/claude-goodies/1.10.0/skills/$SKILL"
  run "$H"
  assert_eq "$RC" "0"; assert_match "$OUT" "/1\.10\.0/skills/$SKILL$" )

( t "each harness-native user root resolves: Cursor, OpenCode, omp, Codex"
  for root in .cursor/skills .config/opencode/skills .omp/agent/skills .codex/skills; do
    H="$(mktemp -d)"; mkskill "$H/$root/$SKILL"
    run "$H"
    assert_eq "$OUT" "$H/$root/$SKILL"
  done )

( t "omp marketplace cache with several versions picks the newest by version sort"
  H="$(mktemp -d)"
  mkskill "$H/.omp/plugins/cache/plugins/u___omp-goodies___1.9.0/skills/$SKILL"
  mkskill "$H/.omp/plugins/cache/plugins/u___omp-goodies___1.10.0/skills/$SKILL"
  run "$H"
  assert_eq "$RC" "0"; assert_eq "$OUT" "$H/.omp/plugins/cache/plugins/u___omp-goodies___1.10.0/skills/$SKILL" )

( t "Cursor marketplace cache (commit-SHA dirs) picks the most recently installed copy"
  H="$(mktemp -d)"; C="$H/.cursor/plugins/cache/u/cursor-goodies"
  mkskill "$C/ffff/skills/$SKILL"; touch -t 202001010000 "$C/ffff/skills/$SKILL"
  mkskill "$C/0000/skills/$SKILL"
  run "$H"
  assert_eq "$RC" "0"; assert_eq "$OUT" "$C/0000/skills/$SKILL" )

( t "OpenCode package cache picks the most recently installed copy"
  H="$(mktemp -d)"; C="$H/.cache/opencode/npm/git-claude_goodies-abc"
  mkskill "$C/2/node_modules/opencode-goodies/skills/$SKILL"
  touch -t 202001010000 "$C/2/node_modules/opencode-goodies/skills/$SKILL"
  mkskill "$C/1/node_modules/opencode-goodies/skills/$SKILL"
  run "$H"
  assert_eq "$RC" "0"; assert_eq "$OUT" "$C/1/node_modules/opencode-goodies/skills/$SKILL" )

( t "HOME with a space in its path still resolves"
  H="$(mktemp -d)/my home"; mkskill "$H/.agents/skills/$SKILL"
  run "$H"
  assert_eq "$RC" "0"; assert_eq "$OUT" "$H/.agents/skills/$SKILL" )

( t "nothing installed anywhere -> non-zero, empty stdout, stderr names $OVERRIDE_VAR"
  H="$(mktemp -d)"
  run "$H"
  [ "$RC" -ne 0 ] && ok || bad "expected non-zero exit"
  assert_eq "$OUT" ""
  assert_match "$ERR" "$OVERRIDE_VAR" )

# ----------------------------------------------------------------

PASS="$(grep -c '^PASS$' "$RESULTS")"
FAIL="$(grep -c '^FAIL' "$RESULTS")"
rm -rf "$WORKROOT"
echo ""
echo "passed: $PASS  failed: $FAIL"
[ "$FAIL" -eq 0 ]
