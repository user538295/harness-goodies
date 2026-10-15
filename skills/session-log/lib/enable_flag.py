#!/usr/bin/env python3
# universal-session-log: managed
"""Locked create/remove of the session-log enable flag.

Extracted verbatim from bin/session-log so the lock dance and O_NOFOLLOW
walk are auditable and testable in one place. Reads the SESSION_LOG_ENABLE_*
env vars; the action ("on"/"off") comes from SESSION_LOG_ENABLE_ACTION.
"""
import os
import stat
import secrets
import pathsafe


def _is_safe_owned_file(info):
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_nlink == 1
        and pathsafe.is_owned_by_user(info)
    )


lock_path = os.environ["SESSION_LOG_ENABLE_LOCK"]
flag_path = os.environ["SESSION_LOG_ENABLE_FLAG"]
action = os.environ["SESSION_LOG_ENABLE_ACTION"]
lock_flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
parent, name = os.path.split(flag_path)
lock_parent, lock_name = os.path.split(lock_path)
if lock_parent != parent:
    raise RuntimeError("session-log enable lock is outside the flag directory")
parent_fd = pathsafe.open_directory(parent, create=False)
lock_fd = pathsafe.open_at(parent_fd, lock_name, lock_flags, 0o600)
try:
    lock_stat = os.fstat(lock_fd)
    if not _is_safe_owned_file(lock_stat):
        raise RuntimeError("unsafe session-log enable lock")
    pathsafe.set_file_mode(lock_fd, 0o600)
    pathsafe.lock_file(lock_fd)
    try:
        try:
            current = pathsafe.stat_at(parent_fd, name)
        except FileNotFoundError:
            current = None
        if current is not None and not _is_safe_owned_file(current):
            raise RuntimeError("unsafe session-log enable flag")
        if action == "on":
            if current is None:
                descriptor = pathsafe.open_at(
                    parent_fd,
                    name,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_TRUNC
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                try:
                    pathsafe.set_file_mode(descriptor, 0o600)
                    os.write(descriptor, f"enabled:{secrets.token_hex(16)}\n".encode())
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            else:
                descriptor = pathsafe.open_at(
                    parent_fd,
                    name,
                    os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                )
                try:
                    actual = os.fstat(descriptor)
                    if actual.st_dev != current.st_dev or actual.st_ino != current.st_ino:
                        raise RuntimeError("session-log enable flag changed during update")
                    pathsafe.set_file_mode(descriptor, 0o600)
                finally:
                    os.close(descriptor)
        elif action == "off":
            if current is not None:
                pathsafe.unlink_at(parent_fd, name)
        else:
            raise RuntimeError(f"unknown enable action: {action}")
    finally:
        pathsafe.close_directory(parent_fd)
finally:
    # Closing the lock releases it.
    os.close(lock_fd)
