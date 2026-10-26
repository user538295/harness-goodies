"""Use-case-owned C2 reader boundary; one adapter per harness implements it and owns every path and format rule."""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol, Tuple, runtime_checkable

from .model import Harness, Session


UNSAFE_ID_REASON = "unsafe id"
PARENT_CYCLE_REASON = "parent cycle"
CANNOT_BE_OPENED_REASON = "cannot be opened"
CANNOT_BE_DECODED_REASON = "cannot be decoded"
CANNOT_BE_LISTED_REASON = "cannot be listed"
CANNOT_BE_PARSED_REASON = "cannot be parsed"
NOT_A_REGULAR_FILE_REASON = "not a regular file"


def build_session_key(harness: Harness, root_id: str) -> str:
    """The session key every reader and the state use: `<harness>:<root id>`."""
    return "%s:%s" % (harness.value, root_id)


def root_id_of(session_key: str) -> str:
    """The root id part of a session key built by `build_session_key`; empty for a text without `:`."""
    return session_key.partition(":")[2]


@dataclass(frozen=True)
class SourceUnit:
    """One source a session is built from. `key` identifies it between scans. `fingerprint` is stable while the source
    is unchanged and changes whenever its content may have changed; it is derived without reading content (Claude files:
    size and mtime_ns; Codex files: mtime_ns alone, which compression keeps; OMP files: size and mtime_ns; other harnesses: their own
    cheap signal).
    `modified_at` (timezone-aware) is the sync's pre-filter: a unit last modified before the on-time has no activity since."""

    key: str
    fingerprint: str
    modified_at: datetime


@dataclass(frozen=True)
class UnreadableSource:
    """A source the reader could not use; `reason` is a short text recorded for status. When the failure concerns a unit
    handed to `read`, `path` equals that unit's `key`: sync marks the units it names unreadable and retries them on every
    sync (every unit of the session when none could be built). `hides_sessions` is False for a finding that cannot hide a session
    (Cursor's folder of filed-away hook calls): `status` lists it, but a `usage` lookup is not stopped by it."""

    harness: Harness
    path: str
    reason: str
    hides_sessions: bool = True


@dataclass(frozen=True)
class ScanResult:
    """What a scan found; `unreadable` lists sources that could not even be listed."""

    units: Tuple[SourceUnit, ...] = ()
    unreadable: Tuple[UnreadableSource, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "units", tuple(self.units))
        object.__setattr__(self, "unreadable", tuple(self.unreadable))


@dataclass(frozen=True)
class ReadResult:
    """`session` is None when nothing can be learned from the sources."""

    session: Optional[Session] = None
    unreadable: Tuple[UnreadableSource, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "unreadable", tuple(self.unreadable))


class SessionReader(Protocol):
    def scan(self) -> ScanResult:
        """Lists every unit without opening it; a missing source folder gives an empty result."""
        ...

    def session_of(self, unit: SourceUnit) -> Optional[str]:
        """The session key `<harness>:<root id>` the unit belongs to; None when it cannot be learned now. The key is
        returned even when its root id is not a safe id: sync lists such a unit unreadable ("unsafe id") and never reads it."""
        ...

    def read(self, session_key: str, units: Tuple[SourceUnit, ...]) -> ReadResult:
        """Builds the whole session from all its units, which sync has already grouped by session: `read` must not learn
        a unit's session from its content again (sync counts `session_of` calls: a unit is learned once). Never raises
        for file-system or format errors."""
        ...


@runtime_checkable
class SubAgentNaming(Protocol):
    """Optional capability of a reader that names a sub-agent by the file of its unit: a lookup of a sub-agent id then reads only the
    sessions that have such a unit. A reader without it is asked to read every session."""

    def may_hold_sub_agent(self, unit: SourceUnit, agent_id: str) -> bool:
        """False only when `unit` certainly holds no sub-agent with `agent_id`, known from the unit alone (no read)."""
        ...


@runtime_checkable
class RankingUnits(Protocol):
    """Optional capability of a reader whose sub-agent units never change a session's prompts, project folder or orphan state: a lookup
    that ranks many sessions reads only those units of each, and reads one session whole only once it is picked. A reader without it
    is asked to read every session whole."""

    def units_for_ranking(self, units: Tuple[SourceUnit, ...]) -> Tuple[SourceUnit, ...]:
        """The units of one session that alone decide its prompts, project folder and orphan state."""
        ...
