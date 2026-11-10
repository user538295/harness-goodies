"""Presentation module (S67): pure result -> stdout text, each set field with its fixed wording, each unset field absent."""
import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from sessionlog.archive_port import ReportTotals
from sessionlog.commands import (LatestSession, ListedSession, ListResult, OffResult, OnResult, StatusResult, UnreadableItem, UsageData,
                                 UsageResult, UsageRows)
from sessionlog.leftover_port import Leftover
from sessionlog.model import Harness, Prompt, Session, SubAgent
from sessionlog.pricing import PricingRules
from sessionlog.probe_port import ProbeResult
from sessionlog.presentation import LEFTOVERS_HEADING as LEFTOVERS
from sessionlog.presentation import (PROBE_FAILED, render_list, render_list_json, render_off, render_on, render_status, render_usage,
                                     render_usage_json)
from tests.test_pricing import Book, request

ON_AT = datetime(2026, 3, 4, 5, 6, 7, tzinfo=timezone.utc)
SYNC_AT = datetime(2026, 3, 5, 8, 9, 10, tzinfo=timezone.utc)
ROOT = "/home/u/.claude/prompt-logs"
LEFTOVER = Leftover("/home/u/.claude/scripts/prompt_log_hook.sh", "move it aside")
PROBE_FAIL = ProbeResult(ok=False, fix="install Python 3.14 and make python3 resolve to it")
ON_AT_TEXT = "2026-03-04 05:06:07 +0000"
SYNC_AT_TEXT = "2026-03-05 08:09:10 +0000"
SESSION_ID = "abc"
SESSION_SOURCE = "/home/u/.claude/projects/-work-proj/abc.jsonl"
PROMPT_LOG = "/home/u/.claude/prompt-logs/proj-0123456789ab/session_2026-03-04_05-06-07_abc.md"


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
        for absent in ("Last sync", "Unreadable", "Cursor", "Price file", "rebuilt", "leftover", "python3", "Latest archived session"):
            self.assertNotIn(absent, text)

    def test_status_on_time_read_from_file_time_is_marked(self):
        text = render_status(_status(on_at=ON_AT, on_at_from_file_time=True))
        self.assertIn("Session logging is on since %s (from the file time: .log_enabled could not be parsed).\n" % ON_AT_TEXT, text)
        self.assertNotIn("file time", render_status(_status(on_at=ON_AT)))

    def test_status_names_the_latest_archived_session_and_leaves_out_a_source_that_is_gone(self):
        text = render_status(_status(archived_session_count=1, latest_session=LatestSession(SESSION_ID, PROMPT_LOG)))
        self.assertIn("Archived sessions: 1\nLatest archived session: %s\nPrompt log: %s\n" % (SESSION_ID, PROMPT_LOG), text)
        self.assertNotIn("Session source", text)


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
                latest_session=LatestSession(SESSION_ID, PROMPT_LOG, SESSION_SOURCE),
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
            "Latest archived session: %s\nSession source: %s\nPrompt log: %s\n"
            "Unreadable or skipped items:\n  /a: bad\n  /c\n"
            "Cursor has recorded no hook events since logging was turned on.\n"
            "Price file problem: prices.json is unreadable\n"
            "The sync state was empty or corrupt and has been rebuilt.\n"
            "%s; fix: %s\n%s\n  %s: %s\n  /b: delete it\n"
            % (ON_AT_TEXT, ROOT, SYNC_AT_TEXT, SESSION_ID, SESSION_SOURCE, PROMPT_LOG, PROBE_FAILED, PROBE_FAIL.fix, LEFTOVERS, LEFTOVER.path,
               LEFTOVER.fix),
        )


class UsageRenderingTest(unittest.TestCase):
    def test_usage_result_text_printed_unchanged(self):
        report = "Session x\nTotal ≥ 1.20 USD\nKérem ő\n"
        self.assertEqual(render_usage(UsageResult(report_text=report)), report)
        for odd in ("", "no newline", "\n\n lead\r\ntrail  \n\n\n"):
            self.assertEqual(render_usage(UsageResult(report_text=odd)), odd)


TOTALS = ReportTotals(working_seconds=11280, input_tokens=190200, output_tokens=121300, reasoning_tokens=None, cache_read_tokens=27805100,
                      cache_write_tokens=1046900, total_tokens=29163500, price_cents=2795, price_complete=True,
                      models=("claude-opus-5-5", "claude-opus-5-5:fast"))
PLUS_TWO = timezone(timedelta(hours=2))


def _listed(session_id="abc", started_at=ON_AT, source=SESSION_SOURCE, log_path=PROMPT_LOG, prompts=("first", "second")):
    return ListedSession("claude", session_id, "/work/proj", started_at, SYNC_AT, source, log_path, prompts)


class UsageJsonRenderingTest(unittest.TestCase):
    def test_an_archived_session_renders_its_numbers_source_and_log(self):
        data = UsageData("claude", SESSION_ID, "/work/proj", SESSION_SOURCE, PROMPT_LOG, TOTALS)
        self.assertEqual({
            "harness": "claude", "id": SESSION_ID, "projectDir": "/work/proj", "path": SESSION_SOURCE, "log": PROMPT_LOG,
            "agentTimeSeconds": 11280,
            "tokens": {"input": 190200, "output": 121300, "reasoning": None, "cacheRead": 27805100, "cacheWrite": 1046900, "total": 29163500},
            "costUsd": 27.95, "costComplete": True, "models": ["claude-opus-5-5", "claude-opus-5-5:fast"], "prompts": None,
            "subAgents": None,
        }, json.loads(render_usage_json(UsageResult("report\n", data))))

    def test_a_live_session_without_source_log_or_project_renders_nulls_reasoning_and_an_incomplete_cost(self):
        totals = replace(TOTALS, reasoning_tokens=42, price_cents=5, price_complete=False)
        rendered = render_usage_json(UsageResult("report\n", UsageData("omp", SESSION_ID, None, None, None, totals)))
        parsed = json.loads(rendered)
        self.assertEqual((None, None, None, 42, 0.05, False), (parsed["projectDir"], parsed["path"], parsed["log"],
                                                              parsed["tokens"]["reasoning"], parsed["costUsd"], parsed["costComplete"]))
        self.assertTrue(rendered.endswith("}\n"))

    def test_rows_render_in_report_order_with_utc_times_full_texts_and_exact_costs(self):
        answered = Prompt(submitted_at=ON_AT.astimezone(PLUS_TWO), text="first <b>line</b>\nsecond line",
                          reply_at=ON_AT + timedelta(seconds=5), working_time=timedelta(seconds=4.5),
                          requests=(request(input_tokens=1000, output_tokens=500, cache_read_tokens=2000, cache_write_5m_tokens=100),))
        unanswered = Prompt(submitted_at=ON_AT + timedelta(minutes=1), text="open", requests=(request("unpriced", input_tokens=5),),
                            after_reset=True, written_paths=("docs/a.md", "docs/b.md"))
        child = SubAgent("child", "explore", ON_AT + timedelta(seconds=20), requests=(request(output_tokens=7, recorded_cost_usd=0.25),))
        parent = SubAgent("parent", "worker", ON_AT + timedelta(seconds=10), requests=(request(recorded_cost_usd=0.5),),
                          tokens_not_recorded=True, sub_agents=(child,))
        session = Session(harness=Harness.CURSOR, session_id=SESSION_ID, started_at=ON_AT, prompts=(answered, unanswered),
                          sub_agents=(parent,))
        rows = UsageRows.of(session, PricingRules(Book()).build(session))
        parsed = json.loads(render_usage_json(UsageResult("report\n", UsageData("cursor", SESSION_ID, None, None, None, TOTALS, rows))))
        self.assertEqual([
            {"at": "2026-03-04T05:06:07+00:00", "replyAt": "2026-03-04T05:06:12+00:00", "text": "first <b>line</b>\nsecond line",
             "agentTimeSeconds": 4,
             "tokens": {"input": 1000, "output": 500, "reasoning": None, "cacheRead": 2000, "cacheWrite": 100, "total": 3600},
             "costUsd": 0.02325, "costComplete": True, "models": ["known"], "afterReset": False, "writtenPaths": []},
            {"at": "2026-03-04T05:07:07+00:00", "replyAt": None, "text": "open", "agentTimeSeconds": 0,
             "tokens": {"input": 5, "output": 0, "reasoning": None, "cacheRead": 0, "cacheWrite": 0, "total": 5},
             "costUsd": 0.0, "costComplete": False, "models": ["unpriced"], "afterReset": True,
             "writtenPaths": ["docs/a.md", "docs/b.md"]},
        ], parsed["prompts"])
        self.assertEqual([
            {"id": "parent", "type": "worker", "startedAt": "2026-03-04T05:06:17+00:00", "depth": 0, "tokens": None,
             "costUsd": 0.5, "costComplete": False, "models": ["known"]},
            {"id": "child", "type": "explore", "startedAt": "2026-03-04T05:06:27+00:00", "depth": 1,
             "tokens": {"input": 0, "output": 7, "reasoning": None, "cacheRead": 0, "cacheWrite": 0, "total": 7},
             "costUsd": 0.25, "costComplete": True, "models": ["known"]},
        ], parsed["subAgents"])


class ListRenderingTest(unittest.TestCase):
    def test_list_json_is_an_array_of_sessions_with_utc_times_and_full_prompts(self):
        local_start = ON_AT.astimezone(PLUS_TWO)
        result = ListResult("/work/proj", (_listed(started_at=local_start, prompts=("Kérem ő\nsecond line",)),
                                           _listed("def", source=None, log_path=None, prompts=())))
        self.assertEqual([
            {"harness": "claude", "id": "abc", "projectDir": "/work/proj", "startedAt": "2026-03-04T05:06:07+00:00",
             "lastActivityAt": "2026-03-05T08:09:10+00:00", "path": SESSION_SOURCE, "log": PROMPT_LOG, "prompts": ["Kérem ő\nsecond line"]},
            {"harness": "claude", "id": "def", "projectDir": "/work/proj", "startedAt": "2026-03-04T05:06:07+00:00",
             "lastActivityAt": "2026-03-05T08:09:10+00:00", "path": None, "log": None, "prompts": []},
        ], json.loads(render_list_json(result)))
        self.assertEqual([], json.loads(render_list_json(ListResult("/work/proj", ()))))

    def test_list_text_has_one_line_per_session_in_local_time_with_its_log_else_its_source(self):
        result = ListResult("/work/proj", (_listed(), _listed("def", log_path=None, prompts=("one",)),
                                           _listed("ghi", source=None, log_path=None, prompts=())))
        self.assertEqual(
            "2026-03-04 07:06:07 +0200  abc  2 prompts  %s\n"
            "2026-03-04 07:06:07 +0200  def  1 prompt  %s\n"
            "2026-03-04 07:06:07 +0200  ghi  0 prompts  -\n" % (PROMPT_LOG, SESSION_SOURCE),
            render_list(result, lambda moment: moment.astimezone(PLUS_TWO)))

    def test_an_empty_list_says_so_in_text(self):
        self.assertEqual("No session for this project (/work/proj).\n", render_list(ListResult("/work/proj", ()), lambda moment: moment))


if __name__ == "__main__":
    unittest.main()
