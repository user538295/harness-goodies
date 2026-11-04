"""Everything a run takes from the machine. Each argument defaults to the real thing and is a test injection point: the streams,
the working folder, today's date, the conversion of a moment to local time, `which` (finds `check-jsonschema`) and `run`, the
one process runner behind git, `plan-progress.sh`, `check-jsonschema` and session-log."""
import os
import shutil
import subprocess
import sys
from datetime import date
from typing import NamedTuple

NOT_STARTED_CODE = 127  # the shell's "command not found"


class ProcessResult(NamedTuple):
    code: int
    stdout: str
    stderr: str


def run_process(argv, cwd):
    """Runs `argv` in `cwd` and returns its exit code and text output; a program that cannot start is a NOT_STARTED_CODE result."""
    try:
        completed = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   stdin=subprocess.DEVNULL)
    except OSError as error:
        return ProcessResult(NOT_STARTED_CODE, "", str(error))
    return ProcessResult(completed.returncode, completed.stdout, completed.stderr)


class Environment:
    def __init__(self, stdout=None, stderr=None, stdin=None, version_info=None, platform=None, get_cwd=None, today=None,
                 to_local=None, which=None, run=None):
        self.stdout = sys.stdout if stdout is None else stdout
        self.stderr = sys.stderr if stderr is None else stderr
        self.stdin = sys.stdin if stdin is None else stdin
        self.version_info = sys.version_info if version_info is None else version_info
        self.platform = sys.platform if platform is None else platform
        self.get_cwd = os.getcwd if get_cwd is None else get_cwd
        self.today = date.today if today is None else today
        self.to_local = (lambda moment: moment.astimezone()) if to_local is None else to_local
        self.which = shutil.which if which is None else which
        self.run = run_process if run is None else run
