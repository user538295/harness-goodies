#!/usr/bin/env python3
# universal-session-log: managed
"""Removal of the session-log hooks older releases registered in Claude settings.json.

The claude-goodies plugin's hooks/hooks.json registers the session-log hooks, so
settings.json keeps none. The flock + JSON-merge + atomic-replace dances live in
one auditable, testable module instead of large shell here-documents.

Usage:
    python3 claude_settings.py remove     # SETTINGS_PATH, SESSION_LOG_OWNER_MARKER; prints a line only if it removed any
    python3 claude_settings.py migrate    # SETTINGS_PATH, CLAUDE_SCRIPTS_DIR

A hook is owned when its command starts with the ``SESSION_LOG_OWNER=<owner> ``
marker, whatever hook path follows it, so entries written by any older release
(including dead versioned plugin-cache paths) are removed. An unmarked
``[exec] bash <path>/skills/session-log/adapters/claude/claude_hook.sh
<lifecycle>`` command counts as owned as well, whatever its path: a copy of the
plugin's own ``${CLAUDE_PLUGIN_ROOT}`` command (never substituted in
settings.json) or an old install path. A native Windows ``C:\\`` path is
recognized too, but this module cannot run on native Windows (it needs dir_fd
and fcntl), so cleaning one up takes WSL.
"""
import fcntl
import json
import os
import random
import re
import shlex
import stat
import sys
from dataclasses import dataclass

import pathsafe

NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
FILE_FLAGS = os.O_RDONLY | NOFOLLOW
BACKUP_ATTEMPTS = 20
INSTALL_TEMP_ATTEMPTS = 20
TEMP_ATTEMPTS = 100
# Always '/'-separated: it is matched inside hook command strings, which use '/' on every platform.
PLUGIN_HOOK_RELATIVE = "skills/session-log/adapters/claude/claude_hook.sh"
LIFECYCLE_NAMES = frozenset({"session-start", "user-prompt", "stop", "subagent-stop"})
BASH_COMMANDS = frozenset({"bash", "/bin/bash"})
# `bash <claude_hook.sh> <lifecycle>`, once a leading `exec` is dropped.
HOOK_COMMAND_WORDS = 3
WINDOWS_DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")


def plugin_hook_command(name):
    """The shell-form command hooks/hooks.json registers for lifecycle ``name``.

    hooks.json pins ``"shell": "bash"``, so Claude Code runs it in Git Bash on Windows; the
    command stays POSIX sh so it also runs where a release ignores that field and spawns
    ``sh -c`` (dash on Debian/Ubuntu, which rejects bash-only ``${s//…}``). Claude Code exports
    CLAUDE_PLUGIN_ROOT and substitutes the double-quoted placeholder with forward slashes on
    Windows (https://code.claude.com/docs/en/plugins-reference, "Environment variables"); a
    native ``C:\\`` path survives double quotes and its backslashes are rewritten to ``/``.
    A missing script exits 0 silently instead of failing every event.
    """
    return (
        f's="${{CLAUDE_PLUGIN_ROOT}}/{PLUGIN_HOOK_RELATIVE}"; '
        "case $s in *\\\\*) s=$(printf '%s\\n' \"$s\" | tr '\\\\' /);; esac; "
        f'[ -f "$s" ] && exec bash "$s" {name}; exit 0'
    )


PLUGIN_COMMANDS = frozenset(plugin_hook_command(name) for name in LIFECYCLE_NAMES)


@dataclass(frozen=True)
class SettingsSnapshot:
    metadata: object
    contents: bytes
    descriptor: object = None


@dataclass(frozen=True)
class SettingsUpdate:
    document: object
    snapshot: SettingsSnapshot


@dataclass(frozen=True)
class TemporaryFileOptions:
    mode: int
    candidates: object
    exhaust_message: str


@dataclass(frozen=True)
class BackupOptions:
    settings_name: str
    candidates: object
    exhaust_message: str


@dataclass(frozen=True)
class ConflictRestoreOptions:
    settings_name: str
    backup_name: str
    message: str


LEGACY_NAMES = (
    "prompt_log_save.sh",
    "prompt_log_new_session.sh",
    "prompt_log_stop.sh",
    "prompt_log_subagent.sh",
    "prompt_log_lib.sh",
    "prompt_log_usage.sh",
    "prompt_log_usage.jq",
    "prompt_log_prices.json",
)


def _read_all(fd):
    chunks = []
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _read_fd(fd):
    os.lseek(fd, 0, os.SEEK_SET)
    return _read_all(fd)


def _read_named(parent_fd, name):
    descriptor = os.open(name, FILE_FLAGS, dir_fd=parent_fd)
    try:
        info = os.fstat(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        return info, _read_all(descriptor)
    finally:
        os.close(descriptor)


def _content_version_key(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _backup_file_key(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)


def _file_metadata_signature(value):
    return (
        value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns,
        value.st_mode, value.st_nlink, value.st_uid, value.st_gid,
    )


def _identity_after_rename(value):
    return (
        value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
        value.st_mode, value.st_nlink, value.st_uid, value.st_gid,
    )


def _create_temp(parent_fd, options):
    for candidate in options.candidates:
        try:
            fd = os.open(
                candidate,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW,
                options.mode,
                dir_fd=parent_fd,
            )
            return candidate, fd
        except FileExistsError:
            continue
    raise SystemExit(options.exhaust_message)


def _stage_backup(parent_fd, options):
    for candidate in options.candidates:
        if pathsafe.move_no_replace(
            parent_fd,
            options.settings_name,
            candidate,
        ):
            return candidate
    raise SystemExit(options.exhaust_message)


def _restore_after_conflict(parent_fd, options):
    pathsafe.rename_to_conflict(
        parent_fd,
        pathsafe.ConflictRenameOptions(
            source_name=options.settings_name,
            prefix=options.backup_name,
            exhaust_message=options.message,
        ),
    )
    return pathsafe.move_no_replace(
        parent_fd,
        options.backup_name,
        options.settings_name,
    )


def _owner_prefix(owner):
    return f"SESSION_LOG_OWNER={shlex.quote(owner)} "


def _command_word_splits(command):
    """The command's words as POSIX shell splits them, then with backslashes kept literal (native Windows paths)."""
    literal = shlex.shlex(command, posix=True)
    literal.whitespace_split = True
    literal.escape = ""
    literal.commenters = ""
    for split in (lambda: shlex.split(command), lambda: list(literal)):
        try:
            yield split()
        except ValueError:
            continue


def _hook_script_paths(command):
    """The <path> of `[exec] bash <path> <lifecycle>`, once per word split that reads the command that way."""
    for words in _command_word_splits(command):
        if words[:1] == ["exec"]:
            words = words[1:]
        if len(words) == HOOK_COMMAND_WORDS and words[0] in BASH_COMMANDS and words[2] in LIFECYCLE_NAMES:
            yield words[1]


def _runs_claude_hook(command):
    """True for `[exec] bash <any path>/skills/session-log/adapters/claude/claude_hook.sh <lifecycle>`."""
    return any(
        path.replace("\\", "/").endswith("/" + PLUGIN_HOOK_RELATIVE) for path in _hook_script_paths(command)
    )


def _is_owned_command_hook(item, owner_prefix):
    """A session-log registration: owner-marked, a copy of the plugin's own command, or any claude_hook.sh command."""
    return (
        isinstance(item, dict)
        and item.get("type") == "command"
        and isinstance(item.get("command"), str)
        and (
            item["command"].startswith(owner_prefix)
            or item["command"] in PLUGIN_COMMANDS
            or _runs_claude_hook(item["command"])
        )
    )


def _owned_hook(owner):
    """Predicate: the hook item is a session-log registration."""
    prefix = _owner_prefix(owner)
    return lambda item: _is_owned_command_hook(item, prefix)


def _filter_managed_hooks(entries, removable):
    filtered = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
            filtered.append(entry)
            continue
        kept_hooks = []
        removed_owned = False
        for item in entry["hooks"]:
            if removable(item):
                removed_owned = True
            else:
                kept_hooks.append(item)
        if removed_owned and kept_hooks:
            replacement = dict(entry)
            replacement["hooks"] = kept_hooks
            filtered.append(replacement)
        elif not removed_owned:
            filtered.append(entry)
    return filtered


def _load_install_settings(parent_fd, settings_name):
    metadata = None
    contents = None
    try:
        descriptor = os.open(settings_name, FILE_FLAGS, dir_fd=parent_fd)
        try:
            metadata = os.fstat(descriptor)
            contents = _read_all(descriptor)
        finally:
            os.close(descriptor)
        document = json.loads(contents.decode("utf-8"))
    except FileNotFoundError:
        document = {}
    except (json.JSONDecodeError, OSError) as error:
        raise SystemExit(f"cannot update Claude settings: {error}")
    if not isinstance(document, dict):
        raise SystemExit("cannot update Claude settings: root must be an object")
    return document, SettingsSnapshot(metadata, contents)


def _strip_hooks(hooks, removable):
    """Drop removable hook items, and any entry or event they leave empty."""
    changed = False
    for event, entries in list(hooks.items()):
        if not isinstance(entries, list):
            continue
        kept = _filter_managed_hooks(entries, removable)
        if kept == entries:
            continue
        changed = True
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    return changed


def _remove_managed_hooks(document, removable):
    hooks = document.get("hooks")
    if not isinstance(hooks, dict):
        return False
    changed = _strip_hooks(hooks, removable)
    if changed and not hooks:
        document.pop("hooks", None)
    return changed


def _install_snapshot_matches(current_stat, current_contents, snapshot):
    return (
        snapshot.metadata is not None
        and _file_metadata_signature(current_stat)
        == _file_metadata_signature(snapshot.metadata)
        and current_contents == snapshot.contents
    )


def _assert_install_unchanged(parent_fd, settings_name, original):
    try:
        descriptor = os.open(settings_name, FILE_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        if original.metadata is None:
            return
        raise SystemExit("Claude settings changed during update; retry installation")
    try:
        current_stat = os.fstat(descriptor)
        current_settings = _read_all(descriptor)
    finally:
        os.close(descriptor)
    if not _install_snapshot_matches(current_stat, current_settings, original):
        raise SystemExit("Claude settings changed during update; retry installation")


CHANGED_UPDATE = "Claude settings changed during update; retry installation"
CHANGED_MIGRATE = "Claude settings changed during migration; retry installation"


@dataclass
class SettingsWriteState:
    temporary_name: object
    backup_name: object = None
    backup_active: bool = False


@dataclass
class SettingsWriteContext:
    parent_fd: object
    settings_name: str
    snapshot: SettingsSnapshot
    state: SettingsWriteState


def _write_settings_document(descriptor, document):
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _stage_install_backup(context):
    state = context.state
    original = context.snapshot
    backups = (
        f".session-log-backup.{os.getpid()}.{random.randrange(1 << 30):08x}.{attempt}"
        for attempt in range(BACKUP_ATTEMPTS)
    )
    state.backup_name = _stage_backup(
        context.parent_fd,
        BackupOptions(
            settings_name=context.settings_name,
            candidates=backups,
            exhaust_message="cannot stage Claude settings update",
        ),
    )
    state.backup_active = True
    backup_stat, backup_settings = _read_named(context.parent_fd, state.backup_name)
    if (
        _identity_after_rename(backup_stat) != _identity_after_rename(original.metadata)
        or backup_settings != original.contents
    ):
        if pathsafe.move_no_replace(
            context.parent_fd,
            state.backup_name,
            context.settings_name,
        ):
            state.backup_active = False
        raise SystemExit(CHANGED_UPDATE)


def _replace_install_settings(context):
    state = context.state
    _stage_install_backup(context)
    try:
        os.link(
            state.temporary_name,
            context.settings_name,
            src_dir_fd=context.parent_fd,
            dst_dir_fd=context.parent_fd,
            follow_symlinks=False,
        )
    except FileExistsError:
        state.backup_active = not _restore_after_conflict(
            context.parent_fd,
            ConflictRestoreOptions(
                settings_name=context.settings_name,
                backup_name=state.backup_name,
                message=CHANGED_UPDATE,
            ),
        )
        raise SystemExit(CHANGED_UPDATE)
    os.unlink(state.temporary_name, dir_fd=context.parent_fd)
    state.temporary_name = None
    os.unlink(state.backup_name, dir_fd=context.parent_fd)
    state.backup_active = False


def _install_new_settings(parent_fd, settings_name, state):
    try:
        os.link(
            state.temporary_name,
            settings_name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
            follow_symlinks=False,
        )
    except FileExistsError:
        raise SystemExit(CHANGED_UPDATE)
    os.unlink(state.temporary_name, dir_fd=parent_fd)
    state.temporary_name = None


def _cleanup_install_write(parent_fd, settings_name, state):
    if state.temporary_name is not None:
        try:
            os.unlink(state.temporary_name, dir_fd=parent_fd)
        except OSError:
            pass
    if state.backup_active:
        try:
            os.stat(settings_name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            try:
                if pathsafe.move_no_replace(
                    parent_fd,
                    state.backup_name,
                    settings_name,
                ):
                    state.backup_active = False
            except OSError:
                pass


def _write_install_settings(parent_fd, settings_name, update):
    document = update.document
    original = update.snapshot
    mode = stat.S_IMODE(original.metadata.st_mode) if original.metadata is not None else 0o600
    _assert_install_unchanged(parent_fd, settings_name, original)
    temp_candidates = (
        f".session-log.{os.getpid()}.{random.randrange(1 << 30):08x}" for _ in range(INSTALL_TEMP_ATTEMPTS)
    )
    temporary_name, temporary_fd = _create_temp(
        parent_fd,
        TemporaryFileOptions(
            mode=0o600,
            candidates=temp_candidates,
            exhaust_message="cannot create temporary Claude settings file",
        ),
    )
    state = SettingsWriteState(temporary_name)
    try:
        os.fchmod(temporary_fd, mode & 0o777)
        _write_settings_document(temporary_fd, document)
        _assert_install_unchanged(parent_fd, settings_name, original)
        if original.metadata is None:
            _install_new_settings(parent_fd, settings_name, state)
        else:
            _replace_install_settings(
                SettingsWriteContext(parent_fd, settings_name, original, state)
            )
        os.fsync(parent_fd)
    finally:
        _cleanup_install_write(parent_fd, settings_name, state)


def _update_settings(edit, create):
    """Apply ``edit(document) -> change`` under the settings lock; write only when ``change`` is truthy, and return it."""
    directory, settings_name = os.path.split(os.environ["SETTINGS_PATH"])
    parent_fd = pathsafe.open_directory(directory, create=create)
    try:
        lock_fd = os.open(settings_name + ".session-log.lock", os.O_RDWR | os.O_CREAT | NOFOLLOW, 0o600, dir_fd=parent_fd)
        try:
            os.fchmod(lock_fd, 0o600)
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            document, original = _load_install_settings(parent_fd, settings_name)
            change = edit(document)
            if change:
                _write_install_settings(
                    parent_fd,
                    settings_name,
                    SettingsUpdate(document=document, snapshot=original),
                )
            return change
        finally:
            os.close(lock_fd)
    finally:
        os.close(parent_fd)


def remove():
    """Drop every session-log registration, and print how many when any were; never registers any."""
    settings_path = os.environ["SETTINGS_PATH"]
    owned = _owned_hook(os.environ["SESSION_LOG_OWNER_MARKER"])
    if not os.path.lexists(settings_path):
        return

    def strip(document):
        removed = len(_registrations(document, owned))
        _remove_managed_hooks(document, owned)
        return removed

    removed = _update_settings(strip, create=False)
    if removed:
        print(f"removed {removed} legacy session-log hook registration(s) from {settings_path}")


def _registrations(document, selected):
    """``(event, matcher, command)`` of every ``selected`` hook, once per registration."""
    hooks = document.get("hooks")
    if not isinstance(hooks, dict):
        return []
    return [
        (event, entry.get("matcher"), item["command"])
        for event, entries in hooks.items()
        if isinstance(entries, list)
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("hooks"), list)
        for item in entry["hooks"]
        if selected(item)
    ]


def _normalized_legacy_path(token, legacy_scripts_dir):
    raw_home_scripts = os.path.join(os.environ["HOME"], ".claude", "scripts")
    prefixes = (
        legacy_scripts_dir,
        raw_home_scripts,
        "$HOME/.claude/scripts",
        "~/.claude/scripts",
    )
    for prefix in prefixes:
        if token.startswith(prefix + "/"):
            name = token[len(prefix) + 1:]
            if name in LEGACY_NAMES:
                return os.path.join(legacy_scripts_dir, name)
    if WINDOWS_DRIVE_PATH.match(token):
        directory, _, name = token.replace("\\", "/").rpartition("/")
        if directory.endswith("/.claude/scripts") and name in LEGACY_NAMES:
            return os.path.join(legacy_scripts_dir, name)
    return token


def _is_managed_legacy_command(command, legacy_scripts_dir, legacy_paths):
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if len(tokens) != 2 or tokens[0] not in {"bash", "/bin/bash"}:
        return False
    return _normalized_legacy_path(tokens[1], legacy_scripts_dir) in legacy_paths


def _is_managed_legacy_hook(item, legacy_scripts_dir, legacy_paths):
    if not isinstance(item, dict) or item.get("type") != "command":
        return False
    command = item.get("command", "")
    return (
        isinstance(command, str)
        and _is_managed_legacy_command(command, legacy_scripts_dir, legacy_paths)
    )


def _filter_legacy_entry(entry, legacy_scripts_dir, legacy_paths):
    if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
        return entry, False
    filtered = [
        item
        for item in entry["hooks"]
        if not _is_managed_legacy_hook(item, legacy_scripts_dir, legacy_paths)
    ]
    if len(filtered) == len(entry["hooks"]):
        return entry, False
    if not filtered:
        return None, True
    replacement = dict(entry)
    replacement["hooks"] = filtered
    return replacement, True


def _filter_legacy_event(entries, legacy_scripts_dir, legacy_paths):
    kept = []
    changed = False
    for entry in entries:
        filtered, entry_changed = _filter_legacy_entry(
            entry, legacy_scripts_dir, legacy_paths
        )
        changed = changed or entry_changed
        if filtered is not None:
            kept.append(filtered)
    return kept, changed


def _strip_legacy_hooks(document, legacy_scripts_dir):
    legacy_paths = {os.path.join(legacy_scripts_dir, name) for name in LEGACY_NAMES}
    hooks = document.get("hooks")
    if not isinstance(hooks, dict):
        return False
    changed = False
    for event, entries in list(hooks.items()):
        kept, event_changed = _filter_legacy_event(
            entries, legacy_scripts_dir, legacy_paths
        )
        if event_changed:
            changed = True
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
    if changed and not hooks:
        document.pop("hooks", None)
    return changed


def _content_version_matches(current, snapshot):
    return _content_version_key(current) == _content_version_key(snapshot.metadata)


def _migration_file_versions_match(descriptor_stat, path_stat, snapshot):
    return (
        _content_version_matches(descriptor_stat, snapshot)
        and _content_version_matches(path_stat, snapshot)
    )


def _reverify_unchanged_early(parent_fd, settings_name, snapshot):
    current_stat = os.fstat(snapshot.descriptor)
    current_path_stat = os.stat(settings_name, dir_fd=parent_fd, follow_symlinks=False)
    if not _migration_file_versions_match(current_stat, current_path_stat, snapshot):
        raise SystemExit(CHANGED_MIGRATE)
    if _read_fd(snapshot.descriptor) != snapshot.contents:
        raise SystemExit(CHANGED_MIGRATE)


def _verify_migration_snapshot(parent_fd, settings_name, snapshot):
    final_stat = os.fstat(snapshot.descriptor)
    final_path_stat = os.stat(settings_name, dir_fd=parent_fd, follow_symlinks=False)
    if not _migration_file_versions_match(final_stat, final_path_stat, snapshot):
        raise SystemExit(CHANGED_MIGRATE)
    if _read_fd(snapshot.descriptor) != snapshot.contents:
        raise SystemExit(CHANGED_MIGRATE)


def _stage_migration_backup(context):
    state = context.state
    snapshot = context.snapshot
    backups = (
        f".session-log-backup.{os.getpid()}.{attempt}"
        for attempt in range(BACKUP_ATTEMPTS)
    )
    state.backup_name = _stage_backup(
        context.parent_fd,
        BackupOptions(
            settings_name=context.settings_name,
            candidates=backups,
            exhaust_message="cannot stage Claude settings migration",
        ),
    )
    state.backup_active = True
    backup_stat, backup_settings = _read_named(context.parent_fd, state.backup_name)
    if (
        _backup_file_key(backup_stat) != _backup_file_key(snapshot.metadata)
        or backup_settings != snapshot.contents
    ):
        if pathsafe.move_no_replace(
            context.parent_fd,
            state.backup_name,
            context.settings_name,
        ):
            state.backup_active = False
        raise SystemExit(CHANGED_MIGRATE)


def _replace_migrated_settings(context):
    state = context.state
    snapshot = context.snapshot
    _stage_migration_backup(context)
    try:
        os.link(
            state.temporary_name,
            context.settings_name,
            src_dir_fd=context.parent_fd,
            dst_dir_fd=context.parent_fd,
            follow_symlinks=False,
        )
    except FileExistsError:
        state.backup_active = not _restore_after_conflict(
            context.parent_fd,
            ConflictRestoreOptions(
                settings_name=context.settings_name,
                backup_name=state.backup_name,
                message=CHANGED_MIGRATE,
            ),
        )
        raise SystemExit(CHANGED_MIGRATE)
    os.unlink(state.temporary_name, dir_fd=context.parent_fd)
    state.temporary_name = None
    backup_stat, backup_settings = _read_named(context.parent_fd, state.backup_name)
    if (
        _backup_file_key(backup_stat) != _backup_file_key(snapshot.metadata)
        or backup_settings != snapshot.contents
    ):
        pathsafe.rename_to_conflict(
            context.parent_fd,
            pathsafe.ConflictRenameOptions(
                source_name=state.backup_name,
                prefix=state.backup_name,
                exhaust_message=CHANGED_MIGRATE,
            ),
        )
        state.backup_active = False
        raise SystemExit(CHANGED_MIGRATE)
    os.unlink(state.backup_name, dir_fd=context.parent_fd)
    state.backup_active = False


def _cleanup_migration_write(parent_fd, settings_name, state):
    if state.temporary_name is not None:
        try:
            os.unlink(state.temporary_name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
    if state.backup_active:
        try:
            os.stat(settings_name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            try:
                if pathsafe.move_no_replace(
                    parent_fd,
                    state.backup_name,
                    settings_name,
                ):
                    state.backup_active = False
            except OSError:
                pass


def _write_migrate_settings(parent_fd, settings_name, update):
    document = update.document
    snapshot = update.snapshot
    mode = stat.S_IMODE(snapshot.metadata.st_mode)
    temp_candidates = (
        f".session-log-migrate.{os.getpid()}.{attempt}"
        for attempt in range(TEMP_ATTEMPTS)
    )
    temporary, temporary_fd = _create_temp(
        parent_fd,
        TemporaryFileOptions(
            mode=mode & 0o777,
            candidates=temp_candidates,
            exhaust_message="cannot allocate temporary Claude settings file",
        ),
    )
    state = SettingsWriteState(temporary)
    try:
        os.fchmod(temporary_fd, mode & 0o777)
        _write_settings_document(temporary_fd, document)
        _verify_migration_snapshot(parent_fd, settings_name, snapshot)
        _replace_migrated_settings(
            SettingsWriteContext(parent_fd, settings_name, snapshot, state)
        )
        os.fsync(parent_fd)
    finally:
        _cleanup_migration_write(parent_fd, settings_name, state)



def migrate():
    settings_path = os.environ["SETTINGS_PATH"]
    legacy_scripts_dir = os.environ["CLAUDE_SCRIPTS_DIR"]
    directory, settings_name = os.path.split(settings_path)
    parent_fd = pathsafe.open_directory(directory, create=False)
    try:
        lock_fd = os.open(settings_name + ".session-log.lock", os.O_RDWR | os.O_CREAT | NOFOLLOW, 0o600, dir_fd=parent_fd)
        try:
            os.fchmod(lock_fd, 0o600)
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            settings_fd = os.open(settings_name, FILE_FLAGS, dir_fd=parent_fd)
            try:
                metadata = os.fstat(settings_fd)
                if not stat.S_ISREG(metadata.st_mode):
                    raise SystemExit("Claude settings is not a regular file")
                contents = _read_fd(settings_fd)
                snapshot = SettingsSnapshot(metadata, contents, settings_fd)
                try:
                    document = json.loads(contents.decode("utf-8"))
                except json.JSONDecodeError as error:
                    raise SystemExit(f"cannot update Claude settings: {error}")
                if not _strip_legacy_hooks(document, legacy_scripts_dir):
                    raise SystemExit(0)
                _reverify_unchanged_early(parent_fd, settings_name, snapshot)
                _write_migrate_settings(
                    parent_fd,
                    settings_name,
                    SettingsUpdate(document=document, snapshot=snapshot),
                )
            finally:
                os.close(settings_fd)
        finally:
            os.close(lock_fd)
    finally:
        os.close(parent_fd)


def main(argv):
    if not argv:
        raise SystemExit("claude_settings: missing operation")
    operation = argv[0]
    if operation == "remove":
        remove()
    elif operation == "migrate":
        migrate()
    else:
        raise SystemExit(f"claude_settings: unknown operation: {operation}")


if __name__ == "__main__":
    main(sys.argv[1:])
