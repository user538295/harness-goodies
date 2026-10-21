"""Entities: the normalized session model every reader produces and the sync use case and writer consume (C2).

Depends on nothing but the standard library. Collections are tuples (lists are normalized on construction); times are timezone-aware datetimes, naive ones
are rejected. Entities are constructed with keyword arguments: field order cannot follow the contract (.tsp)
because dataclasses need required fields before optional ones.
"""
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Optional, Tuple

MAX_ID_LENGTH = 128
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,%d}" % MAX_ID_LENGTH)
_WINDOWS_DEVICE_NAME = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", re.IGNORECASE)


class Harness(Enum):
    """The five supported harnesses; passed explicitly, never inferred."""

    CLAUDE = "claude"
    CODEX = "codex"
    CURSOR = "cursor"
    OMP = "omp"
    OPENCODE = "opencode"


def is_safe_id(value: str) -> bool:
    """Id rule for every id that becomes part of a path (session id, Cursor conversation id).

    1-128 of [A-Za-z0-9_-] (no dots, no separators) and not a Windows device name. Sub-agent ids never
    become paths, so they are not checked.
    """
    return isinstance(value, str) and _SAFE_ID.fullmatch(value) is not None and _WINDOWS_DEVICE_NAME.fullmatch(value) is None


def _require_aware(moment: object, field: str) -> None:
    if not isinstance(moment, datetime) or moment.utcoffset() is None:
        raise ValueError("%s must be a timezone-aware datetime" % field)


def _require_aware_if_present(moment: object, field: str) -> None:
    if moment is not None:
        _require_aware(moment, field)


def _as_tuple(entity: object, field: str, key: Optional[Callable[[Any], Any]] = None) -> None:
    """Replace a collection field of a frozen entity with a tuple, sorted by `key` when given."""
    values = getattr(entity, field)
    object.__setattr__(entity, field, tuple(sorted(values, key=key) if key else values))


def _by_start_then_id(agent: "SubAgent") -> Tuple[datetime, str]:
    return (agent.started_at, agent.agent_id)


def _activity_of(sub_agents: Tuple["SubAgent", ...]) -> Tuple[datetime, ...]:
    return tuple(agent.last_activity_at for agent in sub_agents)


@dataclass(frozen=True)
class RequestUsage:
    """One request's tokens; `input_tokens` excludes cache reads. Absent `recorded_cost_usd` = compute from prices;
    `recorded_cost_usd` 0 means free."""

    model_name: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_5m_tokens: int
    cache_write_1h_tokens: int
    effort: Optional[str] = None
    fast: Optional[bool] = None
    recorded_cost_usd: Optional[float] = None


@dataclass(frozen=True)
class Prompt:
    """One user prompt and its reply. `working_time` runs from submission to the last reply, so `reply_at` covers it.
    `working_time` is 0 when there is no reply."""

    submitted_at: datetime
    text: str
    reply_at: Optional[datetime] = None
    reply_text: Optional[str] = None
    working_time: Optional[timedelta] = None
    requests: Tuple[RequestUsage, ...] = ()

    def __post_init__(self) -> None:
        _require_aware(self.submitted_at, "Prompt.submitted_at")
        _require_aware_if_present(self.reply_at, "Prompt.reply_at")
        _as_tuple(self, "requests")


@dataclass(frozen=True)
class SubAgent:
    """Belongs to the session, not to a prompt. `sub_agents` are its children; they are kept ordered by start, then id."""

    agent_id: str
    type: str
    started_at: datetime
    working_time: Optional[timedelta] = None
    requests: Tuple[RequestUsage, ...] = ()
    tokens_not_recorded: bool = False
    sub_agents: Tuple["SubAgent", ...] = ()

    def __post_init__(self) -> None:
        _require_aware(self.started_at, "SubAgent.started_at")
        _as_tuple(self, "requests")
        _as_tuple(self, "sub_agents", _by_start_then_id)

    @property
    def last_activity_at(self) -> datetime:
        """Its start plus working time, or the newest activity of its own sub-agents when later."""
        own_end = self.started_at + (self.working_time or timedelta(0))
        return max((own_end,) + _activity_of(self.sub_agents))


@dataclass(frozen=True)
class Session:
    """`session_id` must satisfy `is_safe_id`: a reader lists an unsafe id as an unreadable source and builds no Session."""

    harness: Harness
    session_id: str
    started_at: datetime
    project_dir: Optional[str] = None
    orphan_of_parent_id: Optional[str] = None
    prompts: Tuple[Prompt, ...] = ()
    sub_agents: Tuple[SubAgent, ...] = ()

    def __post_init__(self) -> None:
        if not is_safe_id(self.session_id):
            raise ValueError("session_id %r is not a safe id (see is_safe_id)" % (self.session_id,))
        _require_aware(self.started_at, "Session.started_at")
        _as_tuple(self, "prompts")
        _as_tuple(self, "sub_agents", _by_start_then_id)

    @property
    def last_activity_at(self) -> datetime:
        """Newest prompt submission or reply time or sub-agent activity; the session start only when it has none."""
        prompt_times = tuple(
            moment
            for prompt in self.prompts
            for moment in (prompt.submitted_at, prompt.reply_at)
            if moment is not None
        )
        return max(prompt_times + _activity_of(self.sub_agents), default=self.started_at)
