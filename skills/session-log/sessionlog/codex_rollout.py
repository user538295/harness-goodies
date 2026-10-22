"""Pure parsing of a Codex rollout (its JSON lines) into the session header and the prompts with their replies and requests; no
file access. Unknown record types and lines that do not parse are skipped without a report.

Prompts come only from `UserMessage` items (`event_msg` / `item_completed`), or, in a rollout without any, from the old
`user_message` events: `response_item` entries also carry injected context and are never read. Replies likewise come from
`AgentMessage` items, else from old `agent_message` events; a rollout that mixes both formats is read as the new one. A
prompt's reply is the last `AgentMessage` of its turn marked `final_answer`, else the last one. Requests are the
`token_usage_record` lines when the rollout has any (they also cover compaction calls); otherwise each `token_count` event
whose `total_token_usage` grew is one request with the growth as its tokens. `last_token_usage` is never summed, because
events repeat it, and a `token_count` before the rollout's first `task_started` (a forked thread's inherited total) only sets
the starting total. Input tokens exclude `cached_input_tokens`, which count as cache read; `cache_write_input_tokens` count as
cache write.

Limits of the growth path (a rollout without `token_usage_record` lines): a compaction call is in no running total, so its
tokens are missing, and a fork whose replayed history of its parent holds `task_started` before the replayed
`token_count`s counts that replayed growth as its own (the starting-total rule covers only the `token_count` that comes before
the first `task_started`); records of the fork carry no mark that tells them apart (a timestamp rule was checked against the
local rollouts and misfires on a file whose records all share one time).

A prompt is dropped, with its replies and requests, when its record has no usable time (as in the Claude reader), and a
rollout without any prompt has no rows. Requests before the first prompt belong to it; replies before it belong to none."""
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Tuple

from .json_records import Record, decode_record, parse_moment
from .model import Prompt, RequestUsage

_SESSION_META = "session_meta"
_TURN_CONTEXT = "turn_context"
_TOKEN_USAGE_RECORD = "token_usage_record"
_EVENT = "event_msg"
_TASK_STARTED = "task_started"
_TOKEN_COUNT = "token_count"
_ITEM_COMPLETED = "item_completed"
_USER_MESSAGE_ITEM = "UserMessage"
_AGENT_MESSAGE_ITEM = "AgentMessage"
_USER_MESSAGE_EVENT = "user_message"
_AGENT_MESSAGE_EVENT = "agent_message"
_FINAL_ANSWER_PHASE = "final_answer"
_TEXT_BLOCK = "text"
_PROMPT_TEXT_SEPARATOR = "\n"
_REPLY_PARAGRAPH_SEPARATOR = "\n\n"
_INPUT = "input_tokens"
_CACHED = "cached_input_tokens"
_CACHE_WRITE = "cache_write_input_tokens"
_OUTPUT = "output_tokens"
_USAGE_FIELDS = (_INPUT, _CACHED, _CACHE_WRITE, _OUTPUT)
_MAX_TOKENS = 2**63 - 1

_Tokens = Dict[str, int]


@dataclass(frozen=True)
class RolloutMeta:
    """What a rollout's first line says: its own thread id (`id`), the root session it belongs to (`session_id`; without it
    its `parent_thread_id`, else its own `id`), when it started and the folder it ran in. `started_at` is None when no
    timestamp of the line is a usable time."""

    root_id: str
    thread_id: str
    started_at: Optional[datetime]
    project_dir: Optional[str]


@dataclass(frozen=True)
class Rollout:
    """The parse of a whole rollout, in one of three states: usable (`meta` set), empty (no line at all: `meta` None and
    `unparsable` False) or unparsable (a first line that is no usable `session_meta`: `meta` None and `unparsable` True)."""

    meta: Optional[RolloutMeta]
    prompts: Tuple[Prompt, ...]
    unparsable: bool


class _Opening(NamedTuple):
    index: int
    moment: Optional[datetime]
    text: str


class _Reply(NamedTuple):
    index: int
    moment: Optional[datetime]
    text: str
    final: bool


class _IndexedRequest(NamedTuple):
    index: int
    request: RequestUsage


@dataclass
class _Track:
    """The openings and replies of one record format (new items or old events), in file order."""

    openings: List[_Opening] = field(default_factory=list)
    replies: List[_Reply] = field(default_factory=list)


def _text(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _is_tokens(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= _MAX_TOKENS


def _tokens(value: Any) -> int:
    """Whole tokens in 0..int64 max; anything else is 0."""
    return value if _is_tokens(value) else 0


def _token_fields(usage: Dict[str, Any]) -> _Tokens:
    return {name: _tokens(usage.get(name)) for name in _USAGE_FIELDS}


def _object_or_empty(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _texts_of(content: Any) -> List[str]:
    blocks = content if isinstance(content, list) else []
    texts = [block.get("text") for block in blocks
             if isinstance(block, dict) and isinstance(block.get("type"), str) and block["type"].lower() == _TEXT_BLOCK]
    return [text for text in texts if isinstance(text, str) and text.strip()]


def parse_session_meta(line: str) -> Optional[RolloutMeta]:
    """The header a rollout's first line carries, or None when the line is no `session_meta` record with the thread's own id."""
    record = decode_record(line)
    if record is None or record.get("type") != _SESSION_META or not isinstance(record.get("payload"), dict):
        return None
    payload = record["payload"]
    thread_id = _text(payload.get("id"))
    if thread_id is None:
        return None
    root_id = _text(payload.get("session_id")) or _text(payload.get("parent_thread_id")) or thread_id
    started_at = parse_moment(payload.get("timestamp")) or parse_moment(record.get("timestamp"))
    return RolloutMeta(root_id=root_id, thread_id=thread_id, started_at=started_at, project_dir=_text(payload.get("cwd")))


class _Collector:
    """Gathers, in file order, every opening, reply and request of a rollout; `prompts` then lays them out by position."""

    def __init__(self) -> None:
        self._model = ""
        self._effort: Optional[str] = None
        self._task_started = False
        self._total: _Tokens = dict.fromkeys(_USAGE_FIELDS, 0)
        self._items = _Track()
        self._events = _Track()
        self._recorded: List[_IndexedRequest] = []
        self._grown: List[_IndexedRequest] = []

    def add(self, index: int, record: Record) -> None:
        payload = _object_or_empty(record.get("payload"))
        kind = record.get("type")
        if kind == _TURN_CONTEXT:
            self._take_context(payload)
        elif kind == _TOKEN_USAGE_RECORD:
            self._take_recorded(index, payload.get("usage"))
        elif kind == _EVENT:
            self._take_event(index, record, payload)

    def _take_context(self, payload: Record) -> None:
        """A turn context names the model of the requests after it and its effort; one without a model changes nothing."""
        model = _text(payload.get("model"))
        if model is None:
            return
        settings = _object_or_empty(_object_or_empty(payload.get("collaboration_mode")).get("settings"))
        self._model = model
        self._effort = _text(payload.get("effort")) or _text(settings.get("reasoning_effort"))

    def _request_of(self, tokens: _Tokens) -> RequestUsage:
        cached = tokens[_CACHED]
        return RequestUsage(
            model_name=self._model,
            input_tokens=max(0, tokens[_INPUT] - cached),
            output_tokens=tokens[_OUTPUT],
            cache_read_tokens=cached,
            cache_write_5m_tokens=tokens[_CACHE_WRITE],
            cache_write_1h_tokens=0,
            effort=self._effort,
        )

    def _take_recorded(self, index: int, usage: Any) -> None:
        if isinstance(usage, dict):
            self._recorded.append(_IndexedRequest(index, self._request_of(_token_fields(usage))))

    def _take_event(self, index: int, record: Record, payload: Record) -> None:
        event = payload.get("type")
        if event == _TASK_STARTED:
            self._task_started = True
        elif event == _TOKEN_COUNT:
            self._take_count(index, _object_or_empty(payload.get("info")))
        elif event == _ITEM_COMPLETED:
            self._take_item(index, parse_moment(record.get("timestamp")), _object_or_empty(payload.get("item")))
        elif event == _USER_MESSAGE_EVENT and isinstance(payload.get("message"), str):
            self._events.openings.append(_Opening(index, parse_moment(record.get("timestamp")), payload["message"]))
        elif event == _AGENT_MESSAGE_EVENT:
            self._take_reply(self._events, index, parse_moment(record.get("timestamp")), payload.get("message"), False)

    @staticmethod
    def _take_reply(track: _Track, index: int, moment: Optional[datetime], text: Any, final: bool) -> None:
        if isinstance(text, str) and text.strip():
            track.replies.append(_Reply(index, moment, text, final))

    def _take_item(self, index: int, moment: Optional[datetime], item: Record) -> None:
        kind = item.get("type")
        if kind == _USER_MESSAGE_ITEM:
            self._items.openings.append(_Opening(index, moment, _PROMPT_TEXT_SEPARATOR.join(_texts_of(item.get("content")))))
        elif kind == _AGENT_MESSAGE_ITEM:
            text = _REPLY_PARAGRAPH_SEPARATOR.join(_texts_of(item.get("content")))
            self._take_reply(self._items, index, moment, text, item.get("phase") == _FINAL_ANSWER_PHASE)

    def _take_count(self, index: int, info: Record) -> None:
        """A request is the growth of the running total since the previous event. The total before the first `task_started`
        is inherited from a parent thread: it is the starting total, not a request. A total that shrinks is the new
        baseline and never a negative request. A field of the total that is not a usable number keeps its baseline, so
        it is neither a request nor counted twice later; an event without any usable field changes nothing."""
        total = info.get("total_token_usage")
        if not isinstance(total, dict) or not any(_is_tokens(total.get(name)) for name in _USAGE_FIELDS):
            return
        current = {name: total[name] if _is_tokens(total.get(name)) else self._total[name] for name in _USAGE_FIELDS}
        growth = {name: max(0, current[name] - self._total[name]) for name in _USAGE_FIELDS}
        self._total = current
        if self._task_started and any(growth.values()):
            self._grown.append(_IndexedRequest(index, self._request_of(growth)))

    def prompts(self) -> Tuple[Prompt, ...]:
        """Each opening with the replies and requests up to the next opening."""
        track = self._items if self._items.openings else self._events
        if not track.openings:
            return ()
        starts = [opening.index for opening in track.openings]
        replies: List[List[_Reply]] = [[] for _ in track.openings]
        requests: List[List[RequestUsage]] = [[] for _ in track.openings]
        for reply in track.replies:
            position = bisect_right(starts, reply.index) - 1
            if position >= 0:
                replies[position].append(reply)
        for used in self._recorded or self._grown:
            requests[max(0, bisect_right(starts, used.index) - 1)].append(used.request)
        prompts = (_prompt(opening, turn_replies, turn_requests)
                   for opening, turn_replies, turn_requests in zip(track.openings, replies, requests))
        return tuple(prompt for prompt in prompts if prompt is not None)


def _prompt(opening: _Opening, replies: List[_Reply], requests: List[RequestUsage]) -> Optional[Prompt]:
    if opening.moment is None:
        return None
    reply_moments = [reply.moment for reply in replies if reply.moment is not None]
    reply_at = max(max(reply_moments), opening.moment) if reply_moments else None
    final_replies = [reply for reply in replies if reply.final]
    chosen = (final_replies or replies)[-1] if replies else None
    return Prompt(
        submitted_at=opening.moment,
        text=opening.text,
        reply_at=reply_at,
        reply_text=chosen.text if chosen else None,
        working_time=reply_at - opening.moment if reply_at is not None else timedelta(0),
        requests=tuple(requests),
    )


def parse_rollout(lines: Iterable[str]) -> Rollout:
    """The header of the first non-blank line and the prompts of the rest. A first line that is no usable `session_meta`
    ends the parse: the rollout is then unparsable."""
    collector = _Collector()
    meta: Optional[RolloutMeta] = None
    seen_first_line = False
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        if not seen_first_line:
            seen_first_line = True
            meta = parse_session_meta(line)
            if meta is None:
                return Rollout(meta=None, prompts=(), unparsable=True)
            continue
        record = decode_record(line)
        if record is not None:
            collector.add(index, record)
    return Rollout(meta=meta, prompts=collector.prompts(), unparsable=False)
