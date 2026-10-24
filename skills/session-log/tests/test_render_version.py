"""Render-version rewrite and hook budget (BE-16; S47, S65): every archived session is rewritten once after a `RENDER_VERSION`
raise, tracked per session entry, a budget-stopped sync resumes, an older copy rewrites nothing for its version, and a
rewrite is never built from fewer units than the entry's count. Real `FileStateStore` and real `LogUsageWriter` throughout;
the clock is injected and moves only when a session is written or the scan runs."""
import hashlib
import os
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from sessionlog.archive_port import WriteOutcome
from sessionlog.pricing import PricingRules, Rates
from sessionlog.state import FileStateStore
from sessionlog.state_port import OnFlag
from sessionlog.sync import (HOOK_SYNC_BUDGET_SECONDS, RENDER_VERSION, SESSION_SKIPPED, SESSION_WRITTEN, UNIT_INACTIVE,
                             SyncUseCase)
from tests.test_sync import NOW, ONE_DAY, WRITE_FAILURE, _NoPrices, _transcript
from tests.test_sync_safety import AGENT, BASE, MINUTE, PARTLY_GONE, UNITS_WITH_AGENT, SafetyTestCase

PRICES_FILE = Path(__file__).resolve().parents[1] / "prices.json"
# The sha256 of the shipped prices.json (line ends as LF) per RENDER_VERSION: a price change without a raise fails the pin test.
PRICES_SHA256_BY_RENDER_VERSION = {1: "4fee2ad0869ca633220f3f0c4512af4fe74078086384a8c535301330855f02f5"}
SPEC_HOOK_BUDGET_SECONDS = 20  # S47 and S65 name 20 s
SESSION_IDS = ("s1", "s2", "s3", "s4")
WRITE_TAKES_SECONDS = HOOK_SYNC_BUDGET_SECONDS / 2  # two of the four sessions fit in a hook budget
LONGER_THAN_BUDGET_SECONDS = 2 * HOOK_SYNC_BUDGET_SECONDS
OLDER_COPY = RENDER_VERSION
NEWER_COPY = RENDER_VERSION + 1
UNDONE_PRICE = "unknown"
RATES = Rates(10, 20, 1, 12.5, 20)


class Clock:
    def __init__(self):
        self.moment = NOW

    def __call__(self):
        return self.moment

    def advance(self, seconds):
        self.moment += timedelta(seconds=seconds)


class _PricedModels(_NoPrices):
    def rate_for(self, model_name, fast):
        return RATES


class SlowArchive:
    """The real writer, wrapped: every `write` takes `seconds` on the injected clock. `before_write` runs before it; with
    `failing` set the files are not written and the write reports `WRITE_FAILURE`. `written` lists the log paths asked for."""

    def __init__(self, inner, clock, seconds):
        self._inner = inner
        self._clock = clock
        self._seconds = seconds
        self.before_write = lambda: None
        self.failing = False
        self.written = []

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def write(self, log_path, log_text, report_text):
        self.before_write()
        self.written.append(log_path)
        outcome = WriteOutcome(log_path, False, WRITE_FAILURE) if self.failing else self._inner.write(log_path, log_text, report_text)
        self._clock.advance(self._seconds)
        return outcome


class RenderVersionTestCase(SafetyTestCase):
    def setUp(self):
        super().setUp()
        self.clock = Clock()
        self.write_takes = 0
        self.before_write = lambda: None
        self.failing_writes = False
        self.prices = _NoPrices()

    def _sync_as(self, version, budget_seconds=None):
        """One complete run of a copy whose `RENDER_VERSION` is `version`. Returns the archive of the run: what it was asked
        to write. Read keys are those of this run only."""
        self.reader.read_keys.clear()
        archive = SlowArchive(self.archive, self.clock, self.write_takes)
        archive.before_write = self.before_write
        archive.failing = self.failing_writes
        with patch("sessionlog.sync.RENDER_VERSION", version):
            SyncUseCase(self.reader, archive, FileStateStore(self.logs), PricingRules(self.prices), self.clock).sync(budget_seconds)
        return archive

    def _render_versions(self, session_ids=SESSION_IDS):
        return {session_id: self._entry(session_id)["renderVersion"] for session_id in session_ids}

    def _session_keys(self, *session_ids):
        return [self._key(session_id) for session_id in session_ids]

    def _install_sessions(self):
        for session_id in SESSION_IDS:
            self._install(session_id, _transcript(BASE), NOW)


class RenderVersionRewriteTest(RenderVersionTestCase):
    def test_budget_ends_halfway_second_sync_rewrites_the_rest_older_copy_rewrites_none_third_none(self):
        self._install_sessions()
        self._sync_as(OLDER_COPY)
        archived = self._archived()
        self.assertEqual(self._render_versions(), dict.fromkeys(SESSION_IDS, OLDER_COPY))
        self.write_takes = WRITE_TAKES_SECONDS
        self._sync_as(NEWER_COPY, HOOK_SYNC_BUDGET_SECONDS)  # a hook sync: two sessions fit in the budget
        self.assertEqual(self.reader.read_keys, self._session_keys("s1", "s2"))
        self.assertEqual(self._render_versions(), {"s1": NEWER_COPY, "s2": NEWER_COPY, "s3": OLDER_COPY, "s4": OLDER_COPY})
        self._sync_as(NEWER_COPY)
        self.assertEqual(self.reader.read_keys, self._session_keys("s3", "s4"))
        self.assertEqual(self._render_versions(), dict.fromkeys(SESSION_IDS, NEWER_COPY))
        self.assertEqual(self._sync_as(OLDER_COPY).written, [])
        self.assertEqual((self.reader.read_keys, self._render_versions()), ([], dict.fromkeys(SESSION_IDS, NEWER_COPY)))
        self.assertEqual(self._sync_as(NEWER_COPY).written, [])
        self.assertEqual(self.reader.read_keys, [])
        self.assertEqual(self._archived(), archived)

    def test_hook_budget_is_20_seconds(self):
        self.assertEqual(HOOK_SYNC_BUDGET_SECONDS, SPEC_HOOK_BUDGET_SECONDS)

    def test_hook_budget_saves_progress_after_each_session_and_next_sync_finishes(self):
        self._install_sessions()
        saved_before_each_write = []
        self.write_takes = WRITE_TAKES_SECONDS
        self.before_write = lambda: saved_before_each_write.append(sorted(FileStateStore(self.logs).load_sync()["sessions"]))
        self._sync_as(RENDER_VERSION, HOOK_SYNC_BUDGET_SECONDS)
        self.assertEqual(saved_before_each_write, [[], self._session_keys("s1")])
        state = FileStateStore(self.logs).load_sync()
        self.assertEqual(sorted(state["sessions"]), self._session_keys("s1", "s2"))
        for session_id in ("s3", "s4"):  # learned and kept, not examined yet
            self.assertEqual(state["units"][os.path.join(self.folder, session_id + ".jsonl")]["sessionKey"], self._key(session_id))
        learned_before = self.reader.session_of_calls
        self._sync_as(RENDER_VERSION)
        self.assertEqual(self.reader.read_keys, self._session_keys("s3", "s4"))
        self.assertEqual(self.reader.session_of_calls, learned_before)
        self.assertEqual(sorted(FileStateStore(self.logs).load_sync()["sessions"]), self._session_keys(*SESSION_IDS))
        self._sync_as(RENDER_VERSION)
        self.assertEqual(self.reader.read_keys, [])

    def test_a_sync_without_a_budget_is_never_cut_short(self):
        self._install_sessions()
        self.write_takes = 10 * LONGER_THAN_BUDGET_SECONDS
        self.assertEqual(len(self._sync_as(RENDER_VERSION).written), len(SESSION_IDS))

    def test_a_scan_that_spends_the_budget_starts_no_session_and_still_saves_what_was_learned(self):
        self._install_sessions()
        real_scan = self.reader.scan

        def slow_scan():
            self.clock.advance(LONGER_THAN_BUDGET_SECONDS)
            return real_scan()

        self.reader.scan = slow_scan
        self._sync_as(RENDER_VERSION, HOOK_SYNC_BUDGET_SECONDS)
        state = FileStateStore(self.logs).load_sync()
        self.assertEqual((self.reader.read_keys, state["sessions"]), ([], {}))
        self.assertEqual({unit["sessionKey"] for unit in state["units"].values()}, set(self._session_keys(*SESSION_IDS)))

    def test_a_session_that_overruns_the_budget_finishes_and_no_next_one_starts(self):
        self._install_sessions()
        self.write_takes = LONGER_THAN_BUDGET_SECONDS
        self.assertEqual(len(self._sync_as(RENDER_VERSION, HOOK_SYNC_BUDGET_SECONDS).written), 1)
        self.assertEqual(sorted(FileStateStore(self.logs).load_sync()["sessions"]), self._session_keys("s1"))

    def test_rewrite_for_the_version_keeps_the_log_path_and_the_files_of_unchanged_text(self):
        self._install_sessions()
        self._sync_as(OLDER_COPY)
        archived = self._archived()
        log_paths = {session_id: self._entry(session_id)["logPath"] for session_id in SESSION_IDS}
        archive = self._sync_as(NEWER_COPY)
        self.assertEqual(self.reader.read_keys, self._session_keys(*SESSION_IDS))
        self.assertEqual({session_id: self._entry(session_id)["logPath"] for session_id in SESSION_IDS}, log_paths)
        self.assertEqual(self._archived(), archived)
        self.assertEqual(sorted(archive.written), sorted(log_paths.values()))

    def test_a_raise_rewrites_the_report_with_the_new_rules_and_an_older_copy_then_leaves_it(self):
        self._install("s1", _transcript(BASE), NOW)
        self._sync_as(OLDER_COPY)
        before = self._text_of(self._archived(), "s1", ".usage.txt")
        self.assertIn(UNDONE_PRICE, before)
        self.prices = _PricedModels()
        self._sync_as(NEWER_COPY)
        after = self._text_of(self._archived(), "s1", ".usage.txt")
        self.assertNotIn(UNDONE_PRICE, after)
        self.prices = _NoPrices()
        self.assertEqual(self._sync_as(OLDER_COPY).written, [])
        self.assertEqual(self._text_of(self._archived(), "s1", ".usage.txt"), after)

    def test_session_archived_before_a_later_on_time_is_still_rewritten_for_the_version(self):
        self._install_sessions()
        self._sync_as(OLDER_COPY)
        os.unlink(os.path.join(self.logs, ".log_enabled"))  # off, then on again after every session's last activity
        FileStateStore(self.logs).write_on_flag(OnFlag(BASE + ONE_DAY))
        self._sync_as(NEWER_COPY)
        self.assertEqual(self.reader.read_keys, self._session_keys(*SESSION_IDS))
        self.assertEqual(self._render_versions(), dict.fromkeys(SESSION_IDS, NEWER_COPY))

    def test_session_whose_source_is_gone_is_left_alone_by_the_raise(self):
        path = self._install("gone", _transcript(BASE), NOW)
        self._sync_as(OLDER_COPY)
        archived = self._archived()
        os.unlink(path)
        self._sync_as(NEWER_COPY)
        self.assertEqual((self.reader.read_keys, self._archived(), self._render_versions(("gone",))), ([], archived, {"gone": OLDER_COPY}))

    def test_sessions_waiting_only_for_the_version_come_after_a_session_with_new_turns(self):
        self._install("s1", _transcript(BASE), NOW)
        self._install("s2", _transcript(BASE), NOW)
        self._install("z", _transcript(BASE), NOW)  # "z" sorts after "s1" and "s2": only the ordering puts it first
        self._sync_as(OLDER_COPY)
        self._install("z", _transcript(BASE, BASE + MINUTE), NOW)
        self.write_takes = WRITE_TAKES_SECONDS
        self._sync_as(NEWER_COPY, WRITE_TAKES_SECONDS)  # room for one session
        self.assertEqual(self.reader.read_keys, self._session_keys("z"))
        self.assertEqual(self._render_versions(("s1", "s2", "z")), {"s1": OLDER_COPY, "s2": OLDER_COPY, "z": NEWER_COPY})
        self.assertIn("prompt 1", self._text_of(self._archived(), "z", ".md"))

    def test_failed_rewrite_for_the_version_stays_at_the_old_version_skipped_and_is_retried(self):
        self._install("s1", _transcript(BASE), NOW)
        self._sync_as(OLDER_COPY)
        archived = self._archived()
        self.failing_writes = True
        self._sync_as(NEWER_COPY)
        entry = self._entry("s1")
        self.assertEqual((entry["state"], entry["reason"], entry["renderVersion"]), (SESSION_SKIPPED, WRITE_FAILURE, OLDER_COPY))
        self.assertEqual(self._archived(), archived)
        self.failing_writes = False
        self._sync_as(NEWER_COPY)
        entry = self._entry("s1")
        self.assertEqual((entry["state"], entry["renderVersion"], "reason" in entry), (SESSION_WRITTEN, NEWER_COPY, False))
        self.assertEqual(self._sync_as(NEWER_COPY).written, [])


class RenderVersionFewerUnitsTest(RenderVersionTestCase):
    def test_rewrite_for_the_version_is_withheld_while_a_counted_unit_is_gone_and_done_once_it_is_back(self):
        self._install("partial", _transcript(BASE), NOW)
        agent = self._agent_file("partial")
        self._sync_as(OLDER_COPY)
        archived = self._archived()
        os.unlink(agent)
        for _ in range(2):  # every sync: withheld without a read
            archive = self._sync_as(NEWER_COPY)
            entry = self._entry("partial")
            self.assertEqual((entry["state"], entry["reason"], entry["renderVersion"], entry["unitCount"]),
                             (SESSION_SKIPPED, PARTLY_GONE, OLDER_COPY, UNITS_WITH_AGENT))
            self.assertEqual((self.reader.read_keys, archive.written, self._archived()), ([], [], archived))
        self._agent_file("partial")
        self._sync_as(NEWER_COPY)
        entry = self._entry("partial")
        self.assertEqual((entry["state"], entry["renderVersion"], entry["unitCount"], "reason" in entry),
                         (SESSION_WRITTEN, NEWER_COPY, UNITS_WITH_AGENT, False))
        self.assertEqual(self.reader.read_keys, self._session_keys("partial"))
        self.assertEqual(self._sync_as(NEWER_COPY).written, [])
        self.assertEqual(self.reader.read_keys, [])

    def test_rewrite_for_the_version_is_withheld_while_a_counted_unit_is_unreadable_and_done_once_it_reads(self):
        self._install("partial", _transcript(BASE), NOW)
        agent = self._agent_file("partial")
        self._sync_as(OLDER_COPY)
        archived = self._archived()
        self._corrupt(agent)
        archive = self._sync_as(NEWER_COPY)
        entry = self._entry("partial")
        self.assertEqual((entry["state"], entry["reason"], entry["renderVersion"]), (SESSION_SKIPPED, PARTLY_GONE, OLDER_COPY))
        self.assertEqual((archive.written, self._archived()), ([], archived))
        self._agent_file("partial")
        self._sync_as(NEWER_COPY)
        entry = self._entry("partial")
        self.assertEqual((entry["state"], entry["renderVersion"], entry["unitCount"]), (SESSION_WRITTEN, NEWER_COPY, UNITS_WITH_AGENT))
        self.assertEqual(self._sync_as(NEWER_COPY).written, [])


class OlderCopyTest(RenderVersionTestCase):
    def test_older_copy_retrying_an_unreadable_unit_does_not_rewrite_a_newer_sessions_files(self):
        self._install("s1", _transcript(BASE), NOW)
        self._corrupt(self._install_agent("s1", AGENT, "", NOW))
        self._sync_as(NEWER_COPY)
        archived = self._archived()
        archive = self._sync_as(OLDER_COPY)
        self.assertEqual(self.reader.read_keys, self._session_keys("s1"))  # an unreadable unit is read again on every sync
        self.assertEqual((archive.written, self._archived()), ([], archived))
        self.assertEqual(self._entry("s1")["renderVersion"], NEWER_COPY)

    def test_older_copy_between_two_syncs_of_a_raise_reads_none_of_the_mixed_sessions(self):
        self._install_sessions()
        self._sync_as(OLDER_COPY)
        self.write_takes = WRITE_TAKES_SECONDS
        self._sync_as(NEWER_COPY, HOOK_SYNC_BUDGET_SECONDS)  # stopped halfway: two sessions at each version
        self.write_takes = 0
        self.assertEqual(self._sync_as(OLDER_COPY).written, [])
        self.assertEqual((self.reader.read_keys, sorted(self._render_versions().values())),
                         ([], [OLDER_COPY, OLDER_COPY, NEWER_COPY, NEWER_COPY]))
        self._sync_as(NEWER_COPY)
        self.assertEqual(self.reader.read_keys, self._session_keys("s3", "s4"))

    def test_older_copy_rewrites_after_new_activity_and_the_newer_copy_redoes_it_once(self):
        self._install("s1", _transcript(BASE), NOW)
        self._sync_as(NEWER_COPY)
        self._install("s1", _transcript(BASE, BASE + MINUTE), NOW)
        self._sync_as(OLDER_COPY)
        self.assertEqual((self.reader.read_keys, self._entry("s1")["renderVersion"]), (self._session_keys("s1"), OLDER_COPY))
        self._sync_as(NEWER_COPY)
        self.assertEqual((self.reader.read_keys, self._entry("s1")["renderVersion"]), (self._session_keys("s1"), NEWER_COPY))
        self._sync_as(NEWER_COPY)
        self.assertEqual(self.reader.read_keys, [])
        self.assertIn("prompt 1", self._text_of(self._archived(), "s1", ".md"))


class RenderedWithoutSessionTest(RenderVersionTestCase):
    def test_owed_session_whose_file_was_emptied_keeps_its_files_and_old_version_until_new_activity_rewrites_it(self):
        path = self._install("s1", _transcript(BASE), NOW)
        self._sync_as(OLDER_COPY)
        archived = self._archived()
        self._install("s1", "", NOW)
        for _ in range(2):  # nothing can be rewritten, so it is read again each sync
            archive = self._sync_as(NEWER_COPY)
            self.assertEqual((self.reader.read_keys, archive.written, self._archived()), (self._session_keys("s1"), [], archived))
            self.assertEqual((self._entry("s1")["renderVersion"], FileStateStore(self.logs).load_sync()["units"][path]["state"]),
                             (OLDER_COPY, UNIT_INACTIVE))
        self._install("s1", _transcript(BASE, BASE + MINUTE), NOW)
        self._sync_as(NEWER_COPY)
        self.assertEqual(self._entry("s1")["renderVersion"], NEWER_COPY)
        self.assertIn("prompt 1", self._text_of(self._archived(), "s1", ".md"))


class PricesPinTest(unittest.TestCase):
    def test_prices_json_sha256_pinned_to_render_version_fails_until_raised(self):
        pinned = PRICES_SHA256_BY_RENDER_VERSION.get(RENDER_VERSION)
        self.assertIsNotNone(pinned, "RENDER_VERSION %d has no pinned prices.json digest: add it to this test" % RENDER_VERSION)
        digest = hashlib.sha256(PRICES_FILE.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        self.assertEqual(digest, pinned, "prices.json changed: raise RENDER_VERSION in sessionlog/sync.py and pin the new digest "
                                         "%s to it in this test, so every archived report is rewritten with the new prices" % digest)


if __name__ == "__main__":
    unittest.main()
