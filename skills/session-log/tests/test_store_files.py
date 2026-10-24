"""What every file-based reader shares: queries mapped to unreadable sources, the unit of a regular file and the
open/decode/parse mapping of one file."""
import io
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sessionlog.model import Harness
from sessionlog.reader_port import (CANNOT_BE_DECODED_REASON, CANNOT_BE_LISTED_REASON, CANNOT_BE_OPENED_REASON,
                                    CANNOT_BE_PARSED_REASON, NOT_A_REGULAR_FILE_REASON, SourceUnit, UnreadableSource)
from sessionlog.store_files import FileOpener, NotARegularFileError, StoreFiles, open_text
from tests.store_fakes import FakeEntry, JunctionEntry, StatEntry, scandir_wrapping

ONE_SECOND_NS = 1_000_000_000
FILES = StoreFiles(Harness.OMP)


def _fingerprint(file_stat):
    return "%d:%d" % (file_stat.st_size, file_stat.st_mtime_ns)


class StoreFilesTestCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder)

    def _write(self, name, data=b"{}\n"):
        path = os.path.join(self.folder, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def _entry(self, name):
        (entry,) = [entry for entry in os.scandir(self.folder) if entry.name == name]
        return entry


class ExamineTest(StoreFilesTestCase):
    def test_a_query_result_is_returned_as_it_is(self):
        self.assertEqual(7, FILES.examine("/x", lambda: 7))

    def test_a_vanished_entry_is_none_and_any_other_os_error_is_unreadable_under_the_path_with_the_harness(self):
        def failing(error):
            def query():
                raise error
            return query

        self.assertIsNone(FILES.examine("/x", failing(FileNotFoundError())))
        for error in (PermissionError(), OSError("I/O error")):
            with self.subTest(error=type(error).__name__):
                self.assertEqual(UnreadableSource(Harness.OMP, "/x", CANNOT_BE_LISTED_REASON), FILES.examine("/x", failing(error)))


class ListDirTest(StoreFilesTestCase):
    def test_a_folder_lists_its_entries_a_vanished_one_none_and_an_unlistable_one_is_unreadable(self):
        self._write("a.jsonl")
        self.assertEqual(["a.jsonl"], [entry.name for entry in FILES.list_dir(self.folder)])
        self.assertEqual([], FILES.list_dir(os.path.join(self.folder, "missing")))
        with scandir_wrapping(fail={self.folder: PermissionError()}):
            self.assertEqual(UnreadableSource(Harness.OMP, self.folder, CANNOT_BE_LISTED_REASON), FILES.list_dir(self.folder))


class IsRealDirTest(StoreFilesTestCase):
    def test_a_directory_is_real_and_a_file_is_not(self):
        os.mkdir(os.path.join(self.folder, "dir"))
        self._write("file")
        self.assertEqual((True, False), (FILES.is_real_dir(self._entry("dir")), FILES.is_real_dir(self._entry("file"))))

    @unittest.skipIf(os.name == "nt", "needs POSIX symlinks")
    def test_a_link_to_a_directory_is_not_real(self):
        os.mkdir(os.path.join(self.folder, "dir"))
        os.symlink(os.path.join(self.folder, "dir"), os.path.join(self.folder, "link"))
        self.assertFalse(FILES.is_real_dir(self._entry("link")))

    def test_an_entry_that_vanished_is_none_and_one_that_cannot_be_examined_is_unreadable(self):
        self.assertIsNone(FILES.is_real_dir(FakeEntry("/x/gone", FileNotFoundError())))
        self.assertEqual(UnreadableSource(Harness.OMP, "/x/locked", CANNOT_BE_LISTED_REASON),
                         FILES.is_real_dir(FakeEntry("/x/locked", PermissionError())))

    def test_a_junction_is_not_real_even_though_it_reports_as_a_directory(self):
        self.assertFalse(FILES.is_real_dir(JunctionEntry("/x/junction")))


class RegularFileUnitTest(StoreFilesTestCase):
    def test_a_regular_file_is_a_unit_with_the_given_key_the_fingerprint_made_of_its_stat_and_its_mtime_in_utc(self):
        path = self._write("a.jsonl", b"abc")
        os.utime(path, ns=(0, 1_700_000_000 * ONE_SECOND_NS))
        unit = FILES.regular_file_unit(self._entry("a.jsonl"), "key-of-a", _fingerprint)
        self.assertEqual(SourceUnit("key-of-a", "3:%d" % (1_700_000_000 * ONE_SECOND_NS),
                                    datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)), unit)
        self.assertEqual(timedelta(0), unit.modified_at.utcoffset())

    def test_a_directory_and_a_vanished_file_are_no_unit(self):
        os.mkdir(os.path.join(self.folder, "dir.jsonl"))
        self.assertIsNone(FILES.regular_file_unit(self._entry("dir.jsonl"), "k", _fingerprint))
        self.assertIsNone(FILES.regular_file_unit(FakeEntry("/x/gone.jsonl", FileNotFoundError()), "k", _fingerprint))

    def test_a_file_that_cannot_be_examined_is_unreadable_under_the_key(self):
        self.assertEqual(UnreadableSource(Harness.OMP, "the-key", CANNOT_BE_LISTED_REASON),
                         FILES.regular_file_unit(FakeEntry("/x/locked.jsonl", PermissionError()), "the-key", _fingerprint))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs FIFOs")
    def test_a_fifo_is_unreadable_as_not_a_regular_file_under_the_key_and_never_opened(self):
        os.mkfifo(os.path.join(self.folder, "a.jsonl"))
        self.assertEqual(UnreadableSource(Harness.OMP, "the-key", NOT_A_REGULAR_FILE_REASON),
                         FILES.regular_file_unit(self._entry("a.jsonl"), "the-key", _fingerprint))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs FIFOs")
    def test_open_text_refuses_a_fifo_without_opening_it(self):
        path = os.path.join(self.folder, "a.jsonl")
        os.mkfifo(path)
        with self.assertRaises(NotARegularFileError):
            open_text(path)
        self.assertEqual(NOT_A_REGULAR_FILE_REASON, FILES.parse_file(path, lambda lines: None, FileOpener(open_text)).reason)

    def test_a_modification_time_that_is_no_time_makes_no_unit(self):
        for seconds in (float("inf"), 1e30, -1e30, float("nan")):
            with self.subTest(seconds=seconds):
                file_stat = SimpleNamespace(st_size=1, st_mtime=seconds, st_mtime_ns=0)
                self.assertIsNone(FILES.regular_file_unit(StatEntry("/x/a.jsonl", file_stat), "k", _fingerprint))


class CollectFilesTest(StoreFilesTestCase):
    def _tree(self):
        """`a.jsonl`, `sub/b.jsonl` and `sub/deeper/c.jsonl`, with files and folders that are no `.jsonl` file beside them."""
        paths = [self._write("a.jsonl")]
        for name in ("sub", os.path.join("sub", "deeper")):
            os.makedirs(os.path.join(self.folder, name), exist_ok=True)
        paths += [self._write(os.path.join("sub", "b.jsonl")), self._write(os.path.join("sub", "deeper", "c.jsonl"))]
        self._write("notes.txt")
        self._write("a.jsonl.gz")
        os.mkdir(os.path.join(self.folder, "dir.jsonl"))
        return paths

    def test_the_files_with_the_suffix_at_any_depth_are_units_keyed_by_path_and_nothing_else_is(self):
        paths = self._tree()
        units, problems = FILES.collect_files(self.folder, ".jsonl", _fingerprint)
        self.assertEqual((sorted(paths), []), (sorted(unit.key for unit in units), problems))
        self.assertEqual({"3:%d" % os.stat(path).st_mtime_ns for path in paths}, {unit.fingerprint for unit in units})

    @unittest.skipIf(os.name == "nt", "needs POSIX symlinks")
    def test_a_link_to_a_folder_is_not_walked_but_a_link_to_a_file_is_listed_and_a_dangling_one_is_not(self):
        paths = self._tree()
        os.symlink(self.folder, os.path.join(self.folder, "loop"))
        target = self._write("target.txt")
        os.symlink(target, os.path.join(self.folder, "link.jsonl"))
        os.symlink(os.path.join(self.folder, "nowhere"), os.path.join(self.folder, "dangling.jsonl"))
        units, problems = FILES.collect_files(self.folder, ".jsonl", _fingerprint)
        self.assertEqual((sorted(paths + [os.path.join(self.folder, "link.jsonl")]), []), (sorted(unit.key for unit in units), problems))

    def test_a_missing_folder_gives_nothing_and_an_unlistable_one_is_a_problem_while_the_rest_is_collected(self):
        paths = self._tree()
        self.assertEqual(([], []), FILES.collect_files(os.path.join(self.folder, "missing"), ".jsonl", _fingerprint))
        locked = os.path.join(self.folder, "sub", "deeper")
        with scandir_wrapping(fail={locked: PermissionError()}):
            units, problems = FILES.collect_files(self.folder, ".jsonl", _fingerprint)
        self.assertEqual((sorted(paths[:2]), [UnreadableSource(Harness.OMP, locked, CANNOT_BE_LISTED_REASON)]), (sorted(unit.key for unit in units), problems))

    def test_a_file_that_cannot_be_examined_is_a_problem_under_its_path_and_a_vanished_one_or_folder_is_skipped(self):
        paths = self._tree()
        locked = FakeEntry(os.path.join(self.folder, "locked.jsonl"), PermissionError())
        gone_file = FakeEntry(os.path.join(self.folder, "gone.jsonl"), FileNotFoundError())
        gone_folder = FakeEntry(os.path.join(self.folder, "gone"), FileNotFoundError())
        denied_folder = FakeEntry(os.path.join(self.folder, "denied"), PermissionError())
        with scandir_wrapping({self.folder: [locked, gone_file, gone_folder, denied_folder]}):
            units, problems = FILES.collect_files(self.folder, ".jsonl", _fingerprint)
        self.assertEqual((sorted(paths), sorted([locked.path, denied_folder.path])), (sorted(unit.key for unit in units), sorted(p.path for p in problems)))


class ParseFileTest(StoreFilesTestCase):
    @staticmethod
    def _parse(lines):
        return SimpleNamespace(count=len(list(lines)), unparsable=False)

    def test_what_parse_makes_of_the_lines_is_returned(self):
        path = self._write("a.jsonl", b"one\ntwo\n")
        self.assertEqual(2, FILES.parse_file(path, self._parse).count)

    def test_a_byte_order_mark_is_not_part_of_the_first_line(self):
        path = self._write("a.jsonl", b"\xef\xbb\xbfone\n")
        seen = []
        FILES.parse_file(path, lambda lines: seen.extend(lines) or SimpleNamespace(unparsable=False))
        self.assertEqual(["one\n"], seen)

    def test_an_undecodable_a_missing_and_an_unparsable_file_are_unreadable_with_their_reason(self):
        undecodable = self._write("bad.jsonl", b"\xff\xfe\n")
        missing = os.path.join(self.folder, "missing.jsonl")
        text = self._write("text.jsonl")
        for path, parse, reason in ((undecodable, self._parse, CANNOT_BE_DECODED_REASON), (missing, self._parse, CANNOT_BE_OPENED_REASON),
                                    (self.folder, self._parse, CANNOT_BE_OPENED_REASON),
                                    (text, lambda lines: SimpleNamespace(unparsable=True), CANNOT_BE_PARSED_REASON)):
            with self.subTest(reason=reason):
                self.assertEqual(UnreadableSource(Harness.OMP, path, reason), FILES.parse_file(path, parse))

    def test_the_openers_open_gives_the_lines_and_its_decode_errors_are_undecodable_under_the_path(self):
        path = os.path.join(self.folder, "unit.jsonl")
        opened = []

        def reading(key):
            opened.append(key)
            return io.StringIO("one\ntwo\n")

        self.assertEqual(2, FILES.parse_file(path, self._parse, FileOpener(reading)).count)
        self.assertEqual([path], opened)

        def failing(key):
            raise KeyError(key)

        self.assertEqual(UnreadableSource(Harness.OMP, path, CANNOT_BE_DECODED_REASON),
                         FILES.parse_file(path, self._parse, FileOpener(failing, (KeyError,))))
        self.assertEqual(UnreadableSource(Harness.OMP, path, CANNOT_BE_OPENED_REASON),
                         FILES.parse_file(path, self._parse, FileOpener(lambda key: open(key + ".none"))))


if __name__ == "__main__":
    unittest.main()
