"""Grouping of a transcript's openings and answers into prompts and requests, shared by the OMP and OpenCode readers."""
import unittest
from datetime import datetime, timedelta, timezone
from sessionlog.model import RequestUsage
from sessionlog.prompt_turns import Answer, Opening, group_turns

BASE = datetime(2026, 10, 13, 8, 42, 0, tzinfo=timezone.utc)


def at(second):
    return BASE + timedelta(seconds=second)


def request(tokens):
    return RequestUsage(model_name="m", input_tokens=tokens, output_tokens=0, cache_read_tokens=0, cache_write_5m_tokens=0, cache_write_1h_tokens=0)


def stop(text, second, tokens=1):
    return Answer(request(tokens), True, text, at(second))


class GroupTurnsTest(unittest.TestCase):
    def test_an_opening_and_the_answers_up_to_the_next_opening_make_a_prompt_whose_reply_is_the_last_stopping_answer(self):
        prompts, requests = group_turns([Opening(at(10), "one"), Answer(request(1), False, None, at(11)), stop("early", 12), stop("last", 14, tokens=3),
                                         Opening(at(30), "two"), stop("second", 33)])
        first, second = prompts
        self.assertEqual(("one", "last", at(14), timedelta(seconds=4), [1, 1, 3]),
                         (first.text, first.reply_text, first.reply_at, first.working_time, [r.input_tokens for r in first.requests]))
        self.assertEqual(("two", "second", timedelta(seconds=3)), (second.text, second.reply_text, second.working_time))
        self.assertEqual(4, len(requests))

    def test_a_reply_is_the_last_stopping_answer_even_when_answers_that_do_not_stop_come_after_it_and_the_turn_works_to_the_last_answer(self):
        (prompt,), _ = group_turns([Opening(at(10), "ask"), stop("reply", 12), Answer(request(1), False, None, at(20))])
        self.assertEqual(("reply", at(20), timedelta(seconds=10)), (prompt.reply_text, prompt.reply_at, prompt.working_time))

    def test_answers_before_the_first_opening_fall_under_the_first_prompt(self):
        prompts, requests = group_turns([stop("early", 5, tokens=7), Opening(at(10), "ask"), stop("late", 12, tokens=8)])
        self.assertEqual(([7, 8], "late"), ([r.input_tokens for r in prompts[0].requests], prompts[0].reply_text))
        self.assertEqual(2, len(requests))

    def test_a_turn_without_a_stopping_answer_has_no_reply_but_works_until_its_last_answer(self):
        (prompt,), _ = group_turns([Opening(at(10), "ask"), Answer(request(1), False, None, at(11))])
        self.assertEqual((None, at(11), timedelta(seconds=1), 1), (prompt.reply_text, prompt.reply_at, prompt.working_time, len(prompt.requests)))

    def test_a_reply_before_its_prompt_is_dated_by_the_prompt_and_a_reply_without_time_has_no_reply_time(self):
        (early, timeless), _ = group_turns([Opening(at(30), "a"), stop("r", 11), Opening(at(40), "b"), Answer(None, True, "no time", None)])
        self.assertEqual((at(30), timedelta(0)), (early.reply_at, early.working_time))
        self.assertEqual(("no time", None, timedelta(0)), (timeless.reply_text, timeless.reply_at, timeless.working_time))

    def test_an_opening_without_a_time_is_no_prompt_but_its_requests_stay_in_the_list_of_every_request(self):
        prompts, requests = group_turns([Opening(None, "lost"), stop("r", 11, tokens=5), Opening(at(20), "kept"), stop("ok", 21, tokens=6)])
        self.assertEqual((["kept"], [5, 6]), ([prompt.text for prompt in prompts], [r.input_tokens for r in requests]))

    def test_nothing_gives_nothing(self):
        self.assertEqual(((), ()), group_turns([]))
        self.assertEqual(((), (request(2),)), group_turns([Answer(request(2), False, None, None)]))


if __name__ == "__main__":
    unittest.main()
