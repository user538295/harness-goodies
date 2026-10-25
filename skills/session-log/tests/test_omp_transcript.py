"""The OMP transcript parser (S10, S42; Q16, Q25): header, prompts, replies, requests, effort and activity bounds of the lines of one
session file, without any file access."""
import json
import unittest
from datetime import timedelta

from sessionlog.omp_transcript import ParentLink, Transcript, parse_parent_link, parse_transcript
from tests.omp_records import (assistant_record, at, custom_record, effort_record, header_record, helper_record, init_record, iso, jsonl, ms, parse,
                               usage_of, user_record)

SESSION_ID = "01a10000-0000-7000-8000-000000000001"


def prompts_of(*records):
    return parse(header_record(SESSION_ID), *records).prompts


def requests_of(*records):
    """The requests of the one prompt `ask` the records follow."""
    (prompt,) = prompts_of(user_record(10, "ask"), *records)
    return prompt.requests


def tokens_of(request):
    return (request.input_tokens, request.output_tokens, request.cache_read_tokens, request.cache_write_5m_tokens, request.cache_write_1h_tokens)


class OmpHeaderTest(unittest.TestCase):
    def test_the_header_gives_the_id_the_folder_the_start_and_the_parent_path(self):
        transcript = parse(header_record("child", second=7, parent="/home/user/.omp/agent/sessions/-p/stem.jsonl", cwd="/x"))
        self.assertEqual(("child", "/x", at(7), "/home/user/.omp/agent/sessions/-p/stem.jsonl", False),
                         (transcript.header_id, transcript.project_dir, transcript.header_moment, transcript.parent_session, transcript.unparsable))

    def test_a_header_is_the_first_session_record_wherever_it_stands(self):
        transcript = parse({"type": "title", "title": "t"}, {"type": "model_change", "model": "m"}, header_record("first", second=7, cwd="/x"),
                           header_record("other", cwd="/y"))
        self.assertEqual(("first", "/x", at(7)), (transcript.header_id, transcript.project_dir, transcript.header_moment))

    def test_a_transcript_without_a_header_has_none_of_its_fields(self):
        transcript = parse(user_record(10, "ask"))
        self.assertEqual((None, None, None, None), (transcript.header_id, transcript.project_dir, transcript.header_moment, transcript.parent_session))

    def test_empty_values_in_the_header_are_none_and_a_header_without_a_time_has_none(self):
        transcript = parse({"type": "session", "id": "", "cwd": "", "parentSession": "", "timestamp": "not a time"})
        self.assertEqual((None, None, None, None), (transcript.header_id, transcript.project_dir, transcript.parent_session, transcript.header_moment))
        self.assertIsNone(parse({"type": "session", "timestamp": "2026-10-13T08:42:00"}).header_moment)  # a naive time is no time

    def test_the_agent_is_the_first_non_empty_agent_of_a_session_init(self):
        transcript = parse(header_record("a"), {"type": "session_init", "agent": ""}, {"type": "session_init", "agent": 3}, init_record("task"), init_record("other"))
        self.assertEqual("task", transcript.agent_type)

    def test_a_file_with_text_but_no_json_object_is_unparsable_and_a_file_without_any_line_is_empty(self):
        self.assertTrue(parse_transcript(["this is not json\n", "[1, 2]\n", "\n"]).unparsable)
        for lines in ([], ["\n", "  \n", "\t\n"]):
            with self.subTest(lines=lines):
                self.assertEqual(Transcript(), parse_transcript(lines))

    def test_unknown_record_types_and_bad_lines_are_skipped_without_a_report(self):
        lines = ([json.dumps(header_record(SESSION_ID)), "not json", "[1, 2]", '{"type": "something-new", "x": 1}', "", "[" * 200000,
                  json.dumps(user_record(10, "kept")), '{"type": "message", "message": "text"}', '{"type": "message", "message": null}',
                  '{"type": "custom_message"}', '{"type": "model_usage", "usage": 3}', '{"type": "thinking_level_change"}'])
        transcript = parse_transcript(lines)
        self.assertEqual((False, ["kept"], ()), (transcript.unparsable, [p.text for p in transcript.prompts], transcript.requests))


class OmpParentLinkTest(unittest.TestCase):
    def test_the_parent_is_the_one_the_first_session_record_names_and_nothing_after_it_is_read(self):
        def lines():
            yield "not json\n"
            yield "\n"
            yield json.dumps({"type": "title"}) + "\n"
            yield json.dumps(header_record("a", parent="/x/y/parent.jsonl")) + "\n"
            raise AssertionError("read past the header")
        self.assertEqual(ParentLink("/x/y/parent.jsonl"), parse_parent_link(lines()))

    def test_a_header_without_a_parent_or_with_an_empty_one_and_no_header_at_all_have_none(self):
        for records in ([header_record("a")], [header_record("a", parent="")], [{"type": "message"}], []):
            with self.subTest(records=records):
                self.assertEqual(ParentLink(None), parse_parent_link(jsonl(*records).splitlines(keepends=True)))

    def test_the_link_is_never_unparsable(self):
        self.assertFalse(parse_parent_link(["not json\n"]).unparsable)


class OmpPromptTest(unittest.TestCase):
    def test_a_user_message_is_a_prompt_unless_its_attribution_is_agent(self):
        records = [user_record(10, "typed"), user_record(11, "agent task", attribution="agent"), user_record(12, "no attribution", attribution=None),
                   user_record(13, "", content=[{"type": "image"}]), user_record(14, "", content="plain string"),
                   user_record(15, "", content=[{"type": "text", "text": "one"}, "two", {"type": "text", "text": ""}, {"type": "text", "text": 7}])]
        self.assertEqual(["typed", "no attribution", "", "plain string", "one\ntwo"], [p.text for p in prompts_of(*records)])

    def test_a_prompt_text_is_empty_when_the_content_is_neither_text_nor_blocks(self):
        message = user_record(10, "x")
        message["message"]["content"] = 5
        self.assertEqual([""], [p.text for p in prompts_of(message)])

    def test_a_custom_message_is_a_prompt_with_user_attribution_or_as_a_skill_prompt_and_other_types_are_not(self):
        records = [custom_record(10, "note by user", custom_type="note", attribution="user"), custom_record(11, "skill"),
                   custom_record(12, "background", custom_type="background-note"), custom_record(13, "agent note", custom_type="note", attribution="agent"),
                   custom_record(14, [{"type": "text", "text": "list one"}, {"type": "text", "text": "list two"}], attribution="user")]
        self.assertEqual(["note by user", "skill", "list one\nlist two"], [p.text for p in prompts_of(*records)])

    def test_the_prompt_time_is_the_message_time_else_the_record_time(self):
        records = [user_record(10, "message time", timestamp=ms(11)), custom_record(12, "record time"), user_record(13, "text time", timestamp="2026-10-13T08:42:14.000Z"),
                   user_record(15, "no message time", timestamp=None)]
        self.assertEqual([at(11), at(12), at(14), at(15)], [p.submitted_at for p in prompts_of(*records)])

    def test_a_prompt_without_a_usable_time_is_dropped_with_its_replies_and_requests(self):
        timeless = {"type": "custom_message", "customType": "skill-prompt", "content": "timeless", "attribution": "user"}
        prompts = prompts_of(user_record(10, "first"), timeless, assistant_record(13, "late reply", usage=usage_of(5, 5)), user_record(20, "next"))
        self.assertEqual([("first", 0, None), ("next", 0, None)], [(p.text, len(p.requests), p.reply_text) for p in prompts])

    def test_a_session_without_prompts_has_none_and_keeps_its_requests_and_agent_type(self):
        transcript = parse(header_record("s"), init_record("task"), assistant_record(11, usage=usage_of(1, 1)))
        self.assertEqual(((), 1, "task"), (transcript.prompts, len(transcript.requests), transcript.agent_type))


class OmpRequestTest(unittest.TestCase):
    def test_streamed_copies_of_one_response_count_once_with_the_last_copy_and_the_reply_comes_from_it(self):
        records = [assistant_record(11, "partial", stop="toolUse", response_id="r1", usage=usage_of(5, 5)),
                   assistant_record(12, "final", response_id="r1", usage=usage_of(5, 50), done=3),
                   assistant_record(20, "other", stop="toolUse", response_id="r2", usage=usage_of(1, 1))]
        (prompt,) = prompts_of(user_record(10, "ask"), *records)
        self.assertEqual(([(5, 50), (1, 1)], "final"), ([(r.input_tokens, r.output_tokens) for r in prompt.requests], prompt.reply_text))

    def test_the_last_copy_of_a_response_decides_the_prompt_it_falls_under(self):
        records = [user_record(10, "one"), assistant_record(11, "early", stop="toolUse", response_id="r1", usage=usage_of(1, 1)), user_record(20, "two"),
                   assistant_record(21, "late", response_id="r1", usage=usage_of(2, 2), done=2)]
        one, two = prompts_of(*records)
        self.assertEqual(([], [(2, 2)], "late"), ([r.input_tokens for r in one.requests], [(r.input_tokens, r.output_tokens) for r in two.requests], two.reply_text))

    def test_a_message_id_or_an_id_keys_a_copy_when_there_is_no_response_id_then_the_timestamp_and_without_any_all_are_kept(self):
        def copies(first, second):
            return [r.input_tokens for r in requests_of(first, second)]
        usages = (usage_of(1, 1), usage_of(2, 2))
        self.assertEqual([2], copies(*(assistant_record(11 + n, stop="toolUse", usage=usages[n], messageId="m1") for n in range(2))))
        self.assertEqual([2], copies(*(assistant_record(13 + n, stop="toolUse", usage=usages[n], id="i1") for n in range(2))))
        self.assertEqual([2], copies(*(assistant_record(15 + n, stop="toolUse", usage=usages[n], responseId=7) for n in range(2))))
        self.assertEqual([2], copies(*(assistant_record(17, stop="toolUse", usage=usages[n], timestamp=ms(17)) for n in range(2))))
        self.assertEqual([1, 2], copies(*(assistant_record(19 + n, stop="toolUse", usage=usages[n], timestamp=None) for n in range(2))))
        self.assertEqual([1, 2], copies(*(assistant_record(21 + n, stop="toolUse", usage=usages[n], timestamp=True, id=False) for n in range(2))))

    def test_a_helper_request_counts_in_the_prompt_it_falls_in_and_one_before_the_first_prompt_belongs_to_the_first(self):
        records = [helper_record(5, usage_of(2, 3, cost=0.5)), user_record(10, "one"), assistant_record(11, "a", usage=usage_of(1, 1)),
                   helper_record(12, usage_of(4, 4)), user_record(20, "two"),
                   helper_record(21, usage_of(6, 6, cost=None), model="anthropic/claude-haiku-5", provider="other")]
        one, two = parse(header_record(SESSION_ID), *records).prompts
        self.assertEqual([(2, 3, 0.5, "anthropic/claude-haiku-5"), (1, 1, 0.0, "anthropic/claude-opus-5-5"), (4, 4, 0.0, "anthropic/claude-haiku-5")],
                         [(r.input_tokens, r.output_tokens, r.recorded_cost_usd, r.model_name) for r in one.requests])
        self.assertEqual([(6, 6, None, "anthropic/claude-haiku-5")], [(r.input_tokens, r.output_tokens, r.recorded_cost_usd, r.model_name) for r in two.requests])

    def test_the_effort_is_the_last_thinking_level_carried_forward_and_a_message_level_overrides_it(self):
        records = [effort_record(1, "low"), effort_record(2, 3), effort_record(4, ""), assistant_record(11, stop="toolUse", usage=usage_of()), effort_record(12, "high"),
                   assistant_record(13, stop="toolUse", usage=usage_of()), assistant_record(14, stop="toolUse", usage=usage_of(), thinkingLevel="xhigh"),
                   assistant_record(15, stop="toolUse", usage=usage_of(), effort="minimal"), assistant_record(16, stop="toolUse", usage=usage_of())]
        self.assertEqual(["low", "high", "xhigh", "minimal", "high"], [r.effort for r in requests_of(*records)])

    def test_a_request_has_no_effort_before_any_thinking_level_change(self):
        self.assertEqual([None], [r.effort for r in requests_of(assistant_record(11, usage=usage_of()))])

    def test_tokens_are_whole_non_negative_numbers_under_either_name_and_anything_else_is_zero(self):
        odd = {"inputTokens": 7.9, "output_tokens": 3, "cache_read": 2, "cacheWriteTokens": 4, "cost": {"total": 0.5}}
        bad = {"input": -1, "output": True, "cacheRead": "5", "cacheWrite": None, "cost": {"total": 0.1}}
        first, second = requests_of(assistant_record(11, stop="toolUse", usage=odd), assistant_record(12, stop="toolUse", usage=bad))
        self.assertEqual(((7, 3, 2, 4, 0), (0, 0, 0, 0, 0)), (tokens_of(first), tokens_of(second)))

    def test_a_count_that_is_too_large_or_not_finite_is_clamped_or_zero_and_never_raises(self):
        huge = {"input": 10**400, "output": float("inf"), "cacheRead": float("nan"), "cacheWrite": 2**70, "cost": {"total": 10**400}}
        (request,) = requests_of(assistant_record(11, stop="toolUse", usage=huge))
        self.assertEqual(((2**63 - 1, 0, 0, 2**63 - 1, 0), None), (tokens_of(request), request.recorded_cost_usd))

    def test_the_recorded_cost_is_the_total_or_a_plain_number_zero_is_free_and_a_missing_or_bad_one_is_left_to_the_price_file(self):
        usages = [usage_of(cost=0.25), usage_of(cost=0.0), {**usage_of(cost=None), "cost": 0.75}, usage_of(cost=None), {**usage_of(cost=None), "cost": {"input": 1}},
                  {**usage_of(cost=None), "cost": {"total": "x"}}, {**usage_of(cost=None), "cost": {"total": -1}}, {**usage_of(cost=None), "cost": {"total": True}},
                  {**usage_of(cost=None), "cost": {"total": float("nan")}}, {**usage_of(cost=None), "cost": 3}]
        records = [assistant_record(11 + number, stop="toolUse", usage=usage) for number, usage in enumerate(usages)]
        self.assertEqual([0.25, 0.0, 0.75, None, None, None, None, None, None, 3.0], [r.recorded_cost_usd for r in requests_of(*records)])

    def test_the_model_is_named_with_its_provider_unless_it_already_has_a_path_and_an_assistant_without_usage_makes_no_request(self):
        records = [assistant_record(11, stop="toolUse", usage=usage_of(), provider="amazon", model="us.claude-opus-5-5-medium"),
                   assistant_record(12, stop="toolUse", usage=usage_of(), provider="anthropic", model="anthropic/claude-opus-5-5"),
                   assistant_record(13, stop="toolUse", usage=usage_of(), provider=None, model="bare"), assistant_record(14, stop="toolUse", usage=usage_of(), model=None),
                   assistant_record(15, stop="toolUse"), assistant_record(16, stop="toolUse", usage="text")]
        self.assertEqual(["amazon/us.claude-opus-5-5-medium", "anthropic/claude-opus-5-5", "bare", ""], [r.model_name for r in requests_of(*records)])

    def test_the_one_hour_cache_split_comes_from_cttl_and_cannot_exceed_the_cache_writes(self):
        records = [assistant_record(11, stop="toolUse", usage=usage_of(cache_write=100, one_hour=40)), assistant_record(12, stop="toolUse", usage=usage_of(cache_write=100, one_hour=900)),
                   assistant_record(13, stop="toolUse", usage=usage_of(cache_write=100, one_hour="x")), assistant_record(14, stop="toolUse", usage={**usage_of(cache_write=100), "cttl": "x"})]
        self.assertEqual([(60, 40), (0, 100), (100, 0), (100, 0)], [(r.cache_write_5m_tokens, r.cache_write_1h_tokens) for r in requests_of(*records)])


class OmpReplyTest(unittest.TestCase):
    def test_the_reply_is_the_last_stop_message_of_the_turn_with_its_completion_time_and_later_messages_count_for_the_time_only(self):
        records = [assistant_record(11, "working", stop="toolUse"), assistant_record(20, "first answer", done=3), assistant_record(30, "second answer", done=5),
                   assistant_record(40, "after stop", stop="toolUse")]
        (prompt,) = prompts_of(user_record(10, "ask"), *records)
        self.assertEqual(("second answer", at(40), timedelta(seconds=30)), (prompt.reply_text, prompt.reply_at, prompt.working_time))

    def test_a_turn_without_a_stop_message_has_no_reply_but_works_until_its_last_message(self):
        (prompt,) = prompts_of(user_record(10, "ask"), assistant_record(11, "partial", stop="toolUse"), assistant_record(12, "cut", stop="aborted"))
        self.assertEqual((None, at(12), timedelta(seconds=2)), (prompt.reply_text, prompt.reply_at, prompt.working_time))

    def test_a_reply_is_its_text_blocks_that_are_not_blank_joined_by_blank_lines_and_empty_text_is_no_reply_text(self):
        message = assistant_record(11, usage=usage_of(), done=2)
        message["message"]["content"] = [{"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": "one"}, {"type": "text", "text": "  "}, {"type": "text", "text": "two"}]
        (prompt,) = prompts_of(user_record(10, "ask"), message)
        self.assertEqual(("one\n\ntwo", at(13)), (prompt.reply_text, prompt.reply_at))
        (prompt,) = prompts_of(user_record(10, "ask"), assistant_record(11, "", usage=usage_of(), done=2))
        self.assertEqual((None, at(13)), (prompt.reply_text, prompt.reply_at))
        message["message"]["content"] = "a plain string"
        self.assertEqual("a plain string", prompts_of(user_record(10, "ask"), message)[0].reply_text)

    def test_the_completion_time_is_the_message_completed_at_else_the_record_completed_at_else_the_message_time_else_the_record_time(self):
        by_message = assistant_record(11, "m", done=4)
        by_record = assistant_record(11, "r", done=None)
        by_record["completedAt"] = ms(17)
        by_message_time = assistant_record(11, "t", done=None)
        by_message_time["timestamp"] = iso(19)  # the record time differs from the message's own time
        by_record_time = assistant_record(11, "u", done=None, timestamp=None)
        by_record_time["timestamp"] = iso(21)
        early = assistant_record(1, "early", done=None)
        for name, record, expected in (("message", by_message, at(15)), ("record", by_record, at(17)), ("message time", by_message_time, at(11)),
                                       ("record time", by_record_time, at(21)), ("never before the prompt", early, at(10))):
            with self.subTest(name):
                (prompt,) = prompts_of(user_record(10, "ask"), record)
                self.assertEqual((expected, expected - at(10)), (prompt.reply_at, prompt.working_time))

    def test_a_stop_message_without_any_time_keeps_its_text_and_has_no_reply_time_and_works_no_time(self):
        message = assistant_record(11, "words", done=None, timestamp=None)
        del message["timestamp"]
        (prompt,) = prompts_of(user_record(10, "ask"), message)
        self.assertEqual(("words", None, timedelta(0)), (prompt.reply_text, prompt.reply_at, prompt.working_time))

    def test_every_prompt_has_its_own_turn(self):
        records = [user_record(10, "one"), assistant_record(11, "a one", done=1), user_record(20, "two"), assistant_record(21, "a two", done=2), user_record(30, "three")]
        self.assertEqual([("one", "a one"), ("two", "a two"), ("three", None)], [(p.text, p.reply_text) for p in prompts_of(*records)])


class OmpActivityTest(unittest.TestCase):
    def test_the_activity_starts_with_the_first_message_helper_or_custom_message_and_ends_with_the_newest_end_of_any(self):
        transcript = parse(header_record("s", second=3), init_record("task"), user_record(20, "task text", attribution="agent"), helper_record(22, usage_of(1, 1)),
                           assistant_record(30, "done", usage=usage_of(2, 2), done=5), {"type": "custom", "customType": "session_exit", "timestamp": iso(900)})
        self.assertEqual((at(20), at(35)), (transcript.first_moment, transcript.last_moment))

    def test_a_skipped_custom_message_and_a_tool_result_still_count_as_activity(self):
        tool_result = {"type": "message", "timestamp": iso(50), "message": {"role": "toolResult", "content": [], "timestamp": ms(50)}}
        transcript = parse(header_record("s"), custom_record(40, "not a prompt", custom_type="background-note"), tool_result)
        self.assertEqual(((), at(40), at(50)), (transcript.prompts, transcript.first_moment, transcript.last_moment))

    def test_a_transcript_without_a_timed_activity_record_has_no_bounds(self):
        transcript = parse(header_record("s"), {"type": "custom", "customType": "x"}, {"type": "message", "message": {"role": "user"}})
        self.assertEqual((None, None), (transcript.first_moment, transcript.last_moment))


class OmpTimeTest(unittest.TestCase):
    """What counts as a time: a positive number of epoch milliseconds or an aware ISO-8601 text."""

    def prompt_time(self, value):
        """The time of a prompt whose message carries `value` as its time and whose record carries none."""
        record = user_record(10, "ask", timestamp=value)
        record["timestamp"] = None
        prompts = prompts_of(record)
        return prompts[0].submitted_at if prompts else None

    def test_a_number_of_milliseconds_or_an_aware_iso_text_is_a_time(self):
        for value, expected in ((ms(12), at(12)), (float(ms(13)), at(13)), ("2026-10-13T08:42:14Z", at(14)), ("2026-10-13T10:42:15+02:00", at(15))):
            with self.subTest(value=value):
                self.assertEqual(expected, self.prompt_time(value))

    def test_anything_else_is_no_time_and_never_raises(self):
        for value in (True, False, 0, -5, 10**400, 10**30, float("inf"), float("nan"), "", "  ", "yesterday", str(ms(12)), "2026-10-13T08:42:00", [], {}, None):
            with self.subTest(value=value):
                self.assertIsNone(self.prompt_time(value))


if __name__ == "__main__":
    unittest.main()
