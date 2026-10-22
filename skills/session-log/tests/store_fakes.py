"""Fakes for the file-system side of reader tests: directory entries whose queries fail or answer as chosen, and a `scandir` that
lists extra entries, fails for chosen folders or lists in reverse order."""
import os
from unittest import mock


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
