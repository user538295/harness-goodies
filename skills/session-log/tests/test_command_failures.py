"""A command that fails or is killed must not leave the logging switch in the wrong position (C1).

`on` that fails to sync rolls back the flag it created; `off` still switches logging off when SIGTERM or SIGINT ends it during its
sync (a real subprocess); SIGKILL cannot be caught, so no test (and no code) claims it."""
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from sessionlog.commands import CommandUseCases
from sessionlog.pricing import PricingRules
from tests.test_commands import CommandTestCase
from tests.test_sync import NOW, ON_AT, NoLeftovers, NoProbe

ENTRY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "session_log.py")
SKILL_DIR = os.path.dirname(ENTRY)
SYNC_STARTED_TIMEOUT_SECONDS = 30
EXIT_TIMEOUT_SECONDS = 30
POLL_SECONDS = 0.05
SLOW_SYNC_SECONDS = 120
SIGNAL_EXIT_BASE = 128

# Runs the real entry script as `__main__` with a sync that announces itself and then hangs, so a signal arrives mid-sync.
SLOW_SYNC_DRIVER = """
import runpy, signal, sys, time
signal.signal(signal.SIGINT, signal.default_int_handler)  # a test runner in the background may have SIGINT ignored
entry, marker = sys.argv[1], sys.argv[2]
sys.path.insert(0, sys.argv[3])
from sessionlog.sync import SyncUseCase
def slow_sync(self, *arguments, **keywords):
    open(marker, "w").close()
    time.sleep(%d)
SyncUseCase.sync = slow_sync
sys.argv = [entry] + sys.argv[4:]
runpy.run_path(entry, run_name="__main__")
""" % SLOW_SYNC_SECONDS


class _FailingSync:
    def sync(self, *_):
        raise OSError("disk full")


class FailedOnTest(CommandTestCase):
    def _commands(self, sync):
        return CommandUseCases(sync, None, self.archive, self.state, PricingRules(self.prices), lambda: NOW, self.log_root,
                               NoLeftovers(), NoProbe())

    def test_a_failed_on_leaves_logging_off_and_raises_the_error(self):
        with self.assertRaisesRegex(OSError, "disk full"):
            self._commands(_FailingSync()).on()
        self.assertIsNone(self.state.read_on_flag())

    def test_a_failed_on_keeps_a_flag_that_was_already_there(self):
        self._enable()
        with self.assertRaisesRegex(OSError, "disk full"):
            self._commands(_FailingSync()).on()
        self.assertEqual(self.state.read_on_flag().on_at, ON_AT)

    def test_an_on_interrupted_during_its_sync_leaves_logging_off(self):
        class Interrupted:
            def sync(self, *_):
                raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            self._commands(Interrupted()).on()
        self.assertIsNone(self.state.read_on_flag())


class KilledOffTest(unittest.TestCase):
    def setUp(self):
        if os.name == "nt":
            self.skipTest("a Windows process is ended without a catchable signal")
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        self.log_root = os.path.join(self.home, ".claude", "prompt-logs")
        os.makedirs(self.log_root)
        self.flag = os.path.join(self.log_root, ".log_enabled")
        with open(self.flag, "w", encoding="utf-8") as handle:
            handle.write(ON_AT.isoformat() + "\n")
        self.marker = os.path.join(self.home, "sync-started")

    def _start_off_during_a_slow_sync(self):
        environment = {"HOME": self.home, "PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"}
        process = subprocess.Popen([sys.executable, "-c", SLOW_SYNC_DRIVER, ENTRY, self.marker, SKILL_DIR, "claude", "off"],
                                   env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(process.kill)
        deadline = time.monotonic() + SYNC_STARTED_TIMEOUT_SECONDS
        while not os.path.exists(self.marker):
            if process.poll() is not None or time.monotonic() > deadline:
                self.fail("off never reached its sync: %r" % (process.communicate(),))
            time.sleep(POLL_SECONDS)
        return process

    def test_sigterm_during_the_sync_of_off_still_switches_logging_off(self):
        process = self._start_off_during_a_slow_sync()
        self.assertTrue(os.path.exists(self.flag))  # the sync runs while logging is still on
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=EXIT_TIMEOUT_SECONDS)
        self.assertFalse(os.path.exists(self.flag))
        self.assertEqual(process.returncode, SIGNAL_EXIT_BASE + signal.SIGTERM)  # the shell convention for a TERM exit

    def test_sigint_during_the_sync_of_off_still_switches_logging_off(self):
        process = self._start_off_during_a_slow_sync()
        process.send_signal(signal.SIGINT)
        process.communicate(timeout=EXIT_TIMEOUT_SECONDS)
        self.assertFalse(os.path.exists(self.flag))


if __name__ == "__main__":
    unittest.main()
