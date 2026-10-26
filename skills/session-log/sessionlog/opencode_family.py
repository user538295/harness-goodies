"""Pure rules for the sessions of one OpenCode family: which session is the root of a `parent_id` chain, and how the root's transcript and
its sub-agent sessions' make the session with its nested sub-agents, or the orphan session; no database access.

The root's prompts are its own, led by the row of its requests that fall under no prompt (`prompt_turns.with_unprompted`); every
sub-agent session reports its requests and the time it worked, and is nested under the session
its `parent_id` names (`agent_family.nest_threads`): a parent that is the root, or is not among the sub-agents, leaves it at the top
level. A session whose parent row is gone is the root of its own family and is labelled with the parent it names, so it loses nothing
(unlike Codex and OMP, whose orphan family is split by `agent_family.split_orphan_family`, a session here is one row with its own
prompts). A session in a parent loop, or hanging from one, is judged by the caller (`agent_tree.nodes_entering_a_loop`): it is its own
session key and builds no session."""
from datetime import datetime
from typing import Dict, NamedTuple, Optional, Sequence, Set, Tuple

from .agent_family import Thread, nest_threads
from .model import Harness, Session
from .opencode_messages import Transcript
from .prompt_turns import with_unprompted

ParentOf = Dict[str, Optional[str]]


class SessionRow(NamedTuple):
    """One `session_v2` row: its id, the session its `parent_id` names (None for none), its start (None when the row holds no time), its
    project folder and its agent."""

    id: str
    parent_id: Optional[str]
    created_at: Optional[datetime]
    directory: Optional[str]
    agent: Optional[str]


def root_id_for(session_id: str, parent_of: ParentOf, looping: Set[str]) -> str:
    """The id of the session `session_id` is archived under: the topmost session of its chain of parents (the one whose parent names no
    session), or `session_id` itself when its chain loops or enters a loop (`looping`, from `agent_tree.nodes_entering_a_loop`), so a
    loop is never walked. A loop `looping` does not name ends the walk at the session where the chain comes back to itself."""
    if session_id in looping:
        return session_id
    seen = {session_id}
    node = session_id
    parent = parent_of.get(node)
    while parent is not None and parent in parent_of and parent not in seen:
        node = parent
        seen.add(node)
        parent = parent_of.get(node)
    return node


def _thread_of(row: SessionRow, transcript: Transcript, default_start: datetime) -> Thread:
    started_at = row.created_at or default_start
    return Thread(thread_id=row.id, parent_thread_id=row.parent_id, agent_type=row.agent, started_at=started_at,
                  working_time=transcript.working_time_since(started_at), requests=transcript.requests)


def build_session(root: SessionRow, root_transcript: Transcript, parent_of: ParentOf,
                  children: Sequence[Tuple[SessionRow, Transcript]]) -> Optional[Session]:
    """The session of the family of `root`; None when the row holds no start time. `children` are the sub-agent sessions below it, none
    in a parent loop; one whose row holds no start time starts when the root does. `parent_of` names every session of the database:
    a root whose parent is not among them is labelled with it."""
    if root.created_at is None:
        return None
    threads = [_thread_of(row, transcript, root.created_at) for row, transcript in children]
    orphan_of_parent_id = root.parent_id if root.parent_id not in parent_of else None
    prompts = with_unprompted(root_transcript.prompts, root_transcript.unprompted_requests, root.created_at)
    return Session(harness=Harness.OPENCODE, session_id=root.id, started_at=root.created_at, project_dir=root.directory,
                   orphan_of_parent_id=orphan_of_parent_id, prompts=prompts, sub_agents=nest_threads(threads))
