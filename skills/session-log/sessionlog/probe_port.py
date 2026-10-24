"""Use-case-owned probe boundary (C1): what the command use cases need from the Python probe."""
from dataclasses import dataclass
from typing import Optional, Protocol

MINIMUM_PYTHON = (3, 14)
MINIMUM_TEXT = "%d.%d" % MINIMUM_PYTHON


@dataclass(frozen=True)
class ProbeResult:
    """Windows only: whether `python3` runs Python 3.14 or newer; `fix` is set when it does not."""

    ok: bool
    fix: Optional[str] = None


class PythonProbe(Protocol):
    """Checks that `python3` runs Python 3.14 or newer. Returns None when no probe is due (any platform but Windows)."""

    def check(self) -> Optional[ProbeResult]: ...
