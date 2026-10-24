"""Publishing a file through an exclusive temp and a rename, shared by the archive writer (C3) and the Cursor hook records (C5): a
reader never sees half a file, and a temp a killed run left behind is recognized by its name and removed once it is stale."""
import os
import re
from typing import Pattern

STALE_TEMP_SECONDS = 3600
FILE_MODE = 0o600
TEMP_RANDOM_BYTES = 4
TEMP_SUFFIX = ".tmp"
# What follows the name of the file a temp is for: its pid, a random part and the suffix (see `temp_name`).
STALE_TEMP_TAIL = r"\.[0-9]+\.[0-9a-f]+" + re.escape(TEMP_SUFFIX)
_TEMP_NAME = ".%s.%d.%s" + TEMP_SUFFIX


def temp_name(name: str, pid: int) -> str:
    """The unique temp name of the file `name` written by process `pid`."""
    return _TEMP_NAME % (name, pid, os.urandom(TEMP_RANDOM_BYTES).hex())


def discard(path: str) -> None:
    """Removes `path`, ignoring a failure."""
    try:
        os.unlink(path)
    except OSError:
        pass


def publish(temp: str, path: str, content: bytes) -> None:
    """Writes `content` to the new file `temp` (created exclusively, mode `FILE_MODE`), flushes it to disk and renames it to
    `path`. A failure removes the temp and is raised."""
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
    try:
        try:
            handle = os.fdopen(descriptor, "wb")
        except BaseException:
            os.close(descriptor)
            raise
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        discard(temp)
        raise


def remove_stale_temps(folder: str, name_pattern: Pattern[str], now_seconds: float) -> None:
    """Removes the files of `folder` whose whole name matches `name_pattern` and that are older than `STALE_TEMP_SECONDS` at
    `now_seconds` (epoch); a younger one may belong to a run still going. A folder that cannot be listed or a file that cannot be
    removed is skipped. No link is followed."""
    try:
        names = os.listdir(folder)
    except OSError:
        return
    cutoff = now_seconds - STALE_TEMP_SECONDS
    for name in names:
        if not name_pattern.fullmatch(name):
            continue
        path = os.path.join(folder, name)
        try:
            if os.lstat(path).st_mtime < cutoff:
                os.unlink(path)
        except OSError:
            continue
