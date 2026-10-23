"""C5 file format of a Cursor hook record, shared by the hook that writes it and the reader that scans it: the names of the folders and
files, the keys of the JSON object in a file, and the one parse of that object."""
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Union

from .json_records import Record, decode_record
from .temp_files import STALE_TEMP_TAIL

RECORDS_FOLDER = ".records"
INVALID_FOLDER = ".invalid"
RECORD_SUFFIX = ".json"
RECORD_EVENTS = ("beforeSubmitPrompt", "afterAgentResponse", "stop", "subagentStop")
_RECORD_NAME = "%d-%s-%d" + RECORD_SUFFIX
# Longer arrivals are no record (and `int` refuses more than 4300 digits); a time a datetime cannot hold is still a record.
MAX_ARRIVAL_DIGITS = 30
# A record file's name: arrival in nanoseconds, event, process id. A temp (a dot, then the name and a tail) never matches it.
RECORD_FILE_NAME = re.compile(r"([0-9]{1,%d})-(%s)-[0-9]+%s" % (MAX_ARRIVAL_DIGITS, "|".join(RECORD_EVENTS), re.escape(RECORD_SUFFIX)))
# The temp of a record file (see `temp_files.temp_name`): a dot, the record's name, then the tail.
STALE_RECORD_TEMP = re.compile(r"\.[0-9]+-(?:%s)-[0-9]+%s" % ("|".join(RECORD_EVENTS), re.escape(RECORD_SUFFIX)) + STALE_TEMP_TAIL)

# The keys of the JSON object of one record file.
EVENT_KEY = "event"
CONVERSATION_ID_KEY = "conversationId"
RECEIVED_AT_NS_KEY = "receivedAtNs"
PID_KEY = "pid"
RAW_INPUT_KEY = "rawInput"
# The key of Cursor's own hook input that names the conversation; the hook reads it, and only it, to file a record.
HOOK_CONVERSATION_ID_KEY = "conversation_id"

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_NANOSECONDS_PER_MICROSECOND = 1000


@dataclass(frozen=True)
class HookRecord:
    """One parsed record: its event, the time it arrived and the object Cursor sent the hook (`hook_input`)."""

    event: str
    received_at: datetime
    hook_input: Record


def record_file_name(received_at_ns: int, event: str, pid: int) -> str:
    """`<arrival_ns>-<event>-<pid>.json`."""
    return _RECORD_NAME % (received_at_ns, event, pid)


def record_fields(event: str, conversation_id: Optional[str], received_at_ns: int, pid: int, raw_input: str) -> Dict[str, Union[str, int]]:
    """The JSON object of one record file. A record filed under `.records/.invalid/` has no `conversationId`."""
    fields: Dict[str, Union[str, int]] = {EVENT_KEY: event, RECEIVED_AT_NS_KEY: received_at_ns, PID_KEY: pid, RAW_INPUT_KEY: raw_input}
    if conversation_id is not None:
        fields[CONVERSATION_ID_KEY] = conversation_id
    return fields


def moment_of_ns(nanoseconds: int) -> Optional[datetime]:
    """The UTC time `nanoseconds` after the epoch, to the microsecond; None for a time a datetime cannot hold."""
    try:
        return _EPOCH + timedelta(microseconds=nanoseconds // _NANOSECONDS_PER_MICROSECOND)
    except OverflowError:
        return None


def _whole_nanoseconds(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def parse_record(text: str) -> Optional[HookRecord]:
    """The record a file's text holds; None for anything else: text that is no JSON object, an event that is none of
    `RECORD_EVENTS`, an arrival that is no time, or a raw input that is no JSON object."""
    record = decode_record(text)
    if record is None or record.get(EVENT_KEY) not in RECORD_EVENTS:
        return None
    arrived_at_ns = _whole_nanoseconds(record.get(RECEIVED_AT_NS_KEY))
    received_at = None if arrived_at_ns is None else moment_of_ns(arrived_at_ns)
    raw_input = record.get(RAW_INPUT_KEY)
    hook_input = decode_record(raw_input) if isinstance(raw_input, str) else None
    if received_at is None or hook_input is None:
        return None
    return HookRecord(record[EVENT_KEY], received_at, hook_input)
