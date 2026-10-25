"""Shipped prices.json: real model ids keep their price, sibling/successor ids stay unknown (S18)."""
import json
import unittest
from pathlib import Path

from sessionlog.price_file import PriceFile
from sessionlog.pricing import Rates

SHIPPED = Path(__file__).resolve().parents[1] / "prices.json"
OPUS_5 = Rates(5, 25, 0.5, 6.25, 10)
OPUS_5_5 = Rates(4, 20, 0.2, 5, 8)
SONNET_5_5 = Rates(2, 10, 0.1, 2.5, 4)
SONNET_4 = Rates(3, 15, 0.3, 3.75, 6)
FABLE_5_1 = Rates(10, 50, 0.25, 12.5, 20)
HAIKU_4_5 = Rates(1, 5, 0.1, 1.25, 2)
GPT_5 = Rates(1.25, 10, 0.125, 0, 0)
GPT_5_5 = Rates(5, 30, 0.5, 0, 0)
GPT_6_SOL = Rates(2, 10, 0.2, 2.5, 5)
GPT_5_6_LUNA = Rates(0.2, 1.2, 0.02, 0.25, 0.5)

# Model ids as found in real Claude Code, Codex and omp stores.
REAL_IDS_PRICED = (
    ("claude-opus-5-5", OPUS_5_5),
    ("claude-opus-5", OPUS_5),
    ("claude-opus-4-8", OPUS_5),
    ("claude-opus-4-8:medium", OPUS_5),
    ("anthropic/claude-opus-4-8:medium", OPUS_5),
    ("anthropic/claude-opus-5-5", OPUS_5_5),
    ("claude-sonnet-5-5", SONNET_5_5),
    ("anthropic/claude-sonnet-5-5", SONNET_5_5),
    ("claude-sonnet-4-6", SONNET_4),
    ("claude-fable-5-1", FABLE_5_1),
    ("claude-fable-5-1[1m]", FABLE_5_1),
    ("claude-haiku-4-5-20251001", HAIKU_4_5),
    ("claude-opus-4-5-20251101", OPUS_5),
    ("gpt-5.5", GPT_5_5),
    ("gpt-6-sol", GPT_6_SOL),
    ("gpt-5.6-luna", GPT_5_6_LUNA),
    ("openai-codex/gpt-5.6-luna", GPT_5_6_LUNA),
    ("openai-codex/gpt-6-sol", GPT_6_SOL),
    ("gpt-5-2025-08-07", GPT_5),
    ("gpt-5-high", GPT_5),
    ("gpt-5.5-xhigh", GPT_5_5),
    ("gpt-5.5-2026-04-23", GPT_5_5),
)
SIBLINGS_AND_SUCCESSORS = (
    "gpt-5.7", "gpt-5.3", "gpt-5.5-mini", "gpt-6-sol-mini", "gpt-6.2-sol", "gpt-5.6-sol-mini",
    "claude-opus-5-6", "claude-sonnet-4-7", "claude-sonnet-5-6", "claude-fable-5-2", "claude-haiku-4-6",
    "gpt-55", "claude-opus-50", "openai-codex/gpt-5.7",
)
NEVER_PRICED = ("claude-haiku-5-5", "fable", "opus", "sonnet", "<synthetic>", "codex-auto-review",
                "deepseek-v4-pro", "ollama-cloud/nemotron-3-ultra")


class RealStoreModelIdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.book = PriceFile(SHIPPED)

    def test_every_real_id_keeps_its_published_price(self):
        for name, expected in REAL_IDS_PRICED:
            with self.subTest(name=name):
                self.assertEqual(self.book.rate_for(name, False), expected)

    def test_sibling_or_successor_of_a_priced_model_is_unknown_not_the_older_price(self):
        for name in SIBLINGS_AND_SUCCESSORS:
            with self.subTest(name=name):
                self.assertIsNone(self.book.rate_for(name, False))

    def test_unpriceable_real_ids_are_unknown(self):
        for name in NEVER_PRICED:
            with self.subTest(name=name):
                self.assertIsNone(self.book.rate_for(name, False))

    def test_haiku_5_5_has_no_flat_row_because_its_price_depends_on_prompt_size(self):
        shipped = {prefix for model in json.loads(SHIPPED.read_text(encoding="utf-8"))["models"]
                   for prefix in model["prefixes"]}
        self.assertNotIn("claude-haiku-5-5", shipped)


if __name__ == "__main__":
    unittest.main()
