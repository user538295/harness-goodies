#!/usr/bin/env bash
# Tests for the fallback locator blocks in SKILL.md — run: bash skills/estimate/tests/test_locator.sh
#
# SKILL.md carries two copies of the shared harness-neutral locator loop (.claude/rules/skill-paths.md): one
# prints this skill's directory (probe estimate.py, override ESTIMATE_HOME), one prints session-log's
# (probe session_log.py, override SESSION_LOG_HOME). These tests extract each loop, and each whole bash block
# around it, from SKILL.md verbatim and run them against fixture layouts under a sandbox $HOME, so the doc
# and the code under test cannot drift apart.
set -u

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$TESTS_DIR/.." && pwd)"
GOLDEN_SKILL="$(cd "$SKILL_DIR/.." && pwd)/clean-code-review/SKILL.md"
WORKROOT="$(mktemp -d)"
RESULTS="$WORKROOT/results"
STARTED="$WORKROOT/started"
BLOCK_STDERR="$WORKROOT/block.stderr"
CURRENT=""

t()   { CURRENT="$1"; echo "$1" >> "$STARTED"; }
ok()  { echo "PASS: $CURRENT" >> "$RESULTS"; }
bad() { echo "FAIL: $CURRENT — $1" >> "$RESULTS"; echo "FAIL: $CURRENT — $1" >&2; }

assert_eq() { if [ "$1" = "$2" ]; then ok; else bad "expected [$2], got [$1]"; fi; }

# Every resolution loop of a SKILL.md: from `BASE=""; for d in` through the closing `done`.
resolution_loops() { grep -o 'BASE=""; for d in .*; done' "$1"; }
# The loop whose override variable is $2.
loop_for() { resolution_loops "$1" | grep -F "\"\${$2:-}\""; }
# The bash block of SKILL.md whose locator uses override variable $1, without its fences.
# shellcheck disable=SC2016 # the sed addresses match literal fence lines, not shell expressions
block_for() { sed -n '/^```bash$/,/^```$/p' "$SKILL_DIR/SKILL.md" | sed '/^```/d' | grep -F "\"\${$1:-}\""; }

newhome() { mktemp -d "$WORKROOT/home.XXXXXX"; }

# run_suite <skill name> <override variable> <probe file>
run_suite() {
  local NAME="$1" VAR="$2" PROBE="$3" LOOP BLOCK
  LOOP="$(loop_for "$SKILL_DIR/SKILL.md" "$VAR")"
  BLOCK="$(block_for "$VAR")"

  # A package copy as a plugin ships it: SKILL.md beside the probe file (a stub here).
  mkpackage() { mkdir -p "$1"; printf -- '---\nname: %s\n---\n' "$NAME" > "$1/SKILL.md"; printf 'stub\n' > "$1/$PROBE"; }
  # A copy without the probe file: SKILL.md only.
  mkbare() { mkdir -p "$1"; printf -- '---\nname: %s\n---\n' "$NAME" > "$1/SKILL.md"; }

  # resolve <home> [cwd] → prints the BASE the loop picks (empty when nothing matched)
  resolve() {
    local home="$1" cwd="${2:-}"
    [ -n "$cwd" ] || cwd="$(mktemp -d "$WORKROOT/project.XXXXXX")"
    (cd "$cwd" && env HOME="$home" "$VAR=${OVERRIDE:-}" bash -c "$LOOP"$'\nprintf %s "$BASE"')
  }
  # run_block <home> → stdout of the whole locator block, then `exit <status>`; its stderr goes to $BLOCK_STDERR
  run_block() {
    local cwd
    cwd="$(mktemp -d "$WORKROOT/project.XXXXXX")"
    (cd "$cwd" && env HOME="$1" "$VAR=" bash -c "$BLOCK" 2>"$BLOCK_STDERR"; echo "exit $?")
  }

  ( t "$NAME: SKILL.md carries exactly one resolution loop and one bash block for it"
    assert_eq "$(printf '%s\n' "$LOOP" | grep -c .)/$(printf '%s\n' "$BLOCK" | grep -c .)" "1/1" )

  ( t "$NAME: the loop is the golden clean-code-review loop with only the name, override variable, and probe file swapped"
    golden="$(resolution_loops "$GOLDEN_SKILL" | sed -e "s/clean-code-review/$NAME/g" -e "s/CCR_HOME/$VAR/g" -e "s|scripts/collect\.sh|$PROBE|g")"
    assert_eq "$LOOP" "$golden" )

  ( t "$NAME: each user-level root resolves: Claude Code, Cursor, OpenCode, omp, Codex, shared"
    for root in .agents/skills .claude/skills .cursor/skills .config/opencode/skills .omp/agent/skills .codex/skills; do
      H="$(newhome)"; mkpackage "$H/$root/$NAME"
      assert_eq "$(resolve "$H")" "$H/$root/$NAME"
    done )

  ( t "$NAME: a copy without $PROBE is skipped at every user-level root in favour of the plugin cache"
    for root in .agents/skills .claude/skills .cursor/skills .config/opencode/skills .omp/agent/skills .codex/skills; do
      H="$(newhome)"; mkbare "$H/$root/$NAME"
      mkpackage "$H/.claude/plugins/cache/u/claude-goodies/1.20.0/skills/$NAME"
      assert_eq "$(resolve "$H")" "$H/.claude/plugins/cache/u/claude-goodies/1.20.0/skills/$NAME"
    done )

  ( t "$NAME: each project-level root resolves and beats a user-level copy"
    for root in .agents/skills .claude/skills .cursor/skills .opencode/skills .codex/skills; do
      H="$(newhome)"; mkpackage "$H/.agents/skills/$NAME"
      P="$(mktemp -d "$WORKROOT/project.XXXXXX")"; mkpackage "$P/$root/$NAME"
      assert_eq "$(resolve "$H" "$P")" "$root/$NAME"
    done )

  ( t "$NAME: $VAR beats every discovered copy, and is ignored without $PROBE"
    H="$(newhome)"; mkpackage "$H/.agents/skills/$NAME"
    O="$(mktemp -d "$WORKROOT/override.XXXXXX")"; mkpackage "$O"
    assert_eq "$(OVERRIDE="$O" resolve "$H")" "$O"
    B="$(mktemp -d "$WORKROOT/bare-override.XXXXXX")"; mkbare "$B"
    assert_eq "$(OVERRIDE="$B" resolve "$H")" "$H/.agents/skills/$NAME" )

  ( t "$NAME: a dangling skill-root link is skipped, a valid one followed"
    H="$(newhome)"; mkdir -p "$H/.claude/skills"
    ln -s "$H/missing/$NAME" "$H/.claude/skills/$NAME"
    mkpackage "$H/.claude/plugins/cache/u/claude-goodies/1.20.0/skills/$NAME"
    assert_eq "$(resolve "$H")" "$H/.claude/plugins/cache/u/claude-goodies/1.20.0/skills/$NAME"
    H="$(newhome)"; mkpackage "$H/real/$NAME"; mkdir -p "$H/.claude/skills"
    ln -s "$H/real/$NAME" "$H/.claude/skills/$NAME"
    assert_eq "$(resolve "$H")" "$H/.claude/skills/$NAME" )

  ( t "$NAME: Claude Code, omp, and Codex caches pick the newest version by version sort"
    H="$(newhome)"
    mkpackage "$H/.claude/plugins/cache/u/claude-goodies/1.9.0/skills/$NAME"
    mkpackage "$H/.claude/plugins/cache/u/claude-goodies/1.10.0/skills/$NAME"
    assert_eq "$(resolve "$H")" "$H/.claude/plugins/cache/u/claude-goodies/1.10.0/skills/$NAME"
    H="$(newhome)"
    mkpackage "$H/.omp/plugins/cache/plugins/u___omp-goodies___1.9.0/skills/$NAME"
    mkpackage "$H/.omp/plugins/cache/plugins/u___omp-goodies___1.10.0/skills/$NAME"
    assert_eq "$(resolve "$H")" "$H/.omp/plugins/cache/plugins/u___omp-goodies___1.10.0/skills/$NAME"
    H="$(newhome)"
    mkpackage "$H/.codex/plugins/cache/u/codex-goodies/1.9.0/skills/$NAME"
    mkpackage "$H/.codex/plugins/cache/u/codex-goodies/1.10.0/skills/$NAME"
    assert_eq "$(resolve "$H")" "$H/.codex/plugins/cache/u/codex-goodies/1.10.0/skills/$NAME" )

  ( t "$NAME: Cursor and OpenCode caches pick the most recently installed copy"
    H="$(newhome)"; C="$H/.cursor/plugins/cache/u/cursor-goodies"
    mkpackage "$C/0000/skills/$NAME"; touch -t 202001010000 "$C/0000/skills/$NAME"
    mkpackage "$C/ffff/skills/$NAME"
    assert_eq "$(resolve "$H")" "$C/ffff/skills/$NAME"
    H="$(newhome)"; C="$H/.cache/opencode/npm/git-harness-goodies-abc"
    mkpackage "$C/1/node_modules/opencode-goodies/skills/$NAME"
    touch -t 202001010000 "$C/1/node_modules/opencode-goodies/skills/$NAME"
    mkpackage "$C/2/node_modules/opencode-goodies/skills/$NAME"
    assert_eq "$(resolve "$H")" "$C/2/node_modules/opencode-goodies/skills/$NAME" )

  ( t "$NAME: HOME with a space in its path still resolves"
    H="$(newhome)/my home"; mkpackage "$H/.agents/skills/$NAME"
    assert_eq "$(resolve "$H")" "$H/.agents/skills/$NAME" )

  ( t "$NAME: the block prints the newest plugin-cache copy and exits 0 with nothing on stderr"
    H="$(newhome)"; mkbare "$H/.claude/skills/$NAME"
    mkpackage "$H/.claude/plugins/cache/u/claude-goodies/1.9.0/skills/$NAME"
    mkpackage "$H/.claude/plugins/cache/u/claude-goodies/1.10.0/skills/$NAME"
    assert_eq "$(run_block "$H")" "$H/.claude/plugins/cache/u/claude-goodies/1.10.0/skills/$NAME"$'\nexit 0'
    assert_eq "$(cat "$BLOCK_STDERR")" "" )

  ( t "$NAME: not found prints the error with the $VAR hint on stderr and nothing on stdout"
    assert_eq "$(run_block "$(newhome)")" "exit 1"
    assert_eq "$(cat "$BLOCK_STDERR")" "ERROR: $NAME not found in any known skills root — set $VAR=<skill dir>" )
}

( t "SKILL.md carries exactly two resolution loops"
  assert_eq "$(resolution_loops "$SKILL_DIR/SKILL.md" | grep -c .)" "2" )

run_suite estimate ESTIMATE_HOME estimate.py
run_suite session-log SESSION_LOG_HOME session_log.py

# ----------------------------------------------------------------

PASS="$(grep -c '^PASS' "$RESULTS")"
FAIL="$(grep -c '^FAIL' "$RESULTS")"
# A test that died before its first assertion records neither PASS nor FAIL: count it as a failure.
while IFS= read -r name; do
  grep -qxF "PASS: $name" "$RESULTS" || grep -qF "FAIL: $name — " "$RESULTS" || { echo "FAIL: $name — no assertion ran" >&2; FAIL=$((FAIL + 1)); }
done < "$STARTED"
if command -v trash >/dev/null; then trash "$WORKROOT"; elif [ -d ~/.Trash ]; then mv "$WORKROOT" ~/.Trash/; fi
echo ""
echo "passed: $PASS  failed: $FAIL"
[ "$FAIL" -eq 0 ]
