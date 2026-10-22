"""Sync rewrite safety (BE-15; S15, S17, S33, S48, S61): unique temp names, key-merged `.sync.json` saves, failed replaces,
partly gone sources, old-format logs, reused log paths and zones. Real `FileStateStore` and real `LogUsageWriter` throughout."""
import os
import unittest
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sessionlog.commands import CommandUseCases, UnreadableItem
from sessionlog.log_usage_writer import LogUsageWriter
from sessionlog.model import Harness
from sessionlog.pricing import PricingRules
from sessionlog.reader_port import CANNOT_BE_DECODED_REASON, CANNOT_BE_LISTED_REASON, UnreadableSource
from sessionlog.state import FileStateStore
from sessionlog.state_port import OnFlag
from sessionlog.sync import RENDER_VERSION, SyncUseCase
from sessionlog.usage_target import UsageTargetResolver
from tests.test_log_usage_writer import SHIFT_AT, Shifting
from tests.test_sync import (FILES_PER_SESSION, NOW, ON_AT, ONE_DAY, PROJECT, CountingReader, RealArchiveTestCase,
                             _agent_transcript, _NoPrices, _transcript)

CWD = "/work/proj"
OWN_PID = 111
RIVAL_PID = 222
OWN_SESSIONS = 1  # run A scans only s1
RIVAL_SESSIONS = 2  # run B, started in the middle of A, finds s1 (not yet published) and s2
TEMP_PREFIX = ".session_"
STALE_TEMP = ".session_2026-01-01_00-00-00_old.md.999.deadbeef.tmp"
FRESH_TEMP = ".session_2026-01-01_00-00-00_other.md.333.cafe0123.tmp"
STALE_AGE = timedelta(hours=2)
LOG_SUFFIX = ".md"
REPORT_SUFFIX = ".usage.txt"
SYNC_FILE = ".sync.json"
LOCKED = "locked"
BUSY = "busy"
UNITS_WITH_AGENT = 2  # a main file and one sub-agent file
PARTLY_GONE = "source partly gone (%d of %d units)" % (UNITS_WITH_AGENT - 1, UNITS_WITH_AGENT)
BASE = ON_AT + ONE_DAY
MINUTE = timedelta(minutes=1)
AGENT = "a1"
AGENT_SPAN = timedelta(seconds=5)
ACTIVE_OFFSET = 5 * MINUTE
PARTIAL_OFFSET = 10 * MINUTE
PARTIAL_LATER_OFFSET = 20 * MINUTE
ACTIVE_LOG_STAMP = (BASE + ACTIVE_OFFSET).strftime("%Y-%m-%d_%H-%M-%S")
REPORT_BLOCKED_TURNS = 2
LOG_BLOCKED_TURNS = 3
FIRST_FAILING_SYNC_SAVE = 2  # save 1 of a run is the one before any session is read
LEGACY_PID = 4242
OLD_FORMAT_STAMP = "2026-01-01_00-00-00"
INSTANT = datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)
AFTER_SHIFT = SHIFT_AT.replace(tzinfo=timezone.utc) + timedelta(hours=1)  # the `Shifting` zone has moved from +2 to +1 by then
ZONES = (("UTC+2", timezone(timedelta(hours=2)), "12:00:00"), ("UTC-5", timezone(timedelta(hours=-5)), "05:00:00"),
         ("UTC+5:30", timezone(timedelta(hours=5, minutes=30)), "15:30:00"), ("UTC", timezone.utc, "10:00:00"))
GARBAGE = b"\xff\xfe\x00bad"


def _write(path, text, modified_at=NOW):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    os.utime(path, (modified_at.timestamp(), modified_at.timestamp()))


def _read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def _report_path(log_path):
    return log_path[:-len(LOG_SUFFIX)] + REPORT_SUFFIX


def _session_files(root):
    """Every archived file below `root`: relative path -> bytes."""
    found = {}
    for folder, _, names in os.walk(root):
        for name in names:
            if name.startswith("session_"):
                path = os.path.join(folder, name)
                found[os.path.relpath(path, root)] = _read_bytes(path)
    return found


def _temp_files(root):
    return sorted(os.path.join(folder, name) for folder, _, names in os.walk(root) for name in names if name.endswith(".tmp"))


def _blocking(real_replace, blocked):
    """An `os.replace` that raises for every target `blocked` accepts and works for all others."""
    def replace_file(source, target, *args, **kwargs):
        if blocked(str(target)):
            raise PermissionError(LOCKED)
        return real_replace(source, target, *args, **kwargs)
    return replace_file


class UnreplaceableSyncFile:
    """An `os.replace` that raises for the `.sync.json` target from its `first_failing_call`-th replace on (1-based) and
    keeps the bytes of the first one in `first_saved`. Save 1 of a run is the one before any session is read."""

    def __init__(self, real_replace, first_failing_call):
        self._real_replace = real_replace
        self._first_failing_call = first_failing_call
        self._calls = 0
        self.first_saved = None

    def __call__(self, source, target, *args, **kwargs):
        if str(target).endswith(SYNC_FILE):
            self._calls += 1
            if self._calls >= self._first_failing_call:
                raise PermissionError(BUSY)
            if self._calls == 1:
                self.first_saved = _read_bytes(source)
        return self._real_replace(source, target, *args, **kwargs)


class RecordingArchive:
    """The real writer, wrapped: remembers the text passed to every `write` per target path and, when given, first runs
    `before_first_write(log_path)` once: a second run in the middle of this one."""

    def __init__(self, inner, passed=None, before_first_write=None):
        self._inner = inner
        self.passed = {} if passed is None else passed
        self._before_first_write = before_first_write

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def write(self, log_path, log_text, report_text):
        self.passed[log_path] = log_text
        self.passed[_report_path(log_path)] = report_text
        before, self._before_first_write = self._before_first_write, None
        if before is not None:
            before(log_path)
        return self._inner.write(log_path, log_text, report_text)


class IncompleteScanReader(CountingReader):
    """A reader whose scan reports one folder it could not list."""

    def scan(self):
        return replace(super().scan(), unreadable=(UnreadableSource(Harness.CLAUDE, self._projects, CANNOT_BE_LISTED_REASON),))


class SafetyTestCase(RealArchiveTestCase):
    def _run(self, pid=OWN_PID, wrap=None, to_local=lambda moment: moment):
        """One complete run: its own state store and writer (with process id `pid`), over the same log root."""
        writer = LogUsageWriter(self.logs, to_local, pid=lambda: pid)
        archive = wrap(writer) if wrap else writer
        return SyncUseCase(self.reader, archive, FileStateStore(self.logs), PricingRules(_NoPrices()), lambda: NOW).sync()

    def _commands(self):
        state = FileStateStore(self.logs)
        archive = LogUsageWriter(self.logs, lambda moment: moment)
        sync = SyncUseCase(self.reader, archive, state, PricingRules(_NoPrices()), lambda: NOW)
        return CommandUseCases(sync, UsageTargetResolver(self.reader), archive, state, _NoPrices(), lambda: NOW, self.logs)

    def _saved(self):
        return FileStateStore(self.logs).load_sync()

    def _entry(self, sid):
        return self._saved()["sessions"][self._key(sid)]

    def _agent_file(self, sid):
        return self._install_agent(sid, AGENT, _agent_transcript(AGENT, BASE, BASE + AGENT_SPAN), NOW)

    @staticmethod
    def _corrupt(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(GARBAGE)
        os.utime(path, (NOW.timestamp(), NOW.timestamp()))

    @contextmanager
    def _replace_blocked(self, blocked_path):
        with patch("os.replace", _blocking(os.replace, lambda target: target == blocked_path)):
            yield


class TempNameAndMergeTest(SafetyTestCase):
    def test_two_syncs_use_different_temp_names_and_each_publishes_complete_files(self):
        self._install("s1", _transcript(NOW), NOW)
        real_replace = os.replace
        passed, replaced = {}, []

        def spy(source, target, *args, **kwargs):
            if os.path.basename(source).startswith(TEMP_PREFIX):
                replaced.append((os.path.basename(source), str(target), _read_bytes(source)))
            return real_replace(source, target, *args, **kwargs)

        def rival_run(log_path):
            self._plant_foreign_temps(os.path.dirname(log_path))
            self._install("s2", _transcript(NOW), NOW)
            self._run(RIVAL_PID, lambda writer: RecordingArchive(writer, passed))

        with patch("os.replace", spy):
            self._run(OWN_PID, lambda writer: RecordingArchive(writer, passed, rival_run))
        names = [name for name, _, _ in replaced]
        self.assertEqual(len(set(names)), len(names))
        for pid, sessions in ((OWN_PID, OWN_SESSIONS), (RIVAL_PID, RIVAL_SESSIONS)):
            self.assertEqual(sum(".%d." % pid in name for name in names), sessions * FILES_PER_SESSION)
        for name, target, content in replaced:
            self.assertEqual(content, passed[target].encode("utf-8"), name)  # complete when it was published
        self.assertEqual([os.path.basename(path) for path in _temp_files(self.logs)], [FRESH_TEMP])
        saved = self._saved()
        self.assertEqual(sorted(saved["sessions"]), [self._key("s1"), self._key("s2")])
        self.assertEqual(sorted(saved["units"]), sorted(os.path.join(self.folder, sid + ".jsonl") for sid in ("s1", "s2")))

    @staticmethod
    def _plant_foreign_temps(folder):
        now = datetime.now(timezone.utc)
        _write(os.path.join(folder, STALE_TEMP), "temp of a dead run", now - STALE_AGE)
        _write(os.path.join(folder, FRESH_TEMP), "temp of a running run", now)

    def test_older_read_publishing_last_drops_fingerprint_and_next_sync_rewrites_from_newer_source(self):
        path = self._install("s1", _transcript(NOW), NOW)
        self._run(OWN_PID, lambda writer: RecordingArchive(writer, before_first_write=lambda log_path: self._newer_run_publishes_first()))
        self.assertNotIn("prompt 1", self._text_of(self._archived(), "s1", LOG_SUFFIX))
        self.assertNotIn("fingerprint", self._saved()["units"][path])
        self.reader.read_keys.clear()
        self._sync()
        self.assertEqual(self.reader.read_keys, [self._key("s1")])
        self.assertIn("prompt 1", self._text_of(self._archived(), "s1", LOG_SUFFIX))
        self.assertIn("fingerprint", self._saved()["units"][path])
        self._sync()
        self.assertEqual(self.reader.read_keys, [self._key("s1")])  # not cleared: the third sync read nothing more

    def _newer_run_publishes_first(self):
        self._install("s1", _transcript(NOW, NOW + MINUTE), NOW)
        self._run(RIVAL_PID)
        self.assertIn("prompt 1", self._text_of(self._archived(), "s1", LOG_SUFFIX))


class PartlyGoneTest(SafetyTestCase):
    def test_deleted_source_and_old_format_logs_byte_identical_and_partly_gone_session_not_rewritten(self):
        gone = self._install("gone", _transcript(BASE), NOW)
        self._install("partial", _transcript(BASE + PARTIAL_OFFSET), NOW)
        agent = self._agent_file("partial")
        self._sync()
        partial_log = self._entry("partial")["logPath"]
        partial_report = _read_bytes(_report_path(partial_log)).decode("utf-8")
        protected = {**self._archived(), **self._plant_old_format_logs(os.path.dirname(partial_log), ("gone", "partial", "active"))}
        os.unlink(gone)
        os.unlink(agent)
        self._install("partial", _transcript(BASE + PARTIAL_OFFSET, BASE + PARTIAL_LATER_OFFSET), NOW)
        self._install("active", _transcript(BASE + ACTIVE_OFFSET), NOW)
        self.reader.read_keys.clear()
        files = self._syncs_through_a_render_version_raise(protected, partial_log, partial_report)
        new_names = sorted(os.path.basename(name) for name in files if name not in protected)  # `active`: a separate new log
        self.assertEqual(new_names, ["session_%s_active%s" % (ACTIVE_LOG_STAMP, suffix) for suffix in (LOG_SUFFIX, REPORT_SUFFIX)])
        self.assertNotIn(self._key("partial"), self.reader.read_keys)  # withheld without a read, every time
        self.assertEqual(self.reader.read_keys.count(self._key("active")), 2)  # new in the first pass, rewritten for the raise
        self.assertEqual(self._entry("active")["renderVersion"], RENDER_VERSION + 1)
        self.assertEqual(self._entry("partial")["renderVersion"], RENDER_VERSION)  # a rewrite from fewer units is never built
        self._agent_file("partial")
        self._sync()
        entry = self._entry("partial")
        self.assertEqual((entry["state"], entry["unitCount"], "reason" in entry), ("written", UNITS_WITH_AGENT, False))
        self.assertIn("prompt 1", _read_bytes(partial_log).decode("utf-8"))
        self.assertIn("agent-a1", _read_bytes(_report_path(partial_log)).decode("utf-8"))
        self.assertEqual(self._commands().status().unreadable, ())

    def _plant_old_format_logs(self, folder, session_ids):
        """Old-release logs of the ids: `session_<id>.md`, Claude `session_<ts>_<id>_<pid>.md`, OpenCode `session_<ts>_<id>.md`."""
        planted = {}
        for sid in session_ids:
            for name in ("session_%s.md" % sid, "session_%s_%s_%d.md" % (OLD_FORMAT_STAMP, sid, LEGACY_PID),
                         "session_%s_%s.md" % (OLD_FORMAT_STAMP, sid)):
                path = os.path.join(folder, name)
                _write(path, "old format %s\n" % name)
                planted[os.path.relpath(path, self.logs)] = _read_bytes(path)
        return planted

    def _syncs_through_a_render_version_raise(self, protected, partial_log, partial_report):
        """One plain sync pass and one after a render-version raise (S15 names both). The raise rewrites `active` for its
        version; `partial` is withheld by the pre-read guard before `_already_written` is asked, so it stays at the old
        version, and `gone` has no units and is never a candidate."""
        commands = self._commands()
        for render_version in (RENDER_VERSION, RENDER_VERSION + 1):
            with patch("sessionlog.sync.RENDER_VERSION", render_version):
                self._sync()
                files = self._archived()
                self.assertEqual({name: files[name] for name in protected}, protected)
                entry = self._entry("partial")
                self.assertEqual((entry["state"], entry["reason"], entry["unitCount"], entry["logPath"]),
                                 ("skipped", PARTLY_GONE, UNITS_WITH_AGENT, partial_log))
                self.assertEqual(commands.status().unreadable, (UnreadableItem(partial_log, PARTLY_GONE),))
                self.assertEqual(commands.usage_latest(CWD).report_text, partial_report)
        return files

    def test_counted_subagent_file_present_but_unreadable_withholds_the_rewrite_and_a_valid_file_rewrites_it(self):
        self._install("partial", _transcript(BASE), NOW)
        agent = self._agent_file("partial")
        self._sync()
        before = self._archived()
        log_path = self._entry("partial")["logPath"]
        self._corrupt(agent)
        self._sync()
        self.assertEqual(self._archived(), before)
        entry = self._entry("partial")
        self.assertEqual((entry["state"], entry["reason"], entry["unitCount"]), ("skipped", PARTLY_GONE, UNITS_WITH_AGENT))
        self.assertEqual(self._commands().status().unreadable,
                         (UnreadableItem(agent, CANNOT_BE_DECODED_REASON), UnreadableItem(log_path, PARTLY_GONE)))
        self._agent_file("partial")
        self._sync()
        entry = self._entry("partial")
        self.assertEqual((entry["state"], entry["unitCount"], "reason" in entry), ("written", UNITS_WITH_AGENT, False))
        self.assertEqual(self._archived(), before)
        self.assertEqual(self._commands().status().unreadable, ())


class FailedReplaceTest(SafetyTestCase):
    def test_os_replace_raising_skips_file_removes_temp_and_reports_path_and_reason(self):
        path = self._install("s1", _transcript(NOW), NOW)
        self._sync()
        log_path = self._entry("s1")["logPath"]
        self._report_blocked(log_path)
        self._log_blocked(log_path)
        self.assertEqual(self._saved()["units"][path]["state"], "read")

    def _grow(self, turns):
        self._install("s1", _transcript(*(NOW + number * MINUTE for number in range(turns))), NOW)

    def _blocked_sync(self, blocked_path, turns):
        """Grows s1 to `turns` turns, then syncs and asks status and usage while `blocked_path` cannot be replaced. Returns
        the hand-back of that sync, the entry's reason, the unreadable list of `status` and the report `usage` printed."""
        self._grow(turns)
        commands = self._commands()
        with self._replace_blocked(blocked_path):
            outcome = self._sync()
            reason = self._entry("s1")["reason"]
            status = commands.status().unreadable
            printed = commands.usage_latest(CWD).report_text
        self.assertEqual(_temp_files(self.logs), [])
        self.assertEqual(self._entry("s1")["state"], "skipped")
        return outcome.unsaved_reports[self._key("s1")], reason, status, printed

    def _assert_next_sync_writes_it(self):
        self._sync()
        self.assertEqual((self._entry("s1")["state"], "reason" in self._entry("s1")), ("written", False))
        self.assertEqual(_temp_files(self.logs), [])

    def _report_blocked(self, log_path):
        """The report cannot be replaced: the log is new, the report on disk is old, the new report is handed back."""
        report_path = _report_path(log_path)
        old_log, old_report = _read_bytes(log_path), _read_bytes(report_path)
        self.assertIn(b"TOTAL (1 requests", old_report)
        handed_back, reason, status, printed = self._blocked_sync(report_path, REPORT_BLOCKED_TURNS)
        self.assertNotEqual(_read_bytes(log_path), old_log)
        self.assertEqual(_read_bytes(report_path), old_report)
        self.assertEqual(reason, "%s: %s" % (report_path, LOCKED))
        self.assertEqual(status, (UnreadableItem(log_path, reason),))
        self.assertIn("TOTAL (%d requests" % REPORT_BLOCKED_TURNS, handed_back)
        self.assertEqual(printed, handed_back)
        self._assert_next_sync_writes_it()
        self.assertEqual(_read_bytes(report_path).decode("utf-8"), handed_back)

    def _log_blocked(self, log_path):
        """The log cannot be replaced: the log on disk is old, the report is new and equals the hand-back."""
        report_path = _report_path(log_path)
        old_log, old_report = _read_bytes(log_path), _read_bytes(report_path)
        handed_back, reason, status, printed = self._blocked_sync(log_path, LOG_BLOCKED_TURNS)
        self.assertEqual(_read_bytes(log_path), old_log)
        self.assertIn("TOTAL (%d requests" % LOG_BLOCKED_TURNS, handed_back)
        self.assertNotEqual(handed_back.encode("utf-8"), old_report)
        self.assertEqual(_read_bytes(report_path).decode("utf-8"), handed_back)
        self.assertEqual(reason, LOCKED)  # the failed file is the one status already names
        self.assertEqual(status, (UnreadableItem(log_path, reason),))
        self.assertEqual(printed, handed_back)
        self._assert_next_sync_writes_it()
        self.assertIn("prompt %d" % (LOG_BLOCKED_TURNS - 1), _read_bytes(log_path).decode("utf-8"))

    def test_failed_first_write_leaves_no_entry_units_unreadable_with_path_and_reason_and_hands_the_report_back(self):
        path = self._install("s1", _transcript(NOW), NOW)
        with patch("os.replace", _blocking(os.replace, lambda target: target.endswith(LOG_SUFFIX))):
            outcome = self._sync()
        saved = self._saved()
        unit = saved["units"][path]
        self.assertEqual((saved["sessions"], unit["state"]), ({}, "unreadable"))
        self.assertEqual(list(outcome.unsaved_reports), [self._key("s1")])
        self.assertEqual(_temp_files(self.logs), [])
        handed_back = outcome.unsaved_reports[self._key("s1")]
        published = _session_files(self.logs)
        self.assertEqual([name for name in published if name.endswith(REPORT_SUFFIX)], list(published))  # only the report
        self.assertEqual(len(published), 1)  # and something was published at all
        self._sync()
        log_path = self._entry("s1")["logPath"]
        self.assertEqual(unit["reason"], "%s: %s" % (log_path, LOCKED))
        self.assertEqual(self._entry("s1")["state"], "written")
        self.assertEqual(_read_bytes(_report_path(log_path)).decode("utf-8"), handed_back)
        self.assertEqual(len(_session_files(self.logs)), FILES_PER_SESSION)  # the log is there now

    def test_failed_write_that_published_one_file_keeps_the_larger_unit_count_so_a_later_loss_is_withheld(self):
        self._install("s1", _transcript(NOW), NOW)
        self._sync()
        log_path = self._entry("s1")["logPath"]
        self.assertEqual(self._entry("s1")["unitCount"], UNITS_WITH_AGENT - 1)
        agent = self._agent_file("s1")
        with self._replace_blocked(_report_path(log_path)):
            self._sync()  # the log is replaced from both units, the report is not
        entry = self._entry("s1")
        self.assertEqual((entry["state"], entry["unitCount"]), ("skipped", UNITS_WITH_AGENT))
        os.unlink(agent)
        self._grow(REPORT_BLOCKED_TURNS)
        before = self._archived()
        self._sync()
        entry = self._entry("s1")
        self.assertEqual((entry["state"], entry["reason"], entry["unitCount"]), ("skipped", PARTLY_GONE, UNITS_WITH_AGENT))
        self.assertEqual(self._archived(), before)

    def test_unreplaceable_sync_json_keeps_old_file_and_no_temp_and_next_sync_redoes_the_lost_work(self):
        path = self._install("s1", _transcript(NOW), NOW)
        sync_file = os.path.join(self.logs, SYNC_FILE)
        unreplaceable = UnreplaceableSyncFile(os.replace, FIRST_FAILING_SYNC_SAVE)
        with patch("os.replace", unreplaceable):
            with self.assertRaises(PermissionError):
                self._sync()
        self.assertEqual(_read_bytes(sync_file), unreplaceable.first_saved)
        self.assertEqual(_temp_files(self.logs), [])
        written = self._archived()
        self.assertEqual(len(written), FILES_PER_SESSION)
        self.assertEqual(self._saved()["sessions"], {})
        self.reader.read_keys.clear()
        self._sync()
        self.assertEqual(self.reader.read_keys, [self._key("s1")])
        self.assertEqual(self._archived(), written)
        self.assertEqual(self._entry("s1")["state"], "written")
        self.assertIn("fingerprint", self._saved()["units"][path])


class LogPathAndZoneTest(SafetyTestCase):
    def test_log_path_reused_name_stays_utc_offsets_plus2_minus5_plus530_dst_row_gets_own_offset_tz_change_rewrites_nothing(self):
        self._assert_log_path_is_reused_when_start_and_cwd_are_read_differently()
        self._assert_offsets_show_the_same_instant_and_the_name_stays_utc()
        root, reader, files = self._assert_the_row_after_a_daylight_saving_change_has_its_own_offset()
        self._assert_a_zone_change_alone_rewrites_nothing(root, reader, files)

    def _assert_log_path_is_reused_when_start_and_cwd_are_read_differently(self):
        self._install("moved", _transcript(BASE), NOW)
        self._sync()
        before = self._archived()
        log_path = self._entry("moved")["logPath"]
        self.assertIn("- project: %s\n" % CWD, _read_bytes(log_path).decode("utf-8"))
        earlier_start = _transcript(BASE - ONE_DAY, BASE).replace(CWD, "/work/elsewhere")
        self._install("moved", earlier_start, NOW)
        self._sync()
        self.assertEqual(sorted(self._archived()), sorted(before))  # no second log in any folder
        self.assertEqual(self._entry("moved")["logPath"], log_path)
        text = _read_bytes(log_path).decode("utf-8")
        self.assertIn("- project: /work/elsewhere\n", text)
        self.assertIn("- start: %s UTC\n" % (BASE - ONE_DAY).strftime("%Y-%m-%d %H:%M:%S"), text)
        self.assertEqual(os.path.basename(log_path), "session_%s_moved.md" % BASE.strftime("%Y-%m-%d_%H-%M-%S"))

    def _assert_offsets_show_the_same_instant_and_the_name_stays_utc(self):
        reader = self._zone_source("zoned", INSTANT)
        names = []
        for number, (label, zone, clock) in enumerate(ZONES):
            files = self._zoned_sync(os.path.join(self.root, "logs-%d" % number), reader, lambda moment, zone=zone: moment.astimezone(zone))
            log, report = self._only(files, LOG_SUFFIX), self._only(files, REPORT_SUFFIX)
            names.append(sorted(files))
            self.assertIn("- start: 2026-08-28 %s %s\n" % (clock, label), log)
            self.assertIn("## 2026-08-28 %s %s\n" % (clock, label), log)
            self.assertIn("times: %s\n" % label, report)
            self.assertIn("1. %s (working time" % clock, report)
        self.assertEqual(len({tuple(name) for name in names}), 1)
        self.assertTrue(os.path.basename(names[0][0]).startswith("session_2026-08-28_10-00-00_zoned"))

    def _assert_the_row_after_a_daylight_saving_change_has_its_own_offset(self):
        reader = self._zone_source("shifted", INSTANT, AFTER_SHIFT)
        root = os.path.join(self.root, "logs-dst")
        files = self._zoned_sync(root, reader, lambda moment: moment.astimezone(Shifting()))
        log, report = self._only(files, LOG_SUFFIX), self._only(files, REPORT_SUFFIX)
        # `Shifting` is +2 until SHIFT_AT (UTC) and +1 from then on: the wall times below follow from INSTANT and AFTER_SHIFT
        for heading in ("## 2026-08-28 12:00:00 UTC+2", "### 2026-08-28 12:00:01 UTC+2 response",
                        "## 2026-08-28 13:00:00 UTC+1", "### 2026-08-28 13:00:01 UTC+1 response"):
            self.assertIn(heading + "\n", log)
        self.assertIn("times: UTC+2\n", report)
        self.assertIn("1. 12:00:00 (working time", report)
        self.assertIn("2. 13:00:00 UTC+1 (working time", report)
        return root, reader, files

    def _assert_a_zone_change_alone_rewrites_nothing(self, root, reader, files):
        reads = len(reader.read_keys)
        other_zone = timezone(timedelta(hours=-5))
        again = self._zoned_sync(root, reader, lambda moment: moment.astimezone(other_zone))
        self.assertEqual((again, len(reader.read_keys)), (files, reads))  # the files keep the zone of their last write

    def _zone_source(self, name, *prompt_times):
        source = os.path.join(self.root, name)
        _write(os.path.join(source, "projects", PROJECT, name + ".jsonl"), _transcript(*prompt_times))
        return CountingReader(source)

    @staticmethod
    def _zoned_sync(root, reader, to_local):
        state = FileStateStore(root)
        state.write_on_flag(OnFlag(ON_AT))
        SyncUseCase(reader, LogUsageWriter(root, to_local), state, PricingRules(_NoPrices()), lambda: NOW).sync()
        return _session_files(root)

    def _only(self, files, suffix):
        matching = [content for name, content in files.items() if name.endswith(suffix)]
        self.assertEqual(len(matching), 1, sorted(files))
        return matching[0].decode("utf-8")


class VanishedUnreadableUnitTest(SafetyTestCase):
    def test_unreadable_unit_that_left_the_scan_is_no_longer_listed_unreadable(self):
        healthy = self._install("ok", _transcript(NOW), NOW)
        agent = os.path.join(self.folder, "ok", "subagents", "agent-%s.jsonl" % AGENT)
        bad = os.path.join(self.folder, "bad.jsonl")
        gone = self._install("gone", _transcript(NOW), NOW)
        self._corrupt(agent)
        self._corrupt(bad)
        self.assertEqual([item.path for item in self._commands().status().unreadable], sorted((agent, bad)))
        before = self._saved()["units"]
        os.unlink(gone)
        os.unlink(agent)
        os.unlink(bad)
        self.assertEqual(self._commands().status().unreadable, ())
        units = self._saved()["units"]
        self.assertEqual(units[agent], {"sessionKey": self._key("ok"), "state": "inactive"})
        self.assertEqual(units[bad], {"sessionKey": self._key("bad"), "state": "inactive"})
        for kept in (healthy, gone):
            self.assertEqual(units[kept], before[kept])
        self.assertEqual(before[gone]["state"], "read")
        self.reader.read_keys.clear()
        self._install("bad", _transcript(NOW), NOW)
        self._sync()
        self.assertEqual(self.reader.read_keys, [self._key("bad")])
        self.assertEqual((self._entry("bad")["state"], self._saved()["units"][bad]["state"]), ("written", "read"))

    def test_incomplete_scan_does_not_retire_unreadable_units_that_seem_gone(self):
        bad = os.path.join(self.folder, "bad.jsonl")
        self._corrupt(bad)
        self._sync()
        os.unlink(bad)
        self.reader = IncompleteScanReader(self.root)
        self._sync()
        self.assertEqual(self._saved()["units"][bad]["state"], "unreadable")
        self.assertEqual([item.path for item in self._commands().status().unreadable], [bad])
        self.reader = CountingReader(self.root)
        self._sync()
        self.assertEqual(self._saved()["units"][bad], {"sessionKey": self._key("bad"), "state": "inactive"})


if __name__ == "__main__":
    unittest.main()
