"""Cursor adapter of the C2 reader port: Cursor keeps no session store the log can read, so its hooks write one record file per call
(`hook_records`, format in `cursor_records`), and this reader turns the folders of those records into sessions.

A unit is one conversation folder `<records>/<conversation id>/` (only a real folder named by a safe id; anything else directly under the
records folder is ignored, except a link named like a conversation folder or `.invalid`, which is listed unreadable: the hook writes nothing
through a link. A `.records` that is a link is listed unreadable alone and not read). A unit's key is the folder's path and its
fingerprint the number of its record files and the newest record's name,
so a new record, a repeated one too, makes the conversation read again; it is modified at the arrival of that newest record. A scan
only lists names: no record is opened. A file counts as a record when its name is `<arrival_ns>-<event>-<pid>.json` and it is a regular
file; temps and other files are left out, and an entry named like a record that is no regular file (a FIFO, a device) is listed unreadable
as "not a regular file" and never opened, the rest of the conversation staying a unit.

`.records/.invalid/` holds the calls the hook could not file under a conversation; it is no unit, and a scan lists it once, naming how many
calls it holds (none, no entry), for `status` to show. It hides no session, so what is listed for it (a link, an unreadable listing too)
is a source with `hides_sessions` False: it never stops a `usage` lookup.

`read` opens every record of the conversation once, in order of arrival (`cursor_conversation` joins them). A record that cannot be
decoded or parsed is skipped, as a torn line of another harness's file is, unless no record of the conversation can be (it is then
unreadable as "cannot be parsed"); one that cannot be opened makes the conversation unreadable
under its folder, so sync retries it, and no session is built from a part of it. Nothing raises.

A sub-agent may run as a conversation of its own, its hooks filed under its own conversation id. Such a conversation is no root session:
`read` gives none for a conversation that the `subagentStop` record of another conversation names as its `child_conversation_id`, so it
is neither archived nor a candidate for `usage --latest`, while the parent keeps its sub-agent row. Nothing marks a conversation as a
child before that record arrives, so until then it is read like any other (and stays archived if it was); a conversation never counts as
its own child. The named children are learned from the `subagentStop` records alone, once per scan; a record that cannot be opened names
nobody, so a conversation is never hidden by a guess."""
import os
from dataclasses import replace
from datetime import datetime, timezone
from typing import Dict, List, NamedTuple, Optional, Set, Tuple, Union

from .cursor_conversation import SUBAGENT_EVENT, build_session, child_of
from .cursor_records import INVALID_FOLDER, RECORD_FILE_NAME, HookRecord, moment_of_ns, nanoseconds_of, parse_record
from .model import Harness, is_safe_id
from .reader_port import (CANNOT_BE_OPENED_REASON, CANNOT_BE_PARSED_REASON, NOT_A_REGULAR_FILE_REASON, UNSAFE_ID_REASON, ReadResult, ScanResult,
                          SourceUnit, UnreadableSource, build_session_key, root_id_of)
from .safe_paths import LINK_REASON, is_link
from .store_files import NotARegularFileError, StoreFiles, open_text

# What `status` shows beside the path of `.invalid`: the folder is a record of filed-away calls, nothing to repair.
INVALID_CALLS_REASON = "hook calls filed here for lack of a usable conversation id: %d; delete this folder to clear them"

_FILES = StoreFiles(Harness.CURSOR)
# The arrival of a record whose name holds a time no datetime can hold: the newest, so the conversation is always read.
_LATEST_TIME = datetime.max.replace(tzinfo=timezone.utc)


class _RecordFile(NamedTuple):
    arrival_ns: int
    name: str
    path: str
    event: str


def _is_regular_file(entry: os.DirEntry) -> Optional[bool]:
    """Whether the entry is a regular file; False for a FIFO, a device or any other special file, None for a folder (never a record), a
    dangling link (as for the other readers, it has vanished) or an entry that cannot be examined (left out silently, as
    before: a record that cannot be examined cannot be told from one that is gone)."""
    try:
        if entry.is_file():
            return True
        if entry.is_dir():
            return None
        entry.stat()  # raises FileNotFoundError for a dangling link
        return False
    except OSError:
        return None


def _filed_away(source: UnreadableSource) -> UnreadableSource:
    """The source as a finding about `.invalid`: shown by `status`, never a reason a session may be missing."""
    return replace(source, hides_sessions=False)


def _names_folder(name: str) -> bool:
    """Whether a hook could write the records folder `name`: a conversation's or `.invalid`."""
    return name == INVALID_FOLDER or is_safe_id(name)


def _record_files(listing: List[os.DirEntry]) -> Tuple[List[_RecordFile], List[UnreadableSource]]:
    """The record files of a folder listing, oldest arrival first, then by name, and the entries named like a record that are no
    regular file (listed unreadable, never opened)."""
    found = []
    problems = []
    for entry in listing:
        match = RECORD_FILE_NAME.fullmatch(entry.name)
        if match is None:
            continue
        is_regular = _is_regular_file(entry)
        if is_regular:
            found.append(_RecordFile(int(match.group(1)), entry.name, entry.path, match.group(2)))
        elif is_regular is False:
            problems.append(_FILES.unreadable(entry.path, NOT_A_REGULAR_FILE_REASON))
    return sorted(found), problems


def _fingerprint(files: List[_RecordFile]) -> str:
    """The record count and the newest record's name."""
    return "%d:%s" % (len(files), files[-1].name)


class CursorReader:
    """Reads the hook records below `records_folder`, the `prompt-logs/.records` folder of Cursor's log root."""

    def __init__(self, records_folder: str) -> None:
        self._records = records_folder
        self._parents: Optional[Dict[str, Set[str]]] = None

    def scan(self) -> ScanResult:
        self._parents = None
        if is_link(self._records):
            return ScanResult(unreadable=(_FILES.unreadable(self._records, LINK_REASON),))
        listing = _FILES.list_dir(self._records)
        if isinstance(listing, UnreadableSource):
            return ScanResult(unreadable=(listing,))
        units: List[SourceUnit] = []
        problems: List[UnreadableSource] = []
        for entry in listing:
            is_dir = _FILES.is_real_dir(entry)
            if isinstance(is_dir, UnreadableSource):
                problems.append(_filed_away(is_dir) if entry.name == INVALID_FOLDER else is_dir)
            elif not is_dir and _names_folder(entry.name) and is_link(entry.path):
                link = _FILES.unreadable(entry.path, LINK_REASON)
                problems.append(_filed_away(link) if entry.name == INVALID_FOLDER else link)
            elif is_dir and entry.name == INVALID_FOLDER:
                problems.extend(self._invalid_calls(entry))
            elif is_dir and is_safe_id(entry.name):
                found, listed_problems = self._conversation_unit(entry)
                problems.extend(listed_problems)
                if isinstance(found, SourceUnit):
                    units.append(found)
                elif found is not None:
                    problems.append(found)
        return ScanResult(units=sorted(units, key=lambda unit: unit.key), unreadable=sorted(problems, key=lambda source: source.path))

    @staticmethod
    def _invalid_calls(folder: os.DirEntry) -> List[UnreadableSource]:
        """The `.invalid` folder as one source with its call count (nothing when it holds no call), then the entries in it that are
        no regular file; each a finding that hides no session."""
        listing = _FILES.list_dir(folder.path)
        if isinstance(listing, UnreadableSource):
            return [_filed_away(listing)]
        files, problems = _record_files(listing)
        calls = [_FILES.unreadable(folder.path, INVALID_CALLS_REASON % len(files))] if files else []
        return [_filed_away(source) for source in calls + problems]

    @staticmethod
    def _conversation_unit(folder: os.DirEntry) -> Tuple[Union[SourceUnit, UnreadableSource, None], List[UnreadableSource]]:
        """The unit of a conversation folder (None when it holds no record) and the entries in it that are no regular file."""
        listing = _FILES.list_dir(folder.path)
        if isinstance(listing, UnreadableSource):
            return listing, []
        files, problems = _record_files(listing)
        if not files:
            return None, problems
        newest_at = moment_of_ns(files[-1].arrival_ns) or _LATEST_TIME
        return SourceUnit(key=folder.path, fingerprint=_fingerprint(files), modified_at=newest_at), problems

    @staticmethod
    def session_of(unit: SourceUnit) -> str:
        return build_session_key(Harness.CURSOR, os.path.basename(unit.key))

    def _own_unit(self, session_key: str, units: Tuple[SourceUnit, ...]) -> Optional[SourceUnit]:
        matching = [unit for unit in units if self.session_of(unit) == session_key]
        return max(matching, key=lambda unit: (unit.modified_at, unit.key), default=None)

    @staticmethod
    def _child_named_in(path: str) -> Optional[str]:
        """The child a `subagentStop` record file names; None when it cannot be opened or parsed (the read of its own conversation reports
        that), so a record nobody can use never hides a conversation."""
        try:
            with open_text(path) as handle:
                record = parse_record(handle.read())
        except (UnicodeDecodeError, OSError):
            return None
        return None if record is None else child_of(record)

    def _parents_by_child(self) -> Dict[str, Set[str]]:
        """The conversations whose `subagentStop` records name each child, from the records now on disk."""
        parents: Dict[str, Set[str]] = {}
        if is_link(self._records):
            return parents
        listing = _FILES.list_dir(self._records)
        if isinstance(listing, UnreadableSource):
            return parents
        for entry in listing:
            if not is_safe_id(entry.name) or _FILES.is_real_dir(entry) is not True:
                continue
            inner = _FILES.list_dir(entry.path)
            if isinstance(inner, UnreadableSource):
                continue
            for file in _record_files(inner)[0]:
                child = self._child_named_in(file.path) if file.event == SUBAGENT_EVENT else None
                if child is not None:
                    parents.setdefault(child, set()).add(entry.name)
        return parents

    def _is_named_as_child(self, conversation_id: str) -> bool:
        """Whether a conversation other than itself names it as a sub-agent; the answer holds until the next scan."""
        if self._parents is None:
            self._parents = self._parents_by_child()
        return bool(self._parents.get(conversation_id, set()) - {conversation_id})

    def read(self, session_key: str, units: Tuple[SourceUnit, ...]) -> ReadResult:
        root_id = root_id_of(session_key)
        if not root_id or session_key != build_session_key(Harness.CURSOR, root_id):
            return ReadResult()
        unit = self._own_unit(session_key, units)
        if unit is None:
            return ReadResult()
        if not is_safe_id(root_id):
            return ReadResult(unreadable=(_FILES.unreadable(unit.key, UNSAFE_ID_REASON),))
        if self._is_named_as_child(root_id):
            return ReadResult()
        listing = _FILES.list_dir(unit.key)
        if isinstance(listing, UnreadableSource):
            return ReadResult(unreadable=(listing,))
        files, _ = _record_files(listing)
        records: List[HookRecord] = []
        for file in files:
            try:
                with open_text(file.path) as handle:
                    text = handle.read()
            except (UnicodeDecodeError, FileNotFoundError):
                continue
            except NotARegularFileError:
                return ReadResult(unreadable=(_FILES.unreadable(file.path, NOT_A_REGULAR_FILE_REASON),))
            except OSError:
                return ReadResult(unreadable=(_FILES.unreadable(unit.key, CANNOT_BE_OPENED_REASON),))
            record = parse_record(text)
            if record is not None:
                records.append(record)
        if files and not records:
            return ReadResult(unreadable=(_FILES.unreadable(unit.key, CANNOT_BE_PARSED_REASON),))
        return ReadResult(session=build_session(root_id, records))


class CursorHookActivity:
    """Whether a hook call was recorded since a time: any record file named by an arrival at or after it, in a conversation folder or in
    `.invalid`. Only names are read. A folder that cannot be listed leaves the answer open (None) unless a record was found elsewhere."""

    def __init__(self, records_folder: str) -> None:
        self._records = records_folder

    def recorded_since(self, moment: datetime) -> Optional[bool]:
        floor = nanoseconds_of(moment)
        if is_link(self._records):
            return None
        listing = _FILES.list_dir(self._records)
        if isinstance(listing, UnreadableSource):
            return None
        undetermined = False
        for entry in listing:
            is_dir = _FILES.is_real_dir(entry)
            if isinstance(is_dir, UnreadableSource):
                undetermined = True
            elif is_dir and (entry.name == INVALID_FOLDER or is_safe_id(entry.name)):
                inner = _FILES.list_dir(entry.path)
                if isinstance(inner, UnreadableSource):
                    undetermined = True
                elif any(file.arrival_ns >= floor for file in _record_files(inner)[0]):
                    return True
        return None if undetermined else False
