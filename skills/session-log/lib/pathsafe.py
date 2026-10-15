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

The ``*_at`` functions are the one way every module names an entry inside a
directory handle. On macOS and Linux a handle is a directory descriptor and each
name resolves relative to it (``dir_fd``), so no component can be swapped for a
symlink between a check and its use. Windows has no ``dir_fd``: a handle there is
the directory's path, every component of which :func:`open_directory` checked is
not a reparse point (symlink or junction), and each name is resolved again
through that path. A component swapped after that check goes unnoticed, but only
a program running as the same user can swap one in the user's profile, and the
POSIX design trusts same-user writers too (see ``_is_untrusted_cleanup_directory``).
Windows keeps files private by the profile's access control list, not by mode
bits and owner ids, so the mode and owner checks apply only where ``os.getuid``
exists.
"""
import ctypes
import errno
import os
import random
import re
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
DIR_FD = os.open in os.supports_dir_fd
POSIX_OWNERSHIP = hasattr(os, "getuid")
# Windows takes "/" besides "\"; on POSIX a backslash is an ordinary file name character.
PATH_SEPARATORS = re.compile("[" + re.escape(os.sep + (os.altsep or "")) + "]")
DIRECTORY_LOCK_NAME = ".session-log.directory-lock"


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
    """Return a directory handle for ``path`` while refusing every symlinked component.

    When ``create`` is true, missing components are created 0o700 race-safely;
    otherwise a missing component raises ``FileNotFoundError``.
    """
    drive, rest = os.path.splitdrive(path)
    parts = [part for part in PATH_SEPARATORS.split(rest) if part and part != "."]
    if ".." in parts:
        raise SystemExit(f"refusing parent traversal: {path}")
    fd = os.open(os.sep, DIR_FLAGS) if DIR_FD else drive + os.sep
    try:
        for part in parts:
            try:
                nxt = open_child_directory(fd, part)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    mkdir_at(fd, part, PRIVATE_DIR_MODE)
                except FileExistsError:
                    pass
                nxt = open_child_directory(fd, part)
            close_directory(fd)
            fd = nxt
        return fd
    except BaseException:
        close_directory(fd)
        raise


def _entry(directory, name):
    """``(path, dir_fd)`` that name entry ``name`` of directory handle ``directory`` in an os call."""
    return (name, directory) if DIR_FD else (os.path.join(directory, name), None)


def open_child_directory(directory, name):
    """The handle of subdirectory ``name``; a symlink, a junction, or a non-directory raises OSError."""
    if DIR_FD:
        return os.open(name, DIR_FLAGS, dir_fd=directory)
    path = os.path.join(directory, name)
    info = os.lstat(path)
    _refuse_reparse_point(info, path)
    if not stat.S_ISDIR(info.st_mode):
        raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), path)
    return path


def _refuse_reparse_point(info, path):
    """Windows: raise as O_NOFOLLOW does when ``info`` is a symlink or a junction. Other reparse points (cloud
    file placeholders, deduplicated files) are no links, so they pass."""
    if stat.S_ISLNK(info.st_mode) or info.st_reparse_tag in (stat.IO_REPARSE_TAG_SYMLINK,
                                                             stat.IO_REPARSE_TAG_MOUNT_POINT):
        raise OSError(errno.ELOOP, "refusing a symbolic link or junction", path)


if not DIR_FD:
    from ctypes import wintypes
    import msvcrt

    _GENERIC_READ, _GENERIC_WRITE = 0x80000000, 0x40000000
    # FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE: as on POSIX, others may rename or remove an open file.
    _SHARE_ALL = 0x7
    _CREATE_NEW, _CREATE_ALWAYS, _OPEN_EXISTING, _OPEN_ALWAYS, _TRUNCATE_EXISTING = 1, 2, 3, 4, 5
    _FILE_ATTRIBUTE_NORMAL = 0x80
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    def _windows_open(path, flags):
        """os.open for Windows that lets other handles rename or remove the file and never follows a reparse
        point at ``path``; the CRT's own open allows neither."""
        access = {os.O_RDONLY: _GENERIC_READ, os.O_WRONLY: _GENERIC_WRITE,
                  os.O_RDWR: _GENERIC_READ | _GENERIC_WRITE}[flags & (os.O_RDONLY | os.O_WRONLY | os.O_RDWR)]
        if flags & os.O_CREAT:
            disposition = (_CREATE_NEW if flags & os.O_EXCL else _CREATE_ALWAYS if flags & os.O_TRUNC
                           else _OPEN_ALWAYS)
        else:
            disposition = _TRUNCATE_EXISTING if flags & os.O_TRUNC else _OPEN_EXISTING
        handle = _kernel32.CreateFileW(path, access, _SHARE_ALL, None, disposition,
                                       _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OPEN_REPARSE_POINT, None)
        if handle == _INVALID_HANDLE_VALUE:
            error = ctypes.get_last_error()
            raise OSError(None, ctypes.FormatError(error), path, error)
        try:
            fd = msvcrt.open_osfhandle(handle, flags & (os.O_APPEND | os.O_RDONLY | os.O_WRONLY | os.O_RDWR))
        except BaseException:
            _kernel32.CloseHandle(handle)
            raise
        try:
            _refuse_reparse_point(os.fstat(fd), path)
        except BaseException:
            os.close(fd)
            raise
        return fd


def close_directory(directory):
    if DIR_FD:
        os.close(directory)


def stat_directory(directory):
    return os.fstat(directory) if DIR_FD else os.stat(directory)


def chmod_directory(directory, mode):
    if POSIX_OWNERSHIP:
        os.fchmod(directory, mode)


def sync_directory(directory):
    if DIR_FD:
        os.fsync(directory)


def open_at(directory, name, flags, mode=0o777):
    """os.open of entry ``name``; on Windows ``mode`` is ignored and a reparse point there is never followed."""
    if not DIR_FD:
        return _windows_open(os.path.join(directory, name), flags)
    return os.open(name, flags, mode, dir_fd=directory)


def stat_at(directory, name):
    """The status of entry ``name`` itself: a symlink is never followed."""
    path, dir_fd = _entry(directory, name)
    return os.stat(path, dir_fd=dir_fd, follow_symlinks=False)


def mkdir_at(directory, name, mode):
    path, dir_fd = _entry(directory, name)
    os.mkdir(path, mode, dir_fd=dir_fd)


def rmdir_at(directory, name):
    path, dir_fd = _entry(directory, name)
    os.rmdir(path, dir_fd=dir_fd)


def unlink_at(directory, name):
    path, dir_fd = _entry(directory, name)
    os.unlink(path, dir_fd=dir_fd)


def readlink_at(directory, name):
    path, dir_fd = _entry(directory, name)
    return os.readlink(path, dir_fd=dir_fd)


def link_at(directory, source_name, target_name):
    """Hard-link ``source_name`` as ``target_name``; FileExistsError when the target exists."""
    source, source_fd = _entry(directory, source_name)
    target, target_fd = _entry(directory, target_name)
    os.link(source, target, src_dir_fd=source_fd, dst_dir_fd=target_fd, follow_symlinks=False)


def set_file_mode(fd, mode):
    if POSIX_OWNERSHIP:
        os.fchmod(fd, mode)


def is_owned_by_user(info):
    return not POSIX_OWNERSHIP or info.st_uid == os.getuid()


def has_private_mode(info):
    """Whether only the owner may access the file; Windows has no such mode bits to check."""
    return not POSIX_OWNERSHIP or not info.st_mode & (stat.S_IRWXG | stat.S_IRWXO)


def lock_file(fd, *, blocking=True):
    """Lock open file ``fd`` exclusively until it is closed. BlockingIOError when ``blocking`` is false and
    another process holds the lock."""
    if sys.platform != "win32":
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        return
    import msvcrt

    # Locks the first byte, which may lie past the end of the file.
    os.lseek(fd, 0, os.SEEK_SET)
    while True:
        try:
            msvcrt.locking(fd, msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            return
        except OSError as error:
            # Another process holds the lock (EACCES), or LK_LOCK gave up after 10 seconds (EDEADLK) where
            # flock waits as long as it takes.
            if error.errno not in (errno.EACCES, errno.EDEADLK):
                raise
            if not blocking:
                raise BlockingIOError(errno.EAGAIN, "file is locked by another process") from error


def lock_directory(directory):
    """Lock directory handle ``directory`` against other processes without waiting (BlockingIOError when one
    holds it). Returns None where closing ``directory`` releases the lock; Windows cannot lock a directory, so
    there it locks DIRECTORY_LOCK_NAME inside it and returns that file's descriptor, whose closing releases it."""
    if DIR_FD:
        lock_file(directory, blocking=False)
        return None
    fd = open_at(directory, DIRECTORY_LOCK_NAME, os.O_RDWR | os.O_CREAT, PRIVATE_FILE_MODE)
    try:
        lock_file(fd, blocking=False)
    except BaseException:
        os.close(fd)
        raise
    return fd


def ensure_directory(directory):
    fd = open_directory(directory, create=True)
    try:
        info = stat_directory(fd)
        if not stat.S_ISDIR(info.st_mode) or not is_owned_by_user(info):
            raise SystemExit(f"refusing unsafe private directory: {directory}")
        chmod_directory(fd, PRIVATE_DIR_MODE)
    finally:
        close_directory(fd)


def _is_safe_private_file_target(info):
    return not stat.S_ISLNK(info.st_mode) and stat.S_ISREG(info.st_mode)


def write_file(path, content):
    directory, name = os.path.split(path)
    parent_fd = open_directory(directory, create=True)
    temporary = None
    temporary_stat = None
    descriptor = None
    try:
        chmod_directory(parent_fd, PRIVATE_DIR_MODE)
        try:
            existing = stat_at(parent_fd, name)
        except FileNotFoundError:
            existing = None
        if existing is not None and not _is_safe_private_file_target(existing):
            raise SystemExit(f"refusing to overwrite unsafe private file: {path}")
        for _ in range(MAX_TEMP_FILE_ATTEMPTS):
            candidate = f".session-log.{os.getpid()}.{random.randrange(1 << 30):08x}"
            try:
                descriptor = open_at(
                    parent_fd,
                    candidate,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW,
                    PRIVATE_FILE_MODE,
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
        set_file_mode(descriptor, PRIVATE_FILE_MODE)
        os.fsync(descriptor)
        # Keep the inode alive through replacement and cleanup so a swapped path cannot reuse it.
        replace_file_no_replace(
            AtomicReplacement(parent_fd, temporary, name, existing, temporary_stat)
        )
        temporary = None
        sync_directory(parent_fd)
    finally:
        if temporary is not None:
            try:
                unlink_if_same_file(parent_fd, temporary, temporary_stat)
            except OSError:
                pass
        if descriptor is not None:
            os.close(descriptor)
        close_directory(parent_fd)


def remove_path(target):
    directory, name = os.path.split(os.path.abspath(target))
    try:
        parent_fd = open_directory(directory, create=False)
    except FileNotFoundError:
        return
    try:
        try:
            current = stat_at(parent_fd, name)
        except FileNotFoundError:
            return
        if stat.S_ISDIR(current.st_mode) and not stat.S_ISLNK(current.st_mode):
            rmdir_at(parent_fd, name)
        else:
            unlink_at(parent_fd, name)
    finally:
        close_directory(parent_fd)


def _rename_no_replace(dir_fd, source_name, target_name):
    if not DIR_FD:
        # Windows rename (MoveFileEx without MOVEFILE_REPLACE_EXISTING) never replaces its target.
        os.rename(os.path.join(dir_fd, source_name), os.path.join(dir_fd, target_name))
        return
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
    if not POSIX_OWNERSHIP:
        return False
    mode = directory.st_mode
    return not (mode & stat.S_ISVTX) and (
        directory.st_uid != os.getuid()
        or mode & (stat.S_IWGRP | stat.S_IWOTH)
    )


def unlink_if_same_file(dir_fd, name, expected):
    if expected is None or not is_owned_by_user(expected):
        return
    directory = stat_directory(dir_fd)
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
        current = stat_at(dir_fd, candidate)
        if _same_file_identity(current, expected):
            unlink_at(dir_fd, candidate)
        return


def _install_absent_target(request):
    dir_fd = request.dir_fd
    if not move_no_replace(dir_fd, request.source_name, request.target_name):
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), request.target_name)
    installed = stat_at(dir_fd, request.target_name)
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
    current = stat_at(dir_fd, request.target_name)
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
        stat_at(dir_fd, target_name)
    except FileNotFoundError:
        try:
            move_no_replace(dir_fd, backup_name, target_name)
        except OSError:
            pass
    try:
        stat_at(dir_fd, backup_name)
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
        backup_stat = stat_at(dir_fd, backup_name)
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
    current_source = stat_at(request.dir_fd, request.source_name)
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
