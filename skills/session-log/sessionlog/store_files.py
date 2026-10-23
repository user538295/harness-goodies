"""What every reader of a file-based harness store shares: directory and file queries that map file-system problems to
unreadable sources, the unit of a regular file, the walk of a folder tree for the units of one file suffix (for the readers
whose files sit at any depth), and the open/decode/parse mapping of one file. Nothing here knows a harness's
folder layout or record format."""
import errno
import os
import stat
from datetime import datetime, timezone
from typing import Callable, Iterable, List, NamedTuple, Protocol, TextIO, Tuple, Type, TypeVar, Union

from .model import Harness
from .reader_port import (CANNOT_BE_DECODED_REASON, CANNOT_BE_LISTED_REASON, CANNOT_BE_OPENED_REASON, CANNOT_BE_PARSED_REASON,
                          NOT_A_REGULAR_FILE_REASON, SourceUnit, UnreadableSource)

FILE_ENCODING = "utf-8-sig"
_SOCKET_OPEN_ERRORS = (errno.ENXIO, errno.EOPNOTSUPP)  # what opening a socket fails with (Linux, macOS)
_NOT_REGULAR = object()  # what the stat of a file that is a FIFO, a socket or a device is reported as


class _Parsed(Protocol):
    @property
    def unparsable(self) -> bool:
        ...


_T = TypeVar("_T")
_P = TypeVar("_P", bound=_Parsed)


class NotARegularFileError(OSError):
    """A source that is a FIFO, a socket, a device or another special file: opening it could block, so it is never opened."""


def require_regular_file(path: str) -> None:
    """Raises `NotARegularFileError` when `path` (a link followed) exists and is a FIFO, a socket, a device or another special file (a
    folder is left to `open`, which fails on it); a missing one raises what `os.stat` does."""
    mode = os.stat(path).st_mode
    if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
        raise NotARegularFileError(path)


def _open_without_blocking(path: str, flags: int) -> int:
    """The `opener` of `open_text`: opens `path` without waiting for a writer and asks the open descriptor what kind of file it is."""
    try:
        descriptor = os.open(path, flags | getattr(os, "O_NONBLOCK", 0))
    except OSError as error:
        if error.errno in _SOCKET_OPEN_ERRORS:
            raise NotARegularFileError(path) from error
        raise
    try:
        mode = os.fstat(descriptor).st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise NotARegularFileError(path)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def open_text(path: str) -> TextIO:
    """The file at `path` as text, as every reader decodes it (a BOM is dropped). A FIFO or another special file is never waited on:
    the path is opened without blocking and its kind asked of the open descriptor, so a file swapped for a FIFO after a scan cannot hang
    the run either (`NotARegularFileError`). A folder fails as `open` does."""
    return open(path, encoding=FILE_ENCODING, opener=_open_without_blocking)


class FileOpener(NamedTuple):
    """How a reader opens a file as text: `open` and the errors reading it raises besides `UnicodeDecodeError` that mean its
    content cannot be decoded (a compressed file that is corrupt)."""

    open: Callable[[str], TextIO]
    decode_errors: Tuple[Type[Exception], ...] = ()


PLAIN_FILES = FileOpener(open_text)


class StoreFiles:
    """The file-system queries of one harness's reader; every problem becomes an `UnreadableSource` of `harness`."""

    def __init__(self, harness: Harness) -> None:
        self._harness = harness

    def unreadable(self, path: str, reason: str) -> UnreadableSource:
        return UnreadableSource(self._harness, path, reason)

    def examine(self, path: str, query: Callable[[], _T]) -> Union[_T, UnreadableSource, None]:
        """The result of a directory query; None when the entry vanished, an unreadable source for any other OSError."""
        try:
            return query()
        except FileNotFoundError:
            return None
        except OSError:
            return self.unreadable(path, CANNOT_BE_LISTED_REASON)

    def list_dir(self, path: str) -> Union[List[os.DirEntry], UnreadableSource]:
        """The entries of a folder; an empty list for a folder that vanished."""
        listing = self.examine(path, lambda: list(os.scandir(path)))
        return [] if listing is None else listing

    def is_real_dir(self, entry: os.DirEntry) -> Union[bool, UnreadableSource, None]:
        """Whether the entry is a directory itself, never a link or junction to one, so no folder tree is walked twice or in a loop."""
        return self.examine(entry.path, lambda: entry.is_dir(follow_symlinks=False) and not entry.is_junction())

    def regular_file_unit(self, entry: os.DirEntry, key: str, fingerprint_of: Callable[[os.stat_result], str]
                          ) -> Union[SourceUnit, UnreadableSource, None]:
        """The unit of a regular file with `key` and the fingerprint `fingerprint_of` makes of its stat; None for a directory,
        a file that vanished meanwhile (a dangling link too) or a modification time that cannot be a time. Any other kind of file
        (a FIFO, a socket, a device) is unreadable as "not a regular file" and never opened. A problem is reported under `key`."""
        file_stat = self.examine(key, lambda: self._stat_of_file(entry))
        if file_stat is None or isinstance(file_stat, UnreadableSource):
            return file_stat
        if file_stat is _NOT_REGULAR:
            return self.unreadable(key, NOT_A_REGULAR_FILE_REASON)
        try:
            modified_at = datetime.fromtimestamp(file_stat.st_mtime, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
        return SourceUnit(key=key, fingerprint=fingerprint_of(file_stat), modified_at=modified_at)

    @staticmethod
    def _stat_of_file(entry: os.DirEntry) -> Union[os.stat_result, object, None]:
        """The stat of a regular file (a link followed), None for a directory, `_NOT_REGULAR` for any other kind."""
        if entry.is_file():
            return entry.stat()
        if entry.is_dir():
            return None
        entry.stat()  # raises FileNotFoundError for a dangling link, so that it counts as vanished
        return _NOT_REGULAR

    def collect_files(self, folder: str, suffix: str, fingerprint_of: Callable[[os.stat_result], str]
                      ) -> Tuple[List[SourceUnit], List[UnreadableSource]]:
        """The units of the regular files whose names end in `suffix`, anywhere below `folder`, keyed by path, and the problems met on
        the way. A link or junction to a folder is not walked into (no tree is walked twice or in a loop), nor is a folder named like
        a file; a folder or file that vanished is skipped, and a folder that cannot be listed is a problem."""
        units: List[SourceUnit] = []
        problems: List[UnreadableSource] = []
        pending = [folder]
        while pending:
            listing = self.list_dir(pending.pop())
            if isinstance(listing, UnreadableSource):
                problems.append(listing)
                continue
            for entry in listing:
                if entry.name.endswith(suffix):
                    source = self.regular_file_unit(entry, entry.path, fingerprint_of)
                    if isinstance(source, SourceUnit):
                        units.append(source)
                    elif source:
                        problems.append(source)
                    continue
                is_dir = self.is_real_dir(entry)
                if isinstance(is_dir, UnreadableSource):
                    problems.append(is_dir)
                elif is_dir:
                    pending.append(entry.path)
        return units, problems

    def parse_file(self, path: str, parse: Callable[[Iterable[str]], _P], opener: FileOpener = PLAIN_FILES) -> Union[_P, UnreadableSource]:
        """What `parse` makes of the file's lines, or the unreadable source the file is. A problem is reported under `path`, the
        key of the unit, even when `opener` opened another file for it. The `opener`'s decode errors are those of the whole
        open-and-parse step: `parse` is a pure function of the lines and raises none of them itself."""
        try:
            with opener.open(path) as handle:
                parsed = parse(handle)
        except (UnicodeDecodeError,) + opener.decode_errors:
            return self.unreadable(path, CANNOT_BE_DECODED_REASON)
        except NotARegularFileError:
            return self.unreadable(path, NOT_A_REGULAR_FILE_REASON)
        except OSError:
            return self.unreadable(path, CANNOT_BE_OPENED_REASON)
        return self.unreadable(path, CANNOT_BE_PARSED_REASON) if parsed.unparsable else parsed
