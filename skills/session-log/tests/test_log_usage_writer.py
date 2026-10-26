"""C3 LogUsageWriter: path rules, rendering and atomic writes (S33, S38, S48, S53, S61)."""
import ntpath
import os
import posixpath
import stat
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from unittest.mock import patch

from sessionlog.log_usage_writer import LogUsageWriter
from sessionlog.model import Harness, Prompt, RequestUsage, Session, SubAgent
from sessionlog.pricing import PARTIAL, PRICED, Price, PricingRules, PromptRow, Rates, Total, UsageReport
from sessionlog.safe_paths import FOLDER_LINK_REASON, LINK_REASON

UTC = timezone.utc
START = datetime(2026, 8, 28, 10, 0, 0, tzinfo=UTC)
HASH_LENGTH = 12
SHIFT_AT = datetime(2026, 8, 28, 11, 0, 0)
SLUG_MAX = 64
POSIX_ONLY = unittest.skipIf(os.name == "nt", "POSIX file modes")
LINKS_ONLY = unittest.skipUnless(hasattr(os, "symlink") and os.name != "nt", "needs symlinks")
STALE_SECONDS = 3600
MILLION = 1_000_000
NOW = 100000.0


class Book:
    def rate_for(self, model_name, fast, prompt_tokens):
        return Rates(10, 20, 1, 12.5, 20) if model_name == "known" else None

    def problem(self):
        return None


def session(cwd="/work/proj", prompts=(), **changes):
    values = dict(harness=Harness.CLAUDE, session_id="abc", started_at=START, project_dir=cwd, prompts=tuple(prompts))
    values.update(changes)
    return Session(**values)


def writer(root="/logs", zone=UTC, path_module=posixpath, **extra):
    return LogUsageWriter(root, lambda moment: moment.astimezone(zone), path_module, **extra)


def slug_of(path_module, cwd):
    return path_module.basename(path_module.dirname(writer(path_module=path_module).new_log_path(session(cwd))))


def row(head="h", at=START, working=timedelta(0), price=None):
    return PromptRow(input_tokens=0, output_tokens=0, cache_write_tokens=0, cache_read_tokens=0, total_tokens=0,
                     price=price or Price(0, PRICED), model_names="m", effort=None, at=at, working_time=working,
                     prompt_head=head)


def report_of(*rows, project="/p"):
    total = Total(total_tokens=0, price_cents=0, price_incomplete=False, api_equivalent=False,
                  working_time=timedelta(seconds=sum(int(each.working_time.total_seconds()) for each in rows)),
                  request_count=len(rows), sub_agent_count=0, input_tokens=0, output_tokens=0, cache_read_tokens=0,
                  cache_write_tokens=0, model_names="m", effort=None)
    return UsageReport(harness=Harness.CLAUDE, session_id="abc", project_dir=project, prompts=tuple(rows),
                       sub_agents=(), total=total)


class SlugTests(unittest.TestCase):
    def test_slug_replaces_other_chars_cuts_64_root_and_unknown_fallbacks(self):
        self.assertRegex(slug_of(posixpath, "/work/my proj.v2"), hashed(r"my-proj\.v2"))
        self.assertRegex(slug_of(posixpath, "/work/\u00e9\u00e9\u00fc"), hashed("---"))
        self.assertRegex(slug_of(posixpath, "/work/" + "x" * 100), hashed("x{%d}" % SLUG_MAX))
        self.assertRegex(slug_of(posixpath, "/"), hashed("root"))
        self.assertRegex(slug_of(ntpath, "C:\\"), hashed("root"))
        self.assertRegex(slug_of(ntpath, "C:\\Users\\Me\\My App"), hashed("My-App"))
        self.assertEqual(slug_of(posixpath, None), "unknown")
        self.assertEqual(slug_of(ntpath, "C:\\Work\\App").split("-")[-1], slug_of(ntpath, "c:\\work\\APP").split("-")[-1])

    def test_slug_length_boundary_64_kept_65_cut(self):
        self.assertRegex(slug_of(posixpath, "/w/" + "x" * SLUG_MAX), hashed("x{%d}" % SLUG_MAX))
        self.assertRegex(slug_of(posixpath, "/w/" + "x" * (SLUG_MAX + 1)), hashed("x{%d}" % SLUG_MAX))

    def test_slug_keeps_dots_so_a_project_folder_matches_the_old_log_key(self):
        """Pinned from the old native hook's workspace_key (git ae476af): `.` was a kept character, a leading dot included."""
        self.assertEqual(slug_of(posixpath, "/Users/me/dev/my.app"), "my.app-49e58f1bee07")
        self.assertEqual(slug_of(posixpath, "/Users/me/.claude"), ".claude-0bd83d077606")
        self.assertRegex(slug_of(posixpath, "/w/" + "." * 70), hashed(r"\." + "{%d}" % SLUG_MAX))

    def test_a_dot_dot_basename_stays_one_safe_folder_name(self):
        folder = slug_of(posixpath, "/a/..")
        self.assertRegex(folder, hashed(r"\.\."))
        self.assertNotIn(folder, (".", ".."))

    def test_slug_hash_is_pinned_sha256_prefix_of_the_cwd(self):
        self.assertEqual(slug_of(posixpath, "/work/proj"), "proj-277ae841cc3b")

    def test_slug_hash_of_a_non_ascii_cwd_is_pinned_utf8(self):
        self.assertEqual(slug_of(posixpath, "/work/\u0151-proj"), "--proj-bc3a35837d44")

    def test_slug_hash_of_nt_paths_is_pinned_after_ntpath_normcase(self):
        self.assertEqual(slug_of(ntpath, "C:/Work/App"), "App-f47250f2320c")
        self.assertEqual(slug_of(ntpath, "c:\\work\\app"), "app-f47250f2320c")

    def test_slug_hash_is_of_cwd_as_recorded_and_path_has_utc_name(self):
        path = writer().new_log_path(session("/a/proj"))
        self.assertEqual(posixpath.basename(path), "session_2026-08-28_10-00-00_abc.md")
        self.assertNotEqual(slug_of(posixpath, "/a/proj"), slug_of(posixpath, "/b/proj"))
        self.assertTrue(path.startswith(posixpath.join("/logs", slug_of(posixpath, "/a/proj"))))

    def test_unknown_cwd_folder_under_log_root(self):
        self.assertEqual(writer("/logs").new_log_path(session(None)),
                         posixpath.join("/logs", "unknown", "session_2026-08-28_10-00-00_abc.md"))

    def test_path_module_drives_the_separator(self):
        path = writer("C:\\logs", path_module=ntpath).new_log_path(session("C:\\w\\p"))
        self.assertEqual(path, "C:\\logs\\%s\\session_2026-08-28_10-00-00_abc.md" % slug_of(ntpath, "C:\\w\\p"))

    def test_file_name_is_utc_even_for_a_plus_14_start(self):
        started = datetime(2026, 8, 29, 0, 30, 0, tzinfo=timezone(timedelta(hours=14)))
        path = writer(zone=timezone(timedelta(hours=14))).new_log_path(session(started_at=started))
        self.assertEqual(posixpath.basename(path), "session_2026-08-28_10-30-00_abc.md")

    def test_posix_keeps_case_in_the_slug_hash_and_nt_folds_it(self):
        self.assertNotEqual(slug_of(posixpath, "/W/App"), slug_of(posixpath, "/w/App"))
        self.assertEqual(slug_of(ntpath, "C:\\W\\App").split("-")[-1], slug_of(ntpath, "c:\\w\\APP").split("-")[-1])

    def test_lone_surrogate_cwd_does_not_raise(self):
        path = writer().new_log_path(session("/work/\udcff\ud800x"))
        self.assertRegex(posixpath.basename(posixpath.dirname(path)), hashed("--x"))


def hashed(prefix):
    return r"^%s-[0-9a-f]{%d}$" % (prefix, HASH_LENGTH)


def pricing_report(*prompts, harness=Harness.CLAUDE, agents=()):
    return PricingRules(Book()).build(session(prompts=prompts, harness=harness, sub_agents=agents))


def head_of(text):
    lines = writer().render_report(pricing_report(Prompt(submitted_at=START, text=text))).split("\n")
    return next(line for line in lines if line.startswith("1. ")).split('"')[1]


def replied(minute, name, model, effort):
    """A prompt `minute` minutes in, answered 5 s later with one request on `model` at `effort`."""
    at = START + timedelta(minutes=minute)
    return Prompt(at, name, at + timedelta(seconds=5), "reply " + name, timedelta(seconds=5),
                  (RequestUsage(model, 1, 1, 0, 0, 0, effort=effort),))


class Shifting(tzinfo):
    """+2 before SHIFT_AT (UTC), +1 from then on; wall times are derived from UTC."""

    def fromutc(self, moment):
        utc = moment.replace(tzinfo=None)
        return (utc + timedelta(hours=2 if utc < SHIFT_AT else 1)).replace(tzinfo=self)

    def utcoffset(self, moment):
        wall = moment.replace(tzinfo=None)
        return timedelta(hours=2 if wall < SHIFT_AT + timedelta(hours=2) else 1)

    def dst(self, moment):
        return timedelta(0)

    def tzname(self, moment):
        return None


class RenderTests(unittest.TestCase):
    def test_prompt_head_first_60_chars_newlines_tabs_one_space_no_split_multibyte(self):
        text = "first\r\n\tprompt\nwith newline " + "\u00e9" * 80
        report = pricing_report(Prompt(submitted_at=START, text=text))
        lines = writer().render_report(report).split("\n")
        row_line = next(line for line in lines if line.startswith("1. "))
        head = row_line.split('"')[1]
        self.assertEqual(head, "first prompt with newline " + "\u00e9" * 34)
        self.assertEqual(len(head), 60)
        self.assertTrue(lines[lines.index(row_line) + 1].startswith("est. used token: "))

    def test_prompt_head_keeps_a_bare_double_quote(self):
        text = writer().render_report(pricing_report(Prompt(submitted_at=START, text='say "hi"')))
        self.assertIn('1. 10:00:00 (working time 00:00:00) "say "hi""\n', text)

    def test_prompt_head_astral_emoji_at_the_59_60_boundary_is_never_split(self):
        emoji = "\U0001F600"
        self.assertEqual(head_of("a" * 59 + emoji + "tail"), "a" * 59 + emoji)
        self.assertEqual(head_of("a" * 60 + emoji), "a" * 60)

    def test_empty_prompt_head_renders_empty_quotes(self):
        self.assertIn('1. 10:00:00 (working time 00:00:00) ""\n', writer().render_report(report_of(row(head=""))))

    def test_prompt_head_is_printed_verbatim_from_the_hand_made_report(self):
        self.assertIn('"he said \\"hi\\""', writer().render_report(report_of(row(head='he said \\"hi\\"'))))

    def test_report_layout_rows_sub_agents_total_and_price_labels(self):
        request = RequestUsage("known", 1000, 500, 0, 0, 0, effort="high")
        prompt = Prompt(START, "hello", START + timedelta(seconds=11), "hi", timedelta(seconds=11), (request,))
        agent = SubAgent("a1", "Explore", START, timedelta(seconds=3), (RequestUsage("nope", 7, 0, 0, 0, 0),))
        text = writer(zone=timezone(timedelta(hours=2))).render_report(pricing_report(prompt, agents=(agent,)))
        self.assertEqual(text, "\n".join([
            "session: abc",
            "harness: claude",
            "project: /work/proj",
            "times: UTC+2",
            "",
            '1. 12:00:00 (working time 00:00:11) "hello"',
            "est. used token: input: 1000, output: 500, cache_create: 0, cache_read: 0, total_tokens: 1500, "
            "price: $0.02, model: known, effort: high",
            "",
            "sub-agent: Explore (a1), working time: 00:00:03",
            "est. used token: input: 7, output: 0, cache_create: 0, cache_read: 0, total_tokens: 7, "
            "price: unknown, model: nope, effort: -",
            "",
            "TOTAL (1 requests, 1 sub-agents)",
            "working time: 00:00:11",
            "est. used token: input: 1007, output: 500, cache_create: 0, cache_read: 0, total_tokens: 1507, "
            "price: \u2265 $0.02 (incomplete), model: known+nope, effort: high",
            ""]))

    def test_omp_and_opencode_rows_and_total_show_reasoning_between_output_and_cache_create_the_others_do_not(self):
        """Old OMP/OpenCode report layout (git ae476af): `output: N, reasoning: N, cache_write`; a subset of output, so no total changes."""
        prompt = Prompt(START, "hello", requests=(RequestUsage("known", 1000, 500, 0, 0, 0, reasoning_tokens=20),))
        agent = SubAgent("a1", "Explore", START, requests=(RequestUsage("known", 7, 3, 0, 0, 0, reasoning_tokens=2),))
        for harness in (Harness.OMP, Harness.OPENCODE):
            with self.subTest(harness=harness):
                lines = writer().render_report(pricing_report(prompt, harness=harness, agents=(agent,))).split("\n")
                estimates = [line for line in lines if line.startswith("est. used token: ")]
                self.assertEqual(["est. used token: input: 1000, output: 500, reasoning: 20, cache_create: 0",
                                  "est. used token: input: 7, output: 3, reasoning: 2, cache_create: 0",
                                  "est. used token: input: 1007, output: 503, reasoning: 22, cache_create: 0"],
                                 [line[:line.index(", cache_read")] for line in estimates])
                self.assertIn("total_tokens: 1510,", estimates[2])
        claude = writer().render_report(pricing_report(prompt))
        self.assertNotIn("reasoning", claude)

    def test_a_recorded_transcript_path_replaces_the_session_id_line_and_ends_the_subagent_line_as_in_the_old_cursor_report(self):
        """Old Cursor report (git ae476af): `session: <transcript path>` and `sub-agent: <type> (<id>), working time: <t>, jsonl: <path>`."""
        agents = (SubAgent("c1", "explore", START, timedelta(seconds=65), tokens_not_recorded=True, transcript_path="/t/sub/c1.jsonl"),
                  SubAgent("c2", "explore", START, timedelta(seconds=5), tokens_not_recorded=True))
        prompt = Prompt(START, "p", requests=(RequestUsage("known", 1, 1, 0, 0, 0),))
        report = pricing_report(prompt, harness=Harness.CURSOR, agents=agents)
        recorded = writer().render_report(replace(report, transcript_path="/t/main.jsonl")).split("\n")
        self.assertEqual(["session: /t/main.jsonl", "harness: cursor"], recorded[:2])
        self.assertIn("sub-agent: explore (c1), working time: 00:01:05, jsonl: /t/sub/c1.jsonl", recorded)
        self.assertIn("sub-agent: explore (c2), working time: 00:00:05", recorded)
        self.assertEqual("session: abc", writer().render_report(report).split("\n")[0])

    def test_unexpected_price_status_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            writer().render_report(report_of(row(price=Price(5, "bogus"))))

    def test_total_working_time_line_renders_the_total_as_given(self):
        text = writer().render_report(report_of(row(working=timedelta(seconds=3725))))
        self.assertEqual(text.count("(working time 01:02:05)"), 1)
        self.assertIn("\nworking time: 01:02:05\n", text)

    def test_negative_cents_put_the_sign_before_the_dollar(self):
        priced = writer().render_report(report_of(row(price=Price(-195, PRICED))))
        partial = writer().render_report(report_of(row(price=Price(-195, PARTIAL))))
        self.assertIn("price: -$1.95, model", priced)
        self.assertIn("price: \u2265 -$1.95 (incomplete), model", partial)
        self.assertNotIn("$-", priced + partial)

    def test_report_without_prompts_has_no_times_line(self):
        self.assertEqual(writer().render_report(report_of()), "\n".join([
            "session: abc", "harness: claude", "project: /p", "", "",
            "TOTAL (0 requests, 0 sub-agents)", "working time: 00:00:00",
            "est. used token: input: 0, output: 0, cache_create: 0, cache_read: 0, total_tokens: 0, "
            "price: $0.00, model: m, effort: -", ""]))

    def test_naive_to_local_result_is_rejected(self):
        naive = LogUsageWriter("/logs", lambda moment: moment.replace(tzinfo=None), posixpath)
        with self.assertRaisesRegex(ValueError, "to_local must return an aware datetime"):
            naive.render_log(session())
        with self.assertRaisesRegex(ValueError, "to_local must return an aware datetime"):
            naive.render_report(report_of(row()))

    def test_to_local_that_cannot_convert_a_time_falls_back_to_utc_in_the_log_and_the_report(self):
        for error in (OverflowError("date value out of range"), OSError("Invalid argument"), ValueError("year out of range")):
            def to_local(moment, error=error):
                raise error
            failing = LogUsageWriter("/logs", to_local, posixpath)
            with self.subTest(error=type(error).__name__):
                self.assertIn("- start: 2026-08-28 10:00:00 UTC\n", failing.render_log(session()))
                self.assertIn("times: UTC\n", failing.render_report(report_of(row())))

    def test_lone_surrogates_are_scrubbed_in_the_renders_so_printed_equals_saved(self):
        prompt = Prompt(START, "a\ud800b", START + timedelta(seconds=1), "r\udcffs", timedelta(seconds=1))
        data = session(cwd="/p\ud800", prompts=(prompt,))
        log = writer().render_log(data)
        report = writer().render_report(report_of(row(head="h\ud800"), project="/p\udcff"))
        self.assertIn("- project: /p?\n", log)
        self.assertIn("\na?b\n", log)
        self.assertIn("\nr?s\n", log)
        self.assertIn("project: /p?\n", report)
        self.assertIn('"h?"', report)
        with tempfile.TemporaryDirectory() as root:
            out = LogUsageWriter(root, lambda moment: moment.astimezone(UTC))
            path = out.new_log_path(data)
            self.assertTrue(out.write(path, log, report).written)
            self.assertEqual(out.read_report(path), report)
            self.assertEqual(Path(path).read_bytes(), log.encode("utf-8"))

    def test_negative_working_time_is_shown_as_zero(self):
        text = writer().render_report(report_of(row(working=timedelta(seconds=-5))))
        self.assertIn("(working time 00:00:00)", text)

    def test_cursor_subagent_rows_not_recorded_and_total_main_session_only_price_incomplete(self):
        agent = SubAgent("c1", "explore", START, working_time=timedelta(seconds=65), tokens_not_recorded=True)
        report = pricing_report(Prompt(START, "p", requests=(RequestUsage("known", MILLION, 0, 0, 0, 0),)),
                                harness=Harness.CURSOR, agents=(agent,))
        text = writer().render_report(report)
        self.assertIn("sub-agent: explore (c1), working time: 00:01:05\ntokens: not recorded by Cursor\n", text)
        self.assertIn("\nworking time: 00:00:00\ntokens: main session only \u2014 Cursor does not record sub-agent tokens\n"
                      "est. used token: ", text)
        self.assertRegex(text, r"\nest\. used token: [^\n]*price: \u2265 \$10\.00 \(incomplete\) \(API-equivalent\), model: known")

    def test_cursor_prices_labelled_api_equivalent(self):
        prompt = Prompt(START, "p", requests=(RequestUsage("known", MILLION, 0, 0, 0, 0),))
        cursor = writer().render_report(pricing_report(prompt, harness=Harness.CURSOR))
        self.assertRegex(cursor, r'\n1\. [^\n]*"p"\nest\. used token: [^\n]*price: \$10\.00 \(API-equivalent\), model: known, effort: -\n')
        codex = writer().render_report(pricing_report(prompt, harness=Harness.CODEX))
        self.assertIn("price: $10.00 (API-equivalent), model: known", codex)
        claude = writer().render_report(pricing_report(prompt))
        self.assertIn("price: $10.00, model: known", claude)
        self.assertNotIn("API-equivalent", claude)

    def test_log_golden_text(self):
        prompts = (Prompt(START, "first", START + timedelta(seconds=5), "reply one", timedelta(seconds=5)),
                   Prompt(START + timedelta(minutes=1), "second"))
        log = writer().render_log(session(prompts=prompts, orphan_of_parent_id="p1"))
        self.assertEqual(log, "\n".join([
            "# Session log", "- harness: claude", "- session: abc", "- project: /work/proj",
            "- start: 2026-08-28 10:00:00 UTC", "- sub-agent of p1 (parent not found)",
            "", "## 2026-08-28 10:00:00 UTC", "first",
            "", "### 2026-08-28 10:00:05 UTC response", "reply one",
            "", "## 2026-08-28 10:01:00 UTC", "second", ""]))
        self.assertIn("- project: -\n", writer().render_log(session(None)))

    def test_response_header_uses_the_reply_time_not_the_prompt_time(self):
        prompt = Prompt(START, "ask", START + timedelta(minutes=2), "answer", timedelta(minutes=2))
        self.assertIn("### 2026-08-28 10:02:00 UTC response\nanswer", writer().render_log(session(prompts=(prompt,))))

    def test_a_response_notes_the_model_and_effort_switch_from_the_previous_prompt(self):
        """Old Claude log wording (git ae476af prompt_log_lib.sh switch_line): only the part that changed, `→` between the values."""
        log = writer().render_log(session(prompts=(
            replied(0, "a", "claude-sonnet", "high"), replied(1, "b", "claude-fable", "max"), replied(2, "c", "claude-fable", "low"),
            replied(3, "d", "claude-fable", "low"))))
        self.assertEqual(log.count("switched:"), 2)
        self.assertIn("### 2026-08-28 10:01:05 UTC response\nreply b\n\nswitched: model claude-sonnet \u2192 claude-fable, effort high \u2192 max\n", log)
        self.assertIn("### 2026-08-28 10:02:05 UTC response\nreply c\n\nswitched: effort max \u2192 low\n", log)

    def test_the_first_prompt_and_an_unknown_side_never_switch(self):
        log = writer().render_log(session(prompts=(
            replied(0, "a", "m1", None), replied(1, "b", "m1", "high"), replied(2, "c", "m2", None))))
        self.assertEqual(log.count("switched:"), 1)
        self.assertIn("reply c\n\nswitched: model m1 \u2192 m2\n", log)
        self.assertNotIn("effort", log.split("switched:")[1])

    def test_a_prompt_that_used_several_models_is_compared_by_its_last_request(self):
        first = Prompt(START, "a", START, "reply a", timedelta(0), (RequestUsage("m1", 1, 1, 0, 0, 0), RequestUsage("m2", 1, 1, 0, 0, 0)))
        second = replied(1, "b", "m2", None)
        self.assertNotIn("switched:", writer().render_log(session(prompts=(first, second))))

    def test_times_render_local_with_utc_offset_zero_offset_plain_utc(self):
        prompt = Prompt(START, "ask", START + timedelta(minutes=1), "answer", timedelta(minutes=1))
        for hours, minutes, label, clock in (
                (2, 0, "UTC+2", "12:00:00"), (-5, 0, "UTC-5", "05:00:00"),
                (5, 30, "UTC+5:30", "15:30:00"), (0, 0, "UTC", "10:00:00"),
                (-3, -30, "UTC-3:30", "06:30:00"), (5, 45, "UTC+5:45", "15:45:00"),
                (5, 5, "UTC+5:05", "15:05:00"), (0, -30, "UTC-0:30", "09:30:00")):
            with self.subTest(label=label):
                zone = timezone(timedelta(hours=hours, minutes=minutes))
                log = writer(zone=zone).render_log(session(prompts=(prompt,)))
                stamp = "2026-08-28 %s %s" % (clock, label)
                self.assertIn("- start: " + stamp + "\n", log)
                self.assertIn("## " + stamp + "\nask\n", log)
                self.assertIn(" %s response\nanswer" % label, log)
                report = writer(zone=zone).render_report(pricing_report(prompt))
                self.assertIn("times: %s\n" % label, report)
                self.assertIn("1. %s (working" % clock, report)

    def test_sub_minute_offsets_truncate_toward_zero_symmetrically(self):
        for offset, label in ((timedelta(seconds=30), "UTC"), (timedelta(seconds=-30), "UTC"),
                              (timedelta(hours=5, minutes=45, seconds=30), "UTC+5:45"),
                              (-timedelta(hours=3, minutes=30, seconds=30), "UTC-3:30")):
            with self.subTest(offset=offset):
                log = writer(zone=timezone(offset)).render_log(session())
                self.assertRegex(log, r"- start: \S+ \S+ %s\n" % label.replace("+", r"\+"))

    def test_local_date_may_differ_from_the_utc_date_while_the_file_name_stays_utc(self):
        late = START.replace(hour=23)
        target = writer(zone=timezone(timedelta(hours=14)))
        self.assertIn("- start: 2026-08-29 13:00:00 UTC+14\n", target.render_log(session(started_at=late)))
        self.assertTrue(target.new_log_path(session(started_at=late)).endswith("session_2026-08-28_23-00-00_abc.md"))

    def test_row_with_other_offset_adds_its_own_and_log_marks_orphan(self):
        later = START + timedelta(hours=2)
        report = pricing_report(Prompt(START, "a"), Prompt(later, "b"))
        text = writer(zone=Shifting()).render_report(report)
        self.assertIn("times: UTC+2", text)
        self.assertIn('1. 12:00:00 (working time 00:00:00) "a"', text)
        self.assertIn('2. 13:00:00 UTC+1 (working time 00:00:00) "b"', text)
        log = writer().render_log(session(orphan_of_parent_id="p1"))
        self.assertIn("- sub-agent of p1 (parent not found)\n", log)

    def test_log_entries_across_an_offset_change_each_carry_their_own_offset(self):
        later = START + timedelta(hours=2)
        prompts = (Prompt(START, "a", later, "answer", timedelta(hours=2)), Prompt(later, "b"))
        log = writer(zone=Shifting()).render_log(session(prompts=prompts))
        self.assertIn("\n## 2026-08-28 12:00:00 UTC+2\na\n", log)
        self.assertIn("\n### 2026-08-28 13:00:00 UTC+1 response\nanswer\n", log)
        self.assertIn("\n## 2026-08-28 13:00:00 UTC+1\nb\n", log)

    def test_renders_are_lf_only_so_printed_text_equals_saved_text(self):
        prompt = Prompt(START, "x\r\ny\rz", START + timedelta(seconds=1), "r\r\ns", timedelta(seconds=1))
        agent = SubAgent("a\r1", "T\rY", START, timedelta(0), (RequestUsage("known", 1, 1, 0, 0, 0),))
        data = session(cwd="/a\rb", prompts=(prompt,))
        log = writer().render_log(data)
        report = writer().render_report(PricingRules(Book()).build(
            session(cwd="/a\rb", prompts=(prompt,), sub_agents=(agent,))))
        for text in (log, report):
            self.assertNotIn("\r", text)
        self.assertIn("\nx\ny\nz\n", log)
        self.assertIn("\nr\ns\n", log)
        with tempfile.TemporaryDirectory() as root:
            out = LogUsageWriter(root, lambda moment: moment.astimezone(UTC))
            path = out.new_log_path(data)
            self.assertTrue(out.write(path, log, report).written)
            self.assertEqual(out.read_report(path), report)
            self.assertEqual(Path(path).read_bytes(), log.encode("utf-8"))

    def test_single_line_header_fields_collapse_line_breaks_and_tabs_to_one_space(self):
        agent = SubAgent("a\n\t1", "T\r\nY", START, timedelta(0), (RequestUsage("known", 1, 1, 0, 0, 0),))
        data = session(cwd="/a\r\nb\tc", prompts=(Prompt(START, "p"),), sub_agents=(agent,))
        log = writer().render_log(data)
        report = writer().render_report(PricingRules(Book()).build(data))
        self.assertIn("- project: /a b c\n", log)
        self.assertIn("project: /a b c\n", report)
        self.assertIn("sub-agent: T Y (a 1)", report)

    def test_line_breaks_cannot_split_the_orphan_line_or_the_est_rows(self):
        agent = SubAgent("a", "T", START, timedelta(0), (RequestUsage("known", 1, 1, 0, 0, 0),))
        data = session(prompts=(Prompt(START, "p"),), sub_agents=(agent,), orphan_of_parent_id="p\r\n1\t2")
        self.assertIn("- sub-agent of p 1 2 (parent not found)\n", writer().render_log(data))
        report = report_of(row())
        broken = replace(report.total, model_names="m\nx", effort="hi\r\nlo", label="L\n1")
        text = writer().render_report(replace(report, total=broken))
        self.assertIn("\nL 1\n", text)
        self.assertIn("model: m x, effort: hi lo\n", text)
        row_text = writer().render_report(replace(report, prompts=(replace(report.prompts[0], model_names="a\tb",
                                                                           effort="e\nf"),)))
        self.assertIn("model: a b, effort: e f\n", row_text)


class WriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        self.out = self.make()

    def make(self, **extra):
        return LogUsageWriter(self.root, lambda moment: moment.astimezone(UTC), **extra)

    def temps(self, path):
        return [p for p in Path(path).parent.iterdir() if p.name.endswith(".tmp")]

    def test_write_goes_through_unique_temp_then_replace_stale_temp_removed_fresh_kept(self):
        path = self.out.new_log_path(session())
        folder = Path(path).parent
        folder.mkdir(parents=True)
        stale = folder / ".session_x.md.1.deadbeef.tmp"
        fresh = folder / ".session_y.md.2.cafe.tmp"
        other = folder / "notes.tmp"
        for item in (stale, fresh, other):
            item.write_text("x")
        os.utime(stale, (1, 1))
        os.utime(other, (1, 1))
        seen = []
        real_replace = os.replace

        def spy(source, target):
            seen.append((os.path.basename(source), os.path.dirname(source) == os.path.dirname(target), target))
            return real_replace(source, target)

        with patch("sessionlog.log_usage_writer.os.replace", spy):
            outcome = self.out.write(path, "log \u00e9\r\nline\n", "report \u0151\n")
        self.assertTrue(outcome.written)
        self.assertEqual(outcome.path, path)
        self.assertEqual(len(seen), 2)
        for name, same_folder, target in seen:
            self.assertRegex(name, r"^\.%s\.%d\.[0-9a-f]+\.tmp$" % (os.path.basename(target).replace(".", r"\."), os.getpid()))
            self.assertTrue(same_folder)
        self.assertEqual(Path(path).read_bytes(), "log \u00e9\nline\n".encode("utf-8"))
        report = Path(path[:-3] + ".usage.txt")
        self.assertEqual(report.read_bytes(), "report \u0151\n".encode("utf-8"))
        self.assertEqual(self.out.read_report(path), "report \u0151\n")
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())
        self.assertTrue(other.exists())
        self.assertEqual(sorted(p.name for p in self.temps(path)), sorted([fresh.name, other.name]))

    def test_stale_cleanup_keeps_unrelated_files_and_honours_the_exact_hour_boundary(self):
        out = self.make(clock=lambda: NOW)
        path = out.new_log_path(session())
        folder = Path(path).parent
        folder.mkdir(parents=True)
        ages = (STALE_SECONDS - 1, STALE_SECONDS, STALE_SECONDS + 1)
        mine = {age: folder / (".session_a.md.1.ab%d.tmp" % age) for age in ages}
        keep = [folder / ".foo.1.ab.tmp", folder / ".session_a.md.1.ab", folder / "session_a.md", folder / ".session_a.txt.1.ab.tmp"]
        for item in list(mine.values()) + keep:
            item.write_text("x")
        for age, item in mine.items():
            os.utime(item, (NOW - age, NOW - age))
        for item in keep:
            os.utime(item, (1, 1))
        self.assertTrue(out.write(path, "l\n", "r\n").written)
        self.assertTrue(mine[STALE_SECONDS - 1].exists())
        self.assertTrue(mine[STALE_SECONDS].exists())
        self.assertFalse(mine[STALE_SECONDS + 1].exists())
        for item in keep:
            self.assertTrue(item.exists(), item.name)

    def test_stale_cleanup_removes_report_temps_and_keeps_non_hex_or_non_numeric_lookalikes(self):
        out = self.make(clock=lambda: NOW)
        path = out.new_log_path(session())
        folder = Path(path).parent
        folder.mkdir(parents=True)
        stale = folder / ".session_a.usage.txt.1.deadbeef.tmp"
        keep = [folder / ".session_a.md.1.xyz.tmp", folder / ".session_a.md.x.ab.tmp",
                folder / ".session_a.md.1..tmp", folder / ".session_a.usage.txt.1.AB.tmp"]
        for item in [stale] + keep:
            item.write_text("x")
            os.utime(item, (1, 1))
        self.assertTrue(out.write(path, "l\n", "r\n").written)
        self.assertFalse(stale.exists())
        for item in keep:
            self.assertTrue(item.exists(), item.name)

    def test_cleanup_failure_never_blocks_the_write(self):
        path = self.out.new_log_path(session())
        with patch("sessionlog.log_usage_writer.os.listdir", side_effect=PermissionError("denied")):
            self.assertTrue(self.out.write(path, "l\n", "r\n").written)
        Path(path).parent.joinpath(".session_a.md.1.ab.tmp").write_text("x")
        os.utime(Path(path).parent / ".session_a.md.1.ab.tmp", (1, 1))
        with patch("sessionlog.log_usage_writer.os.unlink", side_effect=PermissionError("denied")):
            self.assertTrue(self.out.write(path, "l\n", "r\n").written)

    def test_empty_error_message_still_gives_a_reason_naming_the_error_type(self):
        path = self.out.new_log_path(session())
        with patch("sessionlog.log_usage_writer.make_real_folders", side_effect=OSError()):
            self.assertEqual(self.out.write(path, "l\n", "r\n").reason, "OSError")
        with patch("sessionlog.log_usage_writer.os.replace", side_effect=OSError()):
            outcome = self.out.write(path, "l\n", "r\n")
        self.assertEqual((outcome.written, outcome.path, outcome.reason), (False, path, "OSError"))

    def test_folder_creation_failure_is_an_outcome_not_an_exception(self):
        path = self.out.new_log_path(session())
        Path(path).parent.write_text("i am a file")
        outcome = self.out.write(path, "l\n", "r\n")
        self.assertFalse(outcome.written)
        self.assertEqual(outcome.path, path)
        self.assertTrue(outcome.reason)

    def test_lone_surrogates_are_replaced_not_raised(self):
        path = self.out.new_log_path(session())
        outcome = self.out.write(path, "a\ud800b\n", "c\udcffd\n")
        self.assertTrue(outcome.written)
        self.assertEqual(Path(path).read_bytes(), b"a?b\n")
        self.assertEqual(self.out.read_report(path), "c?d\n")

    def test_log_failure_does_not_skip_the_report_and_names_the_log(self):
        path = self.out.new_log_path(session())
        real = os.replace

        def failing(source, target):
            if target.endswith(".md"):
                raise PermissionError("log locked")
            return real(source, target)

        with patch("sessionlog.log_usage_writer.os.replace", failing):
            outcome = self.out.write(path, "l\n", "r\n")
        self.assertEqual((outcome.written, outcome.path), (False, path))
        self.assertIn("log locked", outcome.reason)
        self.assertEqual(self.out.read_report(path), "r\n")
        self.assertEqual(self.temps(path), [])

    def test_report_failure_keeps_the_log_and_names_the_report(self):
        path = self.out.new_log_path(session())
        real = os.replace

        def failing(source, target):
            if target.endswith(".usage.txt"):
                raise PermissionError("report locked")
            return real(source, target)

        with patch("sessionlog.log_usage_writer.os.replace", failing):
            outcome = self.out.write(path, "l\n", "r\n")
        self.assertEqual((outcome.written, outcome.path), (False, path[:-3] + ".usage.txt"))
        self.assertIn("report locked", outcome.reason)
        self.assertEqual(Path(path).read_text(encoding="utf-8"), "l\n")

    def test_failed_replace_removes_temp_keeps_old_file_and_reports_reason(self):
        path = self.out.new_log_path(session())
        self.assertTrue(self.out.write(path, "old\n", "r\n").written)
        with patch("sessionlog.log_usage_writer.os.replace", side_effect=PermissionError("locked")):
            outcome = self.out.write(path, "new\n", "r2\n")
        self.assertFalse(outcome.written)
        self.assertEqual(outcome.path, path)
        self.assertIn("locked", outcome.reason)
        self.assertEqual(Path(path).read_text(encoding="utf-8"), "old\n")
        self.assertEqual(self.temps(path), [])

    def test_cleanup_unlink_failure_does_not_hide_the_original_error(self):
        path = self.out.new_log_path(session())
        with patch("sessionlog.log_usage_writer.os.replace", side_effect=PermissionError("locked")), \
                patch("sessionlog.log_usage_writer.os.unlink", side_effect=OSError("unlink boom")):
            outcome = self.out.write(path, "l\n", "r\n")
        self.assertFalse(outcome.written)
        self.assertIn("locked", outcome.reason)

    def test_descriptor_is_closed_when_fdopen_fails(self):
        path = self.out.new_log_path(session())
        closed = []
        real_close = os.close

        def spy(descriptor):
            closed.append(descriptor)
            return real_close(descriptor)

        with patch("sessionlog.log_usage_writer.os.fdopen", side_effect=OSError("no handle")), \
                patch("sessionlog.log_usage_writer.os.close", spy):
            outcome = self.out.write(path, "l\n", "r\n")
        self.assertFalse(outcome.written)
        self.assertEqual(len(closed), 2)
        self.assertEqual(self.temps(path), [])

    def test_data_is_fsynced_before_replace(self):
        path = self.out.new_log_path(session())
        events = []
        real_replace = os.replace
        with patch("sessionlog.log_usage_writer.os.fsync", lambda descriptor: events.append("fsync")), \
                patch("sessionlog.log_usage_writer.os.replace",
                      lambda source, target: (events.append("replace"), real_replace(source, target))[1]):
            self.assertTrue(self.out.write(path, "l\n", "r\n").written)
        self.assertEqual(events, ["fsync", "replace", "fsync", "replace"])

    def test_injected_pids_give_different_temp_names_for_the_same_target(self):
        path = self.out.new_log_path(session())
        names = []
        real_replace = os.replace

        def spy(source, target):
            names.append(os.path.basename(source))
            return real_replace(source, target)

        with patch("sessionlog.log_usage_writer.os.replace", spy):
            self.make(pid=lambda: 111).write(path, "l\n", "r\n")
            self.make(pid=lambda: 222).write(path, "l\n", "r\n")
        self.assertEqual(len(names), 4)
        self.assertTrue(all(".111." in name for name in names[:2]))
        self.assertTrue(all(".222." in name for name in names[2:]))

    def test_same_pid_and_target_written_twice_use_different_temp_names(self):
        path = self.out.new_log_path(session())
        names = []
        real_replace = os.replace

        def spy(source, target):
            names.append(os.path.basename(source))
            return real_replace(source, target)

        with patch("sessionlog.log_usage_writer.os.replace", spy):
            for _ in range(2):
                self.make(pid=lambda: 111).write(path, "l\n", "r\n")
        self.assertEqual(len(set(names)), 4)

    @POSIX_ONLY
    def test_overwriting_a_0644_log_and_report_leaves_both_0600(self):
        path = self.out.new_log_path(session())
        report = path[:-3] + ".usage.txt"
        self.assertTrue(self.out.write(path, "old\n", "old\n").written)
        for target in (path, report):
            os.chmod(target, 0o644)
        self.assertTrue(self.out.write(path, "new\n", "new\n").written)
        for target in (path, report):
            self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o600)
            self.assertEqual(Path(target).read_text(encoding="utf-8"), "new\n")

    @POSIX_ONLY
    def test_files_and_temps_are_0600_and_temp_is_complete_at_replace(self):
        path = self.out.new_log_path(session())
        seen = []
        real_replace = os.replace

        def spy(source, target):
            seen.append((stat.S_IMODE(os.stat(source).st_mode), Path(source).read_bytes()))
            return real_replace(source, target)

        with patch("sessionlog.log_usage_writer.os.replace", spy):
            self.assertTrue(self.out.write(path, "log\n", "report\n").written)
        self.assertEqual(seen, [(0o600, b"log\n"), (0o600, b"report\n")])
        for target in (path, path[:-3] + ".usage.txt"):
            self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o600)

    @POSIX_ONLY
    def test_new_folders_are_0700_under_umask_022_and_an_existing_one_keeps_its_mode(self):
        self.addCleanup(os.umask, os.umask(0o022))
        path = self.out.new_log_path(session())
        slug = os.path.dirname(path)
        self.assertTrue(self.out.write(path, "l\n", "r\n").written)
        self.assertEqual(stat.S_IMODE(os.stat(slug).st_mode), 0o700)
        os.chmod(slug, 0o750)
        self.assertTrue(self.out.write(path, "l\n", "r\n").written)
        self.assertEqual(stat.S_IMODE(os.stat(slug).st_mode), 0o750)

    @LINKS_ONLY
    def test_symlinked_slug_folder_is_not_written_through_and_named_by_the_log_path(self):
        path = self.out.new_log_path(session())
        target = os.path.join(self.root, "target")
        os.mkdir(target)
        os.symlink(target, os.path.dirname(path), target_is_directory=True)
        outcome = self.out.write(path, "l\n", "r\n")
        self.assertEqual((outcome.written, outcome.path, outcome.reason), (False, path, FOLDER_LINK_REASON))
        self.assertEqual(os.listdir(target), [])

    def test_a_junctioned_slug_folder_is_not_written_through_and_named_by_the_log_path(self):
        path = self.out.new_log_path(session())
        folder = os.path.dirname(path)
        os.makedirs(folder)
        with patch("sessionlog.safe_paths.os.path.isjunction", side_effect=lambda candidate: candidate == folder):
            outcome = self.out.write(path, "l\n", "r\n")
        self.assertEqual((outcome.written, outcome.path, outcome.reason), (False, path, FOLDER_LINK_REASON))
        self.assertEqual(os.listdir(folder), [])

    @LINKS_ONLY
    def test_symlinked_log_is_skipped_and_the_report_beside_it_is_still_written(self):
        path = self.out.new_log_path(session())
        os.makedirs(os.path.dirname(path))
        target = os.path.join(self.root, "target.md")
        Path(target).write_text("keep\n")
        os.symlink(target, path)
        outcome = self.out.write(path, "l\n", "r\n")
        self.assertEqual((outcome.written, outcome.path, outcome.reason), (False, path, LINK_REASON))
        self.assertEqual(Path(target).read_text(), "keep\n")
        self.assertTrue(os.path.islink(path))
        self.assertEqual(Path(path[:-3] + ".usage.txt").read_text(), "r\n")
        self.assertEqual(self.temps(path), [])

    @LINKS_ONLY
    def test_dangling_symlinked_report_is_not_created_through_and_named_after_the_log_is_written(self):
        path = self.out.new_log_path(session())
        report = path[:-3] + ".usage.txt"
        os.makedirs(os.path.dirname(path))
        missing = os.path.join(self.root, "missing.txt")
        os.symlink(missing, report)
        outcome = self.out.write(path, "l\n", "r\n")
        self.assertEqual((outcome.written, outcome.path, outcome.reason), (False, report, LINK_REASON))
        self.assertEqual(Path(path).read_text(), "l\n")
        self.assertFalse(os.path.exists(missing))

    def test_read_report_absent_is_none(self):
        self.assertIsNone(self.out.read_report(os.path.join(self.root, "none", "session_x.md")))

    def test_read_report_unreadable_or_not_utf8_is_none(self):
        directory = os.path.join(self.root, "session_d.md")
        os.mkdir(directory[:-3] + ".usage.txt")
        self.assertIsNone(self.out.read_report(directory))
        binary = os.path.join(self.root, "session_b.md")
        Path(binary[:-3] + ".usage.txt").write_bytes(b"\xff\xfe\x00bad")
        self.assertIsNone(self.out.read_report(binary))


if __name__ == "__main__":
    unittest.main()
