"""Pure parsing of the `session_message` rows of one OpenCode session (OpenCode v2.0.30) into its prompts with their replies and requests
and the end of its activity; no database access. The rows are `(type, data text)` pairs in `seq` order. A row whose `data` is no JSON
object is skipped without a report, and so is an assistant row without a `tokens` object as a request (it still ends a turn and carries
a reply). Rows of a type this reader does not use (`system`, `synthetic`, `shell`, `compaction`, `idle`, `model-switched`) open no
prompt and make no request, but their times still count as activity.

A `user` row opens a prompt: its text is `data.text` and its time `data.time.created` (epoch milliseconds); a prompt with no usable time
is dropped with its replies and the requests under it, as in the other readers (`Transcript.requests`, which a sub-agent
reports, still holds them). An `assistant` row is a request when `data.tokens` is an
object: `input`, `output`, `cache.read` and `cache.write` are its tokens, `reasoning` counts as output (the model has no field for it),
the recorded cost is `data.cost` (0 means free; an absent or invalid cost is left to the price file), the model is `data.model`'s
`providerID/id`. OpenCode records no effort. A request falls under the prompt before it, and those before the first prompt under the first.

A prompt's reply is the last assistant row of its turn (up to the next `user` row) whose `finish` is `stop`: its text blocks joined by
blank lines, its time `data.time.completed` (else `data.time.created`). A turn without such a row has no reply and works no time."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable, List, Optional, Tuple, Union

from .json_records import (MAX_WHOLE_COUNT, Record, decode_record, model_label, parse_cost, parse_epoch_milliseconds,
                           whole_count)
from .model import Prompt, RequestUsage
from .prompt_turns import Answer, Opening, group_turns

_USER, _ASSISTANT = "user", "assistant"
_STOP = "stop"
_TEXT_BLOCK = "text"
_REPLY_PARAGRAPH_SEPARATOR = "\n\n"


@dataclass(frozen=True)
class Transcript:
    """The parse of one session's rows: its own `prompts`, every request it made (what a sub-agent reports) and `last_moment`, the
    newest start or completion time of any row; None without one."""

    prompts: Tuple[Prompt, ...] = ()
    requests: Tuple[RequestUsage, ...] = ()
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
    )


def _reply_text(content: Any) -> Optional[str]:
    """The text blocks of an assistant row that are not blank, joined by blank lines; None when there is none."""
    blocks = content if isinstance(content, list) else []
    texts = (block.get("text") for block in blocks if isinstance(block, dict) and block.get("type") == _TEXT_BLOCK)
    return _REPLY_PARAGRAPH_SEPARATOR.join(text for text in texts if isinstance(text, str) and text.strip()) or None


def _answer_of(data: Record) -> Answer:
    is_stop = data.get("finish") == _STOP
    return Answer(_request_of(data), is_stop, _reply_text(data.get("content")) if is_stop else None,
                   _time_of(data, "completed") or _time_of(data, "created"))


def _events_of(rows: Iterable[Tuple[str, str]]) -> Tuple[List[_Event], Optional[datetime]]:
    """The openings and answers of the rows in order, and the newest start or completion time of any decodable row."""
    events: List[_Event] = []
    last_moment: Optional[datetime] = None
    for kind, data_text in rows:
        data = decode_record(data_text)
        if data is None:
            continue
        started_at = _time_of(data, "created")
        row_moments = (started_at, _time_of(data, "completed"), last_moment)
        last_moment = max((moment for moment in row_moments if moment is not None), default=None)
        if kind == _USER:
            body = data.get("text")
            events.append(Opening(started_at, body if isinstance(body, str) else ""))
        elif kind == _ASSISTANT:
            events.append(_answer_of(data))
    return events, last_moment


def parse_messages(rows: Iterable[Tuple[str, str]]) -> Transcript:
    """The prompts, requests and last moment of the `(type, data text)` rows of one session, in `seq` order."""
    events, last_moment = _events_of(rows)
    prompts, requests = group_turns(events)
    return Transcript(prompts=prompts, requests=requests, last_moment=last_moment)
