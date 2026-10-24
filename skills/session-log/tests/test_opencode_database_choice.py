"""S39 end to end through `session_log.py opencode ...`: which OpenCode database a run reads (`OPENCODE_DB`, `OPENCODE_CHANNEL`,
`opencode.db`, the newest `opencode-*.db`), a database that is missing, busy or v1-only (listed unreadable with its reason, never an error,
read again by the next sync), and a relocated `XDG_DATA_HOME` (exit 1 naming the root). Every database is built here from text; the real
OpenCode data and the real environment are never touched."""
import os
import re
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

import session_log
from sessionlog import opencode_database, opencode_reader
from sessionlog.opencode_reader import (CANNOT_BE_OPENED_REASON, DATABASE_BUSY_REASON, NO_DATABASE_REASON, UNSUPPORTED_DATABASE_REASON)
from tests import test_opencode_entry as entry_tests
from tests.opencode_db import ROOT_ID, V1_SCHEMA, OpenCodeDatabase, folder_snapshot

OLD_SECONDS, NEW_SECONDS = 1_000_000, 2_000_000
IN_MEMORY_TEXT = "session-log: OpenCode in-memory databases are unsupported\n"
OUTSIDE_PREFIX = "session-log: OpenCode database path is not inside the OpenCode data folder: "


class DatabaseChoiceTestCase(entry_tests.OpenCodeEntryTestCase):
    starts_with_database = False

    def setUp(self):
        super().setUp()
        # The home is spelled through a link, so a spelling that differs from the real path (as /var does on macOS) is tested everywhere.
        holder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, holder, True)
        os.symlink(self.home, os.path.join(holder, "home"))
        self.home = os.path.join(holder, "home")
        self.data = os.path.join(self.home, ".local", "share", "opencode")
        self.log_root = os.path.join(self.home, ".config", "opencode", "prompt-logs")
        self.database_path = os.path.join(self.data, "opencode.db")
        self.default_data_home = os.path.join(self.home, ".local", "share")

    def reset(self):
        """Back to a home without any database or archive, between the cases of one test."""
        shutil.rmtree(self.data, ignore_errors=True)
        shutil.rmtree(os.path.join(self.home, ".config"), ignore_errors=True)

    def add_database(self, name, tag, modified=None):
        """A database `name` in the data folder holding one session `ses_<tag>` with a prompt and a reply."""
        database = OpenCodeDatabase(os.path.join(self.data, name))
        session_id = "ses_" + tag
        database.add_session(session_id, created=10, updated=60)
        database.add_user(session_id, 1, 10, "ask " + tag)
        database.add_assistant(session_id, 2, 11, text="reply " + tag, tokens=(1, 1), done=1)
        if modified is not None:
            os.utime(database.path, (modified, modified))
        return database

    def archived_session_ids(self):
        """The ids of the sessions archived so far, from the names of their logs."""
        return sorted(re.search(r"(ses_[A-Za-z0-9]+)\.md$", os.path.basename(path)).group(1) for path in self.archived_files(".md"))

    def run_ok(self, argv, env=None):
        code, stdout, stderr = self.run_main(argv, env=env)
        self.assertEqual((0, ""), (code, stderr), argv)
        return stdout

    def usage_of_unknown_session(self, env=None):
        """What `usage ses_any` says: it needs the whole store, so it names a database that could not be read."""
        code, stdout, stderr = self.run_main(["opencode", "usage", "ses_any"], env=env)
        self.assertEqual((1, ""), (code, stdout))
        return stderr


class DatabaseChoiceTest(DatabaseChoiceTestCase):
    def test_database_chosen_from_env_channel_or_newest_opencode_star_db_memory_exits_1(self):
        def with_default_and(name, tag):
            def prepare(test):
                test.add_database("opencode.db", "default")
                test.add_database(name, tag)
            return prepare

        def newest_of_two(test):  # the newest sorts last, so "the first by name" would be wrong
            test.add_database("opencode-a.db", "a", modified=OLD_SECONDS)
            test.add_database("opencode-b.db", "b", modified=NEW_SECONDS)

        def tied(test):
            test.add_database("opencode-b.db", "b", modified=NEW_SECONDS)
            test.add_database("opencode-a.db", "a", modified=NEW_SECONDS)

        def newest_is_first_by_name(test):  # the newest by time is not the last created or the last by name
            test.add_database("opencode-b.db", "b", modified=OLD_SECONDS)
            test.add_database("opencode-a.db", "a", modified=NEW_SECONDS)

        def newest_regular_database_only(test):
            newest_of_two(test)
            outside = test.add_database("elsewhere.db", "linked", modified=NEW_SECONDS + 1_000)
            os.symlink(outside.path, os.path.join(test.data, "opencode-link.db"))  # a link is never chosen, however new
            os.mkdir(os.path.join(test.data, "opencode-folder.db"))
            for name in ("opencode-folder.db", "opencode-c.db-wal", "opencode-c.db-shm", "opencode-c.db-journal"):  # newer, but no database file
                path = os.path.join(test.data, name)
                if not os.path.exists(path):
                    open(path, "wb").close()
                os.utime(path, (NEW_SECONDS + 1_000, NEW_SECONDS + 1_000))

        def default_wins_over_a_newer_channel_file(test):
            test.add_database("opencode.db", "default", modified=OLD_SECONDS)
            test.add_database("opencode-b.db", "b", modified=NEW_SECONDS)

        cases = [
            ("OPENCODE_DB relative name", with_default_and("custom.db", "custom"), {"OPENCODE_DB": "custom.db"}, "ses_custom"),
            ("OPENCODE_DB relative name in a sub-folder", with_default_and(os.path.join("sub", "custom.db"), "deep"),
             {"OPENCODE_DB": os.path.join("sub", "custom.db")}, "ses_deep"),
            ("OPENCODE_DB wins over OPENCODE_CHANNEL", with_default_and("custom.db", "custom"),
             {"OPENCODE_DB": "custom.db", "OPENCODE_CHANNEL": "nightly"}, "ses_custom"),
            ("OPENCODE_CHANNEL=beta is opencode.db", with_default_and("opencode-beta.db", "beta"), {"OPENCODE_CHANNEL": "beta"}, "ses_default"),
            ("OPENCODE_CHANNEL=latest is opencode.db", with_default_and("opencode-latest.db", "latest"), {"OPENCODE_CHANNEL": "latest"},
             "ses_default"),
            ("OPENCODE_CHANNEL=prod is opencode.db", with_default_and("opencode-prod.db", "prod"), {"OPENCODE_CHANNEL": "prod"}, "ses_default"),
            ("OPENCODE_CHANNEL=nightly is opencode-nightly.db", with_default_and("opencode-nightly.db", "nightly"),
             {"OPENCODE_CHANNEL": "nightly"}, "ses_nightly"),
            ("OPENCODE_DISABLE_CHANNEL_DB=1 is opencode.db", with_default_and("opencode-nightly.db", "nightly"),
             {"OPENCODE_CHANNEL": "nightly", "OPENCODE_DISABLE_CHANNEL_DB": "1"}, "ses_default"),
            ("OPENCODE_DISABLE_CHANNEL_DB=true is opencode.db", with_default_and("opencode-nightly.db", "nightly"),
             {"OPENCODE_CHANNEL": "nightly", "OPENCODE_DISABLE_CHANNEL_DB": "true"}, "ses_default"),
            ("OPENCODE_DISABLE_CHANNEL_DB=0 changes nothing", with_default_and("opencode-nightly.db", "nightly"),
             {"OPENCODE_CHANNEL": "nightly", "OPENCODE_DISABLE_CHANNEL_DB": "0"}, "ses_nightly"),
            ("channel characters outside [A-Za-z0-9._ -] become -", with_default_and("opencode-my-chan.db", "mine"),
             {"OPENCODE_CHANNEL": "my/chan"}, "ses_mine"),
            ("channel characters inside [A-Za-z0-9._ -] stay", with_default_and("opencode-My chan.v_1-X.db", "kept"),
             {"OPENCODE_CHANNEL": "My chan.v_1-X"}, "ses_kept"),
            ("no opencode.db: the newest opencode-*.db", newest_of_two, {}, "ses_b"),
            ("no opencode.db: the newest opencode-*.db whatever its name or creation order", newest_is_first_by_name, {}, "ses_a"),
            ("no opencode.db: equally new databases, the first by name", tied, {}, "ses_a"),
            ("the newest regular opencode-*.db, never a link, a folder or a wal file", newest_regular_database_only, {}, "ses_b"),
            ("opencode.db wins over a newer opencode-*.db", default_wins_over_a_newer_channel_file, {}, "ses_default"),
            ("empty variables are unset", default_wins_over_a_newer_channel_file, {"OPENCODE_DB": "", "OPENCODE_CHANNEL": ""}, "ses_default"),
            ("OPENCODE_DB absolute inside the data folder", with_default_and("custom.db", "custom"),
             {"OPENCODE_DB": os.path.join(self.data, "custom.db")}, "ses_custom"),
        ]
        for label, prepare, env, expected in cases:
            with self.subTest(label):
                self.reset()
                prepare(self)
                self.run_ok(["opencode", "on"], env)
                self.assertEqual([expected], self.archived_session_ids())
        with self.subTest("OPENCODE_DB=:memory: exits 1"):
            self.reset()
            self.add_database("opencode.db", "default")
            self.assertEqual((1, "", IN_MEMORY_TEXT), self.run_main(["opencode", "on"], env={"OPENCODE_DB": ":memory:"}))
            self.assertFalse(os.path.exists(os.path.join(self.home, ".config")))

    def test_a_link_inside_the_data_folder_is_followed_a_link_to_a_file_outside_it_is_refused(self):
        self.add_database("custom.db", "custom")
        os.symlink(os.path.join(self.data, "custom.db"), os.path.join(self.data, "inside-link.db"))
        outside = os.path.join(self.home, "elsewhere", "other.db")
        OpenCodeDatabase(outside)
        os.symlink(outside, os.path.join(self.data, "outside-link.db"))
        self.run_ok(["opencode", "on"], {"OPENCODE_DB": "inside-link.db"})
        self.assertEqual(["ses_custom"], self.archived_session_ids())
        os.symlink(outside, os.path.join(self.data, "opencode.db"))  # chosen by default, not by a variable: refused all the same
        for env in ({"OPENCODE_DB": "outside-link.db"}, {}, {"OPENCODE_CHANNEL": "beta"}):
            with self.subTest(env):
                code, stdout, stderr = self.run_main(["opencode", "usage"], env=env)
                self.assertEqual((1, ""), (code, stdout))
                self.assertTrue(stderr.startswith(OUTSIDE_PREFIX) and stderr.count("\n") == 1, stderr)

    def test_a_database_outside_the_data_folder_exits_1_and_nothing_is_written(self):
        self.add_database("opencode.db", "default")
        elsewhere = OpenCodeDatabase(os.path.join(self.home, "elsewhere", "other.db"))
        OpenCodeDatabase(self.data + "-evil" + os.sep + "x.db")  # a sibling folder whose name starts with the data folder's
        escapes = [elsewhere.path, os.path.join("..", "..", "..", "elsewhere", "other.db"), "..", ".", "sub/../../x.db", "../opencode-evil/x.db"]
        for chosen in escapes:
            with self.subTest(chosen):
                code, stdout, stderr = self.run_main(["opencode", "on"], env={"OPENCODE_DB": chosen})
                self.assertEqual((1, ""), (code, stdout))
                self.assertTrue(stderr.startswith(OUTSIDE_PREFIX) and stderr.endswith("\n") and stderr.count("\n") == 1, stderr)
                self.assertFalse(os.path.exists(os.path.join(self.home, ".config")))

    def test_a_link_before_dot_dot_is_judged_by_the_real_file_not_by_its_spelling(self):
        self.add_database("x.db", "inside")
        os.makedirs(os.path.join(self.data, "a", "b"))
        os.symlink(os.path.join(self.data, "a", "b"), os.path.join(self.data, "deep-link"))
        OpenCodeDatabase(os.path.join(self.home, "elsewhere", "x.db"))
        os.symlink(os.path.join(self.home, "elsewhere", "dir"), os.path.join(self.data, "out-link"))
        os.makedirs(os.path.join(self.home, "elsewhere", "dir"))
        self.run_ok(["opencode", "on"], {"OPENCODE_DB": "deep-link/../../x.db"})  # spelled out of the folder, really data/x.db
        self.assertEqual(["ses_inside"], self.archived_session_ids())
        shutil.rmtree(os.path.join(self.home, ".config"))
        code, stdout, stderr = self.run_main(["opencode", "on"], env={"OPENCODE_DB": "out-link/../x.db"})  # spelled data/x.db, really elsewhere
        self.assertEqual((1, ""), (code, stdout))
        self.assertEqual(OUTSIDE_PREFIX + os.path.join(self.data, "out-link/../x.db") + "\n", stderr)  # the path as spelled

    def test_in_memory_database_exits_1_for_every_command_and_nothing_is_written(self):
        self.add_database("opencode.db", "default")
        for argv in (["opencode", "on"], ["opencode", "off"], ["opencode", "status"], ["opencode", "usage"]):
            with self.subTest(argv):
                code, stdout, stderr = self.run_main(argv, env={"OPENCODE_DB": ":memory:"})
                self.assertEqual((1, "", IN_MEMORY_TEXT), (code, stdout, stderr))
                self.assertFalse(os.path.exists(os.path.join(self.home, ".config")))

    def test_a_refused_choice_leaves_the_old_enabled_flag_unconverted(self):
        self.add_database("opencode.db", "default")
        log_root = self.log_root
        for env in ({"OPENCODE_DB": ":memory:"}, {"OPENCODE_DB": ".."}):
            with self.subTest(env):
                os.makedirs(log_root, exist_ok=True)
                with open(os.path.join(log_root, ".enabled"), "w") as handle:
                    handle.write("1")
                self.assertEqual(1, self.run_main(["opencode", "status"], env=env)[0])
                self.assertEqual([".enabled"], sorted(os.listdir(log_root)))

    def test_a_named_database_that_does_not_exist_is_no_database_and_no_other_one_is_chosen(self):
        self.add_database("opencode.db", "default")
        self.add_database("opencode-other.db", "other", modified=NEW_SECONDS)
        before = sorted(os.listdir(self.data))
        for env, name in (({"OPENCODE_DB": "missing.db"}, "missing.db"), ({"OPENCODE_CHANNEL": "nightly"}, "opencode-nightly.db")):
            with self.subTest(env):
                self.run_ok(["opencode", "on"], env)
                self.assertEqual([], self.archived_session_ids())
                self.assertIn("%s: %s" % (os.path.join(self.data, name), NO_DATABASE_REASON), self.usage_of_unknown_session(env))
                self.assertEqual(before, sorted(os.listdir(self.data)))  # looking for a database never makes one


class DatabaseProblemEntryTest(DatabaseChoiceTestCase):
    def assert_nothing_archived_and_listed_unreadable(self, reason):
        """Every command ends without error and archives nothing; a lookup that needs the whole store names the reason."""
        for argv in (["opencode", "on"], ["opencode", "status"], ["opencode", "off"]):
            self.run_ok(argv)
        self.assertEqual([], self.archived_files(".md"))
        self.run_ok(["opencode", "on"])
        self.assertIn("%s: %s" % (self.database_path, reason), self.usage_of_unknown_session())

    def lock_database(self):
        """OpenCode writing in a rollback-journal database: no reader gets in until the lock is released."""
        writer = sqlite3.connect(self.database_path)
        self.addCleanup(writer.close)
        writer.execute("BEGIN EXCLUSIVE")
        return writer

    def test_no_db_listed_busy_db_unreadable_retried_v1_only_unsupported(self):
        with self.subTest("no database"):
            self.assertFalse(os.path.exists(self.data))
            self.assert_nothing_archived_and_listed_unreadable(NO_DATABASE_REASON)
            self.assertFalse(os.path.exists(self.data))  # looking for a database never makes one
            OpenCodeDatabase(self.database_path).fixture()  # OpenCode starts and creates it: the next sync archives the session
            self.run_ok(["opencode", "status"])
            (log_path,) = self.archived_files(".md")
            self.assertIn(ROOT_ID, os.path.basename(log_path))
        with self.subTest("v1-only database"):
            self.reset()
            OpenCodeDatabase(self.database_path, schema=V1_SCHEMA)
            self.assert_nothing_archived_and_listed_unreadable(UNSUPPORTED_DATABASE_REASON)
        with self.subTest("busy database, read again by the next sync"):
            self.reset()
            OpenCodeDatabase(self.database_path).fixture()
            writer = self.lock_database()
            with mock.patch.object(opencode_reader, "BUSY_TIMEOUT_SECONDS", 0.05):
                self.assert_nothing_archived_and_listed_unreadable(DATABASE_BUSY_REASON)
                writer.rollback()
                self.run_ok(["opencode", "status"])
            (log_path,) = self.archived_files(".md")
            self.assertIn(ROOT_ID, os.path.basename(log_path))

    def test_a_busy_or_missing_database_leaves_the_sessions_already_archived_untouched(self):
        OpenCodeDatabase(self.database_path).fixture()
        self.run_ok(["opencode", "on"])
        archived = {path: folder_snapshot(os.path.dirname(path))[os.path.basename(path)]
                    for path in self.archived_files(".md") + self.archived_files(".usage.txt")}
        self.assertEqual(2, len(archived))
        writer = self.lock_database()
        with mock.patch.object(opencode_reader, "BUSY_TIMEOUT_SECONDS", 0.05):
            self.run_ok(["opencode", "status"])
            writer.rollback()
        os.rename(self.database_path, self.database_path + ".away")
        self.run_ok(["opencode", "status"])
        os.rename(self.database_path + ".away", self.database_path)
        self.run_ok(["opencode", "status"])
        self.assertEqual(archived, {path: folder_snapshot(os.path.dirname(path))[os.path.basename(path)] for path in archived})

    def test_a_database_that_cannot_be_opened_for_another_reason_still_ends_without_error(self):
        os.makedirs(self.data)
        with open(self.database_path, "wb") as handle:
            handle.write(b"this is no database " * 100)
        self.run_ok(["opencode", "on"])
        self.assertIn("%s: %s" % (self.database_path, CANNOT_BE_OPENED_REASON), self.usage_of_unknown_session())


class RelocatedDataRootTest(DatabaseChoiceTestCase):
    def test_relocated_xdg_data_home_exits_1_naming_the_root(self):
        self.add_database("opencode.db", "default")
        relocated = os.path.join(self.home, "relocated-data")
        expected = ("session-log: OpenCode's XDG data home is relocated: XDG_DATA_HOME is %s; session-log supports only the default %s\n"
                    % (relocated, self.default_data_home))
        for argv in (["opencode", "on"], ["opencode", "off"], ["opencode", "status"], ["opencode", "usage"]):
            with self.subTest(argv):
                self.assertEqual((1, "", expected), self.run_main(argv, env={"XDG_DATA_HOME": relocated}))
        self.assertFalse(os.path.exists(os.path.join(self.home, ".config")))

    def test_the_relocated_root_is_refused_before_the_old_enabled_flag_is_converted(self):
        self.add_database("opencode.db", "default")
        log_root = self.log_root
        os.makedirs(log_root)
        with open(os.path.join(log_root, ".enabled"), "w") as handle:
            handle.write("1")
        self.assertEqual(1, self.run_main(["opencode", "status"], env={"XDG_DATA_HOME": os.path.join(self.home, "elsewhere")})[0])
        self.assertEqual([".enabled"], sorted(os.listdir(log_root)))

    def test_the_relocated_root_is_refused_before_the_database_is_chosen(self):
        self.add_database("opencode.db", "default")
        relocated = os.path.join(self.home, "elsewhere")
        for chosen in (":memory:", ".."):
            with self.subTest(chosen):
                code, _, stderr = self.run_main(["opencode", "on"], env={"XDG_DATA_HOME": relocated, "OPENCODE_DB": chosen})
                self.assertEqual(1, code)
                self.assertIn("XDG_DATA_HOME is %s" % relocated, stderr)

    def test_the_default_root_spelled_out_in_any_way_or_left_empty_works_as_unset(self):
        self.add_database("opencode.db", "default")
        spellings = [self.default_data_home, self.default_data_home + os.sep, os.path.realpath(self.default_data_home),
                     os.path.join(self.home, ".local", "..", ".local", "share"), ""]
        for value in spellings:
            with self.subTest(value):
                shutil.rmtree(os.path.join(self.home, ".config"), ignore_errors=True)
                self.run_ok(["opencode", "on"], {"XDG_DATA_HOME": value})
                self.assertEqual(["ses_default"], self.archived_session_ids())


class EnvironmentTest(unittest.TestCase):
    def test_a_run_reads_the_real_environment_unless_a_test_hands_it_one(self):
        self.assertIs(os.environ, session_log.Environment().env)
        self.assertEqual({"A": "1"}, session_log.Environment(env={"A": "1"}).env)


class NewestDatabaseTest(unittest.TestCase):
    def test_a_candidate_that_cannot_be_inspected_is_skipped_not_fatal(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        for name, modified in (("opencode-a.db", OLD_SECONDS), ("opencode-b.db", NEW_SECONDS)):
            path = os.path.join(folder, name)
            open(path, "wb").close()
            os.utime(path, (modified, modified))
        real_lstat = os.lstat

        def lstat(path, *args, **kwargs):
            if str(path).endswith("opencode-b.db"):
                raise PermissionError(path)
            return real_lstat(path, *args, **kwargs)

        # `lstat` is how the module inspects a candidate; if it moves to another call, this test has to follow.
        with mock.patch.object(opencode_database.os, "lstat", lstat):
            self.assertEqual(os.path.join(folder, "opencode-a.db"), opencode_database.choose_database(folder, {}))


if __name__ == "__main__":
    unittest.main()
