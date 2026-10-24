"""The C5 record format shared by the hook that writes a record and the reader that parses it (BE-31, C3-I-50): one set of names and JSON
keys, and one parse that reads back what the writer's fields hold."""
import json
import unittest
from datetime import datetime, timezone

from sessionlog import cursor_records
from sessionlog.cursor_records import (CONVERSATION_ID_KEY, EVENT_KEY, PID_KEY, RAW_INPUT_KEY, RECEIVED_AT_NS_KEY, RECORD_EVENTS,
                                       RECORD_FILE_NAME, STALE_RECORD_TEMP, HookRecord, moment_of_ns, parse_record, record_fields,
                                       record_file_name)
from sessionlog.temp_files import temp_name

ARRIVAL_NS = 1_700_000_000_123_456_789
ARRIVAL = datetime(2023, 11, 14, 22, 13, 20, 123456, tzinfo=timezone.utc)
HOOK_INPUT = {"conversation_id": "conv-1", "generation_id": "g1", "text": "h\u00e9llo"}


def text_of(**overrides):
    fields = record_fields("stop", "conv-1", ARRIVAL_NS, 42, json.dumps(HOOK_INPUT, ensure_ascii=False))
    fields.update(overrides)
    return json.dumps(fields, ensure_ascii=False)


class ParseRecordTest(unittest.TestCase):
    def test_what_the_writer_fields_hold_is_read_back_with_the_arrival_to_the_microsecond(self):
        self.assertEqual(HookRecord("stop", ARRIVAL, HOOK_INPUT), parse_record(text_of()))

    def test_a_record_filed_under_invalid_has_no_conversation_id_and_still_parses(self):
        fields = record_fields("stop", None, ARRIVAL_NS, 42, "{}")
        self.assertNotIn(CONVERSATION_ID_KEY, fields)
        self.assertEqual(HookRecord("stop", ARRIVAL, {}), parse_record(json.dumps(fields)))

    def test_every_recorded_event_parses(self):
        for event in RECORD_EVENTS:
            with self.subTest(event=event):
                self.assertEqual(event, parse_record(text_of(**{EVENT_KEY: event})).event)

    def test_anything_that_is_no_record_is_none(self):
        bad = ("", "junk", "[1]", "null", "{" * 100_000, text_of(**{EVENT_KEY: "sessionStart"}), text_of(**{EVENT_KEY: 3}),
               text_of(**{RECEIVED_AT_NS_KEY: "1"}), text_of(**{RECEIVED_AT_NS_KEY: -1}), text_of(**{RECEIVED_AT_NS_KEY: True}),
               text_of(**{RECEIVED_AT_NS_KEY: 1.5}), text_of(**{RECEIVED_AT_NS_KEY: 10 ** 40}), text_of(**{RAW_INPUT_KEY: 5}),
               text_of(**{RAW_INPUT_KEY: "{bad"}), text_of(**{RAW_INPUT_KEY: "[1]"}), json.dumps({EVENT_KEY: "stop"}))
        for text in bad:
            with self.subTest(text=text[:40]):
                self.assertIsNone(parse_record(text))

    def test_the_writer_does_not_repeat_the_keys_the_reader_parses(self):
        fields = record_fields("stop", "conv-1", 5, 6, "{}")
        self.assertEqual({EVENT_KEY, CONVERSATION_ID_KEY, RECEIVED_AT_NS_KEY, PID_KEY, RAW_INPUT_KEY}, set(fields))


class MomentOfNsTest(unittest.TestCase):
    def test_nanoseconds_since_the_epoch_make_a_utc_time_to_the_microsecond(self):
        self.assertEqual(ARRIVAL, moment_of_ns(ARRIVAL_NS))
        self.assertEqual(datetime(1970, 1, 1, tzinfo=timezone.utc), moment_of_ns(0))

    def test_a_time_a_datetime_cannot_hold_is_none(self):
        self.assertIsNone(moment_of_ns(10 ** 40))


class RecordNamesTest(unittest.TestCase):
    def test_a_record_name_matches_and_names_its_arrival_and_a_temp_or_look_alike_does_not(self):
        name = record_file_name(ARRIVAL_NS, "subagentStop", 42)
        self.assertEqual((str(ARRIVAL_NS), "subagentStop"), RECORD_FILE_NAME.fullmatch(name).groups())
        temp = temp_name(name, 42)
        self.assertIsNone(RECORD_FILE_NAME.fullmatch(temp))
        self.assertIsNotNone(STALE_RECORD_TEMP.fullmatch(temp))
        for other in ("notes.json", "1-sessionStart-2.json", "1-stop-2.json.tmp", "-stop-2.json", "1-stop-.json", "x1-stop-2.json", name + "x"):
            with self.subTest(name=other):
                self.assertIsNone(RECORD_FILE_NAME.fullmatch(other))


if __name__ == "__main__":
    unittest.main()
