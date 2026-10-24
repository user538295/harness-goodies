"""E2E of the entry file on the oldest Python (S55, T-4): `session_log.py` and the shipped Claude hook run for real inside
`python:3.4-slim`, so the run also proves that the entry file still parses on Python 3.4.

The suite itself cannot run on Python 3.4, so it runs on the host and drives Docker: each case is one `docker run` of an `sh`
script, the harness T-4 requires (Python 3.4 cannot run this suite). The repository is mounted read-only (`docker run -v
"$PWD":/repo:ro -e HOME=/tmp/sandbox-home python:3.4-slim sh -c <script> sh <command> ...`), the container has no network, and
`HOME` is a folder inside it. The script lists the paths and checksums under `HOME` before and after the command and reports any
difference on stderr, so an entry that changed the sandbox shows up; the logging-on cases start with the on-flag present. The
container is Linux, so the fix line is the "other" platform's.

The hook is run as Claude Code starts it: the `command` and `args` of the shipped `hooks/hooks.json` with `${CLAUDE_PLUGIN_ROOT}`
replaced by the mounted repository root and a SessionStart payload on stdin (inside the container the `sh` script only forwards the
arguments, no shell parses them). The shipped guard ignores the script's exit code, and a hook swallows every failure (on 3.4 the
package cannot even import), so the hook is also run directly through `session_log.py hook ...` for its own exit code and silence,
and one case imports the entry and shows that no `sessionlog` module was loaded: the version check came first. The first test
proves that the container really is Python 3.4, so a changed image cannot make the others pass for the wrong interpreter.

The tests skip, naming the reason, when the `docker` command, the Docker daemon or the `python:3.4-slim` image is missing
(`docker pull python:3.4-slim` fetches the image).

From the repository root (Docker and Python 3.14 or newer on the host):

    python3 -m unittest discover -s skills/session-log/tests -t skills/session-log -p "test_e2e_python34.py" -v
"""
import json
import shutil
import subprocess
import unittest
import uuid

from tests.test_claude_hooks_config import PLUGIN_ROOT_VARIABLE, REPO, SCRIPT_RELATIVE, session_start_hook

IMAGE = "python:3.4-slim"
REPO_MOUNT = "/repo"
ENTRY_IN_CONTAINER = REPO_MOUNT + "/" + SCRIPT_RELATIVE
SANDBOX_HOME = "/tmp/sandbox-home"
FLAG_PATH = SANDBOX_HOME + "/.claude/prompt-logs/.log_enabled"
LISTING_BEFORE = "/tmp/listing-before"
LISTING_AFTER = "/tmp/listing-after"
SANDBOX_CHANGED_MARK = "session-log test: sandbox changed"
# `$@` is the command under test. `$?` is read first and the script ends with it, so docker reports the command's own exit code.
# The listing holds every path under HOME and its checksum; a missing entry file stops the run with its own message.
SANDBOX_SCRIPT = ('listing() {{ find "{home}" | sort; find "{home}" -type f -exec cksum {{}} +; }}; '
                  'test -f "{entry}" || {{ echo "{missing}" >&2; exit {fatal_code}; }}; '
                  'mkdir -p "{home}" && {seed}listing > {before}; "$@"; code=$?; listing > {after}; '
                  'diff {before} {after} >&2 || echo "{changed}" >&2; exit $code')
SEED_FAILED_MARK = "session-log test: sandbox seed failed"
SEED_FLAG = 'mkdir -p "$(dirname {0})" && echo on > "{0}" || {{ echo "{1}" >&2; exit {2}; }}; '
MISSING_ENTRY_MARK = "session-log test: entry file not mounted"
FATAL_SETUP_CODE = 99


def sandbox_script(flag_present=False):
    """The script that runs the command under test in an empty `HOME`, or in one whose on-flag exists."""
    seed = SEED_FLAG.format(FLAG_PATH, SEED_FAILED_MARK, FATAL_SETUP_CODE) if flag_present else ""
    return SANDBOX_SCRIPT.format(home=SANDBOX_HOME, seed=seed, entry=ENTRY_IN_CONTAINER, before=LISTING_BEFORE, after=LISTING_AFTER,
                                 changed=SANDBOX_CHANGED_MARK, missing=MISSING_ENTRY_MARK, fatal_code=FATAL_SETUP_CODE)


DOCKER_TIMEOUT_SECONDS = 120
DOCKER_PROBE_TIMEOUT_SECONDS = 30
DOCKER_CLEANUP_TIMEOUT_SECONDS = 30
CONTAINER_NAME_PREFIX = "session-log-python34-"
HOOK_PAYLOAD = json.dumps({"session_id": "s55", "hook_event_name": "SessionStart", "source": "startup"}).encode("utf-8")
REQUIREMENT_LINE = "session-log: Python 3.14+ required (found 3.4)\n"
FIX_LINE = ("session-log: fix: install Python 3.14 with your package manager, pyenv or uv, "
            "then make python3 resolve to it\n")
OLD_PYTHON_MESSAGE = REQUIREMENT_LINE + FIX_LINE
EXIT_OK = 0
EXIT_RUNTIME_ERROR = 1
SILENT_SUCCESS = (EXIT_OK, "", "")


def find_docker_unavailable_reason():
    """Why the 3.4 run cannot happen here, or an empty string when it can."""
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


def run_in_container(script, *command, stdin=b""):
    """(exit code, stdout, stderr) of `command` run by `script` under `sh` in the Python 3.4 image."""
    name = CONTAINER_NAME_PREFIX + uuid.uuid4().hex
    docker_run = ["docker", "run", "--rm", "-i", "--name", name, "--network", "none", "-v", "%s:%s:ro" % (REPO, REPO_MOUNT),
                  "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "HOME=" + SANDBOX_HOME, IMAGE, "sh", "-c", script, "sh"] + list(command)
    try:
        result = subprocess.run(docker_run, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=DOCKER_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        # the timeout kills only the docker client; the container would outlive it
        subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=DOCKER_CLEANUP_TIMEOUT_SECONDS, check=False)
        raise
    return result.returncode, result.stdout.decode("utf-8", "replace"), result.stderr.decode("utf-8", "replace")


def shipped_hook_command(plugin_root):
    """The shipped SessionStart hook as Claude Code starts it: the command and its args, `${CLAUDE_PLUGIN_ROOT}` replaced."""
    hook = session_start_hook()
    return [hook["command"]] + [argument.replace(PLUGIN_ROOT_VARIABLE, plugin_root) for argument in hook["args"]]


class Python34Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reason = find_docker_unavailable_reason()
        if reason:
            raise unittest.SkipTest("Python 3.4 run not possible: " + reason)

    def test_python34_runs_the_container_with_python_3_4(self):
        code, stdout, stderr = run_in_container('"$@"', "python3", "-c", "import sys; print(sys.version_info[:2])")
        self.assertEqual((EXIT_OK, "(3, 4)\n", ""), (code, stdout, stderr))

    def test_python34_command_prints_requirement_and_fix_lines_exits_1_and_on_creates_no_flag(self):
        code, stdout, stderr = run_in_container(sandbox_script(), "python3", ENTRY_IN_CONTAINER, "claude", "on")
        self.assertEqual((EXIT_RUNTIME_ERROR, "", OLD_PYTHON_MESSAGE), (code, stdout, stderr))  # a created flag adds the diff to stderr

    def test_python34_every_command_and_argument_shape_gets_the_same_two_lines_before_any_parsing(self):
        shapes = (("claude", "on"), ("claude", "status"), ("claude", "off"), ("claude", "usage"), ("claude", "usage", "--latest"),
                  ("claude", "usage", "some-session"), ("claude", "bogus"), ("nobody", "on"), ("--latest",), ("--help",), ("-h",), ())
        for flag_present in (False, True):  # logging off, and on: a user whose python3 is 3.4 loses nothing either way
            for arguments in shapes:
                with self.subTest(flag_present=flag_present, arguments=arguments):
                    code, stdout, stderr = run_in_container(sandbox_script(flag_present), "python3", ENTRY_IN_CONTAINER, *arguments)
                    self.assertEqual((EXIT_RUNTIME_ERROR, "", OLD_PYTHON_MESSAGE), (code, stdout, stderr))

    def test_python34_hook_exits_0_silently(self):
        hook_command = shipped_hook_command(REPO_MOUNT)
        self.assertIn(ENTRY_IN_CONTAINER, hook_command)  # the guard skips a missing script silently, so the script must be the mounted one
        code, stdout, stderr = run_in_container(sandbox_script(flag_present=True), *hook_command, stdin=HOOK_PAYLOAD)
        self.assertEqual(SILENT_SUCCESS, (code, stdout, stderr))

    def test_python34_entry_hook_exits_0_silently_itself_for_every_hook_shape(self):
        # the shipped guard ignores the script's exit code, so the entry is run directly for its own
        for arguments in (("claude", "SessionStart"), ("cursor", "sessionStart"), ("claude", "Unknown"), ()):
            with self.subTest(arguments=arguments):
                result = run_in_container(sandbox_script(flag_present=True), "python3", ENTRY_IN_CONTAINER, "hook", *arguments,
                                          stdin=HOOK_PAYLOAD)
                self.assertEqual(SILENT_SUCCESS, result)

    def test_python34_hook_stops_at_the_version_check_before_importing_the_package(self):
        # the package cannot even import on 3.4 and a hook swallows every failure, so only the loaded modules show where it stopped
        code, stdout, stderr = run_in_container(
            '"$@"', "python3", "-c",
            "import sys; sys.path.insert(0, '%s'); import session_log; code = session_log.main(['hook', 'claude', 'SessionStart']); "
            "print(code, sorted(name for name in sys.modules if name.startswith('sessionlog')))" % ENTRY_IN_CONTAINER.rsplit("/", 1)[0])
        self.assertEqual((EXIT_OK, "0 []\n", ""), (code, stdout, stderr))


if __name__ == "__main__":
    unittest.main()
