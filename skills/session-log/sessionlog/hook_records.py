"""C5 hook-records module (Frameworks & Drivers): one record file per Cursor hook call under `<log root>/.records/`, and the removal
of the record temps a killed hook left behind.

A record is `.records/<conversation_id>/<arrival_ns>-<event>-<pid>.json` (format: `cursor_records`), published through an
exclusive temp and a rename (`temp_files`). The hook parses its input only to read the top-level `conversation_id` for the folder
name; invalid JSON, a missing id or an id that fails the C2 id rule files the record under `.records/.invalid/`. Everything else in
the input is parsed later, in sync. Nothing is written through a symlinked or junctioned `.records` or conversation folder: such a
call records nothing. Callers swallow every failure; this module raises on them.
"""
import json
import os
import time
from typing import Callable, Iterator, Optional

from .cursor_records import HOOK_CONVERSATION_ID_KEY, INVALID_FOLDER, RECORDS_FOLDER, STALE_RECORD_TEMP, record_fields, record_file_name
from .model import is_safe_id
from .safe_paths import is_real_folder, make_real_folders
from .temp_files import publish, remove_stale_temps, temp_name

NANOSECONDS_PER_SECOND = 10 ** 9
BYTE_ORDER_MARK = "\ufeff"


def _conversation_id(text: str) -> Optional[str]:
    """The `conversation_id` of the hook input when it is a safe id, else None."""
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    value = data.get(HOOK_CONVERSATION_ID_KEY)
    return value if is_safe_id(value) else None


class HookRecords:
    """`log_root` is the checked `prompt-logs` folder. `time_ns` is the clock of arrival times (default `time.time_ns`) and `pid`
    gives the hook's process id for file names (default `os.getpid`)."""

    def __init__(self, log_root: str, time_ns: Optional[Callable[[], int]] = None, pid: Optional[Callable[[], int]] = None):
        self._records = os.path.join(log_root, RECORDS_FOLDER)
        self._time_ns = time.time_ns if time_ns is None else time_ns
        self._pid = os.getpid if pid is None else pid

    def record(self, event: str, read_input: Callable[[], bytes]) -> None:
        """Writes one record of `event` (one of `RECORD_EVENTS`) holding the bytes `read_input` returns, decoded as UTF-8 with
        errors replaced and a byte order mark stripped. The arrival time is taken before the input is read, which may wait."""
        received_at, pid = self._time_ns(), self._pid()
        text = read_input().decode("utf-8", errors="replace").removeprefix(BYTE_ORDER_MARK)
        conversation_id = _conversation_id(text)
        folder = self._folder(conversation_id or INVALID_FOLDER)
        if folder is None:
            return
        name = record_file_name(received_at, event, pid)
        content = json.dumps(record_fields(event, conversation_id, received_at, pid, text), ensure_ascii=False)
        publish(os.path.join(folder, temp_name(name, pid)), os.path.join(folder, name), content.encode("utf-8"))

    def remove_stale_temps(self) -> None:
        """Removes record temps older than `STALE_TEMP_SECONDS` from every real folder under `.records/`; a younger temp may belong
        to a hook still running. Other files are never touched, and no link is followed."""
        now_seconds = self._time_ns() / NANOSECONDS_PER_SECOND
        for folder in self._folders():
            remove_stale_temps(folder, STALE_RECORD_TEMP, now_seconds)

    def _folder(self, name: str) -> Optional[str]:
        """`.records/<name>`, created when missing; None when it or `.records` is a link. A file in the place of a folder raises."""
        folder = os.path.join(self._records, name)
        return folder if make_real_folders(self._records) and make_real_folders(folder) else None

    def _folders(self) -> Iterator[str]:
        if not is_real_folder(self._records):
            return
        try:
            names = os.listdir(self._records)
        except OSError:
            return
        for name in names:
            path = os.path.join(self._records, name)
            if is_real_folder(path):
                yield path
