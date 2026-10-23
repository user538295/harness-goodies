"""T-13 (S30): the Cursor `hook` launcher keeps its executable mode in the plugin Cursor installed from a marketplace.

Run it after release 2, on a Mac whose Cursor installed the plugin from the PUBLIC marketplace (the release-2 commit). Cursor caches an
install per commit under `~/.cursor/plugins/cache/<marketplace>/cursor-goodies/<commit>/` (release-guide.md, Cursor marketplace section;
the same `<marketplace>/<plugin>/<commit>` shape is on disk for Cursor's own `cursor-public/atlassian/<commit>`). The test takes the newest
commit folder (the way the SKILL.md locator does, by folder time) and runs a real `test -x` on its `skills/session-log/hook`.

What the run shows: the executable bit (any executable bit; the plan's "mode 755" is the shipped mode, checked in git by
`test_cursor_hooks_config`) of the hook in whichever `cursor-goodies` install is newest, whatever its marketplace. It prints that path and
mode: a run before the public install is updated to release 2, or one that finds a local-checkout install under `user538295`, says
nothing about the public release-2 install, and the printed path is how to tell. A newest install without `skills/session-log/hook` fails
(a wrong layout is not a missing install). With no install the test skips, naming the reason; a run that must prove the install sets
`SESSION_LOG_REQUIRE_MARKETPLACE_INSTALL=1`, which turns that skip into a failure. The test reads the real `~/.cursor`, so it also runs in
the default `discover` and skips on a host without the install; it is skipped in the S29 child runs, whose `HOME` is a sandbox.

From the repository root:

    SESSION_LOG_REQUIRE_MARKETPLACE_INSTALL=1 python3 -m unittest discover -s skills/session-log/tests -t skills/session-log \\
        -p "test_e2e_marketplace_install.py" -v
"""
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_e2e_s29_matrix import in_child_run

REQUIRE_VARIABLE = "SESSION_LOG_REQUIRE_MARKETPLACE_INSTALL"
REQUIRE_VALUE = "1"
CACHE_RELATIVE = (".cursor", "plugins", "cache")
INSTALL_PATTERN = "*/cursor-goodies/*"
HOOK_RELATIVE = ("skills", "session-log", "hook")
SH_EXECUTABLE = "/bin/sh"
TEST_EXECUTABLE_SCRIPT = 'test -x "$1"'
TEST_TIMEOUT_SECONDS = 10
MODE_MASK = 0o777
EXECUTABLE_MODE = 0o755
NOT_EXECUTABLE_MODE = 0o644


def find_newest_install(home):
    """The newest `<marketplace>/cursor-goodies/<commit>` folder in the Cursor plugin cache of `home`, or None."""
    cache = Path(home).joinpath(*CACHE_RELATIVE)
    installs = [path for path in cache.glob(INSTALL_PATTERN) if path.is_dir()]
    return max(installs, key=lambda path: (path.stat().st_mtime, str(path))) if installs else None


def hook_of(install):
    return install.joinpath(*HOOK_RELATIVE)


def passes_test_x(path):
    """True when a real `test -x` on `path` succeeds."""
    result = subprocess.run([SH_EXECUTABLE, "-c", TEST_EXECUTABLE_SCRIPT, "sh", str(path)], timeout=TEST_TIMEOUT_SECONDS)
    return result.returncode == 0


def reason_without_install():
    return "no Cursor marketplace install of cursor-goodies under ~/%s/%s" % ("/".join(CACHE_RELATIVE), INSTALL_PATTERN)


def check_installed_hook(test_case, home):
    """Skip (fail when required) without an install; fail when the newest install lacks the hook or the hook is not executable."""
    install = find_newest_install(home)
    if install is None:
        if os.environ.get(REQUIRE_VARIABLE) == REQUIRE_VALUE:
            test_case.fail("marketplace install prerequisite missing (%s is set): %s" % (REQUIRE_VARIABLE, reason_without_install()))
        test_case.skipTest(reason_without_install())
    hook = hook_of(install)
    test_case.assertTrue(hook.is_file(), "the newest install has no hook file: %s" % hook)
    mode = stat.S_IMODE(hook.stat().st_mode)
    print("\nchecked installed hook: %s (mode %o)" % (hook, mode))
    test_case.assertTrue(passes_test_x(hook), "installed hook is not executable (mode %o): %s" % (mode, hook))


@unittest.skipIf(in_child_run(), "this is the S29 child run itself: its HOME is a sandbox, not the real Cursor cache")
class MarketplaceInstalledHookTests(unittest.TestCase):
    def test_marketplace_installed_hook_file_mode_is_executable(self):
        check_installed_hook(self, Path.home())


class InstalledHookCheckTests(unittest.TestCase):
    def setUp(self):
        self.home_folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.home_folder.cleanup)
        self.home = Path(self.home_folder.name)
        self.addCleanup(os.environ.pop, REQUIRE_VARIABLE, None)
        os.environ.pop(REQUIRE_VARIABLE, None)

    def install(self, marketplace, commit, mode=None, modified=None):
        folder = self.home.joinpath(*CACHE_RELATIVE, marketplace, "cursor-goodies", commit)
        folder.mkdir(parents=True)
        if mode is not None:
            hook = hook_of(folder)
            hook.parent.mkdir(parents=True)
            hook.write_text("#!/bin/sh\n")
            hook.chmod(mode)
        if modified is not None:
            os.utime(folder, (modified, modified))
        return folder

    def test_newest_install_wins_across_marketplaces(self):
        self.install("a", "old", EXECUTABLE_MODE, modified=1000)
        newest = self.install("b", "new", EXECUTABLE_MODE, modified=2000)
        self.assertEqual(newest, find_newest_install(self.home))

    def test_other_plugins_are_not_installs(self):
        other = self.home.joinpath(*CACHE_RELATIVE, "cursor-public", "atlassian", "abc")
        other.mkdir(parents=True)
        self.assertIsNone(find_newest_install(self.home))

    def test_executable_hook_passes(self):
        self.install("m", "c", EXECUTABLE_MODE)
        check_installed_hook(self, self.home)

    def test_non_executable_hook_fails(self):
        self.install("m", "c", NOT_EXECUTABLE_MODE)
        with self.assertRaises(AssertionError) as raised:
            check_installed_hook(self, self.home)
        self.assertIn("not executable (mode 644)", str(raised.exception))

    def test_newest_install_without_hook_fails_even_when_an_older_one_has_it(self):
        self.install("m", "old", EXECUTABLE_MODE, modified=1000)
        self.install("m", "new", modified=2000)
        with self.assertRaises(AssertionError) as raised:
            check_installed_hook(self, self.home)
        self.assertIn("no hook file", str(raised.exception))

    def test_no_install_skips_without_the_require_variable(self):
        with self.assertRaises(unittest.SkipTest):
            check_installed_hook(self, self.home)

    def test_no_install_fails_with_the_require_variable(self):
        os.environ[REQUIRE_VARIABLE] = REQUIRE_VALUE
        with self.assertRaises(AssertionError) as raised:
            check_installed_hook(self, self.home)
        self.assertIn(REQUIRE_VARIABLE, str(raised.exception))

    def test_require_variable_with_another_value_still_skips(self):
        os.environ[REQUIRE_VARIABLE] = "true"
        with self.assertRaises(unittest.SkipTest):
            check_installed_hook(self, self.home)


if __name__ == "__main__":
    unittest.main()
