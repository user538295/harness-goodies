#!/usr/bin/env python3
# universal-session-log: managed
import errno
import json
import os
import random
import shlex
import stat
import sys
from pathlib import Path

LIB_PATH = str(Path(__file__).resolve().parents[2] / "lib")
if LIB_PATH not in sys.path:
    sys.path.insert(0, LIB_PATH)
import pathsafe

MAX_TEMP_FILE_ATTEMPTS = 20
# O_NONBLOCK keeps a FIFO swapped in for the hooks file from blocking the open.
READ_FLAGS = pathsafe.FILE_FLAGS | os.O_NONBLOCK
# Codex runs a new or changed hooks.json command only after the user reviews and trusts it.
RESTORE_FOLLOW_UP = {"codex": "; review and trust them in Codex, then restart"}
# argv lengths, program name included: mode, harness, config path, and hook path; `remove` takes no hook path.
ARGUMENT_COUNT = 5
REMOVE_ARGUMENT_COUNT = 4


def _events(harness):
    if harness == "cursor":
        return (
            ("sessionStart", "session-start"),
            ("beforeSubmitPrompt", "user-prompt"),
            ("afterAgentResponse", "assistant-response"),
            ("subagentStop", "subagent-stop"),
            ("stop", "stop"),
        )
    return (
        ("SessionStart", "session-start"),
        ("UserPromptSubmit", "user-prompt"),
        ("Stop", "stop"),
        ("SubagentStop", "subagent-stop"),
    )


def fail(message):
    print(f"session-log: {message}", file=sys.stderr)
    raise SystemExit(1)


def build_command(harness, hook, lifecycle):
    return f"python3 {shlex.quote(str(hook))} {harness} {lifecycle}"


def managed_command(value, harness, lifecycle):
    if not isinstance(value, str):
        return False
    try:
        words = shlex.split(value)
    except ValueError:
        return False
    return (
        len(words) == 4
        and words[0] == "python3"
        and words[1].replace("\\", "/").endswith("/skills/session-log/adapters/native/session_log_hook.py")
        and words[2:] == [harness, lifecycle]
    )


def expected_cursor(harness, hook, lifecycle):
    return {"command": build_command(harness, hook, lifecycle), "timeout": 30}


def expected_codex(harness, hook, lifecycle):
    return {"hooks": [{"type": "command", "command": build_command(harness, hook, lifecycle)}]}


def _expected_builder(harness):
    return expected_cursor if harness == "cursor" else expected_codex


def is_cursor_managed_entry(entry, harness, lifecycle):
    return isinstance(entry, dict) and managed_command(entry.get("command"), harness, lifecycle)


def is_codex_managed_entry(entry, harness, lifecycle):
    if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
        return False
    items = entry["hooks"]
    return len(items) == 1 and isinstance(items[0], dict) and managed_command(items[0].get("command"), harness, lifecycle)


def is_managed_entry(entry, harness, lifecycle):
    if harness == "cursor":
        return is_cursor_managed_entry(entry, harness, lifecycle)
    return is_codex_managed_entry(entry, harness, lifecycle)


def prepare_cursor_document(document):
    version = document.get("version", 1)
    if version != 1:
        fail("Cursor hooks version must be 1")
    document["version"] = 1


def is_current(document, harness, hook):
    hooks = document.get("hooks")
    if not isinstance(hooks, dict):
        return False
    expected_for = _expected_builder(harness)
    for event, lifecycle in _events(harness):
        entries = hooks.get(event)
        if not isinstance(entries, list):
            return False
        if expected_for(harness, hook, lifecycle) not in entries:
            return False
    return True


def require_hook_arrays(document, harness):
    """Fail unless ``hooks`` is absent or an object whose lifecycle events are absent or arrays."""
    hooks = document.get("hooks", {})
    if not isinstance(hooks, dict):
        fail(f"{harness} hooks must be an object")
    for event, _ in _events(harness):
        if not isinstance(hooks.get(event, []), list):
            fail(f"{harness} hooks.{event} must be an array")


def update(document, harness, hook):
    require_hook_arrays(document, harness)
    hooks = document.setdefault("hooks", {})
    if harness == "cursor":
        prepare_cursor_document(document)
    expected_for = _expected_builder(harness)
    for event, lifecycle in _events(harness):
        entries = hooks.setdefault(event, [])
        kept = [entry for entry in entries if not is_managed_entry(entry, harness, lifecycle)]
        hooks[event] = kept + [expected_for(harness, hook, lifecycle)]


def managed_entry_counts(document, harness, hook):
    """(managed entries found, of which current) — one current entry per event is the clean state."""
    hooks = document.get("hooks", {})
    expected_for = _expected_builder(harness)
    found = current = 0
    for event, lifecycle in _events(harness):
        managed = [entry for entry in hooks.get(event, []) if is_managed_entry(entry, harness, lifecycle)]
        found += len(managed)
        current += min(1, managed.count(expected_for(harness, hook, lifecycle)))
    return found, current


def repair(config, harness, hook):
    """Leave one current managed entry per event: drop repeated or stale ones and restore missing ones.

    A hooks file without any managed entry is never touched: repair registers nothing from scratch.
    """
    if not os.path.lexists(config):
        return
    document = load(config)
    require_hook_arrays(document, harness)
    found, current = managed_entry_counts(document, harness, hook)
    events = len(_events(harness))
    if found == 0 or found == current == events:
        return
    update(document, harness, hook)
    atomic_write(config, document)
    if found > current:
        print(f"removed {found - current} duplicate or stale hook registration(s) from {config}")
    if events > current:
        print(f"restored {events - current} missing hook registration(s) in {config}{RESTORE_FOLLOW_UP.get(harness, '')}")


def has_managed_entries(document, harness):
    """Whether any lifecycle event of a parsed hooks ``document`` holds a session-log entry; never fails."""
    hooks = document.get("hooks") if isinstance(document, dict) else None
    if not isinstance(hooks, dict):
        return False
    return any(
        isinstance(hooks.get(event), list)
        and any(is_managed_entry(entry, harness, lifecycle) for entry in hooks[event])
        for event, lifecycle in _events(harness)
    )


def remove(config, harness):
    """Drop every session-log entry of ``harness`` from ``config``; every other entry stays."""
    if not os.path.lexists(config):
        return
    document = load(config)
    require_hook_arrays(document, harness)
    hooks = document.get("hooks", {})
    removed = 0
    for event, lifecycle in _events(harness):
        entries = hooks.get(event, [])
        kept = [entry for entry in entries if not is_managed_entry(entry, harness, lifecycle)]
        if len(kept) == len(entries):
            continue
        removed += len(entries) - len(kept)
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if removed:
        atomic_write(config, document)
        print(f"removed {removed} legacy hook registration(s) from {config}")


def safe_parent(path):
    current = Path(path.anchor)
    for part in path.parts[1:-1]:
        current /= part
        if current.is_symlink():
            fail(f"refusing to follow symlinked hooks parent: {current}")
        if current.exists() and not current.is_dir():
            fail(f"refusing non-directory hooks parent: {current}")
        if not current.exists():
            current.mkdir(mode=0o700, exist_ok=True)


def open_hooks_file(path):
    try:
        return os.open(path, READ_FLAGS)
    except FileNotFoundError:
        return None
    except OSError as error:
        if error.errno == errno.ELOOP:
            fail(f"refusing to update unsafe hooks file: {path}")
        fail(f"cannot read hooks file: {error}")


def load(path):
    safe_parent(path)
    descriptor = open_hooks_file(path)
    if descriptor is None:
        return {}
    with os.fdopen(descriptor, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            fail(f"refusing to update unsafe hooks file: {path}")
        try:
            value = json.loads(handle.read().decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            fail(f"cannot read hooks file: {error}")
    if not isinstance(value, dict):
        fail("hooks root must be an object")
    return value


def _is_unsafe_existing_target(original):
    return original is not None and (
        stat.S_ISLNK(original.st_mode) or not stat.S_ISREG(original.st_mode)
    )


def atomic_write(path, document):
    path = path.absolute()
    parent_fd = pathsafe.open_directory(str(path.parent), create=False)
    temporary = None
    descriptor = None
    temporary_stat = None
    try:
        try:
            original = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            original = None
        if _is_unsafe_existing_target(original):
            fail(f"refusing to replace unsafe hooks file: {path}")
        mode = stat.S_IMODE(original.st_mode) if original is not None else 0o600
        data = (json.dumps(document, indent=2) + "\n").encode()
        for _ in range(MAX_TEMP_FILE_ATTEMPTS):
            candidate = f".{path.name}.{os.getpid()}.{random.randrange(1 << 30):08x}"
            try:
                descriptor = os.open(
                    candidate,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=parent_fd,
                )
                temporary = candidate
                temporary_stat = os.fstat(descriptor)
                break
            except FileExistsError:
                continue
        else:
            fail("cannot create temporary hooks file")
        os.fchmod(descriptor, mode)
        data_view = memoryview(data)
        offset = 0
        while offset < len(data_view):
            written = os.write(descriptor, data_view[offset:])
            if written == 0:
                raise OSError("hooks file write made no progress")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        pathsafe.replace_file_no_replace(
            pathsafe.AtomicReplacement(
                parent_fd,
                temporary,
                path.name,
                original,
                temporary_stat,
            )
        )
        temporary = None
        os.fsync(parent_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                pathsafe.unlink_if_same_file(parent_fd, temporary, temporary_stat)
            except OSError:
                pass
        os.close(parent_fd)

def invalid_arguments(argv):
    expected = REMOVE_ARGUMENT_COUNT if len(argv) > 1 and argv[1] == "remove" else ARGUMENT_COUNT
    return len(argv) != expected or argv[1] not in ("check", "install", "repair", "remove") or argv[2] not in ("cursor", "codex")


def main(argv=None):
    args = sys.argv if argv is None else [sys.argv[0], *argv]
    if invalid_arguments(args):
        fail("hooks configurator requires check|install|repair, cursor|codex, config path, and hook path; "
             "remove takes no hook path")
    mode, harness, config_path = args[1:REMOVE_ARGUMENT_COUNT]
    config = Path(config_path)
    if mode == "remove":
        remove(config, harness)
        return
    hook = Path(args[ARGUMENT_COUNT - 1])
    if not hook.is_file() or hook.is_symlink():
        fail(f"native lifecycle adapter is missing: {hook}")
    if mode == "repair":
        repair(config, harness, hook)
        return
    document = load(config)
    if mode == "check":
        raise SystemExit(0 if is_current(document, harness, hook) else 1)
    update(document, harness, hook)
    atomic_write(config, document)


if __name__ == "__main__":
    main()
