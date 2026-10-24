"""PriceFile behavior at the C4 lookup seam (S51)."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sessionlog.model import Harness, Prompt, RequestUsage, Session
from sessionlog.price_file import PriceFile
from sessionlog.pricing import PricingRules, Rates

SHIPPED = Path(__file__).resolve().parents[1] / "prices.json"
FAST = {"in": 4, "out": 20, "cache_read": 0.4, "cache_write_5m": 5, "cache_write_1h": 8}

CLAUDE_PAGE = "https://platform.claude.com/docs/en/about-claude/pricing"
OPENAI_PAGE = "https://developers.openai.com/api/docs/pricing"
CURSOR_PAGE = "https://cursor.com/docs/models-and-pricing"
OFFICIAL_PAGES = (CLAUDE_PAGE, OPENAI_PAGE, CURSOR_PAGE)
OPUS_5_5_FAST = (8, 40, 0.4, 10, 16)
OPUS_5_FAST = (10, 50, 1, 12.5, 20)
OPUS_4_7_FAST = (30, 150, 3, 37.5, 60)
GPT_5_FAST = (2.5, 20, 0.25, 0, 0)
COMPOSER_FAST = (3, 15, 0.5, 0, 0)
# prefix -> (normal, fast or None) in USD per million tokens, typed from the three official pages
# (in, out, cache read, cache write 5m / OpenAI 30m, cache write 1h / OpenAI 12h).
PUBLISHED = {
    "claude-opus-5-5": ((4, 20, 0.2, 5, 8), OPUS_5_5_FAST),
    "claude-opus-5": ((5, 25, 0.5, 6.25, 10), OPUS_5_FAST),
    "claude-opus-4-8": ((5, 25, 0.5, 6.25, 10), OPUS_5_FAST),
    "claude-opus-5-5-fast": (OPUS_5_5_FAST, OPUS_5_5_FAST),
    "claude-opus-5-fast": (OPUS_5_FAST, OPUS_5_FAST),
    "claude-opus-4-8-fast": (OPUS_5_FAST, OPUS_5_FAST),
    "claude-opus-4-7-fast": (OPUS_4_7_FAST, OPUS_4_7_FAST),
    "claude-opus-4-7": ((5, 25, 0.5, 6.25, 10), None),
    "claude-opus-4-6": ((5, 25, 0.5, 6.25, 10), None),
    "claude-opus-4-5": ((5, 25, 0.5, 6.25, 10), None),
    "claude-sonnet-5-5": ((2, 10, 0.1, 2.5, 4), None),
    "claude-sonnet-5": ((2, 10, 0.2, 2.5, 4), None),
    "claude-sonnet-4-6": ((3, 15, 0.3, 3.75, 6), None),
    "claude-sonnet-4-5": ((3, 15, 0.3, 3.75, 6), None),
    "claude-sonnet-4": ((3, 15, 0.3, 3.75, 6), None),
    "claude-fable-5-1": ((10, 50, 0.25, 12.5, 20), None),
    "claude-mythos-5-1": ((10, 50, 0.25, 12.5, 20), None),
    "claude-fable-5": ((10, 50, 1, 12.5, 20), None),
    "claude-mythos-5": ((10, 50, 1, 12.5, 20), None),
    "claude-haiku-4-5": ((1, 5, 0.1, 1.25, 2), None),
    "gpt-6-astra": ((10, 50, 1, 12.5, 25), None),
    "gpt-6.1-sol": ((2, 10, 0.1, 2.5, 5), None),
    "gpt-6-sol": ((2, 10, 0.2, 2.5, 5), None),
    "gpt-6-luna": ((0.1, 0.5, 0.01, 0.125, 0.25), None),
    "gpt-5.6-sol": ((4, 20, 0.4, 5, 10), None),
    "gpt-5.6-terra": ((2, 12, 0.2, 2.5, 5), None),
    "gpt-5.6-luna": ((0.2, 1.2, 0.02, 0.25, 0.5), None),
    "gpt-5.6-cyber": ((12.5, 75, 1.25, 15.625, 31.25), None),
    "gpt-5.5": ((5, 30, 0.5, 0, 0), None),
    "gpt-5.5-pro": ((30, 180, 30, 0, 0), None),
    "gpt-5.5-cyber": ((12.5, 75, 1.25, 0, 0), None),
    "gpt-5.4": ((2.5, 15, 0.25, 0, 0), None),
    "gpt-5.4-pro": ((30, 180, 30, 0, 0), None),
    "gpt-5.4-mini": ((0.75, 4.5, 0.075, 0, 0), None),
    "gpt-5.4-nano": ((0.2, 1.25, 0.02, 0, 0), None),
    "gpt-5.2": ((1.75, 14, 0.175, 0, 0), None),
    "gpt-5.2-pro": ((21, 168, 21, 0, 0), None),
    "gpt-5.2-codex": ((1.75, 14, 0.175, 0, 0), None),
    "gpt-5.3-codex": ((1.75, 14, 0.175, 0, 0), None),
    "gpt-5.1": ((1.25, 10, 0.125, 0, 0), None),
    "gpt-5.1-codex": ((1.25, 10, 0.125, 0, 0), None),
    "gpt-5.1-codex-mini": ((0.25, 2, 0.025, 0, 0), None),
    "gpt-5": ((1.25, 10, 0.125, 0, 0), None),
    "gpt-5-codex": ((1.25, 10, 0.125, 0, 0), None),
    "gpt-5-pro": ((15, 120, 15, 0, 0), None),
    "gpt-5-mini": ((0.25, 2, 0.025, 0, 0), None),
    "gpt-5-nano": ((0.05, 0.4, 0.005, 0, 0), None),
    "gpt-5-fast": (GPT_5_FAST, GPT_5_FAST),
    "gpt-5-high-fast": (GPT_5_FAST, GPT_5_FAST),
    "gpt-5-low-fast": (GPT_5_FAST, GPT_5_FAST),
    "composer-2.5": ((0.5, 2.5, 0.2, 0, 0), COMPOSER_FAST),
    "composer-2.5-fast": (COMPOSER_FAST, COMPOSER_FAST),
}


def entry(prefix, **changes):
    value = dict(prefixes=[prefix], **{"in": 2, "out": 10}, cache_read=0.2,
                 cache_write_5m=2.5, cache_write_1h=4,
                 source="https://platform.claude.com/docs/en/about-claude/pricing", as_of="2026-10-21")
    value.update(changes)
    return value


class PriceFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "prices.json"

    def book(self, *models):
        self.path.write_text(json.dumps({"models": models}), encoding="utf-8")
        return PriceFile(self.path)

    def test_longest_prefix_wins_and_name_matched_from_first_claude(self):
        book = self.book(entry("claude-sonnet-5"), entry("claude-sonnet-5-5", cache_read=0.1),
                         entry("claude-opus-5"), entry("claude-opus-5-5", **{"in": 4}))
        self.assertEqual(book.rate_for("claude-sonnet-5-5-medium", False).cache_read, 0.1)
        self.assertEqual(book.rate_for("claude-sonnet-5", False).cache_read, 0.2)
        self.assertEqual(book.rate_for("us.claude-opus-5-5-medium", False).input, 4)
        self.assertEqual(book.rate_for("us.anthropic.claude-opus-5-v1:0", False).input, 2)
        self.assertIsNone(book.rate_for("unlisted", False))
        self.assertIsNone(book.problem())

    def test_missing_malformed_or_duplicate_prefix_file_makes_every_price_unknown_and_names_problem(self):
        deep = "[" * 100000
        # the message of a 100000-deep JSON array differs by Python build: "recursion" (older), "Stack overflow" (3.14 on Linux, seen in python:3.14-slim), or a plain parse error
        for name, content, reason in (
                ("missing", None, "No such file"), ("malformed", "{", "Expecting"),
                ("truncated", '{"models": [', "Expecting"), ("deeply nested", deep, "recursion|Stack overflow|Expecting value"),
                ("empty models", '{"models": []}', "nonempty models"),
                ("duplicate", json.dumps({"models": [entry("known"), entry("known")]}), "duplicate prefix known")):
            with self.subTest(name):
                if content is not None:
                    self.path.write_text(content, encoding="utf-8")
                book = PriceFile(self.path)
                self.assertIsNone(book.rate_for("known", False))
                self.assertTrue(book.problem().startswith(str(self.path) + ": "))
                self.assertRegex(book.problem(), reason)

    def test_utf8_bom_file_is_valid(self):
        self.path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"models": [entry("known")]}).encode("utf-8"))
        book = PriceFile(self.path)
        self.assertIsNone(book.problem())
        self.assertEqual(book.rate_for("known", False), Rates(2, 10, 0.2, 2.5, 4))

    def test_fast_request_without_fast_rates_unknown(self):
        book = self.book(entry("normal"), entry("fast", fast=FAST))
        self.assertIsNone(book.rate_for("normal", True))
        self.assertEqual(book.rate_for("fast", True), Rates(4, 20, 0.4, 5, 8))
        self.assertEqual(book.rate_for("fast", False), Rates(2, 10, 0.2, 2.5, 4))

    def test_invalid_entry_invalidates_whole_file_and_names_the_entry(self):
        missing_in = entry("invalid")
        del missing_in["in"]
        for name, bad, reason in (
                ("negative rate", entry("invalid", **{"in": -1}), "models[1]: in must be"),
                ("bool rate", entry("invalid", out=True), "models[1]: out must be"),
                ("string rate", entry("invalid", out="10"), "models[1]: out must be"),
                ("infinite rate", entry("invalid", cache_read=float("inf")), "models[1]: cache_read must be"),
                ("NaN rate", entry("invalid", cache_read=float("nan")), "models[1]: cache_read must be"),
                ("huge integer rate", entry("invalid", cache_read=10 ** 400), "models[1]: cache_read must be"),
                ("missing rate", missing_in, "models[1]: missing in"),
                ("typo key", entry("invalid", fats=FAST), "models[1]: unknown fats"),
                ("empty prefix", entry("invalid", prefixes=[""]), "models[1]: prefixes must be"),
                ("prefix not a list", entry("invalid", prefixes="known"), "models[1]: prefixes must be"),
                ("empty prefix list", entry("invalid", prefixes=[]), "models[1]: prefixes must be"),
                ("non-string prefix", entry("invalid", prefixes=[1]), "models[1]: prefixes must be"),
                ("repeated prefix", entry("invalid", prefixes=["twice", "twice"]), "models[1]: duplicate prefix twice"),
                ("dead provider prefix", entry("invalid", prefixes=["us.claude-opus"]),
                 "models[1]: prefix us.claude-opus can never match"),
                ("ftp source", entry("invalid", source="ftp://example.com/p"), "models[1]: source must be"),
                ("hostless source", entry("invalid", source="https://"), "models[1]: source must be"),
                ("malformed source", entry("invalid", source="https://[::1"), "models[1]: source must be"),
                ("non-string source", entry("invalid", source=7), "models[1]: source must be"),
                ("bad calendar date", entry("invalid", as_of="2026-02-30"), "models[1]: as_of must be"),
                ("compact date", entry("invalid", as_of="20261021"), "models[1]: as_of must be"),
                ("datetime date", entry("invalid", as_of="2026-10-21T00:00:00"), "models[1]: as_of must be"),
                ("partial fast", entry("invalid", fast={"in": 2}), "models[1]: fast needs exactly"),
                ("extra fast key", entry("invalid", fast=dict(FAST, extra=1)), "models[1]: fast needs exactly"),
                ("null fast", entry("invalid", fast=None), "models[1]: fast must be an object"),
                ("negative fast", entry("invalid", fast=dict(FAST, out=-1)), "models[1]: fast: out must be"),
                ("non-object model", "text", "models[1]: expected an object")):
            with self.subTest(name):
                book = self.book(entry("valid"), bad)
                self.assertIsNone(book.rate_for("valid", False))
                self.assertIn(reason, book.problem())
        self.path.write_text('{"models": {}}', encoding="utf-8")
        self.assertIn("nonempty models", PriceFile(self.path).problem())
        self.path.write_text("[]", encoding="utf-8")
        self.assertIn("nonempty models", PriceFile(self.path).problem())

    def test_duplicate_json_key_in_an_entry_invalidates_the_file(self):
        self.path.write_text(
            '{"models": [{"prefixes": ["known"], "in": 1, "in": 2, "out": 1, "cache_read": 1, '
            '"cache_write_5m": 1, "cache_write_1h": 1, "source": "https://example.com", '
            '"as_of": "2026-10-21"}]}', encoding="utf-8")
        book = PriceFile(self.path)
        self.assertIsNone(book.rate_for("known", False))
        self.assertIn("duplicate key in in the entry with prefixes ['known']", book.problem())

    def test_invalid_utf8_and_unreadable_path_name_the_problem(self):
        self.path.write_bytes(b"\xff\xfe\x00")
        self.assertIn(str(self.path), PriceFile(self.path).problem())
        self.assertTrue(PriceFile(Path(self.directory.name)).problem().startswith(self.directory.name + ": "))

    def test_cache_write_tokens_in_the_5m_field_use_the_5m_rate(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        book = self.book(entry("known", cache_write_5m=7, cache_write_1h=99))
        request = RequestUsage(model_name="known", input_tokens=0, output_tokens=0,
                               cache_read_tokens=0, cache_write_5m_tokens=1000000,
                               cache_write_1h_tokens=0)
        report = PricingRules(book).build(Session(
            harness=Harness.CODEX, session_id="s", started_at=now,
            prompts=(Prompt(submitted_at=now, text="p", requests=(request,)),)))
        self.assertEqual(report.prompts[0].price.cents, 700)


class ShippedPricesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.book = PriceFile(SHIPPED)
        cls.document = json.loads(SHIPPED.read_text(encoding="utf-8"))

    def test_shipped_prices_json_valid_with_source_and_as_of(self):
        self.assertIsNone(self.book.problem())
        for model in self.document["models"]:
            with self.subTest(prefixes=model["prefixes"]):
                self.assertIn(model["source"], OFFICIAL_PAGES)
                self.assertRegex(model["as_of"], r"^\d{4}-\d{2}-\d{2}$")

    def test_every_shipped_prefix_resolves_to_its_published_rates(self):
        shipped = {prefix for model in self.document["models"] for prefix in model["prefixes"]}
        self.assertEqual(shipped, set(PUBLISHED))
        for prefix, (normal, fast) in PUBLISHED.items():
            with self.subTest(prefix=prefix):
                self.assertEqual(self.book.rate_for(prefix, False), Rates(*normal))
                self.assertEqual(self.book.rate_for(prefix, True), Rates(*fast) if fast else None)

    def test_shipped_provider_forms_and_overlapping_prefixes_resolve_to_published_rates(self):
        for name, fast, expected in (
                ("us.claude-opus-5-5-medium", False, Rates(4, 20, 0.2, 5, 8)),
                ("us.claude-opus-5-5-medium", True, Rates(8, 40, 0.4, 10, 16)),
                ("us.anthropic.claude-opus-5-v1:0", False, Rates(5, 25, 0.5, 6.25, 10)),
                ("claude-sonnet-5", False, Rates(2, 10, 0.2, 2.5, 4)),
                ("claude-sonnet-5-5", False, Rates(2, 10, 0.1, 2.5, 4)),
                ("claude-fable-5-1", False, Rates(10, 50, 0.25, 12.5, 20)),
                ("composer-2.5", False, Rates(0.5, 2.5, 0.2, 0, 0)),
                ("composer-2.5", True, Rates(3, 15, 0.5, 0, 0))):
            with self.subTest(name=name, fast=fast):
                self.assertEqual(self.book.rate_for(name, fast), expected)

    def test_shipped_unlisted_model_is_unknown(self):
        self.assertIsNone(self.book.rate_for("unlisted-model", False))

    def test_shipped_fast_named_models_price_the_same_whether_or_not_the_reader_flags_fast(self):
        fast_named = [prefix for prefix in PUBLISHED if prefix.endswith("-fast")]
        self.assertTrue(fast_named)
        for prefix in fast_named:
            with self.subTest(prefix=prefix):
                self.assertEqual(self.book.rate_for(prefix, True), self.book.rate_for(prefix, False))
                self.assertIsNotNone(self.book.rate_for(prefix, True))

    def test_shipped_codex_only_models_cite_the_page_that_lists_them(self):
        by_prefix = {prefix: model for model in self.document["models"] for prefix in model["prefixes"]}
        for prefix in ("gpt-5-codex", "gpt-5.1-codex", "gpt-5.1-codex-mini", "gpt-5.2-codex", "gpt-5.3-codex"):
            with self.subTest(prefix=prefix):
                self.assertEqual(by_prefix[prefix]["source"], "https://cursor.com/docs/models-and-pricing")
