"""Which session a `usage` call names (C1): the latest root session of a project, or the session an id names.

Ranking reads only the session entries of `.sync.json`, so it is the same in every harness. An id is matched exactly, else as
a unique prefix, over the archived sessions and the sessions of the harness store together. Nothing is written; the store is
listed (and a unit `.sync.json` has not recorded is asked of the reader) only when no archived session has that exact id.
"""
import functools
import os
from datetime import datetime
from typing import Dict, Tuple

from .command_error import CommandError
from .model import is_safe_id
from .reader_port import ScanResult, SessionReader, root_id_of
from .state_port import parse_timestamp, recorded_session_key

MAX_LISTED_MATCHES = 5  # an ambiguous prefix names this many matching ids at most
MORE_MATCHES = ", ..."


class UsageTargetResolver:
    """`reader` is the reader of the invoked harness; `path_module` is `os.path` (tests pass `ntpath`)."""

    def __init__(self, reader: SessionReader, path_module=os.path) -> None:
        self._reader = reader
        self._path = path_module

    def latest(self, state: dict, cwd: str) -> str:
        """The key of the root session of the project of `cwd` with the newest prompt; a session without a prompt ranks below
        every session with one, and when none has a prompt the newest activity wins. Sub-agents and orphans never win."""
        candidates = self._project_sessions(state["sessions"], cwd)
        if not candidates:
            raise CommandError("no archived session for this project (%s)" % cwd)

        def rank(item: Tuple[str, dict]) -> Tuple[bool, datetime, str]:
            key, entry = item
            prompt_at = entry.get("lastPromptAt")
            return (prompt_at is not None, parse_timestamp(prompt_at or entry["lastActivityAt"]), key)

        return max(candidates.items(), key=rank)[0]

    def by_id(self, state: dict, target: str) -> str:
        """The key of the session `target` names: an archived exact id, else a store exact id, else the only session whose id
        starts with it. A store that could not be listed completely may hide a second match, so a prefix is not resolved."""
        if not target:
            raise CommandError("a session id must not be empty")
        archived = {root_id_of(key): key for key in state["sessions"]}
        if target in archived:
            return archived[target]
        scan = self._reader.scan()
        stored = {root_id_of(key): key for key in self._store_session_keys(state, scan)}
        if target in stored:
            return stored[target]
        if scan.unreadable:
            first = scan.unreadable[0]
            raise CommandError("the store could not be read completely (%s: %s), so the id %r cannot be resolved"
                               % (first.path, first.reason, target))
        known = {**stored, **archived}
        matches = sorted(session_id for session_id in known if session_id.startswith(target))
        return known[self._the_only(matches, target)]

    def _store_session_keys(self, state: dict, scan: ScanResult) -> set:
        """The session keys of the scanned units: the one sync recorded, else what the reader learns now. A session whose id
        is not a safe id is left out, as sync never archives it."""
        keys = set()
        for unit in scan.units:
            session_key = recorded_session_key(state, unit.key) or self._reader.session_of(unit)
            if session_key is not None and is_safe_id(root_id_of(session_key)):
                keys.add(session_key)
        return keys

    @staticmethod
    def _the_only(matches: list, target: str) -> str:
        if not matches:
            raise CommandError("no session has the id %r" % target)
        if len(matches) > 1:
            listed = ", ".join(matches[:MAX_LISTED_MATCHES]) + (MORE_MATCHES if len(matches) > MAX_LISTED_MATCHES else "")
            raise CommandError("the id prefix %r matches %d sessions: %s" % (target, len(matches), listed))
        return matches[0]

    def _real_or_same(self, folder: str) -> str:
        """`realpath`, or the folder itself when it cannot be resolved (a recorded cwd may hold any text)."""
        try:
            return self._path.realpath(folder)
        except (OSError, ValueError):
            return folder

    def _project_sessions(self, sessions: Dict[str, dict], cwd: str) -> Dict[str, dict]:
        """The root sessions recorded in `cwd`; when none, those of its nearest parent folder that has any.
        Folders match under `normcase`, or both after `realpath` (looked up once per distinct folder)."""
        roots = {key: entry for key, entry in sessions.items() if not entry["orphan"] and "projectDir" in entry}
        real_of = functools.lru_cache(maxsize=None)(self._real_or_same)
        normcase = self._path.normcase
        folder = cwd
        while True:
            folder_normal, folder_real = normcase(folder), real_of(folder)
            found = {key: entry for key, entry in roots.items()
                     if normcase(entry["projectDir"]) == folder_normal or real_of(entry["projectDir"]) == folder_real}
            parent = self._path.dirname(folder)
            if found or parent == folder:
                return found
            folder = parent
