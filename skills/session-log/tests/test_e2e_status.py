"""E2E of the full `status` output (S3) for a store-based harness (Claude) and for Cursor: `session_log.py <harness> status` and the Cursor hook
run as real subprocesses in a sandbox `HOME`, from a copy of the skill without its tests whose `prices.json` is malformed.

Claude: the first `status` lists an unreadable sub-agent file, the session it holds back as skipped ("source partly gone") and a price-file
problem; after `.sync.json` is corrupted the next `status` also says the sync state was rebuilt. Cursor: `status` right after `on` says no
hook recorded anything; after hook calls (one valid, two without a usable conversation id) that line is gone and `.records/.invalid/` is
listed once with its call count and the note that it can be deleted. The real-Cursor check of the imported Claude hook (S59) is manual and not part of this file.

On macOS, from the repository root:

    python3 -m unittest discover -s skills/session-log/tests -t skills/session-log -p "test_e2e_status.py" -v

On Linux in Docker, from the repository root (the Linux floor image; the repository is mounted read-only, nothing is written to it):

    docker run --rm -v "$PWD":/repo:ro -w /repo/skills/session-log -e PYTHONDONTWRITEBYTECODE=1 python:3.14-slim \\
        python3 -m unittest discover -s tests -t . -p "test_e2e_status.py" -v
"""
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from tests import test_e2e_skeleton as skeleton
from tests.test_e2e_claude_hook import NOT_INSTALLED_FOLDERS

SKILL_COPY_FOLDER = "skill-copy"
PRICE_FILE = "prices.json"
MALFORMED_PRICES = "{not json"
PRICE_PROBLEM_LABEL = "Price file problem: "
RESET_LINE = "The sync state was empty or corrupt and has been rebuilt."
PARTLY_GONE_REASON = "source partly gone (1 of 2 units)"
AGENT_FILE = "agent-a1.jsonl"
CURSOR_HARNESS = "cursor"
CURSOR_FOLDER = ".cursor"
RECORDS_FOLDER = ".records"
INVALID_FOLDER = ".invalid"
CURSOR_SILENT_LINE = "Cursor has recorded no hook events since logging was turned on."
INVALID_REASON = "hook calls filed here for lack of a usable conversation id: %d; delete this folder to clear them"
PROMPT_EVENT = "beforeSubmitPrompt"
CONVERSATION = "conv-1"
NOT_JSON = b"not json at all"
NO_CONVERSATION_ID = b'{"prompt": "no conversation id"}'
UNIT_COUNT_WITH_AGENT = 2


def agent_transcript(prompt_at):
    records = [
        {"type": "user", "isSidechain": True, "agentId": "a1", "cwd": "/work/proj", "timestamp": prompt_at,
         "message": {"role": "user", "content": "do the task"}},
        {"type": "assistant", "timestamp": prompt_at,
         "message": {"id": "msg_a1", "model": skeleton.MODEL, "content": [{"type": "text", "text": "done"}],
                     "usage": {"input_tokens": 1, "output_tokens": 1}}},
    ]
    return "".join(json.dumps(record) + "\n" for record in records)


class FullStatusTests(skeleton.SkeletonCase):
    def setUp(self):
        super().setUp()
        self.skill = os.path.join(self.home, SKILL_COPY_FOLDER)
        shutil.copytree(skeleton.SKILL_DIR, self.skill, ignore=shutil.ignore_patterns(*NOT_INSTALLED_FOLDERS))
        self.price_file = os.path.join(self.skill, PRICE_FILE)
        Path(self.price_file).write_text(MALFORMED_PRICES, encoding="utf-8")
        self.cursor_log_root = os.path.join(self.home, CURSOR_FOLDER, skeleton.LOG_FOLDER)

    def environment(self):
        env = {name: os.environ[name] for name in skeleton.SUBPROCESS_ENV_NAMES if name in os.environ}
        env["PATH"] = self.stub_dir + os.pathsep + env.get("PATH", "")
        env.update(skeleton.HOSTILE_CONSOLE_ENV, HOME=self.home)
        return env

    def run_harness(self, harness, *arguments, stdin=b""):
        result = subprocess.run([sys.executable, os.path.join(self.skill, "session_log.py"), harness] + list(arguments),
                                env=self.environment(), cwd=self.project, input=stdin, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=skeleton.SUBPROCESS_TIMEOUT)
        self.assertEqual((0, b""), (result.returncode, result.stderr), msg=result.stdout)
        return result.stdout.decode("utf-8")

    def run_cursor_hook(self, event, stdin):
        result = subprocess.run([sys.executable, os.path.join(self.skill, "session_log.py"), "hook", CURSOR_HARNESS, event],
                                env=self.environment(), cwd=self.project, input=stdin, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=skeleton.SUBPROCESS_TIMEOUT)
        self.assertEqual((0, b"", b""), (result.returncode, result.stdout, result.stderr))

    def write_agent_file(self, session_id, content):
        path = os.path.join(os.path.dirname(self.project_file(session_id)), session_id, "subagents", AGENT_FILE)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        Path(path).write_bytes(content)
        os.utime(path, (skeleton.FUTURE_FILE_TIME, skeleton.FUTURE_FILE_TIME))
        return path

    def test_status_lists_unreadable_skipped_price_problem_state_reset_and_cursor_hook_silence(self):
        self.check_claude_status()
        self.check_cursor_status()
        self.assert_python_never_called()

    def check_claude_status(self):
        self.run_harness("claude", "on")
        self.write_session(skeleton.ACTIVE_SESSION)
        agent = self.write_agent_file(skeleton.ACTIVE_SESSION, agent_transcript(skeleton.ACTIVE_PROMPT_AT).encode("utf-8"))
        self.run_harness("claude", "status")  # archives the session from its two units
        session_key = "claude:" + skeleton.ACTIVE_SESSION
        log_path = self.stored_sync_state()["sessions"][session_key]["logPath"]
        self.assertEqual(UNIT_COUNT_WITH_AGENT, self.stored_sync_state()["sessions"][session_key]["unitCount"])
        Path(agent).write_bytes(b"\xff\xfe not utf-8 \xff\n")  # the unit stays but cannot be read: the rewrite is withheld
        os.utime(agent, (skeleton.FUTURE_FILE_TIME + 1, skeleton.FUTURE_FILE_TIME + 1))

        lines = self.run_harness("claude", "status").splitlines()

        self.assertEqual("Session logging is on since %s." % skeleton.shown(self.stored_on_time()), lines[0])
        self.assertEqual("Log root: %s" % self.log_root, lines[1])
        self.assertEqual("Last sync: %s" % skeleton.shown(skeleton.parse_time(self.stored_sync_state()["lastSyncAt"])), lines[2])
        self.assertEqual("Archived sessions: 1", lines[3])
        self.assertEqual([skeleton.UNREADABLE_HEADING,
                          "  %s: %s" % (agent, skeleton.UNDECODABLE_REASON),
                          "  %s: %s" % (log_path, PARTLY_GONE_REASON)], lines[4:7])
        self.assertTrue(lines[7].startswith(PRICE_PROBLEM_LABEL + self.price_file + ": "), msg=lines[7])
        self.assertEqual(8, len(lines), msg=lines)
        self.assertNotIn(RESET_LINE, lines)

        Path(self.sync_state).write_bytes(b"{corrupt")
        rebuilt = self.run_harness("claude", "status").splitlines()
        self.assertIn(RESET_LINE, rebuilt)
        self.assertTrue(any(line.startswith(PRICE_PROBLEM_LABEL) for line in rebuilt), msg=rebuilt)

    def check_cursor_status(self):
        self.run_harness(CURSOR_HARNESS, "on")
        quiet = self.run_harness(CURSOR_HARNESS, "status").splitlines()
        self.assertIn(CURSOR_SILENT_LINE, quiet)
        self.assertNotIn(skeleton.UNREADABLE_HEADING, quiet)

        prompt = json.dumps({"conversation_id": CONVERSATION, "generation_id": "g1", "prompt": "hello",
                             "workspace_roots": [self.project]}).encode("utf-8")
        self.run_cursor_hook(PROMPT_EVENT, prompt)
        self.run_cursor_hook(PROMPT_EVENT, NOT_JSON)
        self.run_cursor_hook(PROMPT_EVENT, NO_CONVERSATION_ID)

        lines = self.run_harness(CURSOR_HARNESS, "status").splitlines()
        invalid = os.path.join(self.cursor_log_root, RECORDS_FOLDER, INVALID_FOLDER)
        self.assertNotIn(CURSOR_SILENT_LINE, lines)
        self.assertEqual([skeleton.UNREADABLE_HEADING, "  %s: %s" % (invalid, INVALID_REASON % 2)],
                         [line for line in lines if line == skeleton.UNREADABLE_HEADING or line.startswith("  ")])
        self.assertEqual("Archived sessions: 1", lines[3])  # the one valid call's conversation; the invalid calls make no session

        # off: nothing is synced and the hook line is not due; on again: records older than the new on-time do not count
        self.run_harness(CURSOR_HARNESS, "off")
        self.assertNotIn(CURSOR_SILENT_LINE, self.run_harness(CURSOR_HARNESS, "status").splitlines())
        self.run_harness(CURSOR_HARNESS, "on")
        self.assertIn(CURSOR_SILENT_LINE, self.run_harness(CURSOR_HARNESS, "status").splitlines())


if __name__ == "__main__":
    unittest.main()
