#!/usr/bin/env bash
# Run: bash skills/session-log/tests/test_omp_restart_status.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
WORKROOT="$(mktemp -d)"
TEST_HOME="$WORKROOT/home"
OMP_PID=""
# How long to wait for the extension process and its background repair: 50 polls 0.1 s apart.
readonly POLL_ATTEMPTS=50
readonly POLL_SECONDS=0.1
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

# The omp-goodies marketplace plugin as omp caches it: the repository root, whose package.json names the extension.
PLUGIN="$TEST_HOME/.omp/plugins/cache/plugins/user538295___omp-goodies___1.19.0"
mkdir -p "$PLUGIN/skills"
cp "$REPO/package.json" "$PLUGIN/package.json"
cp -R "$REPO/skills/session-log" "$PLUGIN/skills/session-log"
OMP_SKILL="$PLUGIN/skills/session-log"
OMP_EXTENSION="$PLUGIN/$(jq -r '.omp.extensions[0]' "$PLUGIN/package.json")"
session_log() {
  env -u PI_CODING_AGENT_DIR -u OMP_PROMPT_LOG_DIR HOME="$TEST_HOME" bash "$OMP_SKILL/install.sh" --harness omp --arguments "$1"
}
expect() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$actual" != "$expected" ]]; then
    printf 'FAIL: %s (expected: %s; actual: %s)\n' "$label" "$expected" "$actual" >&2
    exit 1
  fi
}
expect "OMP activation before the extension loads" 'OMP: on — restart required' "$(session_log on)"

# What `on` left in older releases: a package copy seeded with its ownership manifest and usage link,
# and the extension link into it. Loading the plugin's extension removes them in the background.
SEEDED="$TEST_HOME/.omp/agent/skills/session-log"
LEGACY_EXTENSION="$TEST_HOME/.omp/agent/extensions/session-log.js"
mkdir -p "$(dirname "$SEEDED")" "$(dirname "$LEGACY_EXTENSION")"
cp -R "$REPO/skills/session-log" "$SEEDED"
python3 - "$SEEDED" <<'PY'
import hashlib, pathlib, sys
copy = pathlib.Path(sys.argv[1])
lines = ["format=1", "owner=universal-session-log", "version=2.0.0"]
for path in sorted(item for item in copy.rglob("*") if item.is_file()):
    lines += [f"asset={path.relative_to(copy)}", f"sha256={hashlib.sha256(path.read_bytes()).hexdigest()}"]
(copy / ".universal-session-log.manifest").write_text("\n".join(lines) + "\n")
PY
mkdir "$SEEDED/scripts"
ln -s "$SEEDED/adapters/omp/session_log_usage.ts" "$SEEDED/scripts/session_log_usage.ts"
ln -s "$SEEDED/adapters/omp/session-log.js" "$LEGACY_EXTENSION"

# A restarted OMP process imports the plugin's extension and remains alive while status is queried.
env -u PI_CODING_AGENT_DIR -u OMP_PROMPT_LOG_DIR HOME="$TEST_HOME" OMP_EXTENSION="$OMP_EXTENSION" \
  bun --eval 'const adapter = await import(process.env.OMP_EXTENSION); adapter.default({ registerCommand() {}, on() {} }); console.log("ready"); setInterval(() => {}, 1000);' \
  >"$WORKROOT/omp.out" 2>"$WORKROOT/omp.err" &
OMP_PID=$!
for ((attempt = 0; attempt < POLL_ATTEMPTS; attempt++)); do
  [[ -s "$WORKROOT/omp.out" ]] && break
  if ! kill -0 "$OMP_PID" 2>/dev/null; then
    printf 'FAIL: restarted OMP extension exited: %s\n' "$(cat "$WORKROOT/omp.err")" >&2
    exit 1
  fi
  sleep "$POLL_SECONDS"
done
if [[ "$(cat "$WORKROOT/omp.out")" != ready ]]; then
  printf 'FAIL: restarted OMP extension did not load: %s\n' "$(cat "$WORKROOT/omp.err")" >&2
  exit 1
fi
# The repair holds the install lock until it exits, after its last removal.
INSTALL_LOCK="$TEST_HOME/.omp/agent/session-log/.install.lock"
for ((attempt = 0; attempt < POLL_ATTEMPTS; attempt++)); do
  [[ -e "$SEEDED" || -L "$LEGACY_EXTENSION" || -e "$INSTALL_LOCK" ]] || break
  sleep "$POLL_SECONDS"
done
expect "the extension's load-time repair removes the seeded install" "" \
  "$(find "$TEST_HOME/.omp/agent/skills" "$TEST_HOME/.omp/agent/extensions" -mindepth 1 -maxdepth 1 -print)"

expect "OMP post-restart status" 'OMP: on' "$(session_log status)"
# The loaded extension reads the flag at every prompt, so off and on take effect without a restart.
expect "OMP off while loaded" 'OMP: off' "$(session_log off)"
expect "OMP on while loaded" 'OMP: on' "$(session_log on)"
expect "OMP status after off and on" 'OMP: on' "$(session_log status)"
kill "$OMP_PID"
wait "$OMP_PID" 2>/dev/null || true
OMP_PID=""
expect "OMP post-exit status" 'OMP: on — restart required' "$(session_log status)"
printf 'PASS: OMP post-restart status\n'
