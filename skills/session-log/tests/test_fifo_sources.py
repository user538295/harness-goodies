"""Non-regular sources (BE-36; S62): a FIFO where a source file is expected (a `.jsonl` in the Claude, Codex or OMP store, a Cursor record,
the OpenCode database) is listed unreadable "not a regular file" and never opened, so a sync never hangs on it. Every call that could open
a FIFO runs under a watchdog: a reader that opens one fails the test after `HANG_SECONDS` instead of blocking the run."""
import os
import shutil
import socket
import tempfile
import threading
import unittest
from unittest import mock

from sessionlog.claude_reader import ClaudeReader
from sessionlog.codex_reader import CodexReader
from sessionlog.cursor_reader import CursorHookActivity, CursorReader
from sessionlog.model import Harness
from sessionlog.omp_reader import OmpReader
from sessionlog.opencode_reader import OpenCodeReader
from sessionlog.reader_port import NOT_A_REGULAR_FILE_REASON, SourceUnit, UnreadableSource, build_session_key
from sessionlog.store_files import NotARegularFileError, open_text
from tests import omp_records
from tests import test_claude_reader as claude_tests
from tests import test_codex_reader as codex_tests
from tests.cursor_record_files import CONVERSATION, at_ns, moment, prompt_input, write_record
from tests.store_fakes import write_compressed, write_gzip
from tests.test_sync import SyncTestCase

HANG_SECONDS = 10
SESSION_ID = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
NEEDS_FIFO = unittest.skipUnless(hasattr(os, "mkfifo"), "needs FIFOs")


def within_the_watchdog(call):
    """The result of `call()`, or what it raised; fails the test when it has not returned after `HANG_SECONDS` (blocked on a FIFO)."""
    outcome = []

    def run():
        try:
            outcome.append((call(), None))
        except BaseException as error:  # re-raised in the test's own thread
            outcome.append((None, error))

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(HANG_SECONDS)
    if worker.is_alive():
        raise AssertionError("blocked for %d s: a FIFO was opened" % HANG_SECONDS)
    result, error = outcome[0]
    if error is not None:
        raise error
    return result


def _copy(source, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    shutil.copyfile(source, path)


def _not_regular(harness, *paths):
    return [UnreadableSource(harness, path, NOT_A_REGULAR_FILE_REASON) for path in paths]


class FifoTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

    @staticmethod
    def fifo(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        os.mkfifo(path)
        return path

    @staticmethod
    def swap_for_a_fifo(path):
        os.remove(path)
        os.mkfifo(path)

    @staticmethod
    def scan_of(reader):
        scan = within_the_watchdog(reader.scan)
        return list(scan.units), sorted(scan.unreadable, key=lambda source: source.path)

    def read_after_swap(self, reader, path):
        """The unreadable list of `read` for a file that was scanned as a regular one and became a FIFO afterwards."""
        (unit,), _ = self.scan_of(reader)
        session_key = reader.session_of(unit)
        self.swap_for_a_fifo(path)
        return list(within_the_watchdog(lambda: reader.read(session_key, (unit,))).unreadable)


@NEEDS_FIFO
class ClaudeFifoTest(FifoTestCase):
    def setUp(self):
        super().setUp()
        self.project = os.path.join(self.root, "projects", claude_tests.PROJECT)

    def test_main_and_sub_agent_fifos_are_listed_unreadable_not_a_regular_file(self):
        main = self.fifo(os.path.join(self.project, SESSION_ID + ".jsonl"))
        agent = self.fifo(os.path.join(self.project, SESSION_ID, "subagents", "agent-a1.jsonl"))
        self.assertEqual(([], _not_regular(Harness.CLAUDE, *sorted((main, agent)))), self.scan_of(ClaudeReader(self.root)))

    def test_a_link_to_a_fifo_is_listed_unreadable_and_a_link_to_a_regular_file_stays_a_unit(self):
        target = self.fifo(os.path.join(self.root, "elsewhere", "fifo"))
        regular = os.path.join(self.root, "elsewhere", "regular.jsonl")
        _copy(claude_tests.FIXTURE, regular)
        os.makedirs(self.project)
        link_to_fifo = os.path.join(self.project, SESSION_ID + ".jsonl")
        link_to_file = os.path.join(self.project, "1" + SESSION_ID[1:] + ".jsonl")
        try:
            os.symlink(target, link_to_fifo)
            os.symlink(regular, link_to_file)
        except (OSError, NotImplementedError):
            self.skipTest("cannot make symlinks")
        units, problems = self.scan_of(ClaudeReader(self.root))
        self.assertEqual(([link_to_file], _not_regular(Harness.CLAUDE, link_to_fifo)), ([unit.key for unit in units], problems))

    def test_a_file_that_became_a_fifo_after_the_scan_is_listed_unreadable_when_read(self):
        path = os.path.join(self.project, SESSION_ID + ".jsonl")
        _copy(claude_tests.FIXTURE, path)
        self.assertEqual(_not_regular(Harness.CLAUDE, path), self.read_after_swap(ClaudeReader(self.root), path))


@NEEDS_FIFO
class CodexFifoTest(FifoTestCase):
    def setUp(self):
        super().setUp()
        self.path = os.path.join(self.root, "sessions", codex_tests.DAY_FOLDER, codex_tests.ROOT_NAME)

    def test_a_fifo_rollout_is_listed_unreadable_not_a_regular_file(self):
        self.fifo(self.path)
        self.assertEqual(([], _not_regular(Harness.CODEX, self.path)), self.scan_of(CodexReader(self.root)))

    def test_a_fifo_in_archived_sessions_is_listed_unreadable_too(self):
        archived = self.fifo(os.path.join(self.root, "archived_sessions", codex_tests.ROOT_NAME))
        self.assertEqual(([], _not_regular(Harness.CODEX, archived)), self.scan_of(CodexReader(self.root)))

    def test_a_file_that_became_a_fifo_after_the_scan_is_listed_unreadable_when_read(self):
        _copy(codex_tests.ROOT_FIXTURE, self.path)
        self.assertEqual(_not_regular(Harness.CODEX, self.path), self.read_after_swap(CodexReader(self.root), self.path))

    def test_a_fifo_beside_a_regular_compressed_rollout_does_not_hide_it_and_is_never_opened_by_the_read(self):
        self.fifo(self.path)
        with open(codex_tests.ROOT_FIXTURE, encoding="utf-8") as handle:
            write_compressed(self.path + ".zst", handle.read())
        reader = CodexReader(self.root)
        units, problems = self.scan_of(reader)
        self.assertEqual(([self.path], []), ([unit.key for unit in units], problems))
        session_key = within_the_watchdog(lambda: reader.session_of(units[0]))
        result = within_the_watchdog(lambda: reader.read(session_key, tuple(units)))
        self.assertEqual(((), True), (result.unreadable, result.session is not None))


@NEEDS_FIFO
class OmpFifoTest(FifoTestCase):
    def setUp(self):
        super().setUp()
        self.plain = os.path.join(self.root, "sessions", omp_records.PROJECT, omp_records.FIXTURE_ROOT_STEM + ".jsonl")

    def test_plain_and_archived_fifos_are_listed_unreadable_not_a_regular_file(self):
        self.fifo(self.plain)
        archived = self.fifo(os.path.join(self.root, "archive", "sessions", omp_records.PROJECT, omp_records.root_of(2)[0] + ".jsonl.gz"))
        self.assertEqual(([], _not_regular(Harness.OMP, *sorted((self.plain, archived)))), self.scan_of(OmpReader(self.root)))

    def test_a_file_that_became_a_fifo_after_the_scan_is_listed_unreadable_when_read(self):
        _copy(omp_records.ROOT_FIXTURE, self.plain)
        self.assertEqual(_not_regular(Harness.OMP, self.plain), self.read_after_swap(OmpReader(self.root), self.plain))

    def test_an_archived_file_that_became_a_fifo_after_the_scan_is_listed_unreadable_when_read(self):
        archived = os.path.join(self.root, "archive", "sessions", omp_records.PROJECT, omp_records.FIXTURE_ROOT_STEM + ".jsonl.gz")
        os.makedirs(os.path.dirname(archived))
        with open(omp_records.ROOT_FIXTURE, encoding="utf-8") as handle:
            write_gzip(archived, handle.read())
        self.assertEqual(_not_regular(Harness.OMP, archived), self.read_after_swap(OmpReader(self.root), archived))


@NEEDS_FIFO
class CursorFifoTest(FifoTestCase):
    def setUp(self):
        super().setUp()
        self.records = os.path.join(self.root, ".records")
        self.reader = CursorReader(self.records)

    def test_a_record_fifo_is_listed_unreadable_and_the_regular_records_stay_a_unit(self):
        write_record(self.records, "beforeSubmitPrompt", 0, prompt_input("g1", "kept"))
        fifo = self.fifo(os.path.join(self.records, CONVERSATION, "%d-stop-1.json" % at_ns(1)))
        units, problems = self.scan_of(self.reader)
        self.assertEqual((1, _not_regular(Harness.CURSOR, fifo)), (len(units), problems))

    def test_a_conversation_holding_only_a_fifo_is_no_unit_and_lists_the_fifo(self):
        fifo = self.fifo(os.path.join(self.records, CONVERSATION, "%d-stop-1.json" % at_ns(1)))
        self.assertEqual(([], _not_regular(Harness.CURSOR, fifo)), self.scan_of(self.reader))

    def test_the_invalid_folder_lists_its_fifo_besides_its_call_count(self):
        write_record(self.records, "stop", 0, {"conversation_id": ""}, conversation=".invalid")
        fifo = self.fifo(os.path.join(self.records, ".invalid", "%d-stop-1.json" % at_ns(1)))
        units, problems = self.scan_of(self.reader)
        self.assertEqual(([], 2, fifo), (units, len(problems), next(p.path for p in problems if p.reason == NOT_A_REGULAR_FILE_REASON)))

    def test_a_record_that_became_a_fifo_between_the_listing_and_the_open_is_listed_unreadable_when_read(self):
        path = write_record(self.records, "beforeSubmitPrompt", 0, prompt_input("g1"))
        (unit,), _ = self.scan_of(self.reader)
        with mock.patch("sessionlog.cursor_reader.open_text", side_effect=NotARegularFileError(path)):
            result = self.reader.read(self.reader.session_of(unit), (unit,))
        self.assertEqual(_not_regular(Harness.CURSOR, path), list(result.unreadable))

    def test_read_beside_a_listed_fifo_never_opens_it_and_keeps_the_regular_records(self):
        write_record(self.records, "beforeSubmitPrompt", 0, prompt_input("g1", "kept"))
        self.fifo(os.path.join(self.records, CONVERSATION, "%d-stop-1.json" % at_ns(1)))
        (unit,), _ = self.scan_of(self.reader)
        result = within_the_watchdog(lambda: self.reader.read(self.reader.session_of(unit), (unit,)))
        self.assertEqual(((), "kept"), (result.unreadable, result.session.prompts[0].text))

    def test_a_dangling_link_named_like_a_record_is_ignored_as_for_the_other_readers(self):
        write_record(self.records, "beforeSubmitPrompt", 0, prompt_input("g1"))
        link = os.path.join(self.records, CONVERSATION, "%d-stop-1.json" % at_ns(1))
        try:
            os.symlink(os.path.join(self.root, "nowhere"), link)
        except (OSError, NotImplementedError):
            self.skipTest("cannot make symlinks")
        units, problems = self.scan_of(self.reader)
        self.assertEqual((1, []), (len(units), problems))

    def test_hook_activity_reads_names_only_and_a_fifo_is_no_activity(self):
        self.fifo(os.path.join(self.records, CONVERSATION, "%d-stop-1.json" % at_ns(5)))
        self.assertIs(False, within_the_watchdog(lambda: CursorHookActivity(self.records).recorded_since(moment(0))))


@NEEDS_FIFO
class OpenCodeFifoTest(FifoTestCase):
    def test_a_fifo_in_the_place_of_the_database_is_listed_unreadable_and_never_opened_by_scan_or_read(self):
        path = self.fifo(os.path.join(self.root, "opencode.db"))
        reader = OpenCodeReader(path)
        self.assertEqual(([], _not_regular(Harness.OPENCODE, path)), self.scan_of(reader))
        unit = SourceUnit(key="ses_1", fingerprint="0:0:0", modified_at=moment(0))
        self.assertIsNone(within_the_watchdog(lambda: reader.session_of(unit)))
        key = build_session_key(Harness.OPENCODE, "ses_1")
        result = within_the_watchdog(lambda: reader.read(key, (unit,)))
        self.assertEqual((None, tuple(_not_regular(Harness.OPENCODE, "ses_1"))), (result.session, result.unreadable))


@NEEDS_FIFO
class OpenTextTest(FifoTestCase):
    def test_a_fifo_is_refused_without_waiting_for_a_writer(self):
        path = self.fifo(os.path.join(self.root, "a.jsonl"))
        with self.assertRaises(NotARegularFileError):
            within_the_watchdog(lambda: open_text(path))

    def test_the_descriptor_of_a_refused_fifo_is_closed(self):
        path = self.fifo(os.path.join(self.root, "a.jsonl"))
        with mock.patch("sessionlog.store_files.os.close", wraps=os.close) as close:
            with self.assertRaises(NotARegularFileError):
                within_the_watchdog(lambda: open_text(path))
        self.assertEqual(1, close.call_count)

    def test_a_socket_is_refused_as_not_a_regular_file(self):
        path = os.path.join(self.root, "s.sock")
        with socket.socket(socket.AF_UNIX) as server:
            try:
                server.bind(path)
            except OSError:
                self.skipTest("cannot bind a unix socket here")
            with self.assertRaises(NotARegularFileError):
                within_the_watchdog(lambda: open_text(path))

    def test_a_regular_file_is_read_with_its_byte_order_mark_dropped_and_a_link_to_it_too(self):
        path = os.path.join(self.root, "a.jsonl")
        with open(path, "wb") as handle:
            handle.write(b"\xef\xbb\xbfline\r\n")
        link = os.path.join(self.root, "link.jsonl")
        try:
            os.symlink(path, link)
        except (OSError, NotImplementedError):
            self.skipTest("cannot make symlinks")
        for each in (path, link):
            with open_text(each) as handle:
                self.assertEqual("line\n", handle.read())


@NEEDS_FIFO
class SyncFifoTest(SyncTestCase):
    def test_sync_with_a_fifo_source_never_hangs_archives_the_regular_sessions_and_names_the_fifo_in_every_run(self):
        self._install_fixture("fixture")
        fifo = os.path.join(self.folder, "fifo.jsonl")
        os.mkfifo(fifo)
        for _ in range(2):  # the FIFO is retried, not remembered
            outcome = within_the_watchdog(self._sync)
            self.assertEqual(tuple(_not_regular(Harness.CLAUDE, fifo)), outcome.scan_problems)
        self.assertEqual(["/logs/fixture.md"], sorted({log_path for log_path, _, _ in self.archive.writes}))
        self.assertEqual([build_session_key(Harness.CLAUDE, "fixture")], sorted(self.state.saved["sessions"]))


if __name__ == "__main__":
    unittest.main()
