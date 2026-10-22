"""E2E of the Claude SessionStart hook (S23, and the hook half of S2): the args array of the shipped `hooks/hooks.json` run for real as a
subprocess, without a shell, in a sandbox `HOME`.

What Claude Code does is reproduced as far as a test can: `${CLAUDE_PLUGIN_ROOT}` in the args is replaced by the root of an installed
copy of the plugin (a folder name with a space and an apostrophe, holding the skill without its tests), the command `python3` is
found through `PATH`, stdin carries the JSON a SessionStart hook receives, stdout and stderr are captured, and the hook's `timeout`
limits the run. The guard, the entry script, the Claude transcript tree built under the sandbox, the clock and the file system are
real; only the environment is shaped, as in the skeleton e2e: a fixed time zone and a cp1252 console in the C locale (S54).

`PATH` holds one folder with a `python3` link to the interpreter that runs the tests (which must be Python 3.14 or newer, the entry
file's floor), so the hook needs no other command. The real-Claude manual checks of S23 (start, resume, clear, compact with a large
backlog; `python3` missing) are not part of this file.

On macOS, from the repository root:

    python3 -m unittest discover -s skills/session-log/tests -t skills/session-log -p "test_e2e_claude_hook.py" -v

On Linux in Docker, from the repository root (the Linux floor image; the repository is mounted read-only, nothing is written to it):

    docker run --rm -v "$PWD":/repo:ro -w /repo/skills/session-log -e PYTHONDONTWRITEBYTECODE=1 python:3.14-slim \\
        python3 -m unittest discover -s tests -t . -p "test_e2e_claude_hook.py" -v
"""
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from tests import test_e2e_skeleton as skeleton
from tests.test_claude_hooks_config import (PLUGIN_FOLDER_WITH_SPACE_AND_APOSTROPHE, PLUGIN_ROOT_VARIABLE, load_hooks,
                                            session_start_hook)

EMPTY_PLUGIN_FOLDER = "empty plug in's root"
SKILL_RELATIVE = Path("skills", "session-log")
NOT_INSTALLED_FOLDERS = ("tests", "__pycache__")
HOOK_BIN_FOLDER = "hook-bin"
HOOK_EVENT = "SessionStart"
HOOK_SESSION_ID = "claude-session"  # the hook syncs every session logged since `on`, not only the one that starts
STARTUP_SOURCE = "startup"
# SessionStart's `source`: how the session started (the four cases of S23)
START_SOURCES = (STARTUP_SOURCE, "resume", "clear", "compact")
SILENT_SUCCESS = (0, b"", b"")


def session_of(source):
    """A session id that is not the hook payload's own and never holds `_` (a saved file name is `session_<stamp>_<id>`)."""
    return source + "-session"


class ClaudeHookTests(skeleton.SkeletonCase):
    """The shipped SessionStart hook in a sandbox: an installed plugin copy and a `PATH` that finds only `python3`."""

    def setUp(self):
        super().setUp()
        self.plugin_root = os.path.join(self.home, PLUGIN_FOLDER_WITH_SPACE_AND_APOSTROPHE)
        self.install_plugin(self.plugin_root)
        self.hook_bin = os.path.join(self.home, HOOK_BIN_FOLDER)
        os.makedirs(self.hook_bin)
        os.symlink(sys.executable, os.path.join(self.hook_bin, session_start_hook()["command"]))

    @staticmethod
    def install_plugin(plugin_root):
        """Copies the skill, without its tests, to `skills/session-log` under `plugin_root`."""
        skill = Path(plugin_root, SKILL_RELATIVE)
        skill.parent.mkdir(parents=True)
        shutil.copytree(skeleton.SKILL_DIR, skill, ignore=shutil.ignore_patterns(*NOT_INSTALLED_FOLDERS))

    def run_hook(self, source, plugin_root):
        """(exit code, stdout bytes, stderr bytes) of the shipped hook as Claude Code starts it: `command` + `args`, no shell."""
        hook = session_start_hook()
        args = [argument.replace(PLUGIN_ROOT_VARIABLE, plugin_root) for argument in hook["args"]]
        payload = {"session_id": HOOK_SESSION_ID, "cwd": self.project, "hook_event_name": HOOK_EVENT, "source": source}
        env = dict(skeleton.HOSTILE_CONSOLE_ENV, HOME=self.home, PATH=self.hook_bin)
        result = subprocess.run([hook["command"]] + args, env=env, cwd=self.project, input=json.dumps(payload).encode("utf-8"),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=hook["timeout"])
        return result.returncode, result.stdout, result.stderr

    def read_log_entries(self):
        """Every file with its bytes and every folder under the log folder, except the sync lock: the full state of the logs."""
        folders = {str(path): None for path in Path(self.log_root).rglob("*") if path.is_dir()}
        return {**self.log_tree(), **folders}

    def test_hook_claude_session_start_syncs_prints_nothing_for_start_resume_clear_compact(self):
        [group] = load_hooks()[HOOK_EVENT]
        self.assertEqual({"hooks"}, set(group))  # no matcher: every way a session starts
        self.assertIs(True, session_start_hook()["async"])
        self.run_ok("on")
        archived = {}
        for source in START_SOURCES:
            session = session_of(source)
            self.write_session(session)
            self.assertNotIn(session, self.saved_logs())  # written after the last sync: only the hook can archive it
            self.assertEqual(SILENT_SUCCESS, self.run_hook(source, self.plugin_root), msg=source)
            self.assertIn(skeleton.PROMPT_TEXT, self.saved_logs()[session], msg=source)
            self.assertIn(skeleton.PROMPT_TEXT, self.saved_reports()[session], msg=source)
            for earlier, (log, report) in archived.items():
                self.assertEqual(log, self.saved_logs()[earlier])
                self.assertEqual(report, self.saved_reports()[earlier])
            archived[session] = (self.saved_logs()[session], self.saved_reports()[session])
        self.assertEqual({session_of(source) for source in START_SOURCES}, set(self.saved_logs()))
        self.assertEqual({session_of(source) for source in START_SOURCES}, set(self.saved_reports()))

    def test_missing_script_guard_exits_0_silent_and_after_off_hook_writes_nothing(self):
        self.run_ok("on")
        self.write_session(skeleton.ACTIVE_SESSION)
        empty_plugin_root = os.path.join(self.home, EMPTY_PLUGIN_FOLDER)
        os.makedirs(empty_plugin_root)

        before = self.read_log_entries()
        self.assertEqual(SILENT_SUCCESS, self.run_hook(STARTUP_SOURCE, empty_plugin_root))
        self.assertEqual(before, self.read_log_entries())
        self.assertEqual({}, self.saved_logs())

        # the installed hook does sync while on, so the silence after `off` is the gate and not a broken hook
        self.assertEqual(SILENT_SUCCESS, self.run_hook(STARTUP_SOURCE, self.plugin_root))
        self.assertIn(skeleton.PROMPT_TEXT, self.saved_logs()[skeleton.ACTIVE_SESSION])

        self.run_ok("off")
        self.assertFalse(os.path.exists(self.on_flag))
        self.write_session(skeleton.NEWER_SESSION, skeleton.NEWER_PROMPT_AT)
        kept = self.read_log_entries()
        for source in START_SOURCES:
            self.assertEqual(SILENT_SUCCESS, self.run_hook(source, self.plugin_root), msg=source)
            self.assertEqual(kept, self.read_log_entries(), msg=source)
        self.assertEqual({skeleton.ACTIVE_SESSION}, set(self.saved_logs()))


if __name__ == "__main__":
    unittest.main()
