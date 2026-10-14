#!/usr/bin/env python3
# universal-session-log: managed
"""Removal of the package copies older install.sh releases seeded into harness skill roots.

Usage:
    python3 legacy_package.py remove <skill-root>/session-log   # HOME

A copy goes only when its ownership manifest proves every file in it: each regular file is
the manifest itself or an asset the manifest lists with the same SHA-256, apart from the
bytecode Python writes beside a module (``__pycache__/*.pyc``) and macOS Finder metadata
(``.DS_Store``). A symlink in the copy's place goes only when it points into a claude-goodies
plugin cache or the release store of older releases, which is where those releases pointed it;
its target is never touched. No symlink is ever followed, including a symlinked skill root.

Prints ``removed`` or ``removed link``; ``kept: <reason>`` when the copy carries session-log's
ownership marks but cannot be proven unchanged or cannot be read; nothing when the path is not
session-log's.
"""
import errno
import fnmatch
import hashlib
import os
import stat
import sys

import pathsafe

MANIFEST = ".universal-session-log.manifest"
MANIFEST_HEADER = ["format=1", "owner=universal-session-log"]
MANIFEST_VERSION_PREFIX = "version="
ASSET_PREFIX = "asset="
DIGEST_PREFIX = "sha256="
SKILL_FILE = "SKILL.md"
SKILL_OWNERSHIP_LINE = "<!-- universal-session-log: managed -->"
BYTECODE_DIRECTORY = "__pycache__"
BYTECODE_SUFFIX = ".pyc"
FINDER_METADATA = ".DS_Store"
RESULT_REMOVED = "removed"
RESULT_REMOVED_LINK = "removed link"
RESULT_KEPT_PREFIX = "kept: "
# Where older releases pointed a skill-root symlink, below the home directory.
LINK_DESTINATIONS = (
    ".claude/plugins/cache/*/claude-goodies/*/skills/session-log",
    ".local/share/universal-session-log/releases/*/skills/session-log",
)
# Errors that mean the path is absent or reached only through a symlink: not session-log's.
NOT_REACHABLE = (errno.ENOENT, errno.ENOTDIR, errno.ELOOP)


class Kept(Exception):
    """The copy carries session-log's ownership marks but cannot be proven unchanged."""


def _identity(info):
    """What must stay the same between a file's check and its removal."""
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _read_regular(dir_fd, name):
    """The contents of regular file ``name``; never follows a symlink or blocks on a FIFO."""
    flags = pathsafe.FILE_FLAGS | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(name, flags, dir_fd=dir_fd), "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise OSError(errno.EINVAL, "not a regular file", name)
        return handle.read()


def _manifest_assets(copy_fd):
    """The ``{relative path: sha256}`` the copy's manifest lists, or None when it has none."""
    try:
        lines = _read_regular(copy_fd, MANIFEST).decode("utf-8").splitlines()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as error:
        raise Kept("its ownership manifest is invalid") from error
    header, version, pairs = lines[:2], lines[2:3], lines[3:]
    if header != MANIFEST_HEADER or not version or not version[0].startswith(MANIFEST_VERSION_PREFIX) or len(pairs) % 2:
        raise Kept("its ownership manifest is invalid")
    assets = {}
    for asset, digest in zip(pairs[::2], pairs[1::2]):
        if not asset.startswith(ASSET_PREFIX) or not digest.startswith(DIGEST_PREFIX):
            raise Kept("its ownership manifest is invalid")
        assets[asset[len(ASSET_PREFIX):]] = digest[len(DIGEST_PREFIX):]
    return assets


def _is_tolerated(relative):
    """Files a copy gains without anyone editing it: Python bytecode and Finder metadata."""
    directory, _, name = relative.rpartition("/")
    is_bytecode = directory.split("/")[-1] == BYTECODE_DIRECTORY and name.endswith(BYTECODE_SUFFIX)
    return is_bytecode or name == FINDER_METADATA


def _check_file(dir_fd, name, relative, assets):
    if relative == MANIFEST or _is_tolerated(relative):
        return
    if relative not in assets:
        raise Kept(f"{relative} is not a file session-log installed")
    if hashlib.sha256(_read_regular(dir_fd, name)).hexdigest() != assets[relative]:
        raise Kept(f"{relative} was changed")


def _inventory(dir_fd, prefix, assets):
    """``{relative path: identity}`` of every file below ``dir_fd`` once each is proven session-log's."""
    checked = {}
    for name in sorted(os.listdir(dir_fd)):
        relative = prefix + name
        try:
            info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child_fd = os.open(name, pathsafe.DIR_FLAGS, dir_fd=dir_fd)
                try:
                    checked.update(_inventory(child_fd, relative + "/", assets))
                finally:
                    os.close(child_fd)
                continue
            if not stat.S_ISREG(info.st_mode):
                raise Kept(f"{relative} is not a file session-log installed")
            _check_file(dir_fd, name, relative, assets)
        except OSError as error:
            raise Kept(f"cannot read {relative}: {error.strerror}") from error
        checked[relative] = _identity(info)
    return checked


def _remove_tree(dir_fd, prefix, checked):
    """Remove everything below ``dir_fd`` that is unchanged since its check, through one-component
    descriptors only; the top-level manifest stays for the caller to remove last."""
    for name in sorted(os.listdir(dir_fd)):
        relative = prefix + name
        if relative == MANIFEST:
            continue
        info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child_fd = os.open(name, pathsafe.DIR_FLAGS, dir_fd=dir_fd)
            try:
                _remove_tree(child_fd, relative + "/", checked)
            finally:
                os.close(child_fd)
            os.rmdir(name, dir_fd=dir_fd)
        elif checked.get(relative) == _identity(info):
            os.unlink(name, dir_fd=dir_fd)
        else:
            raise Kept(f"{relative} changed during removal")


def _has_ownership_marker(copy_fd):
    try:
        return SKILL_OWNERSHIP_LINE in _read_regular(copy_fd, SKILL_FILE).decode("utf-8", "replace").splitlines()
    except FileNotFoundError:
        return False
    except OSError as error:
        raise Kept(f"cannot read {SKILL_FILE}: {error.strerror}") from error


def _recognized_link(destination, home):
    if "/../" in destination + "/" or "/./" in destination + "/":
        return False
    homes = {home.rstrip("/"), os.path.realpath(home)}
    return any(
        fnmatch.fnmatchcase(destination.rstrip("/"), f"{root}/{pattern}")
        for root in homes
        for pattern in LINK_DESTINATIONS
    )


def _remove_copy(parent_fd, name):
    copy_fd = os.open(name, pathsafe.DIR_FLAGS, dir_fd=parent_fd)
    try:
        assets = _manifest_assets(copy_fd)
        if assets is None:
            if _has_ownership_marker(copy_fd):
                raise Kept("it has no ownership manifest")
            return ""
        checked = _inventory(copy_fd, "", assets)
        try:
            _remove_tree(copy_fd, "", checked)
            if checked.get(MANIFEST) != _identity(os.stat(MANIFEST, dir_fd=copy_fd, follow_symlinks=False)):
                raise Kept(f"{MANIFEST} changed during removal")
            os.unlink(MANIFEST, dir_fd=copy_fd)
        except OSError as error:
            raise Kept(f"cannot remove {error.filename or name}: {error.strerror}") from error
    finally:
        os.close(copy_fd)
    try:
        os.rmdir(name, dir_fd=parent_fd)
    except OSError as error:
        raise Kept(f"cannot remove it: {error.strerror}") from error
    return RESULT_REMOVED


def remove(path, home):
    """Remove the seeded copy (or recognized symlink) at ``path`` when it is proven session-log's.

    Never follows a symlink. Returns ``removed``, ``removed link``, ``kept: <reason>``, or an
    empty string when ``path`` is absent, reached only through a symlink, or not session-log's.
    ``home`` is the home directory the recognized link destinations live under.
    """
    directory, name = os.path.split(os.path.abspath(path))
    try:
        parent_fd = pathsafe.open_directory(directory, create=False)
    except OSError as error:
        if error.errno in NOT_REACHABLE:
            return ""
        return f"{RESULT_KEPT_PREFIX}cannot read {directory}: {error.strerror}"
    try:
        try:
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                if not _recognized_link(os.readlink(name, dir_fd=parent_fd), home):
                    return ""
                os.unlink(name, dir_fd=parent_fd)
                return RESULT_REMOVED_LINK
            if not stat.S_ISDIR(info.st_mode):
                return ""
            return _remove_copy(parent_fd, name)
        except Kept as kept:
            return f"{RESULT_KEPT_PREFIX}{kept}"
        except OSError as error:
            if error.errno in NOT_REACHABLE:
                return ""
            return f"{RESULT_KEPT_PREFIX}cannot read it: {error.strerror}"
    finally:
        os.close(parent_fd)


def main(argv):
    if len(argv) != 2 or argv[0] != "remove":
        raise SystemExit("legacy_package: usage: legacy_package.py remove <path>")
    result = remove(argv[1], os.environ["HOME"])
    if result:
        print(result)


if __name__ == "__main__":
    main(sys.argv[1:])
