"""Pure rules for the sub-agents of one Codex session: the thread a rollout makes and which copy of a thread is read; the nesting of
the child threads by `parent_thread_id` and the split of a family whose root rollout is gone are the shared rules of `agent_family`;
no file access."""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from .agent_family import Thread
from .codex_rollout import Rollout

_NO_TIME = datetime.min.replace(tzinfo=timezone.utc)  # ranks a header without a start time under every real one


def copy_rank(started_at: Optional[datetime], key: str) -> Tuple[datetime, str, str]:
    """Which of several rollouts of one thread is read, the greatest rank: the latest header start (none is the oldest), then
    the file name (the last part of the key), then the whole key. The folder comes only after the name, and the file's mtime
    is not part of the rank, so touching or restoring an older copy changes nothing."""
    return started_at or _NO_TIME, os.path.basename(key), key


def thread_of_rollout(rollout: Rollout, modified_at: datetime) -> Optional[Thread]:
    """The thread a rollout makes; None when it has no header or nothing gives it a start. It starts with its first record
    after the header (a header is written when the thread is created, which can be minutes before it does anything); one with
    no record time after its header starts at the header; a header without a time gives the rollout's file time `modified_at`
    when it has requests (as for a Claude sub-agent), else the rollout has no row. It works from its start to its newest
    record; a rollout with no record time after the header works no time."""
    meta = rollout.meta
    if meta is None:
        return None
    started_at = rollout.first_moment or meta.started_at or (modified_at if rollout.requests else None)
    if started_at is None:
        return None
    working_time = timedelta(0)
    if rollout.first_moment is not None and rollout.last_moment is not None:
        working_time = rollout.last_moment - started_at  # `started_at` is `first_moment`, never after the newest record
    return Thread(thread_id=meta.thread_id, parent_thread_id=meta.parent_thread_id, agent_type=meta.agent_type,
                  started_at=started_at, working_time=working_time, requests=rollout.requests, prompts=rollout.prompts,
                  project_dir=meta.project_dir)
