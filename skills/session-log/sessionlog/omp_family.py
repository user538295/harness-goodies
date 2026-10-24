"""Pure rules for the files of one OMP session family (see `omp_layout`): which children hang in a parent loop, and how the root's
transcript and its children's make the session with its nested sub-agents, or, without the root's own file, the orphan session; no
file access.

A child links to its parent by the file stem of its header's `parentSession`, so a parent moved into `archive/sessions/` is still
found. Files that share a stem are one node of that link graph: the first of them, in key order, is the parent others name. The
nesting and the orphan split are the shared ones of `agent_family`, over `Thread`s made from the transcripts."""
from datetime import datetime, timedelta
from typing import Dict, List, NamedTuple, Optional, Sequence, Set

from .agent_family import Thread, nest_threads, split_orphan_family
from .agent_tree import nodes_entering_a_loop
from .model import Harness, Session
from .omp_layout import stem_of_path
from .omp_transcript import Transcript


class Member(NamedTuple):
    """One readable file of the family: its unit `key`, its file `stem`, the file time and what it holds."""

    key: str
    stem: str
    modified_at: datetime
    transcript: Transcript

    @property
    def agent_id(self) -> str:
        """The id the sub-agent is shown and linked by: its header's id, else its file stem."""
        return self.transcript.header_id or self.stem

    @property
    def parent_stem(self) -> Optional[str]:
        parent = self.transcript.parent_session
        return stem_of_path(parent) if parent else None

    @property
    def started_at(self) -> Optional[datetime]:
        """When the sub-agent started: its first record (a header is written when the session is created, before it does anything),
        else its header, else the file time when it made requests; None when nothing gives it a start."""
        transcript = self.transcript
        return transcript.first_moment or transcript.header_moment or (self.modified_at if transcript.requests else None)

    @property
    def working_time(self) -> timedelta:
        """From its start to its newest record; none without a start or a record time."""
        started_at, last_moment = self.started_at, self.transcript.last_moment
        if started_at is None or last_moment is None:
            return timedelta(0)
        return max(last_moment - started_at, timedelta(0))


def _parent_stems(children: Sequence[Member]) -> Dict[str, Optional[str]]:
    """The parent stem each stem names; of files that share a stem the first, in the order given, counts."""
    parent_of: Dict[str, Optional[str]] = {}
    for child in children:
        parent_of.setdefault(child.stem, child.parent_stem)
    return parent_of


def cyclic_keys(children: Sequence[Member]) -> Set[str]:
    """The keys of the children that are in a parent loop or hang from one. The root's own file has no parent link."""
    looping = nodes_entering_a_loop(_parent_stems(children))
    return {child.key for child in children if child.stem in looping}


def _threads_of(root_id: str, root_stem: str, children: Sequence[Member]) -> List[Thread]:
    """The sub-agent threads of the children. A child's parent is named by the agent id of the child it links to, by the root id when
    it links to the root's file, and by the parent's file stem when no file of the family has that stem (a parent that is gone);
    none of the last two is a thread, so such a child stays at the top level."""
    first_by_stem: Dict[str, Member] = {}
    for child in children:
        first_by_stem.setdefault(child.stem, child)
    threads = []
    for child in children:
        started_at = child.started_at
        if started_at is None:
            continue
        parent = child.parent_stem
        if parent is None:
            parent_id = None
        elif parent in first_by_stem:
            parent_id = first_by_stem[parent].agent_id
        else:
            parent_id = root_id if parent == root_stem else parent
        transcript = child.transcript
        threads.append(Thread(thread_id=child.agent_id, parent_thread_id=parent_id, agent_type=transcript.agent_type,
                              started_at=started_at, working_time=child.working_time, requests=transcript.requests,
                              prompts=transcript.prompts, project_dir=transcript.project_dir))
    return threads


def build_session(root_id: str, root_stem: str, root: Optional[Member], children: Sequence[Member]) -> Optional[Session]:
    """The session of the family. With the root's own file it has the root's prompts and every child as a sub-agent (a child whose
    parent is gone stays at the top level); None when the file gives no start time. Without it the family is an orphan archived
    alone (`split_orphan_family`): the first top-level child with a prompt is the session itself, the others are its sub-agents, and
    the session is labelled with the parent the promoted child names; None when no child gives a start time. `children` have no
    parent loop (see `cyclic_keys`)."""
    threads = _threads_of(root_id, root_stem, children)
    if root is not None:
        transcript = root.transcript
        started_at = transcript.header_moment or transcript.first_moment
        if started_at is None:
            return None
        return Session(harness=Harness.OMP, session_id=root_id, started_at=started_at, project_dir=transcript.project_dir,
                       prompts=transcript.prompts, sub_agents=nest_threads(threads))
    family = split_orphan_family(root_id, threads)
    if family is None:
        return None
    return Session(harness=Harness.OMP, session_id=root_id, started_at=family.started_at, project_dir=family.project_dir,
                   orphan_of_parent_id=family.orphan_of_parent_id, prompts=family.prompts, sub_agents=family.sub_agents)
