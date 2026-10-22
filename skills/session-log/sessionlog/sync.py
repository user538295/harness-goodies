"""Sync use case (C1): archives the sessions of one harness whose sources changed since logging was turned on.

Cheap path first: `scan` gives every unit a fingerprint without opening it. A session none of whose units changed (and that is
neither skipped nor holding an unreadable unit) is not read, and a changed unit last modified before the on-time cannot hold
activity since it. A candidate session is read whole from all its units, and when it has activity since the on-time its log and
report are rewritten whole through `Archive`. Progress is saved after each session (C6), so a killed run loses at most the
session in progress.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

from .archive_port import Archive
from .model import Session
from .pricing import PricingRules
from .reader_port import SessionReader, SourceUnit
from .state_port import StateStore

RENDER_VERSION = 1
UNIT_READ = "read"
UNIT_INACTIVE = "inactive"
UNIT_UNREADABLE = "unreadable"
SESSION_WRITTEN = "written"
SESSION_SKIPPED = "skipped"
UNKNOWN_SESSION_REASON = "session cannot be determined"

_Units = Tuple[SourceUnit, ...]


@dataclass(frozen=True)
class SyncOutcome:
    """`unsaved_reports` holds the report text per session key whose files could not be written in this run."""

    unsaved_reports: Dict[str, str] = field(default_factory=dict)


class _Publication(NamedTuple):
    """`failure` is None when the files were written; `report_text` is None when rendering failed."""

    log_path: Optional[str]
    report_text: Optional[str]
    failure: Optional[str]


def _reason(error: Exception) -> str:
    return str(error) or type(error).__name__


class SyncUseCase:
    """Syncs the store of the harness `reader` belongs to; `now` returns a timezone-aware datetime."""

    def __init__(self, reader: SessionReader, archive: Archive, state: StateStore, pricing: PricingRules,
                 now: Callable[[], datetime]) -> None:
        self._reader = reader
        self._archive = archive
        self._state = state
        self._pricing = pricing
        self._now = now

    def sync(self) -> SyncOutcome:
        """Does nothing while logging is off."""
        flag = self._state.read_on_flag()
        if flag is None:
            return SyncOutcome()
        on_at = flag.on_at
        state = self._state.load_sync()
        scan = self._reader.scan()
        candidates: List[Tuple[str, _Units]] = []
        for session_key, units in sorted(self._learn_and_group(state, scan.units).items()):  # sorted: a fixed order
            if self._must_read(state, session_key, units, on_at):
                candidates.append((session_key, units))
            else:
                self._mark_changed_inactive(state, session_key, units)
        self._save(state)
        unsaved: Dict[str, str] = {}
        for session_key, units in candidates:
            report_text = self._archive_session(state, session_key, units, on_at)
            if report_text is not None:
                unsaved[session_key] = report_text
            self._save(state)
        return SyncOutcome(unsaved)

    def _learn_and_group(self, state: dict, scan_units: _Units) -> Dict[str, _Units]:
        """Units per session key, asking the reader only for units without a recorded key; a unit whose session cannot
        be learned is recorded unreadable in `state` and left out."""
        grouped: Dict[str, List[SourceUnit]] = {}
        for unit in scan_units:
            known = state["units"].get(unit.key)
            session_key = (known or {}).get("sessionKey") or self._reader.session_of(unit)
            if session_key is None:
                state["units"][unit.key] = {"state": UNIT_UNREADABLE, "reason": UNKNOWN_SESSION_REASON}
            else:
                grouped.setdefault(session_key, []).append(unit)
        return {session_key: tuple(units) for session_key, units in grouped.items()}

    @staticmethod
    def _changed(state: dict, unit: SourceUnit) -> bool:
        known = state["units"].get(unit.key)
        return known is None or known.get("fingerprint") != unit.fingerprint

    @staticmethod
    def _unit_unreadable(state: dict, unit: SourceUnit) -> bool:
        known = state["units"].get(unit.key)
        return known is not None and known.get("state") == UNIT_UNREADABLE

    def _may_hold_new_activity(self, state: dict, unit: SourceUnit, on_at: datetime) -> bool:
        return self._unit_unreadable(state, unit) or (self._changed(state, unit) and unit.modified_at >= on_at)

    def _must_read(self, state: dict, session_key: str, units: _Units, on_at: datetime) -> bool:
        entry = state["sessions"].get(session_key)
        if entry is not None and entry["state"] == SESSION_SKIPPED:
            return True
        return any(self._may_hold_new_activity(state, unit, on_at) for unit in units)

    @staticmethod
    def _set_unit(state: dict, unit: SourceUnit, session_key: str, unit_state: str, reason: Optional[str] = None) -> None:
        """A fingerprint is kept only for units that need no retry; an unreadable unit is read again on the next sync."""
        entry = {"sessionKey": session_key, "state": unit_state}
        if unit_state != UNIT_UNREADABLE:
            entry["fingerprint"] = unit.fingerprint
        if reason:
            entry["reason"] = reason
        state["units"][unit.key] = entry

    def _mark_changed_inactive(self, state: dict, session_key: str, units: _Units) -> None:
        for unit in units:
            if self._changed(state, unit):
                self._set_unit(state, unit, session_key, UNIT_INACTIVE)

    def _mark(self, state: dict, session_key: str, units: _Units, unit_state: str, reason: Optional[str] = None,
              failed: Optional[Dict[str, str]] = None) -> None:
        """Records `unit_state` for every unit, except units named in `failed` (unit key -> reason), which are unreadable."""
        for unit in units:
            if failed and unit.key in failed:
                self._set_unit(state, unit, session_key, UNIT_UNREADABLE, failed[unit.key])
            else:
                self._set_unit(state, unit, session_key, unit_state, reason)

    def _archive_session(self, state: dict, session_key: str, units: _Units, on_at: datetime) -> Optional[str]:
        """Reads the session whole and archives it when it has activity since the on-time; returns the report text
        that could not be saved, if any."""
        result = self._reader.read(session_key, units)
        failed = {source.path: source.reason for source in result.unreadable}
        session = result.session
        if session is None and failed:
            self._mark(state, session_key, units, UNIT_UNREADABLE, result.unreadable[0].reason, failed)
        elif session is None or session.last_activity_at < on_at:
            self._mark(state, session_key, units, UNIT_INACTIVE, failed=failed)
        else:
            return self._write_session(state, session_key, units, session, failed)
        return None

    def _publish(self, session: Session, known_log_path: Optional[str]) -> _Publication:
        log_path = report_text = None
        try:
            log_path = known_log_path or self._archive.new_log_path(session)
            log_text = self._archive.render_log(session)
            report_text = self._archive.render_report(self._pricing.build(session))
            outcome = self._archive.write(log_path, log_text, report_text)
        except Exception as error:
            return _Publication(log_path, report_text, _reason(error))
        return _Publication(log_path, report_text, None if outcome.written else outcome.reason)

    def _write_session(self, state: dict, session_key: str, units: _Units, session: Session,
                       failed: Dict[str, str]) -> Optional[str]:
        existing = state["sessions"].get(session_key)
        log_path, report_text, failure = self._publish(session, existing["logPath"] if existing is not None else None)
        if failure is None:
            built_from = sum(unit.key not in failed for unit in units)
            state["sessions"][session_key] = self._session_entry(session, log_path, built_from)
            self._mark(state, session_key, units, UNIT_READ, failed=failed)
            return None
        if existing is not None:
            existing.update(state=SESSION_SKIPPED, reason=failure)
            self._mark(state, session_key, units, UNIT_READ, failed=failed)
        else:
            self._mark(state, session_key, units, UNIT_UNREADABLE, failure, failed)
        return report_text

    @staticmethod
    def _session_entry(session: Session, log_path: str, unit_count: int) -> dict:
        entry = {"logPath": log_path, "state": SESSION_WRITTEN}
        if session.project_dir is not None:
            entry["projectDir"] = session.project_dir
        if session.prompts:
            entry["lastPromptAt"] = max(prompt.submitted_at for prompt in session.prompts).isoformat()
        entry.update(lastActivityAt=session.last_activity_at.isoformat(), orphan=session.orphan_of_parent_id is not None,
                     renderVersion=RENDER_VERSION, unitCount=unit_count)
        return entry

    def _save(self, state: dict) -> None:
        state["lastSyncAt"] = self._now().isoformat()
        self._state.save_sync(state)
