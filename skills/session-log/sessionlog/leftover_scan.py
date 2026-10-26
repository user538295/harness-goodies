"""Leftover scan (C1, Interface Adapters): the closed list of old-release leftovers of the invoked harness.

The list lives here and nowhere else. It reports; it deletes, moves and rewrites nothing. A leftover is any existing:
- hook entry in ~/.claude/settings.json, ~/.codex/hooks.json or ~/.cursor/hooks.json whose command runs
  adapters/claude/claude_hook.sh, adapters/native/session_log_hook.py or ~/.claude/scripts/prompt_log_* (one finding per file);
- ~/.claude/scripts/prompt_log_* file;
- seeded copy <harness root>/skills/session-log or ~/.agents/skills/session-log (shared by the harnesses): a folder holding a file
  only an old package has (install script, bin/session-log, an old hook script, the old ownership manifest) or the
  `universal-session-log: managed` marker, or a link where old releases linked it (a claude-goodies plugin cache package, the old
  release store), whatever its target holds now;
- the old release store ~/.local/share/universal-session-log (shared by the harnesses);
- OpenCode plugins/session-log.js, plugins/session-log-omp.js, scripts/session_log_usage.sh, commands/session-log.md,
  commands/session-log-omp.md; OMP extensions/session-log.js, extensions/session-log-omp.js, skills/session-log-omp;
- the old state folder <harness root>/session-log/ (the adapter version stamps, the manifest and the cleanup stamps live in it);
  ~/.claude/session-maps/.
The two shared folders are reported whichever harness runs: any harness may load a copy in ~/.agents/skills, and the store is what
old links pointed at. Every other path is the invoked harness's own.
Not reported: inert files in the log root beside old-format logs (`.enabled.lock`, OMP `.*.dedupe`), which stay untouched; the
`.session-log-backup.*` versions an old cleanup kept beside a hook file, which hold the user's own settings.

A hook entry that runs a moved file is one item with it, in the order that keeps the hook working until the entry is gone.
The recognizers are ported from the old release's lib/legacy_cleanup.sh, lib/legacy_package.py and lib/claude_settings.py.
"""
import fnmatch
import json
import os
import re
import shlex
import stat
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from .leftover_fix import HookFile, Move, move_fix, ordered_fix, remove_entries_fix
from .leftover_port import Leftover
from .model import Harness

# A move goes to <anchor>/session-log-old/<same relative parent>: outside every skills folder (a copy moved inside one may still
# load), and two leftovers named alike (the seeded copy and the old state folder are both `session-log`) never collide.
OLD_RELEASE_FOLDER = "session-log-old"

HOOK_FILES: Dict[Harness, str] = {Harness.CLAUDE: "settings.json", Harness.CODEX: "hooks.json", Harness.CURSOR: "hooks.json"}
# The folder of each hook file below the home folder, to read another harness's hook file for a shared folder it runs.
HARNESS_FOLDERS: Dict[Harness, str] = {Harness.CLAUDE: ".claude", Harness.CODEX: ".codex", Harness.CURSOR: ".cursor"}
# The shared seeded copy (below the skills folder `~/.agents/skills` every harness may read) and the folder a move out of it keeps
# its relative path under, and the old release store (`~/.local/share/universal-session-log`, moved out of `~/.local/share`).
SHARED_SKILL_ANCHOR = ".agents"
SHARED_SEEDED_COPY = ("skills", "session-log")
OLD_STORE = (".local", "share", "universal-session-log")
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
# Files only an old package has, whatever was edited in it: the install script, the CLI, the two hook scripts hooks named, and the
# manifest the old release wrote to prove ownership of its copy.
OLD_PACKAGE_FILES = (("install.sh",), ("bin", "session-log"), ("adapters", "claude", "claude_hook.sh"),
                     ("adapters", "native", "session_log_hook.py"), (".universal-session-log.manifest",))
# Where old releases pointed a skill-root link, below the home folder (`*` never spans a `/`).
RELEASE_LINK_DESTINATIONS = (".claude/plugins/cache/*/claude-goodies/*/skills/session-log",
                             ".local/share/universal-session-log/releases/*/skills/session-log")
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


def _is_plain_folder(path: str) -> bool:
    """A real folder: a link is never entered below a copy's top folder, so a link cannot send the scan into a big tree."""
    return os.path.isdir(path) and not os.path.islink(path)


def _holds_old_package_file(path: str) -> bool:
    """Whether `path` holds one of the files only an old package has, found with no link followed below `path`."""
    for parts in OLD_PACKAGE_FILES:
        directory = path
        for part in parts[:-1]:
            directory = os.path.join(directory, part)
            if not _is_plain_folder(directory):
                break
        else:
            if os.path.lexists(os.path.join(directory, parts[-1])):
                return True
    return False


def _has_marked_file(path: str) -> bool:
    for folder in SEEDED_MARKER_FOLDERS:
        directory = os.path.join(path, folder) if folder else path
        if folder and not _is_plain_folder(directory):
            continue
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            continue
        if any(_has_ownership_marker(os.path.join(directory, name)) for name in names):
            return True
    return False


def _is_release_link(path: str, home: str) -> bool:
    """A link whose destination is where old releases pointed it (a claude-goodies plugin cache package, the release store),
    whatever its target holds now: a plugin update leaves it dangling or pointing at a package with no old marker."""
    try:
        destination = os.readlink(path).replace("\\", "/").rstrip("/")
    except OSError:
        return False
    if "/../" in destination + "/" or "/./" in destination + "/":
        return False
    roots = {home.replace("\\", "/").rstrip("/"), os.path.realpath(home).replace("\\", "/").rstrip("/")}
    names = destination.split("/")
    for root in roots:
        for pattern in RELEASE_LINK_DESTINATIONS:
            patterns = (root + "/" + pattern).split("/")
            if len(names) == len(patterns) and all(map(fnmatch.fnmatchcase, names, patterns)):
                return True
    return False


def _is_seeded_copy(path: str, home: str) -> bool:
    """A link where old releases linked their package, or a folder (or a link to one) holding a file only an old package has
    (the install script, bin/session-log, an old hook script, the old ownership manifest) or a file with the ownership marker."""
    if os.path.islink(path) and _is_release_link(path, home):
        return True
    return os.path.isdir(path) and (_holds_old_package_file(path) or _has_marked_file(path))


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


def _old_hook_commands(path: str) -> Tuple[str, ...]:
    """The commands of a hook file's old entries, each once, in file order. A file that is missing, not a regular file,
    unreadable or not a JSON object holds no entry it can prove."""
    if not _regular_file(path):
        return ()
    try:
        with open(path, encoding="utf-8-sig") as handle:
            document = json.load(handle)
        if not isinstance(document, dict):
            return ()
        return tuple(dict.fromkeys(filter(_is_old_hook_command, _hook_commands(document.get(HOOKS_KEY)))))
    except (OSError, ValueError, RecursionError):
        return ()


def _slashed(text: str) -> str:
    return text.replace("\\", "/")


@dataclass(frozen=True)
class _Old:
    """An old file or folder to move: its path, the folder its move keeps the relative path under, whether every harness shares it,
    and, when a hook entry can run it, the text that names it in a command (`.claude/skills/session-log/`)."""

    path: str
    anchor: str
    shared: bool = False
    needle: Optional[str] = None


@dataclass(frozen=True)
class _Hooks:
    """A hook file with old entries, and whether it is the invoked harness's own. Another harness's file counts only for what
    every harness shares: moving the file of one harness never breaks another's hooks."""

    file: HookFile
    own: bool

    def runs(self, old: _Old) -> bool:
        return ((self.own or old.shared) and old.needle is not None
                and any(old.needle in _slashed(command) for command in self.file.commands))


class LeftoverScan:
    """The leftovers of one harness under its root, sorted by path. Reads only; each harness has its own list, plus the two shared
    folders (the shared skills folder `~/.agents/skills` and the old release store), which any harness may load or lose to.

    A hook entry that runs an old file (a seeded copy, a `prompt_log_*` script, the release store) and that file are one item
    whose fix deletes the entry first: moving the file first makes the hook fail at every prompt. Another harness's hook file is
    read only for entries that run a shared folder, so it joins that item and is never an item of its own."""

    def __init__(self, harness: Harness, root: str, home: str, platform: str) -> None:
        self._harness = harness
        self._root = root
        self._home = home
        self._platform = platform

    def scan(self) -> Tuple[Leftover, ...]:
        runnable = self._runnable()
        hooks = self._hook_files()
        found = [Leftover(old.path, move_fix(self._platform, self._moves([old]))) for old in self._plain()]
        grouped: Set[int] = set()
        for hook_indexes, runs in self._groups(hooks, runnable):
            grouped |= runs
            files = sorted((hooks[index].file for index in hook_indexes), key=lambda file: file.path)
            if not runs:
                found.append(Leftover(files[0].path, remove_entries_fix(files[0].commands)))
                continue
            moved = sorted((runnable[index] for index in runs), key=lambda old: old.path)
            own_paths = sorted(hooks[index].file.path for index in hook_indexes if hooks[index].own)
            found.append(Leftover((own_paths or [moved[0].path])[0], ordered_fix(self._platform, files, self._moves(moved))))
        found.extend(Leftover(old.path, move_fix(self._platform, self._moves([old])))
                     for index, old in enumerate(runnable) if index not in grouped)
        return tuple(sorted(found, key=lambda leftover: leftover.path))

    @staticmethod
    def _groups(hooks: List[_Hooks], runnable: List[_Old]) -> List[Tuple[Set[int], Set[int]]]:
        """The hook files and old files that must be handled together: each hook file with the old files its entries run, joined
        with every other hook file that runs one of the same."""
        groups: List[Tuple[Set[int], Set[int]]] = []
        for index, hook in enumerate(hooks):
            joined_hooks, joined_runs = {index}, {run for run, old in enumerate(runnable) if hook.runs(old)}
            if not hook.own and not joined_runs:
                continue  # another harness's hook file that runs nothing shared is not this scan's business
            rest = []
            for group_hooks, group_runs in groups:
                if group_runs & joined_runs:
                    joined_hooks |= group_hooks
                    joined_runs |= group_runs
                else:
                    rest.append((group_hooks, group_runs))
            groups = rest + [(joined_hooks, joined_runs)]
        return groups

    def _moves(self, olds: List[_Old]) -> List[Move]:
        """The folders to make and the paths to move into each, in path order: `<anchor>/session-log-old/<same relative parent>`."""
        folders: Dict[str, List[str]] = {}
        for old in sorted(olds, key=lambda old: old.path):
            folder = os.path.dirname(os.path.join(old.anchor, OLD_RELEASE_FOLDER, os.path.relpath(old.path, old.anchor)))
            folders.setdefault(folder, []).append(old.path)
        return [(folder, tuple(sources)) for folder, sources in folders.items()]

    def _path(self, *parts: str) -> str:
        return os.path.join(self._root, *parts)

    def _home_path(self, *parts: str) -> str:
        return os.path.join(self._home, *parts)

    def _needle(self, path: str, suffix: str = "/") -> str:
        """How a hook command names `path`, whatever stands for the home folder (`~`, `$HOME`, a full path) before it."""
        return _slashed(os.path.relpath(path, self._home)) + suffix

    def _plain(self) -> List[_Old]:
        """Old files and folders no hook entry runs."""
        candidates = [self._path(*parts) for parts in PLAIN_PATHS.get(self._harness, ())]
        candidates.append(self._path(OLD_STATE_FOLDER))
        return [_Old(path, self._root) for path in candidates if os.path.lexists(path)]

    def _runnable(self) -> List[_Old]:
        """Old files and folders a hook entry may run: seeded copies, the legacy scripts and the old release store."""
        found: List[_Old] = []
        for parts, anchor, shared in ((SEEDED_COPY, self._root, False),
                                      (SHARED_SEEDED_COPY, self._home_path(SHARED_SKILL_ANCHOR), True)):
            path = os.path.join(anchor, *parts)
            if _is_seeded_copy(path, self._home):
                found.append(_Old(path, anchor, shared, self._needle(path)))
        store = self._home_path(*OLD_STORE)
        if os.path.lexists(store):
            found.append(_Old(store, self._home_path(*OLD_STORE[:-1]), True, self._needle(store)))
        for folder in LEGACY_SCRIPT_FOLDERS.get(self._harness, ()):
            scripts = self._path(folder)
            needle = self._needle(scripts, "/" + LEGACY_SCRIPT_PREFIX)
            found.extend(_Old(path, self._root, False, needle) for path in self._legacy_scripts(scripts))
        return found

    def _hook_files(self) -> List[_Hooks]:
        """The invoked harness's hook file when it holds old entries, and each other harness's that does (a hook file of
        another harness is read only to learn whether it runs a shared folder, which `_groups` decides)."""
        found: List[_Hooks] = []
        for harness, name in HOOK_FILES.items():
            own = harness is self._harness
            path = self._path(name) if own else self._home_path(HARNESS_FOLDERS[harness], name)
            commands = _old_hook_commands(path)
            if commands:
                found.append(_Hooks(HookFile(path, harness, commands), own))
        return found

    @staticmethod
    def _legacy_scripts(folder: str) -> List[str]:
        try:
            names = os.listdir(folder)
        except OSError:
            return []
        return [os.path.join(folder, name) for name in names if name.startswith(LEGACY_SCRIPT_PREFIX)]
