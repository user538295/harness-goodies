"""Pure parsing of the `session_message` rows of one OpenCode session (S11): prompts, replies, requests with recorded cost, tokens and
model, and the end of the activity. No database is opened here; the rows are `(type, data text)` pairs in `seq` order."""
import json
import unittest
from datetime import timedelta

from sessionlog.json_records import MAX_WHOLE_COUNT
from sessionlog.model import RequestUsage
from sessionlog.opencode_messages import MAX_COMPLETION_LAG, parse_messages
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

    def test_a_turn_without_a_stopping_row_has_no_reply_but_works_until_its_last_completed_row(self):
        (prompt,) = parse_messages([row("user", user_data(10)), row("assistant", assistant_data(11, finish=None, tokens=(1, 1)))]).prompts
        self.assertEqual((at(12), None, timedelta(seconds=2), 1), (prompt.reply_at, prompt.reply_text, prompt.working_time, len(prompt.requests)))

    def test_a_user_row_without_a_usable_time_is_dropped_with_its_replies_and_its_requests_fall_under_no_prompt(self):
        transcript = parse_messages([row("user", {"text": "no time"}), row("assistant", assistant_data(11, "lost", tokens=(1, 1))),
                                     row("user", user_data(20, "kept")), row("assistant", assistant_data(21, "ok", tokens=(2, 2)))])
        self.assertEqual(["kept"], [prompt.text for prompt in transcript.prompts])
        self.assertEqual([2], [request.input_tokens for request in transcript.prompts[0].requests])
        self.assertEqual([1], [request.input_tokens for request in transcript.unprompted_requests])

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

    def test_a_reply_content_that_is_no_list_has_no_text_and_a_user_text_that_is_no_text_opens_no_prompt(self):
        reply = assistant_data(11, tokens=(1, 1))
        reply["content"] = "just a string"
        (prompt,) = parse_messages([row("user", {"text": 7, "time": {"created": ms(9)}}), row("user", user_data(10)), row("assistant", reply)]).prompts
        self.assertEqual(("ask", None), (prompt.text, prompt.reply_text))

    def test_the_requests_of_a_session_without_any_prompt_are_kept_for_a_sub_agent_and_fall_under_no_prompt(self):
        transcript = parse_messages([row("assistant", assistant_data(11, "x", tokens=(5, 6), cost=3.5))])
        self.assertEqual(((), [5], [5], [3.5]), (transcript.prompts, [request.input_tokens for request in transcript.requests],
                                                  [request.input_tokens for request in transcript.unprompted_requests],
                                                  [request.recorded_cost_usd for request in transcript.unprompted_requests]))

    def test_a_session_whose_requests_all_fall_under_a_prompt_has_none_under_no_prompt(self):
        transcript = parse_messages([row("assistant", assistant_data(5, tokens=(3, 3))), row("user", user_data(10)),
                                     row("assistant", assistant_data(11, "late", tokens=(4, 4)))])
        self.assertEqual((), transcript.unprompted_requests)

    def test_no_rows_give_an_empty_transcript(self):
        transcript = parse_messages([])
        self.assertEqual(((), (), (), None), (transcript.prompts, transcript.requests, transcript.unprompted_requests, transcript.last_moment))


class RequestsTest(unittest.TestCase):
    def request_of(self, data):
        (request,) = parse_messages([row("user", user_data(10)), row("assistant", data)]).requests
        return request

    def test_tokens_cost_and_model_are_read_from_the_assistant_data_with_reasoning_counted_as_output(self):
        request = self.request_of(assistant_data(11, tokens=(10, 100, 20, 200, 50), cost=0.5, model=("anthropic", "claude-opus-5-5")))
        self.assertEqual(RequestUsage(model_name="anthropic/claude-opus-5-5", input_tokens=10, output_tokens=120, cache_read_tokens=200,
                                      cache_write_5m_tokens=50, cache_write_1h_tokens=0, effort=None, recorded_cost_usd=0.5, reasoning_tokens=20), request)

    def test_the_reasoning_tokens_are_kept_apart_though_the_output_total_counts_them(self):
        request = self.request_of(assistant_data(11, tokens=(10, 100, 20, 200, 50)))
        self.assertEqual((120, 20), (request.output_tokens, request.reasoning_tokens))
        self.assertEqual(0, self.request_of(assistant_data(11, tokens=(1, 1))).reasoning_tokens)

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


def compaction_data(second, tokens=None, cost=0, model=("opencode", "big-pickle")):
    """The `data` of a compaction row created at `second`: the summary call OpenCode makes when the context is full. A real row that
    holds no `tokens` also holds no `model` and no `cost`."""
    data = {"reason": "auto", "status": "completed", "time": {"created": ms(second)}}
    if tokens is not None:
        data.update({"model": {"providerID": model[0], "id": model[1], "variant": "default"}, "cost": cost,
                     "tokens": {"input": tokens[0], "output": tokens[1], "reasoning": tokens[2] if len(tokens) > 2 else 0,
                                "cache": {"read": tokens[3] if len(tokens) > 3 else 0, "write": 0}}})
    return data


class CompactionTest(unittest.TestCase):
    """A compaction row that records `tokens` is a real model call: OpenCode adds it to the session's own totals."""

    def test_a_compaction_with_tokens_is_a_request_of_the_prompt_in_progress_with_its_own_model_and_cost(self):
        transcript = parse_messages([row("user", user_data(10)), row("compaction", compaction_data(11, tokens=(300, 20, 0, 40), cost=0.25)),
                                     row("assistant", assistant_data(12, "done", tokens=(5, 6)))])
        compaction, answer = transcript.prompts[0].requests
        self.assertEqual(("opencode/big-pickle", 300, 20, 40, 0.25), (compaction.model_name, compaction.input_tokens, compaction.output_tokens,
                                                                      compaction.cache_read_tokens, compaction.recorded_cost_usd))
        self.assertEqual((5, [compaction, answer]), (answer.input_tokens, list(transcript.requests)))

    def test_a_compaction_is_no_reply_and_moves_neither_the_reply_nor_the_working_time(self):
        transcript = parse_messages([row("user", user_data(10)), row("assistant", assistant_data(11, "ok", tokens=(1, 1))),
                                     row("compaction", compaction_data(500, tokens=(300, 20)))])
        prompt = transcript.prompts[0]
        self.assertEqual(("ok", at(12), timedelta(seconds=2), 2), (prompt.reply_text, prompt.reply_at, prompt.working_time, len(prompt.requests)))

    def test_a_compaction_before_the_first_prompt_falls_under_the_first_prompt_and_one_without_any_prompt_under_no_prompt(self):
        led = parse_messages([row("compaction", compaction_data(5, tokens=(300, 20))), row("user", user_data(10))])
        alone = parse_messages([row("compaction", compaction_data(5, tokens=(300, 20)))])
        self.assertEqual(([300], [], [300], [300]), ([request.input_tokens for request in led.prompts[0].requests], list(led.unprompted_requests),
                                                      [request.input_tokens for request in alone.requests],
                                                      [request.input_tokens for request in alone.unprompted_requests]))

    def test_a_compaction_without_tokens_makes_no_request(self):
        transcript = parse_messages([row("user", user_data(10)), row("compaction", compaction_data(11)),
                                     row("compaction", {"error": {"type": "x", "message": "m"}, "time": {"created": ms(12)}})])
        self.assertEqual((), transcript.requests)


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


def tool_block(started, done):
    return {"type": "tool", "name": "read", "time": {"created": ms(started), "completed": ms(done)}}


MIGRATION_LAG_SECONDS = 100 * 24 * 3600


def migrated_row(started, *blocks, finish="tool-calls", tokens=(1, 1)):
    """An assistant row as OpenCode's v1-to-v2 migration leaves it: its `time.completed` is the instant of the migration, months after
    it started, while the times of its content blocks stay those the work really had."""
    data = assistant_data(started, finish=finish, tokens=tokens, done=MIGRATION_LAG_SECONDS)
    data["content"] = list(blocks)
    return row("assistant", data)


class MigratedCompletionTest(unittest.TestCase):
    """Sessions migrated from OpenCode v1 carry one shared `completed` far after the work (257 rows of 14 sessions in a real database, all
    94 days or more after the newest time of the row's own blocks; genuine rows end within an hour of it)."""

    def test_a_completion_far_after_the_rows_own_blocks_is_replaced_by_the_end_of_its_latest_block(self):
        (prompt,) = parse_messages([row("user", user_data(10)), migrated_row(11, tool_block(12, 15), tool_block(13, 18))]).prompts
        self.assertEqual((at(18), timedelta(seconds=8)), (prompt.reply_at, prompt.working_time))

    def test_the_last_moment_and_the_working_time_of_a_sub_agent_are_not_poisoned_by_the_completion(self):
        transcript = parse_messages([row("user", user_data(10)), migrated_row(11, tool_block(12, 15)), migrated_row(16, tool_block(17, 20))])
        self.assertEqual((at(20), timedelta(seconds=10)), (transcript.last_moment, transcript.working_time_since(at(10))))

    def test_a_stopping_row_without_block_times_is_dated_by_its_start_and_one_that_does_not_stop_has_no_end(self):
        stopped = parse_messages([row("user", user_data(10)), migrated_row(11, {"type": "text", "text": "done"}, finish="stop")])
        running = parse_messages([row("user", user_data(10)), migrated_row(11, finish="tool-calls")])
        (stop_prompt,), (run_prompt,) = stopped.prompts, running.prompts
        self.assertEqual((at(11), "done", timedelta(seconds=1)), (stop_prompt.reply_at, stop_prompt.reply_text, stop_prompt.working_time))
        self.assertEqual((None, timedelta(0)), (run_prompt.reply_at, run_prompt.working_time))
        self.assertEqual((at(11), at(11)), (stopped.last_moment, running.last_moment))

    def test_the_tokens_and_cost_of_a_migrated_row_still_count(self):
        (prompt,) = parse_messages([row("user", user_data(10)), migrated_row(11, tool_block(12, 15), tokens=(7, 8))]).prompts
        self.assertEqual([7], [request.input_tokens for request in prompt.requests])

    def test_a_long_genuine_row_keeps_its_completion_when_its_blocks_run_up_to_it(self):
        data = assistant_data(11, finish="tool-calls", tokens=(1, 1), done=18 * 3600)
        data["content"] = [tool_block(12, 18 * 3600 + 11)]
        (prompt,) = parse_messages([row("user", user_data(10)), row("assistant", data)]).prompts
        self.assertEqual((at(11 + 18 * 3600), timedelta(seconds=11 + 18 * 3600 - 10)), (prompt.reply_at, prompt.working_time))

    def test_a_completion_the_lag_after_the_latest_time_of_the_row_is_kept_and_one_second_more_is_not(self):
        lag = int(MAX_COMPLETION_LAG.total_seconds())
        for extra, expected in ((0, at(11 + lag)), (1, at(11))):
            with self.subTest(extra=extra):
                data = assistant_data(11, finish="stop", tokens=(1, 1), done=lag + extra)
                (prompt,) = parse_messages([row("user", user_data(10)), row("assistant", data)]).prompts
                self.assertEqual(expected, prompt.reply_at)

    def test_the_streaming_time_counts_as_the_rows_own_activity(self):
        data = assistant_data(11, finish="stop", tokens=(1, 1), done=MIGRATION_LAG_SECONDS)
        data["time"]["streamed"] = ms(11 + MIGRATION_LAG_SECONDS - 5)
        (prompt,) = parse_messages([row("user", user_data(10)), row("assistant", data)]).prompts
        self.assertEqual(at(11 + MIGRATION_LAG_SECONDS), prompt.reply_at)


if __name__ == "__main__":
    unittest.main()
