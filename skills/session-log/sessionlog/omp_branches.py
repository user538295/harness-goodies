"""Which entries of an OMP session file lie on the branch the conversation ended on; pure, no file access.

OMP stores a session as an append-only tree: every entry names its `parentId`, the next entry hangs from the current leaf, and
going back to an earlier message (a retry after an abort, `/tree`, a rewind) only moves the leaf, leaving the old branch in the
file. A reloaded session's leaf is its last entry (`model_usage` records excepted: they are attached beside the leaf, possibly to
a branch that is no longer active, and never move it). The active branch is the chain of parents from that leaf up.

An entry stands as `ACTIVE` when it is on that chain, and also when it does not provably fork from it: an entry without links
(an older file has none), one whose parents are missing or loop, and a tree apart from the active one. Only a branch that is
seen to leave the active chain is left: its first entry, a sibling of the active one, is a `SIBLING`; everything deeper is
`ABANDONED`."""
from enum import Enum
from typing import Dict, Optional, Set


class Standing(Enum):
    ACTIVE = "active"
    SIBLING = "sibling"
    ABANDONED = "abandoned"


class SessionTree:
    """The `parentId` links of the entries of a session file in the order they were written, and the standing each entry has."""

    def __init__(self) -> None:
        self._parents: Dict[str, Optional[str]] = {}
        self._leaf: Optional[str] = None
        self._path: Optional[Set[str]] = None
        self._forks: Dict[str, bool] = {}

    def add(self, entry_id: str, parent_id: Optional[str], moves_leaf: bool) -> None:
        """Adds an entry hanging from `parent_id` (None: a root); `moves_leaf` is False for a record attached beside the leaf."""
        self._parents[entry_id] = parent_id
        if moves_leaf:
            self._leaf = entry_id
        self._path = None
        self._forks.clear()

    def _active_path(self) -> Set[str]:
        """The entries from the leaf up to a root, up to a missing parent or to the first entry seen twice (a loop)."""
        if self._path is None:
            path: Set[str] = set()
            entry = self._leaf
            while entry is not None and entry in self._parents and entry not in path:
                path.add(entry)
                entry = self._parents[entry]
            self._path = path
        return self._path

    def _leaves_the_active_path(self, entry_id: str) -> bool:
        """Whether going up from the entry reaches the active chain, so that the entry hangs from it without being on it."""
        if entry_id in self._forks:
            return self._forks[entry_id]
        path = self._active_path()
        trail: Set[str] = set()
        entry: Optional[str] = entry_id
        while entry is not None and entry in self._parents and entry not in trail and entry not in path:
            trail.add(entry)
            entry = self._parents[entry]
        forks = entry in path
        self._forks.update((member, forks) for member in trail)
        return forks

    def standing(self, entry_id: Optional[str]) -> Standing:
        """The standing of an entry (None: an entry that has no links)."""
        if entry_id is None or entry_id in self._active_path() or not self._leaves_the_active_path(entry_id):
            return Standing.ACTIVE
        return Standing.SIBLING if self._parents[entry_id] in self._active_path() else Standing.ABANDONED
