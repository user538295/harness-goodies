"""C3 adapter: renders the log and the usage report and writes them atomically; owns every archive path rule."""
import hashlib
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional

from .archive_port import WriteOutcome
from .model import Session
from .pricing import PARTIAL, PRICED, UNKNOWN, Price, UsageReport

CENTS_PER_DOLLAR = 100
SLUG_MAX_LENGTH = 64
SLUG_HASH_LENGTH = 12
ROOT_SLUG = "root"
UNKNOWN_SLUG = "unknown"
SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600
STALE_TEMP_SECONDS = SECONDS_PER_HOUR
FILE_MODE = 0o600
TEMP_RANDOM_BYTES = 4
SESSION_PREFIX = "session_"
LOG_SUFFIX = ".md"
REPORT_SUFFIX = ".usage.txt"
TEMP_SUFFIX = ".tmp"
FILE_TIME_FORMAT = "%Y-%m-%d_%H-%M-%S"
ENTRY_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
CLOCK_FORMAT = "%H:%M:%S"
NO_VALUE = "-"
API_EQUIVALENT = " (API-equivalent)"
_OTHER_CHARS = re.compile(r"[^A-Za-z0-9_-]")
_TEMP_NAME = ".%s.%d.%s" + TEMP_SUFFIX
_LINE_BREAKS = re.compile(r"[\r\n\t]+")
_STALE_TEMP = re.compile(r"\.%s.*(?:%s|%s)\.\d+\.[0-9a-f]+%s" % (
    re.escape(SESSION_PREFIX), re.escape(LOG_SUFFIX), re.escape(REPORT_SUFFIX), re.escape(TEMP_SUFFIX)))


def _offset_label(moment: datetime) -> str:
    seconds = int(moment.utcoffset().total_seconds())
    minutes = abs(seconds) // SECONDS_PER_MINUTE
    if minutes == 0:
        return "UTC"
    hours, rest = divmod(minutes, SECONDS_PER_MINUTE)
    return "UTC%s%d%s" % ("-" if seconds < 0 else "+", hours, ":%02d" % rest if rest else "")


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
        text = "\u2265 %s (incomplete)" % _dollars(price.cents)
    elif price.status == UNKNOWN:
        text = UNKNOWN
    else:
        raise ValueError("unexpected price status: %r" % (price.status,))
    return text + (API_EQUIVALENT if api_equivalent else "")


def _estimate(usage, price_text: str) -> str:
    return ("est. used token: input: %d, output: %d, cache_create: %d, cache_read: %d, total_tokens: %d, "
            "price: %s, model: %s, effort: %s") % (
        usage.input_tokens, usage.output_tokens, usage.cache_write_tokens, usage.cache_read_tokens,
        usage.total_tokens or 0, price_text, _single_line(usage.model_names), _single_line(usage.effort or NO_VALUE))


def _discard(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


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
        local = self._to_local(moment)
        if local.utcoffset() is None:
            raise ValueError("to_local must return an aware datetime")
        return local

    def _entry_time(self, moment: datetime) -> str:
        local = self._local(moment)
        return "%s %s" % (local.strftime(ENTRY_TIME_FORMAT), _offset_label(local))

    def render_log(self, session: Session) -> str:
        lines: List[str] = ["# Session log", "- harness: %s" % session.harness.value,
                            "- session: %s" % session.session_id,
                            "- project: %s" % _single_line(session.project_dir or NO_VALUE),
                            "- start: %s" % self._entry_time(session.started_at)]
        if session.orphan_of_parent_id:
            lines.append("- sub-agent of %s (parent not found)" % _single_line(session.orphan_of_parent_id))
        for prompt in session.prompts:
            lines += ["", "## %s" % self._entry_time(prompt.submitted_at), prompt.text]
            if prompt.reply_text is not None:
                lines += ["", "### %s response" % self._entry_time(prompt.reply_at or prompt.submitted_at), prompt.reply_text]
        return _text("\n".join(lines) + "\n")

    def render_report(self, report: UsageReport) -> str:
        api = report.total.api_equivalent
        lines = ["session: %s" % report.session_id, "harness: %s" % report.harness.value,
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
            lines.append("sub-agent: %s (%s), working time: %s" % (
                _single_line(agent.type), _single_line(agent.agent_id), _duration(agent.working_time or timedelta(0))))
            lines.append("tokens: not recorded by Cursor" if agent.total_tokens is None
                         else _estimate(agent, _price(agent.price, api)))
        total = report.total
        status = PARTIAL if total.price_incomplete else PRICED
        lines += ["", "TOTAL (%d requests, %d sub-agents)" % (total.request_count, total.sub_agent_count),
                  "working time: %s" % _clock_text(sum(_whole_seconds(row.working_time) for row in report.prompts))]
        if total.label:
            lines.append(_single_line(total.label))
        lines.append(_estimate(total, _price(Price(total.price_cents, status), api)))
        return _text("\n".join(lines) + "\n")

    @staticmethod
    def _report_path(log_path: str) -> str:
        return log_path[:-len(LOG_SUFFIX)] + REPORT_SUFFIX

    def _temp_path(self, path: str) -> str:
        folder, name = self._path.split(path)
        return self._path.join(folder, _TEMP_NAME % (name, self._pid(), os.urandom(TEMP_RANDOM_BYTES).hex()))

    def _remove_stale_temps(self, folder: str) -> None:
        try:
            names = os.listdir(folder)
        except OSError:
            return
        cutoff = self._clock() - STALE_TEMP_SECONDS
        for name in names:
            if not name.endswith(TEMP_SUFFIX) or not _STALE_TEMP.fullmatch(name):
                continue
            path = self._path.join(folder, name)
            try:
                if os.stat(path).st_mtime < cutoff:
                    os.unlink(path)
            except OSError:
                continue

    def _replace_with(self, path: str, text: str) -> None:
        temp = self._temp_path(path)
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
        try:
            try:
                handle = os.fdopen(descriptor, "wb")
            except BaseException:
                os.close(descriptor)
                raise
            with handle:
                handle.write(_text(text).encode("utf-8", errors="replace"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        except BaseException:
            _discard(temp)
            raise

    def write(self, log_path: str, log_text: str, report_text: str) -> WriteOutcome:
        folder = self._path.dirname(log_path)
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as error:
            return WriteOutcome(log_path, False, _reason(error))
        self._remove_stale_temps(folder)
        failure: Optional[WriteOutcome] = None
        for path, text in ((log_path, log_text), (self._report_path(log_path), report_text)):
            try:
                self._replace_with(path, text)
            except OSError as error:
                failure = failure or WriteOutcome(path, False, _reason(error))
        return failure or WriteOutcome(log_path, True)

    def read_report(self, log_path: str) -> Optional[str]:
        try:
            with open(self._report_path(log_path), "rb") as handle:
                return handle.read().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return None
