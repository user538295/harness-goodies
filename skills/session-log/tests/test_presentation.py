"""Presentation module (S67): pure result -> stdout text, each set field with its fixed wording, each unset field absent."""
import unittest
from datetime import datetime, timedelta, timezone

from sessionlog.commands import OffResult, OnResult, ProbeResult, StatusResult, UnreadableItem, UsageResult
from sessionlog.leftover_port import Leftover
from sessionlog.presentation import LEFTOVERS_HEADING as LEFTOVERS, PROBE_FAILED, render_off, render_on, render_status, render_usage

ON_AT = datetime(2026, 3, 4, 5, 6, 7, tzinfo=timezone.utc)
SYNC_AT = datetime(2026, 3, 5, 8, 9, 10, tzinfo=timezone.utc)
ROOT = "/home/u/.claude/prompt-logs"
LEFTOVER = Leftover("/home/u/.claude/scripts/prompt_log_hook.sh", "move it aside")
PROBE_FAIL = ProbeResult(ok=False, fix="install Python 3.14 and make python3 resolve to it")
ON_AT_TEXT = "2026-03-04 05:06:07 +0000"
SYNC_AT_TEXT = "2026-03-05 08:09:10 +0000"


def _status(**changes):
    fields = dict(on=True, log_root=ROOT, archived_session_count=0, unreadable=(), sync_state_reset=False)
    fields.update(changes)
    return StatusResult(**fields)


class OnOffRenderingTest(unittest.TestCase):
    def test_on_off_results_render_already_on_off_probe_fix_and_leftovers(self):
        fresh = render_on(OnResult(already_on=False, on_at=ON_AT, log_root=ROOT))
        self.assertEqual(fresh, "Session logging is on since %s.\nLogs are saved under %s.\n" % (ON_AT_TEXT, ROOT))
        self.assertNotIn("already", fresh)
        self.assertNotIn("leftover", fresh)
        self.assertNotIn("python3", fresh)

        already = render_on(OnResult(already_on=True, on_at=ON_AT, log_root=ROOT))
        self.assertIn("Session logging is already on, since %s." % ON_AT_TEXT, already)

        probed = render_on(OnResult(already_on=False, on_at=ON_AT, log_root=ROOT, probe=PROBE_FAIL, leftovers=(LEFTOVER,)))
        self.assertIn("python3 does not run Python 3.14 or newer; fix: %s\n" % PROBE_FAIL.fix, probed)
        self.assertIn("Old-release leftovers (not deleted):\n  %s: %s\n" % (LEFTOVER.path, LEFTOVER.fix), probed)

        self.assertNotIn("python3", render_on(OnResult(False, ON_AT, ROOT, probe=ProbeResult(ok=True))))
        self.assertNotIn("python3", render_status(_status(probe=ProbeResult(ok=True))))

        self.assertEqual(render_off(OffResult(already_off=False, log_root=ROOT)), "Session logging is off.\nLogs under %s are kept.\n" % ROOT)
        self.assertEqual(
            render_off(OffResult(already_off=True, log_root=ROOT)), "Session logging is already off.\nLogs under %s are kept.\n" % ROOT
        )


class StatusRenderingTest(unittest.TestCase):
    def test_status_result_renders_every_optional_field_with_fixed_wording(self):
        bare = render_status(_status(on=False))
        self.assertEqual(bare, "Session logging is off.\nLog root: %s\nArchived sessions: 0\n" % ROOT)

        full = render_status(
            _status(
                on_at=ON_AT,
                last_sync_at=SYNC_AT,
                archived_session_count=3,
                cursor_recorded_since_on=False,
                price_file_problem="prices.json is unreadable",
                sync_state_reset=True,
                unreadable=(UnreadableItem("/store/a.jsonl", "bad json"),),
                leftovers=(LEFTOVER,),
                probe=PROBE_FAIL,
            )
        )
        self.assertIn("Session logging is on since %s.\n" % ON_AT_TEXT, full)
        self.assertIn("Last sync: %s\n" % SYNC_AT_TEXT, full)
        self.assertIn("Archived sessions: 3\n", full)
        self.assertIn("Unreadable or skipped items:\n  /store/a.jsonl: bad json\n", full)
        self.assertIn("Cursor has recorded no hook events since logging was turned on.\n", full)
        self.assertIn("Price file problem: prices.json is unreadable\n", full)
        self.assertIn("The sync state was empty or corrupt and has been rebuilt.\n", full)
        self.assertIn("Old-release leftovers (not deleted):\n  %s: %s\n" % (LEFTOVER.path, LEFTOVER.fix), full)
        self.assertIn("python3 does not run Python 3.14 or newer; fix: %s\n" % PROBE_FAIL.fix, full)

    def test_status_unset_fields_are_absent_and_cursor_recorded_is_silent(self):
        text = render_status(_status(on_at=ON_AT, cursor_recorded_since_on=True))
        for absent in ("Last sync", "Unreadable", "Cursor", "Price file", "rebuilt", "leftover", "python3"):
            self.assertNotIn(absent, text)

    def test_status_on_time_read_from_file_time_is_marked(self):
        text = render_status(_status(on_at=ON_AT, on_at_from_file_time=True))
        self.assertIn("Session logging is on since %s (from the file time: .log_enabled could not be parsed).\n" % ON_AT_TEXT, text)
        self.assertNotIn("file time", render_status(_status(on_at=ON_AT)))


class EdgeRenderingTest(unittest.TestCase):
    def test_on_order_is_headline_root_probe_then_leftovers_in_on_and_status(self):
        expected_tail = "%s; fix: %s\n%s\n  %s: %s\n" % (PROBE_FAILED, PROBE_FAIL.fix, LEFTOVERS, LEFTOVER.path, LEFTOVER.fix)
        on = render_on(OnResult(False, ON_AT, ROOT, probe=PROBE_FAIL, leftovers=(LEFTOVER,)))
        self.assertTrue(on.endswith(expected_tail))
        status = render_status(_status(probe=PROBE_FAIL, leftovers=(LEFTOVER,)))
        self.assertTrue(status.endswith(expected_tail))

    def test_probe_without_fix_and_unreadable_without_reason_print_no_placeholder(self):
        self.assertIn(PROBE_FAILED + "\n", render_on(OnResult(False, ON_AT, ROOT, probe=ProbeResult(ok=False))))
        self.assertNotIn("None", render_on(OnResult(False, ON_AT, ROOT, probe=ProbeResult(ok=False))))
        self.assertIn("  /store/a.jsonl\n", render_status(_status(unreadable=(UnreadableItem("/store/a.jsonl", ""),))))

    def test_status_on_without_on_time_and_exact_common_state(self):
        self.assertEqual(
            render_status(_status(on_at=ON_AT, last_sync_at=SYNC_AT, archived_session_count=2)),
            "Session logging is on since %s.\nLog root: %s\nLast sync: %s\nArchived sessions: 2\n" % (ON_AT_TEXT, ROOT, SYNC_AT_TEXT),
        )
        self.assertTrue(render_status(_status()).startswith("Session logging is on.\n"))

    def test_cursor_silence_is_reported_only_while_on(self):
        self.assertNotIn("Cursor", render_status(_status(on=False, cursor_recorded_since_on=False)))

    def test_times_keep_their_own_utc_offset_and_non_ascii_survives(self):
        local = datetime(2026, 3, 4, 7, 36, 7, tzinfo=timezone(timedelta(hours=5, minutes=30)))
        text = render_on(OnResult(False, local, "/home/ő/prompt-logs", leftovers=(Leftover("/ő/≥", "move it aside"),)))
        self.assertIn("since 2026-03-04 07:36:07 +0530.", text)
        self.assertIn("/home/ő/prompt-logs", text)
        self.assertIn("  /ő/≥: move it aside\n", text)


class FullStatusTest(unittest.TestCase):
    def test_all_fields_keep_their_order_and_lists_keep_input_order_under_one_heading(self):
        second = Leftover("/b", "delete it")
        text = render_status(
            _status(
                on_at=ON_AT,
                last_sync_at=SYNC_AT,
                archived_session_count=3,
                cursor_recorded_since_on=False,
                price_file_problem="prices.json is unreadable",
                sync_state_reset=True,
                unreadable=(UnreadableItem("/a", "bad"), UnreadableItem("/c", "")),
                leftovers=(LEFTOVER, second),
                probe=PROBE_FAIL,
            )
        )
        self.assertEqual(
            text,
            "Session logging is on since %s.\nLog root: %s\nLast sync: %s\nArchived sessions: 3\n"
            "Unreadable or skipped items:\n  /a: bad\n  /c\n"
            "Cursor has recorded no hook events since logging was turned on.\n"
            "Price file problem: prices.json is unreadable\n"
            "The sync state was empty or corrupt and has been rebuilt.\n"
            "%s; fix: %s\n%s\n  %s: %s\n  /b: delete it\n"
            % (ON_AT_TEXT, ROOT, SYNC_AT_TEXT, PROBE_FAILED, PROBE_FAIL.fix, LEFTOVERS, LEFTOVER.path, LEFTOVER.fix),
        )


class UsageRenderingTest(unittest.TestCase):
    def test_usage_result_text_printed_unchanged(self):
        report = "Session x\nTotal ≥ 1.20 USD\nKérem ő\n"
        self.assertEqual(render_usage(UsageResult(report_text=report)), report)
        for odd in ("", "no newline", "\n\n lead\r\ntrail  \n\n\n"):
            self.assertEqual(render_usage(UsageResult(report_text=odd)), odd)


if __name__ == "__main__":
    unittest.main()
