#!/usr/bin/env bash
# Tests for the fallback package locator in SKILL.md — run: bash skills/session-log/tests/test_locator.sh
#
# When the host announced no skill directory holding install.sh, SKILL.md resolves `$BASE` with the
# shared harness-neutral locator loop (.claude/rules/skill-paths.md). These tests extract that loop, and
# the whole bash block around it, from SKILL.md verbatim and run them against fixture layouts under a
# sandbox $HOME, so the doc and the code under test cannot drift apart.
set -u

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$TESTS_DIR/.." && pwd)"
GOLDEN_SKILL="$(cd "$SKILL_DIR/.." && pwd)/clean-code-review/SKILL.md"
WORKROOT="$(mktemp -d)"
RESULTS="$WORKROOT/results"
CURRENT=""

t()   { CURRENT="$1"; }
ok()  { echo "PASS" >> "$RESULTS"; }
bad() { echo "FAIL: $CURRENT — $1" >> "$RESULTS"; echo "FAIL: $CURRENT — $1" >&2; }

assert_eq() { if [ "$1" = "$2" ]; then ok; else bad "expected [$2], got [$1]"; fi; }

# The resolution loop of each SKILL.md: from `BASE=""; for d in` through the closing `done`.
resolution_loop() { grep -o 'BASE=""; for d in .*; done' "$1"; }
LOOP="$(resolution_loop "$SKILL_DIR/SKILL.md")"

# The bash block of SKILL.md, from its first line through its closing fence.
# shellcheck disable=SC2016 # the sed addresses match literal fence lines, not shell expressions
BLOCK="$(sed -n '/^```bash$/,/^```$/p' "$SKILL_DIR/SKILL.md" | sed '1d;$d')"

# A package copy as a plugin ships it: SKILL.md beside an install.sh that reports how it was run.
mkpackage() {
  mkdir -p "$1"; printf -- '---\nname: session-log\n---\n' > "$1/SKILL.md"
  printf '#!/usr/bin/env bash\nprintf "ran %%s\\n" "$*"\n' > "$1/install.sh"
}
# A directory holding only a SKILL.md: the loop accepts it, but it has no install.sh to run.
mkskillonly() { mkdir -p "$1"; printf -- '---\nname: session-log\n---\n' > "$1/SKILL.md"; }
# A copy the legacy cleanup kept: its SKILL.md removed so no harness loads it, install.sh still present.
mkneutralized() { mkdir -p "$1"; printf '#!/usr/bin/env bash\n' > "$1/install.sh"; }

# resolve <home> [cwd] → prints the BASE the loop picks (empty when nothing matched)
resolve() {
  local home="$1" cwd="${2:-}"
  [ -n "$cwd" ] || cwd="$(mktemp -d "$WORKROOT/project.XXXXXX")"
  (cd "$cwd" && HOME="$home" SESSION_LOG_HOME="${SESSION_LOG_HOME_OVERRIDE:-}" bash -c "$LOOP"$'\nprintf %s "$BASE"')
}
newhome() { mktemp -d "$WORKROOT/home.XXXXXX"; }

# run_block <home> [announced BASE] → stdout and stderr of the whole SKILL.md block run as host claude
# with arguments `status`, then `exit <status>`
run_block() {
  local home="$1" announced="${2:-}" cwd script
  cwd="$(mktemp -d "$WORKROOT/project.XXXXXX")"
  script="$(printf '%s\n' "$BLOCK" | sed -e "s|^harness=.*|harness='claude'|" \
    -e "s|^BASE=.*|BASE='$announced'|" -e "s|^arguments=.*|arguments='status'|")"
  (cd "$cwd" && HOME="$home" SESSION_LOG_HOME="" bash -c "$script" 2>&1; echo "exit $?")
}

# ---------------------------------------------------------------- guards on the loop itself

( t "SKILL.md carries exactly one resolution loop"
  assert_eq "$(printf '%s\n' "$LOOP" | grep -c .)" "1" )

( t "the loop is the golden clean-code-review loop with only the name, override variable, and probe file swapped"
  golden="$(resolution_loop "$GOLDEN_SKILL" | sed -e 's/clean-code-review/session-log/g' -e 's/CCR_HOME/SESSION_LOG_HOME/g' -e 's|scripts/collect\.sh|SKILL.md|g')"
  assert_eq "$LOOP" "$golden" )

# ---------------------------------------------------------------- resolution per layout

( t "a neutralized copy at a legacy root is skipped in favour of the plugin cache"
  H="$(newhome)"; mkneutralized "$H/.claude/skills/session-log"
  mkpackage "$H/.claude/plugins/cache/u/claude-goodies/1.20.0/skills/session-log"
  assert_eq "$(resolve "$H")" "$H/.claude/plugins/cache/u/claude-goodies/1.20.0/skills/session-log" )

( t "a neutralized copy is skipped at every user-level root"
  for root in .agents/skills .claude/skills .cursor/skills .config/opencode/skills .omp/agent/skills .codex/skills; do
    H="$(newhome)"; mkneutralized "$H/$root/session-log"
    assert_eq "$(resolve "$H")" ""
  done )

( t "each user-level root resolves: Claude Code, Cursor, OpenCode, omp, Codex, shared"
  for root in .agents/skills .claude/skills .cursor/skills .config/opencode/skills .omp/agent/skills .codex/skills; do
    H="$(newhome)"; mkpackage "$H/$root/session-log"
    assert_eq "$(resolve "$H")" "$H/$root/session-log"
  done )

( t "project-level .agents/skills (cwd) beats a user-level copy"
  H="$(newhome)"; mkpackage "$H/.agents/skills/session-log"
  P="$(mktemp -d "$WORKROOT/project.XXXXXX")"; mkpackage "$P/.agents/skills/session-log"
  assert_eq "$(resolve "$H" "$P")" ".agents/skills/session-log" )

( t "SESSION_LOG_HOME beats every discovered copy, and is ignored without the probe file"
  H="$(newhome)"; mkpackage "$H/.agents/skills/session-log"
  O="$WORKROOT/override"; mkpackage "$O"
  assert_eq "$(SESSION_LOG_HOME_OVERRIDE="$O" resolve "$H")" "$O"
  N="$WORKROOT/neutralized-override"; mkneutralized "$N"
  assert_eq "$(SESSION_LOG_HOME_OVERRIDE="$N" resolve "$H")" "$H/.agents/skills/session-log" )

( t "a dangling skill-root link is skipped, a valid one followed"
  H="$(newhome)"; mkdir -p "$H/.claude/skills"
  ln -s "$H/missing/session-log" "$H/.claude/skills/session-log"
  mkpackage "$H/.claude/plugins/cache/u/claude-goodies/1.20.0/skills/session-log"
  assert_eq "$(resolve "$H")" "$H/.claude/plugins/cache/u/claude-goodies/1.20.0/skills/session-log"
  H="$(newhome)"; mkpackage "$H/real/session-log"; mkdir -p "$H/.claude/skills"
  ln -s "$H/real/session-log" "$H/.claude/skills/session-log"
  assert_eq "$(resolve "$H")" "$H/.claude/skills/session-log" )

( t "Claude Code, omp, and Codex caches pick the newest version by version sort"
  H="$(newhome)"
  mkpackage "$H/.claude/plugins/cache/u/claude-goodies/1.9.0/skills/session-log"
  mkpackage "$H/.claude/plugins/cache/u/claude-goodies/1.10.0/skills/session-log"
  assert_eq "$(resolve "$H")" "$H/.claude/plugins/cache/u/claude-goodies/1.10.0/skills/session-log"
  H="$(newhome)"
  mkpackage "$H/.omp/plugins/cache/plugins/u___omp-goodies___1.9.0/skills/session-log"
  mkpackage "$H/.omp/plugins/cache/plugins/u___omp-goodies___1.10.0/skills/session-log"
  assert_eq "$(resolve "$H")" "$H/.omp/plugins/cache/plugins/u___omp-goodies___1.10.0/skills/session-log"
  H="$(newhome)"
  mkpackage "$H/.codex/plugins/cache/u/codex-goodies/1.9.0/skills/session-log"
  mkpackage "$H/.codex/plugins/cache/u/codex-goodies/1.10.0/skills/session-log"
  assert_eq "$(resolve "$H")" "$H/.codex/plugins/cache/u/codex-goodies/1.10.0/skills/session-log" )

( t "Cursor and OpenCode caches pick the most recently installed copy"
  H="$(newhome)"; C="$H/.cursor/plugins/cache/u/cursor-goodies"
  mkpackage "$C/ffff/skills/session-log"; touch -t 202001010000 "$C/ffff/skills/session-log"
  mkpackage "$C/0000/skills/session-log"
  assert_eq "$(resolve "$H")" "$C/0000/skills/session-log"
  H="$(newhome)"; C="$H/.cache/opencode/npm/git-harness-goodies-abc"
  mkpackage "$C/2/node_modules/opencode-goodies/skills/session-log"
  touch -t 202001010000 "$C/2/node_modules/opencode-goodies/skills/session-log"
  mkpackage "$C/1/node_modules/opencode-goodies/skills/session-log"
  assert_eq "$(resolve "$H")" "$C/1/node_modules/opencode-goodies/skills/session-log" )

( t "HOME with a space in its path still resolves"
  H="$(newhome)/my home"; mkpackage "$H/.agents/skills/session-log"
  assert_eq "$(resolve "$H")" "$H/.agents/skills/session-log" )

( t "nothing installed anywhere leaves BASE empty"
  assert_eq "$(resolve "$(newhome)")" "" )

# ---------------------------------------------------------------- the whole block

( t "the announced directory runs its install.sh with the harness and the arguments"
  H="$(newhome)"; A="$WORKROOT/announced"; mkpackage "$A"
  assert_eq "$(run_block "$H" "$A")" $'ran --harness claude --arguments status\nexit 0' )

( t "a package the loop finds runs its install.sh"
  H="$(newhome)"; mkpackage "$H/.agents/skills/session-log"
  assert_eq "$(run_block "$H")" $'ran --harness claude --arguments status\nexit 0' )

( t "a loop match without install.sh is named as incomplete, never run"
  H="$(newhome)"; mkskillonly "$H/.agents/skills/session-log"
  assert_eq "$(run_block "$H")" "session-log: $H/.agents/skills/session-log is not a complete package (no install.sh) — set SESSION_LOG_HOME=<skill dir>"$'\nexit 1' )

( t "nothing installed anywhere names the missing package"
  assert_eq "$(run_block "$(newhome)")" $'session-log: complete package not found in any known skills root — set SESSION_LOG_HOME=<skill dir>\nexit 1' )

# ----------------------------------------------------------------

PASS="$(grep -c '^PASS$' "$RESULTS")"
FAIL="$(grep -c '^FAIL' "$RESULTS")"
if command -v trash >/dev/null; then trash "$WORKROOT"; elif [ -d ~/.Trash ]; then mv "$WORKROOT" ~/.Trash/; fi
echo ""
echo "passed: $PASS  failed: $FAIL"
[ "$FAIL" -eq 0 ]
