"""`.enabled` -> `.log_enabled` migration (BE-21; S32, S49): every command and the Claude hook convert an old release's flag after
the version and home checks and before the `.log_enabled` check, so the run that converts it proceeds as on. These run
`session_log.main` over a sandbox home with the real state store, reader and writer; the store's own cases are in test_state.py."""
import errno
import io
import os
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import session_log
from sessionlog.state import FileStateStore
from tests.test_session_log import FIXED_NOW, LOG_SUFFIX, ON_FLAG, RELATIVE_HOME, SYNC_LOCK, SYNC_STATE, SandboxCase

OLD_FLAG = ".enabled"
STATUS = ["claude", "status"]
ON = ["claude", "on"]
OFF = ["claude", "off"]
USAGE = ["claude", "usage"]
HOOK = ["hook", "claude", "SessionStart"]
HOOK_KEY = ("claude", "SessionStart")
SESSION_ID = "abc123"
FIXED_NOW_TEXT = "2000-01-01 00:00:00 +0000"
EARLIER = datetime(1999, 12, 31, 12, tzinfo=timezone.utc)
EARLIER_TEXT = "1999-12-31 12:00:00 +0000"
PYTHON_3_13 = (3, 13, 7)
THREAD_WAIT_SECONDS = 30
THREAD_JOIN_SECONDS = 3 * THREAD_WAIT_SECONDS
SECOND_HOOK_LATER_BY = timedelta(minutes=5)
SILENT_SUCCESS = (0, "", "")


class EnabledMigrationCase(SandboxCase):
    def start_over(self):
        """A new empty sandbox home for the next case of a loop (each sandbox registers its own cleanup)."""
        self.setUp()

    def seed(self, name, text):
        os.makedirs(self.log_root, exist_ok=True)
        with open(os.path.join(self.log_root, name), "w", encoding="utf-8") as stream:
            stream.write(text)

    def seed_old_flag(self):
        self.seed(OLD_FLAG, "old release flag\n")

    def log_root_has(self, name):
        return os.path.lexists(os.path.join(self.log_root, name))

    def read_log_root_file(self, name):
        with open(os.path.join(self.log_root, name), encoding="utf-8") as stream:
            return stream.read()

    def archived_logs(self):
        return [name for _, _, names in os.walk(self.log_root) for name in names if name.endswith(LOG_SUFFIX)]

    def state_files(self):
        """What the log folder holds beside the session folders: the flags, the sync state and its lock, and no temp file."""
        return sorted(name for name in os.listdir(self.log_root) if name.startswith("."))


class OldFlagConvertedByEveryEntryTests(EnabledMigrationCase):
    # (arguments, whether what the run prints is right, whether logging is still on afterwards)
    RUNS = (
        (STATUS, lambda text: text.startswith("Session logging is on since %s.\n" % FIXED_NOW_TEXT), True),
        (ON, lambda text: text.startswith("Session logging is already on, since %s.\n" % FIXED_NOW_TEXT), True),
        (OFF, lambda text: text.startswith("Session logging is off.\n"), False),
        (USAGE, lambda text: SESSION_ID in text, True),
        (HOOK, lambda text: text == "", True),
    )

    def test_old_enabled_converted_before_on_check_same_run_proceeds_as_on(self):
        for arguments, prints_what_is_right, stays_on in self.RUNS:
            with self.subTest(arguments=arguments):
                self.start_over()
                self.write_transcript(session=SESSION_ID)  # a prompt after the migration's on-time: a run that is on archives it
                self.seed_old_flag()
                code, stdout, stderr = self.run_main(arguments)
                self.assertEqual((0, ""), (code, stderr))
                self.assertTrue(prints_what_is_right(stdout), stdout)
                self.assertFalse(self.log_root_has(OLD_FLAG))  # old hooks now see logging off
                self.assertEqual(stays_on, self.log_root_has(ON_FLAG))
                if stays_on:
                    flag = FileStateStore(self.log_root).read_on_flag()
                    self.assertEqual((FIXED_NOW, False), (flag.on_at, flag.from_file_time))  # on-time = now of the converting run
                self.assertEqual(1, len(self.archived_logs()))  # the same run synced, so it was on at its on-check

    def test_nothing_to_convert_writes_nothing_while_logging_is_off(self):
        for arguments in (STATUS, OFF, HOOK):
            with self.subTest(arguments=arguments):
                self.start_over()
                self.run_main(arguments)
                self.assertEqual([], self.home_entries())

    def test_nothing_to_convert_leaves_an_existing_on_flag_untouched(self):
        for arguments in (STATUS, HOOK):
            with self.subTest(arguments=arguments):
                self.start_over()
                self.seed(ON_FLAG, EARLIER.isoformat() + "\n")
                flag_path = os.path.join(self.log_root, ON_FLAG)
                os.utime(flag_path, (EARLIER.timestamp(), EARLIER.timestamp()))  # a rewrite would move the file time
                self.run_main(arguments)
                self.assertEqual((EARLIER.isoformat() + "\n", EARLIER.timestamp()),
                                 (self.read_log_root_file(ON_FLAG), os.stat(flag_path).st_mtime))


class FailedMigrationTests(EnabledMigrationCase):
    """A conversion that fails fails the run (a command says so, a hook is silent as always) instead of going on: an `.enabled` that
    cannot be removed would keep the old release's hooks logging beside the new sync, and one whose `.log_enabled` cannot be
    created stays, so logging is never switched off. `.log_enabled` is created first and the next run retries the removal."""

    def refuse_removal(self):
        def refuse(path, missing_ok=False):
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(path))

        patcher = mock.patch.object(Path, "unlink", autospec=True, side_effect=refuse)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_command_fails_with_a_session_log_line_naming_the_file_and_the_hook_stays_silent(self):
        self.refuse_removal()
        for arguments in (STATUS, USAGE, ON, OFF, HOOK):
            with self.subTest(arguments=arguments):
                self.start_over()
                self.write_transcript(session=SESSION_ID)
                self.seed_old_flag()
                code, stdout, stderr = self.run_main(arguments)
                if arguments == HOOK:
                    self.assertEqual(SILENT_SUCCESS, (code, stdout, stderr))
                else:
                    self.assertEqual((1, ""), (code, stdout))
                    self.assertTrue(stderr.startswith("session-log: "), stderr)
                    self.assertIn(os.path.join(self.log_root, OLD_FLAG), stderr)
                self.assertTrue(self.log_root_has(OLD_FLAG))
                self.assertTrue(self.log_root_has(ON_FLAG))
                self.assertEqual([], self.archived_logs())  # the run did not get as far as a sync

    def test_a_log_enabled_that_cannot_be_created_fails_the_run_and_keeps_enabled(self):
        self.write_transcript(session=SESSION_ID)
        self.seed_old_flag()
        denied = PermissionError(errno.EACCES, os.strerror(errno.EACCES), os.path.join(self.log_root, ON_FLAG))
        with mock.patch.object(FileStateStore, "write_on_flag", side_effect=denied):
            code, stdout, stderr = self.run_main(STATUS)
            hook = self.run_main(HOOK)
        self.assertEqual((1, ""), (code, stdout))
        self.assertTrue(stderr.startswith("session-log: "), stderr)
        self.assertIn(os.path.join(self.log_root, ON_FLAG), stderr)
        self.assertEqual(SILENT_SUCCESS, hook)
        self.assertTrue(self.log_root_has(OLD_FLAG))
        self.assertFalse(self.log_root_has(ON_FLAG))
        self.assertEqual([], self.archived_logs())


class TwoHooksAtOnceTests(EnabledMigrationCase):
    def test_two_hooks_at_once_create_one_log_enabled_on_time_kept_neither_fails(self):
        self.write_transcript(session=SESSION_ID)
        self.seed_old_flag()
        first_time = FIXED_NOW
        second_time = FIXED_NOW + SECOND_HOOK_LATER_BY
        both_saw_old_flag = threading.Barrier(2)
        first_done = threading.Event()
        failures = []
        results = {}

        def clock(moment, waits_for_first):
            """A converting run asks for the time after it saw `.enabled`: both runs have seen it before the first one creates
            `.log_enabled`, and the second one goes on only after the first one has finished, so it finds `.log_enabled`
            created and `.enabled` already gone."""
            asked = []

            def now():
                if not asked:
                    asked.append(True)
                    both_saw_old_flag.wait(timeout=THREAD_WAIT_SECONDS)
                    if waits_for_first and not first_done.wait(timeout=THREAD_WAIT_SECONDS):
                        failures.append(TimeoutError("the first hook never finished"))
                return moment

            return now

        original = session_log.HOOK_HANDLERS[HOOK_KEY]

        def recording(harness, environment):
            try:
                original(harness, environment)
            except BaseException as error:  # the hook entry swallows everything; this is how a failure shows
                failures.append(error)
                raise

        def run(name, moment, waits_for_first):
            stdout, stderr = io.StringIO(), io.StringIO()
            try:
                code = session_log.main(HOOK, self.environment(stdout, stderr, now=clock(moment, waits_for_first)))
                results[name] = (code, stdout.getvalue(), stderr.getvalue())
            finally:
                if name == "first":
                    first_done.set()

        with mock.patch.dict(session_log.HOOK_HANDLERS, {HOOK_KEY: recording}), \
                mock.patch.object(FileStateStore, "write_on_flag", autospec=True, side_effect=FileStateStore.write_on_flag) as create:
            threads = [threading.Thread(target=run, args=("first", first_time, False)),
                       threading.Thread(target=run, args=("second", second_time, True))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=THREAD_JOIN_SECONDS)
                self.assertFalse(thread.is_alive())

        self.assertEqual([], failures)
        self.assertEqual({"first": SILENT_SUCCESS, "second": SILENT_SUCCESS}, results)
        self.assertEqual(2, create.call_count)  # both runs saw `.enabled` and tried to create the flag: the second hit the first's
        self.assertFalse(self.log_root_has(OLD_FLAG))
        self.assertEqual(sorted([ON_FLAG, SYNC_LOCK, SYNC_STATE]), self.state_files())  # one flag, no stray temp
        self.assertEqual(first_time.isoformat() + "\n", self.read_log_root_file(ON_FLAG))  # the second run did not overwrite it
        self.assertEqual(1, len(self.archived_logs()))


class BothFlagsTests(EnabledMigrationCase):
    # (arguments, whether logging is still on afterwards)
    RUNS = ((STATUS, True), (ON, True), (OFF, False), (USAGE, True), (HOOK, True))

    def check_log_enabled_wins(self, flag_text, flag_time_text):
        for arguments, stays_on in self.RUNS:
            with self.subTest(arguments=arguments):
                self.start_over()
                self.write_transcript(session=SESSION_ID)
                self.seed(ON_FLAG, flag_text)
                os.utime(os.path.join(self.log_root, ON_FLAG), (EARLIER.timestamp(), EARLIER.timestamp()))
                self.seed_old_flag()
                code, stdout, stderr = self.run_main(arguments)
                self.assertEqual((0, ""), (code, stderr))
                self.assertFalse(self.log_root_has(OLD_FLAG))
                self.assertEqual(stays_on, self.log_root_has(ON_FLAG))
                if stays_on:
                    self.assertEqual(flag_text, self.read_log_root_file(ON_FLAG))  # not replaced by the time of this run
                if arguments in (STATUS, ON):
                    self.assertIn("since %s" % flag_time_text, stdout)
                self.assertEqual(1, len(self.archived_logs()))  # the run was on, so it synced

    def test_both_flags_present_log_enabled_wins_enabled_removed(self):
        self.check_log_enabled_wins(EARLIER.isoformat() + "\n", EARLIER_TEXT)

    def test_unparsable_log_enabled_wins_too_and_its_file_time_is_the_on_time(self):
        self.check_log_enabled_wins("broken", EARLIER_TEXT)
        self.start_over()
        self.seed(ON_FLAG, "broken")
        self.seed_old_flag()
        self.assertIn("(from the file time: .log_enabled could not be parsed)", self.run_main(STATUS)[1])


class EntryOrderTests(EnabledMigrationCase):
    """The migration comes after the checks that refuse a run (Python version, home folder): such a run writes nothing."""

    def test_old_python_leaves_enabled_in_place_for_a_command_and_a_hook(self):
        self.seed_old_flag()
        code, stdout, stderr = self.run_main(STATUS, version_info=PYTHON_3_13, platform="linux")
        self.assertEqual((1, ""), (code, stdout))
        self.assertTrue(stderr.startswith("session-log: Python 3.14+ required (found 3.13)\n"), stderr)
        self.assertEqual(SILENT_SUCCESS, self.run_main(HOOK, version_info=PYTHON_3_13, platform="linux"))
        self.assertTrue(self.log_root_has(OLD_FLAG))
        self.assertFalse(self.log_root_has(ON_FLAG))

    def test_relative_home_leaves_enabled_in_place_for_a_command_and_a_hook(self):
        self.claude = os.path.join(self.home, "relative", "home", ".claude")
        self.log_root = os.path.join(self.claude, "prompt-logs")
        self.seed_old_flag()
        previous = os.getcwd()
        os.chdir(self.home)  # where a relative home `relative/home` would be found
        self.addCleanup(os.chdir, previous)
        self.assertEqual((1, "", "session-log: cannot determine the home folder\n"), self.run_main(STATUS, home=RELATIVE_HOME))
        self.assertEqual(SILENT_SUCCESS, self.run_main(HOOK, home=RELATIVE_HOME))
        self.assertTrue(self.log_root_has(OLD_FLAG))
        self.assertFalse(self.log_root_has(ON_FLAG))


if __name__ == "__main__":
    unittest.main()
