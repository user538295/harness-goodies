#!/usr/bin/env bash
# Tests for the Step 0 skill locator in SKILL.md — run: bash tests/test_locator.sh
#
# Step 0 tells the orchestrator to use the skill directory the harness announced
# when the skill loaded, and — only when nothing was announced — to run one
# harness-neutral fallback snippet. That snippet must resolve `$BASE` in every
# supported harness layout (Claude Code, Cursor, OpenCode, omp, Codex) without
# Claude Code-only artifacts. These tests extract the snippet from SKILL.md verbatim and
# run it against fixture layouts under a sandbox $HOME, so the doc and the code
# under test cannot drift apart.
set -u

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKROOT="$(mktemp -d)"
RESULTS="$(mktemp)"
CURRENT=""

t()   { CURRENT="$1"; }
ok()  { echo "PASS" >> "$RESULTS"; }
bad() { echo "FAIL: $CURRENT — $1" >> "$RESULTS"; echo "FAIL: $CURRENT — $1" >&2; }

assert_eq()      { [ "$1" = "$2" ] && ok || bad "expected [$2], got [$1]"; }
assert_match()   { echo "$1" | grep -qE -- "$2" && ok || bad "[$1] should match /$2/"; }
assert_nomatch() { echo "$1" | grep -qE -- "$2" && bad "[$1] should NOT match /$2/" || ok; }

# The first ```bash fence under "## Step 0" — the fallback locator, verbatim.
SNIPPET="$(awk '/^## Step 0/{s=1; next} s && /^## /{exit} s && /^```bash/{f=1; next} s && f && /^```/{exit} s && f{print}' "$SKILL_DIR/SKILL.md")"
STEP0="$(awk '/^## Step 0/{s=1} s && /^## Step 1 /{exit} s{print}' "$SKILL_DIR/SKILL.md")"

# A minimal skill install: <dir>/scripts/plan-progress.sh is the marker the locator probes.
mkskill() { mkdir -p "$1/scripts"; printf '#!/usr/bin/env bash\n' > "$1/scripts/plan-progress.sh"; }

# Run the snippet with HOME sandboxed to $1 and cwd $2 (default: an empty project).
run() {
  local home="$1" cwd="${2:-}"
  [ -n "$cwd" ] || { cwd="$(mktemp -d)"; }
  OUT="$(cd "$cwd" && HOME="$home" IMPLEMENT_HOME="${IMPLEMENT_HOME_OVERRIDE:-}" bash -c "$SNIPPET" 2>"$WORKROOT/stderr")"; RC=$?
  ERR="$(cat "$WORKROOT/stderr")"
}

# ---------------------------------------------------------------- guards on the snippet itself

( t "Step 0 has exactly one extractable bash fence, and it is non-empty"
  n="$(printf '%s\n' "$STEP0" | grep -c '^```bash')"
  assert_eq "$n" "1"
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
  H="$(mktemp -d)"; mkskill "$H/.agents/skills/implement"
  run "$H"
  assert_eq "$RC" "0"; assert_eq "$OUT" "$H/.agents/skills/implement" )

( t "project-level .agents/skills (cwd) beats a user-level copy"
  H="$(mktemp -d)"; mkskill "$H/.agents/skills/implement"
  P="$(mktemp -d)"; mkskill "$P/.agents/skills/implement"
  run "$H" "$P"
  assert_eq "$RC" "0"; assert_eq "$OUT" ".agents/skills/implement" )

( t "IMPLEMENT_HOME override beats every discovered copy"
  H="$(mktemp -d)"; mkskill "$H/.agents/skills/implement"
  O="$(mktemp -d)/custom-implement"; mkskill "$O"
  IMPLEMENT_HOME_OVERRIDE="$O" run "$H"
  assert_eq "$RC" "0"; assert_eq "$OUT" "$O" )

( t "IMPLEMENT_HOME pointing at a dir without the marker is ignored, not trusted"
  H="$(mktemp -d)"; mkskill "$H/.agents/skills/implement"
  IMPLEMENT_HOME_OVERRIDE="$(mktemp -d)" run "$H"
  assert_eq "$RC" "0"; assert_eq "$OUT" "$H/.agents/skills/implement" )

( t "dangling ~/.claude/skills symlink (stale plugin cache) is skipped, falls to the live cache"
  H="$(mktemp -d)"; mkdir -p "$H/.claude/skills"
  ln -s "$H/.claude/plugins/cache/u/claude-goodies/1.12.1/skills/implement" "$H/.claude/skills/implement"   # target never created
  mkskill "$H/.claude/plugins/cache/u/claude-goodies/1.13.0/skills/implement"
  run "$H"
  assert_eq "$RC" "0"; assert_eq "$OUT" "$H/.claude/plugins/cache/u/claude-goodies/1.13.0/skills/implement" )

( t "valid ~/.claude/skills symlink is followed"
  H="$(mktemp -d)"; mkskill "$H/real/implement"; mkdir -p "$H/.claude/skills"
  ln -s "$H/real/implement" "$H/.claude/skills/implement"
  run "$H"
  assert_eq "$RC" "0"; assert_eq "$OUT" "$H/.claude/skills/implement" )

( t "plugin cache with several versions picks the newest by version sort, not lexically"
  H="$(mktemp -d)"
  mkskill "$H/.claude/plugins/cache/u/claude-goodies/1.9.0/skills/implement"
  mkskill "$H/.claude/plugins/cache/u/claude-goodies/1.10.0/skills/implement"
  run "$H"
  assert_eq "$RC" "0"; assert_match "$OUT" "/1\.10\.0/skills/implement$" )

( t "each harness-native user root resolves: Cursor, OpenCode, omp, Codex"
  for root in .cursor/skills .config/opencode/skills .omp/agent/skills .codex/skills; do
    H="$(mktemp -d)"; mkskill "$H/$root/implement"
    run "$H"
    assert_eq "$OUT" "$H/$root/implement"
  done )

( t "omp marketplace cache with several versions picks the newest by version sort"
  H="$(mktemp -d)"
  mkskill "$H/.omp/plugins/cache/plugins/u___omp-goodies___1.9.0/skills/implement"
  mkskill "$H/.omp/plugins/cache/plugins/u___omp-goodies___1.10.0/skills/implement"
  run "$H"
  assert_eq "$RC" "0"; assert_eq "$OUT" "$H/.omp/plugins/cache/plugins/u___omp-goodies___1.10.0/skills/implement" )

( t "Cursor marketplace cache (commit-SHA dirs) picks the most recently installed copy"
  H="$(mktemp -d)"; C="$H/.cursor/plugins/cache/u/cursor-goodies"
  mkskill "$C/ffff/skills/implement"; touch -t 202001010000 "$C/ffff/skills/implement"
  mkskill "$C/0000/skills/implement"
  run "$H"
  assert_eq "$RC" "0"; assert_eq "$OUT" "$C/0000/skills/implement" )

( t "OpenCode package cache picks the most recently installed copy"
  H="$(mktemp -d)"; C="$H/.cache/opencode/npm/git-claude_goodies-abc"
  mkskill "$C/2/node_modules/opencode-goodies/skills/implement"
  touch -t 202001010000 "$C/2/node_modules/opencode-goodies/skills/implement"
  mkskill "$C/1/node_modules/opencode-goodies/skills/implement"
  run "$H"
  assert_eq "$RC" "0"; assert_eq "$OUT" "$C/1/node_modules/opencode-goodies/skills/implement" )

( t "HOME with a space in its path still resolves"
  H="$(mktemp -d)/my home"; mkskill "$H/.agents/skills/implement"
  run "$H"
  assert_eq "$RC" "0"; assert_eq "$OUT" "$H/.agents/skills/implement" )

( t "nothing installed anywhere -> non-zero, empty stdout, stderr names IMPLEMENT_HOME"
  H="$(mktemp -d)"
  run "$H"
  [ "$RC" -ne 0 ] && ok || bad "expected non-zero exit"
  assert_eq "$OUT" ""
  assert_match "$ERR" "IMPLEMENT_HOME" )

# ---------------------------------------------------------------- Step 0 prose contract

( t "Step 0 names the harness announcements the orchestrator must prefer over the snippet"
  assert_match "$STEP0" "Base directory for this skill"
  assert_match "$STEP0" "Skill directory"
  assert_match "$STEP0" "Cursor" )

( t "Step 0 states the relative-path convention: skill-root-relative paths, always resolved via \$BASE"
  assert_match "$STEP0" 'relative to the skill root'
  assert_match "$STEP0" '\$BASE/' )

# ----------------------------------------------------------------

PASS="$(grep -c '^PASS$' "$RESULTS")"
FAIL="$(grep -c '^FAIL' "$RESULTS")"
rm -rf "$WORKROOT"
echo ""
echo "passed: $PASS  failed: $FAIL"
[ "$FAIL" -eq 0 ]
