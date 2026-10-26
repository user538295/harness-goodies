"""Leftover scan (C1): the closed list of old-release leftovers of the invoked harness, reported with a one-line fix, never deleted.

Every test runs in a sandbox `HOME` made with `tempfile`; nothing here reads or writes the real harness folders.
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from typing import Dict, List, Tuple

from sessionlog.claude_reader import ClaudeReader
from sessionlog.commands import CommandUseCases
from sessionlog.leftover_port import Leftover
from sessionlog.leftover_scan import LeftoverScan
from sessionlog.model import Harness
from sessionlog.pricing import PricingRules
from sessionlog.state import FileStateStore
from sessionlog.sync import SyncUseCase
from sessionlog.usage_target import UsageTargetResolver
from tests.test_commands import ReportKeepingArchive
from tests.test_sync import NOW, NoLeftovers, NoProbe, _NoPrices

MOVE_FIX = "move it outside the skills folders: `%s` (a copy moved inside a skills folder may still load)"
RESTART_CODEX = "restart Codex (sessions started earlier keep running the old hook), then "
REVERSED_ORDER_CONSEQUENCE = {
    Harness.CLAUDE: "a hook whose script is gone makes Claude Code report a hook error on every prompt",
    Harness.CODEX: "a hook whose script is gone blocks every Codex prompt",
    Harness.CURSOR: "a hook whose script is gone makes Cursor block every prompt",
}
# Folders outside every harness root that hold a leftover, and where a move out of each goes.
SHARED_ANCHORS = (os.path.join(".agents"), os.path.join(".local", "share"))
OLD_RELEASE_FOLDER = "session-log-old"
ANY_FIX = "any fix text"
MARKER = "universal-session-log: managed"
OLD_CLAUDE_HOOK = "bash /home/u/.claude/skills/session-log/adapters/claude/claude_hook.sh session-start"
OLD_PROMPT_LOG_HOOK = "bash $HOME/.claude/scripts/prompt_log_save.sh"
OLD_NATIVE_HOOK = "python3 /home/u/.cursor/skills/session-log/adapters/native/session_log_hook.py cursor stop"
OLD_CODEX_HOOK = ("SESSION_LOG_OWNER=universal-session-log "
                  "sh -c 'package=$(ls -d \"$HOME\"/.codex/plugins/cache/m/codex-goodies/*/skills/session-log | tail -1);"
                  " exec python3 \"$package/adapters/native/session_log_hook.py\" codex stop'")
OWNER_ONLY_HOOK = "SESSION_LOG_OWNER=universal-session-log sh -c 'some text an older release ran'"
UNRELATED_HOOK = "bash /usr/local/bin/my-own-hook.sh"
PLUGIN_SHELL_HOOK = ('s="${CLAUDE_PLUGIN_ROOT}/skills/session-log/adapters/claude/claude_hook.sh"; '
                     "case $s in *\\\\*) s=$(printf '%s\\n' \"$s\" | tr '\\\\' /);; esac; "
                     '[ -f "$s" ] && exec bash "$s" session-start; exit 0')
NEAR_MISS_HOOKS = ("bash /opt/other/claude_hook.sh session-start", "bash /opt/tools/prompt_log_save.sh",
                   "python3 /opt/x/session_log_hook.py codex stop", "echo adapters/claude/claude_hook.sh",
                   "bash /home/u/proj/adapters/claude/claude_hook.sh stop",
                   "vim /home/u/proj/adapters/native/session_log_hook.py",
                   "SESSION_LOG_OWNER=other sh -c 'x'", "SESSION_LOG_OWNER=universal-session-log-x sh -c 'x'")

SUBROOTS = {
    Harness.CLAUDE: ".claude",
    Harness.CODEX: ".codex",
    Harness.CURSOR: ".cursor",
    Harness.OPENCODE: os.path.join(".config", "opencode"),
    Harness.OMP: os.path.join(".omp", "agent"),
}
# Every kind of every harness, as a path under a harness root, written into a root to prove only its own harness's list is read.
EVERY_KIND_PATH = ("session-maps/entry", "scripts/prompt_log_save.sh", "plugins/session-log.js", "plugins/session-log-omp.js",
                   "scripts/session_log_usage.sh", "commands/session-log.md", "commands/session-log-omp.md",
                   "extensions/session-log.js", "extensions/session-log-omp.js", "skills/session-log-omp/SKILL.md")


def _write(path: str, text: str = "") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def _snapshot(home: str) -> Dict[str, str]:
    """Every path under `home` with its content or link target, to prove nothing was deleted or changed."""
    seen: Dict[str, str] = {}
    for folder, dirs, files in os.walk(home):
        for name in dirs + files:
            full = os.path.join(folder, name)
            if os.path.islink(full):
                seen[full] = "-> " + os.readlink(full)
            elif os.path.isdir(full):
                seen[full] = "<dir>"
            else:
                with open(full, "rb") as handle:
                    seen[full] = handle.read().hex()
    return seen


class FixedScan:
    def __init__(self, found: Tuple[Leftover, ...]):
        self._found = found

    def scan(self) -> Tuple[Leftover, ...]:
        return self._found


class SandboxCase(unittest.TestCase):
    def setUp(self):
        self.home = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        patcher = mock.patch.dict(os.environ, {"HOME": self.home, "USERPROFILE": self.home})  # a stray `~` stays in the sandbox
        patcher.start()
        self.addCleanup(patcher.stop)

    def fresh_home(self) -> None:
        shutil.rmtree(self.home)
        os.makedirs(self.home)

    def root(self, harness: Harness) -> str:
        return os.path.join(self.home, SUBROOTS[harness])

    def path(self, harness: Harness, *parts: str) -> str:
        return os.path.join(self.root(harness), *parts)

    def scanner(self, harness: Harness, platform: str = "linux") -> LeftoverScan:
        return LeftoverScan(harness, self.root(harness), self.home, platform)

    def scan(self, harness: Harness) -> Tuple[Leftover, ...]:
        return self.scanner(harness).scan()

    def use_cases(self, harness: Harness, scan) -> CommandUseCases:
        root = self.root(harness)
        log_root = os.path.join(root, "prompt-logs")
        os.makedirs(log_root, exist_ok=True)
        state = FileStateStore(log_root)
        archive = ReportKeepingArchive()
        reader = ClaudeReader(root)  # the reader is irrelevant to the scan; an empty store is enough
        pricing = PricingRules(_NoPrices())
        return CommandUseCases(SyncUseCase(reader, archive, state, pricing, lambda: NOW),
                               UsageTargetResolver(reader), archive, state, pricing, lambda: NOW, log_root, scan, NoProbe())

    def reported(self, harness: Harness) -> List[Tuple[str, str]]:
        return [(item.path, item.fix) for item in self.scan(harness)]

    @staticmethod
    def hooks_document(*commands: str, key: str = "command") -> dict:
        return {"hooks": {"Stop": [{"hooks": [{"type": "command", key: command} for command in commands]}]}}

    def anchor_of(self, path: str) -> str:
        """The folder a move out of `path` keeps its relative path under: its harness root, or a shared folder's anchor."""
        for relative in list(SUBROOTS.values()) + list(SHARED_ANCHORS):
            anchor = os.path.join(self.home, relative)
            if path.startswith(anchor + os.sep):
                return anchor
        raise AssertionError("not under a known root: " + path)

    def move_command(self, paths: List[str]) -> str:
        """The POSIX command that moves `paths` outside the skills folders: into `<anchor>/session-log-old/<same relative parent>`,
        which keeps each path under its anchor so two leftovers named alike cannot collide, one `mkdir`/`mv` pair per folder."""
        folders: Dict[str, List[str]] = {}
        for path in sorted(paths):
            anchor = self.anchor_of(path)
            folder = os.path.dirname(os.path.join(anchor, OLD_RELEASE_FOLDER, os.path.relpath(path, anchor)))
            folders.setdefault(folder, []).append(path)
        return " && ".join("mkdir -p %s && mv %s %s" % (shlex.quote(folder), " ".join(map(shlex.quote, sources)), shlex.quote(folder))
                           for folder, sources in folders.items())

    def moved(self, harness: Harness, path: str) -> Tuple[str, str]:
        """The `(path, fix)` the scan reports for an old file or folder nothing in a hook file runs."""
        return path, MOVE_FIX % self.move_command([path])

    @staticmethod
    def quoted_commands(commands: Tuple[str, ...]) -> str:
        quoted = [json.dumps(command, ensure_ascii=False) for command in commands]
        return quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + " or " + quoted[-1]

    def remove_fix(self, *commands: str) -> str:
        """The fix of a hook file whose old entries run nothing that is moved."""
        return 'delete from its "hooks" every entry whose "command" is %s (keep the other entries)' % self.quoted_commands(commands)

    def ordered_fix(self, hook_files: List[Tuple[str, Harness, Tuple[str, ...]]], sources: List[str]) -> str:
        """The fix of hook files whose old entries run `sources`: delete the entries first, then move."""
        deletions = " and ".join('from "hooks" in %s every entry whose "command" is %s' % (path, self.quoted_commands(commands))
                                 for path, _, commands in hook_files)
        restart = RESTART_CODEX if any(harness is Harness.CODEX for _, harness, _ in hook_files) else ""
        consequence = "; ".join(dict.fromkeys(REVERSED_ORDER_CONSEQUENCE[harness] for _, harness, _ in hook_files))
        return ("First delete %s (keep the other entries), then %smove what they run: `%s`. Not the other way round: %s."
                % (deletions, restart, self.move_command(sources), consequence))

    def ordered(self, hook_path: str, harness: Harness, commands: Tuple[str, ...], sources: List[str]) -> Tuple[str, str]:
        return hook_path, self.ordered_fix([(hook_path, harness, commands)], sources)

    def ordered_with_file(self, path: str, hook_path: str, harness: Harness, commands: Tuple[str, ...], sources: List[str]) -> Tuple[str, str]:
        """The item whose path is `path` (a moved file) when the hook file that runs it is another harness's."""
        return path, self.ordered_fix([(hook_path, harness, commands)], sources)

    def write_json(self, harness: Harness, name: str, document: dict) -> str:
        path = self.path(harness, name)
        _write(path, json.dumps(document))
        return path

    def build_every_kind(self, harness: Harness) -> List[Tuple[str, str]]:
        """Writes each leftover kind of `harness` and returns the expected `(path, fix)` pairs. The hook files of Claude Code and
        Cursor run the seeded copy (and Claude Code's scripts), so each is one item that names the order."""
        expected: List[Tuple[str, str]] = []
        seeded = self.path(harness, "skills", "session-log")
        _write(os.path.join(seeded, "install.sh"))
        if harness is Harness.CLAUDE:
            settings = self.write_json(harness, "settings.json",
                                       self.hooks_document(OLD_CLAUDE_HOOK, OLD_PROMPT_LOG_HOOK, UNRELATED_HOOK))
            scripts = [self.path(harness, "scripts", name) for name in ("prompt_log_lib.sh", "prompt_log_save.sh")]
            for script in scripts:
                _write(script)
            expected.append(self.ordered(settings, harness, (OLD_CLAUDE_HOOK, OLD_PROMPT_LOG_HOOK), scripts + [seeded]))
            _write(self.path(harness, "scripts", "my_own_script.sh"))  # unrelated
            maps = self.path(harness, "session-maps")
            _write(os.path.join(maps, "map"))
            expected.append(self.moved(harness, maps))
        elif harness is Harness.CODEX:
            hooks = self.write_json(harness, "hooks.json", self.hooks_document(OLD_CODEX_HOOK, UNRELATED_HOOK))
            expected.append((hooks, self.remove_fix(OLD_CODEX_HOOK)))  # it runs the plugin's package, not the seeded copy
            expected.append(self.moved(harness, seeded))
        elif harness is Harness.CURSOR:
            hooks = self.write_json(harness, "hooks.json",
                                    {"version": 1, "hooks": {"stop": [{"command": OLD_NATIVE_HOOK},
                                                                      {"command": UNRELATED_HOOK}]}})
            expected.append(self.ordered(hooks, harness, (OLD_NATIVE_HOOK,), [seeded]))
        elif harness is Harness.OPENCODE:
            for name in ("plugins/session-log.js", "plugins/session-log-omp.js", "scripts/session_log_usage.sh",
                         "commands/session-log.md", "commands/session-log-omp.md"):
                leftover = self.path(harness, *name.split("/"))
                _write(leftover)
                expected.append(self.moved(harness, leftover))
            _write(self.path(harness, "plugins", "other-plugin.js"))  # unrelated
            expected.append(self.moved(harness, seeded))
        elif harness is Harness.OMP:
            for name in ("extensions/session-log.js", "extensions/session-log-omp.js"):
                leftover = self.path(harness, *name.split("/"))
                _write(leftover)
                expected.append(self.moved(harness, leftover))
            skill = self.path(harness, "skills", "session-log-omp")
            _write(os.path.join(skill, "SKILL.md"))
            expected.append(self.moved(harness, skill))
            _write(self.path(harness, "extensions", "other.js"))  # unrelated
            expected.append(self.moved(harness, seeded))
        state_folder = self.path(harness, "session-log")
        _write(os.path.join(state_folder, "plugin-root"))
        expected.append(self.moved(harness, state_folder))
        log_root = self.path(harness, "prompt-logs")  # inert files beside old-format logs stay untouched and unreported
        _write(os.path.join(log_root, ".enabled.lock"))
        _write(os.path.join(log_root, ".omp-session.dedupe"))
        _write(os.path.join(log_root, "old-format.md"))
        return expected


class EveryKindTests(SandboxCase):
    def test_each_leftover_kind_reported_with_fix_unrelated_entry_and_inert_files_not_and_nothing_deleted(self):
        for harness in Harness:
            with self.subTest(harness=harness.value):
                self.fresh_home()
                expected = sorted(self.build_every_kind(harness))
                before = _snapshot(self.home)
                use_cases = self.use_cases(harness, self.scanner(harness))

                on = use_cases.on()
                status = use_cases.status()

                self.assertEqual(expected, sorted((item.path, item.fix) for item in on.leftovers))
                self.assertEqual(expected, sorted((item.path, item.fix) for item in status.leftovers))
                reported = {item.path for item in on.leftovers}
                for unrelated in ("my_own_script.sh", "other-plugin.js", "other.js", ".enabled.lock", ".omp-session.dedupe",
                                  "old-format.md"):
                    self.assertFalse(any(path.endswith(unrelated) for path in reported), unrelated)
                after = _snapshot(self.home)
                for path, content in before.items():  # `on` adds its own flag and state files; nothing existing may go or change
                    self.assertEqual(content, after.get(path), path)

    def test_the_unrelated_entry_beside_an_old_one_stays_in_the_untouched_hook_file(self):
        settings = self.write_json(Harness.CLAUDE, "settings.json", self.hooks_document(OLD_CLAUDE_HOOK, UNRELATED_HOOK))
        before = _snapshot(self.home)
        self.use_cases(Harness.CLAUDE, self.scanner(Harness.CLAUDE)).on()
        with open(settings, encoding="utf-8") as handle:
            self.assertIn(UNRELATED_HOOK, json.load(handle)["hooks"]["Stop"][0]["hooks"][1]["command"])
        self.assertEqual(before[settings], _snapshot(self.home)[settings])

    def test_a_bare_scan_writes_nothing_anywhere(self):
        for harness in Harness:
            self.fresh_home()
            expected = self.build_every_kind(harness)
            before = _snapshot(self.home)
            self.assertEqual(sorted(expected), sorted((item.path, item.fix) for item in self.scan(harness)))
            self.assertEqual(before, _snapshot(self.home), harness.value)  # no new file, no changed file

    def test_status_while_off_and_on_while_already_on_report_the_leftovers_too(self):
        found = (Leftover("/x/old", ANY_FIX),)
        use_cases = self.use_cases(Harness.CLAUDE, FixedScan(found))
        self.assertEqual(found, use_cases.status().leftovers)  # off: never turned on
        use_cases.on()
        self.assertEqual(found, use_cases.on().leftovers)  # already on

    def test_use_cases_report_what_the_injected_scan_returns(self):
        found = (Leftover("/x/old", ANY_FIX),)
        use_cases = self.use_cases(Harness.CLAUDE, FixedScan(found))
        self.assertEqual(found, use_cases.on().leftovers)
        self.assertEqual(found, use_cases.status().leftovers)
        self.assertEqual((), self.use_cases(Harness.CLAUDE, NoLeftovers()).status().leftovers)


class PerHarnessTests(SandboxCase):
    def test_leftover_list_is_per_harness_for_the_invoked_harness_only(self):
        for harness in Harness:
            with self.subTest(harness=harness.value):
                self.fresh_home()
                for relative in EVERY_KIND_PATH:  # every harness's kinds, planted in this harness's own root
                    _write(self.path(harness, *relative.split("/")))
                for hook_file in ("settings.json", "hooks.json"):
                    self.write_json(harness, hook_file, self.hooks_document(OLD_CLAUDE_HOOK, OLD_NATIVE_HOOK))
                expected = self.build_every_kind(harness)
                self.assertEqual(sorted(expected), sorted(self.reported(harness)))


class RecognizerTests(SandboxCase):
    def test_nothing_reported_for_an_empty_home(self):
        for harness in Harness:
            self.assertEqual((), self.scan(harness), harness.value)

    def test_hook_commands_of_each_old_shape_are_recognized(self):
        shapes = (OLD_CLAUDE_HOOK, OLD_PROMPT_LOG_HOOK, "bash ~/.claude/scripts/prompt_log_stop.sh",
                  "bash \"$HOME/.claude/scripts/prompt_log_stop.sh\"", OLD_NATIVE_HOOK, OLD_CODEX_HOOK, OWNER_ONLY_HOOK,
                  PLUGIN_SHELL_HOOK,
                  r"bash C:\Users\u\.claude\scripts\prompt_log_stop.sh",
                  r"bash C:\Users\u\.claude\skills\session-log\adapters\claude\claude_hook.sh user-prompt",
                  "exec bash 'unbalanced /x/skills/session-log/adapters/claude/claude_hook.sh")
        for command in shapes:
            with self.subTest(command=command):
                self.fresh_home()
                path = self.write_json(Harness.CLAUDE, "settings.json", self.hooks_document(command))
                self.assertEqual([(path, self.remove_fix(command))], self.reported(Harness.CLAUDE))

    def test_a_hook_command_in_a_nested_or_flat_entry_of_any_event_is_recognized(self):
        documents = (
            ({"hooks": {"SessionStart": [], "Stop": [{"matcher": "x", "hooks": [{"command": OLD_CLAUDE_HOOK}]}]}}, OLD_CLAUDE_HOOK),
            ({"hooks": {"stop": [{"command": UNRELATED_HOOK}, {"command": OLD_NATIVE_HOOK}]}}, OLD_NATIVE_HOOK),
        )
        for document, command in documents:
            self.fresh_home()
            path = self.write_json(Harness.CURSOR, "hooks.json", document)
            self.assertEqual([(path, self.remove_fix(command))], self.reported(Harness.CURSOR))

    def test_a_hook_file_without_old_entries_or_unreadable_is_not_reported(self):
        settings = self.path(Harness.CLAUDE, "settings.json")
        texts = [json.dumps(self.hooks_document(UNRELATED_HOOK)), "{ not json", "[]", json.dumps({"hooks": []}), "",
                 json.dumps({"other": OLD_CLAUDE_HOOK}), json.dumps({"command": OLD_CLAUDE_HOOK}),
                 json.dumps({"hooks": {"Stop": [{"description": OLD_CLAUDE_HOOK}]}}),
                 json.dumps(self.hooks_document(*NEAR_MISS_HOOKS)), "[" * 5000 + "]" * 5000,
                 '{"hooks": ' + "[" * 5000 + "]" * 5000 + "}"]
        for text in texts:
            with self.subTest(text=text[:40]):
                _write(settings, text)
                self.assertEqual((), self.scan(Harness.CLAUDE))

    def test_a_hook_file_with_a_byte_order_mark_is_read(self):
        settings = self.path(Harness.CLAUDE, "settings.json")
        os.makedirs(os.path.dirname(settings))
        with open(settings, "wb") as handle:
            handle.write(b"\xef\xbb\xbf" + json.dumps(self.hooks_document(OLD_CLAUDE_HOOK)).encode("utf-8"))
        self.assertEqual([(settings, self.remove_fix(OLD_CLAUDE_HOOK))], self.reported(Harness.CLAUDE))

    def test_a_hook_file_that_is_a_folder_or_not_utf8_is_not_reported_and_does_not_fail(self):
        settings = self.path(Harness.CLAUDE, "settings.json")
        os.makedirs(settings)
        self.assertEqual((), self.scan(Harness.CLAUDE))
        os.rmdir(settings)
        with open(settings, "wb") as handle:
            handle.write(b"\xff\xfe\x00")
        self.assertEqual((), self.scan(Harness.CLAUDE))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs FIFOs")
    def test_a_fifo_at_the_hook_file_is_never_opened(self):
        os.makedirs(self.root(Harness.CLAUDE))
        os.mkfifo(self.path(Harness.CLAUDE, "settings.json"))
        self.assertEqual((), self.scan_without_hanging(Harness.CLAUDE))  # an open would block forever

    @unittest.skipIf(sys.platform == "win32", "symlinks need privileges on Windows")
    def test_a_hook_file_that_is_a_link_to_a_file_is_read_through_the_link(self):
        real = os.path.join(self.home, "dotfiles", "settings.json")
        _write(real, json.dumps(self.hooks_document(OLD_CLAUDE_HOOK)))
        link = self.path(Harness.CLAUDE, "settings.json")
        os.makedirs(os.path.dirname(link))
        os.symlink(real, link)
        self.assertEqual([(link, self.remove_fix(OLD_CLAUDE_HOOK))], self.reported(Harness.CLAUDE))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs FIFOs")
    def test_a_fifo_or_folder_or_link_at_a_marker_file_of_a_seeded_copy_is_never_opened(self):
        seeded = self.path(Harness.CODEX, "skills", "session-log")
        os.makedirs(os.path.join(seeded, "lib"))
        os.mkfifo(os.path.join(seeded, "SKILL.md"))
        os.mkdir(os.path.join(seeded, "lib", "folder"))
        marked = os.path.join(self.home, "marked")
        _write(marked, "# %s\n" % MARKER)
        os.symlink(marked, os.path.join(seeded, "lib", "linked"))
        self.assertEqual((), self.scan_without_hanging(Harness.CODEX))

    def scan_without_hanging(self, harness: Harness) -> Tuple[Leftover, ...]:
        """The scan in a thread, so a regression that opens a FIFO fails this test instead of blocking the whole suite."""
        outcome: List[Tuple[Leftover, ...]] = []
        failures: List[BaseException] = []

        def run() -> None:
            try:
                outcome.append(self.scan(harness))
            except BaseException as error:  # re-raised in the test thread below
                failures.append(error)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(timeout=10)
        self.assertFalse(worker.is_alive(), "the scan blocked on a special file")
        if failures:
            raise failures[0]
        return outcome[0]

    def test_the_marker_is_looked_for_only_at_the_head_of_a_file_in_the_top_bin_and_lib_folders_of_a_folder(self):
        seeded = self.path(Harness.CODEX, "skills", "session-log")
        _write(os.path.join(seeded, "SKILL.md"), "---\n" + "key: value\n" * 500 + "---\n# %s\n" % MARKER)  # past the bytes read
        _write(os.path.join(seeded, "lib", "deeper", "tool"), "# %s\n" % MARKER)  # one folder too deep
        _write(os.path.join(seeded, "adapters", "tool"), "# %s\n" % MARKER)  # a folder the scan does not enter
        self.assertEqual((), self.scan(Harness.CODEX))
        _write(os.path.join(seeded, "bin", "tool"), "# %s\n" % MARKER)
        self.assertEqual([self.moved(Harness.CODEX, seeded)], self.reported(Harness.CODEX))

    def test_a_file_at_the_seeded_copy_path_is_not_a_seeded_copy(self):
        _write(self.path(Harness.CODEX, "skills", "session-log"), "# %s\n" % MARKER)
        self.assertEqual((), self.scan(Harness.CODEX))

    def test_seeded_copy_is_reported_for_install_script_manifest_or_ownership_marker_only(self):
        seeded = self.path(Harness.CODEX, "skills", "session-log")
        _write(os.path.join(seeded, "SKILL.md"), "# a user's own skill\n")
        self.assertEqual((), self.scan(Harness.CODEX))
        _write(os.path.join(seeded, "SKILL.md"), "---\nname: x\n---\n<!-- %s -->\nbody\n" % MARKER)
        self.assertEqual([self.moved(Harness.CODEX, seeded)], self.reported(Harness.CODEX))
        for name, text in (("lib/common.sh", "# %s\n" % MARKER), ("install.sh", ""),
                           (".universal-session-log.manifest", "format=1\n")):
            shutil.rmtree(seeded)
            _write(os.path.join(seeded, *name.split("/")), text)
            self.assertEqual([self.moved(Harness.CODEX, seeded)], self.reported(Harness.CODEX), name)

    def test_marker_must_be_a_whole_line_in_a_recognized_place(self):
        seeded = self.path(Harness.CURSOR, "skills", "session-log")
        _write(os.path.join(seeded, "SKILL.md"), "mentions %s in prose\n" % MARKER)
        self.assertEqual((), self.scan(Harness.CURSOR))
        _write(os.path.join(seeded, "SKILL.md"), "# %s\r\n" % MARKER)  # the old recognizer split on newlines only
        self.assertEqual((), self.scan(Harness.CURSOR))
        _write(os.path.join(seeded, "SKILL.md"), "line one\n# %s\n" % MARKER)  # line two without a shebang
        self.assertEqual((), self.scan(Harness.CURSOR))
        _write(os.path.join(seeded, "SKILL.md"), "---\nname: unclosed\n<!-- %s -->\n" % MARKER)
        self.assertEqual((), self.scan(Harness.CURSOR))
        for text in ("// %s\n" % MARKER, "%s\n" % MARKER, "#!/bin/sh\n# %s\n" % MARKER):
            _write(os.path.join(seeded, "SKILL.md"), "plain\n")
            _write(os.path.join(seeded, "bin", "tool"), text)
            self.assertEqual([self.moved(Harness.CURSOR, seeded)], self.reported(Harness.CURSOR), text)

    @unittest.skipIf(sys.platform == "win32", "symlinks need privileges on Windows")
    def test_a_symlinked_seeded_copy_is_judged_by_its_target_and_a_dangling_one_holds_nothing(self):
        target = os.path.join(self.home, "checkout")
        _write(os.path.join(target, "install.sh"))
        link = self.path(Harness.CODEX, "skills", "session-log")
        os.makedirs(os.path.dirname(link))
        os.symlink(target, link)
        self.assertEqual([self.moved(Harness.CODEX, link)], self.reported(Harness.CODEX))
        os.remove(link)
        os.symlink(os.path.join(self.home, "gone"), link)
        self.assertEqual((), self.scan(Harness.CODEX))

    @unittest.skipIf(sys.platform == "win32", "symlinks need privileges on Windows")
    def test_a_dangling_symlink_at_a_plain_leftover_path_is_still_a_leftover_and_stays(self):
        link = self.path(Harness.OPENCODE, "plugins", "session-log.js")
        os.makedirs(os.path.dirname(link))
        os.symlink(os.path.join(self.home, "gone"), link)
        before = _snapshot(self.home)
        self.assertEqual([self.moved(Harness.OPENCODE, link)], self.reported(Harness.OPENCODE))
        self.assertEqual(before, _snapshot(self.home))

    def test_an_old_copy_is_recognized_by_its_file_set_even_when_every_ownership_marker_was_edited_away(self):
        for signature in ("bin/session-log", "adapters/claude/claude_hook.sh", "adapters/native/session_log_hook.py"):
            with self.subTest(signature=signature):
                self.fresh_home()
                seeded = self.path(Harness.CODEX, "skills", "session-log")
                _write(os.path.join(seeded, "SKILL.md"), "# edited by the user\n")
                _write(os.path.join(seeded, *signature.split("/")), "# edited too\n")
                self.assertEqual([self.moved(Harness.CODEX, seeded)], self.reported(Harness.CODEX))

    def test_a_folder_that_only_resembles_the_old_file_set_is_not_a_copy(self):
        seeded = self.path(Harness.CODEX, "skills", "session-log")
        for relative in ("bin/other-tool", "adapters/claude/readme.md", "adapters/session-log", "lib/session-log"):
            _write(os.path.join(seeded, *relative.split("/")), "# mine\n")
        self.assertEqual((), self.scan(Harness.CODEX))

    @unittest.skipIf(sys.platform == "win32", "symlinks need privileges on Windows")
    def test_a_linked_folder_inside_a_copy_is_never_entered_to_look_for_the_marker_or_the_file_set(self):
        elsewhere = os.path.join(self.home, "elsewhere")
        _write(os.path.join(elsewhere, "tool"), "# %s\n" % MARKER)
        _write(os.path.join(elsewhere, "session-log"))
        seeded = self.path(Harness.CODEX, "skills", "session-log")
        _write(os.path.join(seeded, "SKILL.md"), "# a user's own skill\n")
        for folder in ("bin", "lib", "adapters"):
            os.symlink(elsewhere, os.path.join(seeded, folder))
        self.assertEqual((), self.scan(Harness.CODEX))

    @unittest.skipIf(sys.platform == "win32", "symlinks need privileges on Windows")
    def test_a_skill_root_link_into_the_old_plugin_cache_or_release_store_is_a_copy_whatever_its_target_holds(self):
        destinations = (".claude/plugins/cache/market/claude-goodies/1.19.0/skills/session-log",
                        ".local/share/universal-session-log/releases/abc123/skills/session-log")
        link = self.path(Harness.CLAUDE, "skills", "session-log")
        os.makedirs(os.path.dirname(link))
        for destination in destinations:
            for target_exists in (False, True):
                with self.subTest(destination=destination, target_exists=target_exists):
                    target = os.path.join(self.home, *destination.split("/"))
                    shutil.rmtree(os.path.join(self.home, ".claude", "plugins"), True)
                    if target_exists:
                        _write(os.path.join(target, "SKILL.md"), "# a package without any old marker\n")
                    if os.path.lexists(link):
                        os.remove(link)
                    os.symlink(target, link)
                    expected = [self.moved(Harness.CLAUDE, link)]
                    if target_exists and destination.startswith(".local"):  # the store itself is an old folder too
                        expected.append(self.moved(Harness.CLAUDE, os.path.join(self.home, ".local", "share", "universal-session-log")))
                    self.assertEqual(sorted(expected), sorted(self.reported(Harness.CLAUDE)))

    @unittest.skipIf(sys.platform == "win32", "symlinks need privileges on Windows")
    def test_a_skill_root_link_elsewhere_or_with_dot_components_is_not_recognized_by_its_destination(self):
        link = self.path(Harness.CLAUDE, "skills", "session-log")
        os.makedirs(os.path.dirname(link))
        for destination in (os.path.join(self.home, "checkout", "skills", "session-log"),
                            os.path.join(self.home, ".claude", "plugins", "cache", "m", "codex-goodies", "1", "skills", "session-log"),
                            os.path.join(self.home, ".claude", "plugins", "cache", "m", "claude-goodies", "1", "..", "skills",
                                         "session-log")):
            if os.path.lexists(link):
                os.remove(link)
            os.symlink(destination, link)
            self.assertEqual((), self.scan(Harness.CLAUDE), destination)

    def test_names_that_only_resemble_a_leftover_are_not_reported(self):
        for relative in ("plugins/session-log.js.bak", "plugins/session-log-omp2.js", "commands/session-log.md.old",
                         "skills/session-log-omp2/SKILL.md", "extensions/xsession-log.js", "session-logs/state",
                         "session-maps-old/x", "scripts/prompt_logger.sh", "scripts/xprompt_log_x"):
            for harness in (Harness.CLAUDE, Harness.OPENCODE, Harness.OMP):
                _write(self.path(harness, *relative.split("/")))
        for harness in (Harness.CLAUDE, Harness.OPENCODE, Harness.OMP):
            self.assertEqual((), self.scan(harness), harness.value)

    def test_results_are_sorted_by_path(self):
        self.build_every_kind(Harness.OPENCODE)
        _write(os.path.join(self.home, ".agents", "skills", "session-log", "install.sh"))
        _write(os.path.join(self.home, ".local", "share", "universal-session-log", "x"))
        paths = [item.path for item in self.scan(Harness.OPENCODE)]
        self.assertGreater(len(paths), 3)
        self.assertEqual(sorted(paths), paths)


class ActionableFixTests(SandboxCase):
    COMMAND_IN_BACKTICKS = re.compile(r"`([^`]+)`")

    @unittest.skipUnless(all(shutil.which(tool) for tool in ("sh", "mkdir", "mv")), "needs sh, mkdir and mv on PATH")
    def test_running_the_move_command_of_every_old_file_or_folder_clears_the_leftover_and_loses_nothing(self):
        for harness in Harness:
            for root_name in (SUBROOTS[harness], os.path.join("with space", SUBROOTS[harness])):  # a quoted path must still work
                with self.subTest(harness=harness.value, root=root_name):
                    self.fresh_home()
                    root = os.path.join(self.home, root_name)
                    self.build_every_kind_under(harness, root)
                    scan = LeftoverScan(harness, root, self.home, "linux")
                    files_before = self.file_count(root)
                    moves = [item for item in scan.scan() if "`" in item.fix]
                    self.assertTrue(moves)
                    for item in moves:  # the seeded copy and the old state folder are both named `session-log`
                        subprocess.run(["sh", "-c", self.COMMAND_IN_BACKTICKS.search(item.fix).group(1)], check=True)
                        if item.fix.startswith("First"):
                            self.assertTrue(os.path.exists(item.path), item.path)  # the hook file is the user's to edit
                        else:
                            self.assertIn("may still load", item.fix)
                            kept = os.path.join(root, OLD_RELEASE_FOLDER, os.path.relpath(item.path, root))
                            self.assertFalse(os.path.lexists(item.path), item.path)
                            self.assertTrue(os.path.lexists(kept), kept)
                    self.assertEqual([], [item for item in scan.scan() if "`" in item.fix])
                    self.assertEqual(files_before, self.file_count(root))

    @staticmethod
    def file_count(root: str) -> int:
        return sum(len(files) for _, _, files in os.walk(root))

    def test_the_hook_fix_names_the_key_and_the_exact_command_of_every_entry_to_delete(self):
        self.write_json(Harness.CURSOR, "hooks.json", self.hooks_document(OLD_NATIVE_HOOK, UNRELATED_HOOK, OLD_CODEX_HOOK))
        (item,) = self.scan(Harness.CURSOR)
        self.assertEqual(self.remove_fix(OLD_NATIVE_HOOK, OLD_CODEX_HOOK), item.fix)
        for named in ('"hooks"', '"command"', json.dumps(OLD_NATIVE_HOOK), json.dumps(OLD_CODEX_HOOK)):
            self.assertIn(named, item.fix)
        self.assertNotIn(UNRELATED_HOOK, item.fix)

    def build_every_kind_under(self, harness: Harness, root: str) -> None:
        """`build_every_kind` for a root other than the default one."""
        default_root = self.root(harness)
        self.build_every_kind(harness)
        if root != default_root:
            os.makedirs(os.path.dirname(root), exist_ok=True)
            shutil.move(default_root, root)


class SharedRootTests(SandboxCase):
    """`~/.agents/skills/session-log` and the old release store belong to no harness: every harness reports them, because any
    harness may load the one and the other holds the packages old links pointed at."""

    def shared_copy(self) -> str:
        path = os.path.join(self.home, ".agents", "skills", "session-log")
        _write(os.path.join(path, "install.sh"))
        return path

    def store(self) -> str:
        path = os.path.join(self.home, ".local", "share", "universal-session-log")
        _write(os.path.join(path, "releases", "r1", "skills", "session-log", "SKILL.md"))
        return path

    def test_every_harness_reports_the_shared_copy_and_the_old_store_with_a_move_that_stays_outside_the_skills_folders(self):
        for harness in Harness:
            with self.subTest(harness=harness.value):
                self.fresh_home()
                copy, store = self.shared_copy(), self.store()
                before = _snapshot(self.home)
                self.assertEqual(sorted([self.moved(harness, copy), self.moved(harness, store)]), sorted(self.reported(harness)))
                self.assertEqual(before, _snapshot(self.home))

    def test_the_shared_moves_go_to_a_folder_beside_the_skills_folder_and_the_data_folder_not_into_them(self):
        copy, store = self.shared_copy(), self.store()
        fixes = dict(self.reported(Harness.CODEX))
        self.assertIn(shlex.quote(os.path.join(self.home, ".agents", OLD_RELEASE_FOLDER, "skills")), fixes[copy])
        self.assertIn(shlex.quote(os.path.join(self.home, ".local", "share", OLD_RELEASE_FOLDER)), fixes[store])

    def test_a_shared_folder_that_is_not_old_session_log_is_not_reported(self):
        _write(os.path.join(self.home, ".agents", "skills", "session-log", "SKILL.md"), "# my own skill\n")
        _write(os.path.join(self.home, ".agents", "skills", "other", "install.sh"))
        _write(os.path.join(self.home, ".local", "share", "opencode", "opencode.db"))
        _write(os.path.join(self.home, ".local", "share", "universal-session-log-x", "f"))
        for harness in Harness:
            self.assertEqual((), self.scan(harness), harness.value)

    @unittest.skipIf(sys.platform == "win32", "symlinks need privileges on Windows")
    def test_a_dangling_link_at_the_old_store_and_a_release_link_in_the_shared_skills_folder_are_reported(self):
        store = os.path.join(self.home, ".local", "share", "universal-session-log")
        os.makedirs(os.path.dirname(store))
        os.symlink(os.path.join(self.home, "gone"), store)
        link = os.path.join(self.home, ".agents", "skills", "session-log")
        os.makedirs(os.path.dirname(link))
        os.symlink(os.path.join(self.home, ".claude", "plugins", "cache", "m", "claude-goodies", "1", "skills", "session-log"), link)
        self.assertEqual(sorted([self.moved(Harness.OMP, store), self.moved(Harness.OMP, link)]), sorted(self.reported(Harness.OMP)))

    def test_a_hook_file_of_the_invoked_harness_that_runs_the_shared_copy_is_one_ordered_item_without_a_separate_copy_line(self):
        copy = self.shared_copy()
        command = "python3 /home/u/.agents/skills/session-log/adapters/native/session_log_hook.py cursor stop"
        hooks = self.write_json(Harness.CURSOR, "hooks.json", self.hooks_document(command))
        self.assertEqual([self.ordered(hooks, Harness.CURSOR, (command,), [copy])], self.reported(Harness.CURSOR))

    def test_a_hook_file_of_another_harness_that_runs_the_shared_copy_must_be_cleaned_before_the_copy_moves(self):
        copy = self.shared_copy()
        command = "python3 /home/u/.agents/skills/session-log/adapters/native/session_log_hook.py cursor stop"
        hooks = self.write_json(Harness.CURSOR, "hooks.json", self.hooks_document(command))
        before = _snapshot(self.home)
        self.assertEqual([self.ordered_with_file(copy, hooks, Harness.CURSOR, (command,), [copy])], self.reported(Harness.OPENCODE))
        self.assertEqual(before, _snapshot(self.home))
        self.assertTrue(os.path.exists(hooks))

    def test_two_hook_files_that_run_the_shared_copy_are_cleaned_in_one_item_before_the_one_move(self):
        copy = self.shared_copy()
        codex_command = ("SESSION_LOG_OWNER=universal-session-log "
                         "python3 /home/u/.agents/skills/session-log/adapters/native/session_log_hook.py codex stop")
        cursor_command = "python3 /home/u/.agents/skills/session-log/adapters/native/session_log_hook.py cursor stop"
        codex = self.write_json(Harness.CODEX, "hooks.json", self.hooks_document(codex_command))
        cursor = self.write_json(Harness.CURSOR, "hooks.json", self.hooks_document(cursor_command))
        (item,) = self.scan(Harness.CODEX)
        self.assertEqual(codex, item.path)
        self.assertEqual(self.ordered_fix([(codex, Harness.CODEX, (codex_command,)), (cursor, Harness.CURSOR, (cursor_command,))], [copy]),
                         item.fix)
        self.assertIn("restart Codex", item.fix)

    def test_the_old_store_run_by_a_hook_is_moved_only_after_the_entry_is_deleted(self):
        store = self.store()
        command = "bash /home/u/.local/share/universal-session-log/releases/r1/skills/session-log/adapters/claude/claude_hook.sh stop"
        settings = self.write_json(Harness.CLAUDE, "settings.json", self.hooks_document(command))
        self.assertEqual([self.ordered(settings, Harness.CLAUDE, (command,), [store])], self.reported(Harness.CLAUDE))


class OrderedItemTests(SandboxCase):
    def test_a_seeded_copy_that_no_hook_runs_stays_a_plain_move_line_beside_the_hook_item(self):
        seeded = self.path(Harness.CURSOR, "skills", "session-log")
        _write(os.path.join(seeded, "install.sh"))
        hooks = self.write_json(Harness.CURSOR, "hooks.json", self.hooks_document(OLD_CODEX_HOOK))
        self.assertEqual(sorted([(hooks, self.remove_fix(OLD_CODEX_HOOK)), self.moved(Harness.CURSOR, seeded)]),
                         sorted(self.reported(Harness.CURSOR)))

    def test_a_hook_that_names_a_copy_that_is_not_there_is_a_plain_hook_item(self):
        hooks = self.write_json(Harness.CURSOR, "hooks.json", self.hooks_document(OLD_NATIVE_HOOK))
        self.assertEqual([(hooks, self.remove_fix(OLD_NATIVE_HOOK))], self.reported(Harness.CURSOR))

    def test_claude_scripts_are_moved_with_their_hooks_but_stay_plain_lines_when_no_hook_runs_them(self):
        scripts = [self.path(Harness.CLAUDE, "scripts", name) for name in ("prompt_log_lib.sh", "prompt_log_save.sh")]
        for script in scripts:
            _write(script)
        settings = self.write_json(Harness.CLAUDE, "settings.json", self.hooks_document(OLD_PROMPT_LOG_HOOK))
        self.assertEqual([self.ordered(settings, Harness.CLAUDE, (OLD_PROMPT_LOG_HOOK,), scripts)], self.reported(Harness.CLAUDE))
        self.write_json(Harness.CLAUDE, "settings.json", self.hooks_document(OLD_CODEX_HOOK))  # runs the plugin's package
        self.assertEqual(sorted([(settings, self.remove_fix(OLD_CODEX_HOOK))] + [self.moved(Harness.CLAUDE, s) for s in scripts]),
                         sorted(self.reported(Harness.CLAUDE)))

    def test_a_codex_item_tells_to_restart_codex_before_the_move_and_the_others_do_not(self):
        seeded = self.path(Harness.CODEX, "skills", "session-log")
        _write(os.path.join(seeded, "install.sh"))
        command = ("SESSION_LOG_OWNER=universal-session-log "
                   "python3 /home/u/.codex/skills/session-log/adapters/native/session_log_hook.py codex stop")
        hooks = self.write_json(Harness.CODEX, "hooks.json", self.hooks_document(command))
        (item,) = self.scan(Harness.CODEX)
        self.assertEqual(self.ordered(hooks, Harness.CODEX, (command,), [seeded]), (item.path, item.fix))
        self.assertTrue(item.fix.startswith("First delete "))
        self.assertLess(item.fix.index("First delete"), item.fix.index("restart Codex"))
        self.assertLess(item.fix.index("restart Codex"), item.fix.index("`mkdir"))

    def test_the_ordered_item_lists_the_exact_commands_deduplicated_and_each_move_after_them(self):
        seeded = self.path(Harness.CURSOR, "skills", "session-log")
        _write(os.path.join(seeded, "install.sh"))
        hooks = self.write_json(Harness.CURSOR, "hooks.json", {"hooks": {"stop": [{"command": OLD_NATIVE_HOOK}],
                                                                          "start": [{"command": OLD_NATIVE_HOOK},
                                                                                    {"command": UNRELATED_HOOK}]}})
        (item,) = self.scan(Harness.CURSOR)
        self.assertEqual(1, item.fix.count(json.dumps(OLD_NATIVE_HOOK)))
        self.assertNotIn(UNRELATED_HOOK, item.fix)
        self.assertEqual((hooks, self.ordered_fix([(hooks, Harness.CURSOR, (OLD_NATIVE_HOOK,))], [seeded])), (item.path, item.fix))


class WindowsFixTests(SandboxCase):
    @staticmethod
    def quoted(text: str) -> str:
        return "'" + text.replace("'", "''") + "'"

    def powershell_move(self, folder: str, sources: List[str]) -> str:
        return ("New-Item -ItemType Directory -Force -ErrorAction Stop -Path %s | Out-Null; Move-Item -LiteralPath %s -Destination %s "
                "-ErrorAction Stop" % (self.quoted(folder), ",".join(map(self.quoted, sources)), self.quoted(folder)))

    def test_on_windows_the_move_is_powershell_with_quoted_paths_holding_spaces_and_apostrophes(self):
        home = os.path.join(self.home, "o'brien and sons")
        root = os.path.join(home, ".config", "opencode")
        command = os.path.join(root, "commands", "session-log.md")
        _write(command)
        scan = LeftoverScan(Harness.OPENCODE, root, home, "win32")
        folder = os.path.join(root, OLD_RELEASE_FOLDER, "commands")
        self.assertEqual([(command, MOVE_FIX % self.powershell_move(folder, [command]))], [(i.path, i.fix) for i in scan.scan()])

    def test_on_windows_the_ordered_item_carries_the_powershell_move_after_the_deletion(self):
        seeded = self.path(Harness.CURSOR, "skills", "session-log")
        _write(os.path.join(seeded, "install.sh"))
        hooks = self.write_json(Harness.CURSOR, "hooks.json", self.hooks_document(OLD_NATIVE_HOOK))
        (item,) = self.scanner(Harness.CURSOR, "win32").scan()
        folder = os.path.join(self.root(Harness.CURSOR), OLD_RELEASE_FOLDER, "skills")
        self.assertEqual(hooks, item.path)
        self.assertIn("`%s`" % self.powershell_move(folder, [seeded]), item.fix)
        self.assertTrue(item.fix.startswith("First delete "))
        self.assertNotIn("mkdir", item.fix)
        self.assertNotIn("&&", item.fix)

    def test_the_platform_changes_only_the_move_syntax_not_what_is_reported(self):
        self.build_every_kind(Harness.CLAUDE)
        posix = [item.path for item in self.scanner(Harness.CLAUDE, "linux").scan()]
        self.assertEqual(posix, [item.path for item in self.scanner(Harness.CLAUDE, "win32").scan()])
        self.assertEqual(posix, [item.path for item in self.scanner(Harness.CLAUDE, "darwin").scan()])


class CleanMachineTests(SandboxCase):
    def test_an_empty_home_reports_nothing_for_every_harness_and_on_and_status_print_no_leftover_section(self):
        from sessionlog import presentation
        for harness in Harness:
            with self.subTest(harness=harness.value):
                self.fresh_home()
                use_cases = self.use_cases(harness, self.scanner(harness))
                on, status = use_cases.on(), use_cases.status()
                self.assertEqual((), on.leftovers)
                self.assertEqual((), status.leftovers)
                self.assertNotIn("leftover", presentation.render_on(on).lower())
                self.assertNotIn("leftover", presentation.render_status(status).lower())

    def test_a_home_with_only_the_new_release_files_reports_nothing(self):
        for harness in Harness:
            _write(self.path(harness, "prompt-logs", ".log_enabled"))
            _write(self.path(harness, "prompt-logs", "2026-01-01", "session.md"))
            _write(self.path(harness, "skills", "my-own-skill", "SKILL.md"), "# mine\n")
        _write(os.path.join(self.home, ".agents", "skills", "other", "SKILL.md"), "# mine\n")
        _write(os.path.join(self.home, ".local", "share", "opencode", "opencode.db"))
        for harness in Harness:
            self.assertEqual((), self.scan(harness), harness.value)


if __name__ == "__main__":
    unittest.main()
