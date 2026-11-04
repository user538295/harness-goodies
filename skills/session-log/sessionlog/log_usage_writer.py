"""C3 adapter: renders the log and the usage report, writes them atomically and parses them back; owns every archive path rule
and the format of both files. New folders are 0700 and every written file 0600; a slug folder, log or report that is a symbolic
link or junction is skipped, never written through (`safe_paths`)."""
import hashlib
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional, Tuple

from .archive_port import ArchivedLog, ArchiveFiles, ReportTotals, WriteOutcome
from .model import Prompt, Session
from .pricing import PARTIAL, PRICED, UNKNOWN, Price, UsageReport
from .safe_paths import FOLDER_LINK_REASON, LINK_REASON, is_link, make_real_folders
from .temp_files import STALE_TEMP_TAIL, publish, remove_stale_temps, temp_name

CENTS_PER_DOLLAR = 100
SLUG_MAX_LENGTH = 64
SLUG_HASH_LENGTH = 12
ROOT_SLUG = "root"
UNKNOWN_SLUG = "unknown"
SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600
SESSION_PREFIX = "session_"
LOG_SUFFIX = ".md"
REPORT_SUFFIX = ".usage.txt"
FILE_TIME_FORMAT = "%Y-%m-%d_%H-%M-%S"
ENTRY_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
CLOCK_FORMAT = "%H:%M:%S"
NO_VALUE = "-"
API_EQUIVALENT = " (API-equivalent)"
INCOMPLETE_MARK = "\u2265 "
LOG_TITLE = "# Session log"
START_PREFIX = "- start: "
TOTAL_PREFIX = "TOTAL ("
TOTAL_BLOCK_MIN_LINES = 3  # heading, working time and token line; a label line may sit between the last two
_OTHER_CHARS = re.compile(r"[^A-Za-z0-9._-]")  # `.` is in the allowed set, so it stays in the folder name
_LINE_BREAKS = re.compile(r"[\r\n\t]+")
_STALE_TEMP = re.compile(r"\.%s.*(?:%s|%s)" % (re.escape(SESSION_PREFIX), re.escape(LOG_SUFFIX), re.escape(REPORT_SUFFIX)) + STALE_TEMP_TAIL)
_ENTRY_TIME_TEXT = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC(?:[+-]\d+(?::\d{2})?)?"  # what `_entry_time` writes
_ENTRY_TIME = re.compile(r"(?P<clock>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC(?:(?P<sign>[+-])(?P<hours>\d+)(?::(?P<minutes>\d{2}))?)?")
_PROMPT_HEADING = re.compile(r"\n\n## %s\n" % _ENTRY_TIME_TEXT)
_RESPONSE_HEADING = re.compile(r"\n\n### %s response\n" % _ENTRY_TIME_TEXT)
_TOTAL_HEADING = re.compile(r"TOTAL \(\d+ requests, \d+ sub-agents\)")
_TOTAL_WORKING_TIME = re.compile(r"working time: (?P<hours>\d+):(?P<minutes>\d{2}):(?P<seconds>\d{2})")
_TOTAL_ESTIMATE = re.compile(  # what `_estimate` writes for the total: a price is always known there, complete or not
    r"est\. used token: input: (?P<input>\d+), output: (?P<output>\d+)(?:, reasoning: (?P<reasoning>\d+))?, "
    r"cache_create: (?P<cache_write>\d+), cache_read: (?P<cache_read>\d+), total_tokens: (?P<total>\d+), "
    r"price: (?P<incomplete>%s)?(?P<sign>-?)\$(?P<dollars>\d+)\.(?P<cents>\d{2})(?(incomplete) \(incomplete\))(?:%s)?, model: "
    % (INCOMPLETE_MARK, re.escape(API_EQUIVALENT)))


def _offset_label(moment: datetime) -> str:
    seconds = int(moment.utcoffset().total_seconds())
    minutes = abs(seconds) // SECONDS_PER_MINUTE
    if minutes == 0:
        return "UTC"
    hours, rest = divmod(minutes, SECONDS_PER_MINUTE)
    return "UTC%s%d%s" % ("-" if seconds < 0 else "+", hours, ":%02d" % rest if rest else "")


def _parse_entry_time(text: str) -> Optional[datetime]:
    """The time `_entry_time` wrote as `text`, in the offset it shows; None for any other text."""
    match = _ENTRY_TIME.fullmatch(text)
    if match is None:
        return None
    offset = timedelta(hours=int(match["hours"] or 0), minutes=int(match["minutes"] or 0))
    try:
        return datetime.strptime(match["clock"], ENTRY_TIME_FORMAT).replace(tzinfo=timezone(-offset if match["sign"] == "-" else offset))
    except ValueError:
        return None


def _whole_seconds(span: timedelta) -> int:
    return max(0, int(span.total_seconds()))


def _clock_text(seconds: int) -> str:
    hours, rest = divmod(seconds, SECONDS_PER_HOUR)
    return "%02d:%02d:%02d" % (hours, rest // SECONDS_PER_MINUTE, rest % SECONDS_PER_MINUTE)


def _duration(span: timedelta) -> str:
    return _clock_text(_whole_seconds(span))


def _text(text: str) -> str:
    """LF line ends and no lone surrogates, so a render equals the bytes `write` saves."""
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8", errors="replace").decode("utf-8")


def _single_line(text: str) -> str:
    return _LINE_BREAKS.sub(" ", text)


def _reason(error: BaseException) -> str:
    return str(error) or type(error).__name__


def _dollars(cents: int) -> str:
    return "%s$%d.%02d" % ("-" if cents < 0 else "", *divmod(abs(cents), CENTS_PER_DOLLAR))


def _price(price: Price, api_equivalent: bool) -> str:
    if price.status == PRICED:
        text = _dollars(price.cents)
    elif price.status == PARTIAL:
        text = "%s%s (incomplete)" % (INCOMPLETE_MARK, _dollars(price.cents))
    elif price.status == UNKNOWN:
        text = UNKNOWN
    else:
        raise ValueError("unexpected price status: %r" % (price.status,))
    return text + (API_EQUIVALENT if api_equivalent else "")


def _estimate(usage, price_text: str) -> str:
    """`reasoning` sits right after `output` as the old OMP and OpenCode reports had it; only a harness that records it has the field."""
    reasoning = "" if usage.reasoning_tokens is None else ", reasoning: %d" % usage.reasoning_tokens
    return ("est. used token: input: %d, output: %d%s, cache_create: %d, cache_read: %d, total_tokens: %d, "
            "price: %s, model: %s, effort: %s") % (
        usage.input_tokens, usage.output_tokens, reasoning, usage.cache_write_tokens, usage.cache_read_tokens,
        usage.total_tokens or 0, price_text, _single_line(usage.model_names), _single_line(usage.effort or NO_VALUE))


def _last_recorded(prompt: Prompt, field: str) -> Optional[str]:
    """The model or effort the prompt ended on: that of its last request that names one."""
    return next((value for value in (getattr(request, field) for request in reversed(prompt.requests)) if value), None)


def _switch_line(previous: Tuple[Optional[str], Optional[str]], model: Optional[str], effort: Optional[str]) -> Optional[str]:
    """The old Claude log's `switched:` line: each half needs both sides known, so a first or unknown value is no transition."""
    parts = ["%s %s \u2192 %s" % (name, _single_line(before), _single_line(after))
             for name, before, after in (("model", previous[0], model), ("effort", previous[1], effort))
             if before and after and before != after]
    return "switched: " + ", ".join(parts) if parts else None


class LogUsageWriter:
    """`to_local` converts an instant to local time with the offset valid at that instant (production: `d.astimezone()`),
    `path_module` decides path rules (tests pass `ntpath`), `clock` gives epoch seconds, `pid` the process id of temp names."""

    def __init__(self, log_root: str, to_local: Callable[[datetime], datetime], path_module=os.path,
                 clock: Callable[[], float] = time.time, pid: Callable[[], int] = os.getpid):
        self._log_root = log_root
        self._to_local = to_local
        self._path = path_module
        self._clock = clock
        self._pid = pid

    def _slug(self, cwd: Optional[str]) -> str:
        if not cwd:
            return UNKNOWN_SLUG
        base = self._path.basename(cwd)
        digest = hashlib.sha256(self._path.normcase(cwd).encode("utf-8", errors="surrogatepass")).hexdigest()
        return "%s-%s" % (_OTHER_CHARS.sub("-", base)[:SLUG_MAX_LENGTH] or ROOT_SLUG, digest[:SLUG_HASH_LENGTH])

    def new_log_path(self, session: Session) -> str:
        stamp = session.started_at.astimezone(timezone.utc).strftime(FILE_TIME_FORMAT)
        return self._path.join(self._log_root, self._slug(session.project_dir),
                               "%s%s_%s%s" % (SESSION_PREFIX, stamp, session.session_id, LOG_SUFFIX))

    def _local(self, moment: datetime) -> datetime:
        """A time the local zone cannot hold (outside the platform's range) is shown in UTC, never an error."""
        try:
            local = self._to_local(moment)
        except (OverflowError, OSError, ValueError):
            return moment.astimezone(timezone.utc)
        if local.utcoffset() is None:
            raise ValueError("to_local must return an aware datetime")
        return local

    def _entry_time(self, moment: datetime) -> str:
        local = self._local(moment)
        return "%s %s" % (local.strftime(ENTRY_TIME_FORMAT), _offset_label(local))

    def render_log(self, session: Session) -> str:
        lines: List[str] = [LOG_TITLE, "- harness: %s" % session.harness.value,
                            "- session: %s" % session.session_id,
                            "- project: %s" % _single_line(session.project_dir or NO_VALUE),
                            START_PREFIX + self._entry_time(session.started_at)]
        if session.orphan_of_parent_id:
            lines.append("- sub-agent of %s (parent not found)" % _single_line(session.orphan_of_parent_id))
        previous: Tuple[Optional[str], Optional[str]] = (None, None)
        for prompt in session.prompts:
            model, effort = _last_recorded(prompt, "model_name"), _last_recorded(prompt, "effort")
            lines += ["", "## %s" % self._entry_time(prompt.submitted_at), prompt.text]
            if prompt.reply_text is not None:
                lines += ["", "### %s response" % self._entry_time(prompt.reply_at or prompt.submitted_at), prompt.reply_text]
                switched = _switch_line(previous, model, effort)
                if switched:
                    lines += ["", switched]
            previous = (model or previous[0], effort or previous[1])
        return _text("\n".join(lines) + "\n")

    @staticmethod
    def parse_log(log_text: str) -> Optional[ArchivedLog]:
        """The start and the prompt texts of a text `render_log` wrote; None when it is not in that format. A prompt is the text
        between its `## <time>` heading and the `### <time> response` heading of its reply (else the next prompt's heading), so a
        prompt or reply that itself holds a blank line followed by such a heading line is split there."""
        if not log_text.endswith("\n"):
            return None
        header, *entries = _PROMPT_HEADING.split(log_text[:-1])
        lines = header.split("\n")
        start = next((line[len(START_PREFIX):] for line in lines if line.startswith(START_PREFIX)), None)
        started_at = None if start is None else _parse_entry_time(start)
        if lines[0] != LOG_TITLE or started_at is None:
            return None
        return ArchivedLog(started_at, tuple(_RESPONSE_HEADING.split(entry, maxsplit=1)[0] for entry in entries))

    def read_log(self, log_path: str) -> Optional[ArchivedLog]:
        try:
            with open(log_path, "rb") as handle:
                log_text = handle.read().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        return self.parse_log(log_text)

    def render_report(self, report: UsageReport) -> str:
        api = report.total.api_equivalent
        lines = ["session: %s" % _single_line(report.transcript_path or report.session_id), "harness: %s" % report.harness.value,
                 "project: %s" % _single_line(report.project_dir or NO_VALUE)]
        header_offset = None
        if report.prompts:
            header_offset = _offset_label(self._local(report.prompts[0].at))
            lines.append("times: %s" % header_offset)
        lines.append("")
        for number, row in enumerate(report.prompts, 1):
            local = self._local(row.at)
            offset = _offset_label(local)
            lines += ['%d. %s%s (working time %s) "%s"' % (
                number, local.strftime(CLOCK_FORMAT), " " + offset if offset != header_offset else "",
                _duration(row.working_time), row.prompt_head), _estimate(row, _price(row.price, api))]
        if report.sub_agents:
            lines.append("")
        for agent in report.sub_agents:
            jsonl = ", jsonl: %s" % _single_line(agent.transcript_path) if agent.transcript_path else ""
            lines.append("sub-agent: %s (%s), working time: %s%s" % (
                _single_line(agent.type), _single_line(agent.agent_id), _duration(agent.working_time or timedelta(0)), jsonl))
            lines.append("tokens: not recorded by Cursor" if agent.total_tokens is None
                         else _estimate(agent, _price(agent.price, api)))
        total = report.total
        status = PARTIAL if total.price_incomplete else PRICED
        lines += ["", "%s%d requests, %d sub-agents)" % (TOTAL_PREFIX, total.request_count, total.sub_agent_count),
                  "working time: %s" % _duration(total.working_time)]
        if total.label:
            lines.append(_single_line(total.label))
        lines.append(_estimate(total, _price(Price(total.price_cents, status), api)))
        return _text("\n".join(lines) + "\n")

    @staticmethod
    def parse_report(report_text: str) -> Optional[ReportTotals]:
        """The numbers of the TOTAL block `render_report` ends a report with: its heading, working time, optional label line and
        token line. None when the text does not end with such a block."""
        _, found, block = report_text.rpartition("\n\n" + TOTAL_PREFIX)
        lines = (TOTAL_PREFIX + block).rstrip("\n").split("\n")
        if not found or len(lines) < TOTAL_BLOCK_MIN_LINES or not _TOTAL_HEADING.fullmatch(lines[0]):
            return None
        working, tokens = _TOTAL_WORKING_TIME.fullmatch(lines[1]), _TOTAL_ESTIMATE.match(lines[-1])
        if working is None or tokens is None:
            return None
        cents = int(tokens["dollars"]) * CENTS_PER_DOLLAR + int(tokens["cents"])
        return ReportTotals(
            working_seconds=int(working["hours"]) * SECONDS_PER_HOUR + int(working["minutes"]) * SECONDS_PER_MINUTE + int(working["seconds"]),
            input_tokens=int(tokens["input"]), output_tokens=int(tokens["output"]),
            reasoning_tokens=None if tokens["reasoning"] is None else int(tokens["reasoning"]),
            cache_read_tokens=int(tokens["cache_read"]), cache_write_tokens=int(tokens["cache_write"]), total_tokens=int(tokens["total"]),
            price_cents=-cents if tokens["sign"] else cents, price_complete=tokens["incomplete"] is None)

    @staticmethod
    def _report_path(log_path: str) -> str:
        return log_path[:-len(LOG_SUFFIX)] + REPORT_SUFFIX

    def _temp_path(self, path: str) -> str:
        folder, name = self._path.split(path)
        return self._path.join(folder, temp_name(name, self._pid()))

    def _remove_stale_temps(self, folder: str) -> None:
        remove_stale_temps(folder, _STALE_TEMP, self._clock())

    def _write_file(self, path: str, text: str) -> WriteOutcome:
        """Writes `path` whole; a link in its place is left alone and named as the reason."""
        if is_link(path):
            return WriteOutcome(path, False, LINK_REASON)
        try:
            publish(self._temp_path(path), path, _text(text).encode("utf-8", errors="replace"))
        except OSError as error:
            return WriteOutcome(path, False, _reason(error))
        return WriteOutcome(path, True)

    def write(self, log_path: str, log_text: str, report_text: str) -> WriteOutcome:
        folder = self._path.dirname(log_path)
        try:
            if not make_real_folders(folder):
                return WriteOutcome(log_path, False, FOLDER_LINK_REASON)
        except OSError as error:
            return WriteOutcome(log_path, False, _reason(error))
        self._remove_stale_temps(folder)
        outcomes = [self._write_file(path, text) for path, text in ((log_path, log_text), (self._report_path(log_path), report_text))]
        return next((outcome for outcome in outcomes if not outcome.written), WriteOutcome(log_path, True))

    def read_report(self, log_path: str) -> Optional[str]:
        try:
            with open(self._report_path(log_path), "rb") as handle:
                return handle.read().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def files_present(self, log_path: str) -> ArchiveFiles:
        return ArchiveFiles(self._is_present(log_path), self._is_present(self._report_path(log_path)))

    @staticmethod
    def _is_present(path: str) -> bool:
        """Absent only when the OS says so: a file that cannot be examined (a folder without access) is taken as present."""
        try:
            os.stat(path)
        except (FileNotFoundError, NotADirectoryError):
            return False
        except OSError:
            pass
        return True
