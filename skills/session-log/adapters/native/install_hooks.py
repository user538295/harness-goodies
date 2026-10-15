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
# Codex runs a new or changed hooks.json command only after the user trusts it: in /hooks, which
# reloads the running session's hooks, or on the "Hooks need review" screen a restart shows.
# bin/session-log's CODEX_TRUST_INSTRUCTION says the same (tests check it).
CODEX_TRUST_INSTRUCTION = (
    "approve the session-log hooks in /hooks, or restart Codex and choose 'Trust all and continue'"
    " if it shows the 'Hooks need review' screen"
)
RESTORE_FOLLOW_UP = f"; {CODEX_TRUST_INSTRUCTION}"
# The marketplace Codex caches codex-goodies under: the "name" of .agents/plugins/marketplace.json.
# Pinning it keeps another marketplace's codex-goodies from running under the trusted command, and
# leaves `sort -V` comparing version directories only. lib/adapter_install.sh (CODEX_MARKETPLACE)
# names the same one, and checks its cache before `on` registers the command; tests check they agree.
CODEX_MARKETPLACE = "user538295"
# Marks a hooks.json command as session-log's, whatever text follows it: the SESSION_LOG_OWNER
# assignment bin/session-log's OWNER_MARKER_VALUE names, as the Claude Code adapter's settings.json
# commands carried it. A `VAR=value command` prefix runs in POSIX shells, bash, and zsh (fish 3.1
# and later accepts it too; untested).
CODEX_OWNER_PREFIX = "SESSION_LOG_OWNER=universal-session-log "
# The command Codex runs from ~/.codex/hooks.json. It names no package: when the hook runs it picks
# the highest codex-goodies version with `sort -V` (Codex keeps one version directory per plugin, so
# that is the version Codex runs) and does nothing once none is installed, or without python3.
# Codex trusts a hook by its hooks.json position plus a hash of its handler config (command text,
# event, matcher, timeout); plugin updates change none of these, so they never ask for a new review.
# `sh -c` keeps it POSIX whatever the user's $SHELL, which Codex runs it with. The hook's runtime
# marker records its parent process, which is Codex when every shell in between execs its last
# command: `sh -c` here, and the user's $SHELL as bash and zsh do (tested); other shells are
# unverified.
CODEX_COMMAND = (
    CODEX_OWNER_PREFIX + "sh -c 'package=$(ls -d \"$HOME\"/.codex/plugins/cache/" + CODEX_MARKETPLACE
    + "/codex-goodies/*/skills/session-log 2>/dev/null | sort -V | tail -1);"
    " [ -f \"$package/adapters/native/session_log_hook.py\" ] || exit 0;"
    " command -v python3 >/dev/null 2>&1 || exit 0;"
    " exec python3 \"$package/adapters/native/session_log_hook.py\" codex {lifecycle}'"
)
# argv, program name included: mode, harness, and config path.
ARGUMENT_COUNT = 4


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


def managed_command(value, harness, lifecycle):
    """Whether ``value`` is a session-log command for ``lifecycle``, or one naming a package's hook by path.

    A Codex command is session-log's when it carries the owner prefix and ends in the lifecycle, so
    whatever text a release runs in between, a later release replaces it.
    """
    if not isinstance(value, str):
        return False
    if harness == "codex" and value.startswith(CODEX_OWNER_PREFIX) and value.endswith(f" codex {lifecycle}'"):
        return True
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


def expected_codex(lifecycle):
    return {"hooks": [{"type": "command", "command": CODEX_COMMAND.format(lifecycle=lifecycle)}]}


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


def is_current(document, harness):
    hooks = document.get("hooks")
    if not isinstance(hooks, dict):
        return False
    for event, lifecycle in _events(harness):
        entries = hooks.get(event)
        if not isinstance(entries, list):
            return False
        if expected_codex(lifecycle) not in entries:
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


def replace_managed_entries(entries, expected, harness, lifecycle):
    """``entries`` with one managed entry slot holding ``expected``, the other managed entries dropped.

    The slot is that of a managed entry already equal to ``expected``, else the first managed one;
    ``expected`` is appended when no entry is managed. Codex keys a hook's trust by its index in the
    event's list, so keeping a current entry's slot keeps it and every foreign hook before it trusted.
    """
    managed = [index for index, entry in enumerate(entries) if is_managed_entry(entry, harness, lifecycle)]
    if not managed:
        return [*entries, expected]
    slot = next((index for index in managed if entries[index] == expected), managed[0])
    return [expected if index == slot else entry
            for index, entry in enumerate(entries) if index == slot or index not in managed]


def update(document, harness):
    require_hook_arrays(document, harness)
    hooks = document.setdefault("hooks", {})
    for event, lifecycle in _events(harness):
        hooks[event] = replace_managed_entries(hooks.get(event, []), expected_codex(lifecycle), harness, lifecycle)


def managed_entry_counts(document, harness):
    """(managed entries found, of which current) — one current entry per event is the clean state."""
    hooks = document.get("hooks", {})
    found = current = 0
    for event, lifecycle in _events(harness):
        managed = [entry for entry in hooks.get(event, []) if is_managed_entry(entry, harness, lifecycle)]
        found += len(managed)
        current += min(1, managed.count(expected_codex(lifecycle)))
    return found, current


def repair(config, harness):
    """Leave one current managed entry per event: drop repeated or stale ones and restore missing ones.

    A hooks file without any managed entry is never touched: repair registers nothing from scratch.
    """
    if not os.path.lexists(config):
        return
    document = load(config)
    require_hook_arrays(document, harness)
    found, current = managed_entry_counts(document, harness)
    events = len(_events(harness))
    if found == 0 or found == current == events:
        return
    update(document, harness)
    atomic_write(config, document)
    if found > current:
        print(f"removed {found - current} duplicate or stale hook registration(s) from {config}")
    if events > current:
        print(f"restored {events - current} missing hook registration(s) in {config}{RESTORE_FOLLOW_UP}")


def has_managed_entries(document, harness):
    """Whether a parsed hooks ``document`` that `remove` accepts holds a session-log entry; never fails.
    A document ``require_hook_arrays`` refuses holds none, since `remove` could not drop it."""
    hooks = document.get("hooks", {}) if isinstance(document, dict) else None
    if not isinstance(hooks, dict) or not all(isinstance(hooks.get(event, []), list) for event, _ in _events(harness)):
        return False
    return any(is_managed_entry(entry, harness, lifecycle)
               for event, lifecycle in _events(harness) for entry in hooks.get(event, []))


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
        # Keep the inode alive through replacement and cleanup so a swapped path cannot reuse it.
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
        if temporary is not None:
            try:
                pathsafe.unlink_if_same_file(parent_fd, temporary, temporary_stat)
            except OSError:
                pass
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_fd)

def invalid_arguments(argv):
    """Codex is checked, installed, and repaired; the Cursor plugin registers its own hooks, so Cursor only removes."""
    if len(argv) != ARGUMENT_COUNT:
        return True
    mode, harness = argv[1:3]
    return harness != ("cursor" if mode == "remove" else "codex") or mode not in ("check", "install", "repair", "remove")


def run(mode, harness, config):
    if mode == "remove":
        remove(config, harness)
        return
    if mode == "repair":
        repair(config, harness)
        return
    document = load(config)
    if mode == "check":
        raise SystemExit(0 if is_current(document, harness) else 1)
    update(document, harness)
    atomic_write(config, document)


def main(argv=None):
    args = sys.argv if argv is None else [sys.argv[0], *argv]
    if invalid_arguments(args):
        fail("hooks configurator requires check|install|repair with codex and a config path, "
             "or remove with cursor and a config path")
    mode, harness, config_path = args[1:]
    config = Path(config_path).absolute()
    try:
        run(mode, harness, config)
    except pathsafe.KeptAsideError as error:
        # report_kept_versions (lib/legacy_cleanup.sh) names these at every `status`, `repair`, and `on` until they
        # are deleted.
        kept = " and ".join(str(config.parent / name) for name in error.kept)
        if error.cause is not None:
            fail(f"cannot update hooks file {config}: {error.cause}; a version is kept in {kept}")
        fail(f"{config} changed while session-log was replacing it; another version is kept in {kept}")
    except OSError as error:
        fail(f"cannot update hooks file {config}: {error}")


if __name__ == "__main__":
    main()
