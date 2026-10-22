"""Command use cases (C1): `on`, `off`, `status` and `usage` return data; the presentation module renders it.

While logging is on, every command syncs first (`off` syncs once, then turns off). While off, no command syncs.
Failures a user must hear about are raised as `CommandError`; the entry prints them as `session-log: <msg>`.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from .archive_port import Archive
from .command_error import CommandError
from .pricing import PriceBook
from .reader_port import root_id_of
from .state_port import OnFlag, StateStore, parse_timestamp
from .sync import SESSION_SKIPPED, UNIT_UNREADABLE, SyncOutcome, SyncUseCase
from .usage_target import UsageTargetResolver


@dataclass(frozen=True)
class Leftover:
    """An old-release leftover: reported, never deleted. `fix` is one line."""

    path: str
    fix: str


@dataclass(frozen=True)
class ProbeResult:
    """Windows only: whether `python3` runs Python 3.14 or newer; `fix` is set when it does not."""

    ok: bool
    fix: Optional[str] = None


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
    """`report_text` is exactly the report text of the session."""

    report_text: str


class CommandUseCases:
    """`now` returns a timezone-aware datetime.

    `sync` and `state` must share one `StateStore` instance: its `sync_reset` tells `status` that the sync rebuilt a corrupt file.
    `targets` must read the same store as `sync`."""

    def __init__(self, sync: SyncUseCase, targets: UsageTargetResolver, archive: Archive, state: StateStore,
                 price_book: PriceBook, now: Callable[[], datetime], log_root: str) -> None:
        self._sync = sync
        self._targets = targets
        self._archive = archive
        self._state = state
        self._price_book = price_book
        self._now = now
        self._log_root = log_root

    def on(self) -> OnResult:
        flag = self._state.read_on_flag()
        already_on = flag is not None
        if flag is None:
            fresh = OnFlag(self._now())
            self._state.write_on_flag(fresh)
            flag = self._state.read_on_flag() or fresh  # a concurrent `on` keeps the time it wrote
        self._sync.sync()
        return OnResult(already_on, flag.on_at, self._log_root)

    def off(self) -> OffResult:
        if self._state.read_on_flag() is None:
            return OffResult(True, self._log_root)
        try:
            self._sync.sync()
        finally:
            self._state.delete_on_flag()  # a failing sync must never keep logging on
        return OffResult(False, self._log_root)

    def status(self) -> StatusResult:
        self._sync_while_on()
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
            unreadable=self._unreadable(state),
            price_file_problem=self._price_book.problem(),
            sync_state_reset=self._state.sync_reset)

    def usage_latest(self, cwd: str) -> UsageResult:
        """`usage` and `usage --latest`: the report of the root session of the project of `cwd` with the newest prompt."""
        return self._usage(lambda state: self._targets.latest(state, cwd))

    def usage_of(self, target: str) -> UsageResult:
        """`usage <id>`: the report of the session `target` names, matched exactly, else as a unique prefix, over archived and
        store sessions together. The word `latest` is such an id, never the flag."""
        return self._usage(lambda state: self._targets.by_id(state, target))

    def _usage(self, resolve: Callable[[dict], str]) -> UsageResult:
        outcome = self._sync_while_on()
        state = self._state.load_sync()
        session_key = resolve(state)
        report_text = outcome.unsaved_reports.get(session_key)
        if report_text is None:
            entry = state["sessions"].get(session_key)
            if entry is None:
                raise CommandError("session %s has no saved report: it was never archived" % root_id_of(session_key))
            report_text = self._archive.read_report(entry["logPath"])
            if report_text is None:
                raise CommandError("the saved report of session %s beside %s cannot be read"
                                   % (root_id_of(session_key), entry["logPath"]))
        return UsageResult(report_text)

    def _sync_while_on(self) -> SyncOutcome:
        return self._sync.sync() if self._state.read_on_flag() is not None else SyncOutcome()

    @staticmethod
    def _unreadable(state: dict) -> Tuple[UnreadableItem, ...]:
        items: List[UnreadableItem] = []
        for key, unit in sorted(state["units"].items()):
            if unit["state"] == UNIT_UNREADABLE:
                items.append(UnreadableItem(key, unit.get("reason", "")))
        for _, entry in sorted(state["sessions"].items()):
            if entry["state"] == SESSION_SKIPPED:
                items.append(UnreadableItem(entry["logPath"], entry.get("reason", "")))
        return tuple(items)
