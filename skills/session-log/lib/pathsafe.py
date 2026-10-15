#!/usr/bin/env python3
# universal-session-log: managed
"""Safe filesystem primitives shared by the session-log bash entrypoints.

Every directory walk goes through :func:`open_directory`, which opens each path
component with ``O_NOFOLLOW`` so a symlink can never be traversed, and creates
missing components privately (mode 0o700) in a race-safe way. This module owns
the generic write/remove primitives. The specialized locked writers
(``claude_settings.py``, ``enable_flag.py``, ``locking.py``) keep their own
minimal O_NOFOLLOW walkers because their traversals raise domain-specific errors
and hold flock/mkdir locks; each is small and audited in its own module.
"""
import ctypes
import errno
import os
import random
import stat
import sys
from dataclasses import dataclass

DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
FILE_FLAGS = os.O_RDONLY | NOFOLLOW
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
MAX_TEMP_FILE_ATTEMPTS = 20
CONFLICT_NAME_BYTES = 8

DARWIN_RENAME_EXCL = 0x00000004
LINUX_RENAME_NOREPLACE = 0x00000001


@dataclass(frozen=True)
class ConflictRenameOptions:
    source_name: str
    prefix: str
    exhaust_message: str


class KeptAsideError(OSError):
    """An atomic replacement failed and left versions of the target beside it, under ``kept``: it raced another
    writer, or, when ``cause`` is set, it failed on that error of its own and could not put the target back."""

    def __init__(self, message, kept, cause=None):
        super().__init__(errno.EAGAIN, message)
        self.kept = tuple(kept)
        self.cause = cause


@dataclass(frozen=True)
class AtomicReplacement:
    dir_fd: object
    source_name: str
    target_name: str
    expected: object
    expected_source: object


def open_directory(path, *, create):
    """Return an fd for ``path`` while refusing every symlinked component.

    When ``create`` is true, missing components are created 0o700 race-safely;
    otherwise a missing component raises ``FileNotFoundError``.
    """
    fd = os.open(os.sep, DIR_FLAGS)
    try:
        for part in path.split(os.sep):
            if not part or part == ".":
                continue
            if part == "..":
                raise SystemExit(f"refusing parent traversal: {path}")
            try:
                nxt = os.open(part, DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, PRIVATE_DIR_MODE, dir_fd=fd)
                except FileExistsError:
                    pass
                nxt = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def ensure_directory(directory):
    fd = open_directory(directory, create=True)
    try:
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise SystemExit(f"refusing unsafe private directory: {directory}")
        os.fchmod(fd, PRIVATE_DIR_MODE)
    finally:
        os.close(fd)


def _is_safe_private_file_target(info):
    return not stat.S_ISLNK(info.st_mode) and stat.S_ISREG(info.st_mode)


def write_file(path, content):
    directory, name = os.path.split(path)
    parent_fd = open_directory(directory, create=True)
    temporary = None
    temporary_stat = None
    descriptor = None
    try:
        os.fchmod(parent_fd, PRIVATE_DIR_MODE)
        try:
            existing = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and not _is_safe_private_file_target(existing):
            raise SystemExit(f"refusing to overwrite unsafe private file: {path}")
        for _ in range(MAX_TEMP_FILE_ATTEMPTS):
            candidate = f".session-log.{os.getpid()}.{random.randrange(1 << 30):08x}"
            try:
                descriptor = os.open(
                    candidate,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW,
                    PRIVATE_FILE_MODE,
                    dir_fd=parent_fd,
                )
                temporary = candidate
                temporary_stat = os.fstat(descriptor)
                break
            except FileExistsError:
                continue
        else:
            raise SystemExit(f"cannot create temporary file in: {directory}")
        data = (content + "\n").encode()
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written == 0:
                raise OSError("private file write made no progress")
            offset += written
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
        os.fsync(descriptor)
        # Keep the inode alive through replacement and cleanup so a swapped path cannot reuse it.
        replace_file_no_replace(
            AtomicReplacement(parent_fd, temporary, name, existing, temporary_stat)
        )
        temporary = None
        os.fsync(parent_fd)
    finally:
        if temporary is not None:
            try:
                unlink_if_same_file(parent_fd, temporary, temporary_stat)
            except OSError:
                pass
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_fd)


def remove_path(target):
    directory, name = os.path.split(os.path.abspath(target))
    try:
        parent_fd = open_directory(directory, create=False)
    except FileNotFoundError:
        return
    try:
        try:
            current = os.lstat(name, dir_fd=parent_fd)
        except FileNotFoundError:
            return
        if stat.S_ISDIR(current.st_mode) and not stat.S_ISLNK(current.st_mode):
            os.rmdir(name, dir_fd=parent_fd)
        else:
            os.unlink(name, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)


def _rename_no_replace(dir_fd, source_name, target_name):
    if sys.platform == "darwin":
        function_name = "renameatx_np"
        flags = DARWIN_RENAME_EXCL
    elif sys.platform.startswith("linux"):
        function_name = "renameat2"
        flags = LINUX_RENAME_NOREPLACE
    else:
        raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable")
    library = ctypes.CDLL(None, use_errno=True)
    try:
        rename = getattr(library, function_name)
    except AttributeError as error:
        raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable") from error
    rename.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    rename.restype = ctypes.c_int
    result = rename(
        dir_fd,
        os.fsencode(source_name),
        dir_fd,
        os.fsencode(target_name),
        flags,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), target_name)


def move_no_replace(dir_fd, source_name, target_name):
    """Move an entry atomically without replacing an existing destination."""
    try:
        _rename_no_replace(dir_fd, source_name, target_name)
    except FileExistsError:
        return False
    return True


def _same_file_identity(current, expected):
    return current.st_dev == expected.st_dev and current.st_ino == expected.st_ino


def _is_untrusted_cleanup_directory(directory):
    # Same-UID writers share this trust boundary; otherwise require a private or sticky directory.
    mode = directory.st_mode
    return not (mode & stat.S_ISVTX) and (
        directory.st_uid != os.getuid()
        or mode & (stat.S_IWGRP | stat.S_IWOTH)
    )


def unlink_if_same_file(dir_fd, name, expected):
    if expected is None or expected.st_uid != os.getuid():
        return
    directory = os.fstat(dir_fd)
    if _is_untrusted_cleanup_directory(directory):
        return
    for attempt in range(MAX_TEMP_FILE_ATTEMPTS):
        candidate = (
            f".session-log-cleanup.{os.getpid()}.{random.randrange(1 << 30):08x}.{attempt}"
        )
        try:
            if not move_no_replace(dir_fd, name, candidate):
                continue
        except FileNotFoundError:
            return
        current = os.stat(candidate, dir_fd=dir_fd, follow_symlinks=False)
        if _same_file_identity(current, expected):
            os.unlink(candidate, dir_fd=dir_fd)
        return


def _install_absent_target(request):
    dir_fd = request.dir_fd
    if not move_no_replace(dir_fd, request.source_name, request.target_name):
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), request.target_name)
    installed = os.stat(request.target_name, dir_fd=dir_fd, follow_symlinks=False)
    if not _same_file_identity(installed, request.expected_source):
        conflict = rename_to_conflict(
            dir_fd,
            ConflictRenameOptions(
                source_name=request.target_name,
                prefix=request.source_name,
                exhaust_message="cannot preserve raced atomic replacement target",
            ),
        )
        raise KeptAsideError("temporary file changed during atomic replacement", (conflict,))


def _stage_replacement_backup(request):
    for attempt in range(MAX_TEMP_FILE_ATTEMPTS):
        candidate = (
            f".session-log-backup.{os.getpid()}.{random.randrange(1 << 30):08x}.{attempt}"
        )
        if move_no_replace(request.dir_fd, request.target_name, candidate):
            return candidate
    raise OSError(errno.EEXIST, "cannot stage file for atomic replacement", request.target_name)


def _install_replacement(request, backup_name, backup_stat):
    dir_fd = request.dir_fd
    if not _same_file_identity(backup_stat, request.expected):
        raise OSError(errno.EAGAIN, "target changed during atomic replacement", request.target_name)
    if not move_no_replace(dir_fd, request.source_name, request.target_name):
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), request.target_name)
    current = os.stat(request.target_name, dir_fd=dir_fd, follow_symlinks=False)
    if not _same_file_identity(current, request.expected_source):
        conflict = rename_to_conflict(
            dir_fd,
            ConflictRenameOptions(
                source_name=request.target_name,
                prefix=backup_name,
                exhaust_message="cannot preserve raced atomic replacement target",
            ),
        )
        raise KeptAsideError("temporary file changed during atomic replacement", (conflict,))


def _restore_backup(dir_fd, backup_name, target_name):
    """Move the staged backup back unless something took the target's place; True once it is gone from its name."""
    try:
        os.stat(target_name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        try:
            move_no_replace(dir_fd, backup_name, target_name)
        except OSError:
            pass
    try:
        os.stat(backup_name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return True
    return False


def _is_raced_replacement(error):
    """Whether _install_replacement failed because another writer changed the target or the temporary file: the
    target changed (EAGAIN, KeptAsideError too), the target came back (EEXIST), or rename_to_conflict found no free
    name for a raced target (SystemExit)."""
    return isinstance(error, SystemExit) or error.errno in (errno.EAGAIN, errno.EEXIST)


def _replace_present_target(request):
    dir_fd = request.dir_fd
    backup_name = _stage_replacement_backup(request)
    backup_stat = None
    try:
        backup_stat = os.stat(backup_name, dir_fd=dir_fd, follow_symlinks=False)
        _install_replacement(request, backup_name, backup_stat)
    except BaseException as error:
        if _restore_backup(dir_fd, backup_name, request.target_name) or not isinstance(error, (OSError, SystemExit)):
            raise
        raise KeptAsideError(
            "cannot restore the target after a failed atomic replacement",
            (*getattr(error, "kept", ()), backup_name),
            cause=None if _is_raced_replacement(error) else error,
        ) from error
    unlink_if_same_file(dir_fd, backup_name, backup_stat)


def replace_file_no_replace(request):
    current_source = os.stat(request.source_name, dir_fd=request.dir_fd, follow_symlinks=False)
    if not _same_file_identity(current_source, request.expected_source):
        raise OSError(errno.EAGAIN, "temporary file changed during atomic replacement", request.source_name)
    if request.expected is None:
        _install_absent_target(request)
    else:
        _replace_present_target(request)


def rename_to_conflict(dir_fd, options):
    for attempt in range(MAX_TEMP_FILE_ATTEMPTS):
        conflict = f"{options.prefix}.conflict.{attempt}"
        if move_no_replace(dir_fd, options.source_name, conflict):
            return conflict
    for _ in range(MAX_TEMP_FILE_ATTEMPTS):
        token = os.urandom(CONFLICT_NAME_BYTES).hex()
        conflict = f"{options.prefix}.conflict.{token}"
        if move_no_replace(dir_fd, options.source_name, conflict):
            return conflict
    raise SystemExit(options.exhaust_message)


def main(argv):
    if not argv:
        raise SystemExit("pathsafe: missing operation")
    operation = argv[0]
    path_variables = {
        "ensure-dir": "SESSION_LOG_DIRECTORY",
        "write-file": "SESSION_LOG_FILE",
        "remove": "SESSION_LOG_REMOVE_PATH",
        "is-untrusted-dir": "SESSION_LOG_DIRECTORY",
    }
    if operation not in path_variables:
        raise SystemExit(f"pathsafe: unknown operation: {operation}")
    path = os.environ[path_variables[operation]]
    try:
        if operation == "ensure-dir":
            ensure_directory(path)
        elif operation == "write-file":
            write_file(path, os.environ["SESSION_LOG_CONTENT"])
        elif operation == "remove":
            remove_path(path)
        else:
            # Exit status 0 when unlink_if_same_file leaves its files in that directory.
            untrusted = _is_untrusted_cleanup_directory(os.stat(path))
            raise SystemExit(0 if untrusted else 1)
    except OSError as error:
        raise SystemExit(f"pathsafe: {operation}: {path}: {error.strerror or error}") from None


if __name__ == "__main__":
    main(sys.argv[1:])
