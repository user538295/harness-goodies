"""The session one Cursor conversation makes of its hook records (C5), joined without any file access.

Prompts and stops join by `generation_id`; usage comes from `stop` alone, because `afterAgentResponse` repeats the same totals, and the
reply text from `afterAgentResponse`. Sub-agents are one flat row per `child_conversation_id`, starting at the arrival of their
`subagentStop` minus its `duration_ms`; Cursor records no sub-agent tokens. A repeated event of one generation, or of one child, counts
once and the last record wins; for the reply that is the last response with text, so a later empty one never erases it. A prompt
without a `stop` was cancelled: it shows no reply and no tokens. A `stop` without a prompt is shown under `NO_PROMPT_TEXT`, so its tokens reach the TOTAL. A response without a `stop` or a prompt is no turn and is left out.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

from .agent_tree import UNKNOWN_TYPE
from .cursor_records import HookRecord
from .json_records import model_label, parse_whole_count
from .model import Harness, Prompt, RequestUsage, Session, SubAgent

NO_PROMPT_TEXT = "(prompt not recorded)"

_PROMPT_EVENT = "beforeSubmitPrompt"
_RESPONSE_EVENT = "afterAgentResponse"
_STOP_EVENT = "stop"
SUBAGENT_EVENT = "subagentStop"
_GENERATION_ID = "generation_id"
_CHILD_ID = "child_conversation_id"
_PROMPT_TEXT = "prompt"
_REPLY_TEXT = "text"
_MODEL = "model"
_MODEL_PARAMS = "model_params"
_PARAM_ID = "id"
_PARAM_VALUE = "value"
_EFFORT_PARAM = "effort"
_FAST_PARAM = "fast"
_INPUT_TOKENS = "input_tokens"
_OUTPUT_TOKENS = "output_tokens"
_CACHE_READ_TOKENS = "cache_read_tokens"
_CACHE_WRITE_TOKENS = "cache_write_tokens"
_TOKEN_KEYS = (_INPUT_TOKENS, _OUTPUT_TOKENS, _CACHE_READ_TOKENS, _CACHE_WRITE_TOKENS)
_SUBAGENT_TYPE = "subagent_type"
_DURATION_MS = "duration_ms"
_WORKSPACE_ROOTS = "workspace_roots"
_TRANSCRIPT_PATH = "transcript_path"
_AGENT_TRANSCRIPT_PATH = "agent_transcript_path"
_FAST_VALUES = {"true": True, "false": False}

_EPOCH = datetime.fromtimestamp(0, timezone.utc)


def _text(hook_input: Dict[str, Any], key: str) -> Optional[str]:
    value = hook_input.get(key)
    return value if isinstance(value, str) and value else None


def _reply_text(hook_input: Dict[str, Any]) -> Optional[str]:
    """The reply a response carries; None when it has no text or only blank text."""
    value = hook_input.get(_REPLY_TEXT)
    return value if isinstance(value, str) and value.strip() else None


def _last_by_id(records: Sequence[HookRecord], event: str, id_key: str,
                counts: Callable[[HookRecord], bool] = lambda record: True) -> Dict[str, HookRecord]:
    """The last record of `event` that `counts`, for each id its input names under `id_key`; a record with no such id is left out. In
    order of first appearance."""
    found: Dict[str, HookRecord] = {}
    for record in records:
        record_id = _text(record.hook_input, id_key) if record.event == event and counts(record) else None
        if record_id is not None:
            found[record_id] = record
    return found


def _model_param(hook_input: Dict[str, Any], name: str) -> Optional[str]:
    params = hook_input.get(_MODEL_PARAMS)
    for param in params if isinstance(params, list) else ():
        if isinstance(param, dict) and param.get(_PARAM_ID) == name and isinstance(param.get(_PARAM_VALUE), str):
            return param[_PARAM_VALUE]
    return None


def _requests_of(stop: Dict[str, Any]) -> Tuple[RequestUsage, ...]:
    """The one request of a `stop`; none when it carries no token count at all. Cursor's `input_tokens` include the cache tokens."""
    counts = {key: parse_whole_count(stop.get(key)) for key in _TOKEN_KEYS}
    if all(count is None for count in counts.values()):
        return ()
    cache_read, cache_write = counts[_CACHE_READ_TOKENS] or 0, counts[_CACHE_WRITE_TOKENS] or 0
    return (RequestUsage(model_name=model_label(stop.get(_MODEL), None), input_tokens=max(0, (counts[_INPUT_TOKENS] or 0) - cache_read - cache_write),
                         output_tokens=counts[_OUTPUT_TOKENS] or 0, cache_read_tokens=cache_read, cache_write_5m_tokens=cache_write,
                         cache_write_1h_tokens=0, effort=_model_param(stop, _EFFORT_PARAM),
                         fast=_FAST_VALUES.get(_model_param(stop, _FAST_PARAM))),)


def _answered(submitted_at: datetime, text: str, stop: Optional[HookRecord], response: Optional[HookRecord]) -> Prompt:
    """The turn submitted at `submitted_at`. Its reply comes with the stop and never ends before its prompt."""
    if stop is None:
        return Prompt(submitted_at=submitted_at, text=text, working_time=timedelta(0))
    working_time = max(timedelta(0), stop.received_at - submitted_at)
    return Prompt(submitted_at=submitted_at, text=text, reply_at=submitted_at + working_time,
                  reply_text=None if response is None else _reply_text(response.hook_input), working_time=working_time,
                  requests=_requests_of(stop.hook_input))


def _prompts_of(records: Sequence[HookRecord]) -> Tuple[Prompt, ...]:
    prompts = _last_by_id(records, _PROMPT_EVENT, _GENERATION_ID)
    responses = _last_by_id(records, _RESPONSE_EVENT, _GENERATION_ID, lambda record: _reply_text(record.hook_input) is not None)
    stops = _last_by_id(records, _STOP_EVENT, _GENERATION_ID)
    turns = [(generation, _answered(prompt.received_at, _text(prompt.hook_input, _PROMPT_TEXT) or "", stops.get(generation),
                                    responses.get(generation))) for generation, prompt in prompts.items()]
    turns += [(generation, _answered(stop.received_at, NO_PROMPT_TEXT, stop, responses.get(generation)))
              for generation, stop in stops.items() if generation not in prompts]
    return tuple(prompt for _, prompt in sorted(turns, key=lambda turn: (turn[1].submitted_at, turn[0])))


def _duration_of(record: HookRecord) -> Optional[timedelta]:
    """The `duration_ms` of a record; None when it is no count, no timedelta can hold it, or it reaches back before the epoch."""
    milliseconds = parse_whole_count(record.hook_input.get(_DURATION_MS))
    if milliseconds is None:
        return None
    try:
        duration = timedelta(milliseconds=milliseconds)
    except OverflowError:
        return None
    return duration if duration <= record.received_at - _EPOCH else None


def child_of(record: HookRecord) -> Optional[str]:
    """The conversation a `subagentStop` record names as its sub-agent; None for any other record, or one that names none."""
    return _text(record.hook_input, _CHILD_ID) if record.event == SUBAGENT_EVENT else None


def _sub_agent_of(child_id: str, record: HookRecord) -> SubAgent:
    """A sub-agent row. A duration that is not recorded (see `_duration_of`) leaves the working time open and the start at the arrival."""
    duration = _duration_of(record)
    started_at = record.received_at if duration is None else record.received_at - duration
    return SubAgent(agent_id=child_id, type=_text(record.hook_input, _SUBAGENT_TYPE) or UNKNOWN_TYPE, started_at=started_at,
                    working_time=duration, tokens_not_recorded=True, transcript_path=_text(record.hook_input, _AGENT_TRANSCRIPT_PATH))


def _transcript_path_of(records: Sequence[HookRecord]) -> Optional[str]:
    """The newest `transcript_path` any hook input names: Cursor puts it on every hook, and null when transcripts are off."""
    return next((path for path in (_text(record.hook_input, _TRANSCRIPT_PATH) for record in reversed(records)) if path), None)


def _project_dir_of(records: Sequence[HookRecord]) -> Optional[str]:
    """`workspace_roots[0]` of the first record that has one."""
    for record in records:
        roots = record.hook_input.get(_WORKSPACE_ROOTS)
        if isinstance(roots, list) and roots and isinstance(roots[0], str) and roots[0]:
            return roots[0]
    return None


def build_session(conversation_id: str, records: Sequence[HookRecord]) -> Optional[Session]:
    """The session of the conversation `conversation_id` from its parsed `records`, in the order they arrived; None without a record.
    `conversation_id` must be a safe id."""
    if not records:
        return None
    sub_agents = tuple(_sub_agent_of(child_id, record) for child_id, record in _last_by_id(records, SUBAGENT_EVENT, _CHILD_ID).items())
    return Session(harness=Harness.CURSOR, session_id=conversation_id, started_at=records[0].received_at,
                   project_dir=_project_dir_of(records), prompts=_prompts_of(records), sub_agents=sub_agents,
                   transcript_path=_transcript_path_of(records))
