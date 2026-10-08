#!/usr/bin/env python3
# universal-session-log: managed
"""mkdir-based package installation lock with owner-liveness reclaim.

Extracted verbatim from install.sh so the O_NOFOLLOW walk and owner
bookkeeping live in one auditable, testable place. Invoked as
``python3 locking.py acquire|release`` with the SESSION_LOG_LOCK_* env vars.
"""
import errno
import fcntl
import os
import subprocess
import sys
import time
import pathsafe

STALE_LOCK_SECONDS = 5

mode = sys.argv[1]
lock_path = os.environ["SESSION_LOG_LOCK_PATH"]
owner_pid = int(os.environ["SESSION_LOG_LOCK_PID"])
owner_start = os.environ["SESSION_LOG_LOCK_START"]
directory, name = os.path.split(lock_path)
flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


def read_owner(fd):
    descriptor = os.open("owner", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=fd)
    try:
        return os.read(descriptor, 512).decode("utf-8").splitlines()
    finally:
        os.close(descriptor)


def _has_liveness_data(lines):
    return len(lines) >= 2 and lines[0].isdigit() and bool(lines[1])


def _matches_owner(lines, pid, process_start):
    return len(lines) >= 2 and lines[0] == str(pid) and lines[1] == process_start


def owner_alive(lines):
    if not _has_liveness_data(lines):
        return None
    pid = int(lines[0])
    try:
        os.kill(pid, 0)
    except OSError as error:
        return getattr(error, "errno", None) != errno.ESRCH
    try:
        actual = subprocess.check_output(["ps", "-p", str(pid), "-o", "lstart="], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return True
    return actual == lines[1] if actual else True


parent_fd = pathsafe.open_directory(directory, create=True)
lock_fd = None
try:
    if mode == "acquire":
        while True:
            try:
                os.mkdir(name, 0o700, dir_fd=parent_fd)
                lock_fd = os.open(name, flags, dir_fd=parent_fd)
                descriptor = os.open("owner", os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=lock_fd)
                try:
                    os.write(descriptor, ("%s\n%s\n" % (owner_pid, owner_start)).encode("utf-8"))
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                break
            except FileExistsError:
                if lock_fd is not None:
                    os.close(lock_fd)
                    lock_fd = None
                # Reclaimers serialize on the parent directory so a lock judged stale cannot be swapped for a
                # competitor's fresh one before its removal; closing parent_fd at exit releases the guard.
                try:
                    fcntl.flock(parent_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise SystemExit(1)
                try:
                    lock_fd = os.open(name, flags, dir_fd=parent_fd)
                except FileNotFoundError:
                    continue
                try:
                    lines = read_owner(lock_fd)
                except FileNotFoundError:
                    lines = []
                if owner_alive(lines):
                    raise SystemExit(1)
                age = time.time() - os.fstat(lock_fd).st_mtime
                if age < STALE_LOCK_SECONDS:
                    raise SystemExit(1)
                try:
                    os.unlink("owner", dir_fd=lock_fd)
                except FileNotFoundError:
                    pass
                os.close(lock_fd)
                lock_fd = None
                os.rmdir(name, dir_fd=parent_fd)
    else:
        try:
            lock_fd = os.open(name, flags, dir_fd=parent_fd)
            lines = read_owner(lock_fd)
            if _matches_owner(lines, owner_pid, owner_start):
                os.unlink("owner", dir_fd=lock_fd)
                os.close(lock_fd)
                lock_fd = None
                os.rmdir(name, dir_fd=parent_fd)
        except (FileNotFoundError, NotADirectoryError):
            pass
finally:
    if lock_fd is not None:
        os.close(lock_fd)
    os.close(parent_fd)
