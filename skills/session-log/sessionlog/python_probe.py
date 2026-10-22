"""Python probe runner (C1, S24): on Windows `on` and `status` run `python3 -c` and report the exact fix when it fails.

`python3` there may be the Microsoft Store stand-in, which exits non-zero without running Python; the probe therefore treats any
failure (non-zero exit, program missing or not startable, timeout) as "Python not usable" and never looks at the exit code (Q2).
The platform name and the command runner are injected, so no test starts a Windows probe. The probe is deliberately not cached:
each `on` and `status` asks again, because the fix is something the user does between runs.
"""
import subprocess
from typing import Callable, Optional, Sequence

from .probe_port import MINIMUM_PYTHON, MINIMUM_TEXT, ProbeResult

WINDOWS_PLATFORM = "win32"
PROBE_SOURCE = "import sys; sys.exit(0 if sys.version_info >= %r else 1)" % (MINIMUM_PYTHON,)
PROBE_COMMAND = ("python3", "-c", PROBE_SOURCE)
PROBE_TIMEOUT_SECONDS = 10
PROBE_FIX = ("install Python %s or newer with the Python install manager from python.org/downloads/windows, then make sure it "
             "is the default python3 (py list) and the Microsoft Store python3 app execution alias does not shadow it "
             "(Settings > Apps > Advanced app settings > App execution aliases)" % MINIMUM_TEXT)

CommandRunner = Callable[[Sequence[str]], int]


def run_command(command: Sequence[str], timeout: float = PROBE_TIMEOUT_SECONDS) -> int:
    """The exit code of `command`, run with no input and its output discarded; raises OSError when it cannot start and
    `subprocess.TimeoutExpired` when it runs longer than `timeout` seconds."""
    completed = subprocess.run(list(command), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=timeout)
    return completed.returncode


class PythonProbeRunner:
    """Implements `PythonProbe`: probes only when `platform` is Windows."""

    def __init__(self, platform: str, run: CommandRunner) -> None:
        self._platform = platform
        self._run = run

    def check(self) -> Optional[ProbeResult]:
        if self._platform != WINDOWS_PLATFORM:
            return None
        try:
            if self._run(PROBE_COMMAND) == 0:
                return ProbeResult(ok=True)
        except (OSError, subprocess.SubprocessError):
            pass
        return ProbeResult(ok=False, fix=PROBE_FIX)
