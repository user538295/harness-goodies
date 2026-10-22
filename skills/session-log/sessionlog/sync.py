"""Sync use case (C1): archives the sessions of one harness whose sources changed since logging was turned on.

Cheap path first: `scan` gives every unit a fingerprint without opening it, and the reader names a unit's session once; the
name is saved before any session is read. A session none of whose units changed (and that is neither skipped nor holding an
unreadable unit) is not read, and a changed unit last modified before the on-time cannot hold activity since it. A session
whose id is not a safe id is never read: its units are unreadable with that reason (a reader checks it too, so a session built
without sync is safe as well). A candidate session is read whole from all its units, and when it has activity since the
on-time its log and report are rewritten whole through `Archive`. Progress is saved after each session (C6), so a killed
run loses at most the session in progress.

Partly gone: a rewrite never replaces the files of an archived session with a report built from fewer units than the last
write counted. Its entry changes only in `state` and `reason` (skipped, "source partly gone (N of M units)", N being the
units available); its files stay. Fewer units present than counted means units are gone, which is detected without reading
the session; a counted unit that is present but unreadable is detected after the read. The label is the plan's wording for
the two cases above. Skipped sessions are candidates on every sync, so the session is rewritten once its units are back;
in the first case this costs no read. The check compares unit counts, as C6 defines them: a deleted unit replaced by a new
one is not noticed. A scan that could not list a folder makes its units look gone and withholds the same way; the next
complete scan heals it (the label is transiently off, nothing is dropped). A skipped session whose sources are all gone is
not retried and stays listed: nothing can heal it and its files are stale.

Retiring: an unreadable unit that left a complete scan can never be retried, so it is recorded inactive and no longer
listed; with an incomplete scan (a folder that could not be listed) no unit is retired.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

from .archive_port import Archive
from .model import Session, is_safe_id
from .pricing import PricingRules
from .reader_port import UNSAFE_ID_REASON, SessionReader, SourceUnit, root_id_of
from .state_port import StateStore

RENDER_VERSION = 1
UNIT_READ = "read"
UNIT_INACTIVE = "inactive"
UNIT_UNREADABLE = "unreadable"
SESSION_WRITTEN = "written"
SESSION_SKIPPED = "skipped"
UNKNOWN_SESSION_REASON = "session cannot be determined"
PARTLY_GONE_REASON = "source partly gone (%d of %d units)"

_Units = Tuple[SourceUnit, ...]


@dataclass(frozen=True)
class SyncOutcome:
    """`unsaved_reports` holds the report text per session key whose files could not be written in this run."""

    unsaved_reports: Dict[str, str] = field(default_factory=dict)


class _Publication(NamedTuple):
    """`failure` is None when the files were written; `report_text` is None when rendering failed. `log_path` is set whenever
    `failure` is None."""

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
        """Does nothing while logging is off. A save of `.sync.json` that fails raises its OSError: `usage` ranks the sessions
        from that file, so a state that cannot be saved cannot serve it either. The old file is kept with no temp file, the
        files of the sessions written meanwhile stay, and the next sync redoes the work lost with the save; a session
        written whose entry was not saved gets its log path computed again then (a lost `.sync.json` can give a session a
        second log)."""
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
        # An unlistable folder makes live units look gone, so nothing is retired then.
        if not scan.unreadable:
            # Before the first save on purpose: retiring needs no read.
            self._retire_vanished_unreadable_units(state, scan.units)
        self._save(state)
        unsaved: Dict[str, str] = {}
        for session_key, units in candidates:
            report_text = self._archive_session(state, session_key, units, on_at)
            if report_text is not None:
                unsaved[session_key] = report_text
            self._save(state)
        return SyncOutcome(unsaved)

    def _learn_and_group(self, state: dict, scan_units: _Units) -> Dict[str, _Units]:
        """Units per session key, asking the reader only for units without a recorded key. A newly learned key is recorded
        in `state` at once, so a run killed before its first session finishes does not learn it again. A unit whose session
        cannot be learned, or whose session id is not a safe id (it would become a path), is recorded unreadable and left out."""
        grouped: Dict[str, List[SourceUnit]] = {}
        for unit in scan_units:
            known_key = (state["units"].get(unit.key) or {}).get("sessionKey")
            session_key = known_key or self._reader.session_of(unit)
            if session_key is None:
                state["units"][unit.key] = {"state": UNIT_UNREADABLE, "reason": UNKNOWN_SESSION_REASON}
            elif not is_safe_id(root_id_of(session_key)):
                self._set_unit(state, unit, session_key, UNIT_UNREADABLE, UNSAFE_ID_REASON)
            else:
                if known_key is None:
                    # No fingerprint: not examined yet. A unit that could not be learned before keeps its unreadable state, so
                    # it still triggers the read of its session; any other new unit is read like a new unit.
                    state["units"].setdefault(unit.key, {"state": UNIT_INACTIVE})["sessionKey"] = session_key
                grouped.setdefault(session_key, []).append(unit)
        return {session_key: tuple(units) for session_key, units in grouped.items()}

    @staticmethod
    def _retire_vanished_unreadable_units(state: dict, scan_units: _Units) -> None:
        """A unit recorded unreadable that is not in the scan any more is recorded inactive, keeping its session key. Units
        in any other state are left alone: a scan gap must not lose their fingerprints and keys."""
        scanned = {unit.key for unit in scan_units}
        for key, entry in state["units"].items():
            if entry["state"] == UNIT_UNREADABLE and key not in scanned:
                entry.pop("reason", None)
                entry["state"] = UNIT_INACTIVE

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
        """A fingerprint is kept only for units that need no retry; an unreadable unit is read again on the next sync. A unit
        entry without a fingerprint (just learned, or unreadable) counts as changed, so it is read like a new unit."""
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
        """Reads the session whole and archives it when it has activity since the on-time, or when its last rewrite was
        skipped (it is archived already, so a later on-time does not excuse the missing rewrite); returns the report
        text that could not be saved, if any. Fewer units present than the last write counted are gone: the rewrite is
        withheld without a read, and no unit is marked, so unreadable ones stay listed and retried."""
        entry = state["sessions"].get(session_key)
        if self._withhold_if_partly_gone(entry, len(units)):
            return None
        result = self._reader.read(session_key, units)
        failed = {source.path: source.reason for source in result.unreadable}
        session = result.session
        skipped = entry is not None and entry["state"] == SESSION_SKIPPED
        if session is None and failed:
            self._mark(state, session_key, units, UNIT_UNREADABLE, result.unreadable[0].reason, failed)
        elif session is None or (session.last_activity_at < on_at and not skipped):
            self._mark(state, session_key, units, UNIT_INACTIVE, failed=failed)
        else:
            return self._write_session(state, session_key, units, session, failed)
        return None

    @staticmethod
    def _skip_rewrite(entry: dict, reason: str, unit_count: Optional[int] = None) -> None:
        """The entry becomes skipped with `reason` and is retried on every sync; its files stay. A failed write passes the
        number of units it built from: the writer replaces the log and the report one by one, so either may already hold
        rows of the new read and `unitCount` must not drop below it (it never shrinks)."""
        entry.update(state=SESSION_SKIPPED, reason=reason)
        if unit_count is not None:
            entry["unitCount"] = max(entry["unitCount"], unit_count)

    def _withhold_if_partly_gone(self, entry: Optional[dict], available: int) -> bool:
        """Skips the rewrite of an archived session that has fewer units available than its last write counted; `available`
        is the number of units present before the read, or readable after it. Returns whether it did."""
        if entry is None or available >= entry["unitCount"]:
            return False
        self._skip_rewrite(entry, PARTLY_GONE_REASON % (available, entry["unitCount"]))
        return True

    def _publish(self, session: Session, known_log_path: Optional[str]) -> _Publication:
        """A failed write is named by its reason. The failed file is put before it unless it is the `logPath` of the session's
        existing entry (`known_log_path`): `status` shows that path with the reason (`CommandUseCases._unreadable`). A first
        write has no entry, so status shows the source file and the failed log is named as well."""
        log_path = report_text = None
        try:
            log_path = known_log_path or self._archive.new_log_path(session)
            log_text = self._archive.render_log(session)
            report_text = self._archive.render_report(self._pricing.build(session))
            outcome = self._archive.write(log_path, log_text, report_text)
        except Exception as error:
            return _Publication(log_path, report_text, _reason(error))
        if outcome.written:
            return _Publication(log_path, report_text, None)
        failure = outcome.reason if outcome.path == known_log_path else "%s: %s" % (outcome.path, outcome.reason)
        return _Publication(log_path, report_text, failure)

    def _already_written(self, state: dict, existing: Optional[dict], units: _Units, failed: Dict[str, str]) -> bool:
        """Whether the files already hold what a rewrite would publish: written by this render version from as many units,
        and every unit that read now still has the fingerprint of that write. A session read only to retry a unit that
        stays unreadable is therefore not written again."""
        readable = [unit for unit in units if unit.key not in failed]
        return (existing is not None and existing["state"] == SESSION_WRITTEN and existing["renderVersion"] == RENDER_VERSION
                and existing["unitCount"] == len(readable) and not any(self._changed(state, unit) for unit in readable))

    def _write_session(self, state: dict, session_key: str, units: _Units, session: Session,
                       failed: Dict[str, str]) -> Optional[str]:
        """Decides in this order: withhold the rewrite when the session is partly gone; skip it when the files already hold
        what it would publish; otherwise publish. A success records the entry from the `built_from` readable units. A failure
        of an archived session skips the rewrite (`unitCount` grows to `built_from`); a failure of a first write leaves the
        units unreadable with the reason. Returns the report text that could not be saved, if any."""
        existing = state["sessions"].get(session_key)
        built_from = sum(unit.key not in failed for unit in units)
        if self._withhold_if_partly_gone(existing, built_from):  # a counted unit is present but unreadable
            self._mark(state, session_key, units, UNIT_READ, failed=failed)
            return None
        if self._already_written(state, existing, units, failed):
            self._mark(state, session_key, units, UNIT_READ, failed=failed)
            return None
        log_path, report_text, failure = self._publish(session, existing["logPath"] if existing is not None else None)
        if failure is None:
            state["sessions"][session_key] = self._session_entry(session, log_path, built_from)
            self._mark(state, session_key, units, UNIT_READ, failed=failed)
            return None
        if existing is not None:
            self._skip_rewrite(existing, failure, built_from)
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
