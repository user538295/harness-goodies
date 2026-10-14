#!/usr/bin/env python3
# universal-session-log: managed
"""Removal of the package copies older install.sh releases seeded into harness skill roots.

Usage:
    python3 legacy_package.py remove <skill-root>/session-log   # HOME

A copy goes only when its ownership manifest proves every file in it: each regular file is
the manifest itself or an asset the manifest lists with the same SHA-256, apart from the
bytecode Python writes beside a module (``__pycache__/*.pyc``). A symlink in the copy's place
goes only when it points into a claude-goodies plugin cache or the release store of older
releases, which is where those releases pointed it; its target is never touched. No symlink
is ever followed, including a symlinked skill root.

Prints ``removed`` or ``removed link``; ``kept: <reason>`` when the copy carries session-log's
ownership marks but cannot be proven unchanged; nothing when the path is not session-log's.
"""
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
OWNERSHIP_MARKER = "<!-- universal-session-log: managed -->"
BYTECODE_DIRECTORY = "__pycache__"
BYTECODE_SUFFIX = ".pyc"
# Where older releases pointed a skill-root symlink, below the home directory.
LINK_DESTINATIONS = (
    ".claude/plugins/cache/*/claude-goodies/*/skills/session-log",
    ".local/share/universal-session-log/releases/*/skills/session-log",
)


class Kept(Exception):
    """The copy carries session-log's ownership marks but cannot be proven unchanged."""


def _read(dir_fd, name):
    with os.fdopen(os.open(name, pathsafe.FILE_FLAGS, dir_fd=dir_fd), "rb") as handle:
        return handle.read()


def _manifest_assets(copy_fd):
    """The ``{relative path: sha256}`` the copy's manifest lists, or None when it has none."""
    try:
        lines = _read(copy_fd, MANIFEST).decode("utf-8").splitlines()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        raise Kept("its ownership manifest is invalid")
    header, version, pairs = lines[:2], lines[2:3], lines[3:]
    if header != MANIFEST_HEADER or not version or not version[0].startswith(MANIFEST_VERSION_PREFIX) or len(pairs) % 2:
        raise Kept("its ownership manifest is invalid")
    assets = {}
    for asset, digest in zip(pairs[::2], pairs[1::2]):
        if not asset.startswith(ASSET_PREFIX) or not digest.startswith(DIGEST_PREFIX):
            raise Kept("its ownership manifest is invalid")
        assets[asset[len(ASSET_PREFIX):]] = digest[len(DIGEST_PREFIX):]
    return assets


def _is_bytecode(relative):
    directory, _, name = relative.rpartition("/")
    return directory.split("/")[-1] == BYTECODE_DIRECTORY and name.endswith(BYTECODE_SUFFIX)


def _check_file(dir_fd, name, relative, assets):
    if relative == MANIFEST or _is_bytecode(relative):
        return
    if relative not in assets:
        raise Kept(f"{relative} is not a file session-log installed")
    if hashlib.sha256(_read(dir_fd, name)).hexdigest() != assets[relative]:
        raise Kept(f"{relative} was changed")


def _inventory(dir_fd, prefix, assets, entries):
    """Append ``(relative path, is directory)`` for everything below ``dir_fd``, children before parents."""
    for name in sorted(os.listdir(dir_fd)):
        relative = prefix + name
        info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child_fd = os.open(name, pathsafe.DIR_FLAGS, dir_fd=dir_fd)
            try:
                _inventory(child_fd, relative + "/", assets, entries)
            finally:
                os.close(child_fd)
            entries.append((relative, True))
        elif stat.S_ISREG(info.st_mode):
            _check_file(dir_fd, name, relative, assets)
            entries.append((relative, False))
        else:
            raise Kept(f"{relative} is not a file session-log installed")


def _has_ownership_marker(copy_fd):
    try:
        return OWNERSHIP_MARKER in _read(copy_fd, "SKILL.md").decode("utf-8", "replace").splitlines()
    except OSError:
        return False


def _remove_entries(copy_fd, entries):
    for relative, is_directory in entries:
        parent, _, name = relative.rpartition("/")
        parent_fd = os.open(parent, pathsafe.DIR_FLAGS, dir_fd=copy_fd) if parent else copy_fd
        try:
            if is_directory:
                os.rmdir(name, dir_fd=parent_fd)
            else:
                os.unlink(name, dir_fd=parent_fd)
        finally:
            if parent_fd != copy_fd:
                os.close(parent_fd)


def _recognized_link(destination):
    if "/../" in destination + "/" or "/./" in destination + "/":
        return False
    home = os.environ["HOME"].rstrip("/")
    homes = {home, os.path.realpath(home)}
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
        entries = []
        _inventory(copy_fd, "", assets, entries)
        try:
            _remove_entries(copy_fd, entries)
        except OSError:
            raise Kept("it changed during removal")
    finally:
        os.close(copy_fd)
    try:
        os.rmdir(name, dir_fd=parent_fd)
    except OSError:
        raise Kept("it changed during removal")
    return "removed"


def remove(path):
    directory, name = os.path.split(os.path.abspath(path))
    try:
        parent_fd = pathsafe.open_directory(directory, create=False)
    except OSError:
        return ""  # absent, or reached only through a symlink
    try:
        try:
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return ""
        if stat.S_ISLNK(info.st_mode):
            if not _recognized_link(os.readlink(name, dir_fd=parent_fd)):
                return ""
            os.unlink(name, dir_fd=parent_fd)
            return "removed link"
        if not stat.S_ISDIR(info.st_mode):
            return ""
        try:
            return _remove_copy(parent_fd, name)
        except Kept as kept:
            return f"kept: {kept}"
    finally:
        os.close(parent_fd)


def main(argv):
    if len(argv) != 2 or argv[0] != "remove":
        raise SystemExit("legacy_package: usage: legacy_package.py remove <path>")
    result = remove(argv[1])
    if result:
        print(result)


if __name__ == "__main__":
    main(sys.argv[1:])
