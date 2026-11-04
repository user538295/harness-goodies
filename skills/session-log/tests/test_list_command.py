"""`list` (C1): the root sessions of the project of the cwd (the cwd or a folder below it), archived ones from the archive and
never-archived ones from the store, oldest start first. A real ClaudeReader, FileStateStore, LogUsageWriter and sync over a
transcript tree."""
import os
from datetime import timezone

from sessionlog.commands import CommandUseCases, ListedSession
from sessionlog.log_usage_writer import LogUsageWriter
from sessionlog.model import Harness, Prompt, Session
from sessionlog.pricing import PricingRules
from sessionlog.reader_port import ScanResult, UnreadableSource
from sessionlog.state_port import OnFlag
from sessionlog.sync import SyncUseCase
from sessionlog.usage_target import UsageTargetResolver
from tests.test_sync import NOW, ON_AT, ONE_DAY, NoLeftovers, NoProbe, _NoPrices
from tests.test_usage_target import ONE_SECOND, PROJECT, UsageTargetTestCase, _CountingSync

SUB_FOLDER = PROJECT + "/sub"
DEEP_FOLDER = PROJECT + "/sub/deep"
PARENT_FOLDER = "/work"
SIBLING_FOLDER = PROJECT + "2"  # shares the project's text as a prefix, but is no folder below it
OTHER_FOLDER = "/work/other"
FINDING_PATH = "/store/unlistable"


class ListTestCase(UsageTargetTestCase):
    def setUp(self):
        super().setUp()
        self.archive = LogUsageWriter(self.log_root, lambda moment: moment.astimezone(timezone.utc))
        self.sync = _CountingSync(SyncUseCase(self.reader, self.archive, self.state, PricingRules(_NoPrices()), lambda: NOW))
        self.commands = CommandUseCases(self.sync, UsageTargetResolver(self.reader), self.archive, self.state, PricingRules(_NoPrices()),
                                        lambda: NOW, self.log_root, NoLeftovers(), NoProbe())

    def _turn_off(self):
        self.state.delete_on_flag()

    def _log_path(self, session_id):
        return self.state.load_sync()["sessions"]["claude:" + session_id]["logPath"]

    def _ids(self, cwd=PROJECT):
        return [session.session_id for session in self.commands.list_sessions(cwd).sessions]

    def _archive_orphan(self, session_id, prompt_at):
        """An archived orphan sub-agent of the project, with a real log: only a list that skips orphans leaves it out. The
        Claude reader never makes orphans, so the entry is written to `.sync.json` directly."""
        orphan = Session(Harness.CLAUDE, session_id, prompt_at, PROJECT, "parent", (Prompt(prompt_at, "orphan prompt"),))
        log_path = self.archive.new_log_path(orphan)
        self.archive.write(log_path, self.archive.render_log(orphan), "report\n")
        state = self.state.load_sync()
        state["sessions"]["claude:" + session_id] = {
            "logPath": log_path, "state": "written", "projectDir": PROJECT, "orphan": True, "lastPromptAt": prompt_at.isoformat(),
            "lastActivityAt": prompt_at.isoformat(), "renderVersion": 1, "unitCount": 1}
        self.state.save_sync(state)


class ListSessionsTest(ListTestCase):
    def test_archived_and_never_archived_sessions_of_the_cwd_and_below_are_listed_oldest_start_first(self):
        archived_sub = self._install("archived_sub", cwd=SUB_FOLDER, prompts=[ON_AT + 2 * ONE_DAY, ON_AT + 4 * ONE_DAY])
        archived_exact = self._install("archived_exact", cwd=PROJECT, prompts=[ON_AT + 3 * ONE_DAY])
        for session_id, folder in (("parent", PARENT_FOLDER), ("sibling", SIBLING_FOLDER), ("other", OTHER_FOLDER)):
            self._install(session_id, cwd=folder, prompts=[ON_AT + ONE_DAY])
        self._turn_on_and_sync()
        self._turn_off()
        stored = self._install("stored", cwd=DEEP_FOLDER, prompts=[ON_AT + ONE_DAY])
        self._archive_orphan("orphan", ON_AT)

        result = self.commands.list_sessions(PROJECT)

        self.assertEqual((
            ListedSession(Harness.CLAUDE.value, "stored", DEEP_FOLDER, ON_AT + ONE_DAY, ON_AT + ONE_DAY + ONE_SECOND, stored, None,
                          ("prompt 0",)),
            ListedSession(Harness.CLAUDE.value, "archived_sub", SUB_FOLDER, ON_AT + 2 * ONE_DAY, ON_AT + 4 * ONE_DAY + ONE_SECOND,
                          archived_sub, self._log_path("archived_sub"), ("prompt 0", "prompt 1")),
            ListedSession(Harness.CLAUDE.value, "archived_exact", PROJECT, ON_AT + 3 * ONE_DAY, ON_AT + 3 * ONE_DAY + ONE_SECOND,
                          archived_exact, self._log_path("archived_exact"), ("prompt 0",)),
        ), result.sessions)
        self.assertEqual([], self.commands.warnings)
        self.assertEqual(0, self.sync.calls)  # logging is off: nothing is synced

    def test_a_folder_below_the_project_lists_only_its_own_part_and_a_parent_lists_everything_below_it(self):
        self._install("exact", cwd=PROJECT, prompts=[ON_AT + ONE_DAY])
        self._install("sub", cwd=SUB_FOLDER, prompts=[ON_AT + 2 * ONE_DAY])
        self._turn_on_and_sync()
        self.assertEqual(["sub"], self._ids(SUB_FOLDER))
        self.assertEqual(["exact", "sub"], self._ids(PARENT_FOLDER))

    def test_a_symlinked_cwd_lists_the_sessions_recorded_under_its_real_folder(self):
        real, link = os.path.join(self.root, "real"), os.path.join(self.root, "link")
        os.makedirs(os.path.join(real, "src"))
        try:
            os.symlink(real, link)
        except (OSError, NotImplementedError):
            self.skipTest("needs symlinks")
        self._install("real", cwd=os.path.join(real, "src"), prompts=[ON_AT + ONE_DAY])
        self._turn_on_and_sync()
        self.assertEqual(["real"], self._ids(link))

    def test_while_logging_is_on_the_list_syncs_first_so_a_new_session_is_listed_from_its_log(self):
        self.state.write_on_flag(OnFlag(ON_AT))
        self._install("new", prompts=[ON_AT + ONE_DAY])
        (listed,) = self.commands.list_sessions(PROJECT).sessions
        self.assertEqual(1, self.sync.calls)
        self.assertEqual(self._log_path("new"), listed.log_path)

    def test_a_project_without_sessions_lists_nothing(self):
        self._install("other", cwd=OTHER_FOLDER, prompts=[ON_AT + ONE_DAY])
        self.assertEqual((), self.commands.list_sessions(PROJECT).sessions)


class ListFallbackTest(ListTestCase):
    def test_an_archived_session_whose_log_is_gone_is_read_from_the_store(self):
        source = self._install("archived", prompts=[ON_AT + ONE_DAY])
        self._turn_on_and_sync()
        self._turn_off()
        os.remove(self._log_path("archived"))
        (listed,) = self.commands.list_sessions(PROJECT).sessions
        self.assertEqual(("archived", ON_AT + ONE_DAY, ("prompt 0",), source, None),
                         (listed.session_id, listed.started_at, listed.prompts, listed.source, listed.log_path))
        self.assertEqual([], self.commands.warnings)

    def test_an_archived_session_whose_log_and_source_are_gone_is_left_out_with_a_warning(self):
        source = self._install("lost", prompts=[ON_AT + ONE_DAY])
        self._install("kept", prompts=[ON_AT + 2 * ONE_DAY])
        self._turn_on_and_sync()
        self._turn_off()
        log_path = self._log_path("lost")
        os.remove(log_path)
        os.remove(source)
        self.assertEqual(["kept"], self._ids())
        self.assertEqual(["session lost is left out: its log %s cannot be read, and its source is gone" % log_path],
                         self.commands.warnings)

    def test_an_unparsable_log_falls_back_to_the_store_and_keeps_the_log_path(self):
        self._install("archived", prompts=[ON_AT + ONE_DAY])
        self._turn_on_and_sync()
        self._turn_off()
        with open(self._log_path("archived"), "w", encoding="utf-8") as handle:
            handle.write("not a log\n")
        (listed,) = self.commands.list_sessions(PROJECT).sessions
        self.assertEqual((("prompt 0",), self._log_path("archived")), (listed.prompts, listed.log_path))

    def test_a_store_that_cannot_be_listed_completely_is_named_in_a_warning_and_the_rest_is_listed(self):
        self._install("archived", prompts=[ON_AT + ONE_DAY])
        self._turn_on_and_sync()
        self._turn_off()
        real_scan = self.reader.scan
        self.reader.scan = lambda: ScanResult(real_scan().units, (UnreadableSource(Harness.CLAUDE, FINDING_PATH, "denied"),))
        self.assertEqual(["archived"], self._ids())
        self.assertEqual(["the store could not be read completely (%s: denied), so the list may miss sessions" % FINDING_PATH],
                         self.commands.warnings)
