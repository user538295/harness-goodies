"""Claude usage report with sub-agents against the real price file (BE-13, S63)."""
import os
import re
import unittest
from pathlib import Path

from sessionlog.log_usage_writer import LogUsageWriter
from sessionlog.price_file import PriceFile
from sessionlog.pricing import PricingRules
from tests.test_claude_reader import _answer, _on_model, _typed
from tests.test_claude_subagents import ClaudeSubAgentTestCase, _agent_user

CENTS_PER_DOLLAR = 100
GOLDEN = os.path.join(os.path.dirname(__file__), "fixtures", "claude", "subagent_report.usage.txt")
PRICES = Path(__file__).resolve().parents[1] / "prices.json"
ESTIMATE_LINE = re.compile(r"^est\. used token: input: (?P<input>\d+), output: (?P<output>\d+), cache_create: (?P<cache_create>\d+), "
                           r"cache_read: (?P<cache_read>\d+), total_tokens: (?P<total>\d+), price: \$(?P<dollars>\d+)\.(?P<cents>\d\d), ")
SUMMED_COLUMNS = ("input", "output", "cache_create", "cache_read", "total")


def _tokens(input_tokens, output_tokens, **extra) -> dict:
    return {"input_tokens": input_tokens, "output_tokens": output_tokens, **extra}


def _reply(second, message_id, model, usage, **extra) -> dict:
    return _on_model(_answer(second, message_id, usage, **extra), model)


class ClaudeSubAgentReportTest(ClaudeSubAgentTestCase):
    def _write_session(self):
        """The golden's expected numbers come from `prices.json` (as_of 2026-10-21), per million tokens.

        Prompt 1: opus-5-5 in 4, out 20, cache write 5m 5 and 1h 8 -> 4.00 + 2.00 + 1.00 + 0.80 = 7.80;
                  fast in 8, out 40, cache read 0.4 -> 0.80 + 2.00 + 0.40 = 3.20; row 11.00.
        Prompt 2: sonnet-5-5 in 2, out 10 -> 1.00 + 0.10 = 1.10. Prompt 3 has no reply: no requests, 0.00.
        a1 haiku-4-5 in 1, out 5 -> 0.20 + 0.50 = 0.70; a2 sonnet-5-5 cache read 0.1 -> 0.20 + 0.20 + 0.015 = 0.415 (42 cents);
        b1 haiku-4-5 -> 0.01 + 0.005 = 0.015 (2 cents); w1 opus-5-5 fast -> 0.80 + 0.40 = 1.20.
        Rows 1100 + 110 + 0 + 70 + 42 + 2 + 120 = 1444 cents; the exact sum (1443) differs, so rows are summed after rounding.
        """
        self._main([
            _typed(1, "ask one"),
            _reply(3, "m1", "claude-opus-5-5", _tokens(1000000, 100000, cache_creation={
                "ephemeral_5m_input_tokens": 200000, "ephemeral_1h_input_tokens": 100000}), effort="high"),
            _reply(5, "m2", "claude-opus-5-5", _tokens(100000, 50000, cache_read_input_tokens=1000000, speed="fast"), effort="max"),
            _typed(20, "ask two"),
            _reply(22, "m3", "claude-sonnet-5-5", _tokens(500000, 10000)),
            _typed(40, "ask three"),
        ])
        self._agent("a1", [_agent_user(10, "a1"), _reply(12, "s1", "claude-haiku-4-5", _tokens(200000, 100000))],
                    {"agentType": "general-purpose"})
        self._agent("a2", [_agent_user(13, "a2"), _reply(14, "s2", "claude-sonnet-5-5", _tokens(100000, 20000, cache_read_input_tokens=150000))],
                    {"agentType": "Explore", "parentAgentId": "a1"})
        self._agent("w1", [_agent_user(11, "w1"), _reply(12, "s4", "claude-opus-5-5", _tokens(100000, 10000, speed="fast"), effort="low")],
                    {"agentType": "workflow-step"}, where=("workflows", "wf_x"))
        self._agent("b1", [_agent_user(11, "b1"), _reply(15, "s3", "claude-haiku-4-5", _tokens(10000, 1000))], {"agentType": "Plan"})

    def test_claude_report_equals_golden_file_byte_for_byte_and_total_equals_sum_of_rows(self):
        self._write_session()
        result = self._session()
        self.assertEqual(result.unreadable, ())
        report = LogUsageWriter("/logs", lambda moment: moment).render_report(PricingRules(PriceFile(PRICES)).build(result.session))
        with open(GOLDEN, encoding="utf-8", newline="") as handle:
            self.assertEqual(report, handle.read())
        *rows, total = [match for match in map(ESTIMATE_LINE.match, report.splitlines()) if match]
        self.assertEqual(len(rows), 7)
        for column in SUMMED_COLUMNS:
            self.assertEqual(sum(int(row[column]) for row in rows), int(total[column]), column)
        self.assertEqual(sum(int(row["dollars"]) * CENTS_PER_DOLLAR + int(row["cents"]) for row in rows),
                         int(total["dollars"]) * CENTS_PER_DOLLAR + int(total["cents"]))


if __name__ == "__main__":
    unittest.main()
