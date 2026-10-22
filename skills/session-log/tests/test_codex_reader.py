"""Codex adapter of the reader port (S7, S43): rollouts under `sessions/` and `archived_sessions/`, their keys and fingerprints,
the session of a rollout from its first line, and the whole session read from the root rollout."""
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from sessionlog.codex_reader import CodexReader
from sessionlog.model import Harness
from sessionlog.reader_port import (CANNOT_BE_DECODED_REASON, CANNOT_BE_LISTED_REASON, CANNOT_BE_OPENED_REASON,
                                    CANNOT_BE_PARSED_REASON, UNSAFE_ID_REASON, UnreadableSource)
from tests.store_fakes import FakeEntry, scandir_wrapping

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "codex")
ROOT_FIXTURE = os.path.join(FIXTURES, "root_with_token_usage_records.jsonl")
CHILD_FIXTURE = os.path.join(FIXTURES, "forked_child_with_inherited_total.jsonl")
ROOT_ID = "1a000001-0000-7000-8000-000000000001"
CHILD_ID = "1a200002-0000-7000-8000-000000000002"
CHILD_ROOT_ID = "1a200001-0000-7000-8000-000000000001"
ROOT_NAME = "rollout-2026-09-11T10-10-15-%s.jsonl" % ROOT_ID
CHILD_NAME = "rollout-2026-08-07T15-31-06-%s.jsonl" % CHILD_ID
DAY_FOLDER = os.path.join("2026", "09", "11")
ONE_SECOND_NS = 1_000_000_000
CWD = "/work/proj"


def _meta_line(**payload) -> str:
    fields = {"id": ROOT_ID, "session_id": ROOT_ID, "timestamp": "2026-09-11T08:10:15.374Z", "cwd": CWD}
    return json.dumps({"timestamp": "2026-09-11T08:10:15.400Z", "type": "session_meta", "payload": {**fields, **payload}}) + "\n"


def _user_record(text) -> dict:
    return {"timestamp": "2026-09-11T08:12:16.703Z", "type": "event_msg",
            "payload": {"type": "item_completed", "item": {"type": "UserMessage", "content": [{"type": "text", "text": text}]}}}


def _rollout_text(text, **payload) -> str:
    """A rollout whose only prompt says `text`."""
    return _meta_line(**payload) + json.dumps(_user_record(text)) + "\n"


class CodexReaderTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        self.sessions = os.path.join(self.root, "sessions")
        self.archived = os.path.join(self.root, "archived_sessions")
        self.day = os.path.join(self.sessions, DAY_FOLDER)
        os.makedirs(self.day)
        self.reader = CodexReader(self.root)

    def _install(self, name=ROOT_NAME, folder=None, source=ROOT_FIXTURE):
        folder = folder or self.day
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        shutil.copy(source, path)
        return path

    def _write(self, name, text, folder=None):
        folder = folder or self.day
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        return path

    def _write_bytes(self, name, data, folder=None):
        path = self._write(name, "", folder)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def _unit(self, path=None):
        units = self.reader.scan().units
        if path is None:
            (unit,) = units
            return unit
        (unit,) = [unit for unit in units if unit.key == path]
        return unit

    def _read_root(self):
        unit = self._unit()
        return self.reader.read(self.reader.session_of(unit), (unit,))


class CodexScanTest(CodexReaderTestCase):
    def test_scan_lists_rollouts_of_sessions_and_archived_sessions_plain_and_compressed(self):
        plain = self._install()
        archived = self._install("rollout-2026-01-01T00-00-00-arch.jsonl", self.archived)
        compressed = self._write("rollout-2026-09-10T00-00-00-zst.jsonl.zst", "not zstd at all")
        deep = self._install("rollout-2026-01-02T00-00-00-deep.jsonl", os.path.join(self.sessions, "a", "b", "c", "d"))
        keys = [unit.key for unit in self.reader.scan().units]
        self.assertEqual(sorted([plain, archived, compressed[: -len(".zst")], deep]), keys)

    def test_scan_key_drops_zst_and_the_fingerprint_is_the_mtime_alone(self):
        path = self._install()
        os.utime(path, ns=(0, 5 * ONE_SECOND_NS))
        (plain,) = self.reader.scan().units
        compressed = path + ".zst"
        with open(compressed, "wb") as handle:
            handle.write(b"compressed bytes of another size")
        os.utime(compressed, ns=(0, 5 * ONE_SECOND_NS))
        os.remove(path)
        (after,) = self.reader.scan().units
        self.assertEqual((plain.key, plain.fingerprint, plain.modified_at), (after.key, after.fingerprint, after.modified_at))
        self.assertEqual(str(5 * ONE_SECOND_NS), after.fingerprint)

    def test_scan_fingerprint_changes_with_the_mtime_but_not_with_the_size(self):
        path = self._install()
        stat = os.stat(path)
        (before,) = self.reader.scan().units
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n")
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertEqual(before.fingerprint, self.reader.scan().units[0].fingerprint)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + ONE_SECOND_NS))
        self.assertNotEqual(before.fingerprint, self.reader.scan().units[0].fingerprint)

    def test_scan_lists_the_plain_file_when_a_rollout_exists_plain_and_compressed_whatever_the_listing_order(self):
        path = self._install()
        os.utime(path, ns=(0, 3 * ONE_SECOND_NS))
        compressed = path + ".zst"
        with open(compressed, "wb") as handle:
            handle.write(b"x")
        os.utime(compressed, ns=(0, 9 * ONE_SECOND_NS))
        for reverse in (False, True):
            with self.subTest(reverse=reverse), scandir_wrapping(reverse=reverse):
                (unit,) = self.reader.scan().units
                self.assertEqual((path, str(3 * ONE_SECOND_NS)), (unit.key, unit.fingerprint))

    def test_scan_modified_at_is_the_file_mtime_as_aware_utc(self):
        path = self._install()
        os.utime(path, ns=(0, 1_700_000_000 * ONE_SECOND_NS))
        (unit,) = self.reader.scan().units
        self.assertEqual(datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc), unit.modified_at)
        self.assertEqual(timedelta(0), unit.modified_at.utcoffset())

    def test_scan_missing_folders_give_an_empty_result(self):
        result = CodexReader(os.path.join(self.root, "nowhere")).scan()
        self.assertEqual(((), ()), (result.units, result.unreadable))

    def test_scan_only_one_of_the_two_folders_existing_is_fine(self):
        shutil.rmtree(self.sessions)
        self._install(folder=self.archived)
        self.assertEqual(1, len(self.reader.scan().units))

    def test_scan_ignores_entries_that_are_not_rollouts(self):
        path = self._install()
        self._write("notes.txt", "x")
        self._write("rollout.zst", "x")
        self._write("rollout.jsonl.gz", "x")
        self._write("rollout.jsonl.zst.tmp", "x")
        os.mkdir(os.path.join(self.day, "dir.jsonl"))
        os.mkdir(os.path.join(self.day, "dir.jsonl.zst"))
        self._write("stray.jsonl", "{}\n", self.root)
        self.assertEqual([path], [unit.key for unit in self.reader.scan().units])

    @unittest.skipIf(os.name == "nt", "needs POSIX symlinks")
    def test_scan_does_not_walk_into_a_symlinked_folder(self):
        path = self._install()
        os.symlink(self.sessions, os.path.join(self.day, "loop"))
        self.assertEqual([path], [unit.key for unit in self.reader.scan().units])

    @unittest.skipIf(os.name == "nt", "needs POSIX symlinks")
    def test_scan_lists_a_symlink_to_a_rollout_file_and_ignores_a_dangling_one(self):
        target = self._write("target.jsonl", _meta_line(), self.root)
        os.symlink(target, os.path.join(self.day, "rollout-link.jsonl"))
        os.symlink(os.path.join(self.root, "nowhere.jsonl"), os.path.join(self.day, "rollout-dangling.jsonl"))
        self.assertEqual([os.path.join(self.day, "rollout-link.jsonl")], [unit.key for unit in self.reader.scan().units])

    def test_scan_sorted_by_key_whatever_the_listing_order(self):
        for name in ("rollout-b.jsonl", "rollout-a.jsonl", "rollout-c.jsonl"):
            self._install(name)
        self._install("rollout-z.jsonl", self.archived)
        with scandir_wrapping(reverse=True):
            keys = [unit.key for unit in self.reader.scan().units]
        self.assertEqual(sorted(keys), keys)
        self.assertEqual(4, len(keys))

    def test_scan_skips_a_file_that_vanished_before_stat(self):
        self._install()
        gone = FakeEntry(os.path.join(self.day, "gone.jsonl"), FileNotFoundError())
        with scandir_wrapping({self.day: [gone]}):
            result = self.reader.scan()
        self.assertEqual(([ROOT_NAME], ()), ([os.path.basename(unit.key) for unit in result.units], result.unreadable))
        self.assertTrue(gone.queried)

    def test_scan_reports_a_file_that_cannot_be_examined_and_lists_the_rest(self):
        self._install()
        for error in (PermissionError(), OSError("I/O error")):
            with self.subTest(error=type(error).__name__):
                locked = FakeEntry(os.path.join(self.day, "locked.jsonl"), error)
                with scandir_wrapping({self.day: [locked]}):
                    result = self.reader.scan()
                self.assertEqual(1, len(result.units))
                self.assertEqual((UnreadableSource(Harness.CODEX, locked.path, CANNOT_BE_LISTED_REASON),), result.unreadable)

    def test_scan_reports_a_compressed_file_that_cannot_be_examined_under_its_key(self):
        locked = FakeEntry(os.path.join(self.day, "rollout-x.jsonl.zst"), PermissionError())
        with scandir_wrapping({self.day: [locked]}):
            result = self.reader.scan()
        self.assertEqual((UnreadableSource(Harness.CODEX, os.path.join(self.day, "rollout-x.jsonl"), CANNOT_BE_LISTED_REASON),),
                         result.unreadable)

    def test_scan_reports_no_problem_for_a_file_whose_other_form_is_listed_under_the_same_key(self):
        """`x.jsonl` listed with a failing `x.jsonl.zst` beside it, and the other way round: the key has its unit."""
        plain = self._install("rollout-p.jsonl")
        failing_zst = FakeEntry(os.path.join(self.day, "rollout-p.jsonl.zst"), PermissionError())
        failing_plain = FakeEntry(os.path.join(self.day, "rollout-q.jsonl"), PermissionError())
        compressed = self._write("rollout-q.jsonl.zst", "compressed")
        with scandir_wrapping({self.day: [failing_zst, failing_plain]}):
            result = self.reader.scan()
        self.assertEqual((sorted([plain, compressed[: -len(".zst")]]), ()), ([unit.key for unit in result.units], result.unreadable))

    def test_scan_reports_a_folder_entry_that_cannot_be_examined_and_skips_one_that_vanished(self):
        self._install()
        denied = FakeEntry(os.path.join(self.day, "denied"), PermissionError())
        vanished = FakeEntry(os.path.join(self.day, "vanished"), FileNotFoundError())
        with scandir_wrapping({self.day: [denied, vanished]}):
            result = self.reader.scan()
        self.assertEqual((1, (UnreadableSource(Harness.CODEX, denied.path, CANNOT_BE_LISTED_REASON),)),
                         (len(result.units), result.unreadable))
        self.assertTrue(vanished.queried)

    def test_scan_reports_an_unlistable_folder_and_scans_the_rest(self):
        self._install()
        locked = os.path.join(self.sessions, "2026", "locked")
        os.makedirs(locked)
        with scandir_wrapping(fail={locked: PermissionError()}):
            result = self.reader.scan()
        self.assertEqual(1, len(result.units))
        self.assertEqual((UnreadableSource(Harness.CODEX, locked, CANNOT_BE_LISTED_REASON),), result.unreadable)

    def test_scan_reports_each_unlistable_top_folder_and_a_vanished_one_is_not_a_problem(self):
        with scandir_wrapping(fail={self.sessions: PermissionError(), self.archived: PermissionError()}):
            result = self.reader.scan()
        self.assertEqual(((), (UnreadableSource(Harness.CODEX, self.archived, CANNOT_BE_LISTED_REASON),
                               UnreadableSource(Harness.CODEX, self.sessions, CANNOT_BE_LISTED_REASON))),
                         (result.units, result.unreadable))
        with scandir_wrapping(fail={self.sessions: FileNotFoundError()}):
            result = self.reader.scan()
        self.assertEqual(((), ()), (result.units, result.unreadable))

    def test_scan_sorts_unreadable_by_path(self):
        locked = {os.path.join(self.sessions, "b"): PermissionError(), os.path.join(self.sessions, "a"): PermissionError()}
        for path in locked:
            os.makedirs(path)
        with scandir_wrapping(fail=locked, reverse=True):
            paths = [source.path for source in self.reader.scan().unreadable]
        self.assertEqual(sorted(locked), paths)


class CodexSessionOfTest(CodexReaderTestCase):
    def test_session_of_is_the_root_id_of_the_first_line(self):
        self._install()
        self.assertEqual("codex:" + ROOT_ID, self.reader.session_of(self._unit()))

    def test_session_of_a_child_rollout_names_the_root_by_session_id(self):
        self._install(CHILD_NAME, source=CHILD_FIXTURE)
        self.assertEqual("codex:" + CHILD_ROOT_ID, self.reader.session_of(self._unit()))

    def test_session_of_without_session_id_is_the_parent_thread_id_else_the_id(self):
        cases = ((_meta_line(session_id=None, parent_thread_id="parent-1"), "codex:parent-1"),
                 (_meta_line(session_id=None), "codex:" + ROOT_ID))
        for text, expected in cases:
            with self.subTest(expected=expected):
                path = self._write("rollout-x.jsonl", text)
                self.assertEqual(expected, self.reader.session_of(self._unit(path)))

    def test_session_of_reads_the_beginning_of_the_file_only_so_a_broken_end_is_no_problem(self):
        filler = b'{"type":"future_record"}\n' * 20000
        path = self._write_bytes("rollout-x.jsonl", _meta_line().encode("utf-8") + filler + b"\xff\xfe broken\n")
        self.assertEqual("codex:" + ROOT_ID, self.reader.session_of(self._unit(path)))

    def test_session_of_skips_blank_lines_and_a_byte_order_mark_before_the_meta(self):
        path = self._write("rollout-x.jsonl", "\n  \n" + _meta_line())
        self.assertEqual("codex:" + ROOT_ID, self.reader.session_of(self._unit(path)))
        self._write_bytes("rollout-x.jsonl", b"\xef\xbb\xbf" + _meta_line().encode("utf-8"))
        self.assertEqual("codex:" + ROOT_ID, self.reader.session_of(self._unit(path)))

    def test_session_of_treats_a_line_of_unicode_whitespace_as_blank_like_the_parser_does(self):
        path = self._write("rollout-x.jsonl", "\u00a0\n" + _meta_line())
        self.assertEqual("codex:" + ROOT_ID, self.reader.session_of(self._unit(path)))

    def test_session_of_is_none_when_the_first_line_is_not_a_session_meta(self):
        for text in ("", "\n\n", "not json\n", json.dumps({"type": "event_msg", "payload": {}}) + "\n" + _meta_line(),
                     json.dumps({"type": "session_meta", "payload": {}}) + "\n"):
            with self.subTest(text=text):
                path = self._write("rollout-x.jsonl", text)
                self.assertIsNone(self.reader.session_of(self._unit(path)))

    def test_session_of_is_none_for_an_undecodable_or_vanished_file(self):
        path = self._write_bytes("rollout-x.jsonl", b"\xff\xfe\n")
        unit = self._unit(path)
        self.assertIsNone(self.reader.session_of(unit))
        os.remove(path)
        self.assertIsNone(self.reader.session_of(unit))

    def test_session_of_a_compressed_only_rollout_is_none(self):
        self._write("rollout-x.jsonl.zst", "compressed")
        self.assertIsNone(self.reader.session_of(self._unit()))

    def test_session_of_returns_the_key_of_an_unsafe_root_id_for_sync_to_list(self):
        path = self._write("rollout-x.jsonl", _meta_line(session_id="bad.id"))
        self.assertEqual("codex:bad.id", self.reader.session_of(self._unit(path)))


class CodexReadTest(CodexReaderTestCase):
    def test_read_builds_the_session_of_the_root_rollout_from_a_real_fixture(self):
        self._install()
        result = self._read_root()
        session = result.session
        self.assertEqual(((), Harness.CODEX, ROOT_ID, CWD, datetime.fromisoformat("2026-09-11T08:10:15.374+00:00")),
                         (result.unreadable, session.harness, session.session_id, session.project_dir, session.started_at))
        self.assertEqual(["prompt 1", "prompt 2"], [prompt.text for prompt in session.prompts])
        self.assertEqual([6, 1], [len(prompt.requests) for prompt in session.prompts])
        self.assertEqual(((), None), (session.sub_agents, session.orphan_of_parent_id))

    def test_read_takes_the_prompts_from_the_root_rollout_only(self):
        self._install()
        self._install(CHILD_NAME, source=CHILD_FIXTURE)
        units = self.reader.scan().units
        result = self.reader.read("codex:" + ROOT_ID, units)
        self.assertEqual(["prompt 1", "prompt 2"], [prompt.text for prompt in result.session.prompts])
        self.assertEqual(((), None), (result.unreadable, self.reader.read("codex:" + CHILD_ROOT_ID, units).session))

    def test_read_finds_the_root_rollout_in_archived_sessions(self):
        self._install(folder=self.archived)
        shutil.rmtree(self.sessions)
        self.assertEqual(ROOT_ID, self._read_root().session.session_id)

    def test_read_finds_the_root_of_a_reverted_thread_named_with_a_rollout_id_suffix(self):
        """A reverted thread's rollout is named `rollout-<time>-<thread id>_<rollout id>` (codex `rollout_file_name.rs`)."""
        self._write("rollout-2026-09-11T10-10-15-%s_01a0aaaa-0000-7000-8000-00000000000a.jsonl" % ROOT_ID, _rollout_text("reverted root"))
        self.assertEqual(["reverted root"], [p.text for p in self._read_root().session.prompts])

    def test_read_does_not_take_a_unit_for_the_root_because_its_name_carries_the_root_id(self):
        child = self._write("rollout-2026-09-11T10-10-15-child-%s.jsonl" % ROOT_ID,
                            _rollout_text("child", id=CHILD_ID, session_id=ROOT_ID, parent_thread_id=ROOT_ID))
        result = self.reader.read("codex:" + ROOT_ID, (self._unit(child),))
        self.assertEqual((None, ()), (result.session, result.unreadable))

    def test_read_finds_a_renamed_root_by_its_first_line_when_no_unit_named_after_the_root_id_is_the_root(self):
        self._write("rollout-renamed.jsonl", _rollout_text("renamed root"))
        self._write("rollout-child-%s.jsonl" % CHILD_ID, _rollout_text("child", id=CHILD_ID, session_id=ROOT_ID, parent_thread_id=ROOT_ID))
        result = self.reader.read("codex:" + ROOT_ID, self.reader.scan().units)
        self.assertEqual((["renamed root"], ()), ([p.text for p in result.session.prompts], result.unreadable))

    def test_read_lists_the_units_it_cannot_read_while_looking_for_a_renamed_root_greatest_key_first_on_equal_times(self):
        broken = self._write("rollout-broken.jsonl", "not json\n")
        gone = self._write("rollout-gone.jsonl", "x")
        for path in (broken, gone):
            os.utime(path, ns=(0, ONE_SECOND_NS))
        units = self.reader.scan().units
        os.remove(gone)
        result = self.reader.read("codex:" + ROOT_ID, units)
        self.assertEqual((None, (UnreadableSource(Harness.CODEX, gone, CANNOT_BE_OPENED_REASON),
                                 UnreadableSource(Harness.CODEX, broken, CANNOT_BE_PARSED_REASON))), (result.session, result.unreadable))

    def test_read_lists_a_renamed_root_whose_header_is_right_but_whose_content_cannot_be_decoded(self):
        filler = b'{"type":"future_record"}\n' * 20000
        path = self._write_bytes("rollout-renamed.jsonl", _meta_line().encode("utf-8") + filler + b"\xff\xfe\n")
        result = self.reader.read("codex:" + ROOT_ID, self.reader.scan().units)
        self.assertEqual((None, (UnreadableSource(Harness.CODEX, path, CANNOT_BE_DECODED_REASON),)), (result.session, result.unreadable))

    def test_read_skips_an_empty_unit_while_looking_for_a_renamed_root_in_any_unit_order(self):
        empty = self._write("rollout-empty.jsonl", "")
        renamed = self._write("rollout-renamed.jsonl", _rollout_text("renamed root"))
        os.utime(empty, ns=(0, 2 * ONE_SECOND_NS))
        os.utime(renamed, ns=(0, ONE_SECOND_NS))
        units = self.reader.scan().units
        for ordered in (units, tuple(reversed(units))):
            result = self.reader.read("codex:" + ROOT_ID, ordered)
            self.assertEqual((["renamed root"], ()), ([p.text for p in result.session.prompts], result.unreadable))

    def test_read_takes_the_root_even_when_a_newer_child_names_the_same_root_session_or_carries_the_root_id_in_its_name(self):
        root = self._write("rollout-a-%s.jsonl" % ROOT_ID, _rollout_text("root"))
        child = self._write("rollout-b-%s.jsonl" % CHILD_ID, _rollout_text("child", id=CHILD_ID, session_id=ROOT_ID, parent_thread_id=ROOT_ID))
        named_child = self._write("rollout-c-%s_%s.jsonl" % (CHILD_ID, ROOT_ID), _rollout_text("named child", id=CHILD_ID, session_id=ROOT_ID))
        os.utime(root, ns=(0, ONE_SECOND_NS))
        os.utime(child, ns=(0, 9 * ONE_SECOND_NS))
        os.utime(named_child, ns=(0, 8 * ONE_SECOND_NS))
        units = self.reader.scan().units
        for ordered in (units, tuple(reversed(units))):
            result = self.reader.read("codex:" + ROOT_ID, ordered)
            self.assertEqual((["root"], ()), ([p.text for p in result.session.prompts], result.unreadable))

    def test_read_does_not_learn_the_session_of_a_unit_again(self):
        self._install()
        unit = self._unit()
        with mock.patch.object(CodexReader, "session_of", side_effect=AssertionError("session_of called")):
            self.assertEqual(ROOT_ID, self.reader.read("codex:" + ROOT_ID, (unit,)).session.session_id)

    def test_read_opens_only_the_root_rollout_once_whatever_the_other_units_hold(self):
        root = self._install()
        self._install(CHILD_NAME, source=CHILD_FIXTURE)
        self._write("rollout-unrelated.jsonl", "not json\n")
        opened = []
        real_open = open

        def counting(name, *args, **kwargs):
            opened.append(name)
            return real_open(name, *args, **kwargs)

        with mock.patch("sessionlog.store_files.open", counting, create=True):
            self.assertEqual(ROOT_ID, self.reader.read("codex:" + ROOT_ID, self.reader.scan().units).session.session_id)
        self.assertEqual([root], opened)

    def test_read_without_a_root_unit_or_for_another_harnesss_key_gives_an_empty_result(self):
        self._install()
        unit = self._unit()
        for key, units in (("codex:" + ROOT_ID, ()), ("codex:other", (unit,)), (ROOT_ID, (unit,)), ("codex:", (unit,)),
                           ("claude:" + ROOT_ID, (unit,))):
            with self.subTest(key=key, units=len(units)):
                result = self.reader.read(key, units)
                self.assertEqual((None, ()), (result.session, result.unreadable))

    def test_read_of_an_unsafe_root_id_is_unreadable_and_builds_no_session(self):
        path = self._write("rollout-2026-x-bad.id.jsonl", _meta_line(session_id="bad.id", id="bad.id"))
        result = self.reader.read("codex:bad.id", (self._unit(path),))
        self.assertEqual((None, (UnreadableSource(Harness.CODEX, path, UNSAFE_ID_REASON),)), (result.session, result.unreadable))

    def test_read_of_an_unsafe_root_id_reports_the_newest_unit_then_the_greatest_key_and_none_without_units(self):
        older = self._write("rollout-a-bad.id.jsonl", _meta_line(session_id="bad.id", id="bad.id"))
        tied_low = self._write("rollout-b-bad.id.jsonl", _meta_line(session_id="bad.id", id="bad.id"))
        tied_high = self._write("rollout-c-bad.id.jsonl", _meta_line(session_id="bad.id", id="bad.id"))
        os.utime(older, ns=(0, ONE_SECOND_NS))
        for path in (tied_low, tied_high):
            os.utime(path, ns=(0, 2 * ONE_SECOND_NS))
        units = self.reader.scan().units
        result = self.reader.read("codex:bad.id", units)
        self.assertEqual((None, (UnreadableSource(Harness.CODEX, tied_high, UNSAFE_ID_REASON),)), (result.session, result.unreadable))
        result = self.reader.read("codex:bad.id", ())
        self.assertEqual((None, ()), (result.session, result.unreadable))

    def test_read_of_a_first_line_that_is_not_a_session_meta_is_unreadable(self):
        path = self._write("rollout-x-%s.jsonl" % ROOT_ID, "not json\n" + _meta_line())
        result = self.reader.read("codex:" + ROOT_ID, (self._unit(path),))
        self.assertEqual((None, (UnreadableSource(Harness.CODEX, path, CANNOT_BE_PARSED_REASON),)), (result.session, result.unreadable))

    def test_read_of_an_undecodable_root_is_unreadable(self):
        path = self._write_bytes("rollout-x-%s.jsonl" % ROOT_ID, _meta_line().encode("utf-8") + b"\xff\xfe\n")
        result = self.reader.read("codex:" + ROOT_ID, (self._unit(path),))
        self.assertEqual((None, (UnreadableSource(Harness.CODEX, path, CANNOT_BE_DECODED_REASON),)), (result.session, result.unreadable))

    def test_read_of_a_vanished_file_is_unreadable_as_not_openable(self):
        path = self._install()
        unit = self._unit()
        os.remove(path)
        result = self.reader.read("codex:" + ROOT_ID, (unit,))
        self.assertEqual((None, (UnreadableSource(Harness.CODEX, path, CANNOT_BE_OPENED_REASON),)), (result.session, result.unreadable))

    def test_read_of_a_compressed_only_rollout_is_unreadable_as_not_openable(self):
        compressed = self._write("rollout-x-%s.jsonl.zst" % ROOT_ID, "compressed")
        result = self.reader.read("codex:" + ROOT_ID, (self._unit(),))
        self.assertEqual((None, (UnreadableSource(Harness.CODEX, compressed[: -len(".zst")], CANNOT_BE_OPENED_REASON),)),
                         (result.session, result.unreadable))

    def test_read_builds_the_session_and_lists_a_candidate_unit_that_cannot_be_read(self):
        root = self._install()
        broken = self._write("rollout-broken-%s_x.jsonl" % ROOT_ID, "not json\n")
        os.utime(root, ns=(0, ONE_SECOND_NS))
        os.utime(broken, ns=(0, 9 * ONE_SECOND_NS))
        result = self.reader.read("codex:" + ROOT_ID, self.reader.scan().units)
        self.assertEqual((ROOT_ID, (UnreadableSource(Harness.CODEX, broken, CANNOT_BE_PARSED_REASON),)),
                         (result.session.session_id, result.unreadable))

    def test_read_of_an_empty_rollout_is_no_session_and_no_problem(self):
        path = self._write("rollout-x-%s.jsonl" % ROOT_ID, "")
        result = self.reader.read("codex:" + ROOT_ID, (self._unit(path),))
        self.assertEqual((None, ()), (result.session, result.unreadable))

    def test_read_of_a_meta_without_any_time_is_no_session(self):
        meta = json.loads(_meta_line())
        del meta["timestamp"]
        meta["payload"]["timestamp"] = "x"
        path = self._write("rollout-x-%s.jsonl" % ROOT_ID, json.dumps(meta) + "\n")
        result = self.reader.read("codex:" + ROOT_ID, (self._unit(path),))
        self.assertEqual((None, ()), (result.session, result.unreadable))

    def test_read_tolerates_a_byte_order_mark_and_skips_lines_it_cannot_parse(self):
        data = b"\xef\xbb\xbf" + _meta_line().encode("utf-8") + b"{broken\n" + json.dumps(_user_record("hi")).encode("utf-8") + b"\n{trunc"
        path = self._write_bytes("rollout-x-%s.jsonl" % ROOT_ID, data)
        self.assertEqual(["hi"], [p.text for p in self.reader.read("codex:" + ROOT_ID, (self._unit(path),)).session.prompts])

    def test_read_picks_the_newest_of_the_units_that_are_the_root_and_the_greatest_key_on_a_tie_in_any_order(self):
        older = self._write("rollout-a-%s.jsonl" % ROOT_ID, _rollout_text("older"))
        newer = self._write("rollout-b-%s.jsonl" % ROOT_ID, _rollout_text("newer"), self.archived)
        os.utime(older, ns=(0, ONE_SECOND_NS))
        os.utime(newer, ns=(0, 2 * ONE_SECOND_NS))
        units = self.reader.scan().units
        for ordered in (units, tuple(reversed(units))):
            self.assertEqual("newer", self.reader.read("codex:" + ROOT_ID, ordered).session.prompts[0].text)
        os.utime(older, ns=(0, 2 * ONE_SECOND_NS))
        units = self.reader.scan().units
        greatest_key_text = {older: "older", newer: "newer"}[max(older, newer)]
        for ordered in (units, tuple(reversed(units))):
            self.assertEqual(greatest_key_text, self.reader.read("codex:" + ROOT_ID, ordered).session.prompts[0].text)


if __name__ == "__main__":
    unittest.main()
