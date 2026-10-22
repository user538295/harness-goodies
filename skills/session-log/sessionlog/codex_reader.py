"""Codex adapter of the C2 reader port: finds rollouts under `<root>/sessions/**` and `<root>/archived_sessions/**` as
`*.jsonl` or compressed `*.jsonl.zst`, and maps file-system problems to unreadable sources; `codex_rollout` parses what is
inside.

A unit's key is the rollout's path without `.zst` and its fingerprint is the mtime alone, because compressing a rollout keeps
its mtime (codex `rollout/src/compression.rs:902`): a rollout compressed after a sync is neither a new nor a changed unit.
A compressed rollout's content is not read: `session_of` of one gives None and `read` lists it as not openable, so sync
retries it on every sync; `_parse_unit` is the one place that opens a unit."""
import os
from typing import Any, Callable, Dict, Iterable, List, NamedTuple, Optional, Tuple, Union

from .codex_rollout import Rollout, RolloutMeta, parse_rollout, parse_session_meta
from .model import Harness, Session, is_safe_id
from .reader_port import (UNSAFE_ID_REASON, ReadResult, ScanResult, SourceUnit, UnreadableSource, build_session_key,
                          root_id_of)
from .store_files import StoreFiles

_STORE_FOLDERS = ("sessions", "archived_sessions")
_ROLLOUT_SUFFIX = ".jsonl"
_COMPRESSED_SUFFIX = ".zst"

_FILES = StoreFiles(Harness.CODEX)


class _Folder(NamedTuple):
    """What one store folder holds: its rollout units, the problems it showed and the folders to walk into."""

    units: List[SourceUnit]
    problems: List[UnreadableSource]
    subfolders: List[str]


class _Header(NamedTuple):
    """The first line of a rollout: `meta` is None for a rollout with no line; `unparsable` says the first line is no `session_meta`."""

    meta: Optional[RolloutMeta]
    unparsable: bool


def _is_rollout_name(name: str) -> bool:
    return name.endswith(_ROLLOUT_SUFFIX) or name.endswith(_ROLLOUT_SUFFIX + _COMPRESSED_SUFFIX)


def _key_of(path: str) -> str:
    """The unit key of a rollout path: the path without `.zst`."""
    return path[: -len(_COMPRESSED_SUFFIX)] if path.endswith(_COMPRESSED_SUFFIX) else path


def _source_of(entry: os.DirEntry) -> Union[SourceUnit, UnreadableSource, None]:
    """The unit of a rollout file; None for a directory or a file that vanished meanwhile."""
    return _FILES.regular_file_unit(entry, _key_of(entry.path), lambda file_stat: str(file_stat.st_mtime_ns))


def _parse_unit(unit: SourceUnit, parse: Callable[[Iterable[str]], Any]) -> Any:
    """What `parse` makes of the lines of the unit's rollout, or the unreadable source it is."""
    return _FILES.parse_file(unit.key, parse)


def _list_folder(path: str) -> _Folder:
    """The rollouts of one folder, one unit per key: a rollout that sits there plain and compressed at once is listed as the
    plain one, so the plain entries are taken first, and a problem with one of the two is no problem of a listed key."""
    listing = _FILES.list_dir(path)
    if isinstance(listing, UnreadableSource):
        return _Folder([], [listing], [])
    units: Dict[str, SourceUnit] = {}
    problems: List[UnreadableSource] = []
    subfolders: List[str] = []
    rollouts = [entry for entry in listing if _is_rollout_name(entry.name)]
    for entry in sorted(rollouts, key=lambda entry: entry.name.endswith(_COMPRESSED_SUFFIX)):
        source = _source_of(entry)
        if isinstance(source, SourceUnit):
            units.setdefault(source.key, source)
        elif source:
            problems.append(source)
    for entry in listing:
        if not _is_rollout_name(entry.name):
            is_dir = _FILES.is_real_dir(entry)
            if isinstance(is_dir, UnreadableSource):
                problems.append(is_dir)
            elif is_dir:
                subfolders.append(entry.path)
    return _Folder(list(units.values()), [problem for problem in problems if problem.path not in units], subfolders)


def _read_header(lines: Iterable[str]) -> _Header:
    """Takes the first non-blank line only."""
    for line in lines:
        if line.strip():
            meta = parse_session_meta(line)
            return _Header(meta, unparsable=meta is None)
    return _Header(None, unparsable=False)


def _build_session(root_id: str, rollout: Rollout) -> Optional[Session]:
    """The session a root rollout holds; None when its header gives no start time."""
    meta = rollout.meta
    if meta is None or meta.started_at is None:
        return None
    return Session(harness=Harness.CODEX, session_id=root_id, started_at=meta.started_at, project_dir=meta.project_dir,
                   prompts=rollout.prompts)


def _newest_first(units: Iterable[SourceUnit]) -> List[SourceUnit]:
    return sorted(units, key=lambda unit: (unit.modified_at, unit.key), reverse=True)


def _parse_if_root(unit: SourceUnit, root_id: str, unreadable: List[UnreadableSource]) -> Optional[Rollout]:
    """The parse of the unit when it is the root's own rollout; a unit that cannot be read is added to `unreadable`."""
    rollout = _parse_unit(unit, parse_rollout)
    if isinstance(rollout, UnreadableSource):
        unreadable.append(rollout)
        return None
    return rollout if rollout.meta is not None and rollout.meta.thread_id == root_id else None


def _is_root_header(unit: SourceUnit, root_id: str, unreadable: List[UnreadableSource]) -> bool:
    """Whether the unit's first line says it is the root's own rollout; a unit that cannot be read is added to `unreadable`."""
    header = _parse_unit(unit, _read_header)
    if isinstance(header, UnreadableSource):
        unreadable.append(header)
        return False
    return header.meta is not None and header.meta.thread_id == root_id


def _root_rollout(root_id: str, units: Tuple[SourceUnit, ...], unreadable: List[UnreadableSource]) -> Optional[Rollout]:
    """The root's own rollout among the units of its session: the units named after the root id first, then, only when none
    of them is the root, the others by their first line."""
    named = [unit for unit in units if root_id in os.path.basename(unit.key)]
    for unit in _newest_first(named):
        rollout = _parse_if_root(unit, root_id, unreadable)
        if rollout is not None:
            return rollout
    for unit in _newest_first(unit for unit in units if unit not in named):
        if _is_root_header(unit, root_id, unreadable):
            rollout = _parse_if_root(unit, root_id, unreadable)
            if rollout is not None:
                return rollout
    return None


class CodexReader:
    """Reads the rollouts below the Codex home `root`; each rollout file is one unit. `session_of` reads the beginning of
    the rollout up to its first non-blank line (a buffered chunk is decoded, so bad bytes right after the header can still
    fail it), whose `session_meta` names the root session; a unit it cannot learn that from (an empty rollout, one whose first
    line is no `session_meta`, one that cannot be opened or decoded) gives None. `read` builds the session from the root's
    own rollout, the one whose `session_meta.id` is the root id. Codex names a rollout after its thread
    (`rollout-<time>-<thread id>`, a reverted thread's `...-<thread id>_<rollout id>`), so the units whose file name carries
    the root id are tried first, newest first (`modified_at`, then greatest key), and only when none is the root are the
    other units' first lines looked at (a renamed rollout): the unit's own `id` is checked there, never its session, so no
    unit's session is learned from content again. A unit that cannot be read is listed unreadable. Nothing raises."""

    def __init__(self, root: str) -> None:
        self._folders = tuple(os.path.join(root, name) for name in _STORE_FOLDERS)

    def scan(self) -> ScanResult:
        units: List[SourceUnit] = []
        problems: List[UnreadableSource] = []
        pending = list(self._folders)
        while pending:
            listed = _list_folder(pending.pop())
            units.extend(listed.units)
            problems.extend(listed.problems)
            pending.extend(listed.subfolders)
        return ScanResult(units=sorted(units, key=lambda unit: unit.key), unreadable=sorted(problems, key=lambda source: source.path))

    def session_of(self, unit: SourceUnit) -> Optional[str]:
        header = _parse_unit(unit, _read_header)
        if isinstance(header, UnreadableSource) or header.meta is None:
            return None
        return build_session_key(Harness.CODEX, header.meta.root_id)

    def read(self, session_key: str, units: Tuple[SourceUnit, ...]) -> ReadResult:
        root_id = root_id_of(session_key)
        if not root_id or session_key != build_session_key(Harness.CODEX, root_id):
            return ReadResult()
        if units and not is_safe_id(root_id):
            return ReadResult(unreadable=(_FILES.unreadable(_newest_first(units)[0].key, UNSAFE_ID_REASON),))
        unreadable: List[UnreadableSource] = []
        rollout = _root_rollout(root_id, units, unreadable)
        session = None if rollout is None else _build_session(root_id, rollout)
        return ReadResult(session=session, unreadable=tuple(unreadable))
