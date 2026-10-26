"""Pure parsing of the `session_message` rows of one OpenCode session (OpenCode v2.0.30) into its prompts with their replies and requests
and the end of its activity; no database access. The rows are `(type, data text)` pairs in `seq` order. A row whose `data` is no JSON
object is skipped without a report, and so is an assistant row without a `tokens` object as a request (it still ends a turn and carries
a reply). Rows of a type this reader does not use (`system`, `synthetic`, `shell`, `compaction`, `idle`, `model-switched`) open no
prompt and make no request, but their times still count as activity.

A `user` row opens a prompt: its text is `data.text` trimmed and its time `data.time.created` (epoch milliseconds); a row with no text
opens none (its answers fall under the prompt before it); a prompt with no usable time is dropped with its replies, and the requests
under it fall under no prompt (`Transcript.unprompted_requests`, which `opencode_family` shows as a row of their own so they reach the
TOTAL; `Transcript.requests`, which a sub-agent reports, holds every request). An `assistant` row is a request when `data.tokens` is an
object: `input`, `output`, `cache.read` and `cache.write` are its tokens, `reasoning` counts as output (as OpenCode's own total does) and is
also kept in `reasoning_tokens`,
the recorded cost is `data.cost` (0 means free; an absent or invalid cost is left to the price file), the model is `data.model`'s
`providerID/id`. OpenCode records no effort. A request falls under the prompt before it, and those before the first prompt under the first.

A prompt's reply is the last assistant row of its turn (up to the next `user` row) whose `finish` is `stop`: its text blocks joined by
blank lines, its time `data.time.completed` (else `data.time.created`). A turn works from its prompt to the `data.time.completed` of
its last assistant row, whatever its `finish` (`error`, `length`, an unfinished row), but not while the user was asked: a row with a
`question`, `confirm` or `ask` tool block asked at its `data.time.streamed` (else `created`), and the user's wait lasts until the
next row starts (as the old script counted it). A turn without a completed row has no reply time and works no time.

A row's `data.time.completed` counts only when it is plausible (`_end_of`). OpenCode's migration from v1 to v2 stamped one `completed`
(the instant of the migration) on every row it carried over, months after the row ran, while the content blocks of those rows keep the
times the work really had. A `completed` more than `MAX_COMPLETION_LAG` after the newest time of the row itself (its `created` and
`streamed` and the `created` and `completed` of its content blocks) is no end: the row ends at its newest block time, else it counts as
never completed (a stopping row is dated by its start, any other has no end). So neither a turn's working time nor the last moment is
stretched across the months. In a real database the rows with a genuine `completed` end at most 59 minutes after the newest time of
the row, the migrated ones at least 94 days after it."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable, List, Optional, Tuple, Union

from .json_records import (MAX_WHOLE_COUNT, Record, decode_record, model_label, parse_cost, parse_epoch_milliseconds,
                           whole_count)
from .model import Prompt, RequestUsage
from .prompt_turns import Answer, Opening, group_turns

# How long after the newest other time of a row its `completed` may lie. Far above the longest genuine lag of a real database (59
# minutes, a request that hung) and far below the shortest migrated one (94 days).
MAX_COMPLETION_LAG = timedelta(hours=6)

_USER, _ASSISTANT = "user", "assistant"
_STOP = "stop"
_TOOL_BLOCK = "tool"
_WAIT_TOOLS = ("question", "confirm", "ask")
_TEXT_BLOCK = "text"
_REPLY_PARAGRAPH_SEPARATOR = "\n\n"


@dataclass(frozen=True)
class Transcript:
    """The parse of one session's rows: its own `prompts`, every request it made (what a sub-agent reports), the `unprompted_requests`
    among them that fall under no prompt, and `last_moment`, the newest start or plausible completion time of any row; None without
    one."""

    prompts: Tuple[Prompt, ...] = ()
    requests: Tuple[RequestUsage, ...] = ()
    unprompted_requests: Tuple[RequestUsage, ...] = ()
    last_moment: Optional[datetime] = None

    def working_time_since(self, started_at: datetime) -> timedelta:
        """From `started_at` to the newest row; none without a row time, and never negative."""
        if self.last_moment is None:
            return timedelta(0)
        return max(self.last_moment - started_at, timedelta(0))


_Event = Union[Opening, Answer]


def _time_of(data: Record, field: str) -> Optional[datetime]:
    times = data.get("time")
    return parse_epoch_milliseconds(times.get(field)) if isinstance(times, dict) else None


def _block_times_of(data: Record) -> List[datetime]:
    """The `created` and `completed` times of the content blocks of a row."""
    content = data.get("content")
    blocks = content if isinstance(content, list) else []
    return [moment for block in blocks if isinstance(block, dict) for moment in (_time_of(block, "created"), _time_of(block, "completed"))
            if moment is not None]


def _end_of(data: Record) -> Optional[datetime]:
    """When the row's work ended: its `completed` when that is plausible (see the module text), else the newest time of its content
    blocks; None when the row never completed or has no such time."""
    completed = _time_of(data, "completed")
    if completed is None:
        return None
    block_times = _block_times_of(data)
    own_times = block_times + [moment for moment in (_time_of(data, "created"), _time_of(data, "streamed")) if moment is not None]
    if not own_times or completed - max(own_times) <= MAX_COMPLETION_LAG:
        return completed
    return max(block_times, default=None)


def _model_of(data: Record) -> str:
    model = data.get("model")
    return model_label(model.get("id"), model.get("providerID")) if isinstance(model, dict) else ""


def _request_of(data: Record) -> Optional[RequestUsage]:
    tokens = data.get("tokens")
    if not isinstance(tokens, dict):
        return None
    cache = tokens.get("cache")
    cache = cache if isinstance(cache, dict) else {}
    return RequestUsage(
        model_name=_model_of(data),
        input_tokens=whole_count(tokens.get("input")),
        output_tokens=min(whole_count(tokens.get("output")) + whole_count(tokens.get("reasoning")), MAX_WHOLE_COUNT),
        cache_read_tokens=whole_count(cache.get("read")),
        cache_write_5m_tokens=whole_count(cache.get("write")),
        cache_write_1h_tokens=0,
        recorded_cost_usd=parse_cost(data.get("cost")),
        reasoning_tokens=whole_count(tokens.get("reasoning")),
    )


def _reply_text(content: Any) -> Optional[str]:
    """The text blocks of an assistant row that are not blank, joined by blank lines; None when there is none."""
    blocks = content if isinstance(content, list) else []
    texts = (block.get("text") for block in blocks if isinstance(block, dict) and block.get("type") == _TEXT_BLOCK)
    return _REPLY_PARAGRAPH_SEPARATOR.join(text for text in texts if isinstance(text, str) and text.strip()) or None


def _asked_at(data: Record) -> Optional[datetime]:
    """When the row put a question to the user (its `question`, `confirm` or `ask` tool call was streamed), else None."""
    content = data.get("content")
    blocks = content if isinstance(content, list) else []
    if not any(isinstance(block, dict) and block.get("type") == _TOOL_BLOCK and block.get("name") in _WAIT_TOOLS for block in blocks):
        return None
    return _time_of(data, "streamed") or _time_of(data, "created")


def _answer_of(data: Record) -> Answer:
    """A row ends its work at its plausible completion; a stopping row that never completed is dated by its start, and a row that did
    not stop and never completed (still running) has no end."""
    is_stop = data.get("finish") == _STOP
    started_at = _time_of(data, "created")
    ended_at = _end_of(data) or (started_at if is_stop else None)
    return Answer(_request_of(data), is_stop, _reply_text(data.get("content")) if is_stop else None, ended_at, started_at, _asked_at(data))


def _events_of(rows: Iterable[Tuple[str, str]]) -> Tuple[List[_Event], Optional[datetime]]:
    """The openings and answers of the rows in order, and the newest start or plausible completion time of any decodable row."""
    events: List[_Event] = []
    last_moment: Optional[datetime] = None
    for kind, data_text in rows:
        data = decode_record(data_text)
        if data is None:
            continue
        started_at = _time_of(data, "created")
        row_moments = (started_at, _end_of(data), last_moment)
        last_moment = max((moment for moment in row_moments if moment is not None), default=None)
        if kind == _USER:
            body = data.get("text")
            text = body.strip() if isinstance(body, str) else ""
            if text:
                events.append(Opening(started_at, text))
        elif kind == _ASSISTANT:
            events.append(_answer_of(data))
    return events, last_moment


def parse_messages(rows: Iterable[Tuple[str, str]]) -> Transcript:
    """The prompts, requests and last moment of the `(type, data text)` rows of one session, in `seq` order."""
    events, last_moment = _events_of(rows)
    prompts, requests, unprompted = group_turns(events)
    return Transcript(prompts=prompts, requests=requests, unprompted_requests=unprompted, last_moment=last_moment)
