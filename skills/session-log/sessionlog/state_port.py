"""Use-case-owned C6 state boundary; sync documents use C6 JSON names."""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol


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
