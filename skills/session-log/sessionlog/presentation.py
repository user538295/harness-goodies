"""Presentation (C1): renders the command use-case results as stdout text. Pure: no I/O, no clock, no environment.

Each set field appears with its fixed wording, each unset field does not. Every returned text ends with a newline,
except a `UsageResult`, whose report text is returned unchanged.
"""
from datetime import datetime
from typing import List, Optional, Sequence

from .commands import OffResult, OnResult, StatusResult, UnreadableItem, UsageResult
from .leftover_port import Leftover
from .probe_port import MINIMUM_TEXT, ProbeResult

TIME_FORMAT = "%Y-%m-%d %H:%M:%S %z"
LEFTOVERS_HEADING = "Old-release leftovers (not deleted):"
PROBE_FAILED = "python3 does not run Python %s or newer" % MINIMUM_TEXT
UNREADABLE_HEADING = "Unreadable or skipped items:"


def _time(value: datetime) -> str:
    return value.strftime(TIME_FORMAT)


def _text(lines: Sequence[str]) -> str:
    return "".join(line + "\n" for line in lines)


def _leftover_lines(leftovers: Sequence[Leftover]) -> List[str]:
    if not leftovers:
        return []
    return [LEFTOVERS_HEADING] + ["  %s: %s" % (leftover.path, leftover.fix) for leftover in leftovers]


def _probe_lines(probe: Optional[ProbeResult]) -> List[str]:
    if probe is None or probe.ok:
        return []
    if probe.fix is None:
        return [PROBE_FAILED]
    return ["%s; fix: %s" % (PROBE_FAILED, probe.fix)]


def _unreadable_lines(items: Sequence[UnreadableItem]) -> List[str]:
    if not items:
        return []
    return [UNREADABLE_HEADING] + [("  %s: %s" % (item.path, item.reason)) if item.reason else ("  %s" % item.path) for item in items]


def render_on(result: OnResult) -> str:
    if result.already_on:
        headline = "Session logging is already on, since %s." % _time(result.on_at)
    else:
        headline = "Session logging is on since %s." % _time(result.on_at)
    lines = [headline, "Logs are saved under %s." % result.log_root]
    return _text(lines + _probe_lines(result.probe) + _leftover_lines(result.leftovers))


def render_off(result: OffResult) -> str:
    headline = "Session logging is already off." if result.already_off else "Session logging is off."
    return _text([headline, "Logs under %s are kept." % result.log_root])


def render_status(result: StatusResult) -> str:
    if not result.on:
        lines = ["Session logging is off."]
    elif result.on_at is None:
        lines = ["Session logging is on."]
    elif result.on_at_from_file_time:
        lines = ["Session logging is on since %s (from the file time: .log_enabled could not be parsed)." % _time(result.on_at)]
    else:
        lines = ["Session logging is on since %s." % _time(result.on_at)]
    lines.append("Log root: %s" % result.log_root)
    if result.last_sync_at is not None:
        lines.append("Last sync: %s" % _time(result.last_sync_at))
    lines.append("Archived sessions: %d" % result.archived_session_count)
    lines += _unreadable_lines(result.unreadable)
    if result.on and result.cursor_recorded_since_on is False:
        lines.append("Cursor has recorded no hook events since logging was turned on.")
    if result.price_file_problem is not None:
        lines.append("Price file problem: %s" % result.price_file_problem)
    if result.sync_state_reset:
        lines.append("The sync state was empty or corrupt and has been rebuilt.")
    return _text(lines + _probe_lines(result.probe) + _leftover_lines(result.leftovers))


def render_usage(result: UsageResult) -> str:
    return result.report_text
