"""Decoding of one JSON-line record and of the ISO-8601 time it carries."""
import unittest
from datetime import datetime, timedelta, timezone

from sessionlog.json_records import decode_record, parse_moment


class DecodeRecordTest(unittest.TestCase):
    def test_a_json_object_is_decoded_and_anything_else_is_none(self):
        self.assertEqual({"a": 1}, decode_record('{"a": 1}\n'))
        for line in ("", "not json", "[1]", "7", "null", "{", "[" * 100000 + "]" * 100000):
            with self.subTest(line=line[:20]):
                self.assertIsNone(decode_record(line))


class ParseMomentTest(unittest.TestCase):
    def test_an_aware_iso_time_is_parsed_with_its_offset(self):
        self.assertEqual(datetime(2026, 8, 28, 10, 0, 0, tzinfo=timezone.utc), parse_moment("2026-08-28T10:00:00Z"))
        self.assertEqual(timedelta(hours=2), parse_moment("2026-08-28T10:00:00+02:00").utcoffset())

    def test_anything_else_is_none(self):
        for value in (None, 5, "", "yesterday", "2026-08-28T10:00:00", "9999-12-31T23:59:59-05:00", "0001-01-01T00:00:00+05:00"):
            with self.subTest(value=value):
                self.assertIsNone(parse_moment(value))


if __name__ == "__main__":
    unittest.main()
