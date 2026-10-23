"""`session_log.py codex ...` (S7 end to end through sync): the entry script runs the Codex reader over `~/.codex`, with the injected
clock before the fixture's times."""
import io
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone

import session_log

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "codex", "root_with_token_usage_records.jsonl")
ROOT_ID = "1a000001-0000-7000-8000-000000000001"
OTHER_ID = "1a000009-0000-7000-8000-000000000009"
ROOT_NAME = "rollout-2026-09-11T10-10-15-%s.jsonl" % ROOT_ID
DAY_FOLDER = os.path.join("2026", "09", "11")
CWD = "/work/proj"
FIXED_NOW = datetime(2000, 1, 1, tzinfo=timezone.utc)
INJECTED_CONTEXT = "# AGENTS.md instructions"
PROMPT_ONE_TOKENS = "input: 63277, output: 5285, cache_create: 0, cache_read: 559744, total_tokens: 628306"
PROMPT_TWO_TOKENS = "input: 65446, output: 4529, cache_create: 0, cache_read: 0, total_tokens: 69975"
TOTAL_TOKENS = "input: 128723, output: 9814, cache_create: 0, cache_read: 559744, total_tokens: 698281"


class CodexEntryTestCase(unittest.TestCase):
    """A Codex home in a temporary folder with an empty `sessions/<day>` folder, and the entry script run over it with the clock `NOW`."""

    NOW = FIXED_NOW

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        self.codex = os.path.join(self.home, ".codex")
        self.log_root = os.path.join(self.codex, "prompt-logs")
        self.day = os.path.join(self.codex, "sessions", DAY_FOLDER)
        os.makedirs(self.day)

    def run_main(self, argv, home=None, now=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        environment = session_log.Environment(stdout=stdout, stderr=stderr, home=home or self.home, get_cwd=lambda: CWD,
                                              now=lambda: now or self.NOW, to_local=lambda moment: moment.astimezone(timezone.utc), env={})
        return session_log.main(argv, environment), stdout.getvalue(), stderr.getvalue()

    def archived_files(self, suffix, log_root=None):
        root = log_root or self.log_root
        return sorted(os.path.join(folder, name) for folder, _, names in os.walk(root) for name in names if name.endswith(suffix))

    @staticmethod
    def read(path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()


class CodexEntryTest(CodexEntryTestCase):
    def setUp(self):
        super().setUp()
        shutil.copy(FIXTURE, os.path.join(self.day, ROOT_NAME))

    def test_on_archives_the_rollout_as_a_log_without_injected_context_and_a_report_with_the_fixture_tokens(self):
        code, _, stderr = self.run_main(["codex", "on"])
        self.assertEqual((0, ""), (code, stderr))
        (log_path,) = self.archived_files(".md")
        (report_path,) = self.archived_files(".usage.txt")
        log, report = self.read(log_path), self.read(report_path)
        self.assertIn(ROOT_ID, os.path.basename(log_path))
        for text in ("## 2026-09-11 08:12:16 UTC\nprompt 1\n", "### 2026-09-11 08:20:31 UTC response\nreply 2\n", "## 2026-09-11 09:41:21 UTC\nprompt 2\n"):
            self.assertIn(text, log)
        self.assertNotIn(INJECTED_CONTEXT, log)
        self.assertNotIn(INJECTED_CONTEXT, report)
        for tokens in (PROMPT_ONE_TOKENS, PROMPT_TWO_TOKENS, TOTAL_TOKENS):
            self.assertIn(tokens, report)
        self.assertIn("model: gpt-5.6-sol, effort: high", report)

    def test_status_then_usage_report_the_archived_rollout(self):
        self.run_main(["codex", "on"])
        code, stdout, stderr = self.run_main(["codex", "status"])
        self.assertEqual((0, ""), (code, stderr))
        self.assertIn("Archived sessions: 1", stdout)
        code, stdout, stderr = self.run_main(["codex", "usage"])
        self.assertEqual((0, ""), (code, stderr))
        self.assertIn(TOTAL_TOKENS, stdout)
        self.assertEqual(self.read(self.archived_files(".usage.txt")[0]), stdout)

    def test_usage_of_a_session_never_archived_is_read_from_the_store_and_nothing_is_written(self):
        code, stdout, stderr = self.run_main(["codex", "usage", ROOT_ID[:8]])
        self.assertEqual((0, ""), (code, stderr))
        self.assertIn(TOTAL_TOKENS, stdout)
        self.assertFalse(os.path.exists(self.log_root))

    def test_cache_write_tokens_of_a_rollout_reach_the_report_as_cache_create(self):
        meta = {"timestamp": "2026-09-12T08:00:00.000Z", "type": "session_meta",
                "payload": {"id": OTHER_ID, "session_id": OTHER_ID, "timestamp": "2026-09-12T08:00:00.000Z", "cwd": CWD}}
        context = {"timestamp": "2026-09-12T08:00:00.500Z", "type": "turn_context", "payload": {"model": "gpt-5.6-sol"}}
        prompt = {"timestamp": "2026-09-12T08:00:01.000Z", "type": "event_msg",
                  "payload": {"type": "item_completed", "item": {"type": "UserMessage", "content": [{"type": "text", "text": "cached"}]}}}
        usage = {"input_tokens": 1000, "cached_input_tokens": 300, "cache_write_input_tokens": 50, "output_tokens": 70}
        record = {"timestamp": "2026-09-12T08:00:02.000Z", "type": "token_usage_record", "payload": {"usage": usage}}
        with open(os.path.join(self.day, "rollout-2026-09-12T10-00-00-%s.jsonl" % OTHER_ID), "w", encoding="utf-8") as handle:
            handle.write("".join(json.dumps(item) + "\n" for item in (meta, context, prompt, record)))
        self.run_main(["codex", "on"])
        reports = [self.read(path) for path in self.archived_files(".usage.txt") if OTHER_ID in path]
        self.assertEqual(1, len(reports))
        self.assertIn("input: 700, output: 70, cache_create: 50, cache_read: 300, total_tokens: 1120", reports[0])

    def test_a_codex_command_reads_only_the_codex_root_and_writes_under_it(self):
        project = os.path.join(self.home, ".claude", "projects", "-work-proj")
        os.makedirs(project)
        with open(os.path.join(project, "abc.jsonl"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "user", "promptSource": "typed", "cwd": CWD, "timestamp": "2026-09-12T08:00:00.000Z",
                                     "message": {"role": "user", "content": "claude prompt"}}) + "\n")
        self.run_main(["codex", "on"])
        self.assertEqual([".claude", ".codex"], sorted(os.listdir(self.home)))
        self.assertEqual(["projects"], os.listdir(os.path.join(self.home, ".claude")))
        self.assertNotIn("claude prompt", "".join(self.read(path) for path in self.archived_files(".md")))

    def test_codex_has_no_hook_so_a_codex_hook_call_syncs_nothing(self):
        self.run_main(["codex", "on"])
        record = {"timestamp": "2026-09-12T08:00:00.000Z", "type": "session_meta",
                  "payload": {"id": OTHER_ID, "session_id": OTHER_ID, "timestamp": "2026-09-12T08:00:00.000Z", "cwd": CWD}}
        prompt = {"timestamp": "2026-09-12T08:00:01.000Z", "type": "event_msg",
                  "payload": {"type": "item_completed", "item": {"type": "UserMessage", "content": [{"type": "text", "text": "later"}]}}}
        with open(os.path.join(self.day, "rollout-2026-09-12T10-00-00-%s.jsonl" % OTHER_ID), "w", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n" + json.dumps(prompt) + "\n")
        before = (self.read(os.path.join(self.log_root, ".sync.json")), self.archived_files(".md"))
        stdout, stderr = io.StringIO(), io.StringIO()
        environment = session_log.Environment(stdout=stdout, stderr=stderr, home=self.home, now=lambda: FIXED_NOW, env={})
        self.assertEqual(0, session_log.main(["hook", "codex", "SessionStart"], environment))
        self.assertEqual(("", "", before), (stdout.getvalue(), stderr.getvalue(),
                                            (self.read(os.path.join(self.log_root, ".sync.json")), self.archived_files(".md"))))
        self.run_main(["codex", "status"])
        self.assertEqual(2, len(self.archived_files(".md")))


if __name__ == "__main__":
    unittest.main()
