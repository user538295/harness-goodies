#!/usr/bin/env python3
# universal-session-log: managed
"""Safe filesystem primitives shared by the session-log bash entrypoints.

Every directory walk goes through :func:`open_directory`, which opens each path
component with ``O_NOFOLLOW`` so a symlink can never be traversed, and creates
missing components privately (mode 0o700) in a race-safe way. This module owns
the generic link/write/remove primitives. The specialized locked writers
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
from dataclasses import dataclass, field
from typing import Optional

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
class AdapterLinkOptions:
    source: str
    target: str
    store: str


@dataclass(frozen=True)
class ConflictRenameOptions:
    source_name: str
    prefix: str
    exhaust_message: str


@dataclass(frozen=True)
class AtomicReplacement:
    dir_fd: object
    source_name: str
    target_name: str
    expected: object
    expected_source: object


@dataclass(frozen=True)
class TemporaryEntry:
    name: str


@dataclass(frozen=True)
class BackupEntry:
    name: str


@dataclass
class ReplacementResources:
    temporary: Optional[TemporaryEntry] = None
    backup: Optional[BackupEntry] = None


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
        try:
            data = (content + "\n").encode()
            offset = 0
            while offset < len(data):
                written = os.write(descriptor, data[offset:])
                if written == 0:
                    raise OSError("private file write made no progress")
                offset += written
            os.fchmod(descriptor, PRIVATE_FILE_MODE)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
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


def _read_all(fd):
    chunks = []
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _classify(dir_fd, name, target):
    try:
        item = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(item.st_mode):
        return ("link", os.readlink(name, dir_fd=dir_fd))
    if not stat.S_ISREG(item.st_mode):
        raise SystemExit(f"refusing to overwrite non-file adapter target: {target}")
    descriptor = os.open(name, FILE_FLAGS, dir_fd=dir_fd)
    try:
        return ("file", _read_all(descriptor))
    finally:
        os.close(descriptor)


def _is_safe_regular_file(info):
    return stat.S_ISREG(info.st_mode) and info.st_nlink == 1


def _is_managed_release_link(existing, store):
    return existing.startswith(f"{store}/releases/")


def _create_temporary_link(target_dir_fd, directory, source):
    for _ in range(MAX_TEMP_FILE_ATTEMPTS):
        candidate = f".session-log.{os.getpid()}.{random.randrange(1 << 30):08x}"
        try:
            os.symlink(source, candidate, dir_fd=target_dir_fd)
            return candidate
        except FileExistsError:
            continue
    raise SystemExit(f"cannot create temporary link in: {directory}")


@dataclass
class AdapterLinkContext:
    source: str
    target: str
    name: str
    target_dir_fd: object
    initial: object = None
    state: ReplacementResources = field(default_factory=ReplacementResources)


def _stage_adapter_backup(context):
    state = context.state
    for attempt in range(MAX_TEMP_FILE_ATTEMPTS):
        candidate = (
            f".session-log-backup.{os.getpid()}.{random.randrange(1 << 30):08x}.{attempt}"
        )
        if move_no_replace(context.target_dir_fd, context.name, candidate):
            state.backup = BackupEntry(candidate)
            break
    if state.backup is None:
        raise SystemExit(f"cannot stage adapter target: {context.target}")
    if _classify(context.target_dir_fd, state.backup.name, context.target) != context.initial:
        if move_no_replace(context.target_dir_fd, state.backup.name, context.name):
            state.backup = None
        raise SystemExit(f"adapter target changed during update: {context.target}")


def _replace_adapter_target(context):
    state = context.state
    if context.initial is None:
        os.unlink(state.temporary.name, dir_fd=context.target_dir_fd)
        state.temporary = None
        try:
            os.symlink(context.source, context.name, dir_fd=context.target_dir_fd)
        except FileExistsError:
            raise SystemExit(f"adapter target changed during update: {context.target}")
        return
    _stage_adapter_backup(context)
    os.unlink(state.temporary.name, dir_fd=context.target_dir_fd)
    state.temporary = None
    try:
        os.symlink(context.source, context.name, dir_fd=context.target_dir_fd)
    except FileExistsError:
        message = f"adapter target changed during update: {context.target}"
        rename_to_conflict(
            context.target_dir_fd,
            ConflictRenameOptions(
                source_name=context.name,
                prefix=state.backup.name,
                exhaust_message=message,
            ),
        )
        if move_no_replace(context.target_dir_fd, state.backup.name, context.name):
            state.backup = None
        raise SystemExit(message)
    os.unlink(state.backup.name, dir_fd=context.target_dir_fd)
    state.backup = None


def _cleanup_adapter_link(context):
    state = context.state
    if state.temporary is not None:
        try:
            os.unlink(state.temporary.name, dir_fd=context.target_dir_fd)
        except OSError:
            pass
    if state.backup is not None:
        try:
            os.stat(context.name, dir_fd=context.target_dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            try:
                if move_no_replace(context.target_dir_fd, state.backup.name, context.name):
                    state.backup = None
            except OSError:
                pass


def link_adapter(options):
    """Atomically point the target at the source without clobbering unowned files."""
    source, target, store = options.source, options.target, options.store
    directory, name = os.path.split(target)
    source_directory, source_name = os.path.split(source)
    source_dir_fd = open_directory(source_directory, create=True)
    try:
        target_dir_fd = open_directory(directory, create=True)
        context = AdapterLinkContext(
            source=source, target=target, name=name, target_dir_fd=target_dir_fd
        )
        source_fd = None
        try:
            source_fd = os.open(source_name, FILE_FLAGS, dir_fd=source_dir_fd)
            source_stat = os.fstat(source_fd)
            if not _is_safe_regular_file(source_stat):
                raise SystemExit(f"adapter asset is not a safe regular file: {source}")
            source_data = _read_all(source_fd)
            initial = _classify(target_dir_fd, name, target)
            context.initial = initial
            if initial is not None and initial[0] == "link":
                existing = initial[1]
                if existing == source:
                    raise SystemExit(0)
                if not _is_managed_release_link(existing, store):
                    raise SystemExit(f"refusing to overwrite unowned adapter link: {target}")
            elif initial is not None and initial[1] != source_data:
                raise SystemExit(f"refusing to overwrite unowned adapter file: {target}")
            context.state.temporary = TemporaryEntry(
                _create_temporary_link(target_dir_fd, directory, source)
            )
            if _classify(target_dir_fd, name, target) != initial:
                raise SystemExit(f"adapter target changed during update: {target}")
            _replace_adapter_target(context)
            os.fsync(target_dir_fd)
        finally:
            if source_fd is not None:
                os.close(source_fd)
            _cleanup_adapter_link(context)
            os.close(target_dir_fd)
    finally:
        os.close(source_dir_fd)


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
        rename_to_conflict(
            dir_fd,
            ConflictRenameOptions(
                source_name=request.target_name,
                prefix=request.source_name,
                exhaust_message="cannot preserve raced atomic replacement target",
            ),
        )
        raise OSError(
            errno.EAGAIN,
            "temporary file changed during atomic replacement",
            request.target_name,
        )


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
        rename_to_conflict(
            dir_fd,
            ConflictRenameOptions(
                source_name=request.target_name,
                prefix=backup_name,
                exhaust_message="cannot preserve raced atomic replacement target",
            ),
        )
        raise OSError(errno.EAGAIN, "temporary file changed during atomic replacement", request.target_name)


def _replace_present_target(request):
    dir_fd = request.dir_fd
    backup_name = _stage_replacement_backup(request)
    installed = False
    backup_stat = None
    try:
        backup_stat = os.stat(backup_name, dir_fd=dir_fd, follow_symlinks=False)
        _install_replacement(request, backup_name, backup_stat)
        installed = True
    finally:
        if installed:
            unlink_if_same_file(dir_fd, backup_name, backup_stat)
        else:
            try:
                os.stat(request.target_name, dir_fd=dir_fd, follow_symlinks=False)
            except FileNotFoundError:
                try:
                    move_no_replace(dir_fd, backup_name, request.target_name)
                except OSError:
                    pass


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
    if operation == "ensure-dir":
        ensure_directory(os.environ["SESSION_LOG_DIRECTORY"])
    elif operation == "write-file":
        write_file(os.environ["SESSION_LOG_FILE"], os.environ["SESSION_LOG_CONTENT"])
    elif operation == "remove":
        remove_path(os.environ["SESSION_LOG_REMOVE_PATH"])
    elif operation == "link-adapter":
        link_adapter(
            AdapterLinkOptions(
                source=os.environ["SESSION_LOG_SOURCE"],
                target=os.environ["SESSION_LOG_TARGET"],
                store=os.environ["SESSION_LOG_STORE"],
            )
        )
    else:
        raise SystemExit(f"pathsafe: unknown operation: {operation}")


if __name__ == "__main__":
    main(sys.argv[1:])
