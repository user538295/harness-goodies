"""Grouping of a transcript's openings and answers into prompts and requests, shared by the OMP and OpenCode readers."""
import unittest
from datetime import datetime, timedelta, timezone
from sessionlog.model import Prompt, RequestUsage
from sessionlog.prompt_turns import NO_PROMPT_TEXT, Answer, Opening, group_turns, with_unprompted

BASE = datetime(2026, 10, 13, 8, 42, 0, tzinfo=timezone.utc)


def at(second):
    return BASE + timedelta(seconds=second)


def request(tokens):
    return RequestUsage(model_name="m", input_tokens=tokens, output_tokens=0, cache_read_tokens=0, cache_write_5m_tokens=0, cache_write_1h_tokens=0)


def stop(text, second, tokens=1):
    return Answer(request(tokens), True, text, at(second))


class GroupTurnsTest(unittest.TestCase):
    def test_an_opening_and_the_answers_up_to_the_next_opening_make_a_prompt_whose_reply_is_the_last_stopping_answer(self):
        prompts, requests, _ = group_turns([Opening(at(10), "one"), Answer(request(1), False, None, at(11)), stop("early", 12), stop("last", 14, tokens=3),
                                            Opening(at(30), "two"), stop("second", 33)])
        first, second = prompts
        self.assertEqual(("one", "last", at(14), timedelta(seconds=4), [1, 1, 3]),
                         (first.text, first.reply_text, first.reply_at, first.working_time, [r.input_tokens for r in first.requests]))
        self.assertEqual(("two", "second", timedelta(seconds=3)), (second.text, second.reply_text, second.working_time))
        self.assertEqual(4, len(requests))

    def test_a_reply_is_the_last_stopping_answer_even_when_answers_that_do_not_stop_come_after_it_and_the_turn_works_to_the_last_answer(self):
        (prompt,), *_ = group_turns([Opening(at(10), "ask"), stop("reply", 12), Answer(request(1), False, None, at(20))])
        self.assertEqual(("reply", at(20), timedelta(seconds=10)), (prompt.reply_text, prompt.reply_at, prompt.working_time))

    def test_answers_before_the_first_opening_fall_under_the_first_prompt(self):
        prompts, requests, _ = group_turns([stop("early", 5, tokens=7), Opening(at(10), "ask"), stop("late", 12, tokens=8)])
        self.assertEqual(([7, 8], "late"), ([r.input_tokens for r in prompts[0].requests], prompts[0].reply_text))
        self.assertEqual(2, len(requests))

    def test_a_turn_without_a_stopping_answer_has_no_reply_but_works_until_its_last_answer(self):
        (prompt,), *_ = group_turns([Opening(at(10), "ask"), Answer(request(1), False, None, at(11))])
        self.assertEqual((None, at(11), timedelta(seconds=1), 1), (prompt.reply_text, prompt.reply_at, prompt.working_time, len(prompt.requests)))

    def test_a_reply_before_its_prompt_is_dated_by_the_prompt_and_a_reply_without_time_has_no_reply_time(self):
        (early, timeless), *_ = group_turns([Opening(at(30), "a"), stop("r", 11), Opening(at(40), "b"), Answer(None, True, "no time", None)])
        self.assertEqual((at(30), timedelta(0)), (early.reply_at, early.working_time))
        self.assertEqual(("no time", None, timedelta(0)), (timeless.reply_text, timeless.reply_at, timeless.working_time))

    def test_an_opening_without_a_time_is_no_prompt_but_its_requests_stay_in_the_list_of_every_request_and_fall_under_no_prompt(self):
        prompts, requests, unprompted = group_turns([Opening(None, "lost"), stop("r", 11, tokens=5), Opening(at(20), "kept"), stop("ok", 21, tokens=6)])
        self.assertEqual((["kept"], [5, 6], [5]), ([prompt.text for prompt in prompts], [r.input_tokens for r in requests], [r.input_tokens for r in unprompted]))

    def test_requests_before_a_first_opening_without_a_time_fall_under_no_prompt_with_those_under_it(self):
        _, _, unprompted = group_turns([stop("early", 5, tokens=7), Opening(None, "lost"), stop("r", 11, tokens=5), Opening(at(20), "kept"), stop("ok", 21, tokens=6)])
        self.assertEqual([7, 5], [r.input_tokens for r in unprompted])

    def test_a_transcript_without_any_opening_leaves_every_request_under_no_prompt(self):
        prompts, requests, unprompted = group_turns([stop("a", 5, tokens=7), Answer(request(2), False, None, None)])
        self.assertEqual(((), [7, 2], [7, 2]), (prompts, [r.input_tokens for r in requests], [r.input_tokens for r in unprompted]))

    def test_requests_under_a_prompt_are_not_unprompted(self):
        _, _, unprompted = group_turns([stop("early", 5), Opening(at(10), "ask"), stop("late", 12)])
        self.assertEqual((), unprompted)

    def test_nothing_gives_nothing(self):
        self.assertEqual(((), (), ()), group_turns([]))
        self.assertEqual(((), (request(2),), (request(2),)), group_turns([Answer(request(2), False, None, None)]))


class WithUnpromptedTest(unittest.TestCase):
    def test_requests_under_no_prompt_make_a_leading_row_dated_by_the_session_start_with_no_reply_and_no_working_time(self):
        kept = Prompt(submitted_at=at(10), text="ask", reply_at=at(12), working_time=timedelta(seconds=2))
        row, same = with_unprompted((kept,), (request(3), request(4)), at(1))
        self.assertEqual((at(1), NO_PROMPT_TEXT, None, None, timedelta(0), [3, 4]),
                         (row.submitted_at, row.text, row.reply_at, row.reply_text, row.working_time, [r.input_tokens for r in row.requests]))
        self.assertIs(kept, same)

    def test_a_session_without_any_prompt_is_the_leading_row_alone(self):
        (row,) = with_unprompted((), (request(3),), at(1))
        self.assertEqual((NO_PROMPT_TEXT, 1), (row.text, len(row.requests)))

    def test_without_requests_under_no_prompt_the_prompts_stay_as_they_are(self):
        kept = (Prompt(submitted_at=at(10), text="ask"),)
        self.assertIs(kept, with_unprompted(kept, (), at(1)))


if __name__ == "__main__":
    unittest.main()
