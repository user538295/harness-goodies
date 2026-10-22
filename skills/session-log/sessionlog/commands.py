"""Command use cases (C1): `on`, `off`, `status` and `usage` return data; the presentation module renders it.

While logging is on, every command syncs first (`off` syncs once, then turns off). While off, no command syncs.
Failures a user must hear about are raised as `CommandError`; the entry prints them as `session-log: <msg>`.
"""
import functools
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from .archive_port import Archive
from .pricing import PriceBook
from .state_port import OnFlag, StateStore
from .sync import SESSION_SKIPPED, UNIT_UNREADABLE, SyncOutcome, SyncUseCase

LATEST = "latest"


class CommandError(Exception):
    """A runtime error with its facts; the entry prints it and exits 1."""


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


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class CommandUseCases:
    """`now` returns a timezone-aware datetime; `path_module` is `os.path` (tests pass `ntpath`).

    `sync` and `state` must share one `StateStore` instance: its `sync_reset` tells `status` that the sync rebuilt a corrupt file."""

    def __init__(self, sync: SyncUseCase, archive: Archive, state: StateStore, price_book: PriceBook,
                 now: Callable[[], datetime], log_root: str, path_module=os.path) -> None:
        self._sync = sync
        self._archive = archive
        self._state = state
        self._price_book = price_book
        self._now = now
        self._log_root = log_root
        self._path = path_module

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
            last_sync_at=_timestamp(last_sync_at) if last_sync_at else None,
            archived_session_count=len(state["sessions"]),
            unreadable=self._unreadable(state),
            price_file_problem=self._price_book.problem(),
            sync_state_reset=self._state.sync_reset)

    def usage(self, cwd: str, target: Optional[str] = None) -> UsageResult:
        """The report of the root session of the project of `cwd` with the newest prompt; no target and `latest` are the same."""
        if target not in (None, LATEST):
            raise CommandError("usage target %r is not supported" % target)
        outcome = self._sync_while_on()
        state = self._state.load_sync()
        session_key = self._latest_session_key(state["sessions"], cwd)
        report_text = outcome.unsaved_reports.get(session_key)
        if report_text is None:
            log_path = state["sessions"][session_key]["logPath"]
            report_text = self._archive.read_report(log_path)
            if report_text is None:
                raise CommandError("the saved report of session %s beside %s cannot be read" % (session_key, log_path))
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

    def _latest_session_key(self, sessions: Dict[str, dict], cwd: str) -> str:
        candidates = self._project_sessions(sessions, cwd)
        if not candidates:
            raise CommandError("no archived session for this project (%s)" % cwd)

        def rank(item: Tuple[str, dict]) -> Tuple[bool, datetime, str]:
            key, entry = item
            prompt_at = entry.get("lastPromptAt")  # a session without a prompt ranks below every session with one
            return (prompt_at is not None, _timestamp(prompt_at or entry["lastActivityAt"]), key)

        return max(candidates.items(), key=rank)[0]
