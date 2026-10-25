"""Sync robustness over a real FileStateStore and writer: lone surrogates in stored text, a log root that moved, the cost of saving
`.sync.json`, units whose source vanished, one session that raises, and the temp a killed save leaves behind."""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sessionlog.log_usage_writer import LogUsageWriter
from sessionlog.model import Harness
from sessionlog.pricing import PricingRules
from sessionlog.reader_port import UnreadableSource
from sessionlog.state import FileStateStore
from sessionlog.state_port import OnFlag
from sessionlog.sync import CHECKPOINT_FRACTION, SyncUseCase
from tests.test_sync import FakeArchive, NOW, ON_AT, PROJECT, CountingReader, _NoPrices, _transcript

PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MINUTE = timedelta(minutes=1)
SYNC_FILE = ".sync.json"
# A save that is killed the moment it would rename its temp over `.sync.json`, as a `claude -p` teardown kills a running sync.
KILLED_SAVE = """
import os, signal, sys
sys.path.insert(0, sys.argv[1])
from sessionlog.state import FileStateStore
os.replace = lambda *args: os.kill(os.getpid(), signal.SIGKILL)
FileStateStore(sys.argv[2]).save_sync({"units": {}, "sessions": {}})
"""

LONE_SURROGATE_CWD = "/work/p\ud800q"
INVALID_BYTE_NAME = "/store/\udcff.jsonl"  # what `os.fsdecode` makes of a file name that is not UTF-8
VALID_TEXT = "/work/\u00e9\U0001f600"
STALE_AGE = timedelta(hours=2)
FRESH_AGE = timedelta(minutes=5)
MANY_SESSIONS = 600  # more entries than the first save alone is allowed to leave unsaved
KILLED_AT_WRITE = MANY_SESSIONS - 20
UNREADABLE = "unreadable"
READ_DEFECT = "reader defect"


def _transcript_in(cwd, *prompt_times):
    """`_transcript` for a session whose cwd is `cwd`; `json.dumps` writes a lone surrogate as an escape, as Node does."""
    return _transcript(*prompt_times).replace('"/work/proj"', json.dumps(cwd))


class HomeTestCase(unittest.TestCase):
    """A harness root `home` with its `prompt-logs`, built the way one run builds them; the root may be renamed."""

    def setUp(self):
        self.parent = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.parent, True)
        self._use(os.path.join(self.parent, "home"))
        os.makedirs(self.folder)
        FileStateStore(self.logs).write_on_flag(OnFlag(ON_AT))

    def _use(self, home):
        self.home = home
        self.logs = os.path.join(home, "prompt-logs")
        self.folder = os.path.join(home, "projects", PROJECT)

    def _install(self, sid, text, modified_at=NOW):
        path = os.path.join(self.folder, sid + ".jsonl")
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.utime(path, (modified_at.timestamp(), modified_at.timestamp()))
        return path

    def _key(self, sid):
        return "claude:" + sid

    def _sync(self, reader=None, archive=None):
        state = FileStateStore(self.logs)
        archive = archive or LogUsageWriter(self.logs, lambda moment: moment)
        SyncUseCase(reader or CountingReader(self.home), archive, state, PricingRules(_NoPrices()), lambda: NOW).sync()

    def _saved(self):
        return FileStateStore(self.logs).load_sync()

    def _archived(self):
        found = {}
        for folder, _, names in os.walk(self.logs):
            for name in names:
                if name.startswith("session_"):
                    with open(os.path.join(folder, name), "rb") as handle:
                        found[os.path.relpath(os.path.join(folder, name), self.logs)] = handle.read()
        return found


class LoneSurrogateTest(HomeTestCase):
    def test_a_session_with_a_lone_surrogate_cwd_is_archived_and_the_sessions_after_it_too(self):
        self._install("a", _transcript(NOW))
        self._install("b", _transcript_in(LONE_SURROGATE_CWD, NOW))
        self._install("c", _transcript(NOW))
        self._sync()
        saved = self._saved()
        self.assertEqual(sorted(saved["sessions"]), [self._key("a"), self._key("b"), self._key("c")])
        self.assertEqual(saved["sessions"][self._key("b")]["projectDir"], LONE_SURROGATE_CWD)
        archived = self._archived()
        self.assertEqual(len([name for name in archived if name.endswith(".md")]), 3)
        self.assertEqual(len([name for name in archived if name.endswith(".usage.txt")]), 3)
        self._sync()
        self.assertEqual(self._archived(), archived)  # a second sync changes nothing

    def test_the_state_keeps_lone_surrogates_and_valid_text_exactly_and_stays_valid_utf8(self):
        entry = {"logPath": "/logs/x.md", "state": "written", "projectDir": LONE_SURROGATE_CWD, "orphan": False,
                 "lastActivityAt": NOW.isoformat(), "renderVersion": 1, "unitCount": 1}
        state = {"units": {INVALID_BYTE_NAME: {"state": UNREADABLE, "reason": "bad \udcff name"},
                           VALID_TEXT: {"state": "inactive", "sessionKey": "claude:v"}},
                 "sessions": {"claude:x": entry, "claude:v": dict(entry, projectDir=VALID_TEXT)}}
        store = FileStateStore(self.logs)
        store.save_sync(state)
        with open(os.path.join(self.logs, SYNC_FILE), "rb") as handle:
            text = handle.read().decode("utf-8")  # raises if the file is not valid UTF-8
        self.assertIn(VALID_TEXT, text)  # valid text stays readable, it is not turned into escapes
        loaded = FileStateStore(self.logs).load_sync()
        self.assertEqual(loaded["units"], state["units"])
        self.assertEqual(loaded["sessions"], state["sessions"])
        self.assertFalse(FileStateStore(self.logs).sync_reset)

    def test_the_writer_renders_and_writes_a_session_whose_text_holds_lone_surrogates(self):
        self._install("a", _transcript_in(LONE_SURROGATE_CWD, NOW).replace("prompt 0", "prompt \\ud800 0"))
        self._sync()
        archived = self._archived()
        self.assertEqual(len(archived), 2)
        for name, content in archived.items():
            self.assertTrue(name.isascii(), name)
            content.decode("utf-8")
        log = next(content for name, content in archived.items() if name.endswith(".md")).decode("utf-8")
        self.assertIn("prompt ? 0", log)  # one lost character, not a failed sync


class MovedLogRootTest(HomeTestCase):
    def _archived_logs(self):
        return sorted(name for name in self._archived() if name.endswith(".md"))

    def test_after_the_root_moves_a_rewrite_goes_into_the_moved_log_and_no_old_tree_is_recreated(self):
        self._install("s1", _transcript(NOW))
        self._sync()
        (log_name,) = self._archived_logs()
        old_home = self.home
        self._use(old_home + "-moved")
        os.rename(old_home, self.home)
        self._install("s1", _transcript(NOW, NOW + MINUTE), NOW + MINUTE)
        self._sync()
        self.assertFalse(os.path.exists(old_home))
        self.assertEqual(self._archived_logs(), [log_name])  # the same log, not a second one
        with open(os.path.join(self.logs, log_name), encoding="utf-8") as handle:
            self.assertIn("prompt 1", handle.read())
        log_path = self._saved()["sessions"][self._key("s1")]["logPath"]
        self.assertEqual(log_path, os.path.join(self.logs, log_name))

    def test_after_the_root_moves_the_saved_report_is_read_from_the_moved_logs_without_any_sync(self):
        self._install("s1", _transcript(NOW))
        self._sync()
        old_home = self.home
        self._use(old_home + "-moved")
        os.rename(old_home, self.home)
        log_path = self._saved()["sessions"][self._key("s1")]["logPath"]
        self.assertTrue(log_path.startswith(self.logs + os.sep), log_path)
        report = LogUsageWriter(self.logs, lambda moment: moment).read_report(log_path)
        self.assertIn("TOTAL (1 requests, 0 sub-agents)", report)

    def test_a_root_that_did_not_move_keeps_every_recorded_path_as_it_is(self):
        self._install("s1", _transcript(NOW))
        self._sync()
        before = self._saved()["sessions"][self._key("s1")]["logPath"]
        self._sync()
        self.assertEqual(self._saved()["sessions"][self._key("s1")]["logPath"], before)


class _CountingReplace:
    """An `os.replace` that counts the replacements of `.sync.json`."""

    def __init__(self, real_replace):
        self._real_replace = real_replace
        self.saves = 0

    def __call__(self, source, target, *args, **kwargs):
        if str(target).endswith(SYNC_FILE):
            self.saves += 1
        return self._real_replace(source, target, *args, **kwargs)


class SaveCostTest(HomeTestCase):
    def _sync_counting_saves(self, archive=None):
        counter = _CountingReplace(os.replace)
        with patch("os.replace", counter):
            self._sync(archive=archive or FakeArchive())
        return counter.saves

    def test_a_small_state_is_saved_after_every_session(self):
        for number in range(3):
            self._install("s%d" % number, _transcript(NOW))
        self.assertEqual(self._sync_counting_saves(), 1 + 3)  # one save before any session is read, one per session

    def test_the_first_sync_of_many_sessions_saves_far_less_often_than_once_per_session_and_loses_nothing(self):
        for number in range(MANY_SESSIONS):
            self._install("s%04d" % number, _transcript(NOW))
        self.assertLess(self._sync_counting_saves(), MANY_SESSIONS // 10)
        saved = self._saved()
        self.assertEqual(len(saved["sessions"]), MANY_SESSIONS)
        self.assertEqual({unit["state"] for unit in saved["units"].values()}, {"read"})

    def test_a_run_killed_in_a_large_store_has_saved_all_sessions_but_one_batch(self):
        for number in range(MANY_SESSIONS):
            self._install("s%04d" % number, _transcript(NOW))
        archive = FakeArchive()
        archive.crash_on_write = KILLED_AT_WRITE
        with self.assertRaises(KeyboardInterrupt):
            self._sync(archive=archive)
        entries = len(self._saved()["units"]) + len(self._saved()["sessions"])
        batch = entries // CHECKPOINT_FRACTION + 1
        self.assertGreaterEqual(len(self._saved()["sessions"]), KILLED_AT_WRITE - 1 - batch)
        archive.crash_on_write = None
        self._sync(archive=archive)
        self.assertEqual(len(self._saved()["sessions"]), MANY_SESSIONS)


class RecordedRootTest(unittest.TestCase):
    """What a store does with the log root `.sync.json` records, apart from any sync."""

    def setUp(self):
        self.parent = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.parent, True)
        self.old, self.new = os.path.join(self.parent, "old"), os.path.join(self.parent, "new")
        os.mkdir(self.old)

    def _entry(self, log_path):
        return {"logPath": log_path, "state": "written", "orphan": False, "lastActivityAt": NOW.isoformat(),
                "renderVersion": 1, "unitCount": 1}

    def _moved_state(self, paths):
        """The state a store at `old` saved with a session per path in `paths`, as a store at `new` loads it after the rename."""
        FileStateStore(self.old).save_sync({"units": {}, "sessions": {name: self._entry(path) for name, path in paths.items()}})
        os.rename(self.old, self.new)
        return FileStateStore(self.new).load_sync()["sessions"]

    def test_only_the_paths_recorded_under_the_old_root_follow_it(self):
        sessions = self._moved_state({
            "inside": os.path.join(self.old, "slug", "a.md"),
            "sibling": self.old + "-other" + os.sep + "slug" + os.sep + "b.md",  # begins with the old root's text, not inside it
            "elsewhere": os.path.join(self.parent, "unrelated", "c.md")})
        self.assertEqual({name: entry["logPath"] for name, entry in sessions.items()}, {
            "inside": os.path.join(self.new, "slug", "a.md"),
            "sibling": self.old + "-other" + os.sep + "slug" + os.sep + "b.md",
            "elsewhere": os.path.join(self.parent, "unrelated", "c.md")})

    def test_a_root_spelled_another_way_is_the_same_root_and_moves_nothing(self):
        FileStateStore(self.old).save_sync({"units": {}, "sessions": {"s": self._entry(os.path.join(self.old, "slug", "a.md"))}})
        spelled = os.path.join(self.old, "..", "old") + os.sep
        self.assertEqual(FileStateStore(spelled).load_sync()["sessions"]["s"]["logPath"], os.path.join(self.old, "slug", "a.md"))

    def test_the_recorded_root_never_reaches_the_state_a_caller_sees_and_a_save_records_the_current_root(self):
        store = FileStateStore(self.old)
        store.save_sync({"units": {}, "sessions": {}})
        self.assertEqual(set(store.load_sync()), {"units", "sessions"})
        os.rename(self.old, self.new)
        moved = FileStateStore(self.new)
        moved.save_sync(moved.load_sync())
        with open(os.path.join(self.new, SYNC_FILE), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["logRoot"], self.new)

    def test_a_recorded_root_that_is_not_text_makes_the_file_corrupt(self):
        os.rename(self.old, self.new)
        with open(os.path.join(self.new, SYNC_FILE), "w", encoding="utf-8") as handle:
            json.dump({"units": {}, "sessions": {}, "logRoot": 7}, handle)
        store = FileStateStore(self.new)
        self.assertEqual(store.load_sync(), {"units": {}, "sessions": {}})
        self.assertTrue(store.sync_reset)


class VanishedUnitTest(HomeTestCase):
    def test_units_whose_source_is_gone_are_dropped_and_their_archived_sessions_stay(self):
        keep = self._install("keep", _transcript(NOW))
        gone = self._install("gone", _transcript(NOW))
        self._sync()
        self.assertEqual(sorted(self._saved()["units"]), sorted((keep, gone)))
        os.unlink(gone)
        self._sync()
        saved = self._saved()
        self.assertEqual(sorted(saved["units"]), [keep])
        self.assertEqual(sorted(saved["sessions"]), [self._key("gone"), self._key("keep")])

    def test_an_incomplete_scan_drops_nothing(self):
        keep = self._install("keep", _transcript(NOW))
        gone = self._install("gone", _transcript(NOW))
        self._sync()
        os.unlink(gone)

        class IncompleteScanReader(CountingReader):
            def scan(self):
                return replace(super().scan(), unreadable=(UnreadableSource(Harness.CLAUDE, self._projects, "cannot be listed"),))

        self._sync(reader=IncompleteScanReader(self.home))
        self.assertEqual(sorted(self._saved()["units"]), sorted((keep, gone)))
        self._sync()
        self.assertEqual(sorted(self._saved()["units"]), [keep])

    def test_a_unit_another_run_changed_meanwhile_is_not_dropped_by_this_runs_save(self):
        store = FileStateStore(self.logs)
        store.save_sync({"units": {"a": {"state": "read", "fingerprint": "1"}, "b": {"state": "read", "fingerprint": "1"}},
                         "sessions": {}})
        mine = store.load_sync()
        rival = FileStateStore(self.logs)
        rival_state = rival.load_sync()
        rival_state["units"]["b"]["fingerprint"] = "2"
        rival.save_sync(rival_state)
        del mine["units"]["a"], mine["units"]["b"]
        store.save_sync(mine)
        self.assertEqual(self._saved()["units"], {"b": {"state": "read", "fingerprint": "2"}})


class IsolationTest(HomeTestCase):
    def _three_sessions(self):
        for sid in ("s1", "s2", "s3"):
            self._install(sid, _transcript(NOW))

    def _sessions_and_unreadable(self):
        saved = self._saved()
        return (sorted(saved["sessions"]),
                {key: unit["reason"] for key, unit in saved["units"].items() if unit["state"] == UNREADABLE})

    def test_a_session_whose_read_raises_is_recorded_unreadable_and_the_others_are_archived(self):
        self._three_sessions()

        class Defective(CountingReader):
            def read(self, session_key, units):
                if session_key.endswith("s2"):
                    raise RecursionError(READ_DEFECT)
                return super().read(session_key, units)

        self._sync(reader=Defective(self.home))
        broken = os.path.join(self.folder, "s2.jsonl")
        self.assertEqual(self._sessions_and_unreadable(), ([self._key("s1"), self._key("s3")], {broken: READ_DEFECT}))
        self._sync()  # the defect is gone: the unreadable unit is retried and the session archived
        self.assertEqual(self._sessions_and_unreadable(), ([self._key(sid) for sid in ("s1", "s2", "s3")], {}))

    def test_a_session_whose_activity_time_cannot_be_compared_is_recorded_unreadable_and_the_others_are_archived(self):
        self._three_sessions()
        naive = datetime(2026, 10, 22, 12, 0)

        class NaiveActivity(CountingReader):
            def read(self, session_key, units):
                result = super().read(session_key, units)
                if session_key.endswith("s2"):
                    object.__setattr__(result.session.prompts[0], "submitted_at", naive)  # a reader that skipped the model's checks
                return result

        self._sync(reader=NaiveActivity(self.home))
        broken = os.path.join(self.folder, "s2.jsonl")
        saved, unreadable = self._sessions_and_unreadable()
        self.assertEqual((saved, list(unreadable)), ([self._key("s1"), self._key("s3")], [broken]))
        self.assertIn("naive and offset-aware", unreadable[broken])

    def test_an_interrupt_still_stops_the_sync(self):
        self._three_sessions()

        class Interrupted(CountingReader):
            def read(self, session_key, units):
                raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            self._sync(reader=Interrupted(self.home))


class StaleStateTempTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.clock = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)

    def _plant(self, name, age):
        path = os.path.join(self.root, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{")
        moment = (self.clock - age).timestamp()
        os.utime(path, (moment, moment))
        return path

    def _names(self):
        return sorted(os.listdir(self.root))

    def test_a_save_removes_a_state_temp_left_by_a_killed_save_once_it_is_stale_and_nothing_else(self):
        self._plant(".sync-abcd1234.tmp", STALE_AGE)
        self._plant(".sync-wxyz9876.tmp", FRESH_AGE)  # another run's save in progress
        self._plant(".sync-notes.txt", STALE_AGE)
        self._plant("keep.tmp", STALE_AGE)
        FileStateStore(self.root, lambda: self.clock).save_sync({"units": {}, "sessions": {}})
        self.assertEqual(self._names(), [".sync-notes.txt", ".sync-wxyz9876.tmp", ".sync.json", ".sync.lock", "keep.tmp"])

    @unittest.skipIf(os.name == "nt", "SIGKILL")
    def test_a_save_killed_before_its_rename_leaves_a_temp_that_the_next_save_removes_once_it_is_an_hour_old(self):
        killed = subprocess.run([sys.executable, "-c", KILLED_SAVE, PACKAGE_ROOT, self.root])
        self.assertEqual(killed.returncode, -signal.SIGKILL)
        (temp,) = [name for name in self._names() if name.startswith(".sync-") and name.endswith(".tmp")]
        leftover = os.path.join(self.root, temp)
        moment = (self.clock - STALE_AGE).timestamp()
        os.utime(leftover, (moment, moment))
        FileStateStore(self.root, lambda: self.clock).save_sync({"units": {}, "sessions": {}})
        self.assertEqual(self._names(), [".sync.json", ".sync.lock"])


if __name__ == "__main__":
    unittest.main()
