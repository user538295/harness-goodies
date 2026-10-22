"""Decoding of one JSON-line record and of the ISO-8601 time it carries; what every format parser of a harness store shares.
Pure: no file access."""
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

Record = Dict[str, Any]


def decode_record(line: str) -> Optional[Record]:
    """The JSON object of one line; None for anything else (torn, too deeply nested, not an object)."""
    try:
        record = json.loads(line)
    except (ValueError, RecursionError):
        return None
    return record if isinstance(record, dict) else None


def parse_moment(value: Any) -> Optional[datetime]:
    """The timezone-aware time an ISO-8601 text names; None for anything else, a naive time or one UTC cannot hold."""
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if moment.utcoffset() is None:
        return None
    try:
        moment.astimezone(timezone.utc)
    except OverflowError:
        return None
    return moment
