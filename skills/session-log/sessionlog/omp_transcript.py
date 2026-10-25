"""Pure parsing of an OMP session transcript (its JSON lines) into the session header, the prompts with their replies and requests
and the times of its activity; no file access. Unknown record types, lines that do not parse and records of a known type whose
parts are not what is expected are skipped without a report.

The header is the first `session` record, wherever it stands (a `title` record may precede it). Prompts are `message` records
whose `message.role` is `user` and whose `attribution` is not `agent` (a sub-agent's task text is such an agent message), and
`custom_message` records whose `attribution` is `user` or whose `customType` is `skill-prompt` (Q25), except the notices OMP writes
beside the user's own message (`image-attachment`, `ultrathink-notice`: they are user-attributed, not prompts); other `custom_message`
types are skipped: they open no turn, so the replies to them belong to the prompt before them. A prompt's time is `message.timestamp`
(epoch milliseconds; a number of milliseconds written as text is no time), else the record's ISO `timestamp`; a prompt with no usable
time is dropped from the prompts with its replies and the requests under it, as in the other readers (`Transcript.requests`, which
a sub-agent reports, still holds them).

Requests are the assistant `message` records that carry a `usage` object and the `model_usage` records (the helper requests OMP
makes beside the conversation); a request belongs to the prompt it falls under, and those before the first prompt to the first.
Streamed copies of one response (the same `responseId`, else `messageId`, `id` or `timestamp` of the message) count once, as the
last copy, which also decides the prompt the response falls under. Tokens are OMP's `input`, `output`, `cacheRead` and `cacheWrite`
(the camel-case and snake-case spellings are read too), the recorded cost is `usage.cost.total` (or a plain
`usage.cost`), and cache writes split into 1h and 5m by `usage.cttl.ephemeral1h`. Effort is the last non-empty
`thinking_level_change.thinkingLevel`, carried forward, unless the message names its own `thinkingLevel` or `effort`. A reasoning
count is not kept apart: the model has no field for it.

A prompt's reply is the last assistant message of its turn whose `stopReason` is `stop`: its text blocks joined by blank lines. A
turn works from its prompt to the last assistant message it completed, whatever its `stopReason` (`toolUse` when a steering prompt cut
the run, `aborted`, `error`): that message's `completedAt` (else the record's own `completedAt`, else the time of the record). A turn
without such a message has no reply time and works no time."""
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Tuple, Union

from .json_records import (Record, decode_record, model_label, parse_cost, parse_epoch_milliseconds, parse_moment,
                           parse_whole_count)
from .model import Prompt, RequestUsage
from .prompt_turns import Opening, group_turns

_SESSION = "session"
_SESSION_INIT = "session_init"
_THINKING_LEVEL_CHANGE = "thinking_level_change"
_MESSAGE = "message"
_CUSTOM_MESSAGE = "custom_message"
_MODEL_USAGE = "model_usage"
_USER, _ASSISTANT = "user", "assistant"
_AGENT_ATTRIBUTION, _USER_ATTRIBUTION = "agent", "user"
_SKILL_PROMPT = "skill-prompt"
_USER_NOTICES = ("image-attachment", "ultrathink-notice")
_STOP = "stop"
_TEXT_BLOCK = "text"
_PROMPT_TEXT_SEPARATOR = "\n"
_REPLY_PARAGRAPH_SEPARATOR = "\n\n"
_INPUT_NAMES = ("input", "inputTokens", "input_tokens")
_OUTPUT_NAMES = ("output", "outputTokens", "output_tokens")
_CACHE_READ_NAMES = ("cacheRead", "cacheReadTokens", "cache_read")
_CACHE_WRITE_NAMES = ("cacheWrite", "cacheWriteTokens", "cache_write")
_ONE_HOUR_CACHE_NAME = "ephemeral1h"
_RESPONSE_ID_NAMES = ("responseId", "messageId", "id")


@dataclass(frozen=True)
class Transcript:
    """The parse of one session file. `unparsable` is True when the file has text but no line that is a JSON object; a file
    with no line at all parses to an empty transcript. `header_id` is the `id` of the header, `parent_session` the path its
    `parentSession` names, `header_moment` its `timestamp`. `first_moment` and `last_moment` bound the activity of the session: the
    start of its first `message`, `model_usage` or `custom_message` record and the newest end of any of them (an assistant
    message ends when it completes). `prompts` are the session's own prompts and `requests` every request it made."""

    unparsable: bool = False
    header_id: Optional[str] = None
    parent_session: Optional[str] = None
    project_dir: Optional[str] = None
    header_moment: Optional[datetime] = None
    agent_type: Optional[str] = None
    prompts: Tuple[Prompt, ...] = ()
    requests: Tuple[RequestUsage, ...] = ()
    first_moment: Optional[datetime] = None
    last_moment: Optional[datetime] = None


class ParentLink(NamedTuple):
    """The `parentSession` of a session file's header (None when it names none). Never unparsable: a file without a header has none."""

    parent_session: Optional[str]
    unparsable: bool = False


class _Answer(NamedTuple):
    """What an assistant message or a helper request contributes to its turn. `key` is None for a helper request and for a message
    that names no response. `began` and `asked_at` stay unset: the user's wait on OMP's `ask` tool counts as work, as in the old script."""

    index: int
    key: Optional[str]
    request: Optional[RequestUsage]
    is_stop: bool
    text: Optional[str]
    moment: Optional[datetime]
    began: Optional[datetime] = None
    asked_at: Optional[datetime] = None


def _text(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _is_number(value: Any) -> bool:
    """Whether JSON gave a number: an int or a float, never a bool."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _moment(value: Any) -> Optional[datetime]:
    """The time a number of epoch milliseconds or an ISO-8601 text names; None for anything else (a zero or negative number as well)."""
    return parse_moment(value) if isinstance(value, str) else parse_epoch_milliseconds(value)


def _entry_time(record: Record, message: Optional[Record] = None) -> Optional[datetime]:
    """When the record happened: the message's own time, else the record's."""
    return (_moment(message.get("timestamp")) if message else None) or _moment(record.get("timestamp"))


def _completed_time(record: Record, message: Record) -> Optional[datetime]:
    return _moment(message.get("completedAt")) or _moment(record.get("completedAt")) or _entry_time(record, message)


def _tokens(usage: Record, names: Tuple[str, ...]) -> int:
    """The first of `names` that holds a valid count; 0 when none does."""
    return next((count for count in (parse_whole_count(usage.get(name)) for name in names) if count is not None), 0)


def _recorded_cost(usage: Record) -> Optional[float]:
    """The cost OMP recorded for the request: `cost.total`, or `cost` itself when it is a number; None when it is absent or no
    number from zero up, so the price file prices the request instead. A recorded zero is a free request."""
    cost = usage.get("cost")
    return parse_cost(cost.get("total") if isinstance(cost, dict) else cost)


def _cache_writes(usage: Record) -> Tuple[int, int]:
    """The 5m and 1h cache-write tokens: OMP's `cttl.ephemeral1h` of them were written for an hour, the rest for five minutes."""
    written = _tokens(usage, _CACHE_WRITE_NAMES)
    time_to_live = usage.get("cttl")
    one_hour = min(_tokens(time_to_live, (_ONE_HOUR_CACHE_NAME,)), written) if isinstance(time_to_live, dict) else 0
    return written - one_hour, one_hour


def _request_of(owner: Record, effort: Optional[str]) -> Optional[RequestUsage]:
    """The request an assistant message or a `model_usage` record makes: `owner` holds its `usage`, `model` and `provider`."""
    usage = owner.get("usage")
    if not isinstance(usage, dict):
        return None
    written_5m, written_1h = _cache_writes(usage)
    return RequestUsage(
        model_name=model_label(owner.get("model"), owner.get("provider")),
        input_tokens=_tokens(usage, _INPUT_NAMES),
        output_tokens=_tokens(usage, _OUTPUT_NAMES),
        cache_read_tokens=_tokens(usage, _CACHE_READ_NAMES),
        cache_write_5m_tokens=written_5m,
        cache_write_1h_tokens=written_1h,
        effort=effort,
        recorded_cost_usd=_recorded_cost(usage),
    )


def _is_usable_id(value: Any) -> bool:
    return _text(value) is not None or _is_number(value)


def _response_key(message: Record) -> Optional[str]:
    """What tells streamed copies of one response apart from other responses: its id, else its timestamp; None without either."""
    for name in _RESPONSE_ID_NAMES:
        if _is_usable_id(message.get(name)):
            return "id:%s" % (message[name],)
    if _is_usable_id(message.get("timestamp")):
        return "timestamp:%s" % (message["timestamp"],)
    return None


def _block_text(block: Any) -> Optional[str]:
    """The text of a content block: a plain string, or the `text` of a `text` block; None for anything else."""
    if isinstance(block, dict) and block.get("type") == _TEXT_BLOCK:
        block = block.get("text")
    return block if isinstance(block, str) else None


def _texts_of(content: Any) -> List[str]:
    if isinstance(content, str):
        blocks = [content]
    elif isinstance(content, list):
        blocks = content
    else:
        blocks = []
    return [text for text in map(_block_text, blocks) if text is not None]


def _prompt_text(content: Any) -> str:
    """A prompt's text: the content of a text message, else its text blocks that are not empty, joined by newlines."""
    return _PROMPT_TEXT_SEPARATOR.join(text for text in _texts_of(content) if text)


def _reply_text(content: Any) -> Optional[str]:
    """The text blocks of an assistant message that are not blank, joined by blank lines; None when there is none."""
    return _REPLY_PARAGRAPH_SEPARATOR.join(text for text in _texts_of(content) if text.strip()) or None


def _is_user_custom_message(record: Record) -> bool:
    custom_type = record.get("customType")
    if custom_type in _USER_NOTICES:
        return False
    return record.get("attribution") == _USER_ATTRIBUTION or custom_type == _SKILL_PROMPT


class _Collector:
    """Gathers, in file order, the openings and answers of a transcript, the header and the bounds of its activity."""

    def __init__(self) -> None:
        self._header: Optional[Record] = None
        self._agent_type: Optional[str] = None
        self._first_moment: Optional[datetime] = None
        self._last_moment: Optional[datetime] = None
        self._effort: Optional[str] = None
        self._count = 0
        self._events: List[Union[Opening, _Answer]] = []
        self._latest_copy: Dict[str, int] = {}

    def add(self, record: Record) -> None:
        index = self._count
        self._count += 1
        kind = record.get("type")
        if kind == _SESSION:
            self._header = self._header or record
        elif kind == _SESSION_INIT:
            self._agent_type = self._agent_type or _text(record.get("agent"))
        elif kind == _THINKING_LEVEL_CHANGE:
            self._effort = _text(record.get("thinkingLevel")) or self._effort
        elif kind == _MESSAGE:
            message = record.get("message")
            if isinstance(message, dict):
                self._add_message(index, record, message)
        elif kind == _CUSTOM_MESSAGE:
            self._note_activity(_entry_time(record))
            if _is_user_custom_message(record):
                self._events.append(Opening(_entry_time(record), _prompt_text(record.get("content"))))
        elif kind == _MODEL_USAGE:
            self._note_activity(_entry_time(record))
            self._events.append(_Answer(index, None, _request_of(record, self._effort), False, None, None))

    def _note_activity(self, start: Optional[datetime], end: Optional[datetime] = None) -> None:
        """Records that something happened from `start` to `end` (a point in time when `end` is None)."""
        self._last_moment = max((moment for moment in (start, end, self._last_moment) if moment is not None), default=None)
        self._first_moment = self._first_moment or start

    def _add_message(self, index: int, record: Record, message: Record) -> None:
        role = message.get("role")
        start = _entry_time(record, message)
        if role == _USER:
            self._note_activity(start)
            if message.get("attribution") != _AGENT_ATTRIBUTION:
                self._events.append(Opening(start, _prompt_text(message.get("content"))))
        elif role == _ASSISTANT:
            completed = _completed_time(record, message)
            self._note_activity(start, completed)
            effort = _text(message.get("thinkingLevel")) or _text(message.get("effort")) or self._effort
            key = _response_key(message)
            if key is not None:
                self._latest_copy[key] = index
            is_stop = message.get("stopReason") == _STOP
            self._events.append(_Answer(index, key, _request_of(message, effort), is_stop,
                                        _reply_text(message.get("content")) if is_stop else None, completed))
        else:
            self._note_activity(start)

    def _is_replaced_copy(self, event: Union[Opening, _Answer]) -> bool:
        """Whether the event is a streamed copy of a response that a later copy replaced."""
        return isinstance(event, _Answer) and event.key is not None and self._latest_copy[event.key] != event.index

    def _kept_events(self) -> List[Union[Opening, _Answer]]:
        """The events without the streamed copies that a later copy of the same response replaced."""
        return [event for event in self._events if not self._is_replaced_copy(event)]

    def _prompts_and_requests(self) -> Tuple[Tuple[Prompt, ...], Tuple[RequestUsage, ...]]:
        """The prompts, each with the answers that fall under its opening, and every request in file order."""
        return group_turns(self._kept_events())

    def transcript(self) -> Transcript:
        prompts, requests = self._prompts_and_requests()
        header = self._header or {}
        return Transcript(
            header_id=_text(header.get("id")),
            parent_session=_text(header.get("parentSession")),
            project_dir=_text(header.get("cwd")),
            header_moment=_moment(header.get("timestamp")),
            agent_type=self._agent_type,
            prompts=prompts,
            requests=requests,
            first_moment=self._first_moment,
            last_moment=self._last_moment,
        )


def parse_parent_link(lines: Iterable[str]) -> ParentLink:
    """The `parentSession` of the first `session` record, read without going past it; none when the lines have no such record."""
    for line in lines:
        record = decode_record(line) if line.strip() else None
        if record is not None and record.get("type") == _SESSION:
            return ParentLink(_text(record.get("parentSession")))
    return ParentLink(None)


def parse_transcript(lines: Iterable[str]) -> Transcript:
    """The header, prompts, requests and activity bounds of the lines of one session file. A file with text but without any JSON
    object line is unparsable; one without any line is an empty transcript."""
    collector = _Collector()
    has_text = has_records = False
    for line in lines:
        if not line.strip():
            continue
        has_text = True
        record = decode_record(line)
        if record is not None:
            has_records = True
            collector.add(record)
    if has_text and not has_records:
        return Transcript(unparsable=True)
    return collector.transcript()
