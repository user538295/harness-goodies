"""E2E of the S29 matrix (T-9): the unit suite and a sync run on macOS under Homebrew `python3` and in Linux Docker
(`python:3.14-slim`), each with `PATH` reduced to the Python interpreter, and the git checks run separately in the macOS checkout.

What runs where:
- `PATH` is one folder that holds a single `python3` link to the interpreter. The unit suite (`python3 -m unittest discover -s
  skills/session-log/tests -t skills/session-log`, exactly the documented command; without `-t` the `sessionlog` import fails) then
  runs as a child with that `PATH`, so a command looked up by name (bash, jq, sqlite3, node, zstd, ...) cannot be found. A command
  started by absolute path would still run, so the sync half does not rely on `PATH` alone: it records every process the entry starts
  (see below). The suite's own fixtures start `/bin/sh` stubs by absolute path; those are the tests' tools, not the skill's.
- The run must pass (exit 0, final line `OK`), run every test the discovery finds (tests of a class whose `setUpClass` skipped are
  not counted by unittest), and skip only tests whose reason names a command missing from `PATH` (Docker, zsh, fish, pwsh, git,
  opencode) or the child marker; any other skip fails the run.
- `ReducedPathSyncTests` is the sync half. It lives in this file but runs only inside such a child (`SESSION_LOG_S29_CHILD=1`, never
  export it by hand): there it proves that `PATH` holds nothing but the interpreter, then runs `on`, the Claude SessionStart hook,
  `usage`, `status` and `off` for real in a sandbox `HOME`, under a `sitecustomize` audit hook that logs every `subprocess.Popen`,
  `os.system`, `os.exec*`, `os.spawn*` and `os.posix_spawn`: the log must stay empty, so the skill called no command at all, by name
  or by absolute path. A control run proves the hook does log a spawn. Everywhere else the class skips, and so does the matrix itself
  inside a child (it would otherwise run the suite from the suite).
- macOS: the interpreter that runs this suite, which must be a Homebrew one (3.15 on the maintainer's Mac) and not a virtual
  environment; the printed version is in the failure message.
- Docker: `python:3.14-slim` with the repository mounted read-only, no network, as the host user; the container makes the `PATH`
  folder itself.
- The launcher tests (S21, S56: `test_e2e_cursor_launchers.py`, with their shells on `PATH`; `test_locator.sh` is S60) run separately
  by their own documented commands, not from here. The git checks (S57's `hook` file mode and S58's `git grep`) run separately in
  the macOS checkout with `git` on `PATH`: `test_git_checks_run_separately_in_macos_checkout_with_git_on_path` runs exactly those two
  tests as a child and requires that both ran, none skipped. `git grep` scans tracked files, so the check is complete only on a
  committed checkout.

The tests skip, naming the reason, when they are not on macOS (the macOS and git tests), when the `docker` command, the daemon or the
image is missing (`docker pull python:3.14-slim`), or when the checkout is not a git work tree (the git test). A run that must prove
them all sets `SESSION_LOG_REQUIRE_S29_MATRIX=1`, which turns every such skip into a failure; a run without it proves nothing on a
machine that lacks one of them, so record that the variable was set. Each child run is a full unit suite run, so the matrix takes about a minute and, inside the full suite, about two minutes more.

From the repository root (macOS with Homebrew Python 3.14 or newer, Docker and git on the host):

    SESSION_LOG_REQUIRE_S29_MATRIX=1 python3 -m unittest discover -s skills/session-log/tests -t skills/session-log \\
        -p "test_e2e_s29_matrix.py" -v

The commands the tests run (`<pybin>` is a temporary folder holding the `python3` link, `<tmp>` another one; repository root as the
working directory; the macOS child gets exactly these four variables and no others):

    # macOS, Homebrew python3
    PATH=<pybin> HOME=<tmp> PYTHONDONTWRITEBYTECODE=1 SESSION_LOG_S29_CHILD=1 \\
        <pybin>/python3 -m unittest discover -s skills/session-log/tests -t skills/session-log -v
    # Linux Docker (<name> is unique; the container makes <pybin> itself, `docker rm -f <name>` follows the run)
    docker run --rm --init --name <name> --network none --user <uid>:<gid> -v "$PWD":/repo:ro -w /repo \\
        -e PYTHONDONTWRITEBYTECODE=1 -e SESSION_LOG_S29_CHILD=1 python:3.14-slim sh -c \\
        'mkdir -p /tmp/pybin /tmp/s29-home && ln -s "$(command -v python3)" /tmp/pybin/python3 && python3 --version \\
        && PATH=/tmp/pybin HOME=/tmp/s29-home exec python3 -m unittest discover -s skills/session-log/tests -t skills/session-log -v'
    # git checks, macOS checkout, git on PATH, run from skills/session-log with the interpreter that runs the suite
    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \\
        tests.test_cursor_hooks_config.CursorHooksConfigTests.test_hook_file_is_git_mode_100755 \\
        tests.test_repo_scan.RepoScanTest.test_git_grep_lists_only_exempt_files
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from tests import test_e2e_skeleton as skeleton

REPO = skeleton.SKILL_DIR.parents[1]
CHILD_VARIABLE = "SESSION_LOG_S29_CHILD"
CHILD_VALUE = "1"
REQUIRE_VARIABLE = "SESSION_LOG_REQUIRE_S29_MATRIX"
REQUIRE_VALUE = "1"
IMAGE = "python:3.14-slim"
PYTHON_COMMAND = "python3"
SKILL_TESTS_FOLDER = "skills/session-log/tests"
SKILL_TOP_LEVEL = "skills/session-log"
MINIMUM_PYTHON = (3, 14)
HOMEBREW_CELLARS = ("/opt/homebrew/Cellar/", "/usr/local/Cellar/")  # where a Homebrew interpreter really lives, behind its links
SUITE_TIMEOUT_SECONDS = 1800
DOCKER_PROBE_TIMEOUT_SECONDS = 30
DOCKER_CLEANUP_TIMEOUT_SECONDS = 30
GIT_TIMEOUT_SECONDS = 600
GIT_PROBE_TIMEOUT_SECONDS = 30
CONTAINER_NAME_PREFIX = "session-log-s29-"
REPO_MOUNT = "/repo"
CONTAINER_PYBIN = "/tmp/pybin"
CONTAINER_HOME = "/tmp/s29-home"
# commands that must not resolve on the reduced `PATH`: the five S29 names (Git Bash is `bash`) and the tools of the tests that skip here
NOT_ALLOWED_TOOLS = ("bash", "jq", "sqlite3", "node", "zstd", "git", "docker", "zsh", "fish", "pwsh")
SPAWN_LOG_VARIABLE = "SESSION_LOG_S29_SPAWN_LOG"
SPAWN_EVENTS = ("subprocess.Popen", "os.system", "os.exec", "os.spawn", "os.posix_spawn")
SITECUSTOMIZE = '''import os
import sys

_SPAWN_EVENTS = %r
_LOG = os.environ[%r]


def _record(event, arguments):
    if event in _SPAWN_EVENTS:
        with open(_LOG, "a", encoding="utf-8") as handle:
            handle.write("%%s %%r\\n" %% (event, arguments))


sys.addaudithook(_record)
''' % (SPAWN_EVENTS, SPAWN_LOG_VARIABLE)
SYNC_SESSION = "hooked-session"
HOOK_PAYLOAD = b'{"session_id": "s29", "hook_event_name": "SessionStart", "source": "startup"}'
SUITE_DISCOVER = ["-m", "unittest", "discover", "-s", SKILL_TESTS_FOLDER, "-t", SKILL_TOP_LEVEL, "-v"]
GIT_TEST_IDS = ("tests.test_cursor_hooks_config.CursorHooksConfigTests.test_hook_file_is_git_mode_100755",
                "tests.test_repo_scan.RepoScanTest.test_git_grep_lists_only_exempt_files")
RAN_PATTERN = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)
SET_UP_CLASS_SKIP_PATTERN = re.compile(r"^setUpClass \((tests\.[\w.]+)\) \.\.\. skipped", re.MULTILINE)
SKIP_REASON_PATTERN = re.compile(r"\.\.\. skipped (.*)$", re.MULTILINE)  # the reason as unittest prints it, a repr in either quote style
SKIPPED_COUNT_PATTERN = re.compile(r"^OK \(skipped=(\d+)\)\s*\Z", re.MULTILINE)
# a skip is allowed when its reason says a command is missing from `PATH` (Docker, zsh, git, opencode ...), or names the child marker
ALLOWED_SKIP_REASON = re.compile(r"on PATH|S29 child run itself")
FINAL_RESULT_PATTERN = re.compile(r"^OK(?: \(skipped=\d+\))?\s*\Z", re.MULTILINE)
STRICT_RESULT_PATTERN = re.compile(r"^OK\s*\Z", re.MULTILINE)  # no skipped=, no failures
SYNC_TEST_NAME = "test_sync_runs_with_only_the_interpreter_on_path"
SYNC_PASSED_LINE = re.compile(r"^%s \(.*ReducedPathSyncTests.*\) \.\.\. ok$" % SYNC_TEST_NAME, re.MULTILINE)


def in_child_run():
    return os.environ.get(CHILD_VARIABLE) == CHILD_VALUE


def skip_or_fail(test_case, reason):
    """A missing prerequisite is a skip, or a failure when the run must prove the whole matrix. Always raises."""
    if os.environ.get(REQUIRE_VARIABLE) == REQUIRE_VALUE:
        test_case.fail("S29 matrix prerequisite missing (%s is set): %s" % (REQUIRE_VARIABLE, reason))
    test_case.skipTest(reason)


def run_utf8(command, **options):
    """A finished child run with its output decoded as UTF-8, whatever the locale of this process."""
    return subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", errors="replace", **options)


def ran_count(test_case, result):
    found = RAN_PATTERN.findall(result.stderr)
    test_case.assertTrue(found, "no 'Ran N tests' line:\n" + (result.stdout + result.stderr)[-2000:])
    return int(found[-1])


def expected_ran_count(output):
    """How many tests a complete child run runs: every test the discovery finds, except those of a class whose `setUpClass` skipped
    (unittest reports such a class as one skip and counts none of its tests as run). It runs the discovery, so it imports the suite."""
    loader = unittest.TestLoader()
    count = loader.discover(str(REPO / SKILL_TESTS_FOLDER), top_level_dir=str(REPO / SKILL_TOP_LEVEL)).countTestCases()
    for class_name in SET_UP_CLASS_SKIP_PATTERN.findall(output):
        count -= loader.loadTestsFromName(class_name).countTestCases()
    return count


def find_docker_unavailable_reason():
    """Why the Docker run cannot happen here, or an empty string when it can."""
    if shutil.which("docker") is None:
        return "the docker command is not on PATH"
    try:
        probe = subprocess.run(["docker", "image", "inspect", IMAGE], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               timeout=DOCKER_PROBE_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as error:
        return "docker cannot be run: %s" % error
    if probe.returncode == 0:
        return ""
    probe_error = probe.stderr.decode("utf-8", "replace").strip()
    return "the Docker daemon is not running or %s is not pulled (docker pull %s): %s" % (IMAGE, IMAGE, probe_error)


def find_unsuitable_interpreter_reason():
    """Why this interpreter is not the Homebrew `python3` the macOS run is about, or an empty string."""
    if not os.path.realpath(sys.executable).startswith(HOMEBREW_CELLARS):
        return "the interpreter %s is not a Homebrew one" % sys.executable
    if sys.prefix != sys.base_prefix:
        return "the interpreter %s runs in a virtual environment, which a bare link to it would lose" % sys.executable
    return ""


@unittest.skipIf(in_child_run(), "this is the S29 child run itself: the matrix does not run the suite from the suite")
class MatrixTests(unittest.TestCase):
    def assert_complete_green_run(self, result, label):
        """The child's exit code, that it ran every test, ended `OK`, skipped only for a command missing from `PATH` or the child
        marker, and ran the sync."""
        self.assertEqual(0, result.returncode, "%s: the unit suite failed:\n%s" % (label, (result.stdout + result.stderr)[-6000:]))
        self.assertEqual(expected_ran_count(result.stderr), ran_count(self, result), "%s: the run did not run every discovered test" % label)
        self.assertRegex(result.stderr, FINAL_RESULT_PATTERN, "%s: the run did not end OK" % label)
        reasons = SKIP_REASON_PATTERN.findall(result.stderr)
        self.assertEqual([], [reason for reason in reasons if not ALLOWED_SKIP_REASON.search(reason)],
                         "%s: tests skipped for a reason other than a command missing from PATH" % label)
        counted = SKIPPED_COUNT_PATTERN.search(result.stderr)
        self.assertEqual(len(reasons), int(counted.group(1)) if counted else 0, "%s: a skip is missing from the listed ones" % label)
        self.assertRegex(result.stderr, SYNC_PASSED_LINE, "%s: the sync test did not run and pass" % label)

    def test_unit_suite_passes_under_homebrew_python3_with_reduced_path(self):
        if sys.platform != "darwin":
            skip_or_fail(self, "the macOS run needs macOS, this is %s" % sys.platform)
        reason = find_unsuitable_interpreter_reason()
        if reason:
            skip_or_fail(self, reason)
        version = sys.version.split()[0]
        self.assertGreaterEqual(sys.version_info[:2], MINIMUM_PYTHON, "Python %s is older than 3.14" % version)
        with tempfile.TemporaryDirectory() as scratch:
            pybin = Path(os.path.realpath(scratch), "pybin")
            pybin.mkdir()
            (pybin / PYTHON_COMMAND).symlink_to(sys.executable)
            env = {"PATH": str(pybin), "HOME": scratch, "PYTHONDONTWRITEBYTECODE": "1", CHILD_VARIABLE: CHILD_VALUE}
            result = run_utf8([str(pybin / PYTHON_COMMAND)] + SUITE_DISCOVER, cwd=REPO, env=env, timeout=SUITE_TIMEOUT_SECONDS)
        self.assert_complete_green_run(result, "macOS, Python %s (%s)" % (version, sys.executable))

    def test_unit_suite_passes_in_python314_slim(self):
        reason = find_docker_unavailable_reason()
        if reason:
            skip_or_fail(self, "the Docker run is not possible: " + reason)
        if not hasattr(os, "getuid"):
            skip_or_fail(self, "the container runs as the host user, which needs a POSIX host")
        script = ('mkdir -p {pybin} {home} && ln -s "$(command -v {python})" {pybin}/{python} && {python} --version '
                  '&& PATH={pybin} HOME={home} exec {python} {suite}').format(
                      pybin=CONTAINER_PYBIN, python=PYTHON_COMMAND, home=CONTAINER_HOME, suite=" ".join(SUITE_DISCOVER))
        name = CONTAINER_NAME_PREFIX + uuid.uuid4().hex
        docker_run = ["docker", "run", "--rm", "--init", "--name", name, "--network", "none", "--user", "%d:%d" % (os.getuid(), os.getgid()),
                      "-v", "%s:%s:ro" % (REPO, REPO_MOUNT), "-w", REPO_MOUNT, "-e", "PYTHONDONTWRITEBYTECODE=1",
                      "-e", "%s=%s" % (CHILD_VARIABLE, CHILD_VALUE), IMAGE, "sh", "-c", script]
        try:
            result = run_utf8(docker_run, timeout=SUITE_TIMEOUT_SECONDS)
        finally:
            # a timeout kills only the docker client, and any other error leaves the container running; after a normal run `--rm` removed it
            try:
                subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=DOCKER_CLEANUP_TIMEOUT_SECONDS, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass  # the run's own result or error matters more than a failed cleanup
        self.assertEqual(0, result.returncode, "Docker %s: the unit suite failed:\n%s" % (IMAGE, (result.stdout + result.stderr)[-6000:]))
        self.assertTrue(result.stdout.startswith("Python 3.14."), "the container is not Python 3.14: %r" % result.stdout[:80])
        self.assert_complete_green_run(result, "Docker " + IMAGE)

    def test_git_checks_run_separately_in_macos_checkout_with_git_on_path(self):
        if sys.platform != "darwin":
            skip_or_fail(self, "the git checks run in the macOS checkout, this is %s" % sys.platform)
        if shutil.which("git") is None:
            skip_or_fail(self, "git is not on PATH")
        probe = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=REPO, capture_output=True, text=True, check=False,
                               timeout=GIT_PROBE_TIMEOUT_SECONDS)
        if probe.returncode != 0 or Path(probe.stdout.strip()).resolve() != REPO:
            skip_or_fail(self, "the checkout is not the top of a git work tree")
        result = run_utf8([sys.executable, "-m", "unittest", "-v", *GIT_TEST_IDS], cwd=REPO / SKILL_TOP_LEVEL,
                          env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHON_COLORS="0", NO_COLOR="1"), timeout=GIT_TIMEOUT_SECONDS)
        self.assertEqual(0, result.returncode, result.stderr[-4000:])
        self.assertEqual(len(GIT_TEST_IDS), ran_count(self, result))
        self.assertRegex(result.stderr, STRICT_RESULT_PATTERN)  # `OK` with no `skipped=`: both ran with git on PATH, none skipped


@unittest.skipUnless(in_child_run(), "runs only inside the S29 child run, where PATH holds nothing but the interpreter")
class ReducedPathSyncTests(skeleton.SkeletonCase):
    """A sync, as `on`, `usage` and the Claude SessionStart hook run it, with nothing but the interpreter on `PATH` and no process spawned."""

    def setUp(self):
        super().setUp()
        self.spawn_log = os.path.join(self.home, "spawn.log")
        self.audit_folder = os.path.join(self.home, "audit-hook")
        os.makedirs(self.audit_folder)
        Path(self.audit_folder, "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")

    def run_command(self, *arguments):
        """The entry run as the skeleton runs it, with the `PATH` of this run (no stubs in front of it)."""
        return self.run_entry(["claude"] + list(arguments))

    def run_entry(self, arguments, stdin=b""):
        env = dict(skeleton.HOSTILE_CONSOLE_ENV, HOME=self.home, PATH=os.environ["PATH"], PYTHONPATH=self.audit_folder,
                   **{SPAWN_LOG_VARIABLE: self.spawn_log})
        result = subprocess.run([sys.executable, str(skeleton.ENTRY)] + arguments, env=env, cwd=self.project, input=stdin,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=skeleton.SUBPROCESS_TIMEOUT)
        return result.returncode, result.stdout.decode("utf-8"), result.stderr.decode("utf-8")

    def spawned(self):
        """The processes started so far by what ran under the audit hook, one line each."""
        return Path(self.spawn_log).read_text(encoding="utf-8").splitlines() if os.path.exists(self.spawn_log) else []

    def test_sync_runs_with_only_the_interpreter_on_path(self):
        path_entries = os.environ["PATH"].split(os.pathsep)
        self.assertEqual(1, len(path_entries), "PATH is not reduced to one folder: %s" % os.environ["PATH"])
        self.assertEqual([PYTHON_COMMAND], os.listdir(path_entries[0]))
        for tool in NOT_ALLOWED_TOOLS:
            self.assertIsNone(shutil.which(tool), "%s resolves on the reduced PATH" % tool)
        self.assertGreaterEqual(sys.version_info[:2], MINIMUM_PYTHON)

        # control: the audit hook does log a started process, so an empty log below means that none started
        control = dict(os.environ, PYTHONPATH=self.audit_folder, **{SPAWN_LOG_VARIABLE: self.spawn_log})
        subprocess.run([sys.executable, "-c", "import subprocess; subprocess.run(['/nonexistent-command'])"], env=control,
                       stderr=subprocess.DEVNULL, check=False, timeout=skeleton.SUBPROCESS_TIMEOUT)
        self.assertTrue(self.spawned(), "the audit hook did not log a process start")
        os.remove(self.spawn_log)

        self.write_session(skeleton.ACTIVE_SESSION)
        self.run_ok("on")
        self.assertIn(skeleton.PROMPT_TEXT, self.saved_logs()[skeleton.ACTIVE_SESSION])  # `on` synced

        self.write_session(SYNC_SESSION, skeleton.NEWER_PROMPT_AT)
        self.assertNotIn(SYNC_SESSION, self.saved_logs())
        self.assertEqual((0, "", ""), self.run_entry(["hook", "claude", "SessionStart"], HOOK_PAYLOAD))  # the hook syncs silently
        self.assertIn(skeleton.PROMPT_TEXT, self.saved_logs()[SYNC_SESSION])
        self.assertIn(skeleton.PROMPT_TEXT, self.saved_reports()[SYNC_SESSION])

        self.write_session(skeleton.EARLIEST_SESSION, skeleton.EARLIEST_PROMPT_AT)
        report = self.run_ok("usage")  # syncs, then prints the report of the session with the newest prompt
        self.assertEqual(self.saved_reports()[SYNC_SESSION], report)
        self.assertIn(skeleton.NON_ASCII_MARK, report)
        self.assertIn(skeleton.EARLIEST_SESSION, self.saved_logs())

        self.run_ok("status")
        self.run_ok("off")
        self.assertEqual([], self.spawned(), "the skill started a process while syncing")


if __name__ == "__main__":
    unittest.main()
