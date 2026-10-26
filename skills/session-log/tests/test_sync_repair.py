"""Rewrites a sync owes to what is on disk or in the price file, not to the source: a log or report deleted from disk is
recreated from the source, `usage` serves a live report while the saved one is gone, and a report written while `prices.json`
was unusable is rewritten once the file works. Real `FileStateStore` and real `LogUsageWriter` throughout."""
import os

from sessionlog.archive_port import ArchiveFiles
from sessionlog.command_error import CommandError
from sessionlog.commands import FILES_MISSING_REASON, UnreadableItem
from sessionlog.log_usage_writer import LogUsageWriter
from sessionlog.state import FileStateStore
from sessionlog.state_port import OnFlag
from sessionlog.sync import FEWER_PROMPTS_REASON, RENDER_VERSION
from tests.test_render_version import RATES, RenderVersionTestCase, _PricedModels
from tests.test_sync import NOW, _transcript
from tests.test_sync_safety import BASE, CWD, MINUTE, SafetyTestCase, _read_bytes, _report_path

UNKNOWN_PRICE = "price: unknown"
FILE_KINDS = (("log", lambda log_path: (log_path,)), ("report", lambda log_path: (_report_path(log_path),)),
              ("both", lambda log_path: (log_path, _report_path(log_path))))


def _prompt_times(count):
    return tuple(BASE + number * MINUTE for number in range(count))


class FilesPresentTest(SafetyTestCase):
    def test_the_writer_tells_which_of_the_two_files_are_on_disk(self):
        self._install("s1", _transcript(BASE), NOW)
        self._sync()
        log_path = self._entry("s1")["logPath"]
        writer = LogUsageWriter(self.logs, lambda moment: moment)
        self.assertEqual(writer.files_present(log_path), ArchiveFiles(True, True))
        os.unlink(_report_path(log_path))
        self.assertEqual(writer.files_present(log_path), ArchiveFiles(True, False))
        os.unlink(log_path)
        self.assertEqual(writer.files_present(log_path), ArchiveFiles(False, False))
        self.assertFalse(writer.files_present(log_path).complete)

    def test_a_folder_that_cannot_be_examined_does_not_make_the_files_look_gone(self):
        folder = os.path.join(self.logs, "closed")
        os.makedirs(folder)
        path = os.path.join(folder, "session_x.md")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("log")
        os.chmod(folder, 0)
        self.addCleanup(os.chmod, folder, 0o700)
        if os.access(folder, os.X_OK):
            self.skipTest("this account can examine any folder")
        self.assertTrue(LogUsageWriter(self.logs, lambda moment: moment).files_present(path).log)


class DeletedFilesTest(SafetyTestCase):
    def _archive(self):
        path = self._install("s1", _transcript(*_prompt_times(2)), NOW)
        self._sync()
        log_path = self._entry("s1")["logPath"]
        return path, log_path, self._archived()

    def test_deleted_log_or_report_is_recreated_from_the_source_byte_for_byte(self):
        _, log_path, archived = self._archive()
        for kind, doomed in FILE_KINDS:
            with self.subTest(kind=kind):
                for path in doomed(log_path):
                    os.unlink(path)
                self.reader.read_keys.clear()
                self._sync()
                self.assertEqual((self._archived(), self.reader.read_keys), (archived, [self._key("s1")]))
                self.assertEqual(self._commands().status().unreadable, ())
                self.reader.read_keys.clear()
                self._sync()
                self.assertEqual(self.reader.read_keys, [])  # whole again: no read

    def test_files_deleted_after_a_later_on_time_made_the_session_old_are_recreated_too(self):
        _, log_path, archived = self._archive()
        os.unlink(log_path)
        os.unlink(_report_path(log_path))
        os.unlink(os.path.join(self.logs, ".log_enabled"))  # off, then on again after every activity of the session
        FileStateStore(self.logs).write_on_flag(OnFlag(BASE + 10 * MINUTE))
        self._sync()
        self.assertEqual(self._archived(), archived)

    def test_status_lists_a_session_whose_files_and_source_are_both_gone_and_usage_names_it(self):
        path, log_path, _ = self._archive()
        os.unlink(path)
        os.unlink(log_path)
        os.unlink(_report_path(log_path))
        self._sync()  # nothing to rebuild from, and no crash
        commands = self._commands()
        self.assertEqual(commands.status().unreadable, (UnreadableItem(log_path, FILES_MISSING_REASON),))
        with self.assertRaisesRegex(CommandError, "the saved report of session s1 beside %s cannot be read, and its source is gone" % log_path):
            self._commands().usage_of("s1")
        with self.assertRaisesRegex(CommandError, "its source is gone"):
            self._commands().usage_latest(CWD)

    def test_usage_serves_a_live_report_while_logging_is_off_and_the_saved_one_is_gone(self):
        _, log_path, _ = self._archive()
        saved = _read_bytes(_report_path(log_path)).decode("utf-8")
        os.unlink(_report_path(log_path))
        os.unlink(os.path.join(self.logs, ".log_enabled"))
        self.assertEqual(self._commands().usage_of("s1").report_text, saved)
        self.assertEqual(self._commands().usage_latest(CWD).report_text, saved)
        self.assertFalse(os.path.exists(_report_path(log_path)))  # a live report is written nowhere

    def test_both_files_deleted_and_the_source_cut_are_written_from_what_the_source_has(self):
        """Guard, green on HEAD by design: with both files gone nothing is protected, so the new prompt-count rule must not withhold."""
        path, log_path, _ = self._archive()
        os.unlink(log_path)
        os.unlink(_report_path(log_path))
        self._install("s1", _transcript(BASE), NOW)
        self._sync()
        self.assertTrue(os.path.exists(log_path) and os.path.exists(_report_path(log_path)))
        self.assertNotIn("prompt 1", _read_bytes(log_path).decode("utf-8"))
        self.assertEqual(self._entry("s1")["state"], "written")

    def test_a_missing_log_beside_a_report_that_holds_more_prompts_than_the_cut_source_is_not_rebuilt(self):
        _, log_path, _ = self._archive()
        report = _read_bytes(_report_path(log_path))
        os.unlink(log_path)
        self._install("s1", _transcript(BASE), NOW)
        self._sync()
        self.assertEqual((os.path.exists(log_path), _read_bytes(_report_path(log_path))), (False, report))
        reason = FEWER_PROMPTS_REASON % (1, 2)
        self.assertEqual((self._entry("s1")["state"], self._entry("s1")["reason"]), ("withheld", reason))
        self.assertEqual(self._commands().status().unreadable, (UnreadableItem(log_path, reason),))


class _FlakyPrices(_PricedModels):
    """A price book whose file is unusable while `broken`: no rates and a problem, like a `PriceFile` that did not load."""

    broken = False

    def rate_for(self, model_name, fast, prompt_tokens):
        return None if self.broken else RATES

    def problem(self):
        return "prices.json: broken" if self.broken else None


class UnusablePriceFileTest(RenderVersionTestCase):
    def setUp(self):
        super().setUp()
        self.prices = _FlakyPrices()

    def _report(self):
        return self._text_of(self._archived(), "s1", ".usage.txt")

    def test_a_report_written_while_the_price_file_was_unusable_is_rewritten_once_the_file_works(self):
        self._install("s1", _transcript(BASE), NOW)
        self.prices.broken = True
        self._sync_as(RENDER_VERSION)
        self.assertIn(UNKNOWN_PRICE, self._report())
        archive = self._sync_as(RENDER_VERSION)  # still unusable: nothing to improve, so nothing is read or written
        self.assertEqual((archive.written, self.reader.read_keys), ([], []))
        self.prices.broken = False
        archive = self._sync_as(RENDER_VERSION)
        self.assertNotIn(UNKNOWN_PRICE, self._report())
        self.assertEqual((archive.written, self.reader.read_keys), ([self._entry("s1")["logPath"]], self._session_keys("s1")))
        self.assertNotIn("priceFileProblem", self._entry("s1"))
        archive = self._sync_as(RENDER_VERSION)
        self.assertEqual((archive.written, self.reader.read_keys), ([], []))

    def test_a_session_with_new_turns_while_the_file_is_unusable_is_still_repaired_once_the_file_works(self):
        self._install("s1", _transcript(BASE), NOW)
        self.prices.broken = True
        self._sync_as(RENDER_VERSION)
        self._install("s1", _transcript(BASE, BASE + MINUTE), NOW)
        self._sync_as(RENDER_VERSION)
        self.assertIn("prompt 1", self._text_of(self._archived(), "s1", ".md"))
        self.prices.broken = False
        self._sync_as(RENDER_VERSION)
        self.assertNotIn(UNKNOWN_PRICE, self._report())
