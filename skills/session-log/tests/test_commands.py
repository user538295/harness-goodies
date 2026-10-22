"""Command use cases (C1) over a real FileStateStore and ClaudeReader, a fake Archive that keeps its reports, and a spying sync."""
import ntpath
import os
import shutil
import tempfile
import unittest
from datetime import timedelta, timezone
from typing import Dict, Optional

from sessionlog.archive_port import WriteOutcome
from sessionlog.claude_reader import ClaudeReader
from sessionlog.commands import CommandError, CommandUseCases, OffResult, OnResult, UnreadableItem
from sessionlog.pricing import PricingRules
from sessionlog.state import FileStateStore
from sessionlog.state_port import OnFlag
from sessionlog.sync import SyncOutcome, SyncUseCase
from tests.test_sync import NOW, ON_AT, ONE_DAY, WRITE_FAILURE, FakeArchive, _NoPrices, _transcript

CWD = "/work/proj"
PRICE_PROBLEM = "prices.json is unreadable"


class ReportKeepingArchive(FakeArchive):
    """Remembers what `write` saved, so `read_report` returns the saved report."""

    def __init__(self):
        super().__init__()
        self.reports: Dict[str, str] = {}

    def write(self, log_path, log_text, report_text):
        outcome = super().write(log_path, log_text, report_text)
        if outcome.written:
            self.reports[log_path] = report_text
        return outcome

    def read_report(self, log_path):
        return self.reports.get(log_path)


class SpySync:
    def __init__(self, sync: SyncUseCase):
        self._sync = sync
        self.calls = 0

    def sync(self):
        self.calls += 1
        return self._sync.sync()


class _Prices(_NoPrices):
    problem_text: Optional[str] = None

    def problem(self):
        return self.problem_text


class CommandTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        self.folder = os.path.join(self.root, "projects", "-work-proj")
        os.makedirs(self.folder)
        self.log_root = os.path.join(self.root, "prompt-logs")
        os.makedirs(self.log_root)
        self.clock = NOW
        self.state = FileStateStore(self.log_root)
        self.archive = ReportKeepingArchive()
        self.prices = _Prices()
        self.sync = SpySync(SyncUseCase(ClaudeReader(self.root), self.archive, self.state, PricingRules(self.prices),
                                        lambda: self.clock))
        self.commands = CommandUseCases(self.sync, self.archive, self.state, self.prices, lambda: self.clock, self.log_root)

    def _install(self, sid, *prompt_times):
        path = os.path.join(self.folder, sid + ".jsonl")
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(_transcript(*prompt_times))
        os.utime(path, (NOW.timestamp(), NOW.timestamp()))
        return path

    def _enable(self, on_at=ON_AT):
        self.state.write_on_flag(OnFlag(on_at))

    def _unparsable_flag_and_corrupt_state(self):
        flag_path = os.path.join(self.log_root, ".log_enabled")
        with open(flag_path, "w", encoding="utf-8") as handle:
            handle.write("not a time")
        os.utime(flag_path, (ON_AT.timestamp(), ON_AT.timestamp()))
        with open(os.path.join(self.log_root, ".sync.json"), "w", encoding="utf-8") as handle:
            handle.write("{ corrupt")
        return flag_path


class MemoryFlagState:
    sync_reset = False

    def __init__(self, sessions=None):
        self.flag = None
        self._sessions = sessions or {}

    def read_on_flag(self):
        return self.flag

    def write_on_flag(self, flag):
        self.flag = flag

    def delete_on_flag(self):
        self.flag = None

    def load_sync(self):
        return {"units": {}, "sessions": self._sessions}

    def save_sync(self, state):
        pass


class OnOffTest(CommandTestCase):
    def test_on_off_semantics_on_keeps_time_off_syncs_once_off_while_off_noop(self):
        first = self.commands.on()
        self.assertEqual(first, OnResult(False, NOW, self.log_root))
        self.assertEqual(self.state.read_on_flag().on_at, NOW)
        self.clock = NOW + ONE_DAY
        again = self.commands.on()
        self.assertEqual(again, OnResult(True, NOW, self.log_root))
        self.assertEqual(self.state.read_on_flag().on_at, NOW)
        self.assertEqual(self.sync.calls, 2)  # `on` while on keeps the time and syncs

        self.assertEqual(self.commands.off(), OffResult(False, self.log_root))
        self.assertEqual(self.sync.calls, 3)  # `off` syncs once, then turns off
        self.assertIsNone(self.state.read_on_flag())

        self._install("late", ON_AT + ONE_DAY)
        self.assertEqual(self.commands.off(), OffResult(True, self.log_root))
        self.assertEqual(self.sync.calls, 3)  # `off` while off does nothing
        self.assertEqual(self.archive.writes, [])
        self.assertIsNone(self.state.read_on_flag())

    def test_on_syncs_after_it_wrote_the_flag(self):
        self._install("running", NOW + ONE_DAY)
        self.commands.on()
        self.assertEqual(sorted(self.archive.reports), ["/logs/running.md"])

    def test_on_keeps_the_time_of_a_concurrent_on(self):
        earlier = NOW - ONE_DAY

        class RacedState(MemoryFlagState):
            def write_on_flag(self, flag):
                self.flag = OnFlag(earlier)  # the other `on` created the file first

        class NoSync:
            def sync(self):
                return SyncOutcome()

        commands = CommandUseCases(NoSync(), None, RacedState(), None, lambda: NOW, self.log_root)
        self.assertEqual(commands.on(), OnResult(False, earlier, self.log_root))

    def test_off_syncs_before_it_turns_off_so_a_late_session_is_archived(self):
        self._enable()
        self._install("late", ON_AT + ONE_DAY)
        self.commands.off()
        self.assertEqual(sorted(self.archive.reports), ["/logs/late.md"])

    def test_off_turns_logging_off_even_when_the_sync_fails(self):
        self._enable()

        class Failing:
            def sync(self):
                raise OSError("read-only")

        commands = CommandUseCases(Failing(), self.archive, self.state, self.prices, lambda: NOW, self.log_root)
        with self.assertRaises(OSError):
            commands.off()
        self.assertIsNone(self.state.read_on_flag())

    def test_every_command_copes_with_unparsable_flag_and_corrupt_state(self):
        flag_path = self._unparsable_flag_and_corrupt_state()
        self._install("running", ON_AT + ONE_DAY)
        on = self.commands.on()
        self.assertEqual((on.already_on, on.on_at), (True, ON_AT))  # the file time is kept; the file is not rewritten
        with open(flag_path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "not a time")
        self.assertEqual(self.commands.usage(CWD).report_text, "report running (1 requests)")  # state rebuilt by the sync
        self.assertEqual(self.commands.off(), OffResult(False, self.log_root))
        self.assertFalse(os.path.exists(flag_path))

    def test_status_while_off_with_corrupt_state_reports_the_reset_without_syncing(self):
        sync_path = os.path.join(self.log_root, ".sync.json")
        with open(sync_path, "w", encoding="utf-8") as handle:
            handle.write("{ corrupt")
        status = self.commands.status()
        self.assertEqual((status.on, status.sync_state_reset, self.sync.calls), (False, True, 0))
        with open(sync_path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "{ corrupt")

    def test_status_and_usage_do_not_sync_while_off(self):
        self._install("late", ON_AT + ONE_DAY)
        self.commands.status()
        with self.assertRaises(CommandError):
            self.commands.usage(CWD)
        self.assertEqual(self.sync.calls, 0)
        self.assertEqual(self.archive.writes, [])
        self.assertFalse(os.path.exists(os.path.join(self.log_root, ".sync.json")))


class StatusTest(CommandTestCase):
    def _append(self, path):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(_transcript(NOW + ONE_DAY))
        os.utime(path, (NOW.timestamp() + 1, NOW.timestamp() + 1))

    def test_status_reports_counts_unreadable_and_state_reset(self):
        self._unparsable_flag_and_corrupt_state()
        self.prices.problem_text = PRICE_PROBLEM
        path_a = self._install("a", ON_AT + ONE_DAY)

        rebuilt = self.commands.status()

        self.assertTrue(rebuilt.on)
        self.assertEqual(rebuilt.on_at, ON_AT)  # the file time, reported as such
        self.assertTrue(rebuilt.on_at_from_file_time)
        self.assertTrue(rebuilt.sync_state_reset)
        self.assertEqual(rebuilt.price_file_problem, PRICE_PROBLEM)
        self.assertEqual((rebuilt.archived_session_count, rebuilt.unreadable), (1, ()))

        self.archive.failure = WriteOutcome("/logs/x.md", False, WRITE_FAILURE)
        self._append(path_a)
        path_b = self._install("b", ON_AT + ONE_DAY)
        self.clock = NOW + ONE_DAY

        status = self.commands.status()

        self.assertEqual(status.last_sync_at, NOW + ONE_DAY)
        self.assertEqual(status.archived_session_count, 1)
        self.assertEqual(status.unreadable, (UnreadableItem(path_b, WRITE_FAILURE), UnreadableItem("/logs/a.md", WRITE_FAILURE)))
        self.assertEqual(status.log_root, self.log_root)

    def test_status_without_problems_has_no_optional_fields(self):
        self._enable()
        status = self.commands.status()
        self.assertEqual((status.on_at, status.on_at_from_file_time, status.sync_state_reset), (ON_AT, False, False))
        self.assertIsNone(status.price_file_problem)
        self.assertEqual(status.unreadable, ())

    def test_status_while_off_reports_the_last_sync_without_syncing(self):
        self._enable()
        self._install("a", ON_AT + ONE_DAY)
        self.commands.off()
        calls = self.sync.calls
        status = self.commands.status()
        self.assertFalse(status.on)
        self.assertIsNone(status.on_at)
        self.assertEqual((status.last_sync_at, status.archived_session_count), (NOW, 1))
        self.assertEqual(self.sync.calls, calls)

    def test_status_while_off_without_state_has_no_on_time_and_counts_nothing(self):
        status = self.commands.status()
        self.assertFalse(status.on)
        self.assertIsNone(status.last_sync_at)
        self.assertEqual((status.archived_session_count, status.unreadable), (0, ()))


class UsageTest(CommandTestCase):
    def _two_sessions(self):
        self._install("older", ON_AT + ONE_DAY)
        self._install("newer", ON_AT + 2 * ONE_DAY)

    def test_usage_latest_prints_saved_report_text(self):
        self._enable()
        self._two_sessions()
        expected = "report newer (1 requests)"
        self.assertEqual(self.commands.usage(CWD).report_text, expected)
        self.assertEqual(self.commands.usage(CWD, "latest").report_text, expected)
        self.assertEqual(self.archive.reports["/logs/newer.md"], expected)

    def test_usage_from_a_subfolder_uses_the_nearest_parent_project(self):
        self._enable()
        self._two_sessions()
        self.assertEqual(self.commands.usage(CWD + "/src/deep").report_text, "report newer (1 requests)")
        with self.assertRaisesRegex(CommandError, "/work/other"):
            self.commands.usage("/work/other")

    def test_usage_while_off_prints_report_saved_at_last_sync_without_syncing(self):
        self._enable()
        self._two_sessions()
        self.commands.off()
        calls = self.sync.calls
        self.assertEqual(self.commands.usage(CWD).report_text, "report newer (1 requests)")
        self.assertEqual(self.sync.calls, calls)

    def test_usage_prints_the_report_a_failed_write_could_not_save(self):
        self._enable()
        self._install("first", ON_AT + ONE_DAY)
        self.commands.status()  # first session is archived and saved
        self.archive.failure = WriteOutcome("/logs/x.md", False, WRITE_FAILURE)
        path = self._install("first", ON_AT + ONE_DAY, ON_AT + 3 * ONE_DAY)
        os.utime(path, (NOW.timestamp() + 5, NOW.timestamp() + 5))
        self.assertEqual(self.commands.usage(CWD).report_text, "report first (2 requests)")

    def test_usage_names_the_log_when_its_saved_report_cannot_be_read(self):
        self._enable()
        self._install("first", ON_AT + ONE_DAY)
        self.commands.status()
        self.archive.reports.clear()
        with self.assertRaisesRegex(CommandError, "/logs/first.md"):
            self.commands.usage(CWD)

    def test_unsupported_target_is_a_command_error_before_any_ranking(self):
        self._enable()
        self._two_sessions()
        with self.assertRaisesRegex(CommandError, "abc"):
            self.commands.usage(CWD, "abc")
        self.assertEqual(self.sync.calls, 0)


class LatestRankingTest(unittest.TestCase):
    """`usage` ranks from the session entries alone (a state double, no sync), through the injected path module."""

    @staticmethod
    def _entry(name, project_dir, prompt_at, activity_at, orphan=False):
        entry = {"logPath": "/logs/%s.md" % name, "state": "written", "lastActivityAt": activity_at.isoformat(),
                 "orphan": orphan, "renderVersion": 1, "unitCount": 1}
        if project_dir is not None:
            entry["projectDir"] = project_dir
        if prompt_at is not None:
            entry["lastPromptAt"] = prompt_at.isoformat()
        return entry

    def _usage(self, entries, cwd, path_module=os.path):
        class Archive:
            def read_report(self, log_path):
                return log_path

        sessions = {"claude:" + name: self._entry(name, *values) for name, values in entries.items()}
        commands = CommandUseCases(None, Archive(), MemoryFlagState(sessions), None, lambda: NOW, "/logs", path_module)
        return commands.usage(cwd).report_text

    def test_prompt_beats_later_activity_without_prompt_and_orphans_never_win(self):
        entries = {
            "streaming": (CWD, ON_AT, NOW),
            "newest-prompt": (CWD, ON_AT + ONE_DAY, ON_AT + ONE_DAY),
            "no-prompt": (CWD, None, NOW + ONE_DAY),
            "orphan": (CWD, NOW + ONE_DAY, NOW + ONE_DAY, True),
            "elsewhere": ("/work/other", NOW, NOW)}
        self.assertEqual(self._usage(entries, CWD), "/logs/newest-prompt.md")

    def test_without_any_prompt_the_newest_activity_wins(self):
        entries = {"old": (CWD, None, ON_AT), "new": (CWD, None, ON_AT + ONE_DAY)}
        self.assertEqual(self._usage(entries, CWD), "/logs/new.md")

    def test_times_compare_as_instants_not_as_text(self):
        plus_two = timezone(timedelta(hours=2))
        later_instant = (ON_AT + ONE_DAY).astimezone(timezone.utc)
        earlier_text_later_clock = (ON_AT + ONE_DAY - timedelta(hours=1)).astimezone(plus_two)  # "T+02:00" sorts above "T+00:00"
        entries = {"earlier": (CWD, earlier_text_later_clock, earlier_text_later_clock),
                   "later": (CWD, later_instant, later_instant)}
        self.assertEqual(self._usage(entries, CWD), "/logs/later.md")

    def test_windows_paths_compare_case_insensitively_and_walk_to_the_parent(self):
        entries = {"win": ("C:\\Work\\Proj", ON_AT, ON_AT)}
        self.assertEqual(self._usage(entries, "c:\\work\\proj\\src", ntpath), "/logs/win.md")

    def test_a_project_reached_through_a_symlink_matches(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root)
        real, link = os.path.join(root, "real"), os.path.join(root, "link")
        os.mkdir(real)
        try:
            os.symlink(real, link)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links are not available here")
        self.assertEqual(self._usage({"linked": (real, ON_AT, ON_AT)}, link), "/logs/linked.md")

    def test_the_nearest_folder_with_sessions_wins_over_a_newer_parent_project(self):
        entries = {"inner": ("/work/proj", ON_AT, ON_AT), "outer": ("/work", ON_AT + ONE_DAY, ON_AT + ONE_DAY)}
        self.assertEqual(self._usage(entries, "/work/proj"), "/logs/inner.md")
        self.assertEqual(self._usage(entries, "/work/proj/src"), "/logs/inner.md")
        self.assertEqual(self._usage(entries, "/work/other"), "/logs/outer.md")

    def test_equal_prompt_times_rank_by_session_key(self):
        entries = {"a": (CWD, ON_AT, ON_AT), "b": (CWD, ON_AT, ON_AT)}
        self.assertEqual(self._usage(entries, CWD), "/logs/b.md")

    def test_an_unresolvable_recorded_folder_does_not_break_the_ranking(self):
        entries = {"nul": ("/work/pro\0j", ON_AT + ONE_DAY, ON_AT + ONE_DAY), "fine": (CWD, ON_AT, ON_AT)}
        self.assertEqual(self._usage(entries, CWD), "/logs/fine.md")

    def test_no_session_for_the_project_raises(self):
        with self.assertRaises(CommandError):
            self._usage({"nowhere": (None, ON_AT, ON_AT)}, CWD)


if __name__ == "__main__":
    unittest.main()
