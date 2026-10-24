"""Use-case-owned C3 archive boundary; the writer adapter implements it and owns every path rule."""
from dataclasses import dataclass
from typing import Optional, Protocol

from .model import Session
from .pricing import UsageReport


@dataclass(frozen=True)
class WriteOutcome:
    """`path` is the file that failed when `written` is false; `reason` (never empty) is recorded for status.
    When `path` differs from the log path, the log IS on disk and only that other file failed; when the folder
    cannot be created nothing was written and `path` is the log path."""

    path: str
    written: bool
    reason: Optional[str] = None


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
