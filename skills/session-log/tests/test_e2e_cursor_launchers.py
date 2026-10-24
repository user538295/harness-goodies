"""E2E of the Cursor launchers and hooks (S21, S34, S56, and the hook half of S2): the shipped `hook` (POSIX sh) and `hook.ps1` run for
real from an installed copy under a folder name with a space and an apostrophe.

Where each launcher runs:
- `hook`: through `sh` and `zsh` on the host, and in Docker through `fish` (an Alpine image, whose `/bin/sh` is busybox ash) and through
  `dash` (the `/bin/sh` of the pwsh image, Ubuntu). On macOS and Linux Cursor runs a hook command as `$SHELL -c <command>` (brief F1);
  here the command is the shipped `.cursor-plugin/hooks.json` command with `${CURSOR_PLUGIN_ROOT}` replaced by the install path (a plain
  text replacement, as in the Claude hook e2e), run as `<shell> -c <command>`, and the shebang then starts `/bin/sh`.
- `hook.ps1`: through `pwsh` in Docker. On Windows Cursor runs hooks as `pwsh -NoProfile -NonInteractive -ExecutionPolicy Bypass -c <wrapper>`
  and adds `.ps1` to the extensionless command (brief F1). Cursor's wrapper text is not available here, so the run models it as
  `$input | & "<install>/skills/session-log/hook.ps1" cursor <event>` (the hook input comes in through `$input`, as the brief says).
- The SKILL.md command line is read out of `skills/session-log/SKILL.md` and run as `sh -c <line>` and as `pwsh -NoProfile
  -NonInteractive -Command <line>`.

Neither fish nor pwsh is installed on the host, so both images are built when the first test needs them (`docker build`, the Dockerfile
on stdin, no build context; Docker's layer cache makes a rerun cheap).

The interpreters are stubs, as S56 asks: `py`, `python3` and `python` are one shell script that appends its name and argv to a `calls`
file and copies its stdin to a `stdin.<name>` file, so the tests compare the exact bytes rather than a hash of them. A stub can exit with
`STUB_EXIT`; a stub `py` can instead report Python 3.8 (print the version line on stderr, exit 1, read nothing: the worst a too old
Python does, where the shipped script exits 0 silently). Only what the launcher exits with and prints on stdout is checked then, not
the interpreter's own stderr. `PATH` holds the stub
folder and a `tools` folder with `dirname`, the only command `hook` itself needs, so a "Python missing" run (no stub folder) has no
interpreter at all. The sandbox is one folder: the install, `HOME` (`.cursor/prompt-logs` for the flags), the stub folders and one call
log per run. The containers get it mounted at `/sandbox`, run without network and as the host user.

The last test runs the real entry script (the whole skill but its tests, and a `python3` link to the interpreter that runs the suite)
under sh and zsh on the host: `sessionStart` syncs, writes no record and prints nothing, and once logging is off the five hooks write
nothing. The suite itself needs Python 3.14 or newer, so the fish and pwsh images carry no Python; what the shell changes is how the
launcher reaches an interpreter, and that is proven there with the stubs.

What the runs showed about `hook.ps1`: it passes the hook input to Python through `$input`, so the bytes arrive as text lines. A UTF-8
BOM and non-ASCII text arrive unchanged and an input that ends in `\\n` (and has no `\\r`) arrives byte for byte, but pwsh appends one `\\n` to an input
that ends without one, turns `\\r\\n` into `\\n`, and replaces bytes that are not UTF-8 (`Shell.stdin_arrives_as` models exactly this and
the tests pin it). The Python hook reads only `conversation_id` out of UTF-8 JSON with an optional BOM (brief line 34, S22), so a record keeps the
text; on Windows, with Cursor's own wrapper and the console code page, the bytes can differ again and S31 (manual) covers that.

The tests skip, naming the reason, for a shell they cannot get: `zsh` not on `PATH`, no `/usr/bin/dirname` on the host, no `docker`
command, no Docker daemon, or a failed image build (the base images are pulled during the build, so it needs network once). A skipped shell is only a skipped sub-test (a skipped whole test where the test has no shell loop), so a run
that must prove all of them sets `SESSION_LOG_REQUIRE_LAUNCHER_SHELLS=1`, which turns every such skip into a failure.

From the repository root (Docker, zsh and Python 3.14 or newer on the host):

    SESSION_LOG_REQUIRE_LAUNCHER_SHELLS=1 python3 -m unittest discover -s skills/session-log/tests -t skills/session-log \\
        -p "test_e2e_cursor_launchers.py" -v

The Docker commands the tests run (`<uid>:<gid>` is the host user, `<sandbox>` a temporary folder, `<argv>` the shell command or the `pwsh`
command above; the `-e` list is `HOME`, `PATH`, `STUB_LOG`, `STUB_EXIT` and `STUB_REPORTS_VERSION` when set, and the `XDG_*_HOME` folders
under `/tmp`; `--platform linux/amd64` is for the pwsh image, which Microsoft publishes for amd64 only, and without it Docker prints a
platform warning on stderr of every run):

    printf 'FROM alpine:3.20\\nRUN apk add --no-cache fish\\n' | docker build --quiet -t session-log-launcher-fish -
    printf 'FROM mcr.microsoft.com/powershell:7.4-ubuntu-22.04\\nENV POWERSHELL_TELEMETRY_OPTOUT=1 DOTNET_CLI_TELEMETRY_OPTOUT=1\\n' \\
        | docker build --quiet --platform linux/amd64 -t session-log-launcher-pwsh -
    docker run --rm -i --name <unique> --network none [--platform linux/amd64] --user <uid>:<gid> -v <sandbox>:/sandbox \\
        -w /sandbox/home -e HOME=/sandbox/home -e PATH=<stub folder>:/sandbox/tools -e STUB_LOG=<log folder> \\
        -e XDG_CACHE_HOME=/tmp/xdg-cache -e XDG_DATA_HOME=/tmp/xdg-data -e XDG_CONFIG_HOME=/tmp/xdg-config \\
        <session-log-launcher-fish | session-log-launcher-pwsh> <argv>
"""
import json
import os
import posixpath
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from tests import test_e2e_skeleton as skeleton
from sessionlog import cursor_records
from tests.cursor_record_files import CONVERSATION, prompt_input, response_input, stop_input, write_record
from tests.test_claude_hooks_config import PLUGIN_FOLDER_WITH_SPACE_AND_APOSTROPHE
from tests.test_cursor_hooks_config import EVENTS, HOOK_TIMEOUT_SECONDS, HOOKS_JSON, PLUGIN_ROOT_VARIABLE

SKILL_DIR = skeleton.SKILL_DIR
SKILL_MD = SKILL_DIR / "SKILL.md"
SKILL_RELATIVE = ("skills", "session-log")
LAUNCHER_NAME = "hook"
POWERSHELL_LAUNCHER_NAME = "hook.ps1"
ENTRY_NAME = "session_log.py"
NOT_INSTALLED_FOLDERS = ("tests", "__pycache__")
CURSOR_FOLDER = ".cursor"
LOG_FOLDER = "prompt-logs"
RECORDS_FOLDER = ".records"
ON_FLAG = ".log_enabled"
OLD_ON_FLAG = ".enabled"
HOME_FOLDER = "home"
TOOLS_FOLDER = "tools"
SANDBOX_MOUNT = "/sandbox"
DIRNAME_COMMAND = "/usr/bin/dirname"  # on macOS, in the alpine image and in the powershell (Ubuntu) image alike
RUN_TIMEOUT_SECONDS = 180  # a container may start slowly (the pwsh image runs emulated on an arm64 host)
BUILD_TIMEOUT_SECONDS = 900
PROBE_TIMEOUT_SECONDS = 30
CLEANUP_TIMEOUT_SECONDS = 30
EXIT_OK = 0
EXIT_BLOCKING = 2  # Cursor blocks the prompt on this code
SILENT_SUCCESS = (EXIT_OK, b"", b"")
BYTE_ORDER_MARK = b"\xef\xbb\xbf"
LINE_END = b"\n"
LARGE_TEXT_LENGTH = 200_000  # characters, far above a 64 KiB pipe buffer
FAR_FUTURE_SECONDS = 2_400_000_000  # after the base time of the record helper: about the year 2099
EXIT_FAILURE = 1
PYTHON_38 = "3.8"
STUB_LOG = "STUB_LOG"
STUB_REPORTS_VERSION = "STUB_REPORTS_VERSION"
STUB_EXIT = "STUB_EXIT"
PY, PYTHON3, PYTHON = "py", "python3", "python"
INTERPRETER_NAMES = (PY, PYTHON3, PYTHON)
# the stub folders hook.ps1 may meet, first interpreter first
INTERPRETER_CHAINS = (INTERPRETER_NAMES, (PYTHON3, PYTHON), (PYTHON,))
SESSION_START, BEFORE_SUBMIT_PROMPT, AFTER_AGENT_RESPONSE, STOP = "sessionStart", "beforeSubmitPrompt", "afterAgentResponse", "stop"
STUB = """#!/bin/sh
name=${0##*/}
{ printf '%s' "$name"; for argument in "$@"; do printf '\\t%s' "$argument"; done; printf '\\n'; } >> "$STUB_LOG/calls"
if [ "$name" = py ] && [ -n "${STUB_REPORTS_VERSION:-}" ]; then
  echo "session-log: Python 3.14+ required (found $STUB_REPORTS_VERSION)" >&2
  exit 1  # EXIT_FAILURE
fi
/bin/cat > "$STUB_LOG/stdin.$name"  # no PATH lookup: the tests choose what PATH holds
exit "${STUB_EXIT:-0}"
"""

REQUIRE_VARIABLE = "SESSION_LOG_REQUIRE_LAUNCHER_SHELLS"
REQUIRE_VALUE = "1"
SH_EXECUTABLE = "/bin/sh"
ON_FLAG_TEXT = "on\n"
SH = "sh"
ZSH = "zsh"
FISH = "fish"
DASH = "dash"
PWSH = "pwsh"
ALL_SHELLS = (SH, ZSH, FISH, DASH, PWSH)
FISH_IMAGE = "session-log-launcher-fish"
PWSH_IMAGE = "session-log-launcher-pwsh"
PWSH_PLATFORM = "linux/amd64"
# image -> (Dockerfile text, platform or None)
IMAGE_BUILDS = {
    FISH_IMAGE: ("FROM alpine:3.20\nRUN apk add --no-cache fish\n", None),
    PWSH_IMAGE: ("FROM mcr.microsoft.com/powershell:7.4-ubuntu-22.04\n"
                 "ENV POWERSHELL_TELEMETRY_OPTOUT=1 DOTNET_CLI_TELEMETRY_OPTOUT=1\n", PWSH_PLATFORM),
}
# shell -> (image, executable inside it, is PowerShell); `dash` is the pwsh image's /bin/sh
CONTAINER_SHELLS = {FISH: (FISH_IMAGE, "/usr/bin/fish", False), DASH: (PWSH_IMAGE, SH_EXECUTABLE, False),
                    PWSH: (PWSH_IMAGE, "/usr/bin/pwsh", True)}
PWSH_ARGUMENTS = ("-NoProfile", "-NonInteractive")
PWSH_HOOK_ARGUMENTS = PWSH_ARGUMENTS + ("-ExecutionPolicy", "Bypass")
CONTAINER_NAME_PREFIX = "session-log-launcher-"
CONTAINER_ENVIRONMENT = {"XDG_CACHE_HOME": "/tmp/xdg-cache", "XDG_DATA_HOME": "/tmp/xdg-data", "XDG_CONFIG_HOME": "/tmp/xdg-config"}


def find_docker_unavailable_reason():
    """Why no container can run here, or an empty string when one can."""
    if shutil.which("docker") is None:
        return "the docker command is not on PATH"
    try:
        probe = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, timeout=PROBE_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as error:
        return "docker cannot be run: %s" % error
    if probe.returncode != 0:
        return "the Docker daemon is not running: " + probe.stderr.decode("utf-8", "replace").strip()
    return ""


def build_image(tag):
    """Builds the image `tag` of `IMAGE_BUILDS`; returns "" or why it failed."""
    dockerfile, platform = IMAGE_BUILDS[tag]
    command = ["docker", "build", "--quiet"] + (["--platform", platform] if platform else []) + ["-t", tag, "-"]
    try:
        build = subprocess.run(command, input=dockerfile.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=BUILD_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as error:
        return "docker build of %s failed: %s" % (tag, error)
    if build.returncode != 0:
        return "docker build of %s failed (it pulls the base image, so it needs network): %s" % (
            tag, build.stderr.decode("utf-8", "replace").strip())
    return ""


_image_problems = {}


def image_problem(tag):
    """Why the image `tag` is not available, or an empty string. Built on first use and remembered for the run."""
    if tag not in _image_problems:
        _image_problems[tag] = find_docker_unavailable_reason() or build_image(tag)
    return _image_problems[tag]


class CallLog:
    """One folder the stubs write to for one launcher run: its path as the process sees it and on the host."""

    def __init__(self, process_path, host_path):
        self.process_path = process_path
        self.host_path = host_path

    def calls(self):
        """The interpreter starts the stubs recorded: one list per start, the stub name first."""
        path = os.path.join(self.host_path, "calls")
        if not os.path.exists(path):
            return []
        return [line.split("\t") for line in Path(path).read_text(encoding="utf-8").splitlines()]

    def stdin_of(self, stub_name):
        return Path(self.host_path, "stdin." + stub_name).read_bytes()


class Sandbox:
    """One folder holding an install, `HOME`, stub folders and call logs. `root` is the folder as the launcher's process sees it: the
    host folder itself, or `/sandbox` in a container."""

    def __init__(self, host_root, root):
        self.host_root = host_root
        self.root = root
        self.counter = 0
        os.makedirs(self.host(HOME_FOLDER))
        os.makedirs(self.host(TOOLS_FOLDER))
        os.symlink(DIRNAME_COMMAND, self.host(TOOLS_FOLDER, "dirname"))
        self.tools = self.process(TOOLS_FOLDER)
        self.home = self.process(HOME_FOLDER)
        self.install_root = self.process(PLUGIN_FOLDER_WITH_SPACE_AND_APOSTROPHE)
        if "'" not in self.install_root or " " not in self.install_root:
            raise AssertionError("the install path must hold a space and an apostrophe (S21, S56): " + self.install_root)
        self.skill = posixpath.join(self.install_root, *SKILL_RELATIVE)
        self.entry = posixpath.join(self.skill, ENTRY_NAME)

    def host(self, *parts):
        return os.path.join(self.host_root, *parts)

    def process(self, *parts):
        return posixpath.join(self.root, *parts)

    @property
    def _skill_on_host(self):
        return Path(self.host(PLUGIN_FOLDER_WITH_SPACE_AND_APOSTROPHE, *SKILL_RELATIVE))

    def install_launchers(self, with_entry=True):
        """The two launchers copied with their modes, and an empty entry script (a stub interpreter never runs it) unless `with_entry` is
        False; replaces an earlier install."""
        shutil.rmtree(self.host(PLUGIN_FOLDER_WITH_SPACE_AND_APOSTROPHE), ignore_errors=True)
        skill = self._skill_on_host
        skill.mkdir(parents=True)
        for name in (LAUNCHER_NAME, POWERSHELL_LAUNCHER_NAME):
            shutil.copy2(SKILL_DIR / name, skill / name)
        if with_entry:
            (skill / ENTRY_NAME).write_text("", encoding="utf-8")

    def install_skill(self):
        """The whole skill but its tests: what a plugin install holds."""
        skill = self._skill_on_host
        skill.parent.mkdir(parents=True)
        shutil.copytree(SKILL_DIR, skill, ignore=shutil.ignore_patterns(*NOT_INSTALLED_FOLDERS))

    def log_folder(self):
        return self.host(HOME_FOLDER, CURSOR_FOLDER, LOG_FOLDER)

    def set_flag(self, name):
        """Makes `name` the only on-flag in the log folder. None removes both flags and leaves a missing folder missing."""
        if name is not None:
            os.makedirs(self.log_folder(), exist_ok=True)
        for flag in (ON_FLAG, OLD_ON_FLAG):
            path = os.path.join(self.log_folder(), flag)
            if flag == name:
                Path(path).write_text(ON_FLAG_TEXT, encoding="utf-8")
            elif os.path.exists(path):
                os.remove(path)

    def stub_folder(self, *names):
        """A folder of stub interpreters named `names`; returns its path as the process sees it."""
        folder = self._new_folder("bin")
        for name in names:
            stub = self.host(folder, name)
            Path(stub).write_text(STUB, encoding="utf-8")
            os.chmod(stub, skeleton.EXECUTABLE_MODE)
        return self.process(folder)

    def call_log(self):
        folder = self._new_folder("log")
        return CallLog(self.process(folder), self.host(folder))

    def _new_folder(self, kind):
        self.counter += 1
        folder = "%s-%d" % (kind, self.counter)
        os.makedirs(self.host(folder))
        return folder


def shipped_command(sandbox, event):
    """The shipped `.cursor-plugin/hooks.json` command of `event` with the install path in place of the plugin-root variable."""
    config = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    entries = config["hooks"][event]
    if len(entries) != 1:
        raise AssertionError("hooks.json must register one command for %s, found %r" % (event, entries))
    return entries[0]["command"].replace(PLUGIN_ROOT_VARIABLE, sandbox.install_root)


def skill_command_line(sandbox, harness, arguments):
    """The command line of SKILL.md (the fenced `python3 "<skill dir>/..."` line) with the skill directory, harness and arguments filled in."""
    lines = [line for line in SKILL_MD.read_text(encoding="utf-8").splitlines() if line.startswith('python3 "<skill dir>/')]
    if len(lines) != 1:
        raise AssertionError("SKILL.md must hold exactly one command line, found %r" % lines)
    return lines[0].replace("<skill dir>", sandbox.skill).replace("<harness>", harness).replace("<arguments>", arguments)


class Shell:
    """A shell that starts the launchers: `sh` and `zsh` on the host, `fish`, `dash` and `pwsh` in a container."""

    def __init__(self, executable, is_powershell, image=None):
        self.executable = executable
        self.is_powershell = is_powershell
        self.image = image

    def new_sandbox(self, case):
        host_root = os.path.realpath(tempfile.mkdtemp())
        case.addCleanup(shutil.rmtree, host_root, ignore_errors=True)
        return Sandbox(host_root, host_root if self.image is None else SANDBOX_MOUNT)

    def hook_argv(self, sandbox, event):
        """What runs for `event`: the shipped command in the shell, or Cursor's wrapper model around `hook.ps1` in pwsh."""
        if self.is_powershell:
            ps1 = posixpath.join(sandbox.skill, POWERSHELL_LAUNCHER_NAME)
            return [self.executable, *PWSH_HOOK_ARGUMENTS, "-Command", '$input | & "%s" cursor %s' % (ps1, event)]
        return [self.executable, "-c", shipped_command(sandbox, event)]

    def line_argv(self, line):
        if self.is_powershell:
            return [self.executable, *PWSH_ARGUMENTS, "-Command", line]
        return [self.executable, "-c", line]

    def run(self, sandbox, argv, path_dirs, stdin=b"", log=None, environment=None, timeout=RUN_TIMEOUT_SECONDS):
        """(exit code, stdout, stderr) of `argv` with `HOME` in the sandbox, `PATH` of `path_dirs` and the stubs logging to `log`."""
        variables = {"HOME": sandbox.home, "PATH": ":".join(path_dirs)}
        if log is not None:
            variables[STUB_LOG] = log.process_path
        variables.update(environment or {})
        if self.image is None:
            result = subprocess.run(argv, env=variables, cwd=sandbox.host(HOME_FOLDER), input=stdin, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=timeout)
            return result.returncode, result.stdout, result.stderr
        return self._run_in_container(sandbox, argv, {**CONTAINER_ENVIRONMENT, **variables}, stdin, timeout)

    def run_hook(self, sandbox, event, path_dirs, stdin=b"", log=None, environment=None, timeout=RUN_TIMEOUT_SECONDS):
        """(exit code, stdout, stderr) of the launcher for `event`."""
        return self.run(sandbox, self.hook_argv(sandbox, event), path_dirs, stdin, log, environment, timeout)

    def _run_in_container(self, sandbox, argv, variables, stdin, timeout):
        name = CONTAINER_NAME_PREFIX + uuid.uuid4().hex
        platform = IMAGE_BUILDS[self.image][1]
        command = ["docker", "run", "--rm", "-i", "--name", name, "--network", "none"] + (["--platform", platform] if platform else [])
        command += ["--user", "%d:%d" % (os.getuid(), os.getgid()), "-v", "%s:%s" % (sandbox.host_root, SANDBOX_MOUNT), "-w", sandbox.home]
        for key, value in variables.items():
            command += ["-e", "%s=%s" % (key, value)]
        try:
            result = subprocess.run(command + [self.image] + argv, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=timeout)
        except subprocess.TimeoutExpired:
            # the timeout kills only the docker client; the container would outlive it
            subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=CLEANUP_TIMEOUT_SECONDS, check=False)
            raise
        return result.returncode, result.stdout, result.stderr

    def interpreter_call(self, sandbox, event, interpreter=None):
        """The one call a launcher makes to the first stub: `hook` -> `python3 <script> hook cursor <event>`, `hook.ps1` ->
        `py -3 <script> hook cursor <event>` while a stub `py` is on `PATH`, else the named `interpreter`."""
        interpreter = interpreter or (PY if self.is_powershell else PYTHON3)
        flag = ["-3"] if interpreter == PY else []
        return [interpreter] + flag + [sandbox.entry, "hook", "cursor", event]

    def stdin_arrives_as(self, payload):
        """The bytes the interpreter gets for `payload` on the launcher's stdin: the same, except under pwsh, which hands the input over as
        text lines (see the module docstring)."""
        if not self.is_powershell:
            return payload
        text = payload.decode("utf-8", "replace").replace("\r\n", "\n").encode("utf-8")
        return text + LINE_END if text and not text.endswith(LINE_END) else text


def hook_payload(text="h\u00e9llo \u0151 \u2265 \u4e16\u754c", terminated=True, with_bom=True, size=None):
    """Cursor hook input: JSON with non-ASCII text, optionally with a UTF-8 BOM, ended by a line end, or padded to a large size."""
    body = json.dumps({"conversation_id": CONVERSATION, "prompt": text * (1 if size is None else size // len(text))}, ensure_ascii=False)
    return (BYTE_ORDER_MARK if with_bom else b"") + body.encode("utf-8") + (LINE_END if terminated else b"")


class LauncherCase(unittest.TestCase):
    """Base of the launcher tests: `shell_named` gives the shell, or skips the sub-test naming the reason (a failure when
    `SESSION_LOG_REQUIRE_LAUNCHER_SHELLS=1`)."""

    def shell_named(self, name):
        reason = self._unavailable_reason(name)
        if reason:
            message = "%s cannot run: %s" % (name, reason)
            if os.environ.get(REQUIRE_VARIABLE) == REQUIRE_VALUE:
                self.fail(message)
            raise unittest.SkipTest(message)
        if name in CONTAINER_SHELLS:
            image, executable, is_powershell = CONTAINER_SHELLS[name]
            return Shell(executable, is_powershell, image)
        return Shell(shutil.which(ZSH) if name == ZSH else SH_EXECUTABLE, False)

    @staticmethod
    def _unavailable_reason(name):
        if name in CONTAINER_SHELLS:
            return image_problem(CONTAINER_SHELLS[name][0])
        if name == ZSH and shutil.which(ZSH) is None:
            return "zsh is not on PATH"
        if not os.path.exists(DIRNAME_COMMAND):
            return "%s is missing on this host" % DIRNAME_COMMAND
        return ""

    def run_silently(self, shell, sandbox, event, path_dirs, payload=b"", environment=None):
        """Runs the launcher for `event`, checks that it exits 0 and prints nothing, and returns the call log of the run."""
        log = sandbox.call_log()
        self.assertEqual(SILENT_SUCCESS, shell.run_hook(sandbox, event, path_dirs, payload, log, environment))
        return log


@unittest.skipIf(sys.platform == "win32", "the launchers are run through POSIX shells, Docker and /usr/bin/dirname")
class LauncherTests(LauncherCase):
    def test_launcher_exits_0_silently_when_script_or_python_missing_under_sh_zsh_fish_pwsh(self):
        for name in ALL_SHELLS:
            with self.subTest(shell=name):
                shell = self.shell_named(name)
                sandbox = shell.new_sandbox(self)
                sandbox.set_flag(ON_FLAG)
                all_stubs = sandbox.stub_folder(*INTERPRETER_NAMES)
                other_stubs = sandbox.stub_folder(PY, PYTHON)  # `hook` needs python3 and tries no other
                cases = [("script missing", False, [all_stubs]), ("python missing", True, []), ("both missing", False, [])]
                if not shell.is_powershell:
                    cases.append(("only py and python on PATH", True, [other_stubs]))
                for label, script_present, stub_dirs in cases:
                    with self.subTest(case=label):
                        sandbox.install_launchers(with_entry=script_present)
                        log = self.run_silently(shell, sandbox, SESSION_START, stub_dirs + [sandbox.tools], hook_payload())
                        self.assertEqual([], log.calls())

    def test_launcher_passes_exact_stdin_bytes_with_either_flag_and_starts_no_interpreter_with_neither(self):
        for name in ALL_SHELLS:
            with self.subTest(shell=name):
                shell = self.shell_named(name)
                sandbox = shell.new_sandbox(self)
                sandbox.install_launchers()
                path_dirs = [sandbox.stub_folder(*INTERPRETER_NAMES), sandbox.tools]
                self.check_flags(shell, sandbox, path_dirs)
                self.check_payloads(shell, sandbox, path_dirs)
                self.check_events(shell, sandbox, path_dirs)
                self.check_failing_interpreter_never_blocks(shell, sandbox)

    def check_flags(self, shell, sandbox, path_dirs):
        """`.log_enabled` or `.enabled`: the interpreter is started once with the input; neither (no `.cursor` folder at all, and an
        empty log folder): no interpreter starts."""
        payload = hook_payload()
        for flag, folder_exists in ((None, False), (ON_FLAG, True), (OLD_ON_FLAG, True), (None, True)):
            with self.subTest(flag=flag, folder_exists=folder_exists):
                sandbox.set_flag(flag)
                self.assertEqual(folder_exists, os.path.isdir(sandbox.log_folder()))
                log = self.run_silently(shell, sandbox, BEFORE_SUBMIT_PROMPT, path_dirs, payload)
                if flag is None:
                    self.assertEqual([], log.calls())
                    continue
                call = shell.interpreter_call(sandbox, BEFORE_SUBMIT_PROMPT)
                self.assertEqual([call], log.calls())
                self.assertEqual(shell.stdin_arrives_as(payload), log.stdin_of(call[0]))

    def check_payloads(self, shell, sandbox, path_dirs):
        payloads = {"terminated": hook_payload(), "unterminated": hook_payload(terminated=False),
                    "no BOM": hook_payload(with_bom=False), "CRLF": hook_payload(terminated=False) + b"\r\n",
                    "not UTF-8": b'{"conversation_id": "c", "prompt": "\xff\xfe"}\n', "empty": b"", "large": hook_payload(size=LARGE_TEXT_LENGTH)}
        sandbox.set_flag(ON_FLAG)
        for label, payload in payloads.items():
            with self.subTest(payload=label):
                log = self.run_silently(shell, sandbox, AFTER_AGENT_RESPONSE, path_dirs, payload)
                call = shell.interpreter_call(sandbox, AFTER_AGENT_RESPONSE)
                self.assertEqual([call], log.calls())
                self.assertEqual(shell.stdin_arrives_as(payload), log.stdin_of(call[0]))

    def check_events(self, shell, sandbox, path_dirs):
        sandbox.set_flag(ON_FLAG)
        for event in EVENTS:
            with self.subTest(event=event):
                log = self.run_silently(shell, sandbox, event, path_dirs, hook_payload())
                self.assertEqual([shell.interpreter_call(sandbox, event)], log.calls())

    def check_failing_interpreter_never_blocks(self, shell, sandbox):
        """Whatever the interpreter returns, the launcher exits 0 (Cursor blocks the prompt on exit code 2): `hook` after its `python3`, and
        `hook.ps1` after each of `py -3`, `python3` and `python`."""
        sandbox.set_flag(ON_FLAG)
        chains = INTERPRETER_CHAINS if shell.is_powershell else (INTERPRETER_NAMES,)
        for chain in chains:
            for code in (EXIT_FAILURE, EXIT_BLOCKING):
                with self.subTest(stubs=chain, interpreter_exit=code):
                    path_dirs = [sandbox.stub_folder(*chain), sandbox.tools]
                    log = self.run_silently(shell, sandbox, STOP, path_dirs, hook_payload(), {STUB_EXIT: str(code)})
                    first = chain[0] if shell.is_powershell else PYTHON3
                    self.assertEqual([shell.interpreter_call(sandbox, STOP, first)], log.calls())

    def test_hook_ps1_picks_py3_first_never_falls_through_even_when_py3_reports_python_38(self):
        shell = self.shell_named(PWSH)
        sandbox = shell.new_sandbox(self)
        sandbox.install_launchers()
        sandbox.set_flag(ON_FLAG)
        event = BEFORE_SUBMIT_PROMPT
        payload = hook_payload()
        for chain in INTERPRETER_CHAINS:
            with self.subTest(stubs=chain):
                log = self.run_silently(shell, sandbox, event, [sandbox.stub_folder(*chain), sandbox.tools], payload)
                self.assertEqual([shell.interpreter_call(sandbox, event, chain[0])], log.calls())  # the rest is never started
                self.assertEqual(shell.stdin_arrives_as(payload), log.stdin_of(chain[0]))
        with self.subTest(stubs="python3 folder ahead of the py folder on PATH"):
            path_dirs = [sandbox.stub_folder(PYTHON3, PYTHON), sandbox.stub_folder(PY), sandbox.tools]
            log = self.run_silently(shell, sandbox, event, path_dirs, payload)
            self.assertEqual([shell.interpreter_call(sandbox, event, PY)], log.calls())  # `py -3` first, whatever the PATH order
        path_dirs = [sandbox.stub_folder(*INTERPRETER_NAMES), sandbox.tools]
        for label, input_bytes in (("input", payload), ("large input the stub never reads", hook_payload(size=LARGE_TEXT_LENGTH))):
            with self.subTest(py="reports Python 3.8", payload=label):
                log = sandbox.call_log()
                code, stdout, _ = shell.run_hook(sandbox, event, path_dirs, input_bytes, log, {STUB_REPORTS_VERSION: PYTHON_38})
                self.assertEqual((EXIT_OK, b""), (code, stdout))  # py's exit code 1 is not the launcher's
                self.assertEqual([shell.interpreter_call(sandbox, event, PY)], log.calls())  # python3 and python never start

    def test_skill_md_command_line_runs_from_path_with_space_and_apostrophe_under_sh_and_pwsh(self):
        invocations = (("cursor", "status"), ("claude", "on"), ("codex", "usage --latest"), ("omp", "usage some-session_1"))
        for name in (SH, PWSH):
            with self.subTest(shell=name):
                shell = self.shell_named(name)
                sandbox = shell.new_sandbox(self)
                sandbox.install_launchers()
                path_dirs = [sandbox.stub_folder(*INTERPRETER_NAMES), sandbox.tools]
                for harness, arguments in invocations:
                    with self.subTest(harness=harness, arguments=arguments):
                        log = sandbox.call_log()
                        line = skill_command_line(sandbox, harness, arguments)
                        self.assertEqual(SILENT_SUCCESS, shell.run(sandbox, shell.line_argv(line), path_dirs, b"", log))
                        # the harness and the arguments reach the stub unchanged, one argument each, the path whole
                        self.assertEqual([[PYTHON3, sandbox.entry, harness] + arguments.split()], log.calls())


def event_of(record_name):
    """The event a record file name (`<arrival_ns>-<event>-<pid>.json`) is for."""
    match = cursor_records.RECORD_FILE_NAME.fullmatch(record_name)
    if match is None:
        raise AssertionError("not a record file name: " + record_name)
    return match.group(2)


def snapshot(folder):
    """Every file under `folder` with its bytes, and every folder: the full state."""
    return {str(path): (path.read_bytes() if path.is_file() else None) for path in sorted(Path(folder).rglob("*"))}


@unittest.skipIf(sys.platform == "win32", "the launchers are run through POSIX shells")
class RealEntryTests(LauncherCase):
    """The shipped launchers in front of the real entry script, on the host (the suite's own interpreter is the only Python 3.14+)."""

    def entry_ready_sandbox(self, shell):
        sandbox = shell.new_sandbox(self)
        sandbox.install_skill()
        os.makedirs(sandbox.host("real-bin"))
        os.symlink(sys.executable, sandbox.host("real-bin", PYTHON3))
        return sandbox, [sandbox.process("real-bin"), sandbox.tools]

    def run_entry(self, sandbox, command):
        result = subprocess.run([sys.executable, sandbox.entry, "cursor", command], env=dict(skeleton.HOSTILE_CONSOLE_ENV, HOME=sandbox.home),
                                cwd=sandbox.host(HOME_FOLDER), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=RUN_TIMEOUT_SECONDS)
        self.assertEqual((EXIT_OK, b""), (result.returncode, result.stderr), msg=result.stdout)

    def run_hook(self, shell, sandbox, path_dirs, event, fields):
        """The launcher as Cursor runs it, with the shipped `timeout` as the limit; the input is `fields` as UTF-8 JSON with a BOM, or empty."""
        payload = b"" if fields is None else BYTE_ORDER_MARK + json.dumps(fields, ensure_ascii=False).encode("utf-8")
        return shell.run_hook(sandbox, event, path_dirs, payload, environment=skeleton.HOSTILE_CONSOLE_ENV, timeout=HOOK_TIMEOUT_SECONDS)

    def test_session_start_runs_sync_writes_no_record_prints_nothing_and_off_hooks_write_nothing(self):
        for name in (SH, ZSH):
            with self.subTest(shell=name):
                shell = self.shell_named(name)
                sandbox, path_dirs = self.entry_ready_sandbox(shell)
                cursor = Path(sandbox.host(HOME_FOLDER, CURSOR_FOLDER))
                log_root = cursor / LOG_FOLDER
                records = log_root / RECORDS_FOLDER
                self.run_entry(sandbox, "on")
                self.assertTrue((log_root / ON_FLAG).is_file())
                for event, fields in ((BEFORE_SUBMIT_PROMPT, prompt_input("g1", "first prompt \u0151")),
                                      (AFTER_AGENT_RESPONSE, response_input("g1", "first reply")), (STOP, stop_input("g1"))):
                    self.assertEqual(SILENT_SUCCESS, self.run_hook(shell, sandbox, path_dirs, event, fields), msg=event)
                recorded = sorted(path.name for path in (records / CONVERSATION).iterdir())
                self.assertEqual(sorted((BEFORE_SUBMIT_PROMPT, AFTER_AGENT_RESPONSE, STOP)), sorted(event_of(name) for name in recorded))
                self.assertEqual([], list(log_root.rglob("*.md")))  # recording alone archives nothing: only a sync does

                before_start = snapshot(records)
                start_fields = {"session_id": "s1", "conversation_id": CONVERSATION, "hook_event_name": SESSION_START}
                self.assertEqual(SILENT_SUCCESS, self.run_hook(shell, sandbox, path_dirs, SESSION_START, start_fields))
                self.assertEqual(before_start, snapshot(records))  # no record of its own, no .invalid folder, nothing else
                [archived] = log_root.rglob("*.md")
                self.assertIn("first prompt \u0151", archived.read_text(encoding="utf-8"))  # the sync ran
                self.assertEqual(1, len(list(log_root.rglob("*.usage.txt"))))

                # control: with logging on, a launcher run for sessionStart archives a record from far in the future
                write_record(str(records), BEFORE_SUBMIT_PROMPT, FAR_FUTURE_SECONDS, prompt_input("g2", "control prompt"))
                self.assertEqual(SILENT_SUCCESS, self.run_hook(shell, sandbox, path_dirs, SESSION_START, start_fields))
                self.assertIn("control prompt", archived.read_text(encoding="utf-8"))

                self.run_entry(sandbox, "off")
                self.assertFalse((log_root / ON_FLAG).exists())
                self.assertEqual([archived], list(log_root.rglob("*.md")))  # existing logs stay
                write_record(str(records), BEFORE_SUBMIT_PROMPT, FAR_FUTURE_SECONDS + 1, prompt_input("g3", "after off prompt"))
                before_hooks = snapshot(sandbox.host(HOME_FOLDER))
                for event in EVENTS:
                    for fields in (prompt_input("g4", "third"), None):
                        self.assertEqual(SILENT_SUCCESS, self.run_hook(shell, sandbox, path_dirs, event, fields), msg=event)
                self.assertEqual(before_hooks, snapshot(sandbox.host(HOME_FOLDER)))  # no record, no log, no state, no folder: "after off prompt" is not archived


if __name__ == "__main__":
    unittest.main()
