"""Pure rules for the sub-agents of one session whose sub-agents are whole transcripts of their own (a Codex rollout, an OMP session
file), shared by those readers: the thread a transcript makes, the nesting of the child threads by the thread their parent names, and
the split of a family whose root transcript is gone into the session it archives as and its sub-agents; no file access."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Sequence, Tuple

from .agent_tree import UNKNOWN_TYPE, build_tree, depths_of, parent_indexes
from .model import Prompt, RequestUsage, SubAgent


@dataclass(frozen=True)
class Thread:
    """One transcript after reading; `thread_id` is the agent id, `parent_thread_id` names the thread that spawned it. `prompts`
    and `project_dir` matter only when the thread becomes the session itself (`split_orphan_family`)."""

    thread_id: str
    parent_thread_id: Optional[str]
    agent_type: Optional[str]
    started_at: datetime
    working_time: timedelta
    requests: Tuple[RequestUsage, ...]
    prompts: Tuple[Prompt, ...] = ()
    project_dir: Optional[str] = None


@dataclass(frozen=True)
class OrphanFamily:
    """The session a family without its root transcript is archived as. `started_at` is the earliest start among the top-level
    threads (a nested thread's start does not count). `project_dir` is the folder of the promoted thread, else of the first
    top-level thread that has one. `orphan_of_parent_id` names the parent the promoted thread hangs from. `prompts` are the
    promoted thread's own and `sub_agents` everything else of the family."""

    started_at: datetime
    project_dir: Optional[str]
    orphan_of_parent_id: str
    prompts: Tuple[Prompt, ...]
    sub_agents: Tuple[SubAgent, ...]


def _top_level(threads: Sequence[Thread]) -> List[Tuple[Thread, SubAgent]]:
    """The top-level threads, each with its finished sub-agent tree, ordered by start, then id."""
    parent_of = parent_indexes([thread.thread_id for thread in threads], [thread.parent_thread_id for thread in threads])

    def build(index: int, children: Tuple[SubAgent, ...]) -> SubAgent:
        thread = threads[index]
        return SubAgent(agent_id=thread.thread_id, type=thread.agent_type or UNKNOWN_TYPE, started_at=thread.started_at,
                        working_time=thread.working_time, requests=thread.requests, sub_agents=children)

    top = [(threads[index], agent) for index, agent in build_tree(parent_of, depths_of(parent_of, len(threads)), build)]
    return sorted(top, key=lambda pair: (pair[1].started_at, pair[1].agent_id))


def nest_threads(threads: Sequence[Thread]) -> Tuple[SubAgent, ...]:
    """The threads as a tree of sub-agents: each under the thread its `parent_thread_id` names, which may be a child itself
    (a grandchild's session names the root, so only the parent it names tells where it belongs). A parent that is not
    among the threads (the root or a deleted one), the thread itself, and every thread in a parent loop leave the thread at
    the top level, so none is dropped. Top-level threads are ordered by start, then id."""
    return tuple(agent for _, agent in _top_level(threads))


def split_orphan_family(root_id: str, threads: Sequence[Thread]) -> Optional[OrphanFamily]:
    """The family of the root `root_id` whose own transcript is gone, as one session.

    The first top-level thread (by start, then id) that has a prompt is promoted to be the session itself. Its children become
    sub-agents of the session beside its siblings, and their own subtrees stay nested. Only that one is promoted: a later
    sibling with prompts is a sub-agent and its prompts are not shown. A family where no thread has a prompt has no prompts,
    and every top-level thread stays a sub-agent.

    The session starts at the earliest start among the top-level threads. Its folder is the promoted thread's, else the first
    top-level thread's (by start, then id) that has one. The parent label is the parent the promoted thread names, or the root
    when it names none or names a thread of the family (a loop or itself).

    The parser drops a prompt whose record has no usable time together with its requests (in every transcript), so a promoted
    thread's requests are those left in its prompts and its sub-agent row is gone; only a family without a prompt keeps every
    request of the family in sub-agent rows. None when there is no thread."""
    top = _top_level(threads)
    if not top:
        return None
    promoted = next((pair for pair in top if pair[0].prompts), None)
    head, head_agent = promoted or top[0]
    if promoted:
        prompts = head.prompts
        sub_agents = tuple(agent for _, agent in top if agent is not head_agent) + head_agent.sub_agents
    else:
        prompts = ()
        sub_agents = tuple(agent for _, agent in top)
    names_family_thread = any(thread.thread_id == head.parent_thread_id for thread in threads)
    parent_id = head.parent_thread_id if head.parent_thread_id and not names_family_thread else root_id
    folder_order = ([head] if promoted else []) + [thread for thread, _ in top]
    project_dir = next((thread.project_dir for thread in folder_order if thread.project_dir), None)
    return OrphanFamily(started_at=min(thread.started_at for thread, _ in top), project_dir=project_dir,
                        orphan_of_parent_id=parent_id, prompts=prompts, sub_agents=sub_agents)
