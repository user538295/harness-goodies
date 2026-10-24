"""Decoding of one JSON-line record, and the reading of the values a harness record or store row carries (times, counts, costs, the
model label); what every format parser of a harness store shares. A value that is not what it should be is None (or empty), never an
error. Pure: no file access."""
import json
import math
from datetime import datetime, timezone
from typing import Any, Dict, Optional

Record = Dict[str, Any]

MAX_WHOLE_COUNT = 2**63 - 1
_MILLISECONDS = 1000
_MODEL_SEPARATOR = "/"


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


def is_finite_number(value: Any) -> bool:
    """Whether `value` is a number that is not `inf` or `nan`: an int (of any size, never a bool) or a finite float."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return isinstance(value, int) or math.isfinite(value)


def parse_epoch_milliseconds(value: Any) -> Optional[datetime]:
    """The UTC time a positive number of epoch milliseconds names; None for anything else (zero, a negative number, a number of
    milliseconds written as text, a time UTC cannot hold)."""
    if not is_finite_number(value) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / _MILLISECONDS, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def parse_whole_count(value: Any) -> Optional[int]:
    """Whole tokens or rows in 0..int64 max; a fraction is cut off, anything else (also a negative number) is None."""
    if not is_finite_number(value) or value < 0:
        return None
    return min(int(value), MAX_WHOLE_COUNT)


def whole_count(value: Any) -> int:
    """`parse_whole_count`, with 0 for what is no count."""
    return parse_whole_count(value) or 0


def parse_cost(value: Any) -> Optional[float]:
    """A cost a harness recorded: a number from zero up (zero is free); None for anything else, so the price file prices the request."""
    if not is_finite_number(value) or value < 0:
        return None
    try:
        return float(value)
    except OverflowError:
        return None


def model_label(model: Any, provider: Any) -> str:
    """The model with its provider (`anthropic/claude-opus-5-5`) unless the model already names a path; empty without a model name."""
    if not isinstance(model, str) or not model:
        return ""
    if isinstance(provider, str) and provider and _MODEL_SEPARATOR not in model:
        return provider + _MODEL_SEPARATOR + model
    return model
