"""Use-case-owned C2 reader boundary; one adapter per harness implements it and owns every path and format rule."""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol, Tuple

from .model import Harness, Session


UNSAFE_ID_REASON = "unsafe id"
CANNOT_BE_OPENED_REASON = "cannot be opened"
CANNOT_BE_DECODED_REASON = "cannot be decoded"
CANNOT_BE_LISTED_REASON = "cannot be listed"


def build_session_key(harness: Harness, root_id: str) -> str:
    """The session key every reader and the state use: `<harness>:<root id>`."""
    return "%s:%s" % (harness.value, root_id)


@dataclass(frozen=True)
class SourceUnit:
    """One source a session is built from. `key` identifies it between scans. `fingerprint` is stable while the source
    is unchanged and changes whenever its content may have changed; it is derived without reading content (files:
    size and mtime_ns; other harnesses: their own cheap signal). `modified_at` (timezone-aware) is the sync's
    pre-filter: a unit last modified before the on-time has no activity since."""

    key: str
    fingerprint: str
    modified_at: datetime


@dataclass(frozen=True)
class UnreadableSource:
    """A source the reader could not use; `reason` is a short text recorded for status."""

    harness: Harness
    path: str
    reason: str


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
        """The session key `<harness>:<root id>` the unit belongs to; None when it cannot be learned now."""
        ...

    def read(self, session_key: str, units: Tuple[SourceUnit, ...]) -> ReadResult:
        """Builds the whole session from all its units. Never raises for file-system or format errors."""
        ...
