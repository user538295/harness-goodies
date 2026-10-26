"""Leftover fix text (C1, Interface Adapters): the one-line fix of a leftover, in the syntax of the user's shell.

Text only: nothing here runs a command or touches a file. A move is POSIX (`mkdir -p` and `mv`) everywhere except native Windows,
where it is PowerShell (`New-Item` and `Move-Item`). A hook entry that runs a file is removed before the file is moved, so the fix
for such a pair is one item that gives the steps in that order.
"""
import json
import shlex
from dataclasses import dataclass
from typing import Sequence, Tuple

from .model import Harness
from .python_probe import WINDOWS_PLATFORM

# A folder and the paths to move into it.
Move = Tuple[str, Tuple[str, ...]]

POSIX_JOIN = " && "
# Windows PowerShell 5.1 has no `&&`; `-ErrorAction Stop` ends the line when a step fails, so a move never runs without its folder.
POWERSHELL_JOIN = "; "
# PowerShell reads each of these as a single quote, so each is doubled inside a single-quoted string.
POWERSHELL_QUOTES = "'\u2018\u2019\u201a\u201b"

MOVE_FIX = "move it outside the skills folders: `{command}` (a copy moved inside a skills folder may still load)"
DELETE_FROM_ITS_HOOKS = 'delete from its "hooks" every entry whose "command" is {commands} (keep the other entries)'
DELETE_FROM_HOOKS_IN = 'from "hooks" in {path} every entry whose "command" is {commands}'
ORDERED_FIX = ("First delete {deletions} (keep the other entries), then {restart}move what they run: `{command}`. "
               "Not the other way round: {consequence}.")
RESTART_CODEX = "restart Codex (sessions started earlier keep running the old hook), then "
# What goes wrong when the file a listed hook runs is moved first: the hook fails at every prompt.
REVERSED_ORDER_CONSEQUENCE = {
    Harness.CLAUDE: "a hook whose script is gone makes Claude Code report a hook error on every prompt",
    Harness.CODEX: "a hook whose script is gone blocks every Codex prompt",
    Harness.CURSOR: "a hook whose script is gone makes Cursor block every prompt",
}
CONSEQUENCE_JOIN = "; "


@dataclass(frozen=True)
class HookFile:
    """A hook file of `harness` and the exact `command` strings of its old entries."""

    path: str
    harness: Harness
    commands: Tuple[str, ...]


def _powershell_quote(text: str) -> str:
    return "'" + "".join(character * 2 if character in POWERSHELL_QUOTES else character for character in text) + "'"


def _folder_command(platform: str, folder: str, sources: Sequence[str]) -> str:
    if platform == WINDOWS_PLATFORM:
        return ("New-Item -ItemType Directory -Force -ErrorAction Stop -Path %s | Out-Null; Move-Item -LiteralPath %s -Destination %s "
                "-ErrorAction Stop" % (_powershell_quote(folder), ",".join(map(_powershell_quote, sources)), _powershell_quote(folder)))
    return "mkdir -p %s && mv %s %s" % (shlex.quote(folder), " ".join(map(shlex.quote, sources)), shlex.quote(folder))


def move_command(platform: str, moves: Sequence[Move]) -> str:
    """One command line that makes each folder and moves its sources into it, in the shell of `platform`."""
    join = POWERSHELL_JOIN if platform == WINDOWS_PLATFORM else POSIX_JOIN
    return join.join(_folder_command(platform, folder, sources) for folder, sources in moves)


def move_fix(platform: str, moves: Sequence[Move]) -> str:
    return MOVE_FIX.format(command=move_command(platform, moves))


def _commands_text(commands: Sequence[str]) -> str:
    quoted = [json.dumps(command, ensure_ascii=False) for command in commands]
    return quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + " or " + quoted[-1]


def remove_entries_fix(commands: Sequence[str]) -> str:
    """The fix of a hook file whose old entries run no file that is moved."""
    return DELETE_FROM_ITS_HOOKS.format(commands=_commands_text(commands))


def ordered_fix(platform: str, hook_files: Sequence[HookFile], moves: Sequence[Move]) -> str:
    """The fix of hook files whose old entries run files that are moved: delete the entries first, then move the files."""
    deletions = " and ".join(DELETE_FROM_HOOKS_IN.format(path=hook.path, commands=_commands_text(hook.commands)) for hook in hook_files)
    restart = RESTART_CODEX if any(hook.harness is Harness.CODEX for hook in hook_files) else ""
    consequences = CONSEQUENCE_JOIN.join(dict.fromkeys(REVERSED_ORDER_CONSEQUENCE[hook.harness] for hook in hook_files))
    return ORDERED_FIX.format(deletions=deletions, restart=restart, command=move_command(platform, moves), consequence=consequences)
