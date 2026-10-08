#!/usr/bin/env python3
# universal-session-log: managed
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

MAX_META_SCAN_LINES = 64
# Ranks rollouts without a user prompt below every rollout that has one.
NO_USER_PROMPT = float("-inf")


def fail(message):
    print(message, file=sys.stderr)
    raise SystemExit(1)


def rollout_files(root):
    return [path for path in root.glob("**/*.jsonl") if path.is_file() and not path.is_symlink()]


def _read_session_meta(path):
    with path.open(encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if index >= MAX_META_SCAN_LINES:
                break
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict) or record.get("type") != "session_meta":
                continue
            value = record.get("payload")
            if not isinstance(value, dict):
                value = record.get("data")
            return value if isinstance(value, dict) else {}
    return {}


def read_session_metadata(path):
    try:
        return _read_session_meta(path)
    except OSError:
        return {}


def paths_resolve_same(left, right):
    return os.path.realpath(left) == os.path.realpath(right)


def is_current_project(cwd):
    return isinstance(cwd, str) and paths_resolve_same(cwd, os.getcwd())


def is_root_session(metadata):
    source = metadata.get("source")
    return not (isinstance(source, dict) and "subagent" in source)


def user_prompt_epoch(record):
    # Codex records each submitted prompt as a completed UserMessage item; injected context is a bare response_item.
    payload = record.get("payload")
    if record.get("type") != "event_msg" or not isinstance(payload, dict) or payload.get("type") != "item_completed":
        return None
    item = payload.get("item")
    if not isinstance(item, dict) or item.get("type") != "UserMessage":
        return None
    try:
        return datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00")).timestamp()
    except (AttributeError, KeyError, ValueError):
        return None


def last_user_prompt_epoch(path):
    latest = NO_USER_PROMPT
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                prompted = user_prompt_epoch(record) if isinstance(record, dict) else None
                if prompted is not None:
                    latest = max(latest, prompted)
    except OSError:
        pass
    return latest


def latest_project_rollout(root):
    files = []
    for path in rollout_files(root):
        metadata = read_session_metadata(path)
        if is_current_project(metadata.get("cwd")) and is_root_session(metadata):
            files.append(path)
    return max(files, key=lambda path: (last_user_prompt_epoch(path), path.stat().st_mtime)) if files else None


def matches_session(path, session_id):
    return (
        read_session_metadata(path).get("id") == session_id
        or path.stem == session_id
        or path.stem.endswith(f"-{session_id}")
    )


def find_rollout_for_session(root, session_id):
    if not re.fullmatch(r"[A-Za-z0-9._-]+", session_id):
        fail(f"invalid Codex session id: {session_id}")
    files = [path for path in rollout_files(root) if matches_session(path, session_id)]
    return max(files, key=lambda path: path.stat().st_mtime) if files else None


def extract_token_usage(record) -> Optional[dict]:
    if record.get("type") != "token_usage_record":
        return None
    value = record.get("data")
    if not isinstance(value, dict):
        value = record.get("payload")
    if isinstance(value, dict) and isinstance(value.get("usage"), dict):
        value = value["usage"]
    return value if isinstance(value, dict) else None




def is_non_negative_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def coerce_non_negative_int(value):
    return value if is_non_negative_int(value) else 0


def aggregate_tokens(handle):
    totals = {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
        "total_tokens": 0,
    }
    records = 0
    for line in handle:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        data = extract_token_usage(record)
        if data is None:
            continue
        records += 1
        for key in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens"):
            totals[key] += coerce_non_negative_int(data.get(key))
        native_total = data.get("total_tokens")
        if is_non_negative_int(native_total):
            totals["total_tokens"] += native_total
        else:
            totals["total_tokens"] += coerce_non_negative_int(data.get("input_tokens")) + coerce_non_negative_int(data.get("output_tokens"))
    return records, totals


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    args = args or ["--latest"]
    if len(args) != 1:
        fail("usage accepts exactly one rollout target")
    sessions_root = Path.home() / ".codex" / "sessions"
    target = args[0]
    if target == "--latest":
        transcript = latest_project_rollout(sessions_root)
        if transcript is None:
            fail("no Codex rollout transcript found for the current project")
    else:
        candidate = Path(target).expanduser()
        if candidate.is_file() and not candidate.is_symlink():
            transcript = candidate
        elif "/" in target or target.endswith(".jsonl"):
            fail(f"Codex rollout transcript does not exist: {candidate}")
        else:
            transcript = find_rollout_for_session(sessions_root, target)
            if transcript is None:
                fail(f"Codex session does not exist: {target}")
    try:
        with transcript.open(encoding="utf-8") as handle:
            records, totals = aggregate_tokens(handle)
    except OSError as error:
        fail(f"cannot read Codex rollout transcript: {error}")
    if not records:
        fail("Codex rollout contains no token_usage_record data")
    print(f"session: {transcript}")
    print("TOTAL")
    for key, value in totals.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
