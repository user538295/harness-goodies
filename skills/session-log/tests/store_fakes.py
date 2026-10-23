"""Fakes for the file-system side of reader tests: directory entries whose queries fail or answer as chosen, a `scandir` that
lists extra entries, fails for chosen folders or lists in reverse order, zstd and gzip files made from text at test time, and a counter of
the files the OMP reader opens."""
import gzip
import os
from collections import Counter
from contextlib import ExitStack
from unittest import mock


def write_compressed(path, text, mtime_ns=None):
    """Writes `text` as a zstd file at `path`, made at test time (no fixture is committed), with the modification time
    `mtime_ns` when given."""
    from compression import zstd
    with open(path, "wb") as handle:
        handle.write(zstd.compress(text.encode("utf-8")))
    if mtime_ns is not None:
        os.utime(path, ns=(mtime_ns, mtime_ns))


def write_gzip(path, text, mtime_ns=None):
    """Writes `text` as a gzip file at `path`, made at test time (no fixture is committed), with the modification time
    `mtime_ns` when given."""
    with open(path, "wb") as handle:
        handle.write(gzip.compress(text.encode("utf-8"), mtime=0))
    if mtime_ns is not None:
        os.utime(path, ns=(mtime_ns, mtime_ns))


def compress_in_place(path):
    """What Codex does to a rollout after a sync: a `.zst` file made from the text at `path`, with its modification time, replaces
    it. Returns the compressed path."""
    with open(path, encoding="utf-8", newline="") as handle:
        text = handle.read()
    write_compressed(path + ".zst", text, os.stat(path).st_mtime_ns)
    os.remove(path)
    return path + ".zst"


class FakeEntry:
    """A DirEntry whose every file-system query raises `error` (a file that vanished or cannot be examined); `queried` says whether
    any query reached it, so a test can tell that the entry was listed at all."""

    def __init__(self, path, error):
        self.path, self.name = path, os.path.basename(path)
        self.queried = False
        self._error = error

    def is_dir(self, *, follow_symlinks=True):
        self.queried = True
        raise self._error

    def is_file(self, *, follow_symlinks=True):
        self.queried = True
        raise self._error

    def stat(self, *, follow_symlinks=True):
        self.queried = True
        raise self._error


class StatEntry:
    """A regular-file DirEntry whose stat is `file_stat`."""

    def __init__(self, path, file_stat):
        self.path, self.name = path, os.path.basename(path)
        self._file_stat = file_stat

    def is_file(self, *, follow_symlinks=True):
        return True

    def stat(self, *, follow_symlinks=True):
        return self._file_stat


class JunctionEntry:
    """A DirEntry that is a directory and a Windows junction at once."""

    def __init__(self, path):
        self.path, self.name = path, os.path.basename(path)

    def is_dir(self, *, follow_symlinks=True):
        return True

    def is_junction(self):
        return True


def scandir_wrapping(extra=None, fail=None, reverse=False):
    """A patch of `os.scandir` that adds `extra` entries per path, raises `fail[path]` and optionally reverses the order."""
    real_scandir = os.scandir
    extra, fail = extra or {}, fail or {}

    def scandir(path):
        if path in fail:
            raise fail[path]
        entries = [*real_scandir(path), *extra.get(path, ())]
        return list(reversed(entries)) if reverse else entries

    return mock.patch("os.scandir", scandir)


class OmpOpenCounter:
    """A context manager that counts how often each file is opened through the OMP reader: plain files by `open_text`, gzipped ones by
    `gzip.open`. Enter it for the counts (a `Counter` of path -> opens); the real openers still do the work."""

    def __init__(self):
        self._real_gzip_open = gzip.open
        self.counts = Counter()
        self._stack = ExitStack()

    def _open_text(self, path):
        from sessionlog.store_files import open_text
        self.counts[str(path)] += 1
        return open_text(path)

    def _gzip_open(self, path, *args, **kwargs):
        self.counts[str(path)] += 1
        return self._real_gzip_open(path, *args, **kwargs)

    def __enter__(self):
        with ExitStack() as stack:  # a patch that fails to start undoes the ones before it
            stack.enter_context(mock.patch("gzip.open", self._gzip_open))
            stack.enter_context(mock.patch("sessionlog.omp_reader.open_text", self._open_text))
            self._stack = stack.pop_all()
        return self.counts

    def __exit__(self, *exception):
        return self._stack.__exit__(*exception)
