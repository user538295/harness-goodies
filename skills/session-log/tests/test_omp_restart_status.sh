#!/usr/bin/env bash
# Run: bash skills/session-log/tests/test_omp_restart_status.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
WORKROOT="$(mktemp -d)"
TEST_HOME="$WORKROOT/home"
OMP_PID=""
cleanup() {
  if [[ -n "$OMP_PID" ]]; then
    kill "$OMP_PID" 2>/dev/null || true
    wait "$OMP_PID" 2>/dev/null || true
  fi
  if command -v trash >/dev/null 2>&1; then
    trash "$WORKROOT"
  else
    mkdir -p "$HOME/.Trash"
    mv "$WORKROOT" "$HOME/.Trash/session-log-omp-restart-test-$$"
  fi
}
trap cleanup EXIT
mkdir -p "$TEST_HOME/.Trash"

env -u PI_CODING_AGENT_DIR -u OMP_PROMPT_LOG_DIR HOME="$TEST_HOME" bash "$REPO/install-universal-session-log.sh" >"$WORKROOT/install.out"
OMP_SKILL="$TEST_HOME/.omp/agent/skills/session-log"
activated="$(env -u PI_CODING_AGENT_DIR -u OMP_PROMPT_LOG_DIR HOME="$TEST_HOME" bash "$OMP_SKILL/install.sh" --harness omp --arguments on)"
if [[ "$activated" != 'OMP: on — restart required' ]]; then
  printf 'FAIL: OMP activation (expected: OMP: on — restart required; actual: %s)\n' "$activated" >&2
  exit 1
fi

# A restarted OMP process imports the installed extension and remains alive while status is queried.
env -u PI_CODING_AGENT_DIR -u OMP_PROMPT_LOG_DIR HOME="$TEST_HOME" OMP_EXTENSION="$TEST_HOME/.omp/agent/extensions/session-log.js" \
  bun --eval 'const adapter = await import(process.env.OMP_EXTENSION); adapter.default({ registerCommand() {}, on() {} }); console.log("ready"); setInterval(() => {}, 1000);' \
  >"$WORKROOT/omp.out" 2>"$WORKROOT/omp.err" &
OMP_PID=$!
for ((attempt = 0; attempt < 50; attempt++)); do
  [[ -s "$WORKROOT/omp.out" ]] && break
  if ! kill -0 "$OMP_PID" 2>/dev/null; then
    printf 'FAIL: restarted OMP extension exited: %s\n' "$(cat "$WORKROOT/omp.err")" >&2
    exit 1
  fi
  sleep 0.1
done
if [[ "$(cat "$WORKROOT/omp.out")" != ready ]]; then
  printf 'FAIL: restarted OMP extension did not load: %s\n' "$(cat "$WORKROOT/omp.err")" >&2
  exit 1
fi

status="$(env -u PI_CODING_AGENT_DIR -u OMP_PROMPT_LOG_DIR HOME="$TEST_HOME" bash "$OMP_SKILL/install.sh" --harness omp --arguments status)"
if [[ "$status" != 'OMP: on' ]]; then
  printf 'FAIL: OMP post-restart status (expected: OMP: on; actual: %s)\n' "$status" >&2
  exit 1
fi
kill "$OMP_PID"
wait "$OMP_PID" 2>/dev/null || true
OMP_PID=""
status="$(env -u PI_CODING_AGENT_DIR -u OMP_PROMPT_LOG_DIR HOME="$TEST_HOME" bash "$OMP_SKILL/install.sh" --harness omp --arguments status)"
if [[ "$status" != 'OMP: on — restart required' ]]; then
  printf 'FAIL: OMP post-exit status (expected: OMP: on — restart required; actual: %s)\n' "$status" >&2
  exit 1
fi
printf 'PASS: OMP post-restart status\n'
