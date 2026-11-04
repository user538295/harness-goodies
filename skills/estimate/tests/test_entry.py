"""The entry script (C3): the version check before the package import, the `estimate:` error line and the exit codes 0/1/2."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests.support import (DEFAULT_SCALE, ENTRY_PATH, EXIT_BAD_ARGUMENTS, EXIT_OK, EXIT_RUNTIME_ERROR, ProjectTestCase)

OLD_PYTHON = (3, 9, 6, "final", 0)
SYSTEM_PYTHON = "/usr/bin/python3"


def _is_older_python(path):
    if not os.path.exists(path):
        return False
    probe = subprocess.run([path, "-c", "import sys; print(sys.version_info[:2] < (3, 14))"], capture_output=True, text=True)
    return probe.stdout.strip() == "True"


class VersionCheckTest(ProjectTestCase):
    def test_an_old_python_gets_the_requirement_and_a_fix_line_and_exit_1(self):
        result = self.run_estimate("read", version_info=OLD_PYTHON)
        self.assertEqual(EXIT_RUNTIME_ERROR, result.code)
        self.assertEqual("", result.out)
        lines = result.err.splitlines()
        self.assertEqual("estimate: Python 3.14+ required (found 3.9)", lines[0])
        self.assertTrue(lines[1].startswith("estimate: fix: "), lines)

    @unittest.skipUnless(_is_older_python(SYSTEM_PYTHON), "no Python older than 3.14 at " + SYSTEM_PYTHON)
    def test_the_entry_parses_and_refuses_on_a_real_older_python(self):
        result = subprocess.run([SYSTEM_PYTHON, ENTRY_PATH, "read"], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(EXIT_RUNTIME_ERROR, result.returncode)
        self.assertIn("estimate: Python 3.14+ required", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class ArgumentTest(ProjectTestCase):
    def test_no_command_is_a_bad_argument(self):
        self.assert_error(self.run_estimate(), EXIT_BAD_ARGUMENTS, "missing command")

    def test_an_unknown_command_is_a_bad_argument(self):
        self.assert_error(self.run_estimate("guess"), EXIT_BAD_ARGUMENTS, "unknown command 'guess'")

    def test_an_unknown_option_is_a_bad_argument(self):
        self.assert_error(self.run_estimate("read", "--all"), EXIT_BAD_ARGUMENTS, "unknown option '--all'")

    def test_a_missing_positional_is_a_bad_argument(self):
        self.assert_error(self.run_estimate("remove"), EXIT_BAD_ARGUMENTS, "remove needs <name>")

    def test_an_extra_positional_is_a_bad_argument(self):
        self.assert_error(self.run_estimate("remove", "a", "b"), EXIT_BAD_ARGUMENTS, "remove takes 1 argument")

    def test_an_option_without_its_value_is_a_bad_argument(self):
        self.assert_error(self.run_estimate("match-sessions", "-", "--session-log"), EXIT_BAD_ARGUMENTS, "--session-log needs a value")


class RealProcessTest(unittest.TestCase):
    """The script as a user runs it: outside a git repository the current folder is the project root."""

    def setUp(self):
        self.folder = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)

    def run_entry(self, *argv):
        return subprocess.run([sys.executable, ENTRY_PATH, *argv], cwd=self.folder, capture_output=True, text=True)

    def test_read_without_a_file_prints_the_empty_document_and_writes_nothing(self):
        result = self.run_entry("read")
        self.assertEqual(EXIT_OK, result.returncode, result.stderr)
        self.assertEqual({"version": 1, "scale": DEFAULT_SCALE, "features": []}, json.loads(result.stdout))
        self.assertEqual([], os.listdir(self.folder))

    def test_a_bad_argument_exits_2_with_one_line_and_no_usage_text(self):
        result = self.run_entry("save")
        self.assertEqual(EXIT_BAD_ARGUMENTS, result.returncode)
        self.assertEqual("estimate: save needs <entry-json | ->\n", result.stderr)
        self.assertEqual("", result.stdout)


if __name__ == "__main__":
    unittest.main()
