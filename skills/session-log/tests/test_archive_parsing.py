"""C3 LogUsageWriter as the format owner read back: an archived log gives its start and prompt texts, a saved report its TOTAL
numbers; each parser is pinned by a round trip through the renderer it reads."""
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sessionlog.archive_port import ArchivedLog, ReportTotals
from sessionlog.log_usage_writer import LogUsageWriter
from sessionlog.model import Harness, Prompt, RequestUsage, SubAgent
from tests.test_log_usage_writer import MILLION, START, UTC, pricing_report, replied, session, writer

ZONES = (timezone.utc, timezone(timedelta(hours=5, minutes=30)), timezone(timedelta(hours=-3, minutes=-30)),
         timezone(timedelta(hours=14)))
LONG_WORK = timedelta(hours=101, minutes=2, seconds=3)
LONG_WORK_SECONDS = 101 * 3600 + 2 * 60 + 3


def _prompts():
    return (Prompt(START, "first line\nsecond line\n\n## not a heading", START + timedelta(seconds=5), "reply\n\n### x response",
                   timedelta(seconds=5)),
            replied(1, "b", "m1", "high"), replied(2, "c", "m2", "low"),
            Prompt(START + timedelta(minutes=3), ""),
            Prompt(START + timedelta(minutes=4), "ends with a line break\n"),
            Prompt(START + timedelta(minutes=5), "last, unanswered"))


class LogParsingTest(unittest.TestCase):
    def test_a_rendered_log_gives_back_its_start_and_its_prompt_texts_in_every_zone(self):
        data = session(prompts=_prompts())
        for zone in ZONES:
            with self.subTest(zone=zone):
                parsed = writer(zone=zone).parse_log(writer(zone=zone).render_log(data))
                self.assertEqual(ArchivedLog(START, tuple(prompt.text for prompt in data.prompts)), parsed)
                self.assertEqual(zone.utcoffset(None), parsed.started_at.utcoffset())

    def test_a_log_without_prompts_and_an_orphan_log_parse_too(self):
        self.assertEqual(ArchivedLog(START, ()), writer().parse_log(writer().render_log(session())))
        orphan = session(prompts=(Prompt(START, "p"),), orphan_of_parent_id="parent")
        self.assertEqual(ArchivedLog(START, ("p",)), writer().parse_log(writer().render_log(orphan)))

    def test_text_that_is_not_a_log_is_none(self):
        valid = writer().render_log(session(prompts=(Prompt(START, "p"),)))
        for text in ("", "random text\n", valid.replace("# Session log", "# Other"), valid.rstrip("\n"),
                     valid.replace("- start: 2026-08-28 10:00:00 UTC", "- start: yesterday")):
            with self.subTest(text=text):
                self.assertIsNone(writer().parse_log(text))

    def test_read_log_reads_the_saved_log_and_is_none_when_it_is_absent_or_not_utf8(self):
        with tempfile.TemporaryDirectory() as root:
            out = LogUsageWriter(root, lambda moment: moment.astimezone(UTC))
            data = session(prompts=(Prompt(START, "\u0151 prompt"),))
            path = out.new_log_path(data)
            self.assertIsNone(out.read_log(path))
            self.assertTrue(out.write(path, out.render_log(data), "report\n").written)
            self.assertEqual(ArchivedLog(START, ("\u0151 prompt",)), out.read_log(path))
            Path(path).write_bytes(b"\xff\xfe")
            self.assertIsNone(out.read_log(path))


REPORT_MODELS = ("known", "unpriced")  # the models of `ReportParsingTest._report`


def _totals(total, models=REPORT_MODELS):
    return ReportTotals(working_seconds=int(total.working_time.total_seconds()), input_tokens=total.input_tokens,
                        output_tokens=total.output_tokens, reasoning_tokens=total.reasoning_tokens,
                        cache_read_tokens=total.cache_read_tokens, cache_write_tokens=total.cache_write_tokens,
                        total_tokens=total.total_tokens, price_cents=total.price_cents, price_complete=not total.price_incomplete,
                        models=models)


class ReportParsingTest(unittest.TestCase):
    def _report(self, harness=Harness.CLAUDE):
        known = (RequestUsage("known", MILLION, 2 * MILLION, 3, 4, 5, reasoning_tokens=7),)
        prompts = (Prompt(START, "a", START + timedelta(seconds=65), "r", timedelta(seconds=65), known),
                   Prompt(START + timedelta(minutes=2), "b", requests=(RequestUsage("unpriced", 11, 12, 13, 14, 15),)))
        agent = SubAgent("c1", "explore", START, timedelta(seconds=9), (RequestUsage("known", 1, 1, 0, 0, 0),))
        return pricing_report(*prompts, harness=harness, agents=(agent,))

    def test_the_parsed_totals_equal_the_report_totals_for_every_harness(self):
        for harness in Harness:
            with self.subTest(harness=harness):
                report = self._report(harness)
                self.assertEqual(_totals(report.total), writer().parse_report(writer().render_report(report)))

    def test_reasoning_incomplete_and_api_equivalent_variants_round_trip(self):
        base = self._report().total
        for changes in (dict(reasoning_tokens=None), dict(reasoning_tokens=0), dict(reasoning_tokens=42),
                        dict(price_incomplete=True), dict(price_incomplete=False, api_equivalent=True),
                        dict(price_incomplete=True, api_equivalent=True, label="tokens: main session only"),
                        dict(working_time=LONG_WORK), dict(price_cents=0), dict(price_cents=123456789)):
            with self.subTest(changes=changes):
                report = replace(self._report(), total=replace(base, **changes))
                parsed = writer().parse_report(writer().render_report(report))
                self.assertEqual(_totals(report.total), parsed)
        long = replace(self._report(), total=replace(base, working_time=LONG_WORK))
        self.assertEqual(LONG_WORK_SECONDS, writer().parse_report(writer().render_report(long)).working_seconds)

    def test_the_parsed_totals_name_every_model_of_the_report_and_none_for_a_report_without_one(self):
        base = self._report().total
        for model_names, models in (("claude-opus-5-5:fast+gpt-5.5", ("claude-opus-5-5:fast", "gpt-5.5")), ("-", ())):
            with self.subTest(model_names=model_names):
                report = replace(self._report(), total=replace(base, model_names=model_names))
                self.assertEqual(models, writer().parse_report(writer().render_report(report)).models)

    def test_totals_of_a_usage_report_equal_its_total_block(self):
        report = self._report(Harness.OMP)
        self.assertEqual(_totals(report.total), ReportTotals.of(report.total))

    def test_text_without_a_readable_total_block_is_none(self):
        valid = writer().render_report(self._report())
        for text in ("", "report abc (1 requests)", valid.replace("TOTAL (", "SUM ("), valid.replace("working time: ", "time: "),
                     valid.rsplit("est. used token:", 1)[0]):
            with self.subTest(text=text[-40:]):
                self.assertIsNone(writer().parse_report(text))


if __name__ == "__main__":
    unittest.main()
