"""Pure parsing of a Codex rollout (its JSON lines) into the session header and the prompts with their replies and requests; no
file access. Unknown record types and lines that do not parse are skipped without a report.

Prompts come only from `UserMessage` items (`event_msg` / `item_completed`), or, in a rollout without any, from the old
`user_message` events: `response_item` entries also carry injected context and are never read. Replies likewise come from
`AgentMessage` items, else from old `agent_message` events; a rollout that mixes both formats is read as the new one. A
prompt's reply is the last `AgentMessage` of its turn marked `final_answer`, else the last one. Requests are the
`token_usage_record` lines when the rollout has any (they also cover compaction calls); otherwise each `token_count` event
whose `total_token_usage` grew is one request with the growth as its tokens. `last_token_usage` is never summed, because
events repeat it, and a `token_count` before the rollout's first `task_started` (a forked thread's inherited total) only sets
the starting total. `input_tokens` is the whole prompt, which holds the cached and the cache-write tokens (`total_tokens` is input plus
output; codex `codex-api` `ResponseCompletedUsage`): input tokens exclude both, `cached_input_tokens` count as cache read and
`cache_write_input_tokens` as cache write, so no token is counted twice. The part of `total_tokens` that input and output do not
account for is counted as input tokens: a `token_count` of a rollout imported from another agent has `input_tokens` and `output_tokens` 0 and only `total_tokens`, whose split is unknown
(the same rule reads a `token_usage_record` usage); such a rollout names no model, so its tokens are never priced.

Limits of the growth path (a rollout without `token_usage_record` lines): a compaction call is in no running total, so its
tokens are missing. A fork (a thread with a `parent_thread_id` or `forked_from_id`) never lets its total fall below the highest
one seen, in any field, so the replayed history of its parent, whose totals lie under the inherited starting total, adds no
request. A fork also replays that history as turns, and two marks tell a replayed turn from the fork's own: its `turn_id` is
`rollout-<n>` (the wrapper the replay is written in) or its `started_at` lies before the second of the header. From such a
`task_started` to the next one that has neither mark, no `UserMessage`, `AgentMessage`, `token_usage_record` or request is the
fork's: the totals in it only raise the starting total. The exception is a replayed turn that holds a `token_usage_record` of the
fork's own thread (its `thread_id` is the fork's `id`; a replayed parent's record names the parent's): a guardian review forked from
its parent writes its first review turn inside that wrapper, so that turn's `UserMessage`, `AgentMessage` and record are the fork's.
A fork whose replay shows neither mark (no replayed turn, or turns
written like its own) still counts the replay as its own; a timestamp rule was checked against the local rollouts and misfires
on a file whose records all share one time.

A prompt is what the user typed. The IDE puts its context in front of it, as a markdown block that ends with a `## My request for
Codex:` line (codex `protocol.rs` `USER_MESSAGE_BEGIN`; the Codex desktop app writes `## My request:`, as its local rollouts show); a
message that opens with a heading (`#`) and holds such a line is read as the text after its first one, trimmed as Codex's
`strip_user_message_prefix` does. Any other message, also one that only mentions the marker, is read as written.

A prompt is dropped, with its replies and requests, when its record has no usable time (as in the Claude reader), and a
rollout without any prompt has no rows. Requests before the first prompt belong to it; replies before it belong to none."""
import math
import re
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import AbstractSet, Any, Dict, Iterable, List, NamedTuple, Optional, Set, Tuple

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
_REQUEST_MARKER = re.compile(r"^## My request(?: for Codex)?:", re.MULTILINE)
_CONTEXT_HEADING = "#"
_TEXT_BLOCK = "text"
_PROMPT_TEXT_SEPARATOR = "\n"
_REPLY_PARAGRAPH_SEPARATOR = "\n\n"
_INPUT = "input_tokens"
_CACHED = "cached_input_tokens"
_CACHE_WRITE = "cache_write_input_tokens"
_OUTPUT = "output_tokens"
_USAGE_FIELDS = (_INPUT, _CACHED, _CACHE_WRITE, _OUTPUT)
_TOTAL_TOKENS = "total_tokens"
_UNSPLIT = "unsplit_tokens"  # what `total_tokens` holds beyond input and output; counted as input, its split is unknown
_COUNTED = _USAGE_FIELDS + (_UNSPLIT,)
_REPLAY_TURN_ID = re.compile(r"rollout-[0-9]+")
_MAX_TOKENS = 2**63 - 1

_Tokens = Dict[str, int]


@dataclass(frozen=True)
class RolloutMeta:
    """What a rollout's first line says: its own thread id (`id`), the root session it belongs to (`session_id`; without it
    its `parent_thread_id`, else its own `id`), the thread that spawned it (`parent_thread_id`), its role as a sub-agent
    (`agent_type`), whether it is an internal helper of Codex (`helper`: a sub-agent source that is no `thread_spawn`, as a
    guardian review; a helper is never a session of the user's own), whether it is a fork of another thread (it has a
    `parent_thread_id` or a `forked_from_id`), when it started and the folder it ran in. `started_at` is None when no timestamp of
    the line is a usable time."""

    root_id: str
    thread_id: str
    parent_thread_id: Optional[str]
    agent_type: Optional[str]
    helper: bool
    forked: bool
    started_at: Optional[datetime]
    project_dir: Optional[str]


@dataclass(frozen=True)
class Rollout:
    """The parse of a whole rollout, in one of three states: usable (`meta` set), empty (no line at all: `meta` None and
    `unparsable` False) or unparsable (a first line that is no usable `session_meta`: `meta` None and `unparsable` True).
    `requests` are all its requests in file order, also those of a rollout without any prompt (a sub-agent usually has
    none); `last_moment` is the newest time of any record, None when no record has a usable one; `first_moment` is the oldest
    time of the records after the header line (the header's own time is `meta.started_at`), None when none of them has one."""

    meta: Optional[RolloutMeta]
    prompts: Tuple[Prompt, ...]
    unparsable: bool
    requests: Tuple[RequestUsage, ...] = ()
    last_moment: Optional[datetime] = None
    first_moment: Optional[datetime] = None


class _Opening(NamedTuple):
    index: int
    moment: Optional[datetime]
    text: str
    replay: Optional[int]  # the replayed turn it was written in, if any (see `_Collector._replay`)


class _Reply(NamedTuple):
    index: int
    moment: Optional[datetime]
    text: str
    final: bool
    replay: Optional[int]


class _IndexedRequest(NamedTuple):
    index: int
    request: RequestUsage


@dataclass
class _Track:
    """The openings and replies of one record format (new items or old events), in file order."""

    openings: List[_Opening] = field(default_factory=list)
    replies: List[_Reply] = field(default_factory=list)

    def outside(self, replays: AbstractSet[int]) -> "_Track":
        """The track without what was written in a replayed turn that is not one of `replays`, the fork's own."""
        return _Track([opening for opening in self.openings if opening.replay is None or opening.replay in replays],
                      [reply for reply in self.replies if reply.replay is None or reply.replay in replays])


def _text(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _is_tokens(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= _MAX_TOKENS


def _tokens(value: Any) -> int:
    """Whole tokens in 0..int64 max; anything else is 0."""
    return value if _is_tokens(value) else 0


def _unsplit(total_tokens: Any, fields: _Tokens) -> int:
    """What the usable `total_tokens` holds beyond the `fields` input and output; none when it holds no more or is no number."""
    return max(0, total_tokens - fields[_INPUT] - fields[_OUTPUT]) if _is_tokens(total_tokens) else 0


def _token_fields(usage: Dict[str, Any]) -> _Tokens:
    fields = {name: _tokens(usage.get(name)) for name in _USAGE_FIELDS}
    fields[_UNSPLIT] = _unsplit(usage.get(_TOTAL_TOKENS), fields)
    return fields


def _object_or_empty(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _typed_text(message: str) -> str:
    """What the user typed in a message: the text after the first request marker line when the message opens with a context
    heading, else the message as written."""
    if not message.lstrip().startswith(_CONTEXT_HEADING):
        return message
    marker = _REQUEST_MARKER.search(message)
    return message[marker.end():].strip() if marker else message


def _texts_of(content: Any) -> List[str]:
    blocks = content if isinstance(content, list) else []
    texts = [block.get("text") for block in blocks
             if isinstance(block, dict) and isinstance(block.get("type"), str) and block["type"].lower() == _TEXT_BLOCK]
    return [text for text in texts if isinstance(text, str) and text.strip()]


def _agent_type(payload: Record) -> Optional[str]:
    """The role of a sub-agent thread: the payload's `agent_role`, else the role of its spawn source, else the kind of
    thread the source names (`guardian`)."""
    subagent = _object_or_empty(_object_or_empty(payload.get("source")).get("subagent"))
    spawn = _object_or_empty(subagent.get("thread_spawn"))
    return _text(payload.get("agent_role")) or _text(spawn.get("agent_role")) or _text(subagent.get("other"))


def _is_helper(payload: Record) -> bool:
    """Whether the header's source is a sub-agent of Codex's own that no agent spawned: a guardian review (`{"other": ...}`), or a
    compaction or review (a bare kind). A `thread_spawn` is the thread of an agent that did spawn, and it names its parent."""
    subagent = _object_or_empty(payload.get("source")).get("subagent")
    return bool(subagent) and not (isinstance(subagent, dict) and "thread_spawn" in subagent)


def _parent_of(payload: Record) -> Optional[str]:
    """The thread that spawned this one: the header's `parent_thread_id`, else the parent its spawn source names (an older Codex
    writes only that, with the thread's own id as `session_id`), else, for a sub-agent, the thread it was forked from (a guardian
    review is forked from the thread it reviews; where a header has both, they name the same thread). A fork that is no
    sub-agent has no parent: it is a session of its own."""
    subagent = _object_or_empty(_object_or_empty(payload.get("source")).get("subagent"))
    spawn = _object_or_empty(subagent.get("thread_spawn"))
    parent = _text(payload.get("parent_thread_id")) or _text(spawn.get("parent_thread_id"))
    return parent or (_text(payload.get("forked_from_id")) if subagent else None)


def _meta_of(record: Optional[Record]) -> Optional[RolloutMeta]:
    if record is None or record.get("type") != _SESSION_META or not isinstance(record.get("payload"), dict):
        return None
    payload = record["payload"]
    thread_id = _text(payload.get("id"))
    if thread_id is None:
        return None
    parent_thread_id = _parent_of(payload)
    session_id = _text(payload.get("session_id"))
    root_id = session_id if session_id not in (None, thread_id) else parent_thread_id or thread_id
    started_at = parse_moment(payload.get("timestamp")) or parse_moment(record.get("timestamp"))
    return RolloutMeta(root_id=root_id, thread_id=thread_id, parent_thread_id=parent_thread_id, agent_type=_agent_type(payload),
                       helper=_is_helper(payload),
                       forked=_text(payload.get("parent_thread_id")) is not None or _text(payload.get("forked_from_id")) is not None,
                       started_at=started_at, project_dir=_text(payload.get("cwd")))


def parse_session_meta(line: str) -> Optional[RolloutMeta]:
    """The header a rollout's first line carries, or None when the line is no `session_meta` record with the thread's own id."""
    return _meta_of(decode_record(line))


class _Collector:
    """Gathers, in file order, every opening, reply and request of a rollout, the newest record time and the oldest one after
    the header line; `prompts` then lays the openings out by position. `forked` rollouts never let a total fall below the
    highest one seen (see `_take_count`), and what a forked rollout replays from its parent is no opening, reply or request
    (see `_is_replay`), except in a replayed turn that holds a record of the fork's own thread (see `_take_recorded`). The first
    record added is the header."""

    def __init__(self, thread_id: str, forked: bool, started_at: Optional[datetime]) -> None:
        self._thread_id = thread_id
        self._forked = forked
        self._header_second = math.floor(started_at.timestamp()) if started_at is not None else None
        self._replay: Optional[int] = None  # the number of the replayed turn being read, None outside one
        self._replays = 0
        self._own_replays: Set[int] = set()  # the replayed turns that hold a record of the fork's own thread
        self._model = ""
        self._effort: Optional[str] = None
        self._task_started = False
        self._total: _Tokens = dict.fromkeys(_COUNTED, 0)
        self._items = _Track()
        self._events = _Track()
        self._recorded: List[_IndexedRequest] = []
        self._grown: List[_IndexedRequest] = []
        self.last_moment: Optional[datetime] = None
        self.first_moment: Optional[datetime] = None
        self._past_header = False

    def add(self, index: int, record: Record) -> None:
        moment = parse_moment(record.get("timestamp"))
        if moment is not None:
            if self.last_moment is None or moment > self.last_moment:
                self.last_moment = moment
            if self._past_header and (self.first_moment is None or moment < self.first_moment):
                self.first_moment = moment
        self._past_header = True
        payload = _object_or_empty(record.get("payload"))
        kind = record.get("type")
        if kind == _TURN_CONTEXT:
            self._take_context(payload)
        elif kind == _TOKEN_USAGE_RECORD:
            self._take_recorded(index, payload)
        elif kind == _EVENT:
            self._take_event(index, moment, payload)

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
            input_tokens=max(0, tokens[_INPUT] - cached - tokens[_CACHE_WRITE]) + tokens[_UNSPLIT],
            output_tokens=tokens[_OUTPUT],
            cache_read_tokens=cached,
            cache_write_5m_tokens=tokens[_CACHE_WRITE],
            cache_write_1h_tokens=0,
            effort=self._effort,
        )

    def _take_recorded(self, index: int, payload: Record) -> None:
        """A record in a replayed turn is the parent's, unless it names the fork's own thread: then the turn is the fork's own
        (a guardian fork writes its first review turn in the wrapper that replays its parent's history)."""
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return
        if self._replay is not None:
            if payload.get("thread_id") != self._thread_id:
                return
            self._own_replays.add(self._replay)
        self._recorded.append(_IndexedRequest(index, self._request_of(_token_fields(usage))))

    def _is_replay(self, payload: Record) -> bool:
        """Whether a `task_started` opens a turn that a forked rollout replays from its parent: the wrapper turn `rollout-<n>`,
        or one that started before the second of the header."""
        turn_id = payload.get("turn_id")
        started = payload.get("started_at")
        if isinstance(turn_id, str) and _REPLAY_TURN_ID.fullmatch(turn_id):
            return True
        return (self._header_second is not None and isinstance(started, int) and not isinstance(started, bool)
                and started < self._header_second)

    def _take_event(self, index: int, moment: Optional[datetime], payload: Record) -> None:
        event = payload.get("type")
        if event == _TASK_STARTED:
            self._task_started = True
            if self._forked and self._is_replay(payload):
                self._replays += 1
                self._replay = self._replays
            else:
                self._replay = None
        elif event == _TOKEN_COUNT:
            self._take_count(index, _object_or_empty(payload.get("info")))
        elif event == _ITEM_COMPLETED:
            self._take_item(index, moment, _object_or_empty(payload.get("item")))
        elif event == _USER_MESSAGE_EVENT and isinstance(payload.get("message"), str):
            self._events.openings.append(_Opening(index, moment, _typed_text(payload["message"]), self._replay))
        elif event == _AGENT_MESSAGE_EVENT:
            self._take_reply(self._events, index, moment, payload.get("message"), False)

    def _take_reply(self, track: _Track, index: int, moment: Optional[datetime], text: Any, final: bool) -> None:
        if isinstance(text, str) and text.strip():
            track.replies.append(_Reply(index, moment, text, final, self._replay))

    def _take_item(self, index: int, moment: Optional[datetime], item: Record) -> None:
        kind = item.get("type")
        if kind == _USER_MESSAGE_ITEM:
            text = _PROMPT_TEXT_SEPARATOR.join(_texts_of(item.get("content")))
            self._items.openings.append(_Opening(index, moment, _typed_text(text), self._replay))
        elif kind == _AGENT_MESSAGE_ITEM:
            text = _REPLY_PARAGRAPH_SEPARATOR.join(_texts_of(item.get("content")))
            self._take_reply(self._items, index, moment, text, item.get("phase") == _FINAL_ANSWER_PHASE)

    def _take_count(self, index: int, info: Record) -> None:
        """A request is the growth of the running total since the previous event. The total before the first `task_started`
        is inherited from a parent thread: it is the starting total, not a request, and neither is the total of a replayed
        turn. A total that shrinks is the new baseline and never a negative request, except in a fork: there no field of the
        total falls below the highest value seen, because totals under the inherited one are the replayed history of its
        parent, not its own work. A field of the total that is not a usable number keeps its baseline, so it is neither a
        request nor counted twice later; an event without any usable field changes nothing. `total_tokens` is not a field of
        its own: what it holds beyond input and output is the `_UNSPLIT` field."""
        total = info.get("total_token_usage")
        if not isinstance(total, dict) or not any(_is_tokens(total.get(name)) for name in _USAGE_FIELDS + (_TOTAL_TOKENS,)):
            return
        current = {name: total[name] if _is_tokens(total.get(name)) else self._total[name] for name in _USAGE_FIELDS}
        current[_UNSPLIT] = _unsplit(total.get(_TOTAL_TOKENS), current) if _is_tokens(total.get(_TOTAL_TOKENS)) else self._total[_UNSPLIT]
        if self._forked:
            current = {name: max(current[name], self._total[name]) for name in _COUNTED}
        growth = {name: max(0, current[name] - self._total[name]) for name in _COUNTED}
        self._total = current
        if self._task_started and self._replay is None and any(growth.values()):
            self._grown.append(_IndexedRequest(index, self._request_of(growth)))

    def prompts(self) -> Tuple[Prompt, ...]:
        """Each opening with the replies and requests up to the next opening."""
        items, events = self._items.outside(self._own_replays), self._events.outside(self._own_replays)
        track = items if items.openings else events
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

    def requests(self) -> Tuple[RequestUsage, ...]:
        """Every request of the rollout in file order, whether or not a prompt carries it."""
        return tuple(used.request for used in (self._recorded or self._grown))


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
    collector: Optional[_Collector] = None
    meta: Optional[RolloutMeta] = None
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        record = decode_record(line)
        if collector is None:
            meta = _meta_of(record)
            if meta is None:
                return Rollout(meta=None, prompts=(), unparsable=True)
            collector = _Collector(meta.thread_id, meta.forked, meta.started_at)
        if record is not None:
            collector.add(index, record)
    if collector is None:
        return Rollout(meta=None, prompts=(), unparsable=False)
    return Rollout(meta=meta, prompts=collector.prompts(), unparsable=False, requests=collector.requests(),
                   last_moment=collector.last_moment, first_moment=collector.first_moment)
