"""Use-case-owned leftover boundary (C1): what the command use cases need from the leftover scan."""
from dataclasses import dataclass
from typing import Protocol, Tuple


@dataclass(frozen=True)
class Leftover:
    """An old-release leftover: reported, never deleted. `fix` is one line."""

    path: str
    fix: str


class LeftoverScanner(Protocol):
    """The old-release leftovers of the invoked harness, sorted by path. Reads only."""

    def scan(self) -> Tuple[Leftover, ...]: ...
