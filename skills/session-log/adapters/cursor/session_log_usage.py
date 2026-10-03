#!/usr/bin/env python3
# universal-session-log: managed
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

NATIVE_PATH = str(Path(__file__).resolve().parents[1] / "native")
if NATIVE_PATH not in sys.path:
    sys.path.insert(0, NATIVE_PATH)
from session_log_hook import SAFE_ID, TOKENS_SUFFIX, is_untrusted_descriptor, token_record, workspace_key

REPORTED_FIELDS = ("input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens")
READ_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_NONBLOCK
TOKEN_FILE_PREFIX = "session_"
STATE_DB_PATHS = {
    "darwin": Path("Library/Application Support/Cursor/User/globalStorage/state.vscdb"),
    "linux": Path(".config/Cursor/User/globalStorage/state.vscdb"),
}
SUBAGENTS_HEADER = "SUBAGENTS (final context tokens each, not billed totals)"
# Cursor stores no subagent token usage; each subagent's final context size is a floor, not a billed total.
SUBAGENT_CONTEXT_QUERY = """
select count(*), coalesce(sum(case
    when json_type(value, '$.contextTokensUsed') = 'integer' and json_extract(value, '$.contextTokensUsed') >= 0
    then json_extract(value, '$.contextTokensUsed') end), 0)
from cursorDiskKV
where key like 'composerData:%' and json_extract(value, '$.subagentInfo.rootParentConversationId') = ?
"""


class SubagentsUnavailable(Exception):
    pass


def fail(message):
    print(message, file=sys.stderr)
    raise SystemExit(1)


def latest_token_file(logs_root, workspace="*", session="*"):
    files = [
        path for path in logs_root.glob(f"{workspace}/{TOKEN_FILE_PREFIX}{session}{TOKENS_SUFFIX}")
        if path.is_file() and not path.is_symlink()
    ]
    return max(files, key=lambda path: path.stat().st_mtime) if files else None


def latest_workspace_token_file(logs_root, cwd):
    # Cursor hooks key logs by the workspace root, so usage from a subdirectory must search upward.
    for directory in (cwd, *cwd.parents):
        path = latest_token_file(logs_root, workspace=workspace_key({"cwd": str(directory)}))
        if path is not None:
            return path
    return None


def resolve_token_file(logs_root, target):
    if target == "--latest":
        path = latest_workspace_token_file(logs_root, Path(os.getcwd()))
        if path is None:
            fail("no Cursor token usage records found for the current project")
        return path
    if not SAFE_ID.fullmatch(target):
        fail(f"invalid Cursor session id: {target}")
    path = latest_token_file(logs_root, session=target)
    if path is None:
        fail(f"Cursor session has no token usage records: {target}")
    return path


def open_token_file(path):
    try:
        descriptor = os.open(path, READ_FLAGS)
    except OSError as error:
        fail(f"cannot read Cursor token usage records: {error}")
    if is_untrusted_descriptor(os.fstat(descriptor)):
        os.close(descriptor)
        fail(f"unsafe Cursor token usage file: {path}")
    return os.fdopen(descriptor, encoding="utf-8", errors="replace")


def parse_record(line):
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    return token_record(record) if isinstance(record, dict) else None


def aggregate_tokens(handle):
    # Stop hooks append without deduplication; a redelivered generation counts once, last record wins.
    generations = {}
    for line in handle:
        record = parse_record(line)
        if record is not None:
            generations[record["generation_id"]] = record
    totals = {key: sum(record.get(key, 0) for record in generations.values()) for key in REPORTED_FIELDS}
    totals["total_tokens"] = totals["input_tokens"] + totals["output_tokens"]
    return len(generations), totals


def state_db_path(platform):
    relative = STATE_DB_PATHS.get(platform)
    if relative is None:
        raise SubagentsUnavailable(f"unsupported platform: {platform}")
    path = Path.home() / relative
    if not path.is_file():
        raise SubagentsUnavailable(f"Cursor state database not found: {path}")
    return path


def subagent_context_tokens(db_path, session_id):
    try:
        with closing(sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)) as connection:
            return connection.execute(SUBAGENT_CONTEXT_QUERY, (session_id,)).fetchone()
    except sqlite3.Error as error:
        raise SubagentsUnavailable(f"cannot read Cursor state database: {error}") from error


def subagent_lines(session_id, platform=sys.platform):
    try:
        count, tokens = subagent_context_tokens(state_db_path(platform), session_id)
    except SubagentsUnavailable as error:
        return [f"subagents: unavailable: {error}"]
    return [f"subagents: {count}", f"final_context_tokens: {tokens}"]


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    args = args or ["--latest"]
    if len(args) != 1:
        fail("usage accepts exactly one Cursor session target")
    token_file = resolve_token_file(Path.home() / ".cursor" / "prompt-logs", args[0])
    with open_token_file(token_file) as handle:
        records, totals = aggregate_tokens(handle)
    if not records:
        fail(f"Cursor session has no token usage records: {token_file}")
    print(f"session: {token_file}")
    print("TOTAL")
    for key, value in totals.items():
        print(f"{key}: {value}")
    print(SUBAGENTS_HEADER)
    session_id = token_file.name[len(TOKEN_FILE_PREFIX):-len(TOKENS_SUFFIX)]
    for line in subagent_lines(session_id):
        print(line)


if __name__ == "__main__":
    main()
