"""`status` and `usage` work when the log root cannot be written (sync is best effort there), and `off` names the operation, the
path and the reason when it cannot turn logging off. The real entry (`session_log.main`) over a sandbox `HOME`."""
import os
import re
import unittest

from tests.test_session_log import ON_FLAG, REPORT_SUFFIX, SandboxCase

READ_ONLY = 0o500
WRITABLE = 0o700
WARNING = re.compile(r"^session-log: cannot update the archive \(showing what is already archived\): (?P<path>\S+): "
                     r"(?P<reason>Permission denied)\n\Z")


@unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), "needs a folder this account cannot write")
class UnwritableLogRootTest(SandboxCase):
    def setUp(self):
        super().setUp()
        self.write_transcript()
        self.turn_logging_on_long_ago()

    def _lock(self):
        os.chmod(self.log_root, READ_ONLY)
        self.addCleanup(os.chmod, self.log_root, WRITABLE)

    def _archive_then_lock(self):
        self.assertEqual(0, self.run_main(["claude", "status"])[0])  # archives the session
        self.assertEqual(1, sum(name.endswith(REPORT_SUFFIX) for _, _, names in os.walk(self.log_root) for name in names))
        self._lock()

    def test_status_still_answers_names_the_failed_update_on_stderr_and_exits_0(self):
        self._lock()
        code, stdout, stderr = self.run_main(["claude", "status"])
        self.assertEqual(0, code)
        self.assertIn("Session logging is on since", stdout)
        self.assertIn("Archived sessions: 0", stdout)
        self.assertRegex(stderr, WARNING)
        self.assertTrue(WARNING.match(stderr).group("path").startswith(self.log_root))

    def test_usage_of_a_never_archived_session_prints_the_live_report_and_warns(self):
        self._lock()
        for argv in (["claude", "usage"], ["claude", "usage", "abc123"]):
            with self.subTest(argv=argv):
                code, stdout, stderr = self.run_main(argv)
                self.assertEqual(0, code)
                self.assertIn("session: abc123", stdout)
                self.assertRegex(stderr, WARNING)

    def test_usage_of_an_archived_session_prints_its_saved_report_and_warns(self):
        self._archive_then_lock()
        code, saved, _ = self.run_main(["claude", "usage", "--latest"])
        self.assertEqual(0, code)
        self.assertIn("session: abc123", saved)
        for argv in (["claude", "usage"], ["claude", "usage", "abc123"]):
            with self.subTest(argv=argv):
                code, stdout, stderr = self.run_main(argv)
                self.assertEqual((0, saved), (code, stdout))
                self.assertRegex(stderr, WARNING)

    def test_status_of_an_archived_session_lists_it_while_the_root_is_locked(self):
        self._archive_then_lock()
        code, stdout, stderr = self.run_main(["claude", "status"])
        self.assertEqual(0, code)
        self.assertIn("Archived sessions: 1", stdout)
        self.assertRegex(stderr, WARNING)

    def test_a_writable_root_prints_no_warning(self):
        code, _, stderr = self.run_main(["claude", "status"])
        self.assertEqual((0, ""), (code, stderr))

    def test_off_fails_naming_the_operation_the_flag_file_and_the_reason_and_logging_stays_on(self):
        self._lock()
        code, stdout, stderr = self.run_main(["claude", "off"])
        self.assertEqual((1, ""), (code, stdout))
        flag = os.path.join(self.log_root, ON_FLAG)
        self.assertEqual("session-log: cannot turn logging off (it stays on): %s: Permission denied\n" % flag, stderr)
        self.assertTrue(os.path.exists(flag))
        self.assertNotIn("Errno", stderr)

    def test_on_in_a_folder_that_cannot_be_created_names_the_operation_the_path_and_the_reason(self):
        os.unlink(os.path.join(self.log_root, ON_FLAG))
        os.rmdir(self.log_root)
        os.chmod(self.claude, READ_ONLY)
        self.addCleanup(os.chmod, self.claude, WRITABLE)
        code, _, stderr = self.run_main(["claude", "on"])
        self.assertEqual(1, code)
        self.assertRegex(stderr, r"^session-log: cannot turn logging on: \S+: Permission denied\n\Z")
        self.assertNotIn("Errno", stderr)


if __name__ == "__main__":
    unittest.main()
