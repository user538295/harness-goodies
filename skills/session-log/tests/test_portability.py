"""Cross-platform tests of the session-log package: they run unchanged on macOS, Linux, and native Windows
(Git Bash + Windows Python), where the POSIX-only suite in test_python_adapters.py cannot even import.

Run: python3 -m unittest skills/session-log/tests/test_portability.py
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PACKAGE = REPO / "skills/session-log"
LIB = PACKAGE / "lib"
# subprocess on Windows searches System32 before PATH, where bash.exe is the WSL launcher.
BASH = shutil.which("bash")
WINDOWS = sys.platform == "win32"
TRANSIENT_FAILURE_EXIT_CODE = 75


def load_module(name):
    sys.path.insert(0, str(LIB))
    try:
        spec = importlib.util.spec_from_file_location(name, LIB / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(LIB))


pathsafe = load_module("pathsafe")
process_identity = load_module("process_identity")


def make_directory_link(target, link):
    """A link to directory ``target``: a symlink, or on Windows a junction (no admin rights needed)."""
    if WINDOWS:
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def claude_project_key(path):
    """How Claude Code names ~/.claude/projects/<key> for working directory ``path``."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


class TemporaryDirectoryTest(unittest.TestCase):
    def setUp(self):
        # realpath: Windows hands out 8.3 short names (SZENTI~1) that Claude Code never records.
        self.root = Path(os.path.realpath(tempfile.mkdtemp()))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)


class PathsafeTests(TemporaryDirectoryTest):
    def test_ensure_directory_creates_every_missing_component(self):
        target = self.root / "a" / "b" / "c"
        pathsafe.ensure_directory(str(target))
        self.assertTrue(target.is_dir())

    def test_ensure_directory_accepts_forward_slashes(self):
        target = self.root / "x" / "y"
        pathsafe.ensure_directory(str(target).replace(os.sep, "/"))
        self.assertTrue(target.is_dir())

    def test_open_directory_refuses_parent_traversal(self):
        with self.assertRaises(SystemExit):
            pathsafe.open_directory(str(self.root) + "/a/../b", create=True)

    def test_open_directory_refuses_a_linked_component(self):
        real = self.root / "real"
        real.mkdir()
        make_directory_link(real, self.root / "link")
        with self.assertRaises(OSError):
            pathsafe.ensure_directory(str(self.root / "link" / "inside"))
        self.assertFalse((real / "inside").exists())

    def test_write_file_creates_and_then_replaces_the_file(self):
        target = self.root / "dir" / "file.txt"
        pathsafe.write_file(str(target), "first")
        self.assertEqual(target.read_text(), "first\n")
        pathsafe.write_file(str(target), "second")
        self.assertEqual(target.read_text(), "second\n")
        self.assertEqual(sorted(os.listdir(target.parent)), ["file.txt"])

    def test_remove_path_removes_a_file_and_an_empty_directory(self):
        (self.root / "empty").mkdir()
        (self.root / "file").write_text("x")
        pathsafe.remove_path(str(self.root / "empty"))
        pathsafe.remove_path(str(self.root / "file"))
        pathsafe.remove_path(str(self.root / "absent"))
        self.assertEqual(os.listdir(self.root), [])

    def test_move_no_replace_keeps_an_existing_target(self):
        (self.root / "source").write_text("source")
        (self.root / "target").write_text("target")
        directory = pathsafe.open_directory(str(self.root), create=False)
        try:
            self.assertFalse(pathsafe.move_no_replace(directory, "source", "target"))
            self.assertTrue(pathsafe.move_no_replace(directory, "source", "moved"))
        finally:
            pathsafe.close_directory(directory)
        self.assertEqual((self.root / "target").read_text(), "target")
        self.assertEqual((self.root / "moved").read_text(), "source")

    def test_the_user_directory_is_trusted_for_cleanup(self):
        directory = self.root / "private"
        pathsafe.ensure_directory(str(directory))
        self.assertFalse(pathsafe._is_untrusted_cleanup_directory(os.stat(directory)))


class ProcessIdentityTests(unittest.TestCase):
    def test_a_running_process_has_a_stable_start_time(self):
        first = process_identity.start_time(os.getpid())
        self.assertTrue(first)
        self.assertEqual(first, process_identity.start_time(os.getpid()))

    def test_a_finished_process_has_no_start_time(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        self.assertIsNone(process_identity.start_time(child.pid))

    @unittest.skipUnless(WINDOWS, "macOS and Linux hooks take their parent process for the harness")
    def test_the_harness_is_the_nearest_ancestor_that_is_no_shell(self):
        # This test plays Claude Code: it starts bash, a shell, which runs the lookup from its own Windows pid.
        result = subprocess.run(
            [BASH, "-c", f'"$0" "{LIB / "process_identity.py"}" harness "$(cat /proc/$$/winpid)"', sys.executable],
            capture_output=True, text=True, check=True,
        )
        pid, start = result.stdout.rstrip("\n").split("\t")
        self.assertEqual(int(pid), os.getpid())
        self.assertEqual(start, process_identity.start_time(os.getpid()))


class PackageTest(TemporaryDirectoryTest):
    """Runs the package's bash entrypoints against a private home directory."""

    def setUp(self):
        super().setUp()
        self.home = self.root / "home"
        (self.home / ".claude").mkdir(parents=True)
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("CLAUDE")}
        self.env.update(HOME=str(self.home), TZ="UTC")

    def run_bash(self, *args, stdin="", cwd=None, env=None):
        return subprocess.run([BASH, *map(str, args)], input=stdin, capture_output=True, encoding="utf-8",
                              cwd=cwd, env=env or self.env, timeout=120)

    def session_log(self, arguments):
        return self.run_bash(PACKAGE / "install.sh", "--harness", "claude", "--arguments", arguments)


class EnableFlagAndLockTests(PackageTest):
    def test_on_status_and_off(self):
        on = self.session_log("on")
        self.assertEqual((on.returncode, on.stderr), (0, ""))
        self.assertEqual(on.stdout, "Claude Code: on — logging starts with the next prompt\n")
        self.assertTrue((self.home / ".claude/prompt-logs/.enabled").read_text().startswith("enabled:"))
        off = self.session_log("off")
        self.assertEqual((off.returncode, off.stdout, off.stderr), (0, "Claude Code: off\n", ""))
        self.assertFalse((self.home / ".claude/prompt-logs/.enabled").exists())

    def lock(self, mode):
        """Takes or releases the install lock for this test process, a live owner."""
        env = dict(self.env, SESSION_LOG_LOCK_PATH=str(self.home / ".claude/session-log/.install.lock"),
                   SESSION_LOG_LOCK_PID=str(os.getpid()), SESSION_LOG_LOCK_START=process_identity.start_time(os.getpid()))
        return subprocess.run([sys.executable, LIB / "locking.py", mode], env=env).returncode

    def test_a_lock_held_by_a_live_owner_is_busy(self):
        self.assertEqual(self.lock("acquire"), 0)
        self.assertEqual(self.lock("acquire"), TRANSIENT_FAILURE_EXIT_CODE)
        self.assertEqual(self.lock("release"), 0)
        self.assertFalse((self.home / ".claude/session-log/.install.lock").exists())

    def test_repair_waits_for_the_install_lock_and_releases_it(self):
        self.assertEqual(self.lock("acquire"), 0)
        busy = self.session_log("repair")
        self.assertEqual((busy.returncode, busy.stderr), (TRANSIENT_FAILURE_EXIT_CODE,
                         "session-log: another session-log command is already in progress for claude\n"))
        self.assertEqual(self.lock("release"), 0)
        repair = self.session_log("repair")
        self.assertEqual((repair.returncode, repair.stderr), (0, ""))
        self.assertFalse((self.home / ".claude/session-log/.install.lock").exists())


class ClaudeSettingsTests(PackageTest):
    def test_repair_removes_a_legacy_hook_registration_and_keeps_the_rest(self):
        settings = self.home / ".claude/settings.json"
        legacy = {"type": "command", "command": "bash /old/skills/session-log/adapters/claude/claude_hook.sh stop"}
        foreign = {"type": "command", "command": "echo keep"}
        settings.write_text(json.dumps({"model": "x", "hooks": {"Stop": [{"hooks": [legacy, foreign]}]}}))
        repair = self.session_log("repair")
        self.assertEqual((repair.returncode, repair.stderr), (0, ""))
        self.assertIn("removed 1 legacy session-log hook registration(s)", repair.stdout)
        self.assertEqual(json.loads(settings.read_text()), {"model": "x", "hooks": {"Stop": [{"hooks": [foreign]}]}})
        self.assertNotIn(b"\r", settings.read_bytes())
        self.assertEqual([name for name in os.listdir(settings.parent) if name.startswith(".session-log")], [])


class LegacyPackageTests(PackageTest):
    def test_repair_removes_a_seeded_copy_its_manifest_proves_unchanged(self):
        import hashlib

        copy = self.home / ".claude/skills/session-log"
        copy.mkdir(parents=True)
        skill = "<!-- universal-session-log: managed -->\n"
        (copy / "SKILL.md").write_text(skill)
        digest = hashlib.sha256((copy / "SKILL.md").read_bytes()).hexdigest()
        (copy / ".universal-session-log.manifest").write_text(
            f"format=1\nowner=universal-session-log\nversion=1\nasset=SKILL.md\nsha256={digest}\n")
        repair = self.session_log("repair")
        self.assertEqual((repair.returncode, repair.stderr), (0, ""))
        self.assertIn("removed legacy package copy", repair.stdout)
        self.assertFalse(copy.exists())


class HookLoggingTests(PackageTest):
    SESSION = "4f1c2b8e-0000-4000-8000-000000000001"

    def setUp(self):
        super().setUp()
        self.project = self.root / "work" / "my.project"
        self.project.mkdir(parents=True)
        self.transcripts = self.home / ".claude/projects" / claude_project_key(str(self.project))
        self.transcripts.mkdir(parents=True)
        self.transcript = self.transcripts / f"{self.SESSION}.jsonl"
        self.transcript.write_text(
            '{"type":"user","timestamp":"2026-10-15T10:00:00.000Z","promptSource":"typed","isMeta":false,'
            '"message":{"role":"user","content":"hello"}}\n'
            '{"type":"assistant","timestamp":"2026-10-15T10:00:05.000Z","requestId":"req_1","message":'
            '{"id":"msg_1","model":"claude-opus-4-1","role":"assistant","content":[{"type":"text","text":"hi"}],'
            '"usage":{"input_tokens":10,"output_tokens":5}}}\n')
        self.assertEqual(self.session_log("on").returncode, 0)

    def test_usage_takes_the_transcript_path_claude_code_names(self):
        usage = self.run_bash(PACKAGE / "adapters/claude/scripts/prompt_log_usage.sh", self.transcript)
        self.assertEqual(usage.returncode, 0, usage.stderr)
        self.assertIn("TOTAL (1 requests, 0 sub-agents)", usage.stdout)

    def test_usage_latest_finds_the_session_of_the_working_directory(self):
        usage = self.run_bash(PACKAGE / "install.sh", "--harness", "claude", "--arguments", "usage --latest",
                              cwd=self.project)
        self.assertEqual(usage.returncode, 0, usage.stderr)
        self.assertIn(f"{self.SESSION}.jsonl", usage.stdout)
        self.assertIn("TOTAL (1 requests, 0 sub-agents)", usage.stdout)


class ProjectKeyTests(unittest.TestCase):
    def resolve(self, path):
        library = PACKAGE / "adapters/claude/scripts/prompt_log_lib.sh"
        script = f'source "{library.as_posix()}"; resolve_project_key "$1"'
        return subprocess.run([BASH, "-c", script, "bash", path], capture_output=True, text=True,
                              check=True).stdout.rstrip("\n")

    def test_every_character_but_letters_and_digits_becomes_a_dash(self):
        self.assertEqual(self.resolve("/Users/me/my.project_x y"), "-Users-me-my-project-x-y")
        self.assertEqual(self.resolve("C:\\Users\\SZENTI~1\\dev"), "C--Users-SZENTI-1-dev")


if __name__ == "__main__":
    unittest.main()
