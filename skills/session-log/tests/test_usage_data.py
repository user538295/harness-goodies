"""`usage` as data (C1): the numbers of the TOTAL block, parsed from the saved report of an archived session and taken from the
report built now for a live one, with the session's own source and archived log; and the prompt and sub-agent rows of the
session read now (a live one's, else the stored session of an archived one, read only when data is asked for)."""
import json
import os
from datetime import timedelta

from sessionlog.archive_port import ReportTotals
from sessionlog.command_error import CommandError
from sessionlog.model import Harness
from sessionlog.reader_port import ReadResult, UnreadableSource
from tests.test_list_command import ListTestCase
from tests.test_sync import ON_AT, ONE_DAY
from tests.test_usage_target import ONE_SECOND, PROJECT, _reply, _user

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


class UsageRowsTest(ListTestCase):
    def _prompts(self, data):
        return [(prompt.text, prompt.submitted_at, prompt.reply_at, row.working_time) for prompt, row in data.rows.prompts]

    def test_a_live_session_gives_its_prompt_rows_in_order_and_its_sub_agent_rows_with_start_and_depth(self):
        self._install_records("live", [_user(ON_AT + ONE_DAY, PROJECT, "first"), _reply(ON_AT + ONE_DAY + ONE_SECOND, "m0"),
                                       _user(ON_AT + 2 * ONE_DAY, PROJECT, "second, unanswered")])
        self._install_sub_agent("live", "worker", ON_AT + ONE_DAY)
        data = self.commands.usage_latest(PROJECT, with_data=True).data
        self.assertEqual([("first", ON_AT + ONE_DAY, ON_AT + ONE_DAY + ONE_SECOND, ONE_SECOND),
                          ("second, unanswered", ON_AT + 2 * ONE_DAY, None, timedelta(0))], self._prompts(data))
        self.assertEqual([("agent-worker", 0, ON_AT + ONE_DAY, 2)],
                         [(row.agent_id, row.depth, row.started_at, row.total_tokens) for row in data.rows.sub_agents])
        self.assertEqual(data.totals.total_tokens,
                         sum(row.total_tokens for row in (*(row for _, row in data.rows.prompts), *data.rows.sub_agents)))

    def test_an_archived_session_gives_the_rows_of_its_stored_session_read_now_beside_its_saved_totals(self):
        source = self._install("archived", prompts=[ON_AT + ONE_DAY, ON_AT + 2 * ONE_DAY])
        self._turn_on_and_sync()
        self._turn_off()
        with open(source, "a", encoding="utf-8") as handle:  # logging is off, so the saved report keeps two prompts
            handle.write(json.dumps(_user(ON_AT + 3 * ONE_DAY, PROJECT, "prompt 2")) + "\n")
        data = self.commands.usage_of("archived", with_data=True).data
        self.assertEqual(2, data.totals.working_seconds)
        self.assertEqual(["prompt 0", "prompt 1", "prompt 2"], [text for text, *_ in self._prompts(data)])
        self.assertEqual((), data.rows.sub_agents)
        self.assertEqual([], self.commands.warnings)

    def test_an_archived_session_whose_source_is_gone_gives_no_rows_and_still_its_saved_totals(self):
        source = self._install("archived", prompts=[ON_AT + ONE_DAY, ON_AT + 2 * ONE_DAY])
        self._turn_on_and_sync()
        self._turn_off()
        os.remove(source)
        data = self.commands.usage_of("archived", with_data=True).data
        self.assertEqual((TWO_PROMPTS, None), (data.totals, data.rows))
        self.assertEqual([], self.commands.warnings)

    def test_an_archived_session_whose_source_cannot_be_read_gives_no_rows_and_a_warning(self):
        source = self._install("archived", prompts=[ON_AT + ONE_DAY])
        self._turn_on_and_sync()
        self._turn_off()
        self.reader.read = lambda session_key, units: ReadResult(unreadable=(UnreadableSource(Harness.CLAUDE, source, "denied"),))
        data = self.commands.usage_of("archived", with_data=True).data
        self.assertIsNone(data.rows)
        self.assertEqual(["session archived cannot be read (%s: denied), so its data holds no prompt or sub-agent rows" % source],
                         self.commands.warnings)

    def test_the_text_report_of_an_archived_session_reads_nothing_from_the_store(self):
        self._install("archived", prompts=[ON_AT + ONE_DAY])
        self._turn_on_and_sync()
        self._turn_off()
        self.reader.read = lambda session_key, units: self.fail("the store was read")
        self.assertIsNone(self.commands.usage_of("archived").data)
