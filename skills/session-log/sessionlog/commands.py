"""Command use cases (C1): `on`, `off`, `status`, `usage` and `list` return data; the presentation module renders it.

While logging is on, every command syncs first (`off` syncs once, then turns off). While off, no command syncs.
The sync of `status` and `usage` is best effort: a log root that cannot be written (read-only, no space) makes it fail, and
the command then goes on with what is archived and a live report, naming the failure in `warnings`; the sync of `on` and `off`
is part of the command and its failure is an error.
`usage` prints the saved report of an archived session; a session that was never archived gets the same report computed live
from the store (read, priced and rendered as a sync would) and never saved, and so does one whose saved report is gone while
its source is not (a sync normally recreates it first).
A withheld session's saved report may be behind its log; `usage` prints it unchanged and names that in a warning.
`usage` as data takes the numbers of the report's TOTAL block: from the report built now for a live one, else parsed from the
report text by the archive, which owns its format. Its prompt and sub-agent rows come from the session read now: a live
report's own session, else the stored session of an archived one, read only when data is asked for. When that session cannot
be read (its source is gone, or unreadable, which a warning names) the data holds no rows; the command still succeeds.
`list` names the root sessions of a project folder and the folders below it: an archived one from `.sync.json` and its log
(read by the archive), a never-archived one from the store, and an archived one whose log cannot be read from the store too;
one that neither can give is left out and named in `warnings`.
Failures a user must hear about are raised as `CommandError`; the entry prints them as `session-log: <msg>`. A failed file
operation names the operation, the path and the reason of the OS.
"""
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from .archive_port import Archive, ReportTotals
from .command_error import CommandError
from .leftover_port import Leftover, LeftoverScanner
from .model import Prompt, Session
from .pricing import PricingRules, PromptRow, SubAgentRow, UsageReport
from .probe_port import ProbeResult, PythonProbe
from .reader_port import harness_of, root_id_of
from .state_port import OnFlag, StateStore, parse_timestamp
from .sync import SESSION_SKIPPED, SESSION_WITHHELD, UNIT_UNREADABLE, SyncOutcome, SyncUseCase
from .usage_target import ResolvedSession, UsageTargetResolver

FLAG_FILE = ".log_enabled"
FILES_MISSING_REASON = "the log or the report is missing from disk"
WITHHELD_REPORT_WARNING = "the saved report of session %s is withheld and may be behind its log (%s); `status` lists it"
LEFT_OUT_WARNING = "session %s is left out: its log %s cannot be read, and %s"
SOURCE_GONE_REASON = "its source is gone"
UNPARSABLE_REPORT = "the report of session %s holds no TOTAL block that can be read as data"
ROWS_UNREADABLE_WARNING = "%s, so its data holds no prompt or sub-agent rows"


def _io_message(operation: str, error: OSError, default_path: Optional[str] = None) -> str:
    """`cannot <operation>: <path>: <reason>` from an OS error: its path (else `default_path`) and its reason, not the raw
    `[Errno N]` text."""
    path = error.filename or default_path
    return "cannot %s: %s%s" % (operation, "%s: " % path if path else "", error.strerror or error)


def _io_failure(operation: str, error: OSError, default_path: Optional[str] = None) -> CommandError:
    return CommandError(_io_message(operation, error, default_path))


@dataclass(frozen=True)
class UnreadableItem:
    path: str
    reason: str


@dataclass(frozen=True)
class OnResult:
    already_on: bool
    on_at: datetime
    log_root: str
    leftovers: Tuple[Leftover, ...] = ()
    probe: Optional[ProbeResult] = None


@dataclass(frozen=True)
class OffResult:
    already_off: bool
    log_root: str


@dataclass(frozen=True)
class LatestSession:
    """The archived session `status` names: its id, its Markdown log and the path of its own source (None when it is gone)."""

    session_id: str
    log_path: str
    source: Optional[str] = None


@dataclass(frozen=True)
class StatusResult:
    on: bool
    log_root: str
    archived_session_count: int
    unreadable: Tuple[UnreadableItem, ...]
    sync_state_reset: bool
    on_at: Optional[datetime] = None
    on_at_from_file_time: bool = False
    last_sync_at: Optional[datetime] = None
    cursor_recorded_since_on: Optional[bool] = None
    price_file_problem: Optional[str] = None
    leftovers: Tuple[Leftover, ...] = ()
    latest_session: Optional[LatestSession] = None
    probe: Optional[ProbeResult] = None


@dataclass(frozen=True)
class UsageRows:
    """The rows of the report of the session read now: each prompt beside its report row (row i is prompt i), then the
    sub-agent rows in report order."""

    prompts: Tuple[Tuple[Prompt, PromptRow], ...]
    sub_agents: Tuple[SubAgentRow, ...]

    @classmethod
    def of(cls, session: Session, report: UsageReport) -> "UsageRows":
        """The rows of `report`, which must be built from `session`."""
        return cls(tuple(zip(session.prompts, report.prompts, strict=True)), report.sub_agents)


@dataclass(frozen=True)
class UsageData:
    """The numbers of a session's report, with where the session is: its own source (None when the store holds none) and its
    archived log (None when it is not archived or the log is gone); `rows` None when the session cannot be read now."""

    harness: str
    session_id: str
    project_dir: Optional[str]
    source: Optional[str]
    log_path: Optional[str]
    totals: ReportTotals
    rows: Optional[UsageRows] = None


@dataclass(frozen=True)
class UsageResult:
    """`report_text` is exactly the report text of the session, saved or computed live; `data` is set when it was asked for."""

    report_text: str
    data: Optional[UsageData] = None


@dataclass(frozen=True)
class ListedSession:
    """One root session of `list`: `source` as for `UsageData`, `log_path` set when the session is archived and its log is on
    disk, `prompts` the full prompt texts in order."""

    harness: str
    session_id: str
    project_dir: str
    started_at: datetime
    last_activity_at: datetime
    source: Optional[str]
    log_path: Optional[str]
    prompts: Tuple[str, ...]


@dataclass(frozen=True)
class ListResult:
    """The root sessions of the project of `cwd`, oldest start first (then by id)."""

    cwd: str
    sessions: Tuple[ListedSession, ...]


class CommandUseCases:
    """`now` returns a timezone-aware datetime.

    `sync` and `state` must share one `StateStore` instance: its `sync_reset` tells `status` that the sync rebuilt a corrupt file.
    `targets` must read the same store as `sync`. `pricing` must be the one `sync` uses, so a live report equals the saved one.
    `leftover_scan` finds the old-release leftovers of the invoked harness; `on` and `status` report them, nothing deletes them.
    `python_probe` checks `python3` on Windows (None elsewhere); `on` and `status` report its result.
    `hooks_recorded_since` is set only for a harness that logs from hook calls: given the on-time it tells whether any hook call was
    recorded since (None when that cannot be told), and `status` reports its answer. While logging is off nothing is synced, so
    `status` then lists only what the sync state holds."""

    def __init__(self, sync: SyncUseCase, targets: UsageTargetResolver, archive: Archive, state: StateStore,
                 pricing: PricingRules, now: Callable[[], datetime], log_root: str, leftover_scan: LeftoverScanner,
                 python_probe: PythonProbe, hooks_recorded_since: Optional[Callable[[datetime], Optional[bool]]] = None) -> None:
        self._sync = sync
        self._targets = targets
        self._archive = archive
        self._state = state
        self._pricing = pricing
        self._now = now
        self._log_root = log_root
        self._leftover_scan = leftover_scan
        self._python_probe = python_probe
        self._hooks_recorded_since = hooks_recorded_since
        self.warnings: List[str] = []  # failures a command went on past (see the module text); the entry prints them

    def on(self) -> OnResult:
        flag = self._state.read_on_flag()
        already_on = flag is not None
        created_here = False
        if flag is None:
            fresh = OnFlag(self._now())
            try:
                self._state.write_on_flag(fresh)
            except OSError as error:
                raise _io_failure("turn logging on", error, self._flag_path()) from error
            flag = self._state.read_on_flag() or fresh  # a concurrent `on` keeps the time it wrote
            created_here = flag.on_at == fresh.on_at
        try:
            self._sync.sync()
        except BaseException as error:  # an `on` that fails or is killed must not leave logging on; a flag that was already there stays
            if created_here:
                self._state.delete_on_flag()
            if isinstance(error, OSError):
                raise _io_failure("archive the sessions", error, self._log_root) from error
            raise
        return OnResult(already_on, flag.on_at, self._log_root, self._leftover_scan.scan(), self._python_probe.check())

    def off(self) -> OffResult:
        if self._state.read_on_flag() is None:
            return OffResult(True, self._log_root)
        sync_failure = None
        try:
            self._sync.sync()
        except OSError as error:
            sync_failure = _io_failure("archive the sessions before turning logging off", error, self._log_root)
        finally:
            self._turn_off()  # a failing sync must never keep logging on
        if sync_failure is not None:
            raise sync_failure
        return OffResult(False, self._log_root)

    def _turn_off(self) -> None:
        try:
            self._state.delete_on_flag()
        except OSError as error:
            raise _io_failure("turn logging off (it stays on)", error, self._flag_path()) from error

    def _flag_path(self) -> str:
        return os.path.join(self._log_root, FLAG_FILE)

    def status(self, cwd: str) -> StatusResult:
        """Also names the archived root session of the project of `cwd` with the newest prompt (while logging is on, the sync that
        comes first archives the session that runs `status`) and its own source."""
        outcome = self._sync_while_on()
        flag = self._state.read_on_flag()
        state = self._state.load_sync()
        last_sync_at = state.get("lastSyncAt")
        return StatusResult(
            on=flag is not None,
            on_at=flag.on_at if flag else None,
            on_at_from_file_time=bool(flag and flag.from_file_time),
            log_root=self._log_root,
            last_sync_at=parse_timestamp(last_sync_at) if last_sync_at else None,
            archived_session_count=len(state["sessions"]),
            unreadable=self._unreadable(state, outcome),
            cursor_recorded_since_on=self._recorded_since_on(flag),
            price_file_problem=self._pricing.problem(),
            sync_state_reset=self._state.sync_reset,
            leftovers=self._leftover_scan.scan(),
            latest_session=self._latest_session(state, cwd),
            probe=self._python_probe.check())

    def usage_latest(self, cwd: str, with_data: bool = False) -> UsageResult:
        """`usage` and `usage --latest`: the report of the root session of the project of `cwd` with the newest prompt, among
        the archived sessions and the never-archived ones of the store (the nearest folder with any session wins)."""
        return self._usage(lambda state: self._targets.latest(state, cwd), with_data)

    def usage_of(self, target: str, with_data: bool = False) -> UsageResult:
        """`usage <id>`: the report of the session `target` names, matched exactly, else as a unique prefix, over archived and
        store sessions together. The word `latest` is such an id, never the flag."""
        return self._usage(lambda state: self._targets.by_id(state, target), with_data)

    def list_sessions(self, cwd: str) -> ListResult:
        """`list`: every root session of the project of `cwd` (see the module text)."""
        self._sync_while_on()
        state = self._state.load_sync()
        found = self._targets.project_sessions(state, cwd)
        self.warnings.extend(found.problems)
        listed = [self._listed_from_store(key, session, found.sources.get(key), None) for key, session in found.stored.items()]
        for session_key in found.archived:
            session = self._archived_listing(state, session_key, found.sources.get(session_key))
            if session is not None:
                listed.append(session)
        return ListResult(cwd, tuple(sorted(listed, key=lambda session: (session.started_at, session.session_id))))

    def _archived_listing(self, state: dict, session_key: str, source: Optional[str]) -> Optional[ListedSession]:
        """From the log; when it cannot be read, from the store; None, named in a warning, when neither can give the session."""
        entry = state["sessions"][session_key]
        log_path = entry["logPath"]
        log = self._archive.read_log(log_path)
        if log is not None:
            return ListedSession(harness_of(session_key), root_id_of(session_key), entry["projectDir"], log.started_at,
                                 parse_timestamp(entry["lastActivityAt"]), source, log_path, log.prompts)
        try:
            session = self._targets.read_stored(state, session_key)
            reason = SOURCE_GONE_REASON
        except CommandError as error:
            session, reason = None, str(error)
        if session is None:
            self.warnings.append(LEFT_OUT_WARNING % (root_id_of(session_key), log_path, reason))
            return None
        return self._listed_from_store(session_key, session, source, log_path if self._archive.files_present(log_path).log else None)

    @staticmethod
    def _listed_from_store(session_key: str, session: Session, source: Optional[str], log_path: Optional[str]) -> ListedSession:
        return ListedSession(harness_of(session_key), root_id_of(session_key), session.project_dir, session.started_at,
                             session.last_activity_at, source, log_path, tuple(prompt.text for prompt in session.prompts))

    def _usage(self, resolve: Callable[[dict], ResolvedSession], with_data: bool) -> UsageResult:
        outcome = self._sync_while_on()
        state = self._state.load_sync()
        resolved = resolve(state)
        report: Optional[UsageReport] = None
        rows: Optional[UsageRows] = None
        report_text = outcome.unsaved_reports.get(resolved.session_key)
        if report_text is None:
            if resolved.session is None:
                report_text, report, rows = self._saved_report(state, resolved.session_key, state["sessions"][resolved.session_key])
            else:
                report_text, report, rows = self._live_report(resolved.session)
        return UsageResult(report_text, self._usage_data(state, resolved, report_text, report, rows) if with_data else None)

    def _usage_data(self, state: dict, resolved: ResolvedSession, report_text: str, report: Optional[UsageReport],
                    rows: Optional[UsageRows]) -> UsageData:
        """The numbers of the live `report`, else those parsed from the report text; the rows of the live report, else of the
        session read now; where the session is, from the store and the archive."""
        session_key = resolved.session_key
        totals = ReportTotals.of(report.total) if report is not None else self._archive.parse_report(report_text)
        if totals is None:
            raise CommandError(UNPARSABLE_REPORT % root_id_of(session_key))
        entry = state["sessions"].get(session_key)
        project_dir = resolved.session.project_dir if resolved.session is not None else (entry or {}).get("projectDir")
        log_path = entry["logPath"] if entry is not None and self._archive.files_present(entry["logPath"]).log else None
        return UsageData(harness_of(session_key), root_id_of(session_key), project_dir, self._targets.source(state, session_key),
                         log_path, totals, rows if rows is not None else self._rows_read_now(state, resolved))

    def _rows_read_now(self, state: dict, resolved: ResolvedSession) -> Optional[UsageRows]:
        """The rows of the resolved session, else of the archived session read now from the store; None when its source is gone,
        or unreadable (named in a warning)."""
        session = resolved.session
        if session is None:
            try:
                session = self._targets.read_stored(state, resolved.session_key)
            except CommandError as error:
                self.warnings.append(ROWS_UNREADABLE_WARNING % error)
                return None
        return None if session is None else UsageRows.of(session, self._pricing.build(session))

    def _saved_report(self, state: dict, session_key: str, entry: dict) -> Tuple[str, Optional[UsageReport], Optional[UsageRows]]:
        """The saved report (with no report data or rows); when its file is gone, the live report of the session if its source is
        still there. A withheld session's saved report may be behind its log (a log that gained prompts keeps the report that holds
        the lost sub-agent rows), so `usage` says so in a warning."""
        report_text = self._archive.read_report(entry["logPath"])
        if report_text is not None:
            if entry["state"] == SESSION_WITHHELD:
                self.warnings.append(WITHHELD_REPORT_WARNING % (root_id_of(session_key), entry.get("reason", "")))
            return report_text, None, None
        session = self._targets.read_stored(state, session_key)
        if session is None:
            raise CommandError("the saved report of session %s beside %s cannot be read, and its source is gone"
                               % (root_id_of(session_key), entry["logPath"]))
        return self._live_report(session)

    def _live_report(self, session: Session) -> Tuple[str, UsageReport, UsageRows]:
        """The report of a session never archived, computed as a sync would and written nowhere, with its data and rows."""
        report = self._pricing.build(session)
        return self._archive.render_report(report), report, UsageRows.of(session, report)

    def _recorded_since_on(self, flag: Optional[OnFlag]) -> Optional[bool]:
        if self._hooks_recorded_since is None or flag is None:
            return None
        return self._hooks_recorded_since(flag.on_at)

    def _latest_session(self, state: dict, cwd: str) -> Optional[LatestSession]:
        session_key = self._targets.latest_archived(state, cwd)
        if session_key is None:
            return None
        return LatestSession(root_id_of(session_key), state["sessions"][session_key]["logPath"],
                             self._targets.main_source(state, session_key))

    def _sync_while_on(self) -> SyncOutcome:
        """The sync of `status` and `usage`: best effort. An OS error (the log root is not writable) is kept as a warning, and
        what is already archived is served."""
        if self._state.read_on_flag() is None:
            return SyncOutcome()
        try:
            return self._sync.sync()
        except OSError as error:
            self.warnings.append(_io_message("update the archive (showing what is already archived)", error, self._log_root))
            return SyncOutcome()

    def _unreadable(self, state: dict, outcome: SyncOutcome) -> Tuple[UnreadableItem, ...]:
        """What the state holds (a unit that cannot be read, a rewrite that was skipped or withheld, an archived session whose
        files are gone from disk, which a sync recreates when its source is there), then what the sync's scan could not use,
        each path once (the state's reason wins)."""
        items: List[UnreadableItem] = []
        for key, unit in sorted(state["units"].items()):
            if unit["state"] == UNIT_UNREADABLE:
                items.append(UnreadableItem(key, unit.get("reason", "")))
        for _, entry in sorted(state["sessions"].items()):
            if entry["state"] in (SESSION_SKIPPED, SESSION_WITHHELD):
                items.append(UnreadableItem(entry["logPath"], entry.get("reason", "")))
            elif not self._archive.files_present(entry["logPath"]).complete:
                items.append(UnreadableItem(entry["logPath"], FILES_MISSING_REASON))
        listed = {item.path for item in items}
        items.extend(UnreadableItem(source.path, source.reason) for source in outcome.scan_problems if source.path not in listed)
        return tuple(items)
