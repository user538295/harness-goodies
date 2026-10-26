import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from sessionlog.claude_reader import ClaudeReader
from sessionlog.model import Harness, RequestUsage
from sessionlog.reader_port import (CANNOT_BE_LISTED_REASON, CANNOT_BE_OPENED_REASON, CANNOT_BE_PARSED_REASON, UNSAFE_ID_REASON,
                                    SourceUnit, UnreadableSource)

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "claude", "main_session.jsonl")
PROJECT = "-work-proj"
SID = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
STAMP = "2026-08-28T10:00:%06.3fZ"
MAX_TOKENS = 2**63 - 1
ONE_SECOND_NS = 1_000_000_000
ONE_MILLISECOND_NS = 1_000_000
FIXTURE_PROMPT_TEXTS = ["first line\nsecond line", "queued prompt", "third prompt"]


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _at(second: float) -> datetime:
    return _utc(STAMP % second)


def _typed(second, text="ask", **extra) -> dict:
    record = {"type": "user", "promptSource": "typed", "cwd": "/work/proj", "message": {"role": "user", "content": text}}
    if second is not None:
        record["timestamp"] = STAMP % second
    return {**record, **extra}


def _answer(second, message_id, usage, text="reply", blocks=None, **extra) -> dict:
    content = blocks if blocks is not None else [{"type": "text", "text": text}]
    record = {"type": "assistant", "message": {"id": message_id, "model": "claude-x", "content": content, "usage": usage}}
    if second is not None:
        record["timestamp"] = STAMP % second
    return {**record, **extra}


def _usage(output_tokens=1, **extra) -> dict:
    return {"input_tokens": 1, "output_tokens": output_tokens, **extra}


def _on_model(record: dict, model: str) -> dict:
    record["message"]["model"] = model
    return record


def _api_error(record: dict) -> dict:
    """The `<synthetic>` message Claude Code writes when the API call failed."""
    return {**_on_model(record, "<synthetic>"), "isApiErrorMessage": True, "error": "rate_limit"}


def _plain_user(second, text, **extra) -> dict:
    return {"type": "user", "promptSource": None, "cwd": "/work/proj", "timestamp": STAMP % second,
            "message": {"role": "user", "content": text}, **extra}


def _queued(second, prompt, command_mode="prompt", **attachment) -> dict:
    record = {"type": "attachment", "cwd": "/work/proj",
              "attachment": {"type": "queued_command", "prompt": prompt, "commandMode": command_mode, **attachment}}
    if second is not None:
        record["timestamp"] = STAMP % second
    return record


BRIDGE = {"type": "bridge-session", "sessionId": SID, "bridgeSessionId": "cse_x"}
COMMAND_TEXT = "<command-message>iterative-review is running</command-message>\n<command-name>/x:iterative-review</command-name>"


class _FakeEntry:
    """A DirEntry whose every file-system query raises `error` (a file that vanished or cannot be examined)."""

    def __init__(self, path, error):
        self.path, self.name = path, os.path.basename(path)
        self._error = error

    def is_dir(self, *, follow_symlinks=True):
        raise self._error

    def is_file(self, *, follow_symlinks=True):
        raise self._error

    def stat(self, *, follow_symlinks=True):
        raise self._error


class ClaudeReaderTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        self.projects = os.path.join(self.root, "projects")
        self.folder = os.path.join(self.projects, PROJECT)
        os.makedirs(self.folder)
        self.reader = ClaudeReader(self.root)

    def _folder(self, name):
        path = os.path.join(self.projects, name)
        os.makedirs(path)
        return path

    def _install(self, sid=SID, folder=None):
        path = os.path.join(folder or self.folder, sid + ".jsonl")
        shutil.copy(FIXTURE, path)
        return path

    def _write(self, sid, text, folder=None):
        path = os.path.join(folder or self.folder, sid + ".jsonl")
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        return path

    def _read_text(self, text, sid="sess-2"):
        self._write(sid, text)
        (unit,) = self.reader.scan().units
        return self.reader.read(self.reader.session_of(unit), (unit,))

    def _read(self, records, sid="sess-2"):
        return self._read_text("".join(json.dumps(record) + "\n" for record in records), sid)

    def _prompts(self, records):
        return self._read(records).session.prompts

    def _fixture_session(self):
        self._install()
        (unit,) = self.reader.scan().units
        return self.reader.read(self.reader.session_of(unit), (unit,)).session

    def _scandir_wrapping(self, extra=None, fail=None, reverse=False):
        """A scandir that adds `extra` entries per path, raises `fail[path]` and optionally reverses the order."""
        real_scandir = os.scandir
        extra, fail = extra or {}, fail or {}

        def scandir(path):
            if path in fail:
                raise fail[path]
            entries = [*real_scandir(path), *extra.get(path, ())]
            return list(reversed(entries)) if reverse else entries

        return mock.patch("sessionlog.claude_reader.os.scandir", scandir)


class ClaudeScanTest(ClaudeReaderTestCase):
    def test_claude_scan_lists_units_with_changing_fingerprint(self):
        path = self._install()
        (first,) = self.reader.scan().units
        self.assertEqual(first.key, path)
        self.assertEqual(first.fingerprint, self.reader.scan().units[0].fingerprint)
        stat = os.stat(path)
        with open(path, "a") as handle:
            handle.write("\n")
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        (grown,) = self.reader.scan().units
        self.assertNotEqual(first.fingerprint, grown.fingerprint)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + ONE_SECOND_NS))
        (touched,) = self.reader.scan().units
        self.assertNotEqual(grown.fingerprint, touched.fingerprint)

    def test_claude_scan_fingerprint_changes_with_a_tiny_mtime_move_at_same_size(self):
        path = self._install()
        (before,) = self.reader.scan().units
        stat = os.stat(path)
        for step_ns in (1, ONE_MILLISECOND_NS):
            with self.subTest(step_ns=step_ns):
                os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + step_ns))
                (after,) = self.reader.scan().units
                self.assertNotEqual(before.fingerprint, after.fingerprint)

    def test_claude_scan_modified_at_is_the_file_mtime_as_aware_utc(self):
        path = self._install()
        os.utime(path, ns=(0, 1_700_000_000 * ONE_SECOND_NS))
        (unit,) = self.reader.scan().units
        self.assertEqual(unit.modified_at, datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc))
        self.assertEqual(unit.modified_at.utcoffset(), timedelta(0))

    def test_claude_scan_missing_projects_dir_gives_empty_result(self):
        result = ClaudeReader(os.path.join(self.root, "nowhere")).scan()
        self.assertEqual((result.units, result.unreadable), ((), ()))

    def test_claude_scan_ignores_non_session_entries(self):
        path = self._install()
        self._write("notes", "x")
        os.rename(os.path.join(self.folder, "notes.jsonl"), os.path.join(self.folder, "notes.txt"))
        os.mkdir(os.path.join(self.folder, "x.jsonl"))
        self._write("stray", "{}\n", self.projects)
        self.assertEqual([unit.key for unit in self.reader.scan().units], [path])

    def test_claude_scan_sorted_by_key_whatever_the_listing_order(self):
        first, second = self._folder("-a-proj"), self._folder("-b-proj")
        for sid, folder in (("zzz", first), ("yyy", first), ("aaa", second), ("bbb", second)):
            self._install(sid, folder)
        with self._scandir_wrapping(reverse=True):
            keys = [unit.key for unit in self.reader.scan().units]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual([os.path.basename(key) for key in keys], ["yyy.jsonl", "zzz.jsonl", "aaa.jsonl", "bbb.jsonl"])

    def test_claude_scan_skips_file_that_vanished_before_stat(self):
        self._install()
        gone = _FakeEntry(os.path.join(self.folder, "gone.jsonl"), FileNotFoundError())
        with self._scandir_wrapping({self.folder: [gone]}):
            result = self.reader.scan()
        self.assertEqual([os.path.basename(unit.key) for unit in result.units], [SID + ".jsonl"])
        self.assertEqual(result.unreadable, ())

    def test_claude_scan_reports_file_that_cannot_be_examined(self):
        self._install()
        for error in (PermissionError(), OSError("I/O error")):
            with self.subTest(error=type(error).__name__):
                locked = _FakeEntry(os.path.join(self.folder, "locked.jsonl"), error)
                with self._scandir_wrapping({self.folder: [locked]}):
                    result = self.reader.scan()
                self.assertEqual(len(result.units), 1)
                self.assertEqual(result.unreadable, (UnreadableSource(Harness.CLAUDE, locked.path, CANNOT_BE_LISTED_REASON),))

    def test_claude_scan_project_dir_that_vanishes_is_skipped_and_one_that_denies_is_unreadable(self):
        self._install()
        gone = _FakeEntry(os.path.join(self.projects, "-gone"), FileNotFoundError())
        denied = _FakeEntry(os.path.join(self.projects, "-denied"), PermissionError())
        with self._scandir_wrapping({self.projects: [gone, denied]}):
            result = self.reader.scan()
        self.assertEqual(len(result.units), 1)
        self.assertEqual(result.unreadable, (UnreadableSource(Harness.CLAUDE, denied.path, CANNOT_BE_LISTED_REASON),))

    def test_claude_scan_project_dir_that_vanishes_between_is_dir_and_listing_is_skipped(self):
        self._install()
        vanishing = self._folder("-vanishing")
        with self._scandir_wrapping(fail={vanishing: FileNotFoundError()}):
            result = self.reader.scan()
        self.assertEqual((len(result.units), result.unreadable), (1, ()))

    def test_claude_scan_reports_unlistable_project_and_scans_the_rest(self):
        self._install()
        locked = self._folder("-locked")
        with self._scandir_wrapping(fail={locked: PermissionError()}):
            result = self.reader.scan()
        self.assertEqual(len(result.units), 1)
        self.assertEqual(result.unreadable, (UnreadableSource(Harness.CLAUDE, locked, CANNOT_BE_LISTED_REASON),))

    def test_claude_scan_reports_unlistable_projects_dir(self):
        with self._scandir_wrapping(fail={self.projects: PermissionError()}):
            result = self.reader.scan()
        self.assertEqual((result.units, result.unreadable), ((), (UnreadableSource(Harness.CLAUDE, self.projects, CANNOT_BE_LISTED_REASON),)))

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), "needs a non-root POSIX user")
    def test_claude_scan_reports_really_unlistable_projects_dir(self):
        self.addCleanup(os.chmod, self.projects, 0o755)
        os.chmod(self.projects, 0)
        result = self.reader.scan()
        self.assertEqual((result.units, result.unreadable), ((), (UnreadableSource(Harness.CLAUDE, self.projects, CANNOT_BE_LISTED_REASON),)))

    def test_claude_scan_sorts_unreadable_by_path(self):
        locked = {self._folder("-a-locked"): PermissionError(), self._folder("-b-locked"): PermissionError()}
        with self._scandir_wrapping(fail=locked, reverse=True):
            paths = [source.path for source in self.reader.scan().unreadable]
        self.assertEqual(paths, sorted(locked))


class ClaudeSessionIdentityTest(ClaudeReaderTestCase):
    def test_claude_session_id_and_cwd_from_path_and_records(self):
        self._install(folder=self._folder("-folder-name-that-decodes-elsewhere"))
        (unit,) = self.reader.scan().units
        self.assertEqual(self.reader.session_of(unit), "claude:" + SID)
        session = self.reader.read("claude:" + SID, (unit,)).session
        self.assertEqual((session.session_id, session.project_dir), (SID, "/work/proj"))

    def test_claude_session_id_comes_from_the_path_not_the_records(self):
        self._install("other-stem")
        (unit,) = self.reader.scan().units
        self.assertEqual(self.reader.read("claude:other-stem", (unit,)).session.session_id, "other-stem")

    def test_claude_project_dir_is_none_without_a_usable_cwd(self):
        for cwd in ({}, {"cwd": 5}, {"cwd": ""}):
            with self.subTest(cwd=cwd):
                record = {k: v for k, v in _typed(1).items() if k != "cwd"}
                self.assertIsNone(self._read([{**record, **cwd}]).session.project_dir)

    def test_claude_unsafe_id_stem_is_unreadable(self):
        path = self._write("bad.id", "{}\n")
        (unit,) = self.reader.scan().units
        result = self.reader.read("claude:bad.id", (unit,))
        self.assertEqual((result.session, result.unreadable), (None, (UnreadableSource(Harness.CLAUDE, path, UNSAFE_ID_REASON),)))

    def test_claude_undecodable_file_is_unreadable(self):
        path = self._write("broken", "")
        with open(path, "wb") as handle:
            handle.write(b"\xff\xfe\n")
        (unit,) = self.reader.scan().units
        result = self.reader.read("claude:broken", (unit,))
        self.assertEqual((result.session, result.unreadable), (None, (UnreadableSource(Harness.CLAUDE, path, CANNOT_BE_PARSED_REASON),)))

    def test_claude_unopenable_file_is_unreadable(self):
        path = self._install()
        (unit,) = self.reader.scan().units
        os.remove(path)
        result = self.reader.read("claude:" + SID, (unit,))
        self.assertEqual((result.session, result.unreadable), (None, (UnreadableSource(Harness.CLAUDE, path, CANNOT_BE_OPENED_REASON),)))

    def test_claude_session_of_does_no_io(self):
        unit = SourceUnit("/nonexistent/proj/abc.jsonl", "0:0", _utc("2026-01-01T00:00:00+00:00"))
        self.assertEqual(self.reader.session_of(unit), "claude:abc")

    def test_claude_read_without_matching_unit_gives_empty_result(self):
        self._install()
        (unit,) = self.reader.scan().units
        for key, units in (("claude:" + SID, ()), ("claude:other", (unit,)), (SID, (unit,))):
            with self.subTest(key=key, units=len(units)):
                result = self.reader.read(key, units)
                self.assertEqual((result.session, result.unreadable), (None, ()))

    def test_claude_read_picks_newest_of_units_sharing_a_session_id(self):
        other = self._folder("-other-proj")
        older = self._write("same", json.dumps(_typed(1, "older")) + "\n")
        newer = self._write("same", json.dumps(_typed(1, "newer")) + "\n", other)
        os.utime(older, ns=(0, ONE_SECOND_NS))
        os.utime(newer, ns=(0, 2 * ONE_SECOND_NS))
        units = self.reader.scan().units
        self.assertEqual(self.reader.read("claude:same", units).session.prompts[0].text, "newer")
        self.assertEqual(self.reader.read("claude:same", tuple(reversed(units))).session.prompts[0].text, "newer")

    def test_claude_read_breaks_modified_at_ties_by_greatest_key(self):
        other = self._folder("-zzz-proj")
        first = self._write("same", json.dumps(_typed(1, "first")) + "\n")
        second = self._write("same", json.dumps(_typed(1, "second")) + "\n", other)
        for path in (first, second):
            os.utime(path, ns=(0, ONE_SECOND_NS))
        self.assertGreater(second, first)
        self.assertEqual(self.reader.read("claude:same", self.reader.scan().units).session.prompts[0].text, "second")


class ClaudeFixtureTest(ClaudeReaderTestCase):
    def test_claude_reader_reads_session_tree_from_text_fixture(self):
        self._install()
        (unit,) = self.reader.scan().units
        result = self.reader.read(self.reader.session_of(unit), (unit,))
        self.assertEqual(result.unreadable, ())
        session = result.session
        self.assertEqual((session.harness, session.session_id, session.project_dir), (Harness.CLAUDE, SID, "/work/proj"))
        self.assertEqual(session.started_at, datetime(2026, 8, 28, 10, 0, 0, 500000, tzinfo=timezone.utc))
        self.assertEqual(session.last_activity_at, datetime(2026, 8, 28, 10, 2, 0, tzinfo=timezone.utc))
        self.assertEqual([p.text for p in session.prompts], FIXTURE_PROMPT_TEXTS)
        self.assertEqual(session.prompts[0].submitted_at, datetime(2026, 8, 28, 10, 0, 1, tzinfo=timezone.utc))

    def test_claude_fixture_prompt_one_counts_each_message_once(self):
        first = self._fixture_session().prompts[0]
        self.assertEqual(first.requests, (
            RequestUsage(model_name="claude-opus-4-1", input_tokens=100, output_tokens=30, cache_read_tokens=40,
                         cache_write_5m_tokens=11, cache_write_1h_tokens=22, effort=None, fast=False),
            RequestUsage(model_name="claude-haiku-4-5", input_tokens=3, output_tokens=4, cache_read_tokens=1,
                         cache_write_5m_tokens=0, cache_write_1h_tokens=0, effort=None, fast=False),
            RequestUsage(model_name="claude-sonnet-4", input_tokens=5, output_tokens=6, cache_read_tokens=7,
                         cache_write_5m_tokens=0, cache_write_1h_tokens=0, effort=None, fast=False),
            RequestUsage(model_name="claude-opus-4-1", input_tokens=200, output_tokens=50, cache_read_tokens=60,
                         cache_write_5m_tokens=13, cache_write_1h_tokens=12, effort="high", fast=False),
        ))

    def test_claude_fixture_prompt_one_reply(self):
        first = self._fixture_session().prompts[0]
        self.assertEqual(first.reply_text, "para one\n\npara two")
        self.assertEqual(first.reply_at, _utc("2026-08-28T10:00:07.500Z"))
        self.assertEqual(first.working_time, timedelta(seconds=6.5))

    def test_claude_fixture_prompt_two_maps_fast_effort_and_cache_writes(self):
        second = self._fixture_session().prompts[1]
        self.assertEqual(second.requests, (
            RequestUsage(model_name="claude-opus-4-1", input_tokens=5, output_tokens=6, cache_read_tokens=7,
                         cache_write_5m_tokens=8, cache_write_1h_tokens=9, effort="max", fast=True),
        ))
        self.assertEqual((second.submitted_at, second.reply_at, second.working_time, second.reply_text),
                         (_utc("2026-08-28T10:01:00Z"), _utc("2026-08-28T10:01:10Z"), timedelta(seconds=10), "queued answer"))

    def test_claude_fixture_prompt_three_has_no_reply(self):
        third = self._fixture_session().prompts[2]
        self.assertEqual((third.reply_at, third.reply_text, third.working_time, third.requests), (None, None, timedelta(0), ()))

    def test_claude_read_truncated_final_line_is_skipped(self):
        torn = '{"type": "user", "promptSource": "typed", "timestamp": "2026-08-28T10:03'
        with open(FIXTURE, encoding="utf-8") as handle:
            session = self._read_text(handle.read() + torn, SID).session
        self.assertEqual([p.text for p in session.prompts], FIXTURE_PROMPT_TEXTS)

    def test_claude_read_survives_deeply_nested_and_wrongly_typed_lines(self):
        text = "[" * 200000 + "\n" + json.dumps(_typed(1, promptSource=["typed"])) + "\n" + json.dumps(_typed(2, "kept")) + "\n"
        self.assertEqual([p.text for p in self._read_text(text).session.prompts], ["kept"])

    def test_claude_read_tolerates_a_byte_order_mark(self):
        session = self._read_text("\ufeff" + json.dumps(_typed(1)) + "\n").session
        self.assertEqual((session.started_at, session.project_dir), (_at(1), "/work/proj"))


class ClaudePromptTest(ClaudeReaderTestCase):
    def test_claude_read_prompt_sources_that_open_prompts(self):
        for source in ("typed", "queued", "sdk", "suggestion_accepted"):
            with self.subTest(source=source):
                self.assertEqual([p.text for p in self._prompts([_typed(1, "ask", promptSource=source)])], ["ask"])

    def test_claude_read_other_prompt_sources_and_meta_do_not_open_prompts(self):
        for extra in ({"promptSource": "bridge"}, {"isMeta": True}):
            with self.subTest(extra=extra):
                self.assertEqual(self._prompts([_typed(1, **extra), _typed(2, "real")])[0].text, "real")

    def test_claude_read_joins_non_blank_text_blocks_of_a_prompt_with_newline(self):
        blocks = [{"type": "text", "text": "one"}, {"type": "image"}, {"type": "text", "text": "  "}, {"type": "text", "text": "two"}]
        self.assertEqual(self._prompts([_typed(1, blocks)])[0].text, "one\ntwo")

    def test_claude_read_image_only_prompt_has_empty_text(self):
        self.assertEqual(self._prompts([_typed(1, [{"type": "image"}])])[0].text, "")

    def test_claude_read_assistant_before_first_prompt_is_ignored(self):
        (prompt,) = self._prompts([_answer(1, "m0", _usage(9)), _typed(2, "ask")])
        self.assertEqual((prompt.text, prompt.reply_at, prompt.requests), ("ask", None, ()))

    def test_claude_read_reply_after_excluded_user_records_attaches_to_the_preceding_prompt(self):
        tool_result = {"type": "user", "promptSource": None, "isMeta": False, "timestamp": STAMP % 2,
                       "message": {"content": [{"type": "tool_result", "content": "ok"}]}}
        (prompt,) = self._prompts([_typed(1), tool_result, _typed(3, "meta", isMeta=True), _answer(4, "m1", _usage(5), "done")])
        self.assertEqual((prompt.reply_text, prompt.reply_at, [r.output_tokens for r in prompt.requests]), ("done", _at(4), [5]))

    def test_claude_read_untimed_prompt_is_dropped_with_its_replies(self):
        records = [_typed(1, "p1"), _answer(2, "m1", _usage(7), "r1"), _typed(None, "p2"), _answer(3, "m2", _usage(8), "r2"), _typed(4, "p3")]
        first, third = self._prompts(records)
        self.assertEqual((first.text, first.reply_text, first.reply_at, [r.output_tokens for r in first.requests]), ("p1", "r1", _at(2), [7]))
        self.assertEqual((third.text, third.requests), ("p3", ()))


class ClaudeRequestTest(ClaudeReaderTestCase):
    def test_claude_read_coerces_token_numbers(self):
        usages = [
            {"input_tokens": 3.7, "output_tokens": "12", "cache_read_input_tokens": -5},
            {"input_tokens": True, "output_tokens": None, "cache_read_input_tokens": "junk",
             "cache_creation": {"ephemeral_5m_input_tokens": "4.9", "ephemeral_1h_input_tokens": 2.0}},
            {"input_tokens": "nan", "output_tokens": "1e400", "cache_read_input_tokens": float("inf")},
            {},
        ]
        records = [_typed(1)] + [_answer(2 + i, "msg_%d" % i, usage) for i, usage in enumerate(usages)]
        requests = self._prompts(records)[0].requests
        self.assertEqual([(r.input_tokens, r.output_tokens, r.cache_read_tokens, r.cache_write_5m_tokens, r.cache_write_1h_tokens) for r in requests],
                         [(3, 12, 0, 0, 0), (0, 0, 0, 4, 2), (0, 0, 0, 0, 0), (0, 0, 0, 0, 0)])

    def test_claude_read_numeric_strings_are_parsed_strictly_and_exactly(self):
        cases = {"9223372036854775807": MAX_TOKENS, "9007199254740993": 9007199254740993, "7": 7, "2.5e1": 25,
                 "1_000": 0, " 5 ": 0, "٣": 0, "+5": 0, "-5": 0, "5.": 0, "9" * 5000: 0}
        records = [_typed(1)] + [_answer(2, "m_%d" % i, _usage(text)) for i, text in enumerate(cases)]
        outputs = [r.output_tokens for r in self._prompts(records)[0].requests]
        self.assertEqual(outputs, list(cases.values()))

    def test_claude_read_token_numbers_above_int64_are_zero(self):
        usages = [
            {"input_tokens": 10**30, "output_tokens": 1e30, "cache_read_input_tokens": "1e30"},
            {"input_tokens": MAX_TOKENS, "output_tokens": MAX_TOKENS + 1},
        ]
        records = [_typed(1)] + [_answer(2 + i, "msg_%d" % i, usage) for i, usage in enumerate(usages)]
        requests = self._prompts(records)[0].requests
        self.assertEqual([(r.input_tokens, r.output_tokens, r.cache_read_tokens) for r in requests], [(0, 0, 0), (MAX_TOKENS, 0, 0)])

    def test_claude_read_entries_without_a_usage_object_contribute_no_request(self):
        records = [_typed(1), _answer(2, "msg_1", "not an object"), _answer(3, "msg_2", _usage())]
        del records[2]["message"]["usage"]
        self.assertEqual(self._prompts(records)[0].requests, ())

    def test_claude_read_same_message_id_with_different_request_ids_is_one_request(self):
        records = [_typed(1), _answer(2, "m", _usage(10), requestId="r1"), _answer(3, "m", _usage(20), requestId="r2")]
        self.assertEqual([r.output_tokens for r in self._prompts(records)[0].requests], [20])

    def test_claude_read_different_message_ids_with_the_same_request_id_are_two_requests(self):
        records = [_typed(1), _answer(2, "m1", _usage(10), requestId="r"), _answer(3, "m2", _usage(20), requestId="r")]
        self.assertEqual([r.output_tokens for r in self._prompts(records)[0].requests], [10, 20])

    def test_claude_read_request_without_message_id_falls_back_to_request_id_then_line(self):
        records = [_typed(1)]
        for output, request_id in ((10, "req_1"), (20, "req_1"), (30, None), (40, None)):
            record = _answer(2, "x", _usage(output))
            del record["message"]["id"]
            if request_id:
                record["requestId"] = request_id
            records.append(record)
        self.assertEqual([r.output_tokens for r in self._prompts(records)[0].requests], [20, 30, 40])

    def test_claude_read_wrongly_typed_fields_do_not_raise(self):
        wrong_model = _answer(2, "m", _usage())
        wrong_model["message"]["model"] = 5
        cases = {
            "non-str model": ([wrong_model], lambda s: s.prompts[0].requests[0].model_name == ""),
            "non-str effort": ([_answer(2, "m", _usage(), effort=5)], lambda s: s.prompts[0].requests[0].effort is None),
            "non-str cwd": ([{"type": "system", "cwd": 5, "timestamp": STAMP % 0}], lambda s: s.project_dir == "/work/proj"),
            "int timestamp": ([{"type": "system", "timestamp": 5}], lambda s: s.started_at == _at(1)),
            "non-dict message": ([{"type": "assistant", "message": "x", "timestamp": STAMP % 2}], lambda s: s.prompts[0].requests == ()),
            "non-list content": ([_answer(2, "m", _usage(), blocks=5)], lambda s: s.prompts[0].reply_text is None),
            "non-str prompt content": ([_typed(3, 5)], lambda s: s.prompts[1].text == ""),
        }
        for name, (extra, is_sane) in cases.items():
            with self.subTest(name):
                self.assertTrue(is_sane(self._read([_typed(1), *extra]).session))


class ClaudeTimeTest(ClaudeReaderTestCase):
    def test_claude_read_untimed_reply_counts_but_leaves_reply_at(self):
        (prompt,) = self._prompts([_typed(2, "real"), _answer(None, "msg_1", _usage(7), "untimed")])
        self.assertEqual((prompt.reply_at, prompt.reply_text, prompt.working_time), (None, "untimed", timedelta(0)))
        self.assertEqual([r.output_tokens for r in prompt.requests], [7])

    def test_claude_read_naive_timestamps_count_as_no_timestamp(self):
        naive = "2026-08-28T10:00:05"
        records = [_typed(1, "p1"), _answer(2, "m1", _usage(7), "r1", timestamp=naive), _typed(None, "p2", timestamp=naive), _answer(3, "m2", _usage(8))]
        (prompt,) = self._prompts(records)
        self.assertEqual((prompt.text, prompt.reply_at, prompt.reply_text, [r.output_tokens for r in prompt.requests]), ("p1", None, "r1", [7]))

    def test_claude_read_timestamp_unconvertible_to_utc_is_ignored(self):
        records = [{"type": "system", "timestamp": "0001-01-01T00:00:00+05:00"}, _typed(1)]
        self.assertEqual(self._read(records).session.started_at, _at(1))

    def test_claude_read_session_without_any_timestamp_is_empty(self):
        result = self._read([_typed(None)])
        self.assertEqual((result.session, result.unreadable), (None, ()))

    def test_claude_read_started_at_is_the_earliest_timestamp_of_any_record(self):
        records = [_typed(5), {"type": "system", "timestamp": STAMP % 2}, _typed(9)]
        self.assertEqual(self._read(records).session.started_at, _at(2))

    def test_claude_read_takes_first_non_empty_cwd_of_any_record(self):
        records = [{"type": "system", "cwd": "", "timestamp": STAMP % 1}, {"type": "system", "cwd": "/first"}, _typed(3, cwd="/second")]
        self.assertEqual(self._read(records).session.project_dir, "/first")

    def test_claude_read_reply_at_is_the_latest_assistant_time_not_the_last_in_file(self):
        records = [_typed(1), _answer(5, "m1", _usage(), "late"), _answer(3, "m2", _usage(), "later in file")]
        (prompt,) = self._prompts(records)
        self.assertEqual((prompt.reply_at, prompt.working_time, prompt.reply_text), (_at(5), timedelta(seconds=4), "later in file"))

    def test_claude_read_reply_at_never_precedes_the_submission(self):
        (prompt,) = self._prompts([_typed(2), _answer(1, "m1", _usage())])
        self.assertEqual((prompt.reply_at, prompt.working_time), (_at(2), timedelta(0)))

    def test_claude_read_reply_text_comes_only_from_the_last_message(self):
        tool_only = [{"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {}}]
        records = [_typed(1), _answer(2, "m1", _usage(5), "said"), _answer(3, "m2", _usage(6), blocks=tool_only)]
        (prompt,) = self._prompts(records)
        self.assertEqual((prompt.reply_text, prompt.reply_at, [r.output_tokens for r in prompt.requests]), (None, _at(3), [5, 6]))

    def test_claude_read_repeated_identical_text_block_of_a_message_is_kept_once(self):
        records = [_typed(1), _answer(2, "m1", _usage(), "x"), _answer(3, "m1", _usage(), "x"), _answer(4, "m1", _usage(), "y")]
        self.assertEqual(self._prompts(records)[0].reply_text, "x\n\ny")


class ClaudeRulesTest(ClaudeReaderTestCase):
    def test_only_real_prompts_open_rows_and_synthetic_skipped_haiku_effort_not_recorded(self):
        haiku = _on_model(_answer(2, "m1", _usage(4), "haiku reply"), "claude-haiku-4-5")
        records = [
            BRIDGE,
            _typed(1, "typed one"), haiku,
            _typed(3, "queued one", promptSource="queued"), _answer(4, "m2", _usage(), "r2", effort="high"),
            _typed(5, "sdk one", promptSource="sdk"), _answer(6, "m3", _usage(), "r3"),
            _typed(7, "accepted", promptSource="suggestion_accepted"), _answer(8, "m4", _usage(), "r4"),
            _plain_user(9, "from the phone"), _answer(10, "m5", _usage(), "r5"),
            _queued(11, "attached"), _answer(12, "m6", _usage(), "r6"),
            _plain_user(13, COMMAND_TEXT), _answer(14, "m7", _usage(), "r7"),
            _plain_user(15, "<command-name>/clear</command-name>\n<command-message>clear</command-message>"),
            _typed(16, "<task-notification>\n<task-id>a1</task-id>\n</task-notification>", promptSource="sdk"),
            _typed(17, "meta", isMeta=True),
            {"type": "user", "promptSource": None, "isMeta": False, "timestamp": STAMP % 18,
             "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
            _on_model(_answer(19, "m8", _usage(99), "No response requested."), "<synthetic>"),
        ]
        prompts = self._prompts(records)
        self.assertEqual([p.text for p in prompts], [
            "typed one", "queued one", "sdk one", "accepted", "from the phone", "attached",
            "/x:iterative-review"])
        self.assertEqual((prompts[0].requests[0].model_name, prompts[0].requests[0].effort), ("claude-haiku-4-5", None))
        self.assertEqual(prompts[1].requests[0].effort, "high")
        self.assertEqual([p.reply_text for p in prompts], ["haiku reply", "r2", "r3", "r4", "r5", "r6", "r7"])
        self.assertEqual([len(p.requests) for p in prompts], [1] * 7)
        self.assertEqual(prompts[-1].reply_at, _at(14))

    def test_streamed_copies_count_once_with_last_copy_interrupted_turn_counted_fast_uses_fast_rates(self):
        interrupted = _answer(4, "m1", _usage(30), "part")
        interrupted["message"]["stop_reason"] = None
        records = [
            _typed(1, "ask"),
            _answer(2, "m1", _usage(10), "p"), _answer(3, "m1", _usage(20), "pa"), interrupted,
            _answer(5, "m2", _usage(50, speed="fast"), "quick"), _answer(5.5, "m2", _usage(5, speed="fast"), "quick"),
            _answer(5.7, "m2", None, "quick"), _answer(6, "m3", _usage(7, speed="standard"), "slow"),
        ]
        (prompt,) = self._prompts(records)
        self.assertEqual([(r.output_tokens, r.fast) for r in prompt.requests], [(30, False), (5, True), (7, False)])
        self.assertEqual((prompt.reply_text, prompt.reply_at), ("slow", _at(6)))

    def test_corrupt_and_truncated_lines_skipped_next_record_kept_working_time_ends_at_last_output(self):
        lines = [json.dumps(_typed(1, "ask")), json.dumps(_answer(4, "m1", _usage(3), "done")), "{not json",
                 '{"type": "assistant", "message": {"id": "m',
                 json.dumps(_answer(6, "m2", {}, "no fields")),
                 json.dumps(_answer(7, "m3", None, "no usage")),
                 json.dumps(_plain_user(20, "<command-name>/model</command-name>", isMeta=True)),
                 json.dumps(_typed(30, "unanswered"))]
        first, second = self._read_text("\n".join(lines) + "\n").session.prompts
        self.assertEqual((first.reply_at, first.working_time, first.reply_text), (_at(7), timedelta(seconds=6), "no usage"))
        self.assertEqual([(r.input_tokens, r.output_tokens) for r in first.requests], [(1, 3), (0, 0)])
        self.assertEqual((second.text, second.requests, second.reply_at, second.working_time), ("unanswered", (), None, timedelta(0)))

    def test_unknown_record_types_skipped_silently_only_unparsable_file_listed(self):
        unknown = [{"type": kind, "timestamp": STAMP % 1, "content": "payload", "lastPrompt": "payload", "customTitle": "t"}
                   for kind in ("custom-title", "agent-name", "atis-latch", "queue-operation", "last-prompt")]
        body = "".join(json.dumps(r) + "\n" for r in unknown) + "{corrupt\n" + json.dumps(_typed(2, "ask")) + "\n"
        self._write("good", body)
        bad = self._write("bad", "not json\n[1, 2]\n")
        self._write("empty", "\n  \n")
        self._write("zero", "")
        self._write("only-unknown", "".join(json.dumps(r) + "\n" for r in unknown))
        nested = self._write("nested", "[" * 200000 + "\n")
        scanned = self.reader.scan()
        self.assertEqual(scanned.unreadable, ())
        units = {os.path.basename(u.key): u for u in scanned.units}
        for _ in range(2):
            good = self.reader.read("claude:good", (units["good.jsonl"],))
            self.assertEqual((good.unreadable, [p.text for p in good.session.prompts]), ((), ["ask"]))
        zero = self.reader.read("claude:zero", (units["zero.jsonl"],))
        self.assertEqual((zero.session, zero.unreadable), (None, ()))
        only_unknown = self.reader.read("claude:only-unknown", (units["only-unknown.jsonl"],))
        self.assertEqual((only_unknown.unreadable, only_unknown.session.prompts), ((), ()))
        self.assertEqual(self.reader.read("claude:nested", (units["nested.jsonl"],)).unreadable,
                         (UnreadableSource(Harness.CLAUDE, nested, CANNOT_BE_PARSED_REASON),))
        unparsable = self.reader.read("claude:bad", (units["bad.jsonl"],))
        self.assertEqual((unparsable.session, unparsable.unreadable), (None, (UnreadableSource(Harness.CLAUDE, bad, CANNOT_BE_PARSED_REASON),)))
        empty = self.reader.read("claude:empty", (units["empty.jsonl"],))
        self.assertEqual((empty.session, empty.unreadable), (None, ()))

    def test_claude_read_plain_user_entries_open_prompts_only_in_bridge_sessions_wherever_the_marker_is(self):
        plain = _plain_user(3, "remote ask")
        for records in ([BRIDGE, _typed(1), plain], [_typed(1), plain, BRIDGE]):
            with self.subTest(first=records[0]["type"]):
                self.assertEqual([p.text for p in self._prompts(records)], ["ask", "remote ask"])
        plain_blocks = _plain_user(3, [{"type": "tool_result", "content": "ok"}])
        self.assertEqual([p.text for p in self._prompts([BRIDGE, _typed(1), plain_blocks])], ["ask"])

    def test_claude_read_plain_user_entry_outside_bridge_session_hands_its_reply_to_the_previous_prompt(self):
        records = [_typed(1), _answer(2, "m1", _usage(5), "a"), _plain_user(3, "plain"), _answer(4, "m2", _usage(6), "b")]
        (prompt,) = self._prompts(records)
        self.assertEqual((prompt.reply_text, prompt.reply_at, [r.output_tokens for r in prompt.requests]), ("b", _at(4), [5, 6]))

    def test_claude_read_queued_command_attachment_opens_a_prompt_only_in_prompt_mode(self):
        records = [_queued(None, "untimed"), _queued(1, "mid-turn ask"), _queued(2, "<task-notification>x</task-notification>", "task-notification"),
                   _queued(3, "meta", isMeta=True), _queued(4, [{"type": "text", "text": "blocks"}, {"type": "image"}]),
                   _queued(5, "<task-notification>y</task-notification>"), _answer(6, "m1", _usage(2), "r")]
        prompts = self._prompts(records)
        self.assertEqual([(p.text, p.submitted_at) for p in prompts], [("mid-turn ask", _at(1)), ("blocks", _at(4))])
        self.assertEqual(prompts[1].reply_text, "r")

    def test_claude_read_slash_command_needs_a_reply_and_is_logged_as_typed(self):
        records = [_plain_user(1, "<command-name>/clear</command-name>"), _typed(2, "ask"), _answer(3, "m1", _usage(), "r"),
                   _plain_user(4, "<command-name>/x</command-name>\n<command-args>a  b</command-args>"), _answer(5, "m2", _usage(), "r2"),
                   _plain_user(6, "<command-message>m</command-message>"), _typed(7, "last")]
        self.assertEqual([p.text for p in self._prompts(records)], ["ask", "/x a  b", "last"])

    def test_claude_read_task_notification_is_never_a_prompt_whatever_its_source(self):
        text = "<task-notification>\n<task-id>a</task-id>\n</task-notification>"
        records = [BRIDGE, _typed(1), _typed(2, text, promptSource="sdk"), _typed(3, text, promptSource="queued"),
                   _plain_user(4, text), _answer(5, "m1", _usage(), "r")]
        (prompt,) = self._prompts(records)
        self.assertEqual((prompt.text, prompt.reply_text), ("ask", "r"))

    def test_claude_read_synthetic_entries_add_no_request_text_time_or_reply_to_a_plain_user(self):
        synthetic = _on_model(_answer(9, "m2", _usage(50), "No response requested."), "<synthetic>")
        records = [_typed(1), _answer(2, "m1", _usage(5), "real"), synthetic, _plain_user(10, "<command-name>/x</command-name>"), synthetic]
        (prompt,) = self._prompts(records)
        self.assertEqual((prompt.reply_text, prompt.reply_at, [r.output_tokens for r in prompt.requests]), ("real", _at(2), [5]))

    def test_claude_read_synthetic_only_answer_still_gives_a_slash_command_its_row_with_the_synthetic_text_as_reply_and_no_request(self):
        limit = _api_error(_answer(5, "m1", _usage(0), "You've hit your org's monthly spend limit"))
        (prompt,) = self._prompts([_plain_user(1, COMMAND_TEXT), limit])
        self.assertEqual((prompt.text, prompt.reply_text, prompt.reply_at, prompt.requests),
                         ("/x:iterative-review", "You've hit your org's monthly spend limit", _at(5), ()))

    def test_claude_read_synthetic_only_answer_is_the_reply_of_a_typed_prompt_but_never_beats_a_real_one(self):
        limit = _api_error(_answer(5, "m1", _usage(0), "Rate limited"))
        (only,) = self._prompts([_typed(1), limit])
        self.assertEqual((only.reply_text, only.requests), ("Rate limited", ()))
        (mixed,) = self._prompts([_typed(1), _answer(2, "m2", _usage(3), "real"), limit])
        self.assertEqual((mixed.reply_text, [r.output_tokens for r in mixed.requests]), ("real", [3]))

    def test_claude_read_synthetic_message_that_is_no_api_error_never_answers_a_slash_command(self):
        note = _on_model(_answer(5, "m1", _usage(0), "No response requested."), "<synthetic>")
        self.assertEqual(self._prompts([_plain_user(1, COMMAND_TEXT), note]), ())

    def test_claude_read_synthetic_answer_is_not_handed_to_the_turn_before_a_plain_entry(self):
        limit = _api_error(_answer(5, "m1", _usage(0), "Rate limited"))
        (prompt,) = self._prompts([_typed(1), _plain_user(2, "plain"), limit])
        self.assertEqual((prompt.reply_text, prompt.requests), (None, ()))

    def test_claude_read_plain_text_of_a_python_sdk_run_is_a_prompt_with_its_reply_and_tokens_without_a_bridge_session(self):
        sdk = {"entrypoint": "sdk-py", "userType": "external"}
        records = [_plain_user(1, "run the cron job", **sdk), _answer(2, "m1", _usage(7), "done")]
        (prompt,) = self._prompts(records)
        self.assertEqual((prompt.text, prompt.reply_text, [r.output_tokens for r in prompt.requests]), ("run the cron job", "done", [7]))

    def test_claude_read_python_sdk_entries_that_are_no_typed_text_stay_no_prompts(self):
        sdk = {"entrypoint": "sdk-py", "userType": "external"}
        noise = [_plain_user(2, "<local-command-stdout>x</local-command-stdout>", **sdk), _plain_user(3, "meta", isMeta=True, **sdk),
                 _plain_user(4, [{"type": "tool_result", "content": "ok"}], **sdk), _plain_user(5, "<task-notification>x</task-notification>", **sdk)]
        (prompt,) = self._prompts([_typed(1), *noise, _answer(7, "m1", _usage(), "r")])
        self.assertEqual((prompt.text, prompt.reply_text), ("ask", "r"))

    def test_claude_read_plain_text_of_other_entrypoints_still_needs_a_bridge_session(self):
        records = [_plain_user(1, "plain", entrypoint="cli", userType="external"), _answer(2, "m1", _usage(), "r")]
        self.assertEqual(self._prompts(records), ())

    def test_claude_read_legacy_cache_creation_tokens_count_as_5m_unless_the_split_is_present(self):
        legacy = _usage(cache_creation_input_tokens=30)
        split = _usage(cache_creation_input_tokens=30, cache_creation={"ephemeral_5m_input_tokens": 1, "ephemeral_1h_input_tokens": 2})
        empty_split = _usage(cache_creation_input_tokens=30, cache_creation={})
        null_split = _usage(cache_creation_input_tokens=30, cache_creation=None)
        records = [_typed(1), _answer(2, "m1", legacy), _answer(3, "m2", split), _answer(4, "m3", empty_split), _answer(5, "m4", null_split)]
        (prompt,) = self._prompts(records)
        self.assertEqual([(r.cache_write_5m_tokens, r.cache_write_1h_tokens) for r in prompt.requests], [(30, 0), (1, 2), (0, 0), (30, 0)])

    def test_claude_read_plain_entries_that_only_report_local_output_shell_or_interrupt_are_no_bridge_prompts(self):
        noise = ["<local-command-stdout>Goodbye!</local-command-stdout>", "<bash-input>ls</bash-input>",
                 "<bash-stdout>a</bash-stdout>", "[Request interrupted by user]", "[Request interrupted by user for tool use]",
                 "Another Claude session sent a message:\n<teammate-message teammate_id=\"x\">hi</teammate-message>"]
        blocks = [_plain_user(20, [{"type": "text", "text": "[Request interrupted by user]"}])]
        records = [BRIDGE, _typed(1)] + [_plain_user(2 + i, text) for i, text in enumerate(noise)] + blocks + [_answer(30, "m1", _usage(), "r")]
        (prompt,) = self._prompts(records)
        self.assertEqual((prompt.text, prompt.reply_text), ("ask", "r"))

    def test_claude_read_prompt_that_merely_starts_with_an_angle_bracket_still_opens_a_row(self):
        records = [_typed(1, "<Role>act as a tester</Role>", promptSource="sdk"), _typed(2, [{"type": "text", "text": "<task-notification>x"}], promptSource="sdk")]
        self.assertEqual([p.text for p in self._prompts(records)], ["<Role>act as a tester</Role>"])

    def test_claude_read_slash_command_keeps_a_reply_that_arrives_after_a_plain_entry(self):
        records = [_plain_user(1, COMMAND_TEXT), _plain_user(2, "plain"), _answer(3, "m1", _usage(), "r")]
        (prompt,) = self._prompts(records)
        self.assertEqual((prompt.text, prompt.reply_text), ("/x:iterative-review", "r"))

    def test_claude_read_slash_command_followed_by_a_reply_without_usage_is_a_prompt(self):
        (prompt,) = self._prompts([_plain_user(1, COMMAND_TEXT), _answer(2, "m1", None, "done")])
        self.assertEqual((prompt.reply_text, prompt.requests), ("done", ()))

    def test_claude_read_bridge_plain_entries_of_any_shape_and_only_prompt_mode_queued_attachments_open_prompts(self):
        absent_key = {"type": "user", "timestamp": STAMP % 2, "message": {"content": "no key at all"}}
        system = _typed(3, "<task-notification>x</task-notification>", promptSource="system")
        ordinary_system = _typed(4, "system text", promptSource="system")
        block_list = _plain_user(5, [{"type": "text", "text": "from blocks"}])
        attachments = [{"type": "attachment", "timestamp": STAMP % 6, "attachment": other} for other in
                       ({"type": "hook_success", "prompt": "x", "commandMode": "prompt"}, {"type": "queued_command", "prompt": "no mode"},
                        "not a dict", None)]
        records = [BRIDGE, _typed(1), absent_key, system, ordinary_system, block_list, *attachments, _queued(7, [{"type": "image"}])]
        self.assertEqual([p.text for p in self._prompts(records)], ["ask", "no key at all", "from blocks", ""])

    def test_claude_read_replies_before_the_first_prompt_stay_unattached_even_after_a_plain_entry(self):
        records = [_plain_user(1, "plain"), _answer(2, "m1", _usage(9), "early"), _typed(3, "ask")]
        (prompt,) = self._prompts(records)
        self.assertEqual((prompt.text, prompt.reply_text, prompt.requests), ("ask", None, ()))

    def test_claude_read_copies_of_one_message_split_by_a_new_prompt_count_in_each_turn(self):
        records = [_typed(1), _answer(2, "m1", _usage(4), "a"), _queued(3, "mid-turn"), _answer(4, "m1", _usage(6), "a")]
        first, second = self._prompts(records)
        self.assertEqual(([r.output_tokens for r in first.requests], [r.output_tokens for r in second.requests]), ([4], [6]))

    def test_claude_read_interleaved_copies_keep_the_order_of_their_first_appearance(self):
        records = [_typed(1), _answer(2, "m1", _usage(1), "a"), _answer(3, "m2", _usage(2), "b"), _answer(4, "m1", _usage(3), "a")]
        (prompt,) = self._prompts(records)
        self.assertEqual([r.output_tokens for r in prompt.requests], [3, 2])

    def test_claude_read_interrupt_marker_between_streamed_copies_changes_nothing(self):
        records = [_typed(1), _answer(2, "m1", _usage(4), "a"), _plain_user(3, "[Request interrupted by user]"), _answer(4, "m1", _usage(6), "a")]
        (prompt,) = self._prompts(records)
        self.assertEqual(([r.output_tokens for r in prompt.requests], prompt.reply_at), ([6], _at(4)))


if __name__ == "__main__":
    unittest.main()
