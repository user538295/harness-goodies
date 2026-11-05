"""Use-case-owned C3 archive boundary; the writer adapter implements it and owns every path rule and every format rule, the
parsing of its own log and report included."""
from dataclasses import dataclass
from datetime import datetime
from typing import NamedTuple, Optional, Protocol, Tuple

from .model import Session
from .pricing import Total, UsageReport, model_list


@dataclass(frozen=True)
class WriteOutcome:
    """`path` is the file that failed when `written` is false; `reason` (never empty) is recorded for status.
    When `path` differs from the log path, the log IS on disk and only that other file failed; when the folder
    cannot be created nothing was written and `path` is the log path."""

    path: str
    written: bool
    reason: Optional[str] = None


class ArchiveFiles(NamedTuple):
    """Whether the log and the report beside it are on disk."""

    log: bool
    report: bool

    @property
    def complete(self) -> bool:
        return self.log and self.report


@dataclass(frozen=True)
class ArchivedLog:
    """What an archived log tells about its session: its start (in the offset the log shows) and its prompt texts, in order."""

    started_at: datetime
    prompts: Tuple[str, ...]


@dataclass(frozen=True)
class ReportTotals:
    """The numbers of a report's TOTAL block: working time in whole seconds, tokens (`reasoning_tokens` None when the harness
    records none), the price in whole cents and whether it is complete (the report shows an incomplete one as `≥ $x (incomplete)`),
    and the models the session's requests name."""

    working_seconds: int
    input_tokens: int
    output_tokens: int
    reasoning_tokens: Optional[int]
    cache_read_tokens: int
    cache_write_tokens: int
    total_tokens: int
    price_cents: int
    price_complete: bool
    models: Tuple[str, ...]

    @classmethod
    def of(cls, total: Total) -> "ReportTotals":
        """The numbers the TOTAL block of `total`'s report shows."""
        return cls(max(0, int(total.working_time.total_seconds())), total.input_tokens, total.output_tokens, total.reasoning_tokens,
                   total.cache_read_tokens, total.cache_write_tokens, total.total_tokens, total.price_cents, not total.price_incomplete,
                   model_list(total.model_names))


class Archive(Protocol):
    """Production wiring of the adapter: `to_local=lambda d: d.astimezone()`, `clock` returns epoch seconds, `pid` is a
    callable. `render_*` may raise on programming errors (unknown price status, a `to_local` failure or a naive
    result); sync treats that as a failure of that one session, but `usage` renders a live report too, where such a raise
    ends the command as an internal error."""

    def new_log_path(self, session: Session) -> str:
        """The log path of a session without a state entry; deterministic and performs no I/O; called once per
        session, the caller keeps it. The report path is derived from the log path by the archive, never built by
        the caller."""
        ...

    def render_log(self, session: Session) -> str:
        """The log text, LF line ends, no lone surrogates; identical to the text `write` saves."""
        ...

    def render_report(self, report: UsageReport) -> str:
        """The report text, LF line ends, no lone surrogates; identical to the text `write` saves."""
        ...

    def write(self, log_path: str, log_text: str, report_text: str) -> WriteOutcome:
        """Writes the log and the report beside it, each independently: a failure of one never skips the other.
        On POSIX new folders are 0700 and every written file 0600; a slug folder, log or report that is a symbolic link or junction is
        not written through and counts as that file failing. Never raises for file-system errors. When every file is written:
        `WriteOutcome(log_path, True)`. Otherwise `written` is false, `path` names the first file that failed (the log before the report)
        and `reason` is always set."""
        ...

    def read_report(self, log_path: str) -> Optional[str]:
        """The saved report beside the log; None when it is absent or cannot be read as UTF-8 text."""
        ...

    def read_log(self, log_path: str) -> Optional[ArchivedLog]:
        """The start and prompt texts of the saved log; None when it is absent, cannot be read as UTF-8 text or is not in the
        log format."""
        ...

    def parse_report(self, report_text: str) -> Optional[ReportTotals]:
        """The numbers of the report's TOTAL block; None when the text holds no TOTAL block in the report format."""
        ...

    def files_present(self, log_path: str) -> ArchiveFiles:
        """Whether the log and the report beside it exist: one stat each, nothing is read, never raises. Only a file that is
        known to be absent counts as absent: one that cannot be examined (a folder without access) counts as present, so
        sync does not try to recreate files that may be there."""
        ...
