"""Pure parsing of a Claude Code main-session transcript (already split into JSON records) into prompts; no file access."""
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple, Union

from .model import Prompt, RequestUsage

Record = Dict[str, Any]

_USER = "user"
_ASSISTANT = "assistant"
_PROMPT_SOURCES = frozenset({"typed", "queued", "sdk", "suggestion_accepted"})
_TEXT_BLOCK = "text"
_REPLY_PARAGRAPH_SEPARATOR = "\n\n"
_PROMPT_TEXT_SEPARATOR = "\n"
_FAST_SPEED = "fast"
_MAX_TOKENS = 2**63 - 1
_NUMERIC_STRING = re.compile(r"\d+(\.\d+)?([eE][+-]?\d+)?", re.ASCII)

_MessageKey = Union[str, int]


@dataclass(frozen=True)
class Transcript:
    """`started_at` and `project_dir` are taken from records of any type."""

    started_at: Optional[datetime]
    project_dir: Optional[str]
    prompts: Tuple[Prompt, ...]


def _token_count(value: Any) -> int:
    """Whole tokens in 0..int64 max from an int, float or plain ASCII decimal string; anything else is 0."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, str):
        match = _NUMERIC_STRING.fullmatch(value)
        if match is None:
            return 0
        try:
            value = float(value) if match.lastindex else int(value)
        except ValueError:
            return 0
    if isinstance(value, float):
        value = math.floor(value) if math.isfinite(value) else -1
    return value if isinstance(value, int) and 0 <= value <= _MAX_TOKENS else 0


def _moment(record: Record) -> Optional[datetime]:
    try:
        moment = datetime.fromisoformat(record.get("timestamp"))
    except (TypeError, ValueError):
        return None
    if moment.utcoffset() is None:
        return None
    try:
        moment.astimezone(timezone.utc)
    except OverflowError:
        return None
    return moment


def _cwd(record: Record) -> Optional[str]:
    cwd = record.get("cwd")
    return cwd if isinstance(cwd, str) and cwd else None


def _message_of(record: Record) -> Record:
    message = record.get("message")
    return message if isinstance(message, dict) else {}


def _text_blocks(record: Record) -> Tuple[str, ...]:
    content = _message_of(record).get("content")
    if isinstance(content, str):
        texts = [content]
    else:
        blocks = content if isinstance(content, list) else []
        texts = [b.get("text") for b in blocks if isinstance(b, dict) and b.get("type") == _TEXT_BLOCK]
    return tuple(text for text in texts if isinstance(text, str) and text.strip())


def _opens_prompt(record: Record) -> bool:
    source = record.get("promptSource")
    return (record.get("type") == _USER and record.get("isMeta") is not True
            and isinstance(source, str) and source in _PROMPT_SOURCES)


def _request_of(record: Record) -> Optional[RequestUsage]:
    message = _message_of(record)
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    cache = usage.get("cache_creation")
    cache = cache if isinstance(cache, dict) else {}
    model, effort = message.get("model"), record.get("effort")
    return RequestUsage(
        model_name=model if isinstance(model, str) else "",
        input_tokens=_token_count(usage.get("input_tokens")),
        output_tokens=_token_count(usage.get("output_tokens")),
        cache_read_tokens=_token_count(usage.get("cache_read_input_tokens")),
        cache_write_5m_tokens=_token_count(cache.get("ephemeral_5m_input_tokens")),
        cache_write_1h_tokens=_token_count(cache.get("ephemeral_1h_input_tokens")),
        effort=effort if isinstance(effort, str) else None,
        fast=usage.get("speed") == _FAST_SPEED,
    )


def _message_key(record: Record, line_number: int) -> _MessageKey:
    for candidate in (_message_of(record).get("id"), record.get("requestId")):
        if isinstance(candidate, str):
            return candidate
    return line_number


@dataclass(frozen=True)
class _Reply:
    """The light data one assistant record contributes to its turn."""

    message_key: _MessageKey
    request: Optional[RequestUsage]
    texts: Tuple[str, ...]
    moment: Optional[datetime]


def _earliest(current: Optional[datetime], moment: Optional[datetime]) -> Optional[datetime]:
    return min((m for m in (current, moment) if m is not None), default=None)


def _latest(current: Optional[datetime], moment: Optional[datetime]) -> Optional[datetime]:
    return max((m for m in (current, moment) if m is not None), default=None)


class _Turn:
    """A prompt and the assistant entries up to the next prompt. One request per message id, the last copy wins.
    A prompt without a valid timestamp still collects its entries but is dropped when finished."""

    def __init__(self, submitted_at: Optional[datetime], text: str) -> None:
        self._submitted_at = submitted_at
        self._text = text
        self._requests: Dict[_MessageKey, RequestUsage] = {}
        self._texts: Dict[_MessageKey, List[str]] = {}
        self._latest_reply: Optional[datetime] = None
        self._last_key: Optional[_MessageKey] = None

    def add(self, reply: _Reply) -> None:
        if reply.request is not None:
            self._requests[reply.message_key] = reply.request
        collected = self._texts.setdefault(reply.message_key, [])
        for text in reply.texts:
            if text not in collected:
                collected.append(text)
        self._latest_reply = _latest(self._latest_reply, reply.moment)
        self._last_key = reply.message_key

    def finish(self) -> Optional[Prompt]:
        if self._submitted_at is None:
            return None
        reply_at = max(self._latest_reply, self._submitted_at) if self._latest_reply else None
        return Prompt(
            submitted_at=self._submitted_at,
            text=self._text,
            reply_at=reply_at,
            reply_text=_REPLY_PARAGRAPH_SEPARATOR.join(self._texts.get(self._last_key, ())) or None,
            working_time=reply_at - self._submitted_at if reply_at else timedelta(0),
            requests=tuple(self._requests.values()),
        )


def json_objects(lines: Iterable[str]) -> Iterator[Tuple[int, Record]]:
    """Numbered JSON-object lines; every other line (torn, blank, not an object, too deeply nested) is skipped."""
    for line_number, line in enumerate(lines):
        try:
            record = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(record, dict):
            yield line_number, record


def parse_main_session(records: Iterable[Tuple[int, Record]]) -> Transcript:
    """The numbered records of one main session folded into prompts; assistant records before the first prompt are ignored."""
    started_at, project_dir = None, None
    turns: List[_Turn] = []
    for line_number, record in records:
        moment = _moment(record)
        started_at = _earliest(started_at, moment)
        project_dir = project_dir or _cwd(record)
        if _opens_prompt(record):
            turns.append(_Turn(moment, _PROMPT_TEXT_SEPARATOR.join(_text_blocks(record))))
        elif turns and record.get("type") == _ASSISTANT:
            turns[-1].add(_Reply(_message_key(record, line_number), _request_of(record), _text_blocks(record), moment))
    finished = (turn.finish() for turn in turns)
    return Transcript(started_at, project_dir, tuple(prompt for prompt in finished if prompt))
