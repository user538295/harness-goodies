"""Leftover scan (C1, Interface Adapters): the closed list of old-release leftovers of the invoked harness.

The list lives here and nowhere else. It reports; it deletes, moves and rewrites nothing. A leftover is any existing:
- hook entry in ~/.claude/settings.json, ~/.codex/hooks.json or ~/.cursor/hooks.json whose command runs
  adapters/claude/claude_hook.sh, adapters/native/session_log_hook.py or ~/.claude/scripts/prompt_log_* (one finding per file);
- ~/.claude/scripts/prompt_log_* file;
- seeded copy <harness root>/skills/session-log holding install.sh, the old ownership manifest or the
  `universal-session-log: managed` marker;
- OpenCode plugins/session-log.js, plugins/session-log-omp.js, scripts/session_log_usage.sh, commands/session-log.md,
  commands/session-log-omp.md; OMP extensions/session-log.js, extensions/session-log-omp.js, skills/session-log-omp;
- the old state folder <harness root>/session-log/; ~/.claude/session-maps/.
Not reported: inert files in the log root beside old-format logs (`.enabled.lock`, OMP `.*.dedupe`), which stay untouched.

The recognizers are ported from the old release's lib/legacy_cleanup.sh and lib/claude_settings.py.
"""
import json
import os
import re
import shlex
import stat
from typing import Any, Dict, Iterator, List, Tuple

from .leftover_port import Leftover
from .model import Harness

FIX_MOVE_ASIDE = "move it aside"
FIX_REMOVE_HOOK_ENTRIES = "remove its old session-log hook entries"

HOOK_FILES: Dict[Harness, str] = {Harness.CLAUDE: "settings.json", Harness.CODEX: "hooks.json", Harness.CURSOR: "hooks.json"}
# Paths (relative to the harness root) that are leftovers whenever they exist, even as a dangling link.
PLAIN_PATHS: Dict[Harness, Tuple[Tuple[str, ...], ...]] = {
    Harness.CLAUDE: (("session-maps",),),
    Harness.OPENCODE: (("plugins", "session-log.js"), ("plugins", "session-log-omp.js"), ("scripts", "session_log_usage.sh"),
                       ("commands", "session-log.md"), ("commands", "session-log-omp.md")),
    Harness.OMP: (("extensions", "session-log.js"), ("extensions", "session-log-omp.js"), ("skills", "session-log-omp")),
}
# Folders whose files named `prompt_log_*` are leftovers: the scripts the pre-plugin Claude install copied.
LEGACY_SCRIPT_FOLDERS: Dict[Harness, Tuple[str, ...]] = {Harness.CLAUDE: ("scripts",)}
LEGACY_SCRIPT_PREFIX = "prompt_log_"
OLD_STATE_FOLDER = "session-log"
SEEDED_COPY = ("skills", "session-log")
SEEDED_INSTALL_SCRIPT = "install.sh"
SEEDED_MANIFEST = ".universal-session-log.manifest"
# Where a seeded copy carries its ownership marker: its top folder and the two folders old releases put scripts in.
SEEDED_MARKER_FOLDERS = ("", "bin", "lib")
OWNERSHIP_MARKER = "universal-session-log: managed"
MARKER_LINES = frozenset({OWNERSHIP_MARKER, "# " + OWNERSHIP_MARKER, "// " + OWNERSHIP_MARKER,
                          "<!-- " + OWNERSHIP_MARKER + " -->"})
# The marker sits in the first lines of a file: the first, the second after a shebang, the first after a front matter. Only
# this many characters are read, so a huge or newline-free file costs nothing.
MARKER_SCAN_CHARS = 4096
FRONTMATTER_FENCE = "---"
SHEBANG = "#!"
HOOKS_KEY = "hooks"
COMMAND_KEY = "command"
# The prefix every owner-marked old hook command carries, whatever follows it (old releases' Codex commands, `sh -c '...'`).
OLD_OWNER_PREFIX = "SESSION_LOG_OWNER=universal-session-log "
# Words (backslashes read as slashes) of an old hook command that run an old script: the old Claude hook and the old native hook
# of an installed package (any path ending in skills/session-log/adapters/..., also the shell form `s="<path>"; ... exec bash "$s"`,
# whose word keeps a trailing `;`), a script of ~/.claude/scripts named `prompt_log_*` (by `~`, `$HOME` or a full path).
OLD_HOOK_WORD = re.compile(
    r"/skills/session-log/adapters/claude/claude_hook\.sh[\"';]*$|/skills/session-log/adapters/native/session_log_hook\.py[\"';]*$"
    r"|\.claude/scripts/prompt_log_[^/]*$")


def _regular_file(path: str) -> bool:
    """A regular file, or a link to one: a FIFO or a device is never opened."""
    try:
        return stat.S_ISREG(os.stat(path).st_mode)
    except OSError:
        return False


def _head_lines(path: str) -> List[str]:
    """The first lines of a regular file that is not a link, split on newlines only (as the old recognizer did); none when it
    cannot be read."""
    if os.path.islink(path) or not _regular_file(path):
        return []
    try:
        with open(path, encoding="utf-8", errors="replace", newline="\n") as handle:
            text = handle.read(MARKER_SCAN_CHARS)
    except OSError:
        return []
    lines = text.split("\n")
    return lines[:-1] if len(text) == MARKER_SCAN_CHARS else lines  # the last line may have been cut


def _has_ownership_marker(path: str) -> bool:
    lines = _head_lines(path)
    if not lines:
        return False
    if lines[0] in MARKER_LINES:
        return True
    if lines[0].startswith(SHEBANG):
        return len(lines) > 1 and lines[1] in MARKER_LINES
    if lines[0] == FRONTMATTER_FENCE:
        for index in range(1, len(lines)):
            if lines[index] == FRONTMATTER_FENCE:
                return index + 1 < len(lines) and lines[index + 1] in MARKER_LINES
    return False


def _is_seeded_copy(path: str) -> bool:
    """A folder (or a link to one) holding install.sh, the old ownership manifest or a file with the ownership marker."""
    if not os.path.isdir(path):
        return False
    if any(os.path.lexists(os.path.join(path, name)) for name in (SEEDED_INSTALL_SCRIPT, SEEDED_MANIFEST)):
        return True
    for folder in SEEDED_MARKER_FOLDERS:
        directory = os.path.join(path, folder)
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            continue
        if any(_has_ownership_marker(os.path.join(directory, name)) for name in names):
            return True
    return False


def _command_words(command: str) -> List[str]:
    normalized = command.replace("\\", "/")
    try:
        return shlex.split(normalized)
    except ValueError:  # an unbalanced quote: plain whitespace words
        return normalized.split()


def _is_old_hook_command(command: str) -> bool:
    return command.startswith(OLD_OWNER_PREFIX) or any(OLD_HOOK_WORD.search(word) for word in _command_words(command))


def _hook_commands(node: Any) -> Iterator[str]:
    """Every string stored under a `command` key, at any depth."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == COMMAND_KEY and isinstance(value, str):
                yield value
            else:
                yield from _hook_commands(value)
    elif isinstance(node, list):
        for item in node:
            yield from _hook_commands(item)


def _holds_old_hook_entry(path: str) -> bool:
    """Whether a hook file lists a command that runs an old release's script. A file that is missing, not a regular file,
    unreadable or not a JSON object holds no entry it can prove."""
    if not _regular_file(path):
        return False
    try:
        with open(path, encoding="utf-8-sig") as handle:
            document = json.load(handle)
        return isinstance(document, dict) and any(map(_is_old_hook_command, _hook_commands(document.get(HOOKS_KEY))))
    except (OSError, ValueError, RecursionError):
        return False


class LeftoverScan:
    """The leftovers of one harness under its root, sorted by path. Reads only; each harness has its own list."""

    def __init__(self, harness: Harness, root: str) -> None:
        self._harness = harness
        self._root = root

    def scan(self) -> Tuple[Leftover, ...]:
        found = [Leftover(path, FIX_MOVE_ASIDE) for path in self._paths_to_move_aside()]
        hook_file = HOOK_FILES.get(self._harness)
        if hook_file is not None and _holds_old_hook_entry(self._path(hook_file)):
            found.append(Leftover(self._path(hook_file), FIX_REMOVE_HOOK_ENTRIES))
        return tuple(sorted(found, key=lambda leftover: leftover.path))

    def _path(self, *parts: str) -> str:
        return os.path.join(self._root, *parts)

    def _paths_to_move_aside(self) -> List[str]:
        candidates = [self._path(*parts) for parts in PLAIN_PATHS.get(self._harness, ())]
        candidates.append(self._path(OLD_STATE_FOLDER))
        paths = [path for path in candidates if os.path.lexists(path)]
        if _is_seeded_copy(self._path(*SEEDED_COPY)):
            paths.append(self._path(*SEEDED_COPY))
        for folder in LEGACY_SCRIPT_FOLDERS.get(self._harness, ()):
            paths.extend(self._legacy_scripts(self._path(folder)))
        return paths

    @staticmethod
    def _legacy_scripts(folder: str) -> List[str]:
        try:
            names = os.listdir(folder)
        except OSError:
            return []
        return [os.path.join(folder, name) for name in names if name.startswith(LEGACY_SCRIPT_PREFIX)]
