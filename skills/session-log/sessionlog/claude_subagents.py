"""Pure rules for Claude Code sub-agents: the `agent-<id>.meta.json` text and the nesting of one session's agents; no file access."""
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import AbstractSet, Any, Dict, List, Optional, Sequence, Tuple

from .agent_tree import UNKNOWN_TYPE, build_tree, depths_of, parent_indexes
from .claude_transcript import TRANSCRIPT_SUFFIX, MessageRequest
from .model import RequestUsage, SubAgent

AGENT_FILE_PREFIX = "agent-"
META_FILE_SUFFIX = ".meta.json"


@dataclass(frozen=True)
class SubAgentMeta:
    """`parent_agent_id` is the agent id (file stem) of the parent, in the same form as `ReadSubAgent.agent_id`."""

    type: str = UNKNOWN_TYPE
    parent_agent_id: Optional[str] = None


@dataclass(frozen=True)
class ReadSubAgent:
    """One sub-agent file after reading; `agent_id` is its file stem. `started_at` is None when nothing gave it a time."""

    agent_id: str
    meta: SubAgentMeta
    started_at: Optional[datetime]
    working_time: timedelta
    requests: Tuple[MessageRequest, ...]


def is_agent_file_name(name: str) -> bool:
    """Whether a file name is that of a sub-agent transcript: `agent-<id>.jsonl`."""
    return name.startswith(AGENT_FILE_PREFIX) and name.endswith(TRANSCRIPT_SUFFIX)


def meta_path_of(agent_path: str) -> str:
    """The `.meta.json` beside a sub-agent transcript path."""
    return agent_path[: -len(TRANSCRIPT_SUFFIX)] + META_FILE_SUFFIX


def _meta_object(text: str) -> Dict[str, Any]:
    try:
        meta = json.loads(text)
    except (ValueError, RecursionError):
        return {}
    return meta if isinstance(meta, dict) else {}


def _agent_id_of_meta_parent(parent: Any) -> Optional[str]:
    """`parentAgentId` of a meta is the bare id; agents are named by file stem (`agent-<id>`), so the parent gets the prefix."""
    return AGENT_FILE_PREFIX + parent if isinstance(parent, str) and parent else None


def parse_meta(text: str) -> SubAgentMeta:
    """Type and parent are taken independently; whatever is missing or of the wrong type is the default."""
    meta = _meta_object(text)
    agent_type = meta.get("agentType")
    return SubAgentMeta(
        type=agent_type if isinstance(agent_type, str) and agent_type else UNKNOWN_TYPE,
        parent_agent_id=_agent_id_of_meta_parent(meta.get("parentAgentId")),
    )


def _requests_counted_once(agents: Sequence[ReadSubAgent], depths: Sequence[int], counted_ids: AbstractSet[str]) -> List[Tuple[RequestUsage, ...]]:
    """A forked agent replays messages of its parent, so one message id can sit in several files, and a fork starts with its
    parent's first record, so both can start at the same time. The id counts for the agent that started first, then the
    one nearer the top (the parent), then the smaller agent id, then file order; a later file drops its copy. Ids in
    `counted_ids` (the main session's) count for the main session."""
    owned = set(counted_ids)
    kept: List[Tuple[RequestUsage, ...]] = [() for _ in agents]
    for index in sorted(range(len(agents)), key=lambda index: (agents[index].started_at, depths[index], agents[index].agent_id)):
        requests = [request for request in agents[index].requests if request.message_id not in owned]
        owned.update(request.message_id for request in requests if request.message_id is not None)
        kept[index] = tuple(request.usage for request in requests)
    return kept


def nest_sub_agents(agents: Sequence[ReadSubAgent], counted_message_ids: AbstractSet[str] = frozenset()) -> Tuple[SubAgent, ...]:
    """The agents of one session as a tree: each under the agent its meta names. An agent with no time at all has no row
    (the reader gives one a time whenever it has requests). Two files with one agent id are both kept, and children of
    that id go to the first. A message counts once in the whole session (see `_requests_counted_once`). Tokens are never
    missing: `tokens_not_recorded` stays false. The tree is built by `agent_tree.build_tree`, deepest agents first."""
    timed = [agent for agent in agents if agent.started_at is not None]
    parent_of = parent_indexes([agent.agent_id for agent in timed], [agent.meta.parent_agent_id for agent in timed])
    depths = depths_of(parent_of, len(timed))
    usage = _requests_counted_once(timed, depths, counted_message_ids)

    def build(index: int, children: Tuple[SubAgent, ...]) -> SubAgent:
        agent = timed[index]
        return SubAgent(agent_id=agent.agent_id, type=agent.meta.type, started_at=agent.started_at,
                        working_time=agent.working_time, requests=usage[index], sub_agents=children)

    return tuple(agent for _, agent in build_tree(parent_of, depths, build))
