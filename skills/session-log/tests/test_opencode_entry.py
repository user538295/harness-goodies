"""`session_log.py opencode ...` (S11, S14 end to end through sync): the entry script runs the OpenCode reader over the database in
`~/.local/share/opencode`, with the injected clock before the fixture's times. The database is built here from text; the real one is never
opened. `OpenCodeEntryTestCase` is also the base of the database-choice tests (S39, `test_opencode_database_choice`)."""
import io
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import session_log
from sessionlog.reader_port import PARENT_CYCLE_REASON
from tests.opencode_db import BASE, CHILD_ID, CWD, ROOT_ID, OpenCodeDatabase, assistant_data, folder_snapshot, read_text

FIXED_NOW = datetime(2000, 1, 1, tzinfo=timezone.utc)
FIRST_PROMPT_TOKENS = "input: 10, output: 120, cache_create: 50, cache_read: 200, total_tokens: 380"
SECOND_PROMPT_TOKENS = "input: 5, output: 30, cache_create: 0, cache_read: 100, total_tokens: 135"
CHILD_TOKENS = "input: 7, output: 9, cache_create: 3, cache_read: 9, total_tokens: 28"
TOTAL_TOKENS = "input: 22, output: 159, cache_create: 53, cache_read: 309, total_tokens: 543"


class OpenCodeEntryTestCase(unittest.TestCase):
    starts_with_database = True  # a test of the database choice sets it False and makes its own databases

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        self.data = os.path.join(self.home, ".local", "share", "opencode")
        self.log_root = os.path.join(self.home, ".config", "opencode", "prompt-logs")
        self.database_path = os.path.join(self.data, "opencode.db")
        if self.starts_with_database:
            self.database = OpenCodeDatabase(self.database_path)

    def run_main(self, argv, now=FIXED_NOW, env=None):
        """One run of the entry; `env` is the whole environment of the run (empty by default: the developer's `OPENCODE_DB`, `OPENCODE_CHANNEL`
        or `XDG_DATA_HOME` must never choose the database of a test)."""
        stdout, stderr = io.StringIO(), io.StringIO()
        environment = session_log.Environment(stdout=stdout, stderr=stderr, home=self.home, get_cwd=lambda: CWD, now=lambda: now,
                                              to_local=lambda moment: moment.astimezone(timezone.utc), env={} if env is None else env)
        return session_log.main(argv, environment), stdout.getvalue(), stderr.getvalue()

    def archived_files(self, suffix):
        return sorted(os.path.join(folder, name) for folder, _, names in os.walk(self.log_root) for name in names if name.endswith(suffix))


class OpenCodeEntryTest(OpenCodeEntryTestCase):
    def setUp(self):
        super().setUp()
        self.database.fixture()

    def test_on_archives_the_session_as_a_log_with_prompts_and_replies_and_a_report_with_recorded_cost_tokens_and_model(self):
        code, _, stderr = self.run_main(["opencode", "on"])
        self.assertEqual((0, ""), (code, stderr))
        (log_path,) = self.archived_files(".md")
        (report_path,) = self.archived_files(".usage.txt")
        log, report = read_text(log_path), read_text(report_path)
        self.assertIn(ROOT_ID, os.path.basename(log_path))
        self.assertTrue(log.startswith("# Session log\n- harness: opencode\n- session: %s\n- project: %s\n" % (ROOT_ID, CWD)), log)
        for text in ("## 2026-10-13 08:42:10 UTC\nfirst prompt\n", "### 2026-10-13 08:42:15 UTC response\nfirst reply\n",
                     "## 2026-10-13 08:42:30 UTC\nsecond prompt\n", "### 2026-10-13 08:42:40 UTC response\nsecond reply\n"):
            self.assertIn(text, log)
        self.assertNotIn("task text for the sub-agent", log)  # the task handed to a sub-agent is no prompt
        for tokens in (FIRST_PROMPT_TOKENS, SECOND_PROMPT_TOKENS, CHILD_TOKENS, TOTAL_TOKENS):
            self.assertIn(tokens, report)
        self.assertIn("%s, price: $0.50, model: anthropic/claude-opus-5-5" % FIRST_PROMPT_TOKENS, report)
        self.assertIn("%s, price: $0.00, model: openai/gpt-5" % SECOND_PROMPT_TOKENS, report)  # a recorded zero is free
        self.assertIn("sub-agent: general (%s), working time: 00:00:06" % CHILD_ID, report)
        self.assertIn("TOTAL (2 requests, 1 sub-agents)", report)

    def test_status_then_usage_report_the_archived_session(self):
        self.run_main(["opencode", "on"])
        code, stdout, stderr = self.run_main(["opencode", "status"])
        self.assertEqual((0, ""), (code, stderr))
        self.assertIn("Archived sessions: 1", stdout)
        code, stdout, stderr = self.run_main(["opencode", "usage"])
        self.assertEqual((0, ""), (code, stderr))
        self.assertIn(TOTAL_TOKENS, stdout)
        self.assertEqual(read_text(self.archived_files(".usage.txt")[0]), stdout)

    def test_usage_of_a_session_never_archived_is_read_from_the_database_and_nothing_is_written(self):
        before = folder_snapshot(self.data)
        code, stdout, stderr = self.run_main(["opencode", "usage", ROOT_ID[:8]])
        self.assertEqual((0, ""), (code, stderr))
        self.assertIn(TOTAL_TOKENS, stdout)
        self.assertFalse(os.path.exists(self.log_root))
        self.assertEqual(before, folder_snapshot(self.data))

    def test_database_opened_read_only_by_a_whole_run_nothing_is_written_to_the_store(self):
        before = folder_snapshot(self.data)
        for argv in (["opencode", "on"], ["opencode", "status"], ["opencode", "usage"], ["opencode", "off"]):
            self.assertEqual(0, self.run_main(argv)[0], argv)
        self.assertEqual(before, folder_snapshot(self.data))

    def test_an_opencode_command_writes_under_the_opencode_config_folder_only(self):
        self.run_main(["opencode", "on"])
        self.assertEqual([".config", ".local"], sorted(os.listdir(self.home)))
        self.assertEqual(["opencode"], os.listdir(os.path.join(self.home, ".config")))
        self.assertEqual(["prompt-logs"], os.listdir(os.path.join(self.home, ".config", "opencode")))


class OpenCodeUpdatedInPlaceTest(OpenCodeEntryTestCase):
    def test_assistant_row_updated_in_place_is_read_again(self):
        """S14: the assistant row is written while it streams and updated in place when it finishes; the new `time_updated` is the signal."""
        self.database.add_session(ROOT_ID, created=10, updated=12, agent="build")
        self.database.add_user(ROOT_ID, 1, 10, "streamed prompt")
        self.database.add_message(ROOT_ID, 2, "assistant", assistant_data(11, "partial", finish=None, tokens=(1, 1), cost=0.01, done=None), updated=12)
        self.assertEqual(0, self.run_main(["opencode", "on"])[0])
        log, report = (read_text(self.archived_files(suffix)[0]) for suffix in (".md", ".usage.txt"))
        self.assertIn("streamed prompt", log)
        self.assertNotIn(" response\n", log)  # no reply yet
        self.assertIn("input: 1, output: 1, cache_create: 0, cache_read: 0, total_tokens: 2", report)

        self.database.replace_data(ROOT_ID, 2, assistant_data(11, "final answer", tokens=(40, 50, 0, 60, 0), cost=0.75, done=9), updated=25)
        self.assertEqual(0, self.run_main(["opencode", "status"])[0])
        (log_path,) = self.archived_files(".md")
        log, report = read_text(log_path), read_text(self.archived_files(".usage.txt")[0])
        self.assertIn("### 2026-10-13 08:42:20 UTC response\nfinal answer\n", log)
        self.assertIn("input: 40, output: 50, cache_create: 0, cache_read: 60, total_tokens: 150", report)
        self.assertIn("price: $0.75", report)

    def test_a_new_row_in_an_archived_session_is_read_again_and_an_unchanged_database_is_not_rewritten(self):
        self.database.fixture()
        self.run_main(["opencode", "on"])
        (log_path,) = self.archived_files(".md")
        before = (read_text(log_path), os.stat(log_path).st_mtime_ns)
        self.run_main(["opencode", "status"])
        self.assertEqual(before, (read_text(log_path), os.stat(log_path).st_mtime_ns))  # not even rewritten with the same text
        self.database.add_user(ROOT_ID, 5, 80, "third prompt")
        self.database.add_assistant(ROOT_ID, 6, 81, text="third reply", tokens=(1, 1), cost=0, done=2)
        self.run_main(["opencode", "status"])
        self.assertIn("third reply", read_text(log_path))


class OpenCodeOnTimeEntryTest(OpenCodeEntryTestCase):
    def test_a_row_updated_in_place_after_the_on_time_is_found_by_the_time_updated_of_its_message_not_of_its_session(self):
        """S14 through sync's pre-filter: the session row keeps its old `time_updated`, only the message row's moves past the on-time."""
        on_time = BASE + timedelta(seconds=100)
        self.database.add_session(ROOT_ID, created=10, updated=20, agent="build")
        self.database.add_user(ROOT_ID, 1, 10, "slow prompt")
        self.database.add_message(ROOT_ID, 2, "assistant", assistant_data(11, finish=None, tokens=(1, 1), cost=0.01, done=None), updated=12)
        self.assertEqual(0, self.run_main(["opencode", "on"], now=on_time)[0])
        self.assertEqual([], self.archived_files(".md"))  # everything happened before the on-time

        self.database.replace_data(ROOT_ID, 2, assistant_data(11, "late answer", tokens=(3, 4), cost=0.5, done=139), updated=150)
        self.assertEqual(0, self.run_main(["opencode", "status"], now=BASE + timedelta(seconds=200))[0])
        (log_path,) = self.archived_files(".md")
        self.assertIn("### 2026-10-13 08:44:30 UTC response\nlate answer\n", read_text(log_path))


class OpenCodeProblemEntryTest(OpenCodeEntryTestCase):
    def test_usage_of_an_id_no_session_has_exits_1_with_one_session_log_line(self):
        self.database.fixture()
        code, stdout, stderr = self.run_main(["opencode", "usage", "ses_nothing"])
        self.assertEqual((1, ""), (code, stdout))
        self.assertEqual("session-log: no session has the id 'ses_nothing'\n", stderr)

    def test_a_session_whose_parent_row_is_gone_is_archived_alone_and_labelled(self):
        self.database.add_session("ses_stray", parent="ses_deleted", created=10, updated=20)
        self.database.add_user("ses_stray", 1, 12, "stray ask")
        self.assertEqual(0, self.run_main(["opencode", "on"])[0])
        (log_path,) = self.archived_files(".md")
        self.assertIn("- sub-agent of ses_deleted (parent not found)\n", read_text(log_path))
        self.assertIn("stray ask", read_text(log_path))

    def test_a_file_that_is_no_database_ends_every_command_without_error_or_traceback_and_archives_nothing(self):
        with open(self.database.path, "wb") as handle:
            handle.write(b"this is no database " * 100)
        for argv in (["opencode", "on"], ["opencode", "status"], ["opencode", "off"]):
            code, _, stderr = self.run_main(argv)
            self.assertEqual((0, ""), (code, stderr), argv)
        self.assertEqual([], self.archived_files(".md"))


class OpenCodeCycleEntryTest(OpenCodeEntryTestCase):
    def test_two_sessions_whose_parent_ids_point_at_each_other_are_listed_unreadable_and_not_archived_and_sync_ends(self):
        """S11: the cyclic pair and the session hanging from it are listed in `status`; the rest of the database is archived."""
        self.database.fixture()
        self.database.add_session("ses_cyc_a", parent="ses_cyc_b", created=70, updated=71)
        self.database.add_session("ses_cyc_b", parent="ses_cyc_a", created=72, updated=73)
        self.database.add_session("ses_cyc_c", parent="ses_cyc_b", created=74, updated=75)
        for name in ("ses_cyc_a", "ses_cyc_b", "ses_cyc_c"):
            self.database.add_user(name, 1, 80, "cyclic ask")
        self.assertEqual(0, self.run_main(["opencode", "on"])[0])
        code, stdout, stderr = self.run_main(["opencode", "status"])
        self.assertEqual((0, ""), (code, stderr))
        for name in ("ses_cyc_a", "ses_cyc_b", "ses_cyc_c"):
            self.assertIn("  %s: %s\n" % (name, PARENT_CYCLE_REASON), stdout)
        (log_path,) = self.archived_files(".md")
        self.assertIn("first prompt", read_text(log_path))
        self.assertNotIn("cyclic ask", read_text(log_path))
        self.assertEqual(0, self.run_main(["opencode", "usage"])[0])


if __name__ == "__main__":
    unittest.main()
