"""Codex adapter of the C2 reader port: finds rollouts under `<root>/sessions/**` and `<root>/archived_sessions/**` as
`*.jsonl` or compressed `*.jsonl.zst`, and maps file-system problems to unreadable sources; `codex_rollout` parses what is
inside, `codex_subagents` makes the threads and `agent_family` nests the child ones and splits an orphan family.

A unit's key is the rollout's path without `.zst` and its fingerprint is the mtime alone, because compressing a rollout keeps
its mtime (codex `rollout/src/compression.rs`): a rollout compressed after a sync is neither a new nor a changed unit.

`_parse_unit` is the one place that opens a unit: the plain file at the key, else the compressed one beside it, read with
`compression.zstd`; a compressed rollout that cannot be decompressed is listed as one that cannot be decoded. A compressed
rollout gives its session, threads and prompts like a plain one, and children nest under it. When the root's own rollout is
gone and the root's or a middle thread's rollout cannot be learned (it is corrupt, or could not be opened) the whole family is
listed unreadable (`PARENT_UNREADABLE_REASON`) instead of being archived as orphans."""
import os
from typing import AbstractSet, Any, Callable, Dict, Iterable, List, NamedTuple, Optional, Sequence, Set, TextIO, Tuple, Union

from .agent_family import Thread, nest_threads, split_orphan_family
from .codex_rollout import Rollout, RolloutMeta, parse_rollout, parse_session_meta
from .codex_subagents import copy_rank, thread_of_rollout
from .model import Harness, Session, is_safe_id
from .reader_port import (UNSAFE_ID_REASON, ReadResult, ScanResult, SourceUnit, UnreadableSource, build_session_key,
                          root_id_of)
from .store_files import FILE_ENCODING, FILE_ERRORS, FileOpener, StoreFiles, open_text, require_regular_file

try:
    from compression import zstd
except ImportError:  # a Python built without zstd still reads plain rollouts; a compressed one is then not openable
    zstd = None

PARENT_UNREADABLE_REASON = "parent rollout cannot be read"

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


class _Found(NamedTuple):
    """A readable rollout of the session: its unit, its parse and the `meta` of that parse, which names the session's root."""

    unit: SourceUnit
    rollout: Rollout
    meta: RolloutMeta


def _is_rollout_name(name: str) -> bool:
    return name.endswith(_ROLLOUT_SUFFIX) or name.endswith(_ROLLOUT_SUFFIX + _COMPRESSED_SUFFIX)


def _key_of(path: str) -> str:
    """The unit key of a rollout path: the path without `.zst`."""
    return path[: -len(_COMPRESSED_SUFFIX)] if path.endswith(_COMPRESSED_SUFFIX) else path


def _source_of(entry: os.DirEntry) -> Union[SourceUnit, UnreadableSource, None]:
    """The unit of a rollout file; None for a directory or a file that vanished meanwhile, unreadable for a FIFO or other special file."""
    return _FILES.regular_file_unit(entry, _key_of(entry.path), lambda file_stat: str(file_stat.st_mtime_ns))


def _open_rollout(key: str) -> TextIO:
    """The unit's rollout as text: the plain file when the key is one, else the compressed file beside it. The plain file is
    asked for first by a stat, never opened blindly: a listed `.zst` can sit beside a plain entry that is no regular file (and
    so was never listed) which opening would block on or fail."""
    if os.path.isfile(key):
        return open_text(key)
    compressed = key + _COMPRESSED_SUFFIX
    if not os.path.lexists(compressed):
        require_regular_file(key)  # a plain entry that is no regular file is reported as such; a vanished one raises as `open` would
    require_regular_file(compressed)
    if zstd is None:
        raise OSError("this Python has no compression.zstd")
    return zstd.open(compressed, "rt", encoding=FILE_ENCODING, errors=FILE_ERRORS)


# `ZstdError` for data that is no zstd stream, `EOFError` for one that ends early
_ROLLOUTS = FileOpener(_open_rollout, (zstd.ZstdError, EOFError) if zstd is not None else ())


def _parse_unit(unit: SourceUnit, parse: Callable[[Iterable[str]], Any]) -> Any:
    """What `parse` makes of the lines of the unit's rollout, plain or compressed, or the unreadable source it is."""
    return _FILES.parse_file(unit.key, parse, _ROLLOUTS)


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


def _newest_first(units: Iterable[SourceUnit]) -> List[SourceUnit]:
    return sorted(units, key=lambda unit: (unit.modified_at, unit.key), reverse=True)


def _is_named_after(thread_id: str, path: str) -> bool:
    """Whether a rollout path is named after the thread: its file name is `rollout-<timestamp>-<thread id>.jsonl`, or, for a
    reverted thread, `rollout-<timestamp>-<thread id>_<rollout id>.jsonl` (codex `rollout_file_name.rs`)."""
    name = os.path.basename(path)
    if not name.endswith(_ROLLOUT_SUFFIX):
        return False
    stem = name[: -len(_ROLLOUT_SUFFIX)]
    return stem.endswith("-" + thread_id) or "-" + thread_id + "_" in stem


def _is_better_copy(found: _Found, known: _Found) -> bool:
    """Whether `found` ranks above `known`, another rollout of the same thread (see `copy_rank`)."""
    return copy_rank(found.meta.started_at, found.unit.key) > copy_rank(known.meta.started_at, known.unit.key)


def _missing_parent_ids(root_id: str, children: Sequence[_Found]) -> Set[str]:
    """The thread ids of the parents the family has no rollout of: the root, whose own rollout is absent, and the parent of
    every child that is no thread of the family."""
    thread_ids = {found.meta.thread_id for found in children}
    parents = {found.meta.parent_thread_id for found in children}
    return {root_id} | {parent for parent in parents if parent is not None and parent not in thread_ids}


def _has_unlearned_parent(root_id: str, children: Sequence[_Found], unit_keys: AbstractSet[str],
                          scanned_paths: Iterable[str]) -> bool:
    """Whether one of `scanned_paths` that is not one of the session's units (`unit_keys`) is named after a missing parent
    (`_missing_parent_ids`): a rollout of the parent is there but could not be learned. `scanned_paths` are the paths as of the
    last scan, which the caller obtains; this reads no file and runs no scan."""
    missing = _missing_parent_ids(root_id, children)
    return any(path not in unit_keys and any(_is_named_after(thread_id, path) for thread_id in missing) for path in scanned_paths)


def _threads_of(found: Iterable[_Found]) -> List[Thread]:
    threads = (thread_of_rollout(item.rollout, item.unit.modified_at) for item in found)
    return [thread for thread in threads if thread is not None]


def _root_session(root_id: str, root: _Found, children: Sequence[_Found]) -> Optional[Session]:
    """The session of the root's own rollout with every other thread as a sub-agent; None when its header gives no start time."""
    if root.meta.started_at is None:
        return None
    return Session(harness=Harness.CODEX, session_id=root_id, started_at=root.meta.started_at, project_dir=root.meta.project_dir,
                   prompts=root.rollout.prompts, sub_agents=nest_threads(_threads_of(children)))


def _orphan_session(root_id: str, found: Sequence[_Found]) -> Optional[Session]:
    """The session of a family whose root rollout is gone (see `split_orphan_family`); None when no thread has a start time, or when
    every thread is a helper: a guardian review is no session of the user's own, so it is archived only as a sub-agent of the
    thread it reviews."""
    if all(item.meta.helper for item in found):
        return None
    family = split_orphan_family(root_id, _threads_of(found))
    if family is None:
        return None
    return Session(harness=Harness.CODEX, session_id=root_id, started_at=family.started_at, project_dir=family.project_dir,
                   orphan_of_parent_id=family.orphan_of_parent_id, prompts=family.prompts, sub_agents=family.sub_agents)


class CodexReader:
    """Reads the rollouts below the Codex home `root`; each rollout file is one unit, plain or compressed (the plain file wins when
    both exist). `session_of` reads the beginning of the rollout up to its first non-blank line (a buffered chunk is decoded, so
    bad bytes right after the header can still fail it), whose `session_meta` names the root session (`session_id`, which a
    grandchild shares with its root); a unit it
    cannot learn that from (an empty rollout, one whose first line is no `session_meta`, one that cannot be opened or
    decoded) gives None.

    `read` opens every unit of the session once. The root's own rollout, the one whose `session_meta.id` is the root id, gives
    the prompts; every other rollout is a sub-agent nested under the thread its `parent_thread_id` names. A thread kept in
    several rollouts (a reverted thread, `...-<thread id>_<rollout id>`) is read from the one `copy_rank` ranks highest: the
    latest header start (a header without a time is the oldest), then the file name, then the whole key; the file's mtime is
    never asked, so touching or restoring an older copy changes nothing. A unit that names another session is left out. A
    unit that cannot be read is listed unreadable. Nothing raises.

    A helper rollout (`RolloutMeta.helper`: a guardian review) is never a session of the user's own: when it is its own root (its
    header names no parent) there is no session, and in a family without its root rollout it is a sub-agent row, never the promoted
    thread, and a family of helpers only gives no session. A helper whose parent rollout is present nests under it like any child.

    Without the root's own rollout the family is an orphan and is archived alone (`split_orphan_family`), unless a parent
    cannot be told from unreadable: a unit of the session that cannot be read, or a rollout named after the root id or after
    the `parent_thread_id` of a child that is no thread of the family (see `_is_named_after`) that is not one of the session's
    units, although the last `scan` found it (listed, or listed as a problem): it could not be learned, as a corrupt compressed one
    cannot. Then no session is built and `read` lists one unit unreadable, the newest of the session's units; sync marks
    every other unit of the session with that first reason (`_mark`), so no family is archived as an orphan by mistake.
    `read` relies on the last `scan` for that (`_scanned_paths`), and runs one itself when none has run on this
    reader yet."""

    def __init__(self, root: str) -> None:
        self._folders = tuple(os.path.join(root, name) for name in _STORE_FOLDERS)
        self._found_paths: Optional[Tuple[str, ...]] = None

    def scan(self) -> ScanResult:
        units: List[SourceUnit] = []
        problems: List[UnreadableSource] = []
        pending = list(self._folders)
        while pending:
            listed = _list_folder(pending.pop())
            units.extend(listed.units)
            problems.extend(listed.problems)
            pending.extend(listed.subfolders)
        self._found_paths = tuple(unit.key for unit in units) + tuple(problem.path for problem in problems)
        return ScanResult(units=sorted(units, key=lambda unit: unit.key), unreadable=sorted(problems, key=lambda source: source.path))

    def session_of(self, unit: SourceUnit) -> Optional[str]:
        header = _parse_unit(unit, _read_header)
        if isinstance(header, UnreadableSource) or header.meta is None:
            return None
        return build_session_key(Harness.CODEX, header.meta.root_id)

    def _scanned_paths(self) -> Tuple[str, ...]:
        """The paths (unit keys and paths of problems) the last scan found, as of that scan: a rollout added or removed since
        does not show. Runs one scan when none has run on this reader yet."""
        if self._found_paths is None:
            self.scan()
        return self._found_paths or ()

    def read(self, session_key: str, units: Tuple[SourceUnit, ...]) -> ReadResult:
        root_id = root_id_of(session_key)
        if not root_id or session_key != build_session_key(Harness.CODEX, root_id):
            return ReadResult()
        if units and not is_safe_id(root_id):
            return ReadResult(unreadable=(_FILES.unreadable(_newest_first(units)[0].key, UNSAFE_ID_REASON),))
        unreadable: List[UnreadableSource] = []
        found_by_thread: Dict[str, _Found] = {}
        for unit in _newest_first(units):
            rollout = _parse_unit(unit, parse_rollout)
            if isinstance(rollout, UnreadableSource):
                unreadable.append(rollout)
            elif rollout.meta is not None and rollout.meta.root_id == root_id:
                found = _Found(unit, rollout, rollout.meta)
                known = found_by_thread.get(found.meta.thread_id)
                if known is None or _is_better_copy(found, known):
                    found_by_thread[found.meta.thread_id] = found
        root = found_by_thread.pop(root_id, None)
        children = list(found_by_thread.values())
        if root is not None:
            session = None if root.meta.helper else _root_session(root_id, root, children)
            return ReadResult(session=session, unreadable=tuple(unreadable))
        if children and not unreadable:
            scanned_paths = self._scanned_paths()
            if _has_unlearned_parent(root_id, children, {unit.key for unit in units}, scanned_paths):
                unreadable.append(_FILES.unreadable(_newest_first(units)[0].key, PARENT_UNREADABLE_REASON))
        session = None if unreadable else _orphan_session(root_id, children)
        return ReadResult(session=session, unreadable=tuple(unreadable))
