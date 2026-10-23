"""E2E of the scripted part of the S30 smoke: the skill as a harness caches an installed plugin, run for real in a sandbox `HOME`.

The skill is copied, without its tests, to the folder each harness keeps an installed plugin in, and `session_log.py <harness> on` and
`usage <session id>` run from that copy as real subprocesses over a store in the shape the harness writes: a Claude transcript tree with a
sub-agent file, a Codex rollout, an OMP session with its child, an OpenCode database, Cursor hook records. Nothing real is read or written;
every harness gets its own sandbox `HOME`. A Codex sub-agent rollout is not staged here (`test_codex_subagents` covers it).

The folders: Codex, OMP and OpenCode are the layouts the T-8 smoke recorded on the Mac for an install from the local checkout (OpenCode's
folder is `git-harness-goodies-<hash>/<install time in ms>/node_modules/opencode-goodies`, so the two parts shown are an example). Claude
Code reads a folder-sourced marketplace in place and caches nothing, so its entry is the GitHub-install path the skill locators search.
Cursor's is the documented cache layout, not a recorded one: the smoke ran Cursor with `--plugin-dir` only. `session_log.py` finds its
own package and `prices.json` next to itself, nothing else by its location, so what this proves is that the copy without `tests/` runs
from a relocated folder with no write into it; the SKILL.md locator is covered by `test_locator.sh`.

Claude's session is dated 2099, so `on` archives it and the saved log and report are checked; the hook test archives Cursor's records the
same way. The other fixtures predate the on-time of this run (the sandbox runs on the real clock, so the host clock must be later than
2026-10-13), so their `usage <id>` is the report computed live from the store and never saved (Q29); the test asserts that nothing was
archived for them, and their archive path is covered by the entry tests, which inject a clock.

The hook test is the Cursor half of S30 that a script can check: the installed `hook` launcher is executable and runs as the shipped
`hooks.json` commands (all five events) under `sh`, a sub-agent's hook call is filed in its parent's record folder, and `usage` shows the
sub-agent from those records. The mode is that of the copied checkout; that a marketplace install keeps mode 755 is checked by T-13 on the public install.
Real Cursor's own behaviour is manual too: whether it waits for the `sessionStart` hook, and whether a sub-agent's own calls arrive under
its own conversation id (the hook files every call under the `conversation_id` of the input, so the guarantee here is for the input shape
the hook was written for).

On macOS, from the repository root:

    python3 -m unittest discover -s skills/session-log/tests -t skills/session-log -p "test_e2e_installed_copy.py" -v

On Linux in Docker, from the repository root (the Linux floor image; the repository is mounted read-only, nothing is written to it):

    docker run --rm -v "$PWD":/repo:ro -w /repo/skills/session-log -e PYTHONDONTWRITEBYTECODE=1 python:3.14-slim \\
        python3 -m unittest discover -s tests -t . -p "test_e2e_installed_copy.py" -v
"""
import json
import os
import shutil
import subprocess
import sys
import types
import unittest
from pathlib import Path

import session_log
from tests import test_codex_entry, test_cursor_entry, test_omp_entry, test_opencode_entry
from tests import test_e2e_skeleton as skeleton
from tests.cursor_record_files import CONVERSATION, prompt_input, response_input, stop_input, subagent_input, write_record
from tests.omp_records import CHILD_FIXTURE, FIXTURE_ROOT_ID, FIXTURE_ROOT_STEM, PROJECT, ROOT_FIXTURE, read_text
from tests.opencode_db import OpenCodeDatabase, folder_snapshot
from tests.test_e2e_claude_hook import NOT_INSTALLED_FOLDERS, SKILL_RELATIVE
from tests.test_e2e_cursor_launchers import DIRNAME_COMMAND, SH_EXECUTABLE, event_of, shipped_command
from tests.test_e2e_status import AGENT_FILE, agent_transcript
from tests.test_cursor_hooks_config import EVENTS

MARKETPLACE = "user538295"
VERSION = "2.1.1"
OPENCODE_CACHE_FOLDER = "git-harness-goodies-29795200def4"
OPENCODE_INSTALL_TIME = "1792778595801"
CURSOR_COMMIT = "0123456789abcdef0123456789abcdef01234567"
# harness -> the install root below `HOME` (the folder holding `skills/`)
INSTALL_ROOTS = {
    "claude": (".claude", "plugins", "cache", MARKETPLACE, "claude-goodies", VERSION),
    "codex": (".codex", "plugins", "cache", MARKETPLACE, "codex-goodies", VERSION),
    "omp": (".omp", "plugins", "cache", "plugins", "%s___omp-goodies___%s" % (MARKETPLACE, VERSION)),
    "opencode": (".cache", "opencode", "npm", OPENCODE_CACHE_FOLDER, OPENCODE_INSTALL_TIME, "node_modules", "opencode-goodies"),
    "cursor": (".cursor", "plugins", "cache", MARKETPLACE, "cursor-goodies", CURSOR_COMMIT),
}
OMP_AGENT_ROOT = (".omp", "agent")
OPENCODE_DATA_ROOT = (".local", "share", "opencode")
# harness -> the folder holding its `.log_enabled` flag and archived logs
LOG_ROOTS = {
    "claude": (".claude", "prompt-logs"),
    "codex": (".codex", "prompt-logs"),
    "omp": OMP_AGENT_ROOT + ("prompt-logs",),
    "opencode": (".config", "opencode", "prompt-logs"),
    "cursor": (".cursor", "prompt-logs"),
}
CURSOR_CHILD = "child-a"  # the sub-agent conversation of the staged Cursor records
# harness -> the session `usage` is asked for and the texts of its report
EXPECTED_REPORTS = {
    "claude": (skeleton.ACTIVE_SESSION, [skeleton.PROMPT_TEXT, "TOTAL (1 requests, 1 sub-agents)", "price: $", "model: " + skeleton.MODEL]),
    "codex": (test_codex_entry.ROOT_ID, [test_codex_entry.TOTAL_TOKENS, "model: gpt-5.6-sol, effort: high", "price: $"]),
    "omp": (FIXTURE_ROOT_ID, [test_omp_entry.PROMPT_ONE_TOKENS, test_omp_entry.PROMPT_TWO_TOKENS, test_omp_entry.CHILD_TOKENS,
                              test_omp_entry.TOTAL_TOKENS, "price: $"]),
    "opencode": (test_opencode_entry.ROOT_ID, [test_opencode_entry.TOTAL_TOKENS, "TOTAL (2 requests, 1 sub-agents)",
                                               "%s, price: $0.50, model: anthropic/claude-opus-5-5" % test_opencode_entry.FIRST_PROMPT_TOKENS]),
    "cursor": (CONVERSATION, [test_cursor_entry.TOKEN_LINE, "sub-agent: explore (%s)" % CURSOR_CHILD, "(incomplete)"]),
}
ON_LINE_START = "Session logging is on since "
CURSOR_RECORDS = ".records"
SUB_AGENT_CONVERSATION = "sub-agent-conv-9"
SUB_AGENT_TYPE = "explore"
SUB_AGENT_DURATION_MS = 1000
GENERATION = "g1"
FIRST_PROMPT = "first prompt"
FIRST_REPLY = "first reply"
CURSOR_SUB_AGENT_LINE = "sub-agent: %s (%s)" % (SUB_AGENT_TYPE, SUB_AGENT_CONVERSATION)
CODEX_SESSION_FILE = os.path.join(".codex", "sessions", test_codex_entry.DAY_FOLDER, test_codex_entry.ROOT_NAME)
OMP_CHILD_NAME = "ChildTask"
TOOLS_FOLDER = "tools"
SAVED_SUFFIXES = (skeleton.LOG_SUFFIX, skeleton.REPORT_SUFFIX)


class InstalledCopyCase(skeleton.SkeletonCase):
    def start_new_sandbox(self):
        """A fresh sandbox `HOME` (and project, stub folder, log roots) so one harness's state never reaches the next."""
        skeleton.SkeletonCase.setUp(self)

    def install(self, harness):
        """Copies the skill, without its tests, to the folder `harness` keeps an installed plugin in; returns the skill directory."""
        skill = Path(self.home, *INSTALL_ROOTS[harness], SKILL_RELATIVE)
        skill.parent.mkdir(parents=True)
        shutil.copytree(skeleton.SKILL_DIR, skill, ignore=shutil.ignore_patterns(*NOT_INSTALLED_FOLDERS))
        return str(skill)

    def environment(self, path):
        return dict(skeleton.HOSTILE_CONSOLE_ENV, HOME=self.home, PATH=path)

    def run_installed(self, skill, harness, *arguments):
        """The stdout of `session_log.py <harness> <arguments>` run from the installed copy; it must exit 0 and print nothing on stderr."""
        env = self.environment(self.stub_dir + os.pathsep + os.environ.get("PATH", ""))
        result = subprocess.run([sys.executable, os.path.join(skill, "session_log.py"), harness] + list(arguments), env=env, cwd=self.project,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=skeleton.SUBPROCESS_TIMEOUT)
        self.assertEqual((0, b""), (result.returncode, result.stderr), msg=result.stdout)
        return result.stdout.decode("utf-8")

    def log_root_of(self, harness):
        return os.path.join(self.home, *LOG_ROOTS[harness])

    def saved_files_of(self, harness):
        return sorted(str(path) for suffix in SAVED_SUFFIXES for path in Path(self.log_root_of(harness)).rglob("*" + suffix))


class InstalledCopyUsageTests(InstalledCopyCase):
    def stage_claude(self):
        self.write_session(skeleton.ACTIVE_SESSION)
        agent = os.path.join(os.path.dirname(self.project_file(skeleton.ACTIVE_SESSION)), skeleton.ACTIVE_SESSION, "subagents", AGENT_FILE)
        os.makedirs(os.path.dirname(agent))
        Path(agent).write_text(agent_transcript(skeleton.ACTIVE_PROMPT_AT), encoding="utf-8")
        os.utime(agent, (skeleton.FUTURE_FILE_TIME, skeleton.FUTURE_FILE_TIME))

    def stage_codex(self):
        path = os.path.join(self.home, CODEX_SESSION_FILE)
        os.makedirs(os.path.dirname(path))
        shutil.copy(test_codex_entry.FIXTURE, path)

    def stage_omp(self):
        sessions = os.path.join(self.home, *OMP_AGENT_ROOT, "sessions", PROJECT)
        child = os.path.join(sessions, FIXTURE_ROOT_STEM, OMP_CHILD_NAME + ".jsonl")
        os.makedirs(os.path.dirname(child))
        Path(os.path.join(sessions, FIXTURE_ROOT_STEM + ".jsonl")).write_text(read_text(ROOT_FIXTURE), encoding="utf-8", newline="")
        Path(child).write_text(read_text(CHILD_FIXTURE), encoding="utf-8", newline="")

    def stage_opencode(self):
        OpenCodeDatabase(os.path.join(self.home, *OPENCODE_DATA_ROOT, "opencode.db")).fixture()

    def stage_cursor(self):
        records = os.path.join(self.log_root_of("cursor"), CURSOR_RECORDS)
        write_record(records, "beforeSubmitPrompt", 0, prompt_input(GENERATION, FIRST_PROMPT))
        write_record(records, "afterAgentResponse", 1, response_input(GENERATION, FIRST_REPLY))
        write_record(records, "stop", 2, stop_input(GENERATION, **test_cursor_entry.TOKENS))
        write_record(records, "subagentStop", 1, subagent_input(CURSOR_CHILD, subagent_type=SUB_AGENT_TYPE, duration_ms=SUB_AGENT_DURATION_MS))

    def test_every_supported_harness_has_a_layout_and_an_expectation(self):
        self.assertEqual(set(session_log.SUPPORTED_HARNESS_ROOTS), set(INSTALL_ROOTS))
        self.assertEqual(set(INSTALL_ROOTS), set(LOG_ROOTS))
        self.assertEqual(set(INSTALL_ROOTS), set(EXPECTED_REPORTS))

    def test_installed_copy_runs_on_and_usage_per_harness_in_sandbox_home(self):
        for harness in INSTALL_ROOTS:
            with self.subTest(harness=harness):
                self.start_new_sandbox()
                skill = self.install(harness)
                getattr(self, "stage_" + harness)()
                session_id, expected = EXPECTED_REPORTS[harness]

                installed = folder_snapshot(skill)
                on_lines = self.run_installed(skill, harness, "on").splitlines()
                self.assertTrue(on_lines and on_lines[0].startswith(ON_LINE_START), msg=on_lines)
                self.assertTrue(os.path.isfile(os.path.join(self.log_root_of(harness), skeleton.ON_FLAG)))

                report = self.run_installed(skill, harness, "usage", session_id)

                self.assertIn("harness: %s\n" % harness, report)
                for text in expected:
                    self.assertIn(text, report)
                if harness == "claude":  # active after the on-time: `on` archived it, and `usage` prints the saved report
                    log = self.saved_logs()[session_id]
                    self.assertIn(skeleton.PROMPT_TEXT, log)
                    self.assertIn(" response\nreply\n", log)
                    self.assertEqual(report, self.saved_reports()[session_id])
                    self.assertEqual(report, self.run_installed(skill, "claude", "usage"))  # no argument: the project's latest session
                else:  # a live report, never saved
                    self.assertEqual([], self.saved_files_of(harness))
                self.assertEqual(installed, folder_snapshot(skill))  # the entry wrote nothing into the install
                self.assert_python_never_called()


@unittest.skipUnless(os.path.exists(DIRNAME_COMMAND), "%s is missing on this host" % DIRNAME_COMMAND)
class InstalledCursorHookTests(InstalledCopyCase):
    def setUp(self):
        super().setUp()
        self.skill = self.install("cursor")
        # `shipped_command` reads only `install_root` of the launchers file's sandbox
        self.plugin_root = types.SimpleNamespace(install_root=os.path.join(self.home, *INSTALL_ROOTS["cursor"]))
        self.records = os.path.join(self.log_root_of("cursor"), CURSOR_RECORDS)
        tools = os.path.join(self.home, TOOLS_FOLDER)  # the only commands on `PATH`: what the launcher needs, a Python 3.14+ as `python3`
        os.makedirs(tools)
        os.symlink(DIRNAME_COMMAND, os.path.join(tools, "dirname"))
        os.symlink(sys.executable, os.path.join(tools, "python3"))
        self.tools = tools

    def run_hook(self, event, payload):
        """Runs the shipped hook command for `event` as `sh -c` does, `payload` on stdin; it must exit 0 and print nothing."""
        result = subprocess.run([SH_EXECUTABLE, "-c", shipped_command(self.plugin_root, event)], env=self.environment(self.tools),
                                cwd=self.project, input=json.dumps(payload).encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=skeleton.SUBPROCESS_TIMEOUT)
        self.assertEqual((0, b"", b""), (result.returncode, result.stdout, result.stderr), msg=event)

    def cursor_sync_time(self):
        state = Path(self.log_root_of("cursor"), skeleton.SYNC_STATE).read_text(encoding="utf-8")
        return json.loads(state)["lastSyncAt"]

    def test_installed_cursor_hook_is_executable_and_subagent_writes_no_record_folder_under_own_conversation_id(self):
        self.assertTrue(os.access(os.path.join(self.skill, "hook"), os.X_OK), "the installed hook is not executable")
        installed = folder_snapshot(self.skill)

        self.run_installed(self.skill, "cursor", "on")
        self.assertTrue(os.path.isfile(os.path.join(self.log_root_of("cursor"), skeleton.ON_FLAG)))
        synced_before = self.cursor_sync_time()
        self.run_hook("sessionStart", {"conversation_id": CONVERSATION})
        self.assertNotEqual(synced_before, self.cursor_sync_time())  # the launcher reached the entry, which synced
        self.run_hook("beforeSubmitPrompt", prompt_input(GENERATION, FIRST_PROMPT))
        self.run_hook("afterAgentResponse", response_input(GENERATION, FIRST_REPLY))
        self.run_hook("subagentStop", subagent_input(SUB_AGENT_CONVERSATION, subagent_type=SUB_AGENT_TYPE, duration_ms=SUB_AGENT_DURATION_MS))
        self.run_hook("stop", stop_input(GENERATION, **test_cursor_entry.TOKENS))

        self.assertEqual([CONVERSATION], os.listdir(self.records))  # `ls .records/`: the parent's folder only
        self.assertFalse(os.path.exists(os.path.join(self.records, SUB_AGENT_CONVERSATION)))
        events = sorted(event_of(name) for name in os.listdir(os.path.join(self.records, CONVERSATION)))
        self.assertEqual(sorted(set(EVENTS) - {"sessionStart"}), events)  # sessionStart syncs and writes no record
        report = self.run_installed(self.skill, "cursor", "usage", CONVERSATION)  # syncs: the records become a log and a report
        self.assertIn(CURSOR_SUB_AGENT_LINE, report)  # the sub-agent's call is a record of its parent
        self.assertIn(test_cursor_entry.TOKEN_LINE, report)
        self.assertIn(FIRST_PROMPT, report)
        (log_path,) = [path for path in self.saved_files_of("cursor") if path.endswith(skeleton.LOG_SUFFIX)]
        log = Path(log_path).read_text(encoding="utf-8")
        self.assertIn("%s\n" % FIRST_PROMPT, log)
        self.assertIn(" response\n%s\n" % FIRST_REPLY, log)
        self.assertEqual(installed, folder_snapshot(self.skill))  # the launcher and the entry wrote nothing into the install


if __name__ == "__main__":
    unittest.main()
