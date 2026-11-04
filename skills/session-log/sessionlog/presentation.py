"""Presentation (C1): renders the command use-case results as stdout text. Pure: no I/O, no clock, no environment (the local
time of a `list` line comes from the conversion the caller passes).

Each set field appears with its fixed wording, each unset field does not. Every returned text ends with a newline,
except a `UsageResult`, whose report text is returned unchanged. The `*_json` renderers print data for programs: one JSON
value, times as UTC ISO 8601 in whole seconds, an absent value as null.
"""
import json
from datetime import datetime, timezone
from typing import Callable, List, Optional, Sequence

from .commands import LatestSession, ListedSession, ListResult, OffResult, OnResult, StatusResult, UnreadableItem, UsageResult
from .leftover_port import Leftover
from .probe_port import MINIMUM_TEXT, ProbeResult

TIME_FORMAT = "%Y-%m-%d %H:%M:%S %z"
LEFTOVERS_HEADING = "Old-release leftovers (not deleted):"
PROBE_FAILED = "python3 does not run Python %s or newer" % MINIMUM_TEXT
UNREADABLE_HEADING = "Unreadable or skipped items:"
NO_PATH = "-"
CENTS_PER_DOLLAR = 100
JSON_INDENT = 2


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


def _latest_session_lines(session: Optional[LatestSession]) -> List[str]:
    if session is None:
        return []
    source = [] if session.source is None else ["Session source: %s" % session.source]
    return ["Latest archived session: %s" % session.session_id] + source + ["Prompt log: %s" % session.log_path]


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
    lines += _latest_session_lines(result.latest_session)
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


def _json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=JSON_INDENT) + "\n"


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def render_usage_json(result: UsageResult) -> str:
    """The numbers of the report's TOTAL block; `result` must carry `data`."""
    data = result.data
    totals = data.totals
    return _json_text({
        "harness": data.harness, "id": data.session_id, "projectDir": data.project_dir, "path": data.source, "log": data.log_path,
        "agentTimeSeconds": totals.working_seconds,
        "tokens": {"input": totals.input_tokens, "output": totals.output_tokens, "reasoning": totals.reasoning_tokens,
                   "cacheRead": totals.cache_read_tokens, "cacheWrite": totals.cache_write_tokens, "total": totals.total_tokens},
        "costUsd": totals.price_cents / CENTS_PER_DOLLAR, "costComplete": totals.price_complete})


def _listed_json(session: ListedSession) -> dict:
    return {"harness": session.harness, "id": session.session_id, "projectDir": session.project_dir,
            "startedAt": _utc_text(session.started_at), "lastActivityAt": _utc_text(session.last_activity_at),
            "path": session.source, "log": session.log_path, "prompts": list(session.prompts)}


def render_list_json(result: ListResult) -> str:
    return _json_text([_listed_json(session) for session in result.sessions])


def _listed_line(session: ListedSession, to_local: Callable[[datetime], datetime]) -> str:
    count = len(session.prompts)
    return "%s  %s  %d prompt%s  %s" % (_time(to_local(session.started_at)), session.session_id, count, "" if count == 1 else "s",
                                        session.log_path or session.source or NO_PATH)


def render_list(result: ListResult, to_local: Callable[[datetime], datetime]) -> str:
    """One line per session: its start in local time, id, prompt count, and its log, else its source."""
    if not result.sessions:
        return _text(["No session for this project (%s)." % result.cwd])
    return _text([_listed_line(session, to_local) for session in result.sessions])
