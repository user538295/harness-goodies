"""OMP adapter of the C2 reader port: finds session files under `<root>/sessions/**` as `*.jsonl` and, after `omp gc --apply`, under
`<root>/archive/sessions/**` as gzipped `*.jsonl.gz` (the same relative path, plus `.gz`), and maps file-system problems to unreadable
sources; `omp_layout` tells where a file sits in its family from its path, `omp_transcript` parses what is inside and `omp_family`
makes the session of a family.

A unit's key is the file's path and its fingerprint the size and mtime, so a file that `omp gc` moves is a new unit under a new key:
sync reads its session once and, when nothing happened since the on-time, records it inactive, and does not read it again.

The session of a file is told by its path alone, so `session_of` opens nothing."""
import gzip
import os
import zlib
from datetime import datetime
from typing import Dict, Hashable, List, NamedTuple, Optional, TextIO, Tuple

from .model import Harness, is_safe_id
from .omp_family import Member, build_session, cyclic_keys
from .omp_layout import (ARCHIVE_FOLDER, ARCHIVED_SUFFIX, SESSION_SUFFIX, SESSIONS_FOLDER, Place, place_of, root_id_of_stem,
                         stem_of_path)
from .omp_transcript import parse_parent_link, parse_transcript
from .reader_port import (PARENT_CYCLE_REASON, UNSAFE_ID_REASON, ReadResult, ScanResult, SourceUnit, UnreadableSource, build_session_key,
                          root_id_of)
from .store_files import FILE_ENCODING, FILE_ERRORS, FileOpener, StoreFiles, open_text, require_regular_file

PARENT_UNREADABLE_REASON = "parent session cannot be read"

_FILES = StoreFiles(Harness.OMP)


def _open_session(key: str) -> TextIO:
    """The unit's file as text, decompressed when it is a gzipped archive file."""
    if key.endswith(ARCHIVED_SUFFIX):
        require_regular_file(key)
        return gzip.open(key, "rt", encoding=FILE_ENCODING, errors=FILE_ERRORS)
    return open_text(key)


# `BadGzipFile` for data that is no gzip stream, `EOFError` for one that ends early, `zlib.error` for a corrupt body
_SESSION_FILES = FileOpener(_open_session, (gzip.BadGzipFile, EOFError, zlib.error))


class _StoreFolder(NamedTuple):
    path: str
    suffix: str


class _FamilyFiles(NamedTuple):
    """The files of one session read: its root's own file (None when it is absent or could not be read), the children, the units
    that could not be read and whether one of them is the root's own file."""

    root: Optional[Member]
    children: List[Member]
    unreadable: List[UnreadableSource]
    root_failed: bool


class _ScanFacts(NamedTuple):
    """What a scan found out besides the units: the paths it could not examine or list, and the newest unit of each root session file
    by its stem."""

    problem_paths: Tuple[str, ...]
    root_units: Dict[str, SourceUnit]


def _fingerprint(file_stat: os.stat_result) -> str:
    return "%d:%d" % (file_stat.st_size, file_stat.st_mtime_ns)


def _oldest_first_key(unit: SourceUnit) -> Tuple[datetime, str]:
    return unit.modified_at, unit.key


class OmpReader:
    """Reads the session files below the OMP agent folder `root`; each file is one unit, plain under `sessions/` or gzipped under
    `archive/sessions/`.

    `read` opens every unit of the session once, except a file that is only a second copy of another (the same relative path,
    one plain and one archived; the root's own file is one per session): the newest copy by file time, then by key, is read and
    the other left alone. The root's own file gives the prompts; every other file is a sub-agent nested under the file its header's
    `parentSession` names by stem (a child whose parent is gone stays at the top level). A unit that names another session, or lies
    outside both store folders, is left out. A unit that cannot be read is listed unreadable. Nothing raises.

    A parent loop is listed unreadable (`PARENT_CYCLE_REASON`): a child in a loop, and every child hanging from one, is left out and
    the rest of the family is read as usual. A root file whose own chain of `parentSession`s (a fork names the root file it was
    forked from) comes back to a file already passed gives no session and is listed itself; sync marks the rest of its family with
    that reason. The chain is followed through the root files of the last scan by their headers alone, each read once per scan; a
    root file without `parentSession` opens nothing else. A root's loop is judged when its own session is read, so a file that later
    completes a loop with an archived one is listed from then on while the other stays archived.

    Without the root's own file the family is an orphan and is archived alone, unless the root might be there: a unit of the family
    cannot be read, or the last `scan` could not examine the root's file or a folder it may sit in (`PARENT_UNREADABLE_REASON`, found
    before any file is read). Then no session is built and `read` lists the failing units, or the newest unit of the session when the
    scan failed; sync marks every other unit of the session with the first reason. A root's own file that cannot be read gives no
    session either. `read` relies on the last `scan` for that, and runs one itself when none has run on this reader."""

    def __init__(self, root: str) -> None:
        self._folders = (_StoreFolder(os.path.join(root, *SESSIONS_FOLDER), SESSION_SUFFIX),
                         _StoreFolder(os.path.join(root, *ARCHIVE_FOLDER), ARCHIVED_SUFFIX))
        self._facts: Optional[_ScanFacts] = None
        self._parent_stems: Dict[str, Optional[str]] = {}  # the parent each root file of the last scan names, filled in as loops are checked

    def _scan(self) -> Tuple[ScanResult, _ScanFacts]:
        units: List[SourceUnit] = []
        problems: List[UnreadableSource] = []
        for store in self._folders:
            found, failed = _FILES.collect_files(store.path, store.suffix, _fingerprint)
            units.extend(found)
            problems.extend(failed)
        facts = _ScanFacts(tuple(problem.path for problem in problems), self._newest_root_units(units))
        result = ScanResult(units=sorted(units, key=lambda unit: unit.key), unreadable=sorted(problems, key=lambda source: source.path))
        return result, facts

    def scan(self) -> ScanResult:
        result, self._facts = self._scan()
        self._parent_stems = {}
        return result

    def _newest_root_units(self, units: List[SourceUnit]) -> Dict[str, SourceUnit]:
        newest: Dict[str, SourceUnit] = {}
        for unit in sorted(units, key=_oldest_first_key):  # the newest copy is stored last
            place = self._place_of(unit.key)
            if place is not None and place.is_root:
                newest[place.stem] = unit
        return newest

    def _place_of(self, key: str) -> Optional[Place]:
        """Where the file at `key` sits in its family, told by its path alone; None for a path outside both store folders."""
        for store in self._folders:
            prefix = store.path + os.sep
            if key.startswith(prefix) and key.endswith(store.suffix):
                return place_of(key[len(prefix):].split(os.sep), store.suffix)
        return None

    def session_of(self, unit: SourceUnit) -> Optional[str]:
        place = self._place_of(unit.key)
        return None if place is None else build_session_key(Harness.OMP, root_id_of_stem(place.root_stem))

    def _chosen_places(self, root_id: str, units: Tuple[SourceUnit, ...]) -> List[Tuple[SourceUnit, Place]]:
        """The units that belong to the session, one per file (see `Place.identity`): of the copies of a file the newest wins. In key order."""
        chosen: Dict[Tuple[Hashable, ...], Tuple[SourceUnit, Place]] = {}
        for unit in sorted(units, key=_oldest_first_key):  # the newest copy is stored last
            place = self._place_of(unit.key)
            if place is not None and root_id_of_stem(place.root_stem) == root_id:
                chosen[place.identity] = (unit, place)
        return sorted(chosen.values(), key=lambda pair: pair[0].key)

    @staticmethod
    def _parse(chosen: List[Tuple[SourceUnit, Place]]) -> _FamilyFiles:
        root: Optional[Member] = None
        children: List[Member] = []
        unreadable: List[UnreadableSource] = []
        root_failed = False
        for unit, place in chosen:
            transcript = _FILES.parse_file(unit.key, parse_transcript, _SESSION_FILES)
            if isinstance(transcript, UnreadableSource):
                unreadable.append(transcript)
                root_failed = root_failed or place.is_root
            elif place.is_root:
                root = Member(unit.key, place.stem, unit.modified_at, transcript)
            else:
                children.append(Member(unit.key, place.stem, unit.modified_at, transcript))
        return _FamilyFiles(root, children, unreadable, root_failed)

    def _scan_facts(self) -> _ScanFacts:
        """What the last scan found out. Runs one scan when none has run on this reader yet."""
        if self._facts is None:
            self._facts = self._scan()[1]
            self._parent_stems = {}
        return self._facts

    def _root_may_be_hidden(self, child: Place) -> bool:
        """Whether the last scan failed on the file of the root session of the family `child` belongs to (in either store folder),
        or on a folder that holds it."""
        project = child.where[0]
        candidates = [os.path.join(store.path, project, child.root_stem + store.suffix) for store in self._folders]
        return any(candidate == path or candidate.startswith(path + os.sep) for candidate in candidates
                   for path in self._scan_facts().problem_paths)

    def _parent_stem_of_root_file(self, stem: str) -> Optional[str]:
        """The stem of the parent the root file `stem` of the last scan names; None when it names none, is not in the scan, or its
        header cannot be read. Only its header is read, once per scan."""
        if stem not in self._parent_stems:
            unit = self._scan_facts().root_units.get(stem)
            link = None if unit is None else _FILES.parse_file(unit.key, parse_parent_link, _SESSION_FILES)
            parent = None if link is None or isinstance(link, UnreadableSource) else link.parent_session
            self._parent_stems[stem] = stem_of_path(parent) if parent else None
        return self._parent_stems[stem]

    def _is_in_root_loop(self, root: Member) -> bool:
        """Whether the chain of `parentSession`s from the root's own file (a fork names the root file it was forked from) comes back
        to a file already passed. The ancestors are root files found by the last scan; one that is missing or cannot be read ends the
        chain."""
        seen = {root.stem}
        parent = root.parent_stem
        while parent is not None:
            if parent in seen:
                return True
            seen.add(parent)
            parent = self._parent_stem_of_root_file(parent)
        return False

    def _result_of(self, root_id: str, root_stem: str, files: _FamilyFiles) -> ReadResult:
        unreadable = list(files.unreadable)
        looping_root = files.root if files.root is not None and self._is_in_root_loop(files.root) else None
        if looping_root is not None:
            unreadable.append(_FILES.unreadable(looping_root.key, PARENT_CYCLE_REASON))
        # a failing unit might be the root's own file or the missing parent, and a root in a parent loop is no session
        blocked = files.root_failed or looping_root is not None or (files.root is None and bool(files.unreadable))
        cyclic = cyclic_keys(files.children)
        unreadable.extend(_FILES.unreadable(child.key, PARENT_CYCLE_REASON) for child in files.children if child.key in cyclic)
        if blocked:
            return ReadResult(unreadable=tuple(unreadable))
        children = [child for child in files.children if child.key not in cyclic]
        return ReadResult(session=build_session(root_id, root_stem, files.root, children), unreadable=tuple(unreadable))

    def read(self, session_key: str, units: Tuple[SourceUnit, ...]) -> ReadResult:
        root_id = root_id_of(session_key)
        if not root_id or session_key != build_session_key(Harness.OMP, root_id):
            return ReadResult()
        chosen = self._chosen_places(root_id, units)
        if not chosen:
            return ReadResult()
        newest = max((unit for unit, _ in chosen), key=_oldest_first_key).key
        if not is_safe_id(root_id):
            return ReadResult(unreadable=(_FILES.unreadable(newest, UNSAFE_ID_REASON),))
        first_child = next((place for _, place in chosen if not place.is_root), None)
        if first_child is not None and not any(place.is_root for _, place in chosen) and self._root_may_be_hidden(first_child):
            return ReadResult(unreadable=(_FILES.unreadable(newest, PARENT_UNREADABLE_REASON),))
        return self._result_of(root_id, chosen[0][1].root_stem, self._parse(chosen))
