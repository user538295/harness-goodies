"""Sync use case (C1): archives the sessions of one harness whose sources changed since logging was turned on.

Cheap path first: `scan` gives every unit a fingerprint without opening it, and the reader names a unit's session once; the
name is saved before any session is read. A session none of whose units changed (and that is neither skipped, owed a
rewrite, nor holding an unreadable unit) is not read, and a changed unit last modified before the on-time
cannot hold activity since it. A session whose id is not a safe id is never read: its units are unreadable with that reason
(a reader checks it too, so a session built without sync is safe as well). A candidate session is read whole from all its
units, and when it has activity since the on-time its log and report are rewritten whole through `Archive`. Progress is
saved after each session (C6) while the state is small; a large one is saved in batches (`_checkpoint_due`), so the first sync
costs time linear in the number of sessions, and a killed run loses at most the session in progress plus one batch.
One session that raises is recorded unreadable with the error and the sync goes on with the others.

Never less than the archive: an archived session's entry records what its files hold, `promptCount` (in the log and the
report) and `subAgentCount` (rows, nested ones too, in the report only). A rewrite is judged after the read, from what the
read session holds, so a deleted unit that was replaced, or a unit that kept its name and lost content, is noticed too:
- fewer prompts than recorded (a reverted OpenCode session, a rewritten Codex rollout, a cut transcript): nothing is written;
  the entry becomes withheld, "source holds fewer prompts than archived (N of M)".
- as many or more prompts, fewer sub-agent rows (a sub-agent file Claude cleaned, a deleted Codex thread or OpenCode child, a unit
  present but unreadable): the log, which holds prompts only, is rewritten whole, so new activity is still archived; the
  report, the only holder of the sub-agent rows, stays as it is (`usage` prints it with a warning). The entry becomes withheld,
  "source partly gone (N of M units)", N being the units that read (when none is missing: "source holds fewer sub-agents than archived (N of M)").
- otherwise both files are rewritten and the counts replaced by those of the read.
Tokens and prices are not compared: a reader fix may legitimately change them. A written session whose source no unit of
changed since and that has no fewer readable units than the last write counted cannot have been shrunk by its source, so a
rewrite owed to it (a render version, a price file that works again, deleted files) is published as read, and a reader fix
that drops rows applies. Only files on disk are protected; a missing file beside a present one does not rescue a read with
fewer prompts (nothing is written until the source grows). A withheld session is read again when a unit changed (whatever its
time: a restored sub-agent file heals it) or is unreadable, not for a render version; a read that finds the same units writes
nothing. Entries written before the counts existed have none (0): nothing is protected for them. A session whose sources are
all gone has no units and is never a candidate. A scan that could not list a folder makes its units look gone; the next
complete scan heals it.

Vanished units: a unit entry whose source left a complete scan is dropped, so `.sync.json` and the cost of saving it do not grow
with how long logging has been on; this includes an unreadable unit, which can never be retried and so is no longer listed.
With an incomplete scan (a folder that could not be listed) no unit is dropped; a finding that hides no session (`hides_sessions` False) does not make a scan incomplete. Session entries are never dropped.

Owed rewrites: a written session is rewritten once, whatever its units did and even when its last activity is older than a
later on-time, when (1) its entry has a lower `RENDER_VERSION` than the installed copy (each session is rewritten once per
raise, and a stopped sync resumes with the rest; an entry with a higher version, written by a newer installed copy on the same
log root, is never rewritten for its version), (2) it was written while `prices.json` was unusable (`priceFileProblem`) and the
file works now, or (3) its log or report is missing on disk (one stat each per archived session and sync); the source is then
read again and both files are recreated, or, when no source is left, nothing can be done and `status` lists the session. A
session read for another reason is not rewritten when its files already hold the same or a newer version, built from as many
units, with no unit changed. Sessions that wait only for such a repair come after every other candidate, so a backlog never
delays new turns. A session that reads as no session and has nothing unreadable (its file was emptied) keeps its entry, so it
costs one read per sync while a rewrite is owed; new activity rewrites it. A skipped session (its last write failed) is a
candidate on every sync.

Budget: a sync given `budget_seconds` starts no new session once that much time has passed on `now` since the sync began;
the session in progress finishes, and what was learned and written is already saved. The next sync continues.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

from .archive_port import Archive, ArchiveFiles
from .model import Session, SubAgent, is_safe_id
from .pricing import PricingRules
from .reader_port import UNSAFE_ID_REASON, SessionReader, SourceUnit, UnreadableSource, root_id_of
from .state_port import StateStore, recorded_session_key

# Raised by every change that alters log or report text for the same source (readers, model, PricingRules, writer,
# prices.json); `test_render_version` pins the sha256 of prices.json to it.
RENDER_VERSION = 5
HOOK_SYNC_BUDGET_SECONDS = 20.0  # below the 30 s hook timeouts; the hook entry passes it to `sync`
UNIT_READ = "read"
UNIT_INACTIVE = "inactive"
UNIT_UNREADABLE = "unreadable"
SESSION_WRITTEN = "written"
SESSION_SKIPPED = "skipped"
SESSION_WITHHELD = "withheld"
UNKNOWN_SESSION_REASON = "session cannot be determined"
PARTLY_GONE_REASON = "source partly gone (%d of %d units)"
FEWER_SUB_AGENTS_REASON = "source holds fewer sub-agents than archived (%d of %d)"
FEWER_PROMPTS_REASON = "source holds fewer prompts than archived (%d of %d)"
CHECKPOINT_FREE_ENTRIES = 500  # up to this many `.sync.json` entries, every session is saved as soon as it is done
CHECKPOINT_FRACTION = 50  # beyond that, a save is due once the sessions done since the last one reach 1/50 of the entries

_Units = Tuple[SourceUnit, ...]


class _Counts(NamedTuple):
    """What the files of an archived session must never lose: its prompts and its sub-agent rows."""

    prompts: int
    sub_agents: int


class _Verdict(NamedTuple):
    """How a read session may be published over an archived one: `publish_log` False withholds everything; `keep_report` publishes
    the log and leaves the saved report as it is. `reason` explains a withhold or a kept report."""

    publish_log: bool
    keep_report: bool
    reason: Optional[str] = None


_PUBLISH_ALL = _Verdict(True, False)


def _sub_agent_rows(agents: Sequence[SubAgent]) -> int:
    return sum(1 + _sub_agent_rows(agent.sub_agents) for agent in agents)


def _counts_of(session: Session) -> _Counts:
    return _Counts(len(session.prompts), _sub_agent_rows(session.sub_agents))


@dataclass(frozen=True)
class SyncOutcome:
    """`unsaved_reports` holds the report text per session key whose files could not be written in this run; `scan_problems` the sources
    the scan could not use (not units), which `status` names."""

    unsaved_reports: Dict[str, str] = field(default_factory=dict)
    scan_problems: Tuple[UnreadableSource, ...] = ()


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

    def sync(self, budget_seconds: Optional[float] = None) -> SyncOutcome:
        """Does nothing while logging is off. With `budget_seconds`, no new session is started once that much time has
        passed since the sync began (a hook sync); the next sync continues with the rest. A save of `.sync.json` that fails
        raises its OSError: `usage` ranks the sessions from that file, so a state that cannot be saved cannot serve it
        either. The old file is kept with no temp file, the files of the sessions written meanwhile stay, and the next sync
        redoes the work lost with the save; a session written whose entry was not saved gets its log path computed again
        then (a lost `.sync.json` can give a session a second log, a killed run in a large store several: the path is
        computed from the session alone, so it is the same file unless the session's first record or cwd now reads differently)."""
        started_at = self._now()
        flag = self._state.read_on_flag()
        if flag is None:
            return SyncOutcome()
        on_at = flag.on_at
        state = self._state.load_sync()
        scan = self._reader.scan()
        candidates: List[Tuple[str, _Units]] = []
        repair_only: List[Tuple[str, _Units]] = []  # waiting only for a repair rewrite: after the rest
        for session_key, units in sorted(self._learn_and_group(state, scan.units).items()):  # sorted: a fixed order
            entry = state["sessions"].get(session_key)
            owed = self._is_rewrite_owed(entry)
            if not self._must_read(state, entry, owed, units, on_at):
                self._mark_changed_inactive(state, session_key, units)
            elif self._waits_only_for_repair(state, entry, owed, units, on_at):
                repair_only.append((session_key, units))
            else:
                candidates.append((session_key, units))
        # An unlistable folder makes live units look gone, so no unit is dropped then; a finding that hides no session (Cursor's
        # `.records/.invalid`) leaves the unit list complete.
        if not any(source.hides_sessions for source in scan.unreadable):
            # Before the first save on purpose: dropping needs no read.
            self._drop_vanished_units(state, scan.units)
        self._save(state)
        unsaved: Dict[str, str] = {}
        unsaved_sessions = 0
        for session_key, units in candidates + repair_only:
            if self._budget_spent(started_at, budget_seconds):
                break
            report_text = self._archive_isolated(state, session_key, units, on_at)
            if report_text is not None:
                unsaved[session_key] = report_text
            unsaved_sessions += 1
            if self._checkpoint_due(state, unsaved_sessions):
                self._save(state)
                unsaved_sessions = 0
        if unsaved_sessions:
            self._save(state)
        return SyncOutcome(unsaved, scan.unreadable)

    def _budget_spent(self, started_at: datetime, budget_seconds: Optional[float]) -> bool:
        return budget_seconds is not None and (self._now() - started_at).total_seconds() >= budget_seconds

    @staticmethod
    def _checkpoint_due(state: dict, unsaved_sessions: int) -> bool:
        """A save writes the whole state, so a save per session makes the first sync of a large store quadratic (5000 sessions
        took minutes). Up to `CHECKPOINT_FREE_ENTRIES` entries every session is saved at once; beyond that a save is due once
        the sessions done since the last one reach 1/`CHECKPOINT_FRACTION` of the entries. That bounds the work a killed run
        loses, and the time all saves take together stays a fixed multiple of the state's size."""
        entries = len(state["units"]) + len(state["sessions"])
        return entries <= CHECKPOINT_FREE_ENTRIES or unsaved_sessions * CHECKPOINT_FRACTION >= entries

    def _archive_isolated(self, state: dict, session_key: str, units: _Units, on_at: datetime) -> Optional[str]:
        """`_archive_session`, except that a session that raises (a reader defect, a time that cannot be compared) must not stop the
        rest: its units are recorded unreadable with the error, so `status` names them and every sync retries them. Only an
        `Exception` is caught: an interrupt still ends the run."""
        try:
            return self._archive_session(state, session_key, units, on_at)
        except Exception as error:
            self._mark(state, session_key, units, UNIT_UNREADABLE, failed=dict.fromkeys((unit.key for unit in units), _reason(error)))
            return None

    def _learn_and_group(self, state: dict, scan_units: _Units) -> Dict[str, _Units]:
        """Units per session key, asking the reader only for units without a recorded key. A newly learned key is recorded
        in `state` at once, so a run killed before its first session finishes does not learn it again. A unit whose session
        cannot be learned, or whose session id is not a safe id (it would become a path), is recorded unreadable and left out."""
        grouped: Dict[str, List[SourceUnit]] = {}
        for unit in scan_units:
            known_key = recorded_session_key(state, unit.key)
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
    def _drop_vanished_units(state: dict, scan_units: _Units) -> None:
        """A unit entry whose source is not in the scan any more is dropped, whatever its state: an unreadable unit that left
        the scan can never be retried, so it is no longer listed, and an entry kept for every source that ever existed would
        make `.sync.json`, and the cost of every save, grow with how long logging has been on. Session entries are never dropped."""
        scanned = {unit.key for unit in scan_units}
        for key in [key for key in state["units"] if key not in scanned]:
            del state["units"][key]

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

    def _owes_price_rewrite(self, entry: dict) -> bool:
        """Written while `prices.json` was unusable, and it is usable now: the report holds unknown prices that can be known."""
        return bool(entry.get("priceFileProblem")) and self._pricing.problem() is None

    def _is_rewrite_owed(self, entry: Optional[dict]) -> bool:
        """An archived session is owed a rewrite whatever its units did and whatever the on-time is now when its last rewrite
        was skipped, its log or report is missing on disk, or (written ones only: a withheld one holds what its source lost,
        and no version can bring that back) its files were written by an older render version or without a usable price file."""
        if entry is None:
            return False
        if entry["state"] == SESSION_SKIPPED or not self._archive.files_present(entry["logPath"]).complete:
            return True
        return entry["state"] == SESSION_WRITTEN and (entry["renderVersion"] < RENDER_VERSION or self._owes_price_rewrite(entry))

    def _waits_only_for_repair(self, state: dict, entry: Optional[dict], owed: bool, units: _Units, on_at: datetime) -> bool:
        """A session owed a rewrite that is no retry of a failed one, and none of whose units may hold new activity."""
        return (owed and entry["state"] != SESSION_SKIPPED
                and not any(self._may_hold_new_activity(state, unit, on_at) for unit in units))

    def _must_read(self, state: dict, entry: Optional[dict], owed: bool, units: _Units, on_at: datetime) -> bool:
        if owed:
            return True
        if entry is not None and entry["state"] == SESSION_WITHHELD and any(self._changed(state, unit) for unit in units):
            return True  # a source that changed in any way may be whole again
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
        """Reads the session whole and archives it when it has activity since the on-time, or when a rewrite is owed to
        it (it is archived already, so a later on-time does not excuse the missing rewrite); returns the report
        text that could not be saved, if any."""
        entry = state["sessions"].get(session_key)
        result = self._reader.read(session_key, units)
        failed = {source.path: source.reason for source in result.unreadable}
        session = result.session
        rewrite_owed = self._is_rewrite_owed(entry)
        if session is None and failed:
            self._mark(state, session_key, units, UNIT_UNREADABLE, result.unreadable[0].reason, failed)
        elif session is None or (session.last_activity_at < on_at and not rewrite_owed):
            self._mark(state, session_key, units, UNIT_INACTIVE, failed=failed)
        else:
            return self._write_session(state, session_key, units, session, failed)
        return None

    @staticmethod
    def _skip_rewrite(entry: dict, reason: str, counts: _Counts) -> None:
        """The entry becomes skipped with `reason` and is retried on every sync; its files stay. The writer replaces the log and
        the report one by one, so either may already hold rows of the new read: the recorded counts never drop below it."""
        entry.update(state=SESSION_SKIPPED, reason=reason,
                     promptCount=max(entry.get("promptCount", 0), counts.prompts),
                     subAgentCount=max(entry.get("subAgentCount", 0), counts.sub_agents))

    def _publish(self, session: Session, known_log_path: Optional[str], kept_report: Optional[str]) -> _Publication:
        """A failed write is named by its reason. The failed file is put before it unless it is the `logPath` of the session's
        existing entry (`known_log_path`): `status` shows that path with the reason (`CommandUseCases._unreadable`). A first
        write has no entry, so status shows the source file and the failed log is named as well. `kept_report` is the saved
        report that stays; it is written back as it is."""
        log_path = report_text = None
        try:
            log_path = known_log_path or self._archive.new_log_path(session)
            log_text = self._archive.render_log(session)
            report_text = kept_report if kept_report is not None else self._archive.render_report(self._pricing.build(session))
            outcome = self._archive.write(log_path, log_text, report_text)
        except Exception as error:
            return _Publication(log_path, report_text, _reason(error))
        if outcome.written:
            return _Publication(log_path, report_text, None)
        failure = outcome.reason if outcome.path == known_log_path else "%s: %s" % (outcome.path, outcome.reason)
        return _Publication(log_path, report_text, failure)

    def _already_written(self, state: dict, existing: Optional[dict], units: _Units, failed: Dict[str, str],
                         files: Optional[ArchiveFiles]) -> bool:
        """Whether the files already hold what a rewrite would publish: both are on disk and every unit that read now still has
        the fingerprint of the last evaluation. A written entry also needs this render version or a newer one, built from as
        many units, and no price rewrite owed; a withheld one has nothing more to publish. A session read only to retry a unit
        that stays unreadable is therefore not written again, and an older copy does not undo a newer one's write."""
        if existing is None or files is None or not files.complete or existing["state"] not in (SESSION_WRITTEN, SESSION_WITHHELD):
            return False
        readable = [unit for unit in units if unit.key not in failed]
        if any(self._changed(state, unit) for unit in readable):
            return False
        if existing["state"] == SESSION_WITHHELD:
            return True
        return (existing["renderVersion"] >= RENDER_VERSION and existing["unitCount"] == len(readable)
                and not self._owes_price_rewrite(existing))

    def _source_unchanged(self, state: dict, existing: dict, units: _Units, failed: Dict[str, str], built_from: int) -> bool:
        """A written session whose readable units all have the fingerprint of the last evaluation and are not fewer than the last
        write counted: its source cannot have lost anything since, so a smaller read is the reader's doing."""
        return (existing["state"] == SESSION_WRITTEN and built_from >= existing["unitCount"]
                and not any(self._changed(state, unit) for unit in units if unit.key not in failed))

    def _judge(self, state: dict, existing: Optional[dict], files: Optional[ArchiveFiles], counts: _Counts, units: _Units,
               failed: Dict[str, str], built_from: int) -> _Verdict:
        """Whether the read may replace the archived files (see the module text): never when it has fewer prompts than the files
        hold; with fewer sub-agent rows only the log, whose prompts are all kept. A file missing on disk protects nothing,
        and a first write or a rewrite from an unchanged source (`_source_unchanged`) is published as read."""
        if existing is None or self._source_unchanged(state, existing, units, failed, built_from):
            return _PUBLISH_ALL
        prompt_floor, sub_agent_floor = existing.get("promptCount", 0), existing.get("subAgentCount", 0)
        if (files.log or files.report) and counts.prompts < prompt_floor:
            return _Verdict(False, False, FEWER_PROMPTS_REASON % (counts.prompts, prompt_floor))
        if files.report and counts.sub_agents < sub_agent_floor:
            reason = (PARTLY_GONE_REASON % (built_from, existing["unitCount"]) if built_from < existing["unitCount"]
                      else FEWER_SUB_AGENTS_REASON % (counts.sub_agents, sub_agent_floor))
            return _Verdict(True, True, reason)
        return _PUBLISH_ALL

    def _write_session(self, state: dict, session_key: str, units: _Units, session: Session,
                       failed: Dict[str, str]) -> Optional[str]:
        """Decides in this order: skip the rewrite when the files already hold what it would publish; withhold it, or publish the
        log alone, when the read holds less than the files (`_judge`); otherwise publish. A success records the entry from the
        `built_from` readable units. A failure of an archived session skips the rewrite (the counts grow to the read's); a
        failure of a first write leaves the units unreadable with the reason. Returns the report text that could not be saved,
        if any."""
        existing = state["sessions"].get(session_key)
        built_from = sum(unit.key not in failed for unit in units)
        files = self._archive.files_present(existing["logPath"]) if existing is not None else None
        if self._already_written(state, existing, units, failed, files):
            self._mark(state, session_key, units, UNIT_READ, failed=failed)
            return None
        counts = _counts_of(session)
        verdict = self._judge(state, existing, files, counts, units, failed, built_from)
        if not verdict.publish_log:
            existing.update(state=SESSION_WITHHELD, reason=verdict.reason)
            self._mark(state, session_key, units, UNIT_READ, failed=failed)
            return None
        kept_report = self._archive.read_report(existing["logPath"]) if verdict.keep_report else None
        if verdict.keep_report and kept_report is None:  # an unreadable report holds nothing to keep
            verdict = _PUBLISH_ALL
        log_path, report_text, failure = self._publish(session, existing["logPath"] if existing is not None else None, kept_report)
        if failure is None:
            state["sessions"][session_key] = self._entry_after_write(existing, session, log_path, built_from, counts, verdict)
            self._mark(state, session_key, units, UNIT_READ, failed=failed)
            return None
        if existing is not None:
            self._skip_rewrite(existing, failure, counts)
            self._mark(state, session_key, units, UNIT_READ, failed=failed)
        else:
            self._mark(state, session_key, units, UNIT_UNREADABLE, failure, failed)
        return report_text

    def _entry_after_write(self, existing: Optional[dict], session: Session, log_path: str, built_from: int, counts: _Counts,
                           verdict: _Verdict) -> dict:
        """The entry of a session whose files were just written. When the saved report was kept, the entry is withheld and
        still describes what that report holds (version, units, sub-agent rows, price state); the log holds the new prompts."""
        entry = self._session_entry(session, log_path, built_from, counts)
        if verdict.keep_report:
            entry.update(state=SESSION_WITHHELD, reason=verdict.reason, renderVersion=existing["renderVersion"],
                         unitCount=existing["unitCount"], subAgentCount=existing["subAgentCount"])
            entry.pop("priceFileProblem", None)
            if existing.get("priceFileProblem"):
                entry["priceFileProblem"] = True
        return entry

    def _session_entry(self, session: Session, log_path: str, unit_count: int, counts: _Counts) -> dict:
        entry = {"logPath": log_path, "state": SESSION_WRITTEN}
        if session.project_dir is not None:
            entry["projectDir"] = session.project_dir
        if session.prompts:
            entry["lastPromptAt"] = max(prompt.submitted_at for prompt in session.prompts).isoformat()
        entry.update(lastActivityAt=session.last_activity_at.isoformat(), orphan=session.orphan_of_parent_id is not None,
                     renderVersion=RENDER_VERSION, unitCount=unit_count, promptCount=counts.prompts,
                     subAgentCount=counts.sub_agents)
        if self._pricing.problem() is not None:
            entry["priceFileProblem"] = True  # every price in this report is unknown; rewritten once the file works
        return entry

    def _save(self, state: dict) -> None:
        state["lastSyncAt"] = self._now().isoformat()
        self._state.save_sync(state)
