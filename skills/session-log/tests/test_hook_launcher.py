"""The `hook` launcher on the host's POSIX shells: it finds `session_log.py` beside the file it really is, also when it is started through
a symbolic link, and it never prints anything, whatever `PATH` holds (the brief: a launcher exits 0 silently). The interpreter is a stub that
appends its arguments to a file, so the tests see whether the entry script would have been started. The wider launcher behavior
(logging off, a missing Python, the bytes of the hook input, fish, pwsh) is in `test_e2e_cursor_launchers`."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests import test_e2e_skeleton as skeleton

SKILL_DIR = skeleton.SKILL_DIR
LAUNCHER_NAME = "hook"
ENTRY_NAME = "session_log.py"
SHELLS = ("/bin/sh", "/bin/dash", "/bin/zsh")
SYSTEM_PATH = "/usr/bin:/bin"
MISSING_PATH = "/nonexistent-session-log-path"
ON_FLAG = ".log_enabled"
EVENT_ARGUMENTS = ("cursor", "stop")
STUB_PYTHON = """#!/bin/sh
printf '%s\\n' "$*" >> "$CALLS"
"""


class HookLauncherCase(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.home = os.path.join(self.root, "home")
        self.calls = os.path.join(self.root, "calls")
        self.stub_bin = os.path.join(self.root, "bin")
        self.skill = os.path.join(self.root, "plugin dir's", "skills", "session-log")
        for folder in (self.home, self.stub_bin, self.skill):
            os.makedirs(folder)
        stub = Path(self.stub_bin, "python3")
        stub.write_text(STUB_PYTHON, encoding="utf-8")
        stub.chmod(skeleton.EXECUTABLE_MODE)
        shutil.copy2(SKILL_DIR / LAUNCHER_NAME, self.skill)
        Path(self.skill, ENTRY_NAME).write_text("", encoding="utf-8")
        self.launcher = os.path.join(self.skill, LAUNCHER_NAME)
        self.path = "%s:%s" % (self.stub_bin, SYSTEM_PATH)
        self.turn_logging_on()

    def turn_logging_on(self):
        logs = os.path.join(self.home, ".cursor", "prompt-logs")
        os.makedirs(logs, exist_ok=True)
        Path(logs, ON_FLAG).write_text("on\n", encoding="utf-8")

    def run_launcher(self, shell, launcher, path, cwd=None):
        """(exit code, stdout, stderr) of `shell <launcher> cursor stop`, or of the launcher run by itself when `shell` is None. `started()`
        then lists what this run alone started."""
        if os.path.exists(self.calls):
            os.remove(self.calls)
        argv = ([shell] if shell else []) + [launcher, *EVENT_ARGUMENTS]
        result = subprocess.run(argv, env={"HOME": self.home, "PATH": path, "CALLS": self.calls}, cwd=cwd or self.home, input=b"{}",
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        return result.returncode, result.stdout.decode(), result.stderr.decode()

    def started(self):
        """The argument lines the stub interpreter was started with, the entry script's path normalized (a link relative to its folder
        leaves `..` parts in it)."""
        lines = Path(self.calls).read_text(encoding="utf-8").splitlines() if os.path.exists(self.calls) else []
        return ["%s hook %s" % (os.path.normpath(script), arguments) for script, _, arguments in (line.partition(" hook ") for line in lines)]

    def entry_start(self):
        return "%s hook %s" % (os.path.join(self.skill, ENTRY_NAME), " ".join(EVENT_ARGUMENTS))

    def link(self, name, target):
        """A symbolic link `name` in a folder of its own, away from the skill; returns its path."""
        folder = os.path.join(self.root, "links")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        os.symlink(target, path)
        return path

    def shells(self):
        """The POSIX shells of `SHELLS` this host has; a test that finds none is skipped."""
        available = [shell for shell in SHELLS if os.path.exists(shell)]
        if not available:
            self.skipTest("no POSIX shell of %s is installed" % (SHELLS,))
        return available

    def assert_silent_and_starts(self, launcher, expected_starts, path=None, cwd=None):
        """The launcher, by itself and through each shell, exits 0 with no output and starts the stub interpreter as `expected_starts`."""
        for shell in self.shells() + [None]:
            with self.subTest(shell=shell):
                self.assertEqual((0, "", ""), self.run_launcher(shell, launcher, path or self.path, cwd))
                self.assertEqual(expected_starts, self.started())


class SymlinkTest(HookLauncherCase):
    def test_the_launcher_started_by_its_own_path_finds_the_entry_script_beside_it(self):
        self.assert_silent_and_starts(self.launcher, [self.entry_start()])

    def test_the_launcher_started_by_a_bare_name_in_its_folder_finds_the_entry_script_there(self):
        for shell in self.shells():  # the file name alone has no folder part: it is the current folder
            with self.subTest(shell=shell):
                self.assertEqual((0, "", ""), self.run_launcher(shell, LAUNCHER_NAME, self.path, cwd=self.skill))
                self.assertEqual(["%s hook cursor stop" % ENTRY_NAME], self.started())

    def test_a_link_to_the_launcher_finds_the_entry_script_beside_the_launcher_not_beside_the_link(self):
        absolute = self.link("absolute", self.launcher)
        relative = self.link("relative", os.path.relpath(self.launcher, os.path.dirname(absolute)))
        chained = self.link("chained", absolute)
        for launcher in (absolute, relative, chained):
            self.assert_silent_and_starts(launcher, [self.entry_start()])

    def test_a_relative_link_to_a_link_in_another_folder_is_followed_from_the_folder_of_each_link(self):
        inner = self.link("inner", self.launcher)
        outer = os.path.join(self.root, "outer", "outer")
        os.makedirs(os.path.dirname(outer))
        os.symlink(os.path.join("..", "links", os.path.basename(inner)), outer)
        self.assert_silent_and_starts(outer, [self.entry_start()])

    def test_a_chain_of_links_that_only_loops_when_followed_by_name_ends_silently_without_starting_python(self):
        """`real/sub/a` -> `../b`, a copy of the launcher: the file system opens it, but read by name `folder_link/../b` is `<root>/b`,
        which links back to `folder_link/a`, so following by name would never end."""
        real = os.path.join(self.root, "real")
        os.makedirs(os.path.join(real, "sub"))
        shutil.copy2(self.launcher, os.path.join(real, "b"))
        os.symlink(os.path.join("..", "b"), os.path.join(real, "sub", "a"))
        os.symlink(os.path.join(real, "sub"), os.path.join(self.root, "folder_link"))
        os.symlink(os.path.join("folder_link", "a"), os.path.join(self.root, "b"))
        self.assert_silent_and_starts(os.path.join(self.root, "folder_link", "a"), [])


class MinimalPathTest(HookLauncherCase):
    def test_a_path_that_holds_nothing_makes_no_noise_with_logging_on_or_off(self):
        self.assert_silent_and_starts(self.launcher, [], MISSING_PATH)
        Path(self.home, ".cursor", "prompt-logs", ON_FLAG).unlink()
        self.assert_silent_and_starts(self.launcher, [], MISSING_PATH)

    def test_a_link_with_a_path_that_holds_nothing_makes_no_noise_either(self):
        self.assert_silent_and_starts(self.link("absolute", self.launcher), [], MISSING_PATH)

    def test_a_path_with_only_the_interpreter_still_starts_it(self):
        self.assert_silent_and_starts(self.launcher, [self.entry_start()], self.stub_bin)


if __name__ == "__main__":
    unittest.main()
