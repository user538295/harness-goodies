"""Unit tests of the normalized session model (C2): the id rule, validation, collection normalization, the sub-agent row order and lastActivityAt.

Run: python3 -m unittest discover -s skills/session-log/tests -t skills/session-log
"""
import dataclasses
import unittest
from datetime import datetime, timedelta, timezone, tzinfo

from sessionlog.model import (
    Harness,
    Prompt,
    RequestUsage,
    Session,
    SubAgent,
    is_safe_id,
)

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
NAIVE = datetime(2026, 1, 1, 12, 0, 0)

PATH_TRICKS = ["..", ".", "../x", "a/b", "a\\b", "a.b", ""]
PUNCTUATION = ["[", "]", "^", "`", ":", "*", "?", "<", ">", "|", '"', "~", "%", "@", "."]
BASE_DEVICE_NAMES = ["CON", "PRN", "AUX", "NUL"]
NUMBERED_DEVICE_NAMES = ["%s%d" % (prefix, digit) for prefix in ("COM", "LPT") for digit in range(1, 10)]
DEVICE_NAMES = BASE_DEVICE_NAMES + NUMBERED_DEVICE_NAMES
SAFE_IDS = ["a", "A-b_9", "0198f2c4-7d3e-7a10-9b1c-2f4e5d6a7b8c", "CONSOLE", "COM10"]
MAX_LENGTH_LITERAL = 128
HARNESS_VALUES = {"claude", "codex", "cursor", "omp", "opencode"}
NON_STRINGS = [None, 5, b"abc", ["abc"]]
NON_DATETIMES = [None, "2026-01-01T12:00:00+00:00", 5]
TOKEN_FIELDS = ["input_tokens", "output_tokens", "cache_read_tokens", "cache_write_5m_tokens", "cache_write_1h_tokens"]
PLUS_TWO = timezone(timedelta(hours=2))
MINUS_FIVE = timezone(timedelta(hours=-5))


class NoOffset(tzinfo):
    def utcoffset(self, moment):
        return None

    def dst(self, moment):
        return None


def usage(model_name="m", **overrides):
    counts = dict.fromkeys(TOKEN_FIELDS, 1)
    counts.update(overrides)
    return RequestUsage(model_name=model_name, **counts)


def at(minutes):
    return T0 + timedelta(minutes=minutes)


def agent(agent_id, start, **kwargs):
    return SubAgent(agent_id=agent_id, type="t", started_at=start, **kwargs)


def session(prompts=(), sub_agents=(), session_id="s1", started_at=T0):
    return Session(
        harness=Harness.CLAUDE,
        session_id=session_id,
        started_at=started_at,
        prompts=prompts,
        sub_agents=sub_agents,
    )


def with_cases(names):
    return [variant for name in names for variant in (name, name.lower(), name.capitalize())]


class SafeIdTest(unittest.TestCase):
    def test_safe_id_rejects_dotdot_slash_and_windows_device_names(self):
        for value in PATH_TRICKS + with_cases(DEVICE_NAMES):
            with self.subTest(value=value):
                self.assertFalse(is_safe_id(value))

    def test_safe_id_rejects_punctuation(self):
        for char in PUNCTUATION:
            with self.subTest(char=char):
                self.assertFalse(is_safe_id("a" + char + "b"))
                self.assertFalse(is_safe_id(char))

    def test_safe_id_accepts_ids_made_of_letters_digits_underscore_and_dash(self):
        for value in SAFE_IDS:
            with self.subTest(value=value):
                self.assertTrue(is_safe_id(value))

    def test_safe_id_length_is_1_to_128(self):
        self.assertTrue(is_safe_id("a" * 128))
        self.assertFalse(is_safe_id("a" * 129))

    def test_safe_id_rejects_non_strings(self):
        for value in NON_STRINGS:
            with self.subTest(value=value):
                self.assertFalse(is_safe_id(value))

    def test_safe_id_rejects_trailing_newline_whitespace_and_non_ascii(self):
        for value in ["abc\n", " abc", "a b", "é", "ａｂｃ", "٣", "a\x00b"]:
            with self.subTest(value=value):
                self.assertFalse(is_safe_id(value))


class HarnessTest(unittest.TestCase):
    def test_harness_values_are_the_five_lowercase_names(self):
        self.assertEqual({member.value for member in Harness}, HARNESS_VALUES)
        self.assertEqual(len(Harness), len(HARNESS_VALUES))


class SessionIdTest(unittest.TestCase):
    def test_session_rejects_unsafe_session_ids(self):
        for value in ["../x", "CON", "", "a/b", "a" * (MAX_LENGTH_LITERAL + 1)]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    session(session_id=value)

    def test_session_rejects_non_string_session_ids(self):
        for value in NON_STRINGS:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    session(session_id=value)

    def test_session_accepts_safe_session_id(self):
        self.assertEqual(session(session_id="A-b_9").session_id, "A-b_9")

    def test_sub_agent_ids_are_not_validated(self):
        unusual = [agent("CON", at(1)), agent("../x", at(2))]

        self.assertEqual([a.agent_id for a in session(sub_agents=unusual).sub_agents], ["CON", "../x"])


class AwareTimeTest(unittest.TestCase):
    def test_naive_times_are_rejected(self):
        cases = {
            "session started_at": lambda: session(started_at=NAIVE),
            "prompt submitted_at": lambda: Prompt(submitted_at=NAIVE, text="x"),
            "prompt reply_at": lambda: Prompt(submitted_at=T0, text="x", reply_at=NAIVE),
            "sub-agent started_at": lambda: agent("a", NAIVE),
        }
        for name, build in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    build()

    def test_required_times_must_be_real_aware_datetimes(self):
        offsetless = datetime(2026, 1, 1, 12, 0, 0, tzinfo=NoOffset())
        builders = {
            "session started_at": lambda value: session(started_at=value),
            "prompt submitted_at": lambda value: Prompt(submitted_at=value, text="x"),
            "sub-agent started_at": lambda value: agent("a", value),
        }
        for name, build in builders.items():
            for value in NON_DATETIMES + [offsetless]:
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ValueError):
                        build(value)

    def test_optional_reply_time_may_be_absent_but_not_a_non_datetime(self):
        with self.assertRaises(ValueError):
            Prompt(submitted_at=T0, text="x", reply_at="soon")

    def test_aware_times_and_absent_reply_are_accepted(self):
        self.assertIsNone(Prompt(submitted_at=at(1), text="x").reply_at)
        self.assertEqual(Prompt(submitted_at=at(1), text="x", reply_at=at(2)).reply_at, at(2))


class RequestUsageTest(unittest.TestCase):
    def test_each_token_count_is_required(self):
        for omitted in TOKEN_FIELDS:
            with self.subTest(omitted=omitted):
                counts = {name: 1 for name in TOKEN_FIELDS if name != omitted}
                with self.assertRaises(TypeError):
                    RequestUsage(model_name="m", **counts)

    def test_optional_fields_default_to_absent(self):
        request = usage()

        self.assertEqual((request.effort, request.fast, request.recorded_cost_usd), (None, None, None))


class NormalizationTest(unittest.TestCase):
    def test_lists_and_tuples_build_equal_hashable_sessions(self):
        prompt = Prompt(submitted_at=at(1), text="a", requests=[usage()])
        same = Prompt(submitted_at=at(1), text="a", requests=(usage(),))
        child = agent("c", at(2), requests=[usage()], sub_agents=[])
        parent = agent("p", at(1), sub_agents=[child])

        from_lists = session(prompts=[prompt], sub_agents=[parent])
        from_tuples = session(
            prompts=(same,), sub_agents=(agent("p", at(1), sub_agents=(agent("c", at(2), requests=(usage(),)),)),)
        )

        self.assertEqual(from_lists, from_tuples)
        self.assertEqual(hash(from_lists), hash(from_tuples))
        self.assertIsInstance(from_lists.prompts, tuple)
        self.assertIsInstance(from_lists.prompts[0].requests, tuple)
        self.assertIsInstance(from_lists.sub_agents, tuple)
        self.assertIsInstance(from_lists.sub_agents[0].sub_agents, tuple)
        self.assertEqual(from_lists.sub_agents[0].sub_agents[0].requests, (usage(),))
        self.assertIsInstance(from_lists.sub_agents[0].sub_agents[0].requests, tuple)


class DefaultsAndImmutabilityTest(unittest.TestCase):
    def test_optional_fields_default_to_absent_and_entities_are_frozen(self):
        built = session(prompts=[Prompt(submitted_at=at(1), text="x")], sub_agents=[agent("a", at(2))])
        prompt, sub_agent = built.prompts[0], built.sub_agents[0]

        self.assertEqual((built.project_dir, built.orphan_of_parent_id), (None, None))
        self.assertEqual((prompt.reply_text, prompt.working_time, prompt.requests), (None, None, ()))
        self.assertEqual((sub_agent.tokens_not_recorded, sub_agent.working_time), (False, None))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            built.session_id = "other"
        with self.assertRaises(dataclasses.FrozenInstanceError):
            prompt.text = "other"
        with self.assertRaises(dataclasses.FrozenInstanceError):
            sub_agent.agent_id = "other"


class SubAgentOrderTest(unittest.TestCase):
    def test_sub_agents_ordered_by_start_then_id(self):
        ordered = session(sub_agents=[agent("a", at(5)), agent("b", at(1)), agent("a", at(1))]).sub_agents

        self.assertEqual([(s.started_at, s.agent_id) for s in ordered], [(at(1), "a"), (at(1), "b"), (at(5), "a")])

    def test_nested_sub_agents_are_ordered_the_same_way(self):
        parent = agent("p", at(1), sub_agents=(agent("c1", at(9)), agent("c2", at(2))))

        self.assertEqual([c.agent_id for c in parent.sub_agents], ["c2", "c1"])

    def test_nested_sub_agents_with_equal_start_are_ordered_by_id(self):
        parent = agent("p", at(1), sub_agents=[agent("z", at(2)), agent("y", at(2)), agent("x", at(3))])

        self.assertEqual([c.agent_id for c in parent.sub_agents], ["y", "z", "x"])


class LastActivityTest(unittest.TestCase):
    def test_last_activity_includes_sub_agent_activity(self):
        prompt = Prompt(submitted_at=at(1), text="hi", reply_at=at(2))
        nested = agent("n", at(10), working_time=timedelta(minutes=5))
        parent = agent("p", at(3), sub_agents=(nested,))

        self.assertEqual(session(prompts=[prompt], sub_agents=[parent]).last_activity_at, at(15))

    def test_last_activity_is_the_prompt_when_later_than_every_sub_agent(self):
        prompt = Prompt(submitted_at=at(1), text="hi", reply_at=at(30))

        self.assertEqual(session(prompts=[prompt], sub_agents=[agent("p", at(3))]).last_activity_at, at(30))

    def test_last_activity_is_newest_prompt_or_reply_time(self):
        replied = Prompt(submitted_at=at(1), text="a", reply_at=at(4))
        unanswered = Prompt(submitted_at=at(3), text="b")

        self.assertEqual(session(prompts=[replied, unanswered]).last_activity_at, at(4))
        self.assertEqual(session(prompts=[unanswered]).last_activity_at, at(3))

    def test_last_activity_of_a_sub_agent_without_working_time_is_its_start(self):
        self.assertEqual(session(sub_agents=[agent("p", at(7))]).last_activity_at, at(7))

    def test_last_activity_takes_the_latest_end_not_the_last_started_sub_agent(self):
        long_early = agent("a", at(1), working_time=timedelta(minutes=60))
        short_late = agent("b", at(5), working_time=timedelta(minutes=1))

        self.assertEqual(session(sub_agents=[long_early, short_late]).last_activity_at, at(61))

    def test_sub_agent_end_later_than_its_nested_children_wins(self):
        child = agent("c", at(2), working_time=timedelta(minutes=1))
        parent = agent("p", at(1), working_time=timedelta(minutes=50), sub_agents=(child,))

        self.assertEqual(parent.last_activity_at, at(51))

    def test_last_activity_is_the_second_prompts_reply_when_it_is_the_maximum(self):
        first = Prompt(submitted_at=at(1), text="a", reply_at=at(2))
        second = Prompt(submitted_at=at(3), text="b", reply_at=at(9))

        self.assertEqual(session(prompts=[first, second]).last_activity_at, at(9))

    def test_last_activity_is_the_later_of_two_top_level_sub_agents(self):
        first = agent("a", at(1), working_time=timedelta(minutes=1))
        second = agent("b", at(5), working_time=timedelta(minutes=20))

        self.assertEqual(session(sub_agents=[first, second]).last_activity_at, at(25))

    def test_sub_agent_activity_is_the_later_of_two_nested_children(self):
        first = agent("c1", at(2), working_time=timedelta(minutes=1))
        second = agent("c2", at(3), working_time=timedelta(minutes=20))
        parent = agent("p", at(1), sub_agents=(first, second))

        self.assertEqual(parent.last_activity_at, at(23))

    def test_last_activity_of_an_empty_session_is_its_start(self):
        self.assertEqual(session().last_activity_at, T0)

    def test_session_start_is_only_the_fallback_not_a_candidate(self):
        prompt = Prompt(submitted_at=at(-5), text="early")

        self.assertEqual(session(prompts=[prompt]).last_activity_at, at(-5))


class NonUtcOffsetTest(unittest.TestCase):
    def test_sub_agents_are_ordered_by_instant_not_wall_clock(self):
        early_instant = agent("a", datetime(2026, 1, 1, 12, 0, tzinfo=PLUS_TWO))
        late_instant = agent("b", datetime(2026, 1, 1, 6, 0, tzinfo=MINUS_FIVE))

        ordered = session(sub_agents=[late_instant, early_instant]).sub_agents

        self.assertEqual([a.agent_id for a in ordered], ["a", "b"])

    def test_last_activity_is_chosen_by_instant_not_wall_clock(self):
        prompt = Prompt(submitted_at=datetime(2026, 1, 1, 12, 0, tzinfo=PLUS_TWO), text="x")
        later_instant = agent("b", datetime(2026, 1, 1, 6, 0, tzinfo=MINUS_FIVE))

        self.assertEqual(session(prompts=[prompt], sub_agents=[later_instant]).last_activity_at, later_instant.started_at)


if __name__ == "__main__":
    unittest.main()
