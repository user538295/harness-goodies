"""Command use cases (C1): `on`, `off`, `status` and `usage` return data; the presentation module renders it.

While logging is on, every command syncs first (`off` syncs once, then turns off). While off, no command syncs.
The sync of `status` and `usage` is best effort: a log root that cannot be written (read-only, no space) makes it fail, and
the command then goes on with what is archived and a live report, naming the failure in `warnings`; the sync of `on` and `off`
is part of the command and its failure is an error.
`usage` prints the saved report of an archived session; a session that was never archived gets the same report computed live
from the store (read, priced and rendered as a sync would) and never saved, and so does one whose saved report is gone while
its source is not (a sync normally recreates it first).
Failures a user must hear about are raised as `CommandError`; the entry prints them as `session-log: <msg>`. A failed file
operation names the operation, the path and the reason of the OS.
"""
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from .archive_port import Archive
from .command_error import CommandError
from .leftover_port import Leftover, LeftoverScanner
from .model import Session
from .pricing import PricingRules
from .probe_port import ProbeResult, PythonProbe
from .reader_port import root_id_of
from .state_port import OnFlag, StateStore, parse_timestamp
from .sync import SESSION_SKIPPED, SESSION_WITHHELD, UNIT_UNREADABLE, SyncOutcome, SyncUseCase
from .usage_target import ResolvedSession, UsageTargetResolver

FLAG_FILE = ".log_enabled"
FILES_MISSING_REASON = "the log or the report is missing from disk"


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
    probe: Optional[ProbeResult] = None


@dataclass(frozen=True)
class UsageResult:
    """`report_text` is exactly the report text of the session, saved or computed live."""

    report_text: str


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

    def status(self) -> StatusResult:
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
            probe=self._python_probe.check())

    def usage_latest(self, cwd: str) -> UsageResult:
        """`usage` and `usage --latest`: the report of the root session of the project of `cwd` with the newest prompt, among
        the archived sessions and the never-archived ones of the store (the nearest folder with any session wins)."""
        return self._usage(lambda state: self._targets.latest(state, cwd))

    def usage_of(self, target: str) -> UsageResult:
        """`usage <id>`: the report of the session `target` names, matched exactly, else as a unique prefix, over archived and
        store sessions together. The word `latest` is such an id, never the flag."""
        return self._usage(lambda state: self._targets.by_id(state, target))

    def _usage(self, resolve: Callable[[dict], ResolvedSession]) -> UsageResult:
        outcome = self._sync_while_on()
        state = self._state.load_sync()
        resolved = resolve(state)
        report_text = outcome.unsaved_reports.get(resolved.session_key)
        if report_text is None:
            if resolved.session is None:
                report_text = self._saved_report(state, resolved.session_key, state["sessions"][resolved.session_key])
            else:
                report_text = self._live_report(resolved.session)
        return UsageResult(report_text)

    def _saved_report(self, state: dict, session_key: str, entry: dict) -> str:
        """The saved report; when its file is gone, the live report of the session if its source is still there."""
        report_text = self._archive.read_report(entry["logPath"])
        if report_text is not None:
            return report_text
        session = self._targets.read_stored(state, session_key)
        if session is None:
            raise CommandError("the saved report of session %s beside %s cannot be read, and its source is gone"
                               % (root_id_of(session_key), entry["logPath"]))
        return self._live_report(session)

    def _live_report(self, session: Session) -> str:
        """The report of a session never archived, computed as a sync would and written nowhere."""
        return self._archive.render_report(self._pricing.build(session))

    def _recorded_since_on(self, flag: Optional[OnFlag]) -> Optional[bool]:
        if self._hooks_recorded_since is None or flag is None:
            return None
        return self._hooks_recorded_since(flag.on_at)

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
