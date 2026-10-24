"""Pure parsing of the `session_message` rows of one OpenCode session (S11): prompts, replies, requests with recorded cost, tokens and
model, and the end of the activity. No database is opened here; the rows are `(type, data text)` pairs in `seq` order."""
import json
import unittest
from datetime import timedelta

from sessionlog.json_records import MAX_WHOLE_COUNT
from sessionlog.model import RequestUsage
from sessionlog.opencode_messages import parse_messages
from tests.opencode_db import BASE, assistant_data, ms, user_data


def row(kind, data):
    return kind, data if isinstance(data, str) else json.dumps(data)


def at(second):
    return BASE + timedelta(seconds=second)


class PromptsAndRepliesTest(unittest.TestCase):
    def test_a_user_row_opens_a_prompt_whose_reply_is_the_last_stopping_assistant_row_before_the_next_user_row(self):
        transcript = parse_messages([
            row("user", user_data(10, "first")),
            row("assistant", assistant_data(11, "partial", finish="tool-calls", tokens=(1, 1))),
            row("assistant", assistant_data(14, "first reply", tokens=(1, 1), done=2)),
            row("user", user_data(30, "second")),
            row("assistant", assistant_data(31, "second reply", tokens=(1, 1), done=5)),
        ])
        first, second = transcript.prompts
        self.assertEqual((at(10), "first", at(16), "first reply", timedelta(seconds=6)),
                         (first.submitted_at, first.text, first.reply_at, first.reply_text, first.working_time))
        self.assertEqual((at(30), "second", at(36), "second reply", timedelta(seconds=6)),
                         (second.submitted_at, second.text, second.reply_at, second.reply_text, second.working_time))
        self.assertEqual([2, 1], [len(first.requests), len(second.requests)])

    def test_the_reply_text_joins_the_text_blocks_by_blank_lines_and_skips_the_other_blocks(self):
        reply = assistant_data(11, tokens=(1, 1))
        reply["content"] = [{"type": "reasoning", "text": "thinking"}, {"type": "text", "text": "one"}, {"type": "tool", "name": "x"},
                            {"type": "text", "text": "  "}, {"type": "text", "text": "two"}]
        (prompt,) = parse_messages([row("user", user_data(10)), row("assistant", reply)]).prompts
        self.assertEqual("one\n\ntwo", prompt.reply_text)

    def test_a_reply_without_completed_time_is_dated_by_its_start(self):
        (prompt,) = parse_messages([row("user", user_data(10)), row("assistant", assistant_data(12, "r", done=None))]).prompts
        self.assertEqual((at(12), timedelta(seconds=2)), (prompt.reply_at, prompt.working_time))

    def test_a_turn_without_a_stopping_row_has_no_reply_and_works_no_time(self):
        (prompt,) = parse_messages([row("user", user_data(10)), row("assistant", assistant_data(11, finish=None, tokens=(1, 1)))]).prompts
        self.assertEqual((None, None, timedelta(0), 1), (prompt.reply_at, prompt.reply_text, prompt.working_time, len(prompt.requests)))

    def test_a_user_row_without_a_usable_time_is_dropped_with_its_replies_and_requests(self):
        transcript = parse_messages([row("user", {"text": "no time"}), row("assistant", assistant_data(11, "lost", tokens=(1, 1))),
                                     row("user", user_data(20, "kept")), row("assistant", assistant_data(21, "ok", tokens=(2, 2)))])
        self.assertEqual(["kept"], [prompt.text for prompt in transcript.prompts])
        self.assertEqual([2], [request.input_tokens for request in transcript.prompts[0].requests])

    def test_assistant_rows_before_the_first_user_row_fall_under_the_first_prompt(self):
        transcript = parse_messages([row("assistant", assistant_data(5, "early", tokens=(3, 3))), row("user", user_data(10)),
                                     row("assistant", assistant_data(11, "late", tokens=(4, 4)))])
        self.assertEqual([3, 4], [request.input_tokens for request in transcript.prompts[0].requests])

    def test_rows_of_other_types_and_rows_that_are_no_json_object_are_skipped_without_a_report(self):
        transcript = parse_messages([row("system", {"text": "s", "time": {"created": ms(1)}}), row("synthetic", {"text": "tool run"}),
                                     row("idle", {"outcome": "x"}), row("user", "not json"), row("user", "[1]"), row("assistant", "{"),
                                     row("user", user_data(10, "real")), row("assistant", assistant_data(11, "ok", tokens=(1, 1)))])
        self.assertEqual(["real"], [prompt.text for prompt in transcript.prompts])

    def test_the_last_stopping_row_of_a_turn_is_its_reply_whatever_the_rows_before_it_say(self):
        (prompt,) = parse_messages([row("user", user_data(10)), row("assistant", assistant_data(11, "early stop", tokens=(1, 1), done=1)),
                                    row("assistant", assistant_data(13, "last stop", tokens=(1, 1), done=1))]).prompts
        self.assertEqual(("last stop", at(14)), (prompt.reply_text, prompt.reply_at))

    def test_a_reply_that_completed_before_its_prompt_was_submitted_is_dated_by_the_submission(self):
        (prompt,) = parse_messages([row("user", user_data(30)), row("assistant", assistant_data(11, "r", done=1))]).prompts
        self.assertEqual((at(30), timedelta(0)), (prompt.reply_at, prompt.working_time))

    def test_a_stopping_row_without_any_time_keeps_its_text_but_gives_no_reply_time_and_no_working_time(self):
        reply = assistant_data(11, "timeless", tokens=(1, 1))
        del reply["time"]
        (prompt,) = parse_messages([row("user", user_data(10)), row("assistant", reply)]).prompts
        self.assertEqual(("timeless", None, timedelta(0)), (prompt.reply_text, prompt.reply_at, prompt.working_time))

    def test_a_reply_content_that_is_no_list_has_no_text_and_a_user_text_that_is_no_text_is_empty(self):
        reply = assistant_data(11, tokens=(1, 1))
        reply["content"] = "just a string"
        (prompt,) = parse_messages([row("user", {"text": 7, "time": {"created": ms(10)}}), row("assistant", reply)]).prompts
        self.assertEqual(("", None), (prompt.text, prompt.reply_text))

    def test_the_requests_of_a_session_without_any_prompt_are_kept_for_a_sub_agent_that_reports_them(self):
        transcript = parse_messages([row("assistant", assistant_data(11, "x", tokens=(5, 6)))])
        self.assertEqual(((), [5]), (transcript.prompts, [request.input_tokens for request in transcript.requests]))

    def test_no_rows_give_an_empty_transcript(self):
        transcript = parse_messages([])
        self.assertEqual(((), (), None), (transcript.prompts, transcript.requests, transcript.last_moment))


class RequestsTest(unittest.TestCase):
    def request_of(self, data):
        (request,) = parse_messages([row("user", user_data(10)), row("assistant", data)]).requests
        return request

    def test_tokens_cost_and_model_are_read_from_the_assistant_data_with_reasoning_counted_as_output(self):
        request = self.request_of(assistant_data(11, tokens=(10, 100, 20, 200, 50), cost=0.5, model=("anthropic", "claude-opus-5-5")))
        self.assertEqual(RequestUsage(model_name="anthropic/claude-opus-5-5", input_tokens=10, output_tokens=120, cache_read_tokens=200,
                                      cache_write_5m_tokens=50, cache_write_1h_tokens=0, effort=None, recorded_cost_usd=0.5), request)

    def test_a_recorded_cost_of_zero_is_kept_as_free_and_an_absent_one_is_left_to_the_price_file(self):
        self.assertEqual(0.0, self.request_of(assistant_data(11, tokens=(1, 1), cost=0)).recorded_cost_usd)
        self.assertIsNone(self.request_of(assistant_data(11, tokens=(1, 1), cost=None)).recorded_cost_usd)

    def test_a_cost_that_is_no_number_from_zero_up_is_not_a_recorded_cost(self):
        for cost in ("1.5", -1, True, None, [1]):
            with self.subTest(cost=cost):
                self.assertIsNone(self.request_of(assistant_data(11, tokens=(1, 1), cost=cost)).recorded_cost_usd)

    def test_a_token_count_that_is_no_count_from_zero_up_is_zero_and_a_fraction_is_cut_off(self):
        data = assistant_data(11, tokens=(1, 1))
        data["tokens"] = {"input": "7", "output": -3, "reasoning": True, "cache": {"read": 2.9, "write": None}}
        request = self.request_of(data)
        self.assertEqual((0, 0, 2, 0), (request.input_tokens, request.output_tokens, request.cache_read_tokens, request.cache_write_5m_tokens))

    def test_a_huge_token_count_stops_at_the_int64_maximum_and_inf_and_nan_count_as_zero(self):
        text = ('{"tokens": {"input": 1e30, "output": Infinity, "reasoning": NaN, "cache": {"read": 99999999999999999999999999, "write": -Infinity}}, '
                '"time": {"created": %d}}' % ms(11))
        (request,) = parse_messages([row("user", user_data(10)), ("assistant", text)]).requests
        self.assertEqual((MAX_WHOLE_COUNT, 0, MAX_WHOLE_COUNT, 0), (request.input_tokens, request.output_tokens, request.cache_read_tokens, request.cache_write_5m_tokens))

    def test_the_reasoning_and_output_tokens_together_never_pass_the_int64_maximum(self):
        data = assistant_data(11)
        data["tokens"] = {"input": 0, "output": MAX_WHOLE_COUNT, "reasoning": MAX_WHOLE_COUNT}
        self.assertEqual(MAX_WHOLE_COUNT, self.request_of(data).output_tokens)

    def test_a_model_without_a_provider_has_its_bare_name_and_a_model_that_names_a_path_keeps_it_and_a_missing_model_has_an_empty_name(self):
        data = assistant_data(11, tokens=(1, 1))
        data["model"] = {"id": "bare-model"}
        self.assertEqual("bare-model", self.request_of(data).model_name)
        self.assertEqual("openrouter/vendor/model", self.request_of(assistant_data(11, tokens=(1, 1), model=("ignored", "openrouter/vendor/model"))).model_name)
        data = assistant_data(11, tokens=(1, 1))
        del data["model"]
        self.assertEqual("", self.request_of(data).model_name)

    def test_an_assistant_row_without_a_tokens_object_makes_no_request(self):
        self.assertEqual((), parse_messages([row("user", user_data(10)), row("assistant", assistant_data(11, "r"))]).requests)

    def test_the_effort_is_never_recorded(self):
        self.assertIsNone(self.request_of(assistant_data(11, tokens=(1, 1))).effort)


class ActivityTest(unittest.TestCase):
    def test_the_last_moment_is_the_newest_start_or_completion_of_any_row(self):
        transcript = parse_messages([row("user", user_data(10)), row("assistant", assistant_data(11, tokens=(1, 1), done=20)),
                                     row("system", {"time": {"created": ms(15)}}), row("idle", {"time": {"created": ms(100)}})])
        self.assertEqual(at(100), transcript.last_moment)

    def test_a_completion_newer_than_every_start_decides_the_last_moment(self):
        transcript = parse_messages([row("user", user_data(10)), row("assistant", assistant_data(11, tokens=(1, 1), done=200)),
                                     row("idle", {"time": {"created": ms(50)}})])
        self.assertEqual(at(211), transcript.last_moment)

    def test_the_working_time_since_a_start_is_measured_to_the_last_moment_and_is_never_negative(self):
        transcript = parse_messages([row("assistant", assistant_data(30, tokens=(1, 1), done=10))])
        self.assertEqual((timedelta(seconds=15), timedelta(0)), (transcript.working_time_since(at(25)), transcript.working_time_since(at(500))))
        self.assertEqual(timedelta(0), parse_messages([]).working_time_since(at(1)))

    def test_times_that_are_no_epoch_milliseconds_are_ignored(self):
        data = assistant_data(11, tokens=(1, 1))
        data["time"] = {"created": "yesterday", "completed": -5}
        self.assertIsNone(parse_messages([row("assistant", data)]).last_moment)


if __name__ == "__main__":
    unittest.main()
