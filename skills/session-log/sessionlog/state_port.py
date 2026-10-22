"""Use-case-owned C6 state boundary; sync documents use C6 JSON names."""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol


def parse_timestamp(value: str) -> datetime:
    """A time as `.sync.json` holds it: ISO 8601, `Z` accepted for UTC."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def recorded_session_key(state: dict, unit_key: str) -> Optional[str]:
    """The session key `.sync.json` records for the source unit; None for a unit sync has not learned yet."""
    return (state["units"].get(unit_key) or {}).get("sessionKey")


@dataclass(frozen=True)
class OnFlag:
    on_at: datetime
    from_file_time: bool = False


class StateStore(Protocol):
    sync_reset: bool

    def read_on_flag(self) -> Optional[OnFlag]: ...
    def write_on_flag(self, flag: OnFlag) -> None: ...
    def delete_on_flag(self) -> None: ...
    def load_sync(self) -> dict: ...
    def save_sync(self, state: dict) -> None: ...
