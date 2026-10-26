"""`usage` resolution across the archive and the store (S37, S68), where the old rules showed a session older than one that exists:
the latest session of the nearest project folder wins whether it is archived or not, and a sub-agent id names its root session."""
import ntpath
import os
import unittest
from datetime import timedelta

from sessionlog.command_error import CommandError
from sessionlog.commands import CommandUseCases
from sessionlog.model import Harness, Prompt, Session, SubAgent
from sessionlog.pricing import PricingRules
from sessionlog.reader_port import ReadResult, UnreadableSource
from sessionlog.sync import SyncUseCase
from sessionlog.usage_target import UsageTargetResolver
from tests.test_live_report import _FakeStoreReader
from tests.test_sync import NOW, ON_AT, ONE_DAY, NoLeftovers, NoProbe, _NoPrices
from tests.test_usage_target import BEFORE_ON, EMPTY_PROJECT, OTHER_PROJECT, PROJECT, UsageTargetTestCase, _report

SUB_FOLDER = PROJECT + "/sub"


class _ReadCounter:
    """Records the session keys the reader is asked to read."""

    def __init__(self, reader) -> None:
        self.keys = []
        real_read = reader.read

        def counted(session_key, units):
            self.keys.append(session_key)
            return real_read(session_key, units)
        reader.read = counted


class LatestAcrossArchiveAndStoreTest(UsageTargetTestCase):
    def test_a_never_archived_session_of_the_exact_folder_beats_an_archived_session_of_a_parent_folder(self):
        self._install("parent", prompts=[ON_AT + ONE_DAY])
        self._install("exact", cwd=SUB_FOLDER, prompts=[BEFORE_ON], modified=BEFORE_ON)
        self._turn_on_and_sync()
        self.assertEqual(set(self.state.load_sync()["sessions"]), {"claude:parent"})  # `exact` was never archived
        self.assertEqual(self._text(cwd=SUB_FOLDER), _report("exact"))
        self.assertEqual(self._text(), _report("parent"))  # the project folder itself has only the archived one

    def test_while_logging_is_off_a_newer_never_archived_session_beats_an_older_archived_one(self):
        self._install("archived", prompts=[ON_AT + ONE_DAY])
        self._turn_on_and_sync()
        self.state.delete_on_flag()
        self._install("newer", prompts=[ON_AT + 5 * ONE_DAY])
        self.assertEqual(self._text(), _report("newer"))
        self.assertEqual(self.sync.calls, 0)
        self.assertEqual(set(self.state.load_sync()["sessions"]), {"claude:archived"})  # nothing was archived by asking

    def test_a_newer_archived_session_wins_but_every_store_session_is_ranked_as_an_old_file_may_hold_a_newer_prompt(self):
        self._install("archived", prompts=[ON_AT + ONE_DAY])
        self._install("older", prompts=[BEFORE_ON], modified=BEFORE_ON)
        self._install("elsewhere", cwd=OTHER_PROJECT, prompts=[BEFORE_ON - ONE_DAY], modified=BEFORE_ON - ONE_DAY)
        self._turn_on_and_sync()
        reads = _ReadCounter(self.reader)
        self.assertEqual(self._text(), _report("archived"))
        self.assertEqual(sorted(reads.keys), ["claude:elsewhere", "claude:older"])

    def test_a_never_archived_session_with_a_prompt_beats_an_archived_one_without_a_prompt(self):
        self._install("older", prompts=[BEFORE_ON], modified=BEFORE_ON)
        self._install("no-prompt", activity=NOW)
        self._turn_on_and_sync()
        self.assertEqual(set(self.state.load_sync()["sessions"]), {"claude:no-prompt"})
        self.assertEqual(self._text(), _report("older"))

    def test_a_store_that_could_not_be_listed_completely_still_gives_the_latest_of_what_it_listed_beside_an_archived_session(self):
        self._install("archived", prompts=[ON_AT + ONE_DAY])
        self._turn_on_and_sync()
        self.state.delete_on_flag()
        self._install("newer", prompts=[ON_AT + 5 * ONE_DAY])
        real_scan = self.reader.scan
        incomplete = UnreadableSource(Harness.CLAUDE, "/store/locked", "cannot be listed")
        self.reader.scan = lambda: type(real_scan())(real_scan().units, (incomplete,))
        self.assertEqual(self._text(), _report("newer"))  # a Cursor `.invalid` folder must not end `usage` for good

    def test_an_unreadable_session_beside_an_archived_one_is_passed_over_but_alone_it_ends_the_lookup(self):
        self._install("archived", prompts=[ON_AT + ONE_DAY])
        self._turn_on_and_sync()
        self.state.delete_on_flag()
        self._install_records("broken", [])
        with open(self.folder + "/broken.jsonl", "wb") as handle:
            handle.write(b"\xff\xfe\xfd not json\n")
        self.assertEqual(self._text(), _report("archived"))
        with self.assertRaisesRegex(CommandError, "broken.jsonl"):
            self._text(cwd=EMPTY_PROJECT)


class SubAgentIdTargetTest(UsageTargetTestCase):
    def test_a_sub_agent_id_names_the_report_of_its_archived_root_session(self):
        self._install("abc", prompts=[ON_AT + ONE_DAY])
        self._install_sub_agent("abc", "child", ON_AT + 2 * ONE_DAY)
        self._install("other", prompts=[ON_AT + 3 * ONE_DAY])
        self._turn_on_and_sync()
        self.assertEqual(self._text("agent-child"), self._text("abc"))
        self.assertNotEqual(self._text("agent-child"), self._text("other"))

    def test_a_sub_agent_id_names_the_live_report_of_a_never_archived_root_session(self):
        self._install("abc", prompts=[BEFORE_ON], modified=BEFORE_ON)
        self._install_sub_agent("abc", "child", BEFORE_ON)
        self.assertEqual(self._text("agent-child"), self._text("abc"))
        self.assertEqual(self.state.load_sync()["sessions"], {})

    def test_only_an_exact_sub_agent_id_matches_never_a_prefix_of_one(self):
        self._install("abc", prompts=[ON_AT + ONE_DAY])
        self._install_sub_agent("abc", "child", ON_AT + 2 * ONE_DAY)
        self._turn_on_and_sync()
        with self.assertRaisesRegex(CommandError, "no session has the id 'agent-chi'"):
            self._text("agent-chi")

    def test_a_root_id_prefix_wins_over_a_sub_agent_id(self):
        self._install("agent-child-root", prompts=[ON_AT + ONE_DAY])
        self._install("abc", prompts=[ON_AT + 2 * ONE_DAY])
        self._install_sub_agent("abc", "child", ON_AT + 3 * ONE_DAY)
        self._turn_on_and_sync()
        self.assertEqual(self._text("agent-child"), _report("agent-child-root"))


class SubAgentFileNameTest(UsageTargetTestCase):
    """The Claude reader names a sub-agent by its file stem, so an id that no sub-agent file is named after is looked for in no session."""

    def _store_with_sub_agents(self):
        for number, session_id in enumerate(("one", "two", "three")):
            self._install(session_id, prompts=[BEFORE_ON + number * ONE_DAY], modified=BEFORE_ON)
        self._install_sub_agent("two", "child", BEFORE_ON)
        self._install_sub_agent("three", "other-child", BEFORE_ON)
        self._install_sub_agent("three", "child-long", BEFORE_ON)  # `agent-child` is no prefix match, only the exact stem is one
        return _ReadCounter(self.reader)

    def test_an_id_that_no_sub_agent_file_is_named_after_reads_no_session(self):
        reads = self._store_with_sub_agents()
        with self.assertRaisesRegex(CommandError, "no session has the id 'agent-nope'"):
            self._text("agent-nope")
        self.assertEqual(reads.keys, [])

    def test_a_sub_agent_id_reads_only_the_session_that_holds_a_file_of_that_name(self):
        reads = self._store_with_sub_agents()
        self.assertEqual(self._text("agent-child"), _report("two"))
        self.assertEqual(reads.keys, ["claude:two"])

    def test_a_main_session_file_named_like_a_sub_agent_is_no_sub_agent(self):
        self._install("agent-ghost", prompts=[BEFORE_ON], modified=BEFORE_ON)
        reads = _ReadCounter(self.reader)
        self.assertEqual(self._text("agent-ghost"), _report("agent-ghost"))  # an exact root id, resolved as one
        self.assertEqual(reads.keys, ["claude:agent-ghost"])


class _WholeReadCounter:
    """Records the session keys the reader is asked to read with a sub-agent file among their units."""

    def __init__(self, reader) -> None:
        self.keys = []
        real_read = reader.read

        def counted(session_key, units):
            if any("subagents" in unit.key for unit in units):
                self.keys.append(session_key)
            return real_read(session_key, units)
        reader.read = counted


class RankedFromMainFilesTest(UsageTargetTestCase):
    """`latest` learns a session's project, prompts and orphan state from its main file alone and reads all the files (sub-agents too)
    of the one session it picks."""

    def _store(self):
        for number, session_id in enumerate(("older", "newer", "elsewhere")):
            cwd = OTHER_PROJECT if session_id == "elsewhere" else PROJECT
            self._install(session_id, cwd=cwd, prompts=[BEFORE_ON + number * ONE_DAY], modified=BEFORE_ON + number * ONE_DAY)
            self._install_sub_agent(session_id, "child", BEFORE_ON)
            moment = (BEFORE_ON + number * ONE_DAY).timestamp()
            os.utime(os.path.join(self.folder, session_id, "subagents", "agent-child.jsonl"), (moment, moment))  # the order of sessions

    def test_only_the_session_it_picks_is_read_with_its_sub_agent_files(self):
        self._store()
        whole = _WholeReadCounter(self.reader)
        self.assertEqual(self._text(cwd=SUB_FOLDER), _report("newer"))  # the nearest folder with a session: its parent, newest prompt
        self.assertEqual(whole.keys, ["claude:newer"])

    def _break_sub_agent_file(self, session_id):
        path = os.path.join(self.folder, session_id, "subagents", "agent-child.jsonl")
        modified = os.stat(path).st_mtime
        with open(path, "wb") as handle:
            handle.write(b"\xff\xfe\xfd not json\n")
        os.utime(path, (modified, modified))  # the order of sessions stays

    def test_a_sub_agent_file_that_cannot_be_read_ends_the_lookup_only_when_its_session_is_the_pick(self):
        self._store()
        self._break_sub_agent_file("older")
        self._break_sub_agent_file("elsewhere")
        self.assertEqual(self._text(cwd=SUB_FOLDER), _report("newer"))
        self._break_sub_agent_file("newer")
        with self.assertRaisesRegex(CommandError, "newer/subagents/agent-child.jsonl"):
            self._text(cwd=SUB_FOLDER)


class _SubAgentStoreCase(unittest.TestCase):
    """A store of ready-made sessions, as the Codex and OMP readers build them: nested sub-agents with their own ids."""

    def setUp(self):
        import os
        import shutil
        import tempfile
        from sessionlog.state import FileStateStore
        from tests.test_commands import ReportKeepingArchive
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        self.log_root = os.path.join(root, "prompt-logs")
        os.makedirs(self.log_root)
        self.state = FileStateStore(self.log_root)
        self.archive = ReportKeepingArchive()

    def _commands(self, reader):
        pricing = PricingRules(_NoPrices())
        return CommandUseCases(SyncUseCase(reader, self.archive, self.state, pricing, lambda: NOW),
                               UsageTargetResolver(reader, ntpath), self.archive, self.state, pricing, lambda: NOW,
                               self.log_root, NoLeftovers(), NoProbe())

    @staticmethod
    def _session(session_id, sub_agents=()):
        return Session(Harness.CODEX, session_id, NOW, prompts=(Prompt(NOW, "a prompt"),), project_dir="C:\\work",
                       sub_agents=tuple(sub_agents))


class NestedSubAgentTest(_SubAgentStoreCase):
    def test_a_nested_sub_agent_id_names_its_root_session(self):
        deep = SubAgent("019e-deep", "worker", NOW)
        child = SubAgent("019e-child", "task", NOW, sub_agents=(deep,))
        reader = _FakeStoreReader([(self._session("root", [child]), NOW), (self._session("other"), NOW - ONE_DAY)])
        commands = self._commands(reader)
        expected = commands.usage_of("root").report_text
        self.assertEqual(commands.usage_of("019e-child").report_text, expected)
        self.assertEqual(commands.usage_of("019e-deep").report_text, expected)

    def test_a_sub_agent_id_that_no_session_holds_is_unknown_naming_the_id(self):
        reader = _FakeStoreReader([(self._session("root", [SubAgent("019e-child", "task", NOW)]), NOW)])
        with self.assertRaisesRegex(CommandError, "no session has the id '019e-nope'"):
            self._commands(reader).usage_of("019e-nope")

    def test_an_unreadable_session_beside_a_missing_id_says_the_id_cannot_be_resolved(self):
        class Unreadable(_FakeStoreReader):
            def read(self, session_key, units):
                return ReadResult(unreadable=(UnreadableSource(Harness.CODEX, "/store/bad.jsonl", "cannot be decoded"),))

        reader = Unreadable([(self._session("bad"), NOW - timedelta(days=1))])
        with self.assertRaisesRegex(CommandError, "/store/bad.jsonl: cannot be decoded"):
            self._commands(reader).usage_of("019e-missing")


if __name__ == "__main__":
    unittest.main()
