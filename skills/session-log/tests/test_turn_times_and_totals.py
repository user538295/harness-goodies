"""Wrong totals and wrong times (user priority over the plan text): the TOTAL price is the exact sum of the rows rounded once, a turn
works from its prompt to its last completed answer whatever stopped it (OMP aborted, error and steered turns; OpenCode error, length and
unfinished turns), an OpenCode turn does not count the user's wait on a `question`/`confirm`/`ask` tool, OMP's `image-attachment` and
`ultrathink-notice` messages are no prompts, OpenCode prompts are trimmed and empty ones skipped, and the report's prompt head is
tag-stripped and space-collapsed as the old Claude script did."""
import json
import unittest
from datetime import datetime, timedelta, timezone

from sessionlog.model import Harness, Prompt, RequestUsage, Session
from sessionlog.opencode_messages import parse_messages
from sessionlog.pricing import PricingRules, Rates
from tests import omp_records as omp
from tests.opencode_db import BASE as OC_BASE, assistant_data, ms as oc_ms, user_data

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
KNOWN = "known"
HALF_CENT_REQUEST = dict(input_tokens=1000, output_tokens=300)  # at Rates(2, 10): exactly $0.005
FOURTH_OF_A_CENT_REQUEST = dict(input_tokens=1000, output_tokens=100)  # at Rates(2, 10): exactly $0.003
PROMPT_COUNT = 30
STEER_SECONDS = 20


class Book:
    def rate_for(self, model_name, fast, prompt_tokens):
        return Rates(2, 10, 0, 0, 0) if model_name == KNOWN else None

    def problem(self):
        return None


def request(model=KNOWN, **tokens):
    values = dict(model_name=model, input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_write_5m_tokens=0, cache_write_1h_tokens=0)
    values.update(tokens)
    return RequestUsage(**values)


def report_of(*prompt_requests):
    prompts = tuple(Prompt(submitted_at=NOW, text="p", requests=requests) for requests in prompt_requests)
    return PricingRules(Book()).build(Session(harness=Harness.CLAUDE, session_id="s", started_at=NOW, prompts=prompts))


class TotalPriceTest(unittest.TestCase):
    def test_many_rows_that_each_round_up_do_not_inflate_the_total(self):
        result = report_of(*[(request(**HALF_CENT_REQUEST),)] * PROMPT_COUNT)
        self.assertEqual([1] * PROMPT_COUNT, [row.price.cents for row in result.prompts])
        self.assertEqual(15, result.total.price_cents)  # 30 x $0.005 = $0.15, not 30 x $0.01

    def test_rows_that_each_round_down_to_zero_still_add_up_in_the_total(self):
        result = report_of(*[(request(**FOURTH_OF_A_CENT_REQUEST),)] * PROMPT_COUNT)
        self.assertEqual({0}, {row.price.cents for row in result.prompts})
        self.assertEqual(9, result.total.price_cents)  # 30 x $0.003 = $0.09

    def test_the_total_of_a_mixed_report_rounds_the_known_part_once_and_stays_incomplete(self):
        result = report_of((request(**HALF_CENT_REQUEST),), (request(**HALF_CENT_REQUEST),), (request("unknown", input_tokens=5),),
                           (request(**HALF_CENT_REQUEST), request("unknown", input_tokens=5)))
        self.assertEqual(2, result.total.price_cents)  # 3 x $0.005 = $0.015, half-up once
        self.assertTrue(result.total.price_incomplete)

    def test_a_complete_report_is_not_marked_incomplete(self):
        self.assertFalse(report_of((request(**HALF_CENT_REQUEST),)).total.price_incomplete)


class PromptHeadTest(unittest.TestCase):
    def head(self, text):
        return PricingRules(Book()).build(Session(harness=Harness.CLAUDE, session_id="s", started_at=NOW,
                                                  prompts=(Prompt(submitted_at=NOW, text=text),))).prompts[0].prompt_head

    def test_tags_are_stripped_and_runs_of_spaces_collapse(self):
        self.assertEqual("spaced text with tabs", self.head("<Role>   spaced   text\twith\r\ntabs"))

    def test_a_slash_command_head_loses_its_tags(self):
        self.assertEqual("/commit fix the thing", self.head("<command-name>/commit</command-name>\n<command-args>fix the thing</command-args>"))

    def test_the_cut_comes_after_the_cleaning_and_never_ends_in_a_space(self):
        self.assertEqual("a" * 59, self.head("<tag>" + "a" * 59 + " b"))

    def test_a_prompt_of_tags_only_has_an_empty_head(self):
        self.assertEqual("", self.head("<a><b>"))


class OmpTurnTimeTest(unittest.TestCase):
    def test_an_aborted_turn_works_until_its_last_completed_message_and_has_no_reply(self):
        (prompt,) = omp.parse(omp.user_record(10), omp.assistant_record(11, stop="toolUse", done=4),
                              omp.assistant_record(20, text="partial", stop="aborted", done=9)).prompts
        self.assertEqual((timedelta(seconds=19), omp.at(29), None), (prompt.working_time, prompt.reply_at, prompt.reply_text))

    def test_an_errored_turn_works_until_its_error_message(self):
        (prompt,) = omp.parse(omp.user_record(10), omp.assistant_record(11, stop="error", done=20)).prompts
        self.assertEqual((timedelta(seconds=21), None), (prompt.working_time, prompt.reply_text))

    def test_a_turn_steered_mid_run_works_until_the_last_message_before_the_steering_prompt(self):
        first, second = omp.parse(omp.user_record(10), omp.assistant_record(11, stop="toolUse", done=4),
                                  omp.user_record(STEER_SECONDS), omp.assistant_record(21, text="ok", done=3)).prompts
        self.assertEqual((timedelta(seconds=5), None), (first.working_time, first.reply_text))
        self.assertEqual((timedelta(seconds=4), "ok"), (second.working_time, second.reply_text))

    def test_the_reply_stays_the_last_stopping_message_and_its_turn_works_to_the_last_message(self):
        (prompt,) = omp.parse(omp.user_record(10), omp.assistant_record(11, text="done", done=4)).prompts
        self.assertEqual((timedelta(seconds=5), "done", omp.at(15)), (prompt.working_time, prompt.reply_text, prompt.reply_at))

    def test_a_turn_without_any_message_works_no_time(self):
        (prompt,) = omp.parse(omp.user_record(10)).prompts
        self.assertEqual((timedelta(0), None), (prompt.working_time, prompt.reply_at))

    def test_the_total_working_time_counts_aborted_and_steered_turns(self):
        transcript = omp.parse(omp.user_record(10), omp.assistant_record(11, stop="toolUse", done=4), omp.user_record(STEER_SECONDS),
                               omp.assistant_record(21, text="ok", done=3), omp.user_record(40),
                               omp.assistant_record(41, stop="aborted", done=9))
        self.assertEqual(timedelta(seconds=5 + 4 + 10), sum((prompt.working_time for prompt in transcript.prompts), timedelta(0)))


class OmpPromptRecordTest(unittest.TestCase):
    def prompts(self, custom_type, attribution="user"):
        return omp.parse(omp.custom_record(10, "notice", custom_type, attribution=attribution), omp.user_record(11, "real"),
                         omp.assistant_record(12, text="ok")).prompts

    def test_an_image_attachment_notice_opens_no_prompt(self):
        self.assertEqual(["real"], [prompt.text for prompt in self.prompts("image-attachment")])

    def test_an_ultrathink_notice_opens_no_prompt(self):
        self.assertEqual(["real"], [prompt.text for prompt in self.prompts("ultrathink-notice")])

    def test_a_collab_prompt_and_a_skill_prompt_are_real_prompts(self):
        self.assertEqual(["notice", "real"], [prompt.text for prompt in self.prompts("collab-prompt")])
        self.assertEqual(["notice", "real"], [prompt.text for prompt in self.prompts("skill-prompt", attribution=None)])

    def test_a_notice_does_not_count_as_a_request(self):
        transcript = omp.parse(omp.custom_record(10, "n", "image-attachment", attribution="user"), omp.user_record(11, "real"),
                               omp.assistant_record(12, text="ok"))
        self.assertEqual(1, len(transcript.prompts))


def row(kind, data):
    return kind, json.dumps(data)


def at(second):
    return OC_BASE + timedelta(seconds=second)


def question_row(second, asked, done, finish="tool-calls", tool="question"):
    """An assistant row started at `second` whose `tool` call was streamed (put to the user) at `asked` and completed at `done`."""
    data = assistant_data(second, finish=finish, tokens=(1, 1), done=None)
    data["content"] = [{"type": "tool", "name": tool}]
    data["time"].update(streamed=oc_ms(asked), completed=oc_ms(done))
    return row("assistant", data)


class OpenCodeTurnTimeTest(unittest.TestCase):
    def only(self, *rows):
        (prompt,) = parse_messages(rows).prompts
        return prompt

    def test_an_errored_turn_works_until_its_last_completed_row_and_has_no_reply(self):
        prompt = self.only(row("user", user_data(10)), row("assistant", assistant_data(11, finish="tool-calls", tokens=(1, 1), done=4)),
                           row("assistant", assistant_data(20, finish="error", tokens=(1, 1), done=3)))
        self.assertEqual((timedelta(seconds=13), None, 2), (prompt.working_time, prompt.reply_text, len(prompt.requests)))

    def test_a_length_and_an_unfinished_row_count_when_they_completed(self):
        for finish in ("length", None, "unknown"):
            prompt = self.only(row("user", user_data(10)), row("assistant", assistant_data(11, finish=finish, tokens=(1, 1), done=6)))
            self.assertEqual(timedelta(seconds=7), prompt.working_time, finish)

    def test_a_row_that_never_completed_adds_no_time(self):
        prompt = self.only(row("user", user_data(10)), row("assistant", assistant_data(11, finish="tool-calls", done=2)),
                           row("assistant", assistant_data(30, finish=None, done=None)))
        self.assertEqual(timedelta(seconds=3), prompt.working_time)

    def test_a_stopping_turn_keeps_its_reply_and_time(self):
        prompt = self.only(row("user", user_data(10)), row("assistant", assistant_data(11, "reply", tokens=(1, 1), done=5)))
        self.assertEqual((timedelta(seconds=6), "reply", at(16)), (prompt.working_time, prompt.reply_text, prompt.reply_at))

    def test_the_wait_on_a_question_is_not_work(self):
        prompt = self.only(row("user", user_data(10)), question_row(11, asked=13, done=100),
                           row("assistant", assistant_data(101, "answer", tokens=(1, 1), done=3)))
        self.assertEqual((timedelta(seconds=3 + 3), "answer"), (prompt.working_time, prompt.reply_text))

    def test_every_wait_tool_and_every_wait_of_a_turn_is_left_out(self):
        prompt = self.only(row("user", user_data(10)), question_row(11, asked=13, done=50, tool="confirm"),
                           question_row(51, asked=55, done=200, tool="ask"), row("assistant", assistant_data(201, "x", tokens=(1, 1), done=2)))
        self.assertEqual(timedelta(seconds=3 + 4 + 2), prompt.working_time)

    def test_a_question_without_a_streamed_time_is_put_at_its_start(self):
        data = json.loads(question_row(11, asked=13, done=100)[1])
        del data["time"]["streamed"]
        prompt = self.only(row("user", user_data(10)), row("assistant", data), row("assistant", assistant_data(101, "a", done=1)))
        self.assertEqual(timedelta(seconds=1 + 1), prompt.working_time)

    def test_a_turn_that_ends_on_its_question_works_until_the_question(self):
        prompt = self.only(row("user", user_data(10)), question_row(11, asked=13, done=500))
        self.assertEqual(timedelta(seconds=3), prompt.working_time)

    def test_other_tools_are_work(self):
        prompt = self.only(row("user", user_data(10)), question_row(11, asked=13, done=100, tool="subagent"),
                           row("assistant", assistant_data(101, "a", tokens=(1, 1), done=3)))
        self.assertEqual(timedelta(seconds=94), prompt.working_time)

    def test_a_wait_does_not_leak_into_the_next_turn(self):
        first, second = parse_messages([row("user", user_data(10)), question_row(11, asked=13, done=100), row("user", user_data(200)),
                                        row("assistant", assistant_data(201, "r", tokens=(1, 1), done=4))]).prompts
        self.assertEqual((timedelta(seconds=3), timedelta(seconds=5)), (first.working_time, second.working_time))


class OpenCodePromptTextTest(unittest.TestCase):
    def test_prompt_text_is_trimmed(self):
        (prompt,) = parse_messages([row("user", user_data(10, "  \n padded text \t\n")), row("assistant", assistant_data(11, "r", done=1))]).prompts
        self.assertEqual("padded text", prompt.text)

    def test_an_empty_or_blank_user_row_opens_no_prompt_and_its_answers_stay_with_the_turn_before(self):
        transcript = parse_messages([row("user", user_data(10, "real")), row("assistant", assistant_data(11, "a", tokens=(1, 1), done=1)),
                                     row("user", user_data(20, " \n ")), row("user", {"time": {"created": oc_ms(21)}}),
                                     row("assistant", assistant_data(22, "b", tokens=(2, 2), done=1))])
        (prompt,) = transcript.prompts
        self.assertEqual(("real", "b", [1, 2]), (prompt.text, prompt.reply_text, [r.input_tokens for r in prompt.requests]))


class TurnTimeInTheReportTest(unittest.TestCase):
    """The row and TOTAL working times a user reads in the report, from transcripts whose turns do not end in a stop."""

    def total_time(self, harness, prompts):
        session = Session(harness=harness, session_id="s", started_at=NOW, prompts=prompts)
        return PricingRules(Book()).build(session).total.working_time

    def test_an_omp_session_of_aborted_errored_and_steered_turns_reports_their_work(self):
        transcript = omp.parse(omp.user_record(10), omp.assistant_record(11, stop="toolUse", done=4), omp.user_record(STEER_SECONDS),
                               omp.assistant_record(21, text="ok", done=3), omp.user_record(40), omp.assistant_record(41, stop="aborted", done=9),
                               omp.user_record(60), omp.assistant_record(61, stop="error", done=5))
        self.assertEqual(timedelta(seconds=5 + 4 + 10 + 6), self.total_time(Harness.OMP, transcript.prompts))

    def test_an_opencode_session_reports_errored_turns_and_leaves_the_questions_out(self):
        prompts = parse_messages([row("user", user_data(10)), row("assistant", assistant_data(11, finish="error", done=4)),
                                  row("user", user_data(30)), question_row(31, asked=33, done=100),
                                  row("assistant", assistant_data(101, "a", tokens=(1, 1), done=3))]).prompts
        self.assertEqual(timedelta(seconds=5 + 3 + 3), self.total_time(Harness.OPENCODE, prompts))


if __name__ == "__main__":
    unittest.main()
