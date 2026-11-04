"""`usage` as data (C1): the numbers of the TOTAL block, parsed from the saved report of an archived session and taken from the
report built now for a live one, with the session's own source and archived log."""
import os

from sessionlog.archive_port import ReportTotals
from sessionlog.command_error import CommandError
from sessionlog.model import Harness
from tests.test_list_command import ListTestCase
from tests.test_sync import ON_AT, ONE_DAY
from tests.test_usage_target import PROJECT

# `_install` writes one request of 1 input and 1 output token on the unpriced `claude-x` per prompt, answered a second later.
TWO_PROMPTS = ReportTotals(working_seconds=2, input_tokens=2, output_tokens=2, reasoning_tokens=None, cache_read_tokens=0,
                           cache_write_tokens=0, total_tokens=4, price_cents=0, price_complete=False)


class UsageDataTest(ListTestCase):
    def test_an_archived_session_gives_the_numbers_of_its_saved_report_its_source_and_its_log(self):
        source = self._install("archived", prompts=[ON_AT + ONE_DAY, ON_AT + 2 * ONE_DAY])
        self._turn_on_and_sync()
        self._turn_off()
        report_path = self._log_path("archived")[:-len(".md")] + ".usage.txt"
        with open(report_path, "r", encoding="utf-8") as handle:
            saved = handle.read()
        with open(report_path, "w", encoding="utf-8") as handle:  # a saved report is read, not recomputed
            handle.write(saved.replace("input: 2, output: 2,", "input: 7, output: 2,"))

        result = self.commands.usage_of("archived", with_data=True)

        self.assertEqual(Harness.CLAUDE.value, result.data.harness)
        self.assertEqual(("archived", PROJECT, source, self._log_path("archived")),
                         (result.data.session_id, result.data.project_dir, result.data.source, result.data.log_path))
        self.assertEqual(ReportTotals(**{**TWO_PROMPTS.__dict__, "input_tokens": 7}), result.data.totals)

    def test_a_never_archived_session_gives_the_numbers_of_its_live_report_its_source_and_no_log(self):
        source = self._install("live", prompts=[ON_AT + ONE_DAY, ON_AT + 2 * ONE_DAY])
        result = self.commands.usage_latest(PROJECT, with_data=True)
        self.assertEqual(("live", PROJECT, source, None, TWO_PROMPTS),
                         (result.data.session_id, result.data.project_dir, result.data.source, result.data.log_path, result.data.totals))

    def test_the_text_report_carries_no_data_unless_asked(self):
        self._install("live", prompts=[ON_AT + ONE_DAY])
        self.assertIsNone(self.commands.usage_latest(PROJECT).data)

    def test_an_archived_session_whose_report_is_gone_gives_its_live_numbers_and_its_log(self):
        self._install("archived", prompts=[ON_AT + ONE_DAY, ON_AT + 2 * ONE_DAY])
        self._turn_on_and_sync()
        self._turn_off()
        os.remove(self._log_path("archived")[:-len(".md")] + ".usage.txt")
        data = self.commands.usage_of("archived", with_data=True).data
        self.assertEqual((TWO_PROMPTS, self._log_path("archived")), (data.totals, data.log_path))

    def test_a_saved_report_without_a_readable_total_block_is_an_error_as_data_but_still_printed_as_text(self):
        self._install("archived", prompts=[ON_AT + ONE_DAY])
        self._turn_on_and_sync()
        self._turn_off()
        with open(self._log_path("archived")[:-len(".md")] + ".usage.txt", "w", encoding="utf-8") as handle:
            handle.write("an old report\n")
        self.assertEqual("an old report\n", self.commands.usage_of("archived").report_text)
        with self.assertRaisesRegex(CommandError, "the report of session archived holds no TOTAL block"):
            self.commands.usage_of("archived", with_data=True)
