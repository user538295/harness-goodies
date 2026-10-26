"""`session_log.py cursor ...` and the Cursor `sessionStart` hook end to end through sync (S12, S14, S44, S34): the entry script builds
the CursorReader over `~/.cursor/prompt-logs/.records`, with the injected clock before the records' times. Records are written the way
the hook writes them; nothing real is used."""
import io
import json
import os
import unittest

import session_log
from tests.cursor_record_files import prompt_input, response_input, stop_input, subagent_input, write_record
from tests.test_session_log import SandboxCase

CURSOR_FOLDER = ".cursor"
LOG_FOLDER = "prompt-logs"
RECORDS_FOLDER = ".records"
INVALID_FOLDER = ".invalid"
MODEL = "claude-opus-5-5"
TOKENS = {"input_tokens": 1000, "output_tokens": 200, "cache_read_tokens": 300, "cache_write_tokens": 100, "model": MODEL}
TOKEN_LINE = "input: 600, output: 200, cache_create: 100, cache_read: 300, total_tokens: 1200"
SUPPORTED_HARNESSES = "(supported: claude, codex, cursor, omp, opencode)"


class CursorEntryTestCase(SandboxCase):
    def setUp(self):
        super().setUp()
        self.cursor = os.path.join(self.home, CURSOR_FOLDER)
        self.log_root = os.path.join(self.cursor, LOG_FOLDER)
        self.records = os.path.join(self.log_root, RECORDS_FOLDER)

    def record(self, event, seconds, hook_input, **kwargs):
        return write_record(self.records, event, seconds, hook_input, **kwargs)

    def install_conversation(self):
        self.record("beforeSubmitPrompt", 0, prompt_input("g1", "first prompt"))
        self.record("afterAgentResponse", 1, response_input("g1", "first reply"))
        self.record("stop", 2, stop_input("g1", **TOKENS))
        self.record("subagentStop", 1, subagent_input("child-a", subagent_type="explore", duration_ms=1000))

    def archived_files(self, suffix):
        return sorted(os.path.join(folder, name) for folder, _, names in os.walk(self.log_root) for name in names if name.endswith(suffix))

    def read_text(self, path):
        with open(path, encoding="utf-8") as stream:
            return stream.read()


class CursorEntryTest(CursorEntryTestCase):
    def test_on_archives_the_conversation_as_a_log_and_a_report_from_its_records(self):
        self.install_conversation()
        code, _, stderr = self.run_main(["cursor", "on"])
        self.assertEqual((0, ""), (code, stderr))
        (log_path,) = self.archived_files(".md")
        (report_path,) = self.archived_files(".usage.txt")
        log, report = self.read_text(log_path), self.read_text(report_path)
        self.assertIn("conv-1", os.path.basename(log_path))
        self.assertTrue(log.startswith("# Session log\n- harness: cursor\n- session: conv-1\n- project: /work/proj\n"), log)
        self.assertIn("first prompt", log)
        self.assertIn("first reply", log)
        self.assertIn(TOKEN_LINE, report)
        self.assertIn("sub-agent: explore (child-a)", report)

    def test_usage_prints_the_saved_report_and_status_counts_the_archived_conversation(self):
        self.install_conversation()
        self.record("stop", 3, {}, conversation=INVALID_FOLDER, raw_input="not json")  # a call the hook could not file: no conversation
        self.run_main(["cursor", "on"])
        code, stdout, stderr = self.run_main(["cursor", "usage"])
        self.assertEqual((0, ""), (code, stderr))
        self.assertEqual(self.read_text(self.archived_files(".usage.txt")[0]), stdout)
        code, stdout, stderr = self.run_main(["cursor", "status"])
        self.assertEqual((0, ""), (code, stderr))
        self.assertIn("Archived sessions: 1", stdout)

    def test_a_stray_invalid_folder_does_not_turn_a_missing_session_into_a_store_error(self):
        self.record("stop", 3, {}, conversation=INVALID_FOLDER, raw_input="")  # a call the hook could not file: no conversation
        self.run_main(["cursor", "on"])
        for target in ([], ["--latest"]):
            self.assertEqual((1, "", "session-log: no session for this project (/work/elsewhere)\n"),
                             self.run_main(["cursor", "usage"] + target, get_cwd=lambda: "/work/elsewhere"))
        self.assertEqual((1, "", "session-log: no session has the id 'nope'\n"), self.run_main(["cursor", "usage", "nope"]))
        invalid = os.path.join(self.records, INVALID_FOLDER)
        _, status, _ = self.run_main(["cursor", "status"])
        self.assertIn("  %s: hook calls filed here for lack of a usable conversation id: 1; delete this folder to clear them\n" % invalid, status)

    def test_a_new_record_changes_the_log_on_the_next_sync(self):
        self.install_conversation()
        self.run_main(["cursor", "on"])
        self.record("beforeSubmitPrompt", 10, prompt_input("g2", "second prompt"))
        self.assertNotIn("second prompt", self.read_text(self.archived_files(".md")[0]))
        code, _, stderr = self.run_main(["cursor", "status"])  # status syncs while logging is on
        self.assertEqual((0, ""), (code, stderr))
        self.assertIn("second prompt", self.read_text(self.archived_files(".md")[0]))

    def test_a_sub_agent_conversation_is_neither_archived_nor_picked_by_latest_over_its_parent(self):
        self.install_conversation()
        self.record("subagentStop", 5, subagent_input("child-1", subagent_type="explore", duration_ms=1000))
        self.record("beforeSubmitPrompt", 6, prompt_input("g-child", "child task", conversation_id="child-1"), conversation="child-1")
        self.record("stop", 7, stop_input("g-child", conversation_id="child-1", **TOKENS), conversation="child-1")
        self.run_main(["cursor", "on"])
        (log_path,) = self.archived_files(".md")
        (report_path,) = self.archived_files(".usage.txt")
        self.assertIn("conv-1", os.path.basename(log_path))
        code, stdout, stderr = self.run_main(["cursor", "usage", "--latest"])
        self.assertEqual((0, ""), (code, stderr))
        self.assertEqual(self.read_text(report_path), stdout)

    def test_usage_from_a_project_subfolder_reports_the_nearest_parent_conversation_not_the_newest_one(self):
        self.install_conversation()
        for conversation, root, seconds in (("conv-2", "/work/other", 20), ("conv-3", "/work", 30)):  # newer, one outside and one above /work/proj
            self.record("beforeSubmitPrompt", seconds, prompt_input("g9", "prompt", conversation_id=conversation, workspace_roots=[root]),
                        conversation=conversation)
            self.record("afterAgentResponse", seconds + 1, response_input("g9", "reply", conversation_id=conversation), conversation=conversation)
            self.record("stop", seconds + 2, stop_input("g9", conversation_id=conversation, **TOKENS), conversation=conversation)
        self.run_main(["cursor", "on"])
        self.assertEqual(3, len(self.archived_files(".usage.txt")))
        (proj_report,) = [path for path in self.archived_files(".usage.txt") if "conv-1" in path]
        code, stdout, stderr = self.run_main(["cursor", "usage"], get_cwd=lambda: "/work/proj/src/deep")
        self.assertEqual((0, ""), (code, stderr))
        self.assertEqual(self.read_text(proj_report), stdout)
        self.assertEqual((1, "", "session-log: no session for this project (/elsewhere/nothing)\n"),
                         self.run_main(["cursor", "usage"], get_cwd=lambda: "/elsewhere/nothing"))

    def test_usage_of_a_conversation_never_archived_is_computed_from_its_records(self):
        self.install_conversation()
        code, stdout, stderr = self.run_main(["cursor", "usage", "conv-1"])
        self.assertEqual((0, ""), (code, stderr))
        self.assertIn(TOKEN_LINE, stdout)
        self.assertEqual([], self.archived_files(".usage.txt"))

    def test_the_harness_list_of_an_unsupported_harness_names_cursor(self):
        code, _, stderr = self.run_main(["bogus", "on"])
        self.assertEqual(2, code)
        self.assertIn(SUPPORTED_HARNESSES, stderr)


class CursorSessionStartTest(CursorEntryTestCase):
    def run_session_start(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        environment = self.environment(stdout, stderr, stdin=io.BytesIO(b""))
        self.assertEqual(0, session_log.main(["hook", "cursor", "sessionStart"], environment))
        return stdout.getvalue(), stderr.getvalue()

    def test_session_start_syncs_the_records_into_a_log_and_prints_nothing(self):
        self.install_conversation()
        with open(os.path.join(self.log_root, ".log_enabled"), "w", encoding="utf-8") as stream:
            stream.write("2000-01-01T00:00:00+00:00\n")
        self.assertEqual(("", ""), self.run_session_start())
        (log_path,) = self.archived_files(".md")
        self.assertIn("first prompt", self.read_text(log_path))
        with open(os.path.join(self.log_root, ".sync.json"), encoding="utf-8") as stream:
            self.assertEqual(["cursor:conv-1"], list(json.load(stream)["sessions"]))

    def test_session_start_while_logging_is_off_syncs_nothing(self):
        self.install_conversation()
        self.assertEqual(("", ""), self.run_session_start())
        self.assertEqual([], self.archived_files(".md"))


if __name__ == "__main__":
    unittest.main()
