"""Sync use case (C1) against fake Archive and StateStore ports and a real ClaudeReader over the session fixture."""
import copy
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sessionlog.archive_port import WriteOutcome
from sessionlog.claude_reader import ClaudeReader
from sessionlog.commands import CommandUseCases, UnreadableItem
from sessionlog.log_usage_writer import LogUsageWriter
from sessionlog.model import MAX_ID_LENGTH, Harness
from sessionlog.pricing import PricingRules
from sessionlog.reader_port import UNSAFE_ID_REASON, ReadResult, ScanResult, SourceUnit, UnreadableSource, build_session_key
from sessionlog.state import FileStateStore
from sessionlog.state_port import OnFlag
from sessionlog.sync import RENDER_VERSION, SyncUseCase

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "claude", "main_session.jsonl")
PROJECT = "-work-proj"
SESSION_COUNT = 200
ARCHIVED_SESSIONS = 3
FILES_PER_SESSION = 2  # log and usage report
ON_AT = datetime(2026, 6, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
LONG_AGO = datetime(2026, 1, 1, tzinfo=timezone.utc)
ONE_DAY = timedelta(days=1)
WRITE_FAILURE = "disk full"


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _transcript(*prompt_times: datetime) -> str:
    records = []
    for number, moment in enumerate(prompt_times):
        records.append({"type": "user", "promptSource": "typed", "cwd": "/work/proj", "timestamp": _stamp(moment),
                        "message": {"role": "user", "content": "prompt %d" % number}})
        records.append({"type": "assistant", "timestamp": _stamp(moment + timedelta(seconds=1)),
                        "message": {"id": "m%d" % number, "model": "claude-x", "content": [{"type": "text", "text": "reply"}],
                                    "usage": {"input_tokens": 1, "output_tokens": 1}}})
    return "".join(json.dumps(record) + "\n" for record in records)


class _NoPrices:
    def rate_for(self, model_name, fast):
        return None

    def problem(self):
        return None


class MemoryState:
    """StateStore port in memory; `saved` is what a killed run would leave on disk."""

    sync_reset = False

    def __init__(self, on_at: Optional[datetime] = ON_AT):
        self.flag = OnFlag(on_at) if on_at else None
        self.saved = {"units": {}, "sessions": {}}
        self.saves = 0

    def read_on_flag(self):
        return self.flag

    def write_on_flag(self, flag):
        self.flag = flag

    def delete_on_flag(self):
        self.flag = None

    def load_sync(self):
        return copy.deepcopy(self.saved)

    def save_sync(self, state):
        self.saved = copy.deepcopy(state)
        self.saves += 1


class FakeArchive:
    def __init__(self):
        self.new_paths: List[str] = []
        self.writes: List[tuple] = []
        self.failure: Optional[WriteOutcome] = None
        self.crash_on_write: Optional[int] = None
        self.before_write = lambda: None

    def new_log_path(self, session):
        self.new_paths.append(session.session_id)
        return "/logs/%s.md" % session.session_id

    def render_log(self, session):
        return "log %s: %s" % (session.session_id, "|".join(prompt.text for prompt in session.prompts))

    def render_report(self, report):
        return "report %s (%d requests)" % (report.session_id, report.total.request_count)

    def write(self, log_path, log_text, report_text):
        self.before_write()
        self.writes.append((log_path, log_text, report_text))
        if self.crash_on_write is not None and len(self.writes) == self.crash_on_write:
            raise KeyboardInterrupt
        return self.failure or WriteOutcome(log_path, True)

    def read_report(self, log_path):
        return None


class CountingReader(ClaudeReader):
    def __init__(self, root):
        super().__init__(root)
        self.read_keys: List[str] = []
        self.read_unit_keys: List[tuple] = []
        self.session_of_calls = 0

    def session_of(self, unit):
        self.session_of_calls += 1
        return super().session_of(unit)

    def read(self, session_key, units):
        self.read_keys.append(session_key)
        self.read_unit_keys.append(tuple(sorted(unit.key for unit in units)))
        calls = self.session_of_calls  # ClaudeReader.read asks session_of itself; only the sync's own calls count
        try:
            return super().read(session_key, units)
        finally:
            self.session_of_calls = calls


class SyncTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        self.folder = os.path.join(self.root, "projects", PROJECT)
        os.makedirs(self.folder)
        self.state = MemoryState()
        self.archive = FakeArchive()
        self.reader = CountingReader(self.root)

    def _sync(self):
        use_case = SyncUseCase(self.reader, self.archive, self.state, PricingRules(_NoPrices()), lambda: NOW)
        return use_case.sync()

    def _install_fixture(self, sid, folder=None):
        path = os.path.join(folder or self.folder, sid + ".jsonl")
        shutil.copy(FIXTURE, path)
        os.utime(path, (NOW.timestamp(), NOW.timestamp()))
        return path

    def _install(self, sid, text, modified_at):
        path = os.path.join(self.folder, sid + ".jsonl")
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.utime(path, (modified_at.timestamp(), modified_at.timestamp()))
        return path

    def _append(self, path, text):
        """Appends and pins the mtime, so the file stays 'modified since the on-time' on any machine clock."""
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)
        os.utime(path, (NOW.timestamp(), NOW.timestamp()))

    def _key(self, sid):
        return build_session_key(Harness.CLAUDE, sid)


class SyncArchivesTest(SyncTestCase):
    def test_sync_archives_session_active_since_on_and_skips_older(self):
        self._install("old", _transcript(LONG_AGO), LONG_AGO)
        self._install("spanning", _transcript(LONG_AGO, ON_AT + ONE_DAY), ON_AT + ONE_DAY)
        self._install_fixture("fixture")
        self._sync()
        self.assertEqual(len(self.archive.writes), 2)
        written = {log_path: log_text for log_path, log_text, _ in self.archive.writes}
        self.assertEqual(sorted(written), ["/logs/fixture.md", "/logs/spanning.md"])
        self.assertEqual(written["/logs/spanning.md"], "log spanning: prompt 0|prompt 1")
        sessions = self.state.saved["sessions"]
        self.assertEqual(sorted(sessions), [self._key("fixture"), self._key("spanning")])
        self.assertEqual(self.reader.read_keys, [self._key("fixture"), self._key("spanning")])
        old_unit = self.state.saved["units"][os.path.join(self.folder, "old.jsonl")]
        self.assertEqual((old_unit["state"], old_unit["sessionKey"]), ("inactive", self._key("old")))
        self.assertIn("fingerprint", old_unit)

    def test_session_entry_records_log_path_activity_prompt_time_and_versions(self):
        self._install("spanning", _transcript(LONG_AGO, ON_AT + ONE_DAY), ON_AT + ONE_DAY)
        self._sync()
        entry = self.state.saved["sessions"][self._key("spanning")]
        self.assertEqual(entry, {
            "logPath": "/logs/spanning.md", "state": "written", "projectDir": "/work/proj",
            "lastPromptAt": (ON_AT + ONE_DAY).isoformat(),
            "lastActivityAt": (ON_AT + ONE_DAY + timedelta(seconds=1)).isoformat(),
            "orphan": False, "renderVersion": RENDER_VERSION, "unitCount": 1})
        unit = self.state.saved["units"][os.path.join(self.folder, "spanning.jsonl")]
        self.assertEqual((unit["state"], unit["sessionKey"]), ("read", self._key("spanning")))
        self.assertEqual(self.state.saved["lastSyncAt"], NOW.isoformat())

    def test_report_and_log_are_rendered_through_archive(self):
        self._install("spanning", _transcript(LONG_AGO, ON_AT + ONE_DAY), ON_AT + ONE_DAY)
        self._sync()
        self.assertEqual(self.archive.writes, [("/logs/spanning.md", "log spanning: prompt 0|prompt 1", "report spanning (2 requests)")])

    def test_sync_while_off_does_nothing(self):
        self.state = MemoryState(on_at=None)
        self._install_fixture("fixture")
        outcome = self._sync()
        self.assertEqual((self.archive.writes, self.state.saves, self.reader.session_of_calls), ([], 0, 0))
        self.assertEqual(outcome.unsaved_reports, {})


class SyncSkipsUnchangedTest(SyncTestCase):
    def test_unchanged_fingerprint_session_not_read_again(self):
        for number in range(3):
            self._install_fixture("s%d" % number)
        self._install("old", _transcript(LONG_AGO), LONG_AGO)
        self._sync()
        self.assertEqual((self.reader.session_of_calls, len(self.reader.read_keys), len(self.archive.writes)), (4, 3, 3))
        self._sync()
        self.assertEqual((self.reader.session_of_calls, len(self.reader.read_keys), len(self.archive.writes)), (4, 3, 3))

    def test_changed_fingerprint_reads_only_that_session_again_and_keeps_its_log_path(self):
        self._install_fixture("steady")
        path = self._install_fixture("grows")
        self._sync()
        self.reader.read_keys.clear()
        self._append(path, _transcript(NOW))
        self._sync()
        self.assertEqual(self.reader.read_keys, [self._key("grows")])
        self.assertEqual(sorted(self.archive.new_paths), ["grows", "steady"])
        self.assertEqual(len(self.archive.writes), 3)
        self.assertEqual(self.archive.writes[-1], ("/logs/grows.md", "log grows: first line\nsecond line|queued prompt|third prompt|prompt 0", "report grows (4 requests)"))


class SyncProgressTest(SyncTestCase):
    def test_progress_saved_after_each_session(self):
        self._install_fixture("s1")
        self._install_fixture("s2")
        self.archive.crash_on_write = 2
        sessions_before_each_write = []
        self.archive.before_write = lambda: sessions_before_each_write.append(sorted(self.state.saved["sessions"]))
        with self.assertRaises(KeyboardInterrupt):
            self._sync()
        self.assertEqual(sessions_before_each_write[0], [])
        self.assertEqual(len(sessions_before_each_write[1]), 1)
        (finished,) = self.state.saved["sessions"]
        unfinished = ({self._key("s1"), self._key("s2")} - {finished}).pop()
        units = {os.path.basename(key): unit["state"] for key, unit in self.state.saved["units"].items()}
        unfinished_unit = unfinished[len(self._key("")):] + ".jsonl"
        self.assertEqual(units, {finished[len(self._key("")):] + ".jsonl": "read", unfinished_unit: "inactive"})
        self.assertNotIn("fingerprint", self.state.saved["units"][os.path.join(self.folder, unfinished_unit)])
        self.archive.crash_on_write = None
        self.reader.read_keys.clear()
        self._sync()
        self.assertEqual(self.reader.read_keys, [unfinished])
        self.assertEqual(sorted(self.state.saved["sessions"]), [self._key("s1"), self._key("s2")])


class SyncWriteFailureTest(SyncTestCase):
    def test_failed_first_write_leaves_units_unreadable_without_session_entry_and_hands_report_back(self):
        path = self._install_fixture("s1")
        self.archive.failure = WriteOutcome("/logs/s1.usage.txt", False, WRITE_FAILURE)
        outcome = self._sync()
        self.assertEqual(self.state.saved["sessions"], {})
        unit = self.state.saved["units"][path]
        self.assertEqual((unit["state"], unit["reason"]), ("unreadable", "/logs/s1.usage.txt: " + WRITE_FAILURE))
        self.assertNotIn("fingerprint", unit)
        self.assertEqual(outcome.unsaved_reports, {self._key("s1"): "report s1 (3 requests)"})
        self.archive.failure = None
        self.reader.read_keys.clear()
        self._sync()
        self.assertEqual(self.reader.read_keys, [self._key("s1")])
        self.assertEqual(self.state.saved["sessions"][self._key("s1")]["state"], "written")

    def test_failed_rewrite_of_archived_session_marks_it_skipped_and_retries_every_sync(self):
        path = self._install_fixture("s1")
        self._sync()
        self._append(path, _transcript(NOW))
        self.archive.failure = WriteOutcome("/logs/s1.md", False, WRITE_FAILURE)
        outcome = self._sync()
        entry = self.state.saved["sessions"][self._key("s1")]
        self.assertEqual((entry["state"], entry["reason"], entry["logPath"]), ("skipped", WRITE_FAILURE, "/logs/s1.md"))
        self.assertEqual(self.state.saved["units"][path]["state"], "read")
        self.assertEqual(list(outcome.unsaved_reports), [self._key("s1")])
        self.assertEqual(self.archive.new_paths, ["s1"])
        self.reader.read_keys.clear()
        self.archive.failure = None
        self._sync()
        self.assertEqual(self.reader.read_keys, [self._key("s1")])
        self.assertEqual(self.state.saved["sessions"][self._key("s1")]["state"], "written")

    def test_rewrite_failing_on_two_syncs_in_a_row_is_retried_and_handed_back_each_time(self):
        path = self._install_fixture("s1")
        self._sync()
        self._append(path, _transcript(NOW))
        self.archive.failure = WriteOutcome("/logs/s1.md", False, WRITE_FAILURE)
        for _ in range(2):
            self.reader.read_keys.clear()
            outcome = self._sync()
            self.assertEqual(self.reader.read_keys, [self._key("s1")])
            self.assertEqual(self.state.saved["sessions"][self._key("s1")]["state"], "skipped")
            self.assertEqual(list(outcome.unsaved_reports), [self._key("s1")])

    def test_skipped_session_is_rewritten_when_the_on_time_moved_past_its_last_activity(self):
        path = self._install_fixture("s1")
        self._sync()
        self._append(path, _transcript(NOW))
        self.archive.failure = WriteOutcome("/logs/s1.md", False, WRITE_FAILURE)
        self._sync()
        self.assertEqual(self.state.saved["sessions"][self._key("s1")]["state"], "skipped")
        self.state.flag = OnFlag(NOW + ONE_DAY)  # off, then on again later
        self.archive.failure = None
        self._sync()
        self.assertEqual(self.state.saved["sessions"][self._key("s1")]["state"], "written")
        self.assertEqual(self.archive.writes[-1][0], "/logs/s1.md")

    def test_exception_while_rendering_fails_only_that_session(self):
        self._install_fixture("s1")
        self._install_fixture("s2")
        real_render = self.archive.render_log

        def render(session):
            if session.session_id == "s1":
                raise ValueError("bad time zone")
            return real_render(session)

        self.archive.render_log = render
        self._sync()
        self.assertEqual(sorted(self.state.saved["sessions"]), [self._key("s2")])
        self.assertEqual(self.state.saved["units"][os.path.join(self.folder, "s1.jsonl")]["reason"], "bad time zone")


class SyncUnreadableTest(SyncTestCase):
    def test_unreadable_unit_is_retried_every_sync(self):
        path = os.path.join(self.folder, "broken.jsonl")
        with open(path, "wb") as handle:
            handle.write(b"\xff\xfe\x00bad")
        os.utime(path, (NOW.timestamp(), NOW.timestamp()))
        self._sync()
        unit = self.state.saved["units"][path]
        self.assertEqual(unit["state"], "unreadable")
        self.assertEqual(self.state.saved["sessions"], {})
        self.reader.read_keys.clear()
        self._sync()
        self.assertEqual(self.reader.read_keys, [self._key("broken")])

    def test_session_with_a_persistently_unreadable_unit_is_read_every_sync_but_rewritten_only_when_a_readable_unit_changed(self):
        main = self._install_fixture("s1")
        directory = os.path.join(self.folder, "s1", "subagents")
        os.makedirs(directory)
        with open(os.path.join(directory, "agent-a1.jsonl"), "wb") as handle:
            handle.write(b"\xff\xfe\x00bad")
        self._sync()
        self._sync()
        self.assertEqual((self.reader.read_keys, len(self.archive.writes)), ([self._key("s1")] * 2, 1))
        self._append(main, _transcript(NOW))
        self._sync()
        self.assertEqual((len(self.reader.read_keys), len(self.archive.writes)), (3, 2))


class SyncBoundaryTest(SyncTestCase):
    def test_session_touched_after_on_with_only_older_activity_is_read_and_marked_inactive(self):
        path = self._install("quiet", _transcript(LONG_AGO), NOW)
        self._sync()
        self.assertEqual(self.reader.read_keys, [self._key("quiet")])
        self.assertEqual((self.archive.writes, self.state.saved["sessions"]), ([], {}))
        self.assertEqual(self.state.saved["units"][path]["state"], "inactive")
        self._sync()
        self.assertEqual(self.reader.read_keys, [self._key("quiet")])

    def test_activity_exactly_at_on_time_is_archived_and_unit_modified_exactly_at_on_time_is_read(self):
        self._install("edge", _transcript(ON_AT - timedelta(seconds=1)), ON_AT)
        self._sync()
        self.assertEqual([log_path for log_path, _, _ in self.archive.writes], ["/logs/edge.md"])

    def test_only_sessions_before_on_still_save_inactive_marks_and_last_sync_time(self):
        path = self._install("old", _transcript(LONG_AGO), LONG_AGO)
        self._sync()
        self.assertEqual(self.state.saved["units"][path]["state"], "inactive")
        self.assertEqual(self.state.saved["lastSyncAt"], NOW.isoformat())
        self._sync()
        self.assertEqual((self.reader.session_of_calls, self.reader.read_keys), (1, []))

    def test_unreadable_unit_modified_before_on_is_still_retried(self):
        path = os.path.join(self.folder, "broken.jsonl")
        with open(path, "wb") as handle:
            handle.write(b"\xff\xfe\x00bad")
        os.utime(path, (NOW.timestamp(), NOW.timestamp()))
        self._sync()
        os.utime(path, (LONG_AGO.timestamp(), LONG_AGO.timestamp()))
        self.reader.read_keys.clear()
        self._sync()
        self.assertEqual(self.reader.read_keys, [self._key("broken")])


class SyncMultiUnitTest(SyncTestCase):
    def test_session_with_two_units_is_read_whole_and_one_changed_unit_reads_it_again(self):
        other = os.path.join(self.root, "projects", "-other")
        os.makedirs(other)
        first = self._install_fixture("shared")
        second = self._install_fixture("shared", other)
        both = tuple(sorted((first, second)))
        self._sync()
        self.assertEqual(self.reader.read_keys, [self._key("shared")])
        self.assertEqual(self.state.saved["sessions"][self._key("shared")]["unitCount"], 2)
        self._sync()
        self.assertEqual(self.reader.read_keys, [self._key("shared")])
        self._append(second, _transcript(NOW))
        self._sync()
        self.assertEqual(self.reader.read_keys, [self._key("shared")] * 2)
        self.assertEqual(self.reader.read_unit_keys, [both, both])
        self.assertEqual(self.state.saved["sessions"][self._key("shared")]["unitCount"], 2)


def _agent_transcript(agent: str, start: datetime, end: datetime) -> str:
    records = (
        {"type": "user", "isSidechain": True, "agentId": agent, "cwd": "/work/proj", "timestamp": _stamp(start),
         "message": {"role": "user", "content": "do the task"}},
        {"type": "assistant", "timestamp": _stamp(end),
         "message": {"id": "msg_" + agent, "model": "claude-x", "content": [{"type": "text", "text": "done"}],
                     "usage": {"input_tokens": 1, "output_tokens": 1}}})
    return "".join(json.dumps(record) + "\n" for record in records)


class RealArchiveTestCase(SyncTestCase):
    """Sync against the real file state store and the real writer under a temporary log root."""

    def setUp(self):
        super().setUp()
        self.logs = os.path.join(self.root, "prompt-logs")
        self.state = FileStateStore(self.logs)
        self.state.write_on_flag(OnFlag(ON_AT))
        self.archive = LogUsageWriter(self.logs, lambda moment: moment)

    def _sync(self):
        self.state = FileStateStore(self.logs)  # one store per run
        return super()._sync()

    def _install_agent(self, sid, agent, text, modified_at):
        directory = os.path.join(self.folder, sid, "subagents")
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "agent-%s.jsonl" % agent)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.utime(path, (modified_at.timestamp(), modified_at.timestamp()))
        return path

    def _archived(self):
        """Every archived file under the log root: relative path -> bytes."""
        found = {}
        for folder, _, names in os.walk(self.logs):
            for name in names:
                if name.startswith("session_"):
                    path = os.path.join(folder, name)
                    with open(path, "rb") as handle:
                        found[os.path.relpath(path, self.logs)] = handle.read()
        return found

    def _text_of(self, archived, sid, suffix):
        names = [name for name in archived if name.endswith("_%s%s" % (sid, suffix))]
        self.assertEqual(len(names), 1, "archived files of %s: %s" % (sid, sorted(archived)))
        return archived[names[0]].decode("utf-8")

    def _files_outside_the_log_root(self):
        found = set()
        for folder, _, names in os.walk(self.root):
            if os.path.commonpath((folder, self.logs)) != self.logs:
                found.update(os.path.join(folder, name) for name in names)
        return found


class SyncOnTimeSessionsTest(RealArchiveTestCase):
    def test_sessions_before_on_skipped_started_before_but_active_after_archived_whole_second_run_identical(self):
        after = ON_AT + ONE_DAY
        self._install("before", _transcript(LONG_AGO), LONG_AGO)
        self._install("after", _transcript(after), after)
        self._install("spanning", _transcript(LONG_AGO, after), after)
        self._install("withagent", _transcript(after), after)
        self._install_agent("withagent", "old1", _agent_transcript("old1", LONG_AGO, LONG_AGO + timedelta(seconds=5)), LONG_AGO)
        self.assertFalse(os.path.exists(os.path.join(self.logs, ".sync.json")))
        self._sync()
        archived = self._archived()
        self.assertEqual(len(archived), ARCHIVED_SESSIONS * FILES_PER_SESSION)
        self.assertNotIn("before", " ".join(archived))
        self.assertIn("prompt 0", self._text_of(archived, "spanning", ".md"))
        self.assertIn("prompt 1", self._text_of(archived, "spanning", ".md"))
        self.assertIn("agent-old1", self._text_of(archived, "withagent", ".usage.txt"))
        self.assertEqual(self.reader.read_keys, [self._key("after"), self._key("spanning"), self._key("withagent")])
        main_path = os.path.join(self.folder, "withagent.jsonl")
        agent_path = os.path.join(self.folder, "withagent", "subagents", "agent-old1.jsonl")
        self.assertEqual(self.reader.read_unit_keys[self.reader.read_keys.index(self._key("withagent"))], tuple(sorted((main_path, agent_path))))
        self._sync()
        self.assertEqual(self._archived(), archived)
        self.assertEqual(len(self.reader.read_keys), 3)
        os.unlink(os.path.join(self.logs, ".sync.json"))  # nothing known any more: every active session is rendered again
        self._sync()
        self.assertEqual(len(self.reader.read_keys), 6)
        self.assertEqual(self._archived(), archived)

    def test_session_whose_only_new_activity_is_in_a_subagent_file_is_archived_whole(self):
        after = ON_AT + ONE_DAY
        self._install("quietmain", _transcript(LONG_AGO), LONG_AGO)
        self._install_agent("quietmain", "late1", _agent_transcript("late1", after, after + timedelta(seconds=5)), after)
        self._sync()
        archived = self._archived()
        self.assertIn("prompt 0", self._text_of(archived, "quietmain", ".md"))
        self.assertIn("agent-late1", self._text_of(archived, "quietmain", ".usage.txt"))
        self.assertEqual(self.state.load_sync()["sessions"][self._key("quietmain")]["unitCount"], 2)

    def test_archived_session_is_read_again_when_another_subagent_file_appears(self):
        after = ON_AT + ONE_DAY
        self._install("withagent", _transcript(after), after)
        self._install_agent("withagent", "a1", _agent_transcript("a1", after, after + timedelta(seconds=5)), after)
        self._sync()
        self.assertNotIn("agent-a2", self._text_of(self._archived(), "withagent", ".usage.txt"))
        self._install_agent("withagent", "a2", _agent_transcript("a2", after, after + timedelta(seconds=7)), after)
        self.reader.read_keys.clear()
        self._sync()
        self.assertEqual(self.reader.read_keys, [self._key("withagent")])
        report = self._text_of(self._archived(), "withagent", ".usage.txt")
        self.assertIn("agent-a2", report)
        self.assertIn("TOTAL (1 requests, 2 sub-agents)", report)
        self.assertEqual(self.state.load_sync()["sessions"][self._key("withagent")]["unitCount"], 3)

    def test_subagent_file_touched_after_on_with_only_older_records_is_read_and_nothing_archived(self):
        self._install("oldrecords", _transcript(LONG_AGO), LONG_AGO)
        agent = self._install_agent("oldrecords", "o1", _agent_transcript("o1", LONG_AGO, LONG_AGO + timedelta(seconds=5)), NOW)
        self._sync()
        self.assertEqual((self.reader.read_keys, self._archived()), ([self._key("oldrecords")], {}))
        self.assertEqual(self.state.load_sync()["units"][agent]["state"], "inactive")


class SyncOneMoreTurnTest(RealArchiveTestCase):
    def test_claude_file_with_one_more_turn_read_again_and_log_updated(self):
        self._install("grows", _transcript(NOW), NOW)
        self._sync()
        before = self._archived()
        self.assertNotIn("prompt 1", self._text_of(before, "grows", ".md"))
        self.assertIn("TOTAL (1 requests, 0 sub-agents)", self._text_of(before, "grows", ".usage.txt"))
        self._install("grows", _transcript(NOW, NOW + timedelta(minutes=1)), NOW)
        self.reader.read_keys.clear()
        self._sync()
        after = self._archived()
        self.assertEqual(self.reader.read_keys, [self._key("grows")])
        self.assertEqual(sorted(after), sorted(before))
        self.assertIn("prompt 1", self._text_of(after, "grows", ".md"))
        self.assertIn("TOTAL (2 requests, 0 sub-agents)", self._text_of(after, "grows", ".usage.txt"))


class SyncUnreadableSubAgentTest(RealArchiveTestCase):
    def test_unreadable_subagent_file_is_retried_every_sync_and_its_row_appears_once_it_reads(self):
        self._install("withagent", _transcript(NOW), NOW)
        agent = self._install_agent("withagent", "a1", "", NOW)
        with open(agent, "wb") as handle:
            handle.write(b"\xff\xfe\x00bad")
        self._sync()
        self.assertEqual(self.state.load_sync()["units"][agent]["state"], "unreadable")
        self.assertEqual(self.state.load_sync()["sessions"][self._key("withagent")]["unitCount"], 1)
        self.assertNotIn("agent-a1", self._text_of(self._archived(), "withagent", ".usage.txt"))
        self._sync()
        self.assertEqual(self.reader.read_keys, [self._key("withagent")] * 2)
        self._install_agent("withagent", "a1", _agent_transcript("a1", NOW, NOW + timedelta(seconds=5)), NOW)
        self._sync()
        self.assertEqual(self.reader.read_keys, [self._key("withagent")] * 3)
        self.assertEqual(self.state.load_sync()["units"][agent]["state"], "read")
        self.assertEqual(self.state.load_sync()["sessions"][self._key("withagent")]["unitCount"], 2)
        self.assertIn("agent-a1", self._text_of(self._archived(), "withagent", ".usage.txt"))
        self._sync()
        self.assertEqual(len(self.reader.read_keys), 3)


class SyncLearnedOnceTest(SyncTestCase):
    def test_200_unchanged_sessions_not_read_and_known_units_not_learned_twice(self):
        paths = [self._install("s%03d" % number, _transcript(NOW), NOW) for number in range(SESSION_COUNT)]
        self._sync()
        self.assertEqual((self.reader.session_of_calls, len(self.reader.read_keys), len(self.archive.writes)),
                         (SESSION_COUNT, SESSION_COUNT, SESSION_COUNT))
        self._append(paths[7], _transcript(NOW + timedelta(minutes=1)))
        self._install("new", _transcript(NOW), NOW)
        self.reader.read_keys.clear()
        self._sync()
        self.assertEqual((self.reader.session_of_calls, self.reader.read_keys, len(self.archive.writes)),
                         (SESSION_COUNT + 1, [self._key("new"), self._key("s007")], SESSION_COUNT + 2))
        self.assertEqual(self.archive.writes[-1][0], "/logs/s007.md")

    def test_new_unit_session_learned_once_and_kept_when_the_run_is_killed_before_the_first_session_finishes(self):
        paths = [self._install("s%d" % number, _transcript(NOW), NOW) for number in range(3)]
        self.archive.crash_on_write = 1
        with self.assertRaises(KeyboardInterrupt):
            self._sync()
        self.assertEqual(self.reader.session_of_calls, 3)
        for number, path in enumerate(paths):
            self.assertEqual(self.state.saved["units"][path], {"sessionKey": self._key("s%d" % number), "state": "inactive"})
        self.archive.crash_on_write = None
        self._sync()
        self.assertEqual(self.reader.session_of_calls, 3)
        self.assertEqual(sorted(self.state.saved["sessions"]), [self._key("s%d" % number) for number in range(3)])

    def test_learned_unit_without_fingerprint_survives_the_file_state_store_between_runs(self):
        logs = os.path.join(self.root, "prompt-logs")
        self._install("s0", _transcript(NOW), NOW)
        self._install("s1", _transcript(NOW), NOW)
        self.state = FileStateStore(logs)
        self.state.write_on_flag(OnFlag(ON_AT))
        self.archive.crash_on_write = 1
        with self.assertRaises(KeyboardInterrupt):
            self._sync()
        self.assertEqual(self.reader.session_of_calls, 2)
        self.state = FileStateStore(logs)
        self.archive.crash_on_write = None
        self._sync()
        self.assertEqual(self.reader.session_of_calls, 2)
        self.assertEqual(sorted(self.state.load_sync()["sessions"]), [self._key("s0"), self._key("s1")])


class SyncUnsafeIdTest(RealArchiveTestCase):
    def test_unsafe_session_id_not_archived_status_lists_it_subagent_row_and_tokens_stay(self):
        unsafe = self._install("a..b", _transcript(NOW), NOW)
        device = self._install("CON", _transcript(NOW), NOW)
        orphan = self._install_agent("c..d", "x1", _agent_transcript("x1", NOW, NOW + timedelta(seconds=5)), NOW)
        self._install("fine", _transcript(NOW), NOW)
        self._install_agent("fine", "x..y", _agent_transcript("x..y", NOW, NOW + timedelta(seconds=5)), NOW)
        outside = self._files_outside_the_log_root()
        state = FileStateStore(self.logs)
        sync = SyncUseCase(self.reader, self.archive, state, PricingRules(_NoPrices()), lambda: NOW)
        status = CommandUseCases(sync, self.archive, state, _NoPrices(), lambda: NOW, self.logs).status()
        self.assertEqual(status.unreadable, (UnreadableItem(device, UNSAFE_ID_REASON), UnreadableItem(unsafe, UNSAFE_ID_REASON),
                                             UnreadableItem(orphan, UNSAFE_ID_REASON)))
        self.assertEqual(status.archived_session_count, 1)
        units = state.load_sync()["units"]
        self.assertEqual(units[unsafe], {"sessionKey": self._key("a..b"), "state": "unreadable", "reason": UNSAFE_ID_REASON})
        self.assertEqual(units[orphan], {"sessionKey": self._key("c..d"), "state": "unreadable", "reason": UNSAFE_ID_REASON})
        self.assertEqual(self.reader.read_keys, [self._key("fine")])
        self.assertEqual(self._files_outside_the_log_root(), outside)
        archived = self._archived()
        self.assertEqual(sorted(name.rsplit("_", 1)[1] for name in archived), ["fine.md", "fine.usage.txt"])
        report = self._text_of(archived, "fine", ".usage.txt")
        self.assertIn("agent-x..y", report)
        self.assertIn("TOTAL (1 requests, 1 sub-agents)", report)
        self.assertIn("est. used token: input: 2, output: 2, cache_create: 0, cache_read: 0, total_tokens: 4", report)
        self.reader.read_keys.clear()
        self._sync()
        self.assertEqual(self.reader.read_keys, [])
        self.assertEqual(self.state.load_sync()["units"][unsafe]["reason"], UNSAFE_ID_REASON)
        self.assertEqual(self.reader.session_of_calls, 5)  # every unit learned once, the unsafe ones included


class StubReader:
    """A reader port that returns canned answers for two units of one session."""

    def __init__(self, units, session_keys, result):
        self._units, self._session_keys, self._result = units, session_keys, result
        self.session_of_calls = 0
        self.read_keys = []
        self.read_units = []

    def scan(self):
        return ScanResult(units=self._units)

    def session_of(self, unit):
        self.session_of_calls += 1
        return self._session_keys[unit.key]

    def read(self, session_key, units):
        self.read_keys.append(session_key)
        self.read_units.append(tuple(unit.key for unit in units))
        return self._result


class SyncReaderAnswersTest(SyncTestCase):
    def _unit(self, key):
        return SourceUnit(key=key, fingerprint="fp-" + key, modified_at=NOW)

    def _run(self, reader):
        SyncUseCase(reader, self.archive, self.state, PricingRules(_NoPrices()), lambda: NOW).sync()

    def test_unit_whose_session_cannot_be_learned_is_recorded_unreadable(self):
        self._run(StubReader((self._unit("a"),), {"a": None}, ReadResult()))
        self.assertEqual(self.state.saved["units"]["a"], {"state": "unreadable", "reason": "session cannot be determined"})

    def test_unit_whose_session_was_not_learned_is_asked_again_and_the_entry_replaced_once_learned(self):
        keys = {"a": None}
        reader = StubReader((self._unit("a"),), keys, ReadResult())
        self._run(reader)
        keys["a"] = self._key("fx")
        self._run(reader)
        self._run(reader)
        self.assertEqual(reader.read_keys, [self._key("fx")])
        self.assertEqual(reader.session_of_calls, 2)
        self.assertEqual(self.state.saved["units"]["a"], {"sessionKey": self._key("fx"), "state": "inactive", "fingerprint": "fp-a"})

    def test_unit_learned_late_with_an_old_modification_time_still_has_its_session_read_again_with_it(self):
        keys = {"a": self._key("fx"), "b": None}
        old = SourceUnit(key="b", fingerprint="fp-b", modified_at=LONG_AGO)
        reader = StubReader((self._unit("a"), old), keys, ReadResult(session=self._session_of_fixture()))
        self._run(reader)
        keys["b"] = self._key("fx")
        self._run(reader)
        self._run(reader)
        self.assertEqual(reader.read_units, [("a",), ("a", "b")])
        self.assertEqual(self.state.saved["sessions"][self._key("fx")]["unitCount"], 2)

    def test_session_keys_with_an_unsafe_root_id_are_unreadable_and_never_read(self):
        unsafe = {"a": "claude:CON", "b": "claude:" + "a" * (MAX_ID_LENGTH + 1), "c": "claude:", "d": "claude:a/b", "e": "claude:..",
                  "f": "claude:nul"}
        reader = StubReader(tuple(self._unit(name) for name in sorted(unsafe)) + (self._unit("ok"),), {**unsafe, "ok": "claude:ok"}, ReadResult())
        self._run(reader)
        for name, session_key in unsafe.items():
            self.assertEqual(self.state.saved["units"][name], {"sessionKey": session_key, "state": "unreadable", "reason": UNSAFE_ID_REASON})
        self.assertEqual(reader.read_keys, ["claude:ok"])
        self.assertEqual((self.archive.writes, self.state.saved["sessions"]), ([], {}))

    def test_unreadable_unit_of_a_built_session_is_retried_while_its_sibling_is_read(self):
        session = self._session_of_fixture()
        result = ReadResult(session=session, unreadable=(UnreadableSource(Harness.CLAUDE, "b", "cannot be opened"),))
        key = self._key("fx")
        self._run(StubReader((self._unit("a"), self._unit("b")), {"a": key, "b": key}, result))
        units = self.state.saved["units"]
        self.assertEqual((units["a"]["state"], units["b"]), ("read", {"sessionKey": key, "state": "unreadable", "reason": "cannot be opened"}))
        self.assertEqual(self.state.saved["sessions"][key]["unitCount"], 1)

    def test_session_not_built_and_nothing_unreadable_marks_every_unit_inactive(self):
        key = self._key("fx")
        self._run(StubReader((self._unit("a"), self._unit("b")), {"a": key, "b": key}, ReadResult()))
        units = self.state.saved["units"]
        self.assertEqual({units["a"]["state"], units["b"]["state"]}, {"inactive"})
        self.assertEqual(self.state.saved["sessions"], {})

    def test_session_not_built_marks_every_unit_unreadable_with_the_named_unit_reason(self):
        result = ReadResult(unreadable=(UnreadableSource(Harness.CLAUDE, "b", "cannot be decoded"),))
        key = self._key("fx")
        self._run(StubReader((self._unit("a"), self._unit("b")), {"a": key, "b": key}, result))
        units = self.state.saved["units"]
        self.assertEqual((units["a"]["reason"], units["b"]["reason"]), ("cannot be decoded", "cannot be decoded"))
        self.assertEqual({units["a"]["state"], units["b"]["state"]}, {"unreadable"})

    def _session_of_fixture(self):
        self._install_fixture("fx")
        (unit,) = self.reader.scan().units
        return self.reader.read(self.reader.session_of(unit), (unit,)).session


class SyncFileStateTest(SyncTestCase):
    def test_sync_runs_against_the_real_state_store_and_second_run_reads_nothing(self):
        logs = os.path.join(self.root, "prompt-logs")
        self.state = FileStateStore(logs)
        self.state.write_on_flag(OnFlag(ON_AT))
        self._install_fixture("s1")
        self._install_fixture("s2")
        self._sync()
        self.assertEqual(self.reader.read_keys, [self._key("s1"), self._key("s2")])
        self.state = FileStateStore(logs)
        self._sync()
        self.assertEqual(self.reader.read_keys, [self._key("s1"), self._key("s2")])
        self.assertEqual(sorted(self.state.load_sync()["sessions"]), [self._key("s1"), self._key("s2")])
