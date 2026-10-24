"""Python probe runner (S24, Q2): on Windows the probe runs `python3 -c` through the injected runner and platform, any failure
of it is "Python not usable" with one exact fix, and nothing runs elsewhere (S1). No test spawns a Windows probe: the runner is a fake."""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from unittest import mock

import session_log
from sessionlog import probe_port, python_probe
from sessionlog.presentation import PROBE_FAILED
from sessionlog.probe_port import ProbeResult
from sessionlog.python_probe import PROBE_COMMAND, PROBE_FIX, PROBE_SOURCE, PythonProbeRunner, run_command

WINDOWS = "win32"
ANY_NONZERO_EXIT_CODE = 9009  # Q2: the exit code of a failing `python3` is irrelevant, so any non-zero value must fail
CWD = "/work/proj"
CLOCK = datetime(2000, 1, 1, tzinfo=timezone.utc)
SHORT_TIMEOUT_SECONDS = 0.5
HANG_SECONDS = 30


class RecordingRunner:
    def __init__(self, outcome=0):
        self.outcome = outcome
        self.commands = []

    def __call__(self, command):
        self.commands.append(tuple(command))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _run_probe_source_on(version):
    """The exit code the probe's `-c` source ends with when the interpreter reports `version`."""
    codes = []
    stand_in = types.SimpleNamespace(version_info=version, exit=codes.append)
    with mock.patch.dict(sys.modules, {"sys": stand_in}):
        exec(PROBE_SOURCE, {})
    return codes[0]


class PythonProbeTest(unittest.TestCase):
    def test_probe_failure_of_any_cause_on_windows_returns_fix_and_stand_in_exit_code_irrelevant(self):
        failures = (1, 2, 49, ANY_NONZERO_EXIT_CODE, -1, FileNotFoundError("python3"), PermissionError("alias"),
                    OSError("cannot start"), subprocess.TimeoutExpired("python3", 10),
                    subprocess.SubprocessError("broken"))
        for failure in failures:
            with self.subTest(failure=repr(failure)):
                runner = RecordingRunner(failure)
                result = PythonProbeRunner(WINDOWS, runner).check()
                self.assertEqual(ProbeResult(ok=False, fix=PROBE_FIX), result)
                self.assertEqual(1, len(runner.commands))

    def test_probe_runs_python3_dash_c_and_passes_on_exit_0(self):
        runner = RecordingRunner(0)
        self.assertEqual(ProbeResult(ok=True), PythonProbeRunner(WINDOWS, runner).check())
        self.assertEqual([("python3", "-c", PROBE_SOURCE)], runner.commands)
        self.assertEqual(PROBE_COMMAND, runner.commands[0])

    def test_probe_source_exits_0_from_python_3_14_up(self):
        for version, expected in (((3, 13, 9), 1), ((3, 14, 0), 0), ((3, 14, 0, "rc", 1), 0), ((3, 15, 1), 0), ((4, 0, 0), 0),
                                  ((3, 4, 0), 1), ((2, 7, 18), 1)):
            with self.subTest(version=version):
                self.assertEqual(expected, _run_probe_source_on(version))

    def test_probe_not_run_on_macos_or_linux(self):
        for platform in ("darwin", "linux", "freebsd14", "cygwin"):  # only native Windows (`win32`) is probed
            with self.subTest(platform=platform):
                runner = RecordingRunner(1)
                self.assertIsNone(PythonProbeRunner(platform, runner).check())
                self.assertEqual([], runner.commands)

    def test_probe_is_asked_again_by_every_check_and_never_cached(self):
        runner = RecordingRunner(ANY_NONZERO_EXIT_CODE)
        probe = PythonProbeRunner(WINDOWS, runner)
        self.assertFalse(probe.check().ok)
        runner.outcome = 0  # the user fixed Python between two runs
        self.assertTrue(probe.check().ok)
        self.assertEqual(2, len(runner.commands))

    def test_fix_is_one_exact_line(self):
        self.assertEqual("install Python 3.14 or newer with the Python install manager from python.org/downloads/windows, then "
                         "make sure it is the default python3 (py list) and the Microsoft Store python3 app execution alias does "
                         "not shadow it (Settings > Apps > Advanced app settings > App execution aliases)", PROBE_FIX)
        self.assertEqual(1, len(PROBE_FIX.splitlines()))

    def test_version_floor_is_one_value_shared_by_the_probe_the_fix_the_entry_and_the_message(self):
        self.assertEqual(session_log.MINIMUM_PYTHON, probe_port.MINIMUM_PYTHON)
        self.assertEqual(session_log.MINIMUM_TEXT, probe_port.MINIMUM_TEXT)
        self.assertIn("Python %s or newer" % session_log.MINIMUM_TEXT, PROBE_FIX)
        self.assertIn(repr(session_log.MINIMUM_PYTHON), PROBE_SOURCE)
        self.assertEqual("python3 does not run Python %s or newer" % session_log.MINIMUM_TEXT, PROBE_FAILED)
        self.assertIn("Python install manager", session_log.FIX_HINTS[python_probe.WINDOWS_PLATFORM])  # both name the same install
        self.assertIn("Python install manager", PROBE_FIX)

    def test_probe_source_has_no_double_quote_that_windows_argument_quoting_could_break(self):
        self.assertNotIn('"', PROBE_SOURCE)


class RunCommandTest(unittest.TestCase):
    def test_real_runner_returns_the_exit_code(self):
        self.assertEqual(0, run_command((sys.executable, "-c", "import sys; sys.exit(0)")))
        self.assertEqual(7, run_command((sys.executable, "-c", "import sys; sys.exit(7)")))

    def test_probe_source_run_by_a_real_interpreter_exits_0_from_the_minimum_version_up(self):
        expected = 0 if sys.version_info >= probe_port.MINIMUM_PYTHON else 1
        self.assertEqual(expected, run_command((sys.executable, "-c", PROBE_SOURCE)))

    def test_real_runner_gives_the_child_no_input_discards_its_output_and_bounds_its_time(self):
        with mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            run_command(("python3",))
        run.assert_called_once_with(["python3"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   timeout=python_probe.PROBE_TIMEOUT_SECONDS)

    def test_real_runner_raises_timeout_for_a_hanging_program_and_oserror_for_a_missing_one(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            run_command((sys.executable, "-c", "import time; time.sleep(%d)" % HANG_SECONDS), timeout=SHORT_TIMEOUT_SECONDS)
        with self.assertRaises(OSError):
            run_command((os.path.join(tempfile.gettempdir(), "session-log-no-such-program"),))


class EntryWiringTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)

    def run_main(self, argv, platform, runner, home=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        environment = session_log.Environment(stdout=stdout, stderr=stderr, home=home or self.home, platform=platform,
                                              run_probe=runner, get_cwd=lambda: CWD, now=lambda: CLOCK,
                                              to_local=lambda moment: moment.astimezone(timezone.utc), env={})
        code = session_log.main(argv, environment)
        return code, stdout.getvalue(), stderr.getvalue()

    def fresh_home(self):
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, True)
        return home

    def test_on_and_status_while_on_or_off_print_the_fix_on_windows_when_the_probe_fails_and_nothing_when_it_passes(self):
        for command, logging_on in (("on", False), ("on", True), ("status", False), ("status", True)):
            for exit_code, fails in ((ANY_NONZERO_EXIT_CODE, True), (0, False)):
                with self.subTest(command=command, logging_on=logging_on, probe_fails=fails):
                    home = self.fresh_home()
                    if logging_on:
                        self.run_main(["claude", "on"], WINDOWS, RecordingRunner(0), home)
                    runner = RecordingRunner(exit_code)
                    code, stdout, stderr = self.run_main(["claude", command], WINDOWS, runner, home)
                    self.assertEqual((0, ""), (code, stderr))
                    self.assertEqual(fails, "%s; fix: %s\n" % (PROBE_FAILED, PROBE_FIX) in stdout)
                    self.assertEqual(fails, "python3" in stdout)
                    self.assertEqual(1, len(runner.commands))

    def test_other_platforms_and_commands_and_the_hook_run_no_probe(self):
        runner = RecordingRunner(1)
        for argv, platform in ((["claude", "on"], "darwin"), (["claude", "status"], "linux"),
                               (["claude", "status"], "freebsd14"), (["hook", "claude", "SessionStart"], WINDOWS)):
            with self.subTest(argv=argv, platform=platform):
                code, _, stderr = self.run_main(argv, platform, runner, self.fresh_home())
                self.assertEqual((0, ""), (code, stderr))
        for command, expected_code in (("usage", 1), ("off", 0)):  # `usage` on an on home with no session stops with its own error
            home = self.fresh_home()
            self.run_main(["claude", "on"], "darwin", runner, home)
            with self.subTest(command=command):
                code, _, _ = self.run_main(["claude", command], WINDOWS, runner, home)
                self.assertEqual(expected_code, code)
        self.assertEqual([], runner.commands)

    def test_without_an_injected_runner_the_real_runner_runs_the_probe_command(self):
        with mock.patch.object(python_probe, "run_command", return_value=0) as real:
            code, stdout, _ = self.run_main(["claude", "on"], WINDOWS, None)
        self.assertEqual(0, code)
        self.assertNotIn(PROBE_FAILED, stdout)
        real.assert_called_once_with(PROBE_COMMAND)


if __name__ == "__main__":
    unittest.main()
