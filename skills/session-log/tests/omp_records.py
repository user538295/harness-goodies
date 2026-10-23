"""Builders of the OMP session records the tests are made of, and the constants of the text fixtures cut from real sessions. Times are
seconds after `BASE`; `at`, `iso` and `ms` give them as a datetime, an ISO text and epoch milliseconds."""
import json
import os
from datetime import datetime, timedelta, timezone

from sessionlog.omp_transcript import Transcript, parse_transcript

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "omp")
ROOT_FIXTURE = os.path.join(FIXTURES, "root_session.jsonl")
CHILD_FIXTURE = os.path.join(FIXTURES, "child_session.jsonl")
FIXTURE_ROOT_STEM = "2026-10-13T08-42-00-000Z_01a10000-0000-7000-8000-000000000001"
FIXTURE_ROOT_ID = "01a10000-0000-7000-8000-000000000001"
FIXTURE_CHILD_ID = "01a10000-0000-7000-8000-0000000000c1"
PROJECT = "-work-proj"
CWD = "/work/proj"
BASE = datetime(2026, 10, 13, 8, 42, 0, tzinfo=timezone.utc)


def at(second) -> datetime:
    return BASE + timedelta(seconds=second)


def iso(second) -> str:
    return at(second).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def ms(second) -> int:
    return int(at(second).timestamp() * 1000)


def root_of(number):
    """The file stem of a root session and its id (the stem after its first `_`), both numbered."""
    session_id = "01a10000-0000-7000-8000-00000000%04d" % number
    return "2026-10-13T08-%02d-00-000Z_%s" % (number % 60, session_id), session_id


def header_record(session_id, second=2, parent=None, cwd=CWD, **extra) -> dict:
    record = {"type": "session", "version": 3, "id": session_id, "timestamp": iso(second)}
    if cwd is not None:
        record["cwd"] = cwd
    if parent is not None:
        record["parentSession"] = parent
    return {**record, **extra}


def effort_record(second, level) -> dict:
    return {"type": "thinking_level_change", "id": "t%d" % second, "timestamp": iso(second), "thinkingLevel": level, "configured": None}


def init_record(agent) -> dict:
    return {"type": "session_init", "id": "i1", "timestamp": iso(3), "agent": agent, "task": "placeholder task"}


def user_record(second, text="ask", attribution="user", **message) -> dict:
    body = {"role": "user", "content": [{"type": "text", "text": text}], "timestamp": ms(second), **message}
    if attribution is not None:
        body["attribution"] = attribution
    return {"type": "message", "id": "u%s" % second, "timestamp": iso(second), "message": body}


def custom_record(second, text, custom_type="skill-prompt", **entry) -> dict:
    return {"type": "custom_message", "customType": custom_type, "content": text, "display": True, "id": "c%s" % second,
            "timestamp": iso(second), **entry}


def usage_of(input_tokens=1, output_tokens=1, cache_read=0, cache_write=0, cost=0.0, one_hour=None, **extra) -> dict:
    usage = {"input": input_tokens, "output": output_tokens, "cacheRead": cache_read, "cacheWrite": cache_write,
             "totalTokens": input_tokens + output_tokens + cache_read + cache_write, **extra}
    if cost is not None:
        usage["cost"] = {"total": cost}
    if one_hour is not None:
        usage["cttl"] = {"ephemeral1h": one_hour}
    return usage


def assistant_record(second, text="", stop="stop", response_id=None, usage=None, done=0, **message) -> dict:
    """An assistant message started at `second`; `done` seconds later (None: never) it completes and is written."""
    body = {"role": "assistant", "content": [{"type": "text", "text": text}] if text else [], "provider": "anthropic",
            "model": "claude-opus-5-5", "stopReason": stop, "timestamp": ms(second), **message}
    if usage is not None:
        body["usage"] = usage
    if response_id is not None:
        body["responseId"] = response_id
    if done is not None:
        body["completedAt"] = ms(second + done)
    return {"type": "message", "id": "a%s" % second, "timestamp": iso(second + (done or 0)), "message": body}


def helper_record(second, usage, model="claude-haiku-5", provider="anthropic") -> dict:
    return {"type": "model_usage", "id": "h%s" % second, "timestamp": iso(second), "provider": provider, "model": model, "usage": usage}


def jsonl(*records) -> str:
    return "".join(json.dumps(record) + "\n" for record in records)


def read_text(path) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def parse(*records) -> Transcript:
    """The transcript of `records` written as the lines of a session file."""
    return parse_transcript(jsonl(*records).splitlines(keepends=True))
