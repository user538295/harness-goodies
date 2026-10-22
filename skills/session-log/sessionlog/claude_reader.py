"""Claude Code adapter of the C2 reader port: finds main-session files under `<root>/projects/<project>/<sid>.jsonl`
and maps file-system problems to unreadable sources; `claude_transcript` parses what is inside."""
import os
from datetime import datetime, timezone
from typing import Callable, List, Optional, Tuple, TypeVar, Union

from .claude_transcript import json_objects, parse_main_session
from .model import Harness, Session, is_safe_id
from .reader_port import (CANNOT_BE_DECODED_REASON, CANNOT_BE_LISTED_REASON, CANNOT_BE_OPENED_REASON, UNSAFE_ID_REASON,
                          ReadResult, ScanResult, SourceUnit, UnreadableSource, build_session_key)

_SESSION_FILE_SUFFIX = ".jsonl"
_PROJECTS_DIR = "projects"
_FILE_ENCODING = "utf-8-sig"

_T = TypeVar("_T")
_Found = Union[SourceUnit, UnreadableSource]


def _stem(path: str) -> str:
    return os.path.basename(path)[: -len(_SESSION_FILE_SUFFIX)]


def _unreadable(path: str, reason: str) -> UnreadableSource:
    return UnreadableSource(Harness.CLAUDE, path, reason)


def _examine(path: str, query: Callable[[], _T]) -> Union[_T, UnreadableSource, None]:
    """The result of a directory query; None when the entry vanished, an unreadable source for any other OSError."""
    try:
        return query()
    except FileNotFoundError:
        return None
    except OSError:
        return _unreadable(path, CANNOT_BE_LISTED_REASON)


def _list_dir(path: str) -> Union[List[os.DirEntry], UnreadableSource]:
    listing = _examine(path, lambda: list(os.scandir(path)))
    return [] if listing is None else listing


def _source_of(entry: os.DirEntry) -> Optional[_Found]:
    """The unit of a main-session file; None for anything else or for a file that vanished meanwhile."""
    if not entry.name.endswith(_SESSION_FILE_SUFFIX):
        return None
    file_stat = _examine(entry.path, lambda: entry.stat() if entry.is_file() else None)
    if file_stat is None or isinstance(file_stat, UnreadableSource):
        return file_stat
    try:
        modified_at = datetime.fromtimestamp(file_stat.st_mtime, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return SourceUnit(key=entry.path, fingerprint="%d:%d" % (file_stat.st_size, file_stat.st_mtime_ns), modified_at=modified_at)


def _project_sources(project: os.DirEntry) -> List[_Found]:
    is_dir = _examine(project.path, project.is_dir)
    if isinstance(is_dir, UnreadableSource):
        return [is_dir]
    listing = _list_dir(project.path) if is_dir else []
    if isinstance(listing, UnreadableSource):
        return [listing]
    return [found for found in map(_source_of, listing) if found]


class ClaudeReader:
    """Reads `<root>/projects/<project>/<sid>.jsonl` below the Claude config dir `root`. `read` uses the main file of the
    session; when several units carry the same session id it takes the newest `modified_at`, ties going to the greatest
    key. Every file-system or decoding problem becomes an `UnreadableSource`, never an exception."""

    def __init__(self, root: str) -> None:
        self._projects = os.path.join(root, _PROJECTS_DIR)

    def scan(self) -> ScanResult:
        projects = _list_dir(self._projects)
        if isinstance(projects, UnreadableSource):
            return ScanResult(unreadable=(projects,))
        found = [source for project in projects for source in _project_sources(project)]
        units = [source for source in found if isinstance(source, SourceUnit)]
        unreadable = [source for source in found if isinstance(source, UnreadableSource)]
        return ScanResult(units=sorted(units, key=lambda unit: unit.key), unreadable=sorted(unreadable, key=lambda source: source.path))

    def session_of(self, unit: SourceUnit) -> Optional[str]:
        return build_session_key(Harness.CLAUDE, _stem(unit.key))

    def read(self, session_key: str, units: Tuple[SourceUnit, ...]) -> ReadResult:
        matching = [unit for unit in units if self.session_of(unit) == session_key]
        if not matching:
            return ReadResult()
        path = max(matching, key=lambda unit: (unit.modified_at, unit.key)).key
        session_id = _stem(path)
        if not is_safe_id(session_id):
            return ReadResult(unreadable=(_unreadable(path, UNSAFE_ID_REASON),))
        try:
            with open(path, encoding=_FILE_ENCODING) as handle:
                transcript = parse_main_session(json_objects(handle))
        except UnicodeDecodeError:
            return ReadResult(unreadable=(_unreadable(path, CANNOT_BE_DECODED_REASON),))
        except OSError:
            return ReadResult(unreadable=(_unreadable(path, CANNOT_BE_OPENED_REASON),))
        if transcript.started_at is None:
            return ReadResult()
        return ReadResult(session=Session(
            harness=Harness.CLAUDE, session_id=session_id, started_at=transcript.started_at,
            project_dir=transcript.project_dir, prompts=transcript.prompts))
