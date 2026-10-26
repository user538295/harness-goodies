"""Which session a `usage` call names (C1): the latest root session of a project, or the session an id names.

Archived sessions are ranked from the session entries of `.sync.json` alone, so it is the same in every harness. The store of
the harness is read only for a session that was never archived. `latest` lists it for those too and, in the nearest folder that
has a session of either kind, takes the one with the newest prompt (an archived session needs no read). An id is matched
exactly, else as a unique prefix, over the archived sessions and the sessions of the store together; failing both, it is
matched exactly against the sub-agent ids of the store's sessions and names the root session that holds it (a reader that names its
sub-agents by unit, `SubAgentNaming`, has only the sessions with such a unit read). The store is listed
only when no archived session has that exact id, and the reader is asked about a unit `.sync.json` has not recorded. The
resolver also reads the store sessions it resolves to, so the caller need not. Nothing is written.
"""
import functools
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, Iterator, List, NamedTuple, Optional, Tuple

from .command_error import CommandError
from .model import Session, SubAgent, is_safe_id
from .reader_port import RankingUnits, ScanResult, SessionReader, SourceUnit, SubAgentNaming, UnreadableSource, root_id_of
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


class _Archived(NamedTuple):
    """The best archived candidate: the folder `level` it was found at (0 = `cwd`), whether it has a prompt, its newest prompt
    (else newest activity) time `at`, and its key."""

    level: int
    has_prompt: bool
    at: datetime
    session_key: str


_StoredRank = Tuple[int, datetime, Tuple[datetime, str]]  # nearer folder, newer prompt, newer source unit rank higher


class _Stored(NamedTuple):
    """The best never-archived candidate and the folder `level` it was found at."""

    level: int
    resolved: ResolvedSession


class UsageTargetResolver:
    """`reader` is the reader of the invoked harness; `path_module` is `os.path` (tests pass `ntpath`)."""

    def __init__(self, reader: SessionReader, path_module=os.path) -> None:
        self._reader = reader
        self._path = path_module

    def latest(self, state: dict, cwd: str) -> ResolvedSession:
        """The root session of the project of `cwd` with the newest prompt, archived or not. The project is the nearest folder,
        `cwd` first and then each parent, that holds a session of either kind: a never-archived session of a nearer folder
        beats an archived one of a parent. In one folder the newest prompt wins; a session without a prompt ranks below every
        session with one, and when no archived one has a prompt the newest activity wins among the archived ones. Sub-agents and
        orphans never win. An archived session is ranked from `.sync.json` alone; the store is listed to find the never-archived
        ones (`_latest_in_store`). A store that cannot be listed completely, or a session in it that cannot be read, ends the
        lookup only when the project has no archived session; else it is passed over, as it never made `usage` fail before."""
        matchers = list(self._project_matchers(cwd))
        archived = self._latest_archived(state["sessions"], matchers)
        scan = self._reader.scan()
        hiding = self._hiding_sessions(scan)
        if hiding and archived is None:
            raise self._incomplete_store(hiding, "so the latest session cannot be determined")
        stored = self._latest_in_store(state, scan, matchers, skip_unreadable=archived is not None)
        if archived is None and stored is None:
            raise CommandError("no session for this project (%s)" % cwd)
        if archived is None:
            return stored.resolved
        if stored is None:
            return ResolvedSession(archived.session_key)
        stored_prompt_at = self._newest_prompt_at(stored.resolved.session)
        stored_is_newer = stored.level < archived.level or (
            stored.level == archived.level and (True, stored_prompt_at) > (archived.has_prompt, archived.at))
        return stored.resolved if stored_is_newer else ResolvedSession(archived.session_key)

    def by_id(self, state: dict, target: str) -> ResolvedSession:
        """The session `target` names: an archived exact id, else a store exact id, else the only session whose id starts
        with it, else the root session that holds a sub-agent of exactly that id (an id the report prints). A store that could
        not be listed completely may hide a second match, so a prefix is not resolved. A match that is not archived is read
        now, completely, and carried in the target."""
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
        hiding = self._hiding_sessions(scan)
        if hiding:
            raise self._incomplete_store(hiding, "so the id %r cannot be resolved" % target)
        known = {**stored, **archived}
        matches = sorted(session_id for session_id in known if session_id.startswith(target))
        if not matches:
            return self._holding_sub_agent(state, units_of, target)
        return self._resolved(state, units_of, known[self._the_only(matches, target)])

    def read_stored(self, state: dict, session_key: str) -> Optional[Session]:
        """The archived session `session_key` read now from the store, whole, for a report whose saved file is gone; None when
        none of its sources is left or they hold nothing. An unreadable source raises (see `_read_complete`)."""
        units = self._store_units(state, self._reader.scan()).get(session_key)
        return None if units is None else self._read_complete(session_key, units)

    def _holding_sub_agent(self, state: dict, units_of: Dict[str, _Units], target: str) -> ResolvedSession:
        """The root session of the store whose sub-agents (nested ones too) include `target`, newest source unit first. Only the
        store tells, as `.sync.json` records no sub-agent ids, so an archived session whose sources are gone is not found. A
        session that cannot be read may hold the id, so a miss beside one is an error, not "no session". A session the reader rules out
        by its units (`_may_hold_sub_agent`) is not read, so it is no such session."""
        first_unreadable: Optional[Tuple[str, UnreadableSource]] = None
        for session_key in self._may_hold_sub_agent(units_of, target):
            result = self._reader.read(session_key, units_of[session_key])
            if result.unreadable and first_unreadable is None:
                first_unreadable = (session_key, result.unreadable[0])
            if result.session is None or not self._has_sub_agent(result.session.sub_agents, target):
                continue
            if session_key in state["sessions"]:
                return ResolvedSession(session_key)
            if result.unreadable:
                raise self._unreadable_session(session_key, result.unreadable[0])
            return ResolvedSession(session_key, result.session)
        if first_unreadable is not None:
            raise CommandError("%s, so the id %r cannot be resolved" % (self._unreadable_session(*first_unreadable), target))
        raise CommandError("no session has the id %r" % target)

    def _may_hold_sub_agent(self, units_of: Dict[str, _Units], agent_id: str) -> List[str]:
        """The session keys, newest source unit first, whose units may hold a sub-agent `agent_id`: all of them, unless the reader names
        its sub-agents by unit (`SubAgentNaming`), and then only those with a unit that may."""
        keys = self._newest_first(units_of)
        if not isinstance(self._reader, SubAgentNaming):
            return keys
        return [key for key in keys if any(self._reader.may_hold_sub_agent(unit, agent_id) for unit in units_of[key])]

    @classmethod
    def _has_sub_agent(cls, sub_agents: Tuple[SubAgent, ...], agent_id: str) -> bool:
        return any(agent.agent_id == agent_id or cls._has_sub_agent(agent.sub_agents, agent_id) for agent in sub_agents)

    @staticmethod
    def _unreadable_session(session_key: str, source: UnreadableSource) -> CommandError:
        return CommandError("session %s cannot be read (%s: %s)" % (root_id_of(session_key), source.path, source.reason))

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
            raise self._unreadable_session(session_key, result.unreadable[0])
        return result.session

    @staticmethod
    def _hiding_sessions(scan: ScanResult) -> Tuple[UnreadableSource, ...]:
        """What the scan could not use that may hide a session; a finding for `status` alone (`hides_sessions` False) is left out."""
        return tuple(source for source in scan.unreadable if source.hides_sessions)

    @staticmethod
    def _incomplete_store(hiding: Tuple[UnreadableSource, ...], consequence: str) -> CommandError:
        first = hiding[0]
        return CommandError("the store could not be read completely (%s: %s), %s" % (first.path, first.reason, consequence))

    def _latest_in_store(self, state: dict, scan: ScanResult, matchers: List[Callable[[str], bool]], skip_unreadable: bool
                         ) -> Optional[_Stored]:
        """Of the never-archived root sessions of the store that hold a prompt, the one with the newest prompt in the nearest folder
        of `matchers` that has one, with its level; None when no folder has one. A tie goes to the session with the newest source
        unit. Archived sessions are left out: their saved report is ranked from `.sync.json`. The file times bound nothing: a restored
        file has an old time and the newest prompt, so every session of the store is read, from the units of `_ranking_units`, and only
        the one picked is read whole. With `skip_unreadable`, unreadable sessions are passed over, a picked one that cannot be read
        whole too (the next best is picked then); an unreadable file only a whole read opens (a sub-agent's) ends the lookup only for a
        session that would win."""
        units_of = {key: units for key, units in self._store_units(state, scan).items() if key not in state["sessions"]}
        ranking: List[Tuple[_StoredRank, str]] = []  # every candidate, light; only the best one keeps the session read for it
        best: Optional[Tuple[_StoredRank, str, Session]] = None
        for session_key, ranked in self._root_sessions_with_prompt(units_of, skip_unreadable):
            level = next((level for level, is_in_folder in enumerate(matchers) if is_in_folder(ranked.project_dir)), None)
            if level is None:
                continue
            rank = (-level, self._newest_prompt_at(ranked), self._newest_unit(units_of[session_key]))
            ranking.append((rank, session_key))
            if best is None or rank > best[0]:
                best = (rank, session_key, ranked)
        for rank, session_key in sorted(ranking, reverse=True):
            ranked = best[2] if best[1] == session_key else None
            session = self._whole(session_key, units_of[session_key], ranked, skip_unreadable)
            if session is not None:
                return _Stored(-rank[0], ResolvedSession(session_key, session))
        return None

    @staticmethod
    def _newest_unit(units: _Units) -> Tuple[datetime, str]:
        return max((unit.modified_at, unit.key) for unit in units)

    @classmethod
    def _newest_first(cls, units_of: Dict[str, _Units]) -> List[str]:
        return sorted(units_of, key=lambda key: cls._newest_unit(units_of[key]), reverse=True)

    @staticmethod
    def _newest_prompt_at(session: Session) -> datetime:
        return max(prompt.submitted_at for prompt in session.prompts)

    def _ranking_units(self, units: _Units) -> _Units:
        """The units that alone rank a session: all of them, unless the reader says fewer do (`RankingUnits`)."""
        return self._reader.units_for_ranking(units) if isinstance(self._reader, RankingUnits) else units

    def _whole(self, session_key: str, units: _Units, ranked: Optional[Session], skip_unreadable: bool) -> Optional[Session]:
        """`ranked`, when read from all `units` already (`_ranking_units` are all of them), else the session read from all `units`;
        None when that read fails and `skip_unreadable`."""
        if ranked is not None and len(self._ranking_units(units)) == len(units):
            return ranked
        try:
            return self._read_complete(session_key, units)
        except CommandError:
            if skip_unreadable:
                return None
            raise

    def _root_sessions_with_prompt(self, units_of: Dict[str, _Units], skip_unreadable: bool) -> Iterator[Tuple[str, Session]]:
        """The root sessions that hold a prompt and a project folder, each read from its ranking units one at a time. An unreadable
        session ends the lookup, unless `skip_unreadable`."""
        for session_key, units in units_of.items():
            try:
                session = self._read_complete(session_key, self._ranking_units(units))
            except CommandError:
                if skip_unreadable:
                    continue
                raise
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

    @staticmethod
    def _latest_archived(sessions: Dict[str, dict], matchers: List[Callable[[str], bool]]) -> Optional[_Archived]:
        """Of the archived root sessions recorded in the nearest folder of `matchers` that has any, the one with the newest
        prompt; None when no folder has one."""
        best: Optional[_Archived] = None
        for key, entry in sessions.items():
            if entry["orphan"] or "projectDir" not in entry:
                continue
            level = next((level for level, is_in_folder in enumerate(matchers) if is_in_folder(entry["projectDir"])), None)
            if level is None:
                continue
            prompt_at = entry.get("lastPromptAt")
            candidate = _Archived(level, prompt_at is not None, parse_timestamp(prompt_at or entry["lastActivityAt"]), key)
            if best is None or (-candidate.level, candidate.has_prompt, candidate.at, candidate.session_key) > (
                    -best.level, best.has_prompt, best.at, best.session_key):
                best = candidate
        return best
