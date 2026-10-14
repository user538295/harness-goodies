#!/usr/bin/env bash
# The session-log skill's entrypoint: runs one command through this package's CLI for the harness
# the host names. The harness plugin ships this package, so nothing is copied anywhere.
set -euo pipefail

readonly PACKAGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
HARNESS=""
ARGUMENTS="status"
ARGUMENTS_SET=0

SESSION_LOG_LOG_PREFIX="session-log"
# shellcheck source=lib/common.sh
source "$PACKAGE_ROOT/lib/common.sh"

parse_args() {
  local arg
  while (($#)); do
    arg="$1"
    case "$arg" in
      --harness)
        (($# >= 2)) || fail "--harness requires a value"
        [[ -z "$HARNESS" ]] || fail "only one --harness value is allowed"
        HARNESS="$2"
        shift 2
        ;;
      --harness=*)
        [[ -z "$HARNESS" ]] || fail "only one --harness value is allowed"
        HARNESS="${arg#*=}"
        shift
        ;;
      --arguments)
        (($# >= 2)) || fail "--arguments requires a value"
        ((ARGUMENTS_SET == 0)) || fail "only one --arguments value is allowed"
        ARGUMENTS="$2"
        ARGUMENTS_SET=1
        shift 2
        ;;
      --arguments=*)
        ((ARGUMENTS_SET == 0)) || fail "only one --arguments value is allowed"
        ARGUMENTS="${arg#*=}"
        ARGUMENTS_SET=1
        shift
        ;;
      --help|-h)
        printf 'Usage: install.sh --harness <claude|codex|cursor|opencode|omp> [--arguments "<command and native args>"]\n'
        exit 0
        ;;
      *)
        fail "unknown argument: $arg"
        ;;
    esac
  done
  [[ -n "$HARNESS" ]] || fail "--harness is required"
}

parse_args "$@"
exec bash "$PACKAGE_ROOT/bin/session-log" --entrypoint "$HARNESS" --harness "$HARNESS" --arguments "$ARGUMENTS"
