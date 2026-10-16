#!/usr/bin/env python3
# universal-session-log: managed
"""mkdir-based per-harness install lock with owner-liveness reclaim.

The O_NOFOLLOW walk and owner bookkeeping of bin/session-log's install lock
live here in one auditable, testable place. Invoked as
``python3 locking.py acquire|release`` with the SESSION_LOG_LOCK_* env vars.
Exits TRANSIENT_FAILURE_EXIT_CODE while another live owner holds the lock; any other failure
exits 1 with a message naming it.
"""
import errno
import os
import subprocess
import sys
import time
import pathsafe
import process_identity

STALE_LOCK_SECONDS = 5
# EX_TEMPFAIL; adapter_install.sh (TRANSIENT_FAILURE_EXIT_CODE) makes it the exit status of a session-log command too.
TRANSIENT_FAILURE_EXIT_CODE = 75

mode = sys.argv[1]
lock_path = os.environ["SESSION_LOG_LOCK_PATH"]
owner_pid = int(os.environ["SESSION_LOG_LOCK_PID"])
owner_start = os.environ["SESSION_LOG_LOCK_START"]
directory, name = os.path.split(lock_path)


def read_owner(fd):
    descriptor = pathsafe.open_at(fd, "owner", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
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
    if process_identity.WINDOWS:
        # There os.kill(pid, 0) sends a CTRL_C_EVENT instead of probing; the start time alone tells.
        return process_identity.start_time(pid) == lines[1]
    try:
        os.kill(pid, 0)
    except OSError as error:
        return getattr(error, "errno", None) != errno.ESRCH
    try:
        actual = subprocess.check_output(["ps", "-p", str(pid), "-o", "lstart="], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return True
    return actual == lines[1] if actual else True


def broken(error):
    return SystemExit(f"session-log: cannot {mode} install lock {lock_path}: {error}")


try:
    parent_fd = pathsafe.open_directory(directory, create=True)
except OSError as error:
    raise broken(error)
lock_fd = None
reclaim_guard = None
try:
    if mode == "acquire":
        while True:
            try:
                pathsafe.mkdir_at(parent_fd, name, 0o700)
                lock_fd = pathsafe.open_child_directory(parent_fd, name)
                descriptor = pathsafe.open_at(lock_fd, "owner", os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
                try:
                    os.write(descriptor, ("%s\n%s\n" % (owner_pid, owner_start)).encode("utf-8"))
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                break
            except FileExistsError:
                if lock_fd is not None:
                    pathsafe.close_directory(lock_fd)
                    lock_fd = None
                # Reclaimers serialize on the parent directory so a lock judged stale cannot be swapped for a
                # competitor's fresh one before its removal; closing parent_fd (or the guard) at exit releases it.
                try:
                    if reclaim_guard is None:
                        reclaim_guard = pathsafe.lock_directory(parent_fd)
                except BlockingIOError:
                    raise SystemExit(TRANSIENT_FAILURE_EXIT_CODE)
                try:
                    lock_fd = pathsafe.open_child_directory(parent_fd, name)
                except FileNotFoundError:
                    continue
                try:
                    lines = read_owner(lock_fd)
                except FileNotFoundError:
                    lines = []
                if owner_alive(lines):
                    raise SystemExit(TRANSIENT_FAILURE_EXIT_CODE)
                age = time.time() - pathsafe.stat_directory(lock_fd).st_mtime
                if age < STALE_LOCK_SECONDS:
                    raise SystemExit(TRANSIENT_FAILURE_EXIT_CODE)
                try:
                    pathsafe.unlink_at(lock_fd, "owner")
                except FileNotFoundError:
                    pass
                pathsafe.close_directory(lock_fd)
                lock_fd = None
                pathsafe.rmdir_at(parent_fd, name)
    else:
        try:
            lock_fd = pathsafe.open_child_directory(parent_fd, name)
            lines = read_owner(lock_fd)
            if _matches_owner(lines, owner_pid, owner_start):
                pathsafe.unlink_at(lock_fd, "owner")
                pathsafe.close_directory(lock_fd)
                lock_fd = None
                pathsafe.rmdir_at(parent_fd, name)
        except (FileNotFoundError, NotADirectoryError):
            pass
except OSError as error:
    raise broken(error)
finally:
    if lock_fd is not None:
        pathsafe.close_directory(lock_fd)
    if reclaim_guard is not None:
        os.close(reclaim_guard)
    pathsafe.close_directory(parent_fd)
