"""Cursor hook records for reader tests, written into a records folder the way the hook writes them (C5): the file name and the
JSON fields come from `sessionlog.cursor_records`, so a test never repeats a key of the format. Nothing here is real user content."""
import json
import os
from datetime import datetime, timezone

from sessionlog import cursor_records

BASE_SECONDS = 1_700_000_000
NANOSECONDS_PER_SECOND = 10 ** 9
CONVERSATION = "conv-1"
WORKSPACE = "/work/proj"
PID = 4242


def at_ns(seconds):
    """The arrival time `seconds` after the base time, in nanoseconds."""
    return (BASE_SECONDS + seconds) * NANOSECONDS_PER_SECOND


def moment(seconds):
    """The same time as a timezone-aware datetime."""
    return datetime.fromtimestamp(BASE_SECONDS + seconds, timezone.utc)


def prompt_input(generation, text="hello", **fields):
    return {"conversation_id": CONVERSATION, "generation_id": generation, "prompt": text, "workspace_roots": [WORKSPACE], **fields}


def response_input(generation, text="reply", **fields):
    return {"conversation_id": CONVERSATION, "generation_id": generation, "text": text, **fields}


def stop_input(generation, **fields):
    return {"conversation_id": CONVERSATION, "generation_id": generation, "status": "completed", **fields}


def subagent_input(child, **fields):
    return {"conversation_id": CONVERSATION, "child_conversation_id": child, **fields}


def write_record(records, event, seconds, hook_input, conversation=CONVERSATION, pid=PID, raw_input=None):
    """Writes one record of `event` that arrived `seconds` after the base time under `records/<conversation>/` and returns its path.
    `raw_input` replaces the JSON text of `hook_input` as the raw hook input."""
    folder = os.path.join(records, conversation)
    os.makedirs(folder, exist_ok=True)
    arrival = at_ns(seconds)
    raw = json.dumps(hook_input, ensure_ascii=False) if raw_input is None else raw_input
    fields = cursor_records.record_fields(event, conversation, arrival, pid, raw)
    path = os.path.join(folder, cursor_records.record_file_name(arrival, event, pid))
    with open(path, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(fields, ensure_ascii=False))
    return path
