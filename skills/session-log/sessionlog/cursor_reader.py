"""Cursor adapter of the C2 reader port: Cursor keeps no session store the log can read, so its hooks write one record file per call
(`hook_records`, format in `cursor_records`), and this reader turns the folders of those records into sessions.

A unit is one conversation folder `<records>/<conversation id>/` (only a real folder named by a safe id; anything else directly under the
records folder is ignored). Its key is the folder's path and its fingerprint the number of its record files and the newest record's name,
so a new record, a repeated one too, makes the conversation read again; it is modified at the arrival of that newest record. A scan
only lists names: no record is opened. A file counts as a record when its name is `<arrival_ns>-<event>-<pid>.json` and it is a regular
file; temps, other files and anything that is no regular file are left out (a FIFO is never opened).

`.records/.invalid/` holds the calls the hook could not file under a conversation; it is no unit, and a scan lists it once as unreadable,
naming how many calls it holds (none, no entry).

`read` opens every record of the conversation once, in order of arrival (`cursor_conversation` joins them). A record that cannot be
decoded or parsed is skipped, as a torn line of another harness's file is, unless no record of the conversation can be (it is then
unreadable as "cannot be parsed"); one that cannot be opened makes the conversation unreadable
under its folder, so sync retries it, and no session is built from a part of it. Nothing raises."""
import os
from datetime import datetime, timezone
from typing import List, NamedTuple, Optional, Tuple, Union

from .cursor_conversation import build_session
from .cursor_records import INVALID_FOLDER, RECORD_FILE_NAME, HookRecord, moment_of_ns, parse_record
from .model import Harness, is_safe_id
from .reader_port import (CANNOT_BE_OPENED_REASON, CANNOT_BE_PARSED_REASON, UNSAFE_ID_REASON, ReadResult, ScanResult, SourceUnit, UnreadableSource,
                          build_session_key, root_id_of)
from .store_files import StoreFiles, open_text

INVALID_CALLS_REASON = "hook calls without a usable conversation id: %d"

_FILES = StoreFiles(Harness.CURSOR)
# The arrival of a record whose name holds a time no datetime can hold: the newest, so the conversation is always read.
_LATEST_TIME = datetime.max.replace(tzinfo=timezone.utc)


class _RecordFile(NamedTuple):
    arrival_ns: int
    name: str
    path: str


def _is_regular_file(entry: os.DirEntry) -> bool:
    try:
        return entry.is_file()
    except OSError:
        return False


def _record_files(listing: List[os.DirEntry]) -> List[_RecordFile]:
    """The record files of a folder listing, oldest arrival first, then by name."""
    found = []
    for entry in listing:
        match = RECORD_FILE_NAME.fullmatch(entry.name)
        if match is not None and _is_regular_file(entry):
            found.append(_RecordFile(int(match.group(1)), entry.name, entry.path))
    return sorted(found)


def _fingerprint(files: List[_RecordFile]) -> str:
    """The record count and the newest record's name."""
    return "%d:%s" % (len(files), files[-1].name)


class CursorReader:
    """Reads the hook records below `records_folder`, the `prompt-logs/.records` folder of Cursor's log root."""

    def __init__(self, records_folder: str) -> None:
        self._records = records_folder

    def scan(self) -> ScanResult:
        listing = _FILES.list_dir(self._records)
        if isinstance(listing, UnreadableSource):
            return ScanResult(unreadable=(listing,))
        units: List[SourceUnit] = []
        problems: List[UnreadableSource] = []
        for entry in listing:
            is_dir = _FILES.is_real_dir(entry)
            if isinstance(is_dir, UnreadableSource):
                problems.append(is_dir)
            elif is_dir and entry.name == INVALID_FOLDER:
                problems.extend(self._invalid_calls(entry))
            elif is_dir and is_safe_id(entry.name):
                found = self._conversation_unit(entry)
                if isinstance(found, SourceUnit):
                    units.append(found)
                elif found is not None:
                    problems.append(found)
        return ScanResult(units=sorted(units, key=lambda unit: unit.key), unreadable=sorted(problems, key=lambda source: source.path))

    @staticmethod
    def _invalid_calls(folder: os.DirEntry) -> List[UnreadableSource]:
        """The `.invalid` folder as one unreadable source with its call count; nothing when it holds no call."""
        listing = _FILES.list_dir(folder.path)
        if isinstance(listing, UnreadableSource):
            return [listing]
        calls = len(_record_files(listing))
        return [_FILES.unreadable(folder.path, INVALID_CALLS_REASON % calls)] if calls else []

    @staticmethod
    def _conversation_unit(folder: os.DirEntry) -> Union[SourceUnit, UnreadableSource, None]:
        """The unit of a conversation folder; None when it holds no record."""
        listing = _FILES.list_dir(folder.path)
        if isinstance(listing, UnreadableSource):
            return listing
        files = _record_files(listing)
        if not files:
            return None
        newest_at = moment_of_ns(files[-1].arrival_ns) or _LATEST_TIME
        return SourceUnit(key=folder.path, fingerprint=_fingerprint(files), modified_at=newest_at)

    @staticmethod
    def session_of(unit: SourceUnit) -> str:
        return build_session_key(Harness.CURSOR, os.path.basename(unit.key))

    def _own_unit(self, session_key: str, units: Tuple[SourceUnit, ...]) -> Optional[SourceUnit]:
        matching = [unit for unit in units if self.session_of(unit) == session_key]
        return max(matching, key=lambda unit: (unit.modified_at, unit.key), default=None)

    def read(self, session_key: str, units: Tuple[SourceUnit, ...]) -> ReadResult:
        root_id = root_id_of(session_key)
        if not root_id or session_key != build_session_key(Harness.CURSOR, root_id):
            return ReadResult()
        unit = self._own_unit(session_key, units)
        if unit is None:
            return ReadResult()
        if not is_safe_id(root_id):
            return ReadResult(unreadable=(_FILES.unreadable(unit.key, UNSAFE_ID_REASON),))
        listing = _FILES.list_dir(unit.key)
        if isinstance(listing, UnreadableSource):
            return ReadResult(unreadable=(listing,))
        files = _record_files(listing)
        records: List[HookRecord] = []
        for file in files:
            try:
                with open_text(file.path) as handle:
                    text = handle.read()
            except (UnicodeDecodeError, FileNotFoundError):
                continue
            except OSError:
                return ReadResult(unreadable=(_FILES.unreadable(unit.key, CANNOT_BE_OPENED_REASON),))
            record = parse_record(text)
            if record is not None:
                records.append(record)
        if files and not records:
            return ReadResult(unreadable=(_FILES.unreadable(unit.key, CANNOT_BE_PARSED_REASON),))
        return ReadResult(session=build_session(root_id, records))
