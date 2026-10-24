"""Which session a `usage` call names (C1): the latest root session of a project, or the session an id names.

Archived sessions are ranked from the session entries of `.sync.json` alone, so it is the same in every harness. The store of
the harness is read only for a session that was never archived. `latest` falls back to it when the project has no archived
session. An id is matched exactly, else as a unique prefix, over the archived sessions and the sessions of the store
together; the store is listed only when no archived session has that exact id, and the reader is asked about a unit
`.sync.json` has not recorded. The resolver also reads the store sessions it resolves to, so the caller need not. Nothing
is written.
"""
import functools
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, Iterator, List, Optional, Tuple

from .command_error import CommandError
from .model import Session, is_safe_id
from .reader_port import ScanResult, SessionReader, SourceUnit, root_id_of
from .state_port import parse_timestamp, recorded_session_key

MAX_LISTED_MATCHES = 5  # an ambiguous prefix names this many matching ids at most
MORE_MATCHES = ", ..."

_Units = Tuple[SourceUnit, ...]


@dataclass(frozen=True)
class ResolvedSession:
    """The session a `usage` call resolved to. `session` is None for an archived session (its saved report is printed, unless
    a rewrite of it failed in this run); otherwise it is the session read from the store while resolving (a never-archived
    one), so nothing reads it twice."""

    session_key: str
    session: Optional[Session] = None


class UsageTargetResolver:
    """`reader` is the reader of the invoked harness; `path_module` is `os.path` (tests pass `ntpath`)."""

    def __init__(self, reader: SessionReader, path_module=os.path) -> None:
        self._reader = reader
        self._path = path_module

    def latest(self, state: dict, cwd: str) -> ResolvedSession:
        """The root session of the project of `cwd` with the newest prompt among the archived ones; a session without a prompt
        ranks below every session with one, and when none has a prompt the newest activity wins. Sub-agents and orphans never
        win. Only a project with no archived session reads the store (`_latest_in_store`)."""
        candidates = self._project_sessions(state["sessions"], cwd)
        if not candidates:
            return self._latest_in_store(state, cwd)

        def rank(item: Tuple[str, dict]) -> Tuple[bool, datetime, str]:
            key, entry = item
            prompt_at = entry.get("lastPromptAt")
            return (prompt_at is not None, parse_timestamp(prompt_at or entry["lastActivityAt"]), key)

        return ResolvedSession(max(candidates.items(), key=rank)[0])

    def by_id(self, state: dict, target: str) -> ResolvedSession:
        """The session `target` names: an archived exact id, else a store exact id, else the only session whose id starts
        with it. A store that could not be listed completely may hide a second match, so a prefix is not resolved. A match
        that is not archived is read now, completely, and carried in the target."""
        if not target:
            raise CommandError("a session id must not be empty")
        archived = {root_id_of(key): key for key in state["sessions"]}
        if target in archived:
            return ResolvedSession(archived[target])
        scan = self._reader.scan()
        units_of = self._store_units(state, scan)
        stored = {root_id_of(key): key for key in units_of}
        if target in stored:
            return self._resolved(state, units_of, stored[target])
        if scan.unreadable:
            raise self._incomplete_store(scan, "so the id %r cannot be resolved" % target)
        known = {**stored, **archived}
        matches = sorted(session_id for session_id in known if session_id.startswith(target))
        return self._resolved(state, units_of, known[self._the_only(matches, target)])

    def _resolved(self, state: dict, units_of: Dict[str, _Units], session_key: str) -> ResolvedSession:
        """The archived session as its key alone; a store session read now, or an error when it holds nothing to report."""
        if session_key in state["sessions"]:
            return ResolvedSession(session_key)
        session = self._read_complete(session_key, units_of[session_key])
        if session is None:
            raise CommandError("session %s has nothing to report" % root_id_of(session_key))
        return ResolvedSession(session_key, session)

    def _read_complete(self, session_key: str, units: _Units) -> Optional[Session]:
        """The session of `units`, or None when they hold nothing. An unreadable source raises even when a session was built:
        a report that silently misses a source would be under-counted."""
        result = self._reader.read(session_key, units)
        if result.unreadable:
            first = result.unreadable[0]
            raise CommandError("session %s cannot be read (%s: %s)" % (root_id_of(session_key), first.path, first.reason))
        return result.session

    @staticmethod
    def _incomplete_store(scan: ScanResult, consequence: str) -> CommandError:
        first = scan.unreadable[0]
        return CommandError("the store could not be read completely (%s: %s), %s" % (first.path, first.reason, consequence))

    def _latest_in_store(self, state: dict, cwd: str) -> ResolvedSession:
        """Of the root sessions of the store that hold a prompt, the first one in the order of the newest source unit (the
        newest file, not necessarily the newest prompt) in the project of `cwd`, else in the nearest parent folder with such
        a session. A winner found only in a parent folder needs every session of the store read (cost accepted). An
        unlistable folder or an unreadable session could hide a newer session, so it ends the lookup. Archived sessions are left
        out: their saved report wins, and they need no read."""
        scan = self._reader.scan()
        if scan.unreadable:
            raise self._incomplete_store(scan, "so the latest session cannot be determined")
        matchers = list(self._project_matchers(cwd))
        first_of_folder: List[Optional[ResolvedSession]] = [None] * len(matchers)  # one winner per folder, nearest folder first
        units_of = {key: units for key, units in self._store_units(state, scan).items() if key not in state["sessions"]}
        for session_key, session in self._root_sessions_with_prompt(units_of):
            for level, is_in_folder in enumerate(matchers):
                if is_in_folder(session.project_dir):
                    if level == 0:
                        return ResolvedSession(session_key, session)  # nothing can be nearer
                    if first_of_folder[level] is None:
                        first_of_folder[level] = ResolvedSession(session_key, session)
                    break
        for winner in first_of_folder:
            if winner is not None:
                return winner
        raise CommandError("no session for this project (%s)" % cwd)

    def _root_sessions_with_prompt(self, units_of: Dict[str, _Units]) -> Iterator[Tuple[str, Session]]:
        """The root sessions that hold a prompt and a project folder, newest source unit first, read one at a time."""
        newest_first = sorted(units_of, key=lambda key: max((unit.modified_at, unit.key) for unit in units_of[key]), reverse=True)
        for session_key in newest_first:
            session = self._read_complete(session_key, units_of[session_key])
            if session is not None and session.orphan_of_parent_id is None and session.prompts and session.project_dir is not None:
                yield session_key, session

    def _store_units(self, state: dict, scan: ScanResult) -> Dict[str, _Units]:
        """The units of the scan per session key: the key `.sync.json` recorded for a unit, else what the reader learns now.
        A session whose id is not a safe id is left out, as sync never archives it."""
        grouped: Dict[str, List[SourceUnit]] = {}
        for unit in scan.units:
            session_key = recorded_session_key(state, unit.key) or self._reader.session_of(unit)
            if session_key is not None and is_safe_id(root_id_of(session_key)):
                grouped.setdefault(session_key, []).append(unit)
        return {session_key: tuple(units) for session_key, units in grouped.items()}

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

    def _project_matchers(self, cwd: str) -> Iterator[Callable[[str], bool]]:
        """For `cwd` and then each parent folder, nearest first, whether a recorded project folder is that folder. Folders
        match under `normcase`, or both after `realpath` (looked up once per distinct folder)."""
        real_of = functools.lru_cache(maxsize=None)(self._real_or_same)
        normcase = self._path.normcase
        folder = cwd
        while True:
            folder_normal, folder_real = normcase(folder), real_of(folder)
            yield lambda project_dir, normal=folder_normal, real=folder_real: (
                normcase(project_dir) == normal or real_of(project_dir) == real)
            parent = self._path.dirname(folder)
            if parent == folder:
                return
            folder = parent

    def _project_sessions(self, sessions: Dict[str, dict], cwd: str) -> Dict[str, dict]:
        """The root sessions recorded in `cwd`; when none, those of its nearest parent folder that has any."""
        roots = {key: entry for key, entry in sessions.items() if not entry["orphan"] and "projectDir" in entry}
        for matches in self._project_matchers(cwd):
            found = {key: entry for key, entry in roots.items() if matches(entry["projectDir"])}
            if found:
                return found
        return {}
