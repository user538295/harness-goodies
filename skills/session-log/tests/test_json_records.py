"""Decoding of one JSON-line record, of the ISO-8601 time it carries and of the numbers and labels the usage records of the harnesses share."""
import time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from sessionlog.json_records import (MAX_WHOLE_COUNT, decode_record, is_finite_number, model_label, parse_cost, parse_epoch_milliseconds,
                                     parse_moment, parse_whole_count, whole_count)

from tests.local_zone import local_zone


class DecodeRecordTest(unittest.TestCase):
    def test_a_json_object_is_decoded_and_anything_else_is_none(self):
        self.assertEqual({"a": 1}, decode_record('{"a": 1}\n'))
        for line in ("", "not json", "[1]", "7", "null", "{", "[" * 100000 + "]" * 100000):
            with self.subTest(line=line[:20]):
                self.assertIsNone(decode_record(line))


class _Unconvertible:
    """An aware time whose conversion to local time fails the way a platform without that range does."""

    def __init__(self, error):
        self._error = error

    def utcoffset(self):
        return timedelta(0)

    def astimezone(self, zone=None):
        if zone is None:
            raise self._error
        return self


@unittest.skipUnless(hasattr(time, "tzset"), "needs time.tzset")
class ParseMomentTest(unittest.TestCase):
    def test_an_aware_iso_time_is_parsed_with_its_offset(self):
        self.assertEqual(datetime(2026, 8, 28, 10, 0, 0, tzinfo=timezone.utc), parse_moment("2026-08-28T10:00:00Z"))
        self.assertEqual(timedelta(hours=2), parse_moment("2026-08-28T10:00:00+02:00").utcoffset())

    def test_anything_else_is_none(self):
        for value in (None, 5, "", "yesterday", "2026-08-28T10:00:00", "9999-12-31T23:59:59-05:00", "0001-01-01T00:00:00+05:00"):
            with self.subTest(value=value):
                self.assertIsNone(parse_moment(value))

    def test_a_time_the_local_zone_cannot_hold_is_none_so_one_bad_record_cannot_spoil_a_session(self):
        for zone, value in (("America/New_York", "0001-01-01T00:00:00Z"), ("Europe/Budapest", "9999-12-31T23:59:59Z")):
            with self.subTest(zone=zone), local_zone(zone):
                self.assertIsNone(parse_moment(value))
                self.assertIsNotNone(parse_moment("2026-08-28T10:00:00Z"))

    def test_an_epoch_time_the_local_zone_cannot_hold_is_none(self):
        with local_zone("Europe/Budapest"):
            self.assertIsNone(parse_epoch_milliseconds(253402300799000))
            self.assertIsNotNone(parse_epoch_milliseconds(1788000000000))

    def test_a_local_conversion_that_fails_on_this_platform_is_none_for_every_error(self):
        for error in (OverflowError, OSError, ValueError):
            with self.subTest(error=error.__name__):
                with patch("sessionlog.json_records.datetime", SimpleNamespace(fromisoformat=lambda value: _Unconvertible(error("x")))):
                    self.assertIsNone(parse_moment("anything"))


class NumberTest(unittest.TestCase):
    def test_a_finite_number_is_an_int_or_float_that_is_not_a_bool_inf_or_nan(self):
        self.assertTrue(all(is_finite_number(value) for value in (0, -3, 2.5, 10**400)))
        self.assertFalse(any(is_finite_number(value) for value in (True, None, "1", float("inf"), float("nan"), [1])))


class ParseEpochMillisecondsTest(unittest.TestCase):
    def test_a_positive_number_of_milliseconds_is_a_utc_time(self):
        self.assertEqual(datetime(2026, 10, 13, 8, 42, 0, 500000, tzinfo=timezone.utc), parse_epoch_milliseconds(1_791_880_920_500))

    def test_a_float_of_milliseconds_is_a_time_too(self):
        self.assertEqual(datetime(2026, 10, 13, 8, 42, 0, 500000, tzinfo=timezone.utc), parse_epoch_milliseconds(1_791_880_920_500.0))

    def test_anything_else_is_none(self):
        for value in (None, True, "1791880920500", 0, -5, float("nan"), float("inf"), 10**400, 10**20, 2.5e17):
            with self.subTest(value=value):
                self.assertIsNone(parse_epoch_milliseconds(value))


class ParseWholeCountTest(unittest.TestCase):
    def test_a_count_from_zero_up_loses_its_fraction_and_stops_at_the_int64_maximum(self):
        self.assertEqual((0, 7, 2, MAX_WHOLE_COUNT), tuple(parse_whole_count(value) for value in (0, 7, 2.9, 10**30)))

    def test_anything_else_is_none(self):
        for value in (None, True, "7", -1, float("nan"), float("inf"), [3]):
            with self.subTest(value=value):
                self.assertIsNone(parse_whole_count(value))


class WholeCountTest(unittest.TestCase):
    def test_what_is_no_count_is_zero(self):
        self.assertEqual((0, 7, 2, 0, 0), tuple(whole_count(value) for value in (0, 7, 2.9, None, "7")))


class ParseCostTest(unittest.TestCase):
    def test_a_number_from_zero_up_is_a_cost_and_zero_is_free(self):
        self.assertEqual((0.0, 0.25, 3.0), tuple(parse_cost(value) for value in (0, 0.25, 3)))

    def test_anything_else_is_none(self):
        for value in (None, True, "1.5", -0.1, float("nan"), float("inf"), 10**400, [1]):
            with self.subTest(value=value):
                self.assertIsNone(parse_cost(value))


class ModelLabelTest(unittest.TestCase):
    def test_the_provider_is_put_before_a_model_that_names_no_path(self):
        self.assertEqual("anthropic/claude-opus-5-5", model_label("claude-opus-5-5", "anthropic"))

    def test_a_model_that_names_a_path_or_has_no_provider_is_kept_and_a_missing_model_is_empty(self):
        self.assertEqual(("a/b", "m", "m", "m", "", ""), (model_label("a/b", "p"), model_label("m", None), model_label("m", ""), model_label("m", 7),
                                                          model_label(None, "p"), model_label("", "p")))


if __name__ == "__main__":
    unittest.main()
