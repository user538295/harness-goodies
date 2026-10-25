"""PricingRules acceptance examples (S18, S19, S20, S52)."""
import unittest
from datetime import datetime, timedelta, timezone

from sessionlog.model import Harness, Prompt, RequestUsage, Session, SubAgent
from sessionlog.pricing import PricingRules, Rates

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class Book:
    def rate_for(self, model_name, fast):
        if model_name == "known":
            return Rates(20, 40, 2, 25, 40) if fast else Rates(10, 20, 1, 12.5, 20)
        if model_name == "second" and not fast:
            return Rates(3, 7, 0.5, 4, 6)
        return None

    def problem(self):
        return None


def request(model="known", **changes):
    values = dict(model_name=model, input_tokens=0, output_tokens=0,
                  cache_read_tokens=0, cache_write_5m_tokens=0, cache_write_1h_tokens=0)
    values.update(changes)
    return RequestUsage(**values)


def report(*rows, harness=Harness.CLAUDE, agents=()):
    return PricingRules(Book()).build(Session(
        harness=harness, session_id="session", started_at=NOW,
        prompts=tuple(Prompt(submitted_at=NOW, text="hello", requests=row) for row in rows),
        sub_agents=agents))


class PricingTests(unittest.TestCase):
    def test_known_model_priced_with_own_cache_read_write_5m_1h_and_fast_rates(self):
        tokens = dict(input_tokens=1000000, output_tokens=1000000, cache_read_tokens=1000000,
                      cache_write_5m_tokens=1000000, cache_write_1h_tokens=1000000)
        result = report((request(**tokens, effort="high"),), (request(**tokens, fast=True),))
        self.assertEqual([row.price.cents for row in result.prompts], [6350, 12700])
        self.assertEqual(result.prompts[0].effort, "high")
        self.assertEqual(result.prompts[1].model_names, "known:fast")
        self.assertEqual(result.prompts[0].total_tokens, 5000000)

    def test_each_token_class_uses_selected_model_rate(self):
        fields = ("input_tokens", "output_tokens", "cache_read_tokens",
                  "cache_write_5m_tokens", "cache_write_1h_tokens")
        for model, fast, expected in (
                ("known", False, (1000, 2000, 100, 1250, 2000)),
                ("known", True, (2000, 4000, 200, 2500, 4000)),
                ("second", False, (300, 700, 50, 400, 600))):
            for field, cents in zip(fields, expected):
                with self.subTest(model=model, fast=fast, token_class=field):
                    result = report((request(model, fast=fast, **{field: 1000000}),))
                    self.assertEqual(result.prompts[0].price.cents, cents)

    def test_rounds_after_summing_requests_within_row(self):
        result = report((request(recorded_cost_usd=0.004), request(recorded_cost_usd=0.004)),)
        self.assertEqual(result.prompts[0].price.cents, 1)
        self.assertEqual(result.total.price_cents, 1)

    def test_total_includes_token_classes_and_unique_models_and_efforts(self):
        agent = SubAgent(agent_id="worker", type="worker", started_at=NOW,
                         requests=(request("second", output_tokens=7, cache_write_1h_tokens=11, effort="low"),))
        result = report((request(input_tokens=2, cache_read_tokens=3, cache_write_5m_tokens=5, effort="high"),),
                        agents=(agent,))
        self.assertEqual((result.total.input_tokens, result.total.output_tokens,
                          result.total.cache_read_tokens, result.total.cache_write_tokens), (2, 7, 3, 16))
        self.assertEqual(result.total.model_names, "known+second")
        self.assertEqual(result.total.effort, "high+low")

    def test_unknown_model_row_unknown_total_incomplete_never_zero(self):
        result = report((request("unknown", input_tokens=17),))
        self.assertEqual(result.prompts[0].price.status, "unknown")
        self.assertEqual(result.prompts[0].total_tokens, 17)
        self.assertTrue(result.total.price_incomplete)

    def test_rows_rounded_half_up_mixed_row_incomplete_total_rounds_the_exact_sum_once(self):
        result = report((request(recorded_cost_usd=0.005),),
                        (request(recorded_cost_usd=0.005), request("unknown")),
                        (request("unknown"),))
        self.assertEqual([row.price.cents for row in result.prompts], [1, 1, 0])
        self.assertEqual([row.price.status for row in result.prompts], ["priced", "partial", "unknown"])
        self.assertEqual(result.total.price_cents, 1)  # $0.005 + $0.005 = $0.01 exactly, not 1 + 1 cents
        self.assertTrue(result.total.price_incomplete)

    def test_recorded_cost_including_free_overrides_unknown_model(self):
        result = report((request("unknown", recorded_cost_usd=0),),
                        (request(recorded_cost_usd=0.125, input_tokens=1000000),))
        self.assertEqual([row.price.cents for row in result.prompts], [0, 13])
        self.assertFalse(result.total.price_incomplete)

    def test_empty_prompt_and_api_equivalent_labels(self):
        for harness in Harness:
            result = report((), harness=harness)
            self.assertEqual(result.total.api_equivalent, harness in (Harness.CODEX, Harness.CURSOR))
            self.assertEqual(result.prompts[0].working_time, timedelta(0))

    def test_row_without_requests_is_zero_priced_and_keeps_total_complete_unlike_unknown_model(self):
        agent = SubAgent(agent_id="idle", type="worker", started_at=NOW)
        result = report((request(recorded_cost_usd=0.25),), (), agents=(agent,))
        for row in (result.prompts[1], result.sub_agents[0]):
            self.assertEqual((row.price.cents, row.price.status, row.total_tokens), (0, "priced", 0))
        self.assertEqual(result.total.price_cents, 25)
        self.assertFalse(result.total.price_incomplete)
        self.assertTrue(report((request("unknown"),), agents=(agent,)).total.price_incomplete)

    def test_cursor_total_is_always_labelled_main_only_and_incomplete(self):
        result = report((request(recorded_cost_usd=0),), harness=Harness.CURSOR)
        self.assertTrue(result.total.price_incomplete)
        self.assertEqual(result.total.label, "tokens: main session only — Cursor does not record sub-agent tokens")

    def test_working_time_sums_only_replied_main_prompts(self):
        prompts = tuple(Prompt(submitted_at=NOW, text="hello", reply_at=NOW + timedelta(seconds=seconds),
                               working_time=timedelta(seconds=seconds)) for seconds in (5, 7))
        agent = SubAgent(agent_id="worker", type="worker", started_at=NOW, working_time=timedelta(seconds=100))
        result = PricingRules(Book()).build(Session(harness=Harness.CLAUDE, session_id="time",
                                                   started_at=NOW, prompts=prompts, sub_agents=(agent,)))
        self.assertEqual(result.total.working_time, timedelta(seconds=12))

    def test_prompt_head_collapses_breaks_and_preserves_unicode_boundary(self):
        prompt = Prompt(submitted_at=NOW, text="a\n\t" + "ő" * 58 + "🙂end")
        result = PricingRules(Book()).build(Session(harness=Harness.CLAUDE, session_id="head",
                                                   started_at=NOW, prompts=(prompt,)))
        self.assertEqual(result.prompts[0].prompt_head, "a " + "ő" * 58)

    def test_row_models_and_efforts_are_sorted_unique_with_fast_marker(self):
        result = report((request("second", effort="low"), request(effort="high", fast=True),
                         request(effort="high"), request(effort="high")),)
        self.assertEqual(result.prompts[0].model_names, "known+known:fast+second")
        self.assertEqual(result.prompts[0].effort, "high+low")

    def test_nested_agents_count_once_and_missing_cursor_tokens_incomplete(self):
        child = SubAgent(agent_id="child", type="worker", started_at=NOW,
                         requests=(request(recorded_cost_usd=0.005, output_tokens=7),))
        parent = SubAgent(agent_id="parent", type="worker", started_at=NOW,
                          requests=(request(recorded_cost_usd=0.005, input_tokens=3),), sub_agents=(child,))
        result = report((request(recorded_cost_usd=0.005, input_tokens=2),), agents=(parent,))
        self.assertEqual([(a.agent_id, a.depth) for a in result.sub_agents], [("parent", 0), ("child", 1)])
        self.assertEqual(result.total.total_tokens, 12)
        self.assertEqual(result.total.price_cents, 2)  # $0.015 exactly rounds half-up once, not 1 + 1 + 1 cents
        self.assertEqual(result.total.sub_agent_count, 2)
        missing = SubAgent(agent_id="missing", type="worker", started_at=NOW, tokens_not_recorded=True)
        result = report((request(recorded_cost_usd=0),), harness=Harness.CURSOR, agents=(missing,))
        self.assertTrue(result.total.price_incomplete)
        self.assertIsNone(result.sub_agents[0].total_tokens)

    def test_total_includes_every_subagent_and_working_time_is_main_only(self):
        child = SubAgent(agent_id="child", type="worker", started_at=NOW, working_time=timedelta(seconds=50),
                         requests=(request(recorded_cost_usd=0.005, output_tokens=7),))
        parent = SubAgent(agent_id="parent", type="worker", started_at=NOW, working_time=timedelta(seconds=100),
                          requests=(request(recorded_cost_usd=0.005, input_tokens=3),), sub_agents=(child,))
        prompt = Prompt(submitted_at=NOW, text="hello", reply_at=NOW + timedelta(seconds=4),
                        working_time=timedelta(seconds=4), requests=(request(recorded_cost_usd=0.005, input_tokens=2),))
        result = PricingRules(Book()).build(Session(harness=Harness.CLAUDE, session_id="total", started_at=NOW,
                                                   prompts=(prompt,), sub_agents=(parent,)))
        rows = (*result.prompts, *result.sub_agents)
        self.assertEqual([row.price.cents for row in rows], [1, 1, 1])
        self.assertEqual((result.total.total_tokens, result.total.price_cents, result.total.request_count,
                          result.total.sub_agent_count), (12, 2, 1, 2))
        self.assertEqual(result.total.working_time, timedelta(seconds=4))
        self.assertFalse(result.total.price_incomplete)

    def test_total_working_time_is_the_sum_of_the_whole_seconds_shown_on_main_rows(self):
        prompts = tuple(Prompt(submitted_at=NOW, text="hello", reply_at=NOW + timedelta(seconds=5),
                               working_time=timedelta(seconds=4.9)) for _ in range(2))
        result = PricingRules(Book()).build(Session(harness=Harness.CLAUDE, session_id="whole", started_at=NOW,
                                                   prompts=prompts))
        self.assertEqual([row.working_time for row in result.prompts], [timedelta(seconds=4)] * 2)
        self.assertEqual(result.total.working_time, timedelta(seconds=8))

    def test_unreplied_prompt_has_zero_working_time_whatever_its_recorded_time(self):
        prompt = Prompt(submitted_at=NOW, text="hello", working_time=timedelta(seconds=9))
        result = PricingRules(Book()).build(Session(harness=Harness.CLAUDE, session_id="open", started_at=NOW, prompts=(prompt,)))
        self.assertEqual((result.prompts[0].working_time, result.total.working_time), (timedelta(0), timedelta(0)))

    def test_fraction_below_half_a_cent_rounds_down_and_missing_tokens_make_the_agent_row_incomplete(self):
        self.assertEqual(report((request(recorded_cost_usd=0.004),)).prompts[0].price.cents, 0)
        for requests, status in (((), "unknown"), ((request(recorded_cost_usd=0.125),), "partial")):
            agent = SubAgent(agent_id="missing", type="worker", started_at=NOW, requests=requests, tokens_not_recorded=True)
            self.assertEqual(report(agents=(agent,)).sub_agents[0].price.status, status)

    def test_unknown_model_subagent_marks_total_incomplete_while_cents_still_sum(self):
        agent = SubAgent(agent_id="new-model", type="worker", started_at=NOW,
                         requests=(request("unknown", input_tokens=5), request(recorded_cost_usd=0.5)))
        result = report((request(recorded_cost_usd=0.25),), agents=(agent,))
        self.assertEqual((result.sub_agents[0].price.status, result.sub_agents[0].price.cents), ("partial", 50))
        self.assertEqual((result.total.price_cents, result.total.price_incomplete), (75, True))

    def test_subagent_rows_with_same_start_ordered_by_id_at_each_level(self):
        def agent(agent_id, started=NOW, children=()):
            return SubAgent(agent_id=agent_id, type="worker", started_at=started, sub_agents=children)

        later = NOW + timedelta(seconds=1)
        result = report(agents=(
            agent("agent-c", children=(agent("agent-y", later), agent("agent-z"), agent("agent-x"))),
            agent("agent-late", later), agent("agent-b"), agent("agent-a", later)))
        self.assertEqual([(row.agent_id, row.depth) for row in result.sub_agents], [
            ("agent-b", 0), ("agent-c", 0), ("agent-x", 1), ("agent-z", 1), ("agent-y", 1),
            ("agent-a", 0), ("agent-late", 0)])
