"""Live report of a session never archived (BE-20, S68): `usage` reads it from the harness store, prices and renders it like a
sync would, and writes nothing. The use-case tests run a real ClaudeReader, FileStateStore and sync over a transcript tree
with a fake archive; the entry tests run `session_log.main` over a sandbox home with the real writer and price file."""
import ntpath
import os
import unittest

from sessionlog.archive_port import WriteOutcome
from sessionlog.command_error import CommandError
from sessionlog.commands import CommandUseCases
from sessionlog.model import Harness, Prompt, Session
from sessionlog.pricing import PricingRules
from sessionlog.reader_port import (CANNOT_BE_LISTED_REASON, CANNOT_BE_PARSED_REASON, ReadResult, ScanResult, SourceUnit,
                                    UnreadableSource, build_session_key, root_id_of)
from sessionlog.state_port import OnFlag
from sessionlog.sync import SyncUseCase
from sessionlog.usage_target import UsageTargetResolver
from tests.test_session_log import LONG_AGO, ON_FLAG, PRICED_MODEL, REPORT_SUFFIX, SYNC_LOCK, SYNC_STATE, SandboxCase
from tests.test_sync import NOW, ON_AT, ONE_DAY, WRITE_FAILURE, NoLeftovers, NoProbe, _NoPrices
from tests.test_usage_target import BEFORE_ON, EMPTY_PROJECT, OTHER_PROJECT, PROJECT, UsageTargetTestCase, _report

AFTER_ALL_ACTIVITY = "2100-01-01T00:00:00+00:00"  # an on-time after the sandbox transcripts: they are never archived
INVALID_UTF8 = b"\xff\xfe\xfd not json\n"


class _FakeStoreReader:
    """A store of ready-made sessions, each with the time of its one source unit: `(session, modified_at)` pairs."""

    def __init__(self, sessions):
        self._sessions = {session.session_id: session for session, _ in sessions}
        self._units = tuple(SourceUnit(session.session_id, "fingerprint", modified_at) for session, modified_at in sessions)

    def scan(self):
        return ScanResult(self._units)

    def session_of(self, unit):
        return build_session_key(Harness.CLAUDE, unit.key)

    def read(self, session_key, units):
        return ReadResult(session=self._sessions[root_id_of(session_key)])


class LatestInStoreTest(UsageTargetTestCase):
    def _never_archived(self, session_id, modified, cwd=PROJECT, prompts=(BEFORE_ON,)):
        return self._install(session_id, cwd=cwd, prompts=list(prompts), modified=modified)

    def test_latest_reads_store_only_without_archived_session_the_newest_prompt_of_a_root_wins(self):
        self._never_archived("old", BEFORE_ON - 3 * ONE_DAY, prompts=[BEFORE_ON])  # oldest file, newest prompt
        self._never_archived("file-newest-prompt-oldest", BEFORE_ON - ONE_DAY, prompts=[BEFORE_ON - 5 * ONE_DAY])
        self._install("meta-only", modified=BEFORE_ON)  # a newer file, but no prompt
        self._never_archived("elsewhere", BEFORE_ON + ONE_DAY, cwd=OTHER_PROJECT)  # newest of all, other project
        state_before = self.state.load_sync()
        sync_file = os.path.join(self.log_root, SYNC_STATE)
        sync_file_existed = os.path.exists(sync_file)
        self.assertEqual(self._text(), _report("old"))  # the newest prompt wins, not the newest file
        self.assertEqual(self._text(cwd=PROJECT + "/src/deep"), _report("old"))  # nearest parent project
        self.assertEqual(self._text(cwd=OTHER_PROJECT), _report("elsewhere"))
        self.assertEqual((self.sync.calls, self.archive.reports), (0, {}))
        self.assertEqual(self.state.load_sync(), state_before)
        self.assertEqual(os.path.exists(sync_file), sync_file_existed)

    def test_a_session_whose_sub_agent_file_is_the_newest_unit_is_the_latest(self):
        self._never_archived("main-newer", BEFORE_ON)
        self._never_archived("main-older", BEFORE_ON - 2 * ONE_DAY)
        self._install_sub_agent("main-older", "child", BEFORE_ON)  # its file is touched at NOW, newer than every main file
        self.assertEqual(self._text(), _report("main-older"))

    def test_a_newer_archived_session_of_the_project_wins_over_a_never_archived_one_with_an_older_prompt(self):
        self._install("archived", prompts=[ON_AT + ONE_DAY])
        self._never_archived("newer-file", NOW + ONE_DAY)  # the newest file, but its prompt is older than the archived one's
        self._turn_on_and_sync()  # the newer file holds activity before the on-time: it is not archived
        self.assertEqual(set(self.state.load_sync()["sessions"]), {"claude:archived"})
        self.state.delete_on_flag()
        self.assertEqual(self._text(), _report("archived"))

    def test_latest_is_decided_per_project_an_archived_session_elsewhere_does_not_hide_the_store(self):
        self._install("elsewhere", cwd=OTHER_PROJECT, prompts=[ON_AT + ONE_DAY])
        self._never_archived("here", BEFORE_ON)
        self._turn_on_and_sync()
        self.assertEqual(self._text(), _report("here"))
        self.assertEqual(self._text(cwd=OTHER_PROJECT), _report("elsewhere"))

    def test_a_project_without_any_root_session_with_a_prompt_exits_1_naming_it(self):
        self._install("meta-only", modified=BEFORE_ON)
        self._never_archived("elsewhere", BEFORE_ON, cwd=OTHER_PROJECT)
        with self.assertRaisesRegex(CommandError, "no session for this project \\(%s\\)" % PROJECT):
            self._text()
        with self.assertRaisesRegex(CommandError, EMPTY_PROJECT):
            self._text(cwd=EMPTY_PROJECT)

    def test_latest_prints_the_report_a_first_failed_write_could_not_save(self):
        self.state.write_on_flag(OnFlag(ON_AT))
        self.archive.failure = WriteOutcome("/logs/x.md", False, WRITE_FAILURE)
        self._install("first", prompts=[ON_AT + ONE_DAY])
        renders = self._count_renders()
        self.assertEqual(self._text(), _report("first") + " #1")  # the text the sync rendered, not a second rendering
        self.assertEqual((len(renders), self.archive.reports), (1, {}))

    def test_a_store_that_could_not_be_listed_completely_cannot_name_the_latest_session(self):
        self._never_archived("here", BEFORE_ON)
        real_scan = self.reader.scan
        incomplete = UnreadableSource(Harness.CLAUDE, "/store/locked", CANNOT_BE_LISTED_REASON)
        self.reader.scan = lambda: ScanResult(real_scan().units, (incomplete,))
        with self.assertRaisesRegex(CommandError, "/store/locked: " + CANNOT_BE_LISTED_REASON):
            self._text()

    def test_an_unreadable_session_source_exits_1_naming_it_in_the_ranking_and_for_its_id(self):
        path = self._never_archived("broken", BEFORE_ON)
        self._never_archived("fine", BEFORE_ON - ONE_DAY, prompts=[BEFORE_ON - ONE_DAY])
        with open(path, "wb") as handle:
            handle.write(INVALID_UTF8)
        os.utime(path, (BEFORE_ON.timestamp(), BEFORE_ON.timestamp()))
        for target in (None, "broken"):
            with self.assertRaisesRegex(CommandError, "%s: %s" % (path, CANNOT_BE_PARSED_REASON)):
                self._text(target)
        self.assertEqual(self._text("fine"), _report("fine"))

    def test_a_session_without_any_readable_content_has_nothing_to_report(self):
        path = self._never_archived("emptied", BEFORE_ON)
        self._write(path, [])
        with self.assertRaisesRegex(CommandError, "session emptied has nothing to report"):
            self._text("emptied")

    def test_an_emptied_newest_file_is_skipped_and_the_older_session_of_the_project_is_the_latest(self):
        emptied = self._never_archived("emptied", BEFORE_ON)
        self._never_archived("older", BEFORE_ON - ONE_DAY)
        self._write(emptied, [])
        self.assertEqual(self._text(), _report("older"))

    def test_a_never_archived_session_is_found_from_a_symlinked_cwd_and_its_subfolder(self):
        real, link = os.path.join(self.root, "real"), os.path.join(self.root, "link")
        os.makedirs(os.path.join(real, "src"))
        try:
            os.symlink(real, link)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links are not available here")
        self._never_archived("linked", BEFORE_ON, cwd=real)
        self.assertEqual(self._text(cwd=link), _report("linked"))
        self.assertEqual(self._text(cwd=os.path.join(link, "src")), _report("linked"))

    def test_a_never_archived_session_is_found_by_windows_path_rules(self):
        reader = _FakeStoreReader([
            (Session(Harness.CLAUDE, "win", NOW, prompts=(Prompt(NOW, "a prompt"),), project_dir="C:\\Work\\Proj"), NOW)])
        pricing = PricingRules(_NoPrices())
        commands = CommandUseCases(SyncUseCase(reader, self.archive, self.state, pricing, lambda: NOW),
                                   UsageTargetResolver(reader, ntpath), self.archive, self.state, pricing, lambda: NOW,
                                   self.log_root, NoLeftovers(), NoProbe())
        self.assertEqual(commands.usage_latest("c:\\work\\proj\\src").report_text, _report("win"))

    def test_an_archived_session_without_a_project_folder_is_never_a_live_report_of_the_store_fallback(self):
        self._never_archived("archived", BEFORE_ON)
        state = self.state.load_sync()
        state["sessions"]["claude:archived"] = {
            "logPath": "/logs/archived.md", "state": "written", "orphan": False, "lastPromptAt": BEFORE_ON.isoformat(),
            "lastActivityAt": BEFORE_ON.isoformat(), "renderVersion": 1, "unitCount": 1}
        self.state.save_sync(state)
        self.archive.reports["/logs/archived.md"] = _report("archived")
        with self.assertRaisesRegex(CommandError, "no session for this project \\(%s\\)" % PROJECT):
            self._text()

    def test_orphans_and_sessions_without_a_project_folder_are_never_the_latest(self):
        def session(session_id, **fields):
            return Session(Harness.CLAUDE, session_id, NOW, prompts=(Prompt(NOW, "a prompt"),), **fields)
        reader = _FakeStoreReader([
            (session("orphan", project_dir=PROJECT, orphan_of_parent_id="parent"), NOW),
            (session("no-project"), NOW - ONE_DAY),
            (session("valid", project_dir=PROJECT), NOW - 2 * ONE_DAY)])
        pricing = PricingRules(_NoPrices())
        commands = CommandUseCases(SyncUseCase(reader, self.archive, self.state, pricing, lambda: NOW),
                                   UsageTargetResolver(reader), self.archive, self.state, pricing, lambda: NOW, self.log_root,
                                   NoLeftovers(), NoProbe())
        self.assertEqual(commands.usage_latest(PROJECT).report_text, _report("valid"))

    def _two_projects_nested_in_work(self):
        self._never_archived("project-older", BEFORE_ON - ONE_DAY)
        self._never_archived("work-newer", BEFORE_ON, cwd="/work")

    def test_latest_from_a_subfolder_is_the_session_of_the_nearest_parent_folder_not_the_newest_file(self):
        self._two_projects_nested_in_work()
        self.assertEqual(self._text(cwd=PROJECT + "/src/deep"), _report("project-older"))

    def test_latest_from_a_folder_beside_the_project_is_the_session_of_the_common_parent(self):
        self._two_projects_nested_in_work()
        self.assertEqual(self._text(cwd=OTHER_PROJECT), _report("work-newer"))

    def test_latest_prefers_a_session_recorded_exactly_in_the_cwd_even_when_older_than_the_parent_ones(self):
        self._two_projects_nested_in_work()
        self._never_archived("exact", BEFORE_ON - 2 * ONE_DAY, cwd=PROJECT + "/src/deep")
        self.assertEqual(self._text(cwd=PROJECT + "/src/deep"), _report("exact"))

    def test_a_session_found_in_the_cwd_itself_does_not_stop_the_read_so_an_older_unreadable_session_of_another_project_is_still_opened(self):
        self._never_archived("here", BEFORE_ON)
        older = self._never_archived("older-elsewhere", BEFORE_ON - ONE_DAY, cwd=OTHER_PROJECT)
        with open(older, "wb") as handle:
            handle.write(INVALID_UTF8)
        os.utime(older, (BEFORE_ON.timestamp() - ONE_DAY.total_seconds(),) * 2)
        with self.assertRaisesRegex(CommandError, "%s: %s" % (older, CANNOT_BE_PARSED_REASON)):  # its project is known only from its content
            self._text()

    def test_a_newer_unreadable_session_of_an_unrelated_project_exits_1_naming_it(self):
        self._never_archived("here", BEFORE_ON - ONE_DAY)
        newer = self._never_archived("newer-elsewhere", BEFORE_ON, cwd=OTHER_PROJECT)
        with open(newer, "wb") as handle:
            handle.write(INVALID_UTF8)
        os.utime(newer, (BEFORE_ON.timestamp(),) * 2)
        with self.assertRaisesRegex(CommandError, "%s: %s" % (newer, CANNOT_BE_PARSED_REASON)):
            self._text()

    def test_a_never_archived_session_of_the_exact_folder_beats_an_archived_session_of_a_parent_folder(self):
        self._install("archived", cwd="/work", prompts=[ON_AT + ONE_DAY])
        self._never_archived("newer-here", NOW + ONE_DAY, cwd=PROJECT + "/src")
        self._turn_on_and_sync()
        self.assertEqual(set(self.state.load_sync()["sessions"]), {"claude:archived"})
        self.state.delete_on_flag()
        self.assertEqual(self._text(cwd=PROJECT + "/src"), _report("newer-here"))
        self.assertEqual(self._text(cwd="/work"), _report("archived"))

    def test_a_never_archived_session_is_read_exactly_once_for_latest(self):
        self._never_archived("solo", BEFORE_ON)
        reads = self._count_reads()
        self.assertEqual(self._text(), _report("solo"))
        self.assertEqual(reads, ["claude:solo"])

    def test_a_never_archived_session_is_read_exactly_once_for_its_id(self):
        self._never_archived("solo", BEFORE_ON)
        reads = self._count_reads()
        self.assertEqual(self._text("solo"), _report("solo"))
        self.assertEqual(reads, ["claude:solo"])

    def _count_reads(self) -> list:
        reads = []
        real_read = self.reader.read
        self.reader.read = lambda key, units: reads.append(key) or real_read(key, units)
        return reads

    def _install_with_unreadable_sub_agent(self, session_id):
        self._never_archived(session_id, BEFORE_ON)
        self._install_sub_agent(session_id, "child", BEFORE_ON)
        path = os.path.join(self.folder, session_id, "subagents", "agent-child.jsonl")
        with open(path, "wb") as handle:
            handle.write(INVALID_UTF8)
        return path

    def test_a_session_with_an_unreadable_sub_agent_file_exits_1_naming_it_for_its_id(self):
        path = self._install_with_unreadable_sub_agent("parent")
        with self.assertRaisesRegex(CommandError, "%s: %s" % (path, CANNOT_BE_PARSED_REASON)):
            self._text("parent")

    def test_a_session_with_an_unreadable_sub_agent_file_exits_1_naming_it_for_latest(self):
        path = self._install_with_unreadable_sub_agent("parent")
        with self.assertRaisesRegex(CommandError, "%s: %s" % (path, CANNOT_BE_PARSED_REASON)):
            self._text()


class IdLiveReportTest(UsageTargetTestCase):
    def _archived_and_two_never_archived_with_shared_prefix(self):
        self._install("abc", prompts=[ON_AT + ONE_DAY])
        self._install("abcd", prompts=[BEFORE_ON], modified=BEFORE_ON)
        self._install("xyz", prompts=[BEFORE_ON], modified=BEFORE_ON)

    def test_usage_id_and_prefix_print_the_live_report_while_on_and_off_and_archive_nothing(self):
        self._archived_and_two_never_archived_with_shared_prefix()
        self._turn_on_and_sync()
        for logging_on in (True, False):
            if not logging_on:
                self.state.delete_on_flag()
            with self.subTest(logging_on=logging_on):
                self.assertEqual(self._text("abcd"), _report("abcd"))  # exact, never archived
                self.assertEqual(self._text("xy"), _report("xyz"))  # unique prefix, never archived
                self.assertEqual(self._text("abc"), _report("abc"))  # the archived one wins its exact id
                with self.assertRaisesRegex(CommandError, "matches 2 sessions: abc, abcd"):
                    self._text("ab")
        self.assertEqual(set(self.state.load_sync()["sessions"]), {"claude:abc"})
        self.assertEqual(sorted(self.archive.reports), ["/logs/abc.md"])

    def test_a_live_report_is_built_from_the_sub_agent_files_of_its_session_too(self):
        self._install("solo", prompts=[BEFORE_ON], modified=BEFORE_ON)
        self._install_sub_agent("solo", "child", BEFORE_ON)
        rendered = []
        self.archive.render_report = lambda report: rendered.append(report) or _report(report.session_id)
        self._text("solo")
        self.assertEqual(len(rendered), 1)
        self.assertEqual(len(rendered[0].sub_agents), 1)

    def test_a_live_id_lookup_while_off_never_syncs(self):
        self._install("solo", prompts=[BEFORE_ON], modified=BEFORE_ON)
        self.assertEqual(self._text("solo"), _report("solo"))
        self.assertEqual(self.sync.calls, 0)


class EntryLiveReportTest(SandboxCase):
    def tree(self):
        """Every file under the log folder with its bytes (a file that is not there is not in it)."""
        found = {}
        for folder, _, names in os.walk(self.log_root):
            for name in names:
                with open(os.path.join(folder, name), "rb") as stream:
                    found[os.path.join(folder, name)] = stream.read()
        return found

    def set_on_time(self, text):
        os.makedirs(self.log_root, exist_ok=True)
        with open(os.path.join(self.log_root, ON_FLAG), "w", encoding="utf-8") as stream:
            stream.write(text + "\n")

    def saved_reports(self):
        return [os.path.join(folder, name) for folder, _, names in os.walk(self.log_root) for name in names
                if name.endswith(REPORT_SUFFIX)]

    def test_never_archived_session_text_equals_what_a_sync_would_save_and_nothing_is_written(self):
        self.write_transcript(session="abc123", model=PRICED_MODEL)

        # logging off: nothing exists beside the transcript afterwards, not even the log folder
        code, live, stderr = self.run_main(["claude", "usage", "abc123"])
        self.assertEqual((0, ""), (code, stderr))
        self.assertIn("abc123", live)
        self.assertIn("$", live)  # priced by the real price file
        self.assertNotIn("price: unknown", live)
        self.assertEqual(self.run_main(["claude", "usage"]), (0, live, ""))  # --latest: nothing archived, so the store
        self.assertEqual(self.run_main(["claude", "usage", "abc"]), (0, live, ""))
        self.assertFalse(os.path.exists(self.log_root))

        # logging on, but turned on after the transcript: the sync archives nothing and the command writes nothing
        self.set_on_time(AFTER_ALL_ACTIVITY)
        self.assertEqual(self.run_main(["claude", "status"])[0], 0)  # the sync alone; the sandbox clock is fixed, so its own
        after_sync = self.tree()  # `lastSyncAt` rewrite is byte-identical and any difference below is the live path's
        self.assertEqual(self.run_main(["claude", "usage", "abc123"]), (0, live, ""))
        self.assertEqual(self.run_main(["claude", "usage"]), (0, live, ""))
        self.assertEqual(self.run_main(["claude", "usage", "abc"]), (0, live, ""))
        self.assertEqual(self.tree(), after_sync)  # every file under the log folder is byte-identical
        sync_files = sorted([ON_FLAG, SYNC_LOCK, SYNC_STATE])  # the sync's own state; no log, report folder or temp file beside them
        self.assertEqual(sorted(os.path.basename(path) for path in self.tree()), sync_files)
        self.assertEqual(sorted(os.listdir(self.log_root)), sync_files)

        # turned on before it, a sync saves the same text (the earlier sync recorded the unit as seen, so its state is reset)
        os.unlink(os.path.join(self.log_root, SYNC_STATE))
        self.set_on_time(LONG_AGO)
        self.assertEqual(self.run_main(["claude", "status"])[0], 0)
        saved = self.saved_reports()
        self.assertEqual(len(saved), 1)
        with open(saved[0], encoding="utf-8") as stream:
            self.assertEqual(stream.read(), live)

    def test_unreadable_source_exits_1_with_a_message_and_writes_nothing(self):
        self.write_transcript(session="abc123")
        path = os.path.join(self.claude, "projects", "-work-proj", "abc123.jsonl")
        with open(path, "wb") as stream:
            stream.write(INVALID_UTF8)
        for argv in (["claude", "usage", "abc123"], ["claude", "usage"]):
            code, stdout, stderr = self.run_main(argv)
            self.assertEqual((1, ""), (code, stdout))
            self.assertEqual("session-log: session abc123 cannot be read (%s: %s)\n" % (path, CANNOT_BE_PARSED_REASON), stderr)
        self.assertFalse(os.path.exists(self.log_root))  # logging was never turned on

        self.set_on_time(AFTER_ALL_ACTIVITY)
        for argv in (["claude", "usage", "abc123"], ["claude", "usage"]):
            code, stdout, stderr = self.run_main(argv)
            self.assertEqual((1, ""), (code, stdout))
            self.assertEqual("session-log: session abc123 cannot be read (%s: %s)\n" % (path, CANNOT_BE_PARSED_REASON), stderr)
        self.assertEqual(sorted(os.listdir(self.log_root)), sorted([ON_FLAG, SYNC_LOCK, SYNC_STATE]))


if __name__ == "__main__":
    unittest.main()
