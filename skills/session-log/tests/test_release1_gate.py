"""Release 1 gate (S27): the OpenCode plugin loads on OpenCode 2.0.4+ and starts nothing; plus the OpenCode wording of the docs.

`test_isolated_opencode_serve_reports_plugin_active_with_all_skills_and_agent` runs a real `opencode serve` (OpenCode 2.0.4 or
newer, found on `PATH`) in a scratch `HOME` and scratch XDG folders on its own free port, with this repository's
`.opencode-plugin` directory as the only configured plugin (OpenCode resolves a configured directory to its `index`, as the
plugin loader does for a package route; a repository-root path is read as a folder of plugins and loads nothing). Every
`OPENCODE_*` variable of the caller is dropped, so the user's own OpenCode config, data and managed service are never touched.
A `bash` stub first on `PATH` records any call, because the old handlers started the shell installer script with `repair` in the background
(S27). The plugin imports `yaml`, so the repository needs its dependencies installed. The test is skipped when no `opencode`
2.0.4 or newer is installed, unless `RELEASE1_GATE_REQUIRE_OPENCODE=1`, which turns that skip into a failure. Being a `test_*.py`
file it also runs in a full `discover` on a machine with OpenCode, and then costs a few seconds.

The wording tests (`OpenCodeEnabledWording`) pin release 2: OpenCode is a supported identity of SKILL.md and no document refuses it.

From the repository root:

    python3 -m unittest discover -s skills/session-log/tests -t skills/session-log -p "test_release1_gate.py" -v
"""
import base64
import html
import json
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
REPO = SKILL_DIR.parents[1]
SKILL_MD = SKILL_DIR / "SKILL.md"
README = REPO / "README.md"
HANDOUT_EN = REPO / "handout" / "skill-session-log.html"
HANDOUT_HU = REPO / "handout" / "skill-session-log-hu.html"
PACKAGE_JSON = REPO / "package.json"
PLUGIN_DIR = REPO / ".opencode-plugin"
PLUGIN_ENTRY = "./.opencode-plugin/index.js"
PLUGIN_ID = "opencode-goodies"
SESSION_LOG_SKILL = "session-log"
MINIMUM_VERSION = (2, 0, 4)
REQUIRE_OPENCODE_VARIABLE = "RELEASE1_GATE_REQUIRE_OPENCODE"
SERVER_USER = "opencode"
SERVER_PASSWORD = "release1-gate"
LOOPBACK = "127.0.0.1"
VERSION_TIMEOUT_SECONDS = 60
LOAD_TIMEOUT_SECONDS = 90
STOP_TIMEOUT_SECONDS = 20
POLL_SECONDS = 1
REQUEST_TIMEOUT_SECONDS = 10
LOG_TAIL_CHARACTERS = 3000
REJECTED_HTTP_CODES = (400, 401, 403)
SESSION_LOG_ARTIFACTS = ("prompt-logs", ".log_enabled", ".sync.json")
REFUSAL_MARKERS = ("until the next release", "do not run the script", "so do not run it there", "still refuses to run in OpenCode",
                   "not usable through the skill in OpenCode")
HANDOUT_REFUSAL_MARKERS = ("Not usable through the skill", "következő kiadásig", "A skillen keresztül nem használható")
SETTLE_SECONDS = 1
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _installed_version(opencode, environment):
    """The `(major, minor, patch)` of `opencode`, or None when it cannot be run or read."""
    try:
        result = subprocess.run(
            [opencode, "--version"], capture_output=True, text=True, timeout=VERSION_TIMEOUT_SECONDS, env=environment,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    match = re.search(r"(?<!\d)(\d+)\.(\d+)\.(\d+)", result.stdout + result.stderr)
    return tuple(int(part) for part in match.groups()) if match else None


def _free_port():
    with socket.socket() as probe:
        probe.bind((LOOPBACK, 0))
        return probe.getsockname()[1]


def _front_matter_name(path, fallback):
    """The `name:` of a Markdown front matter (single word, as in this repository), else `fallback`."""
    match = re.match(r"---\n(.*?)\n---", path.read_text(encoding="utf-8"), re.DOTALL)
    named = re.search(r"^name:\s*(\S+)", match.group(1), re.MULTILINE) if match else None
    return named.group(1).strip("\"'") if named else fallback


def _scratch_environment(scratch):
    stub_bin = str(scratch / "bin")
    kept = {k: v for k, v in os.environ.items() if not k.startswith(("OPENCODE", "XDG_"))}
    kept.update(HOME=str(scratch / "home"), OPENCODE_SERVER_PASSWORD=SERVER_PASSWORD)
    kept["PATH"] = stub_bin + os.pathsep + kept.get("PATH", "")
    return kept


def _make_scratch(scratch):
    """Default OpenCode folders under a scratch HOME (no XDG variable: the old handlers refused a relocated root and hid a regression)."""
    home = scratch / "home"
    for folder in (home / ".config" / "opencode", home / ".local" / "share", home / ".local" / "state", home / ".cache", scratch / "work", scratch / "bin"):
        folder.mkdir(parents=True)
    (home / ".config" / "opencode" / "opencode.json").write_text(json.dumps({"plugins": [str(PLUGIN_DIR)]}), encoding="utf-8")


def _write_bash_stub(scratch):
    """A `bash` that records its call in `bash-calls.txt` and does nothing; returns that file."""
    calls = scratch / "bash-calls.txt"
    stub = scratch / "bin" / "bash"
    stub.write_text('#!/bin/sh\necho called "$@" >> "%s"\nexit 0\n' % calls, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return calls


class IsolatedOpenCodeServe(unittest.TestCase):
    def test_isolated_opencode_serve_reports_plugin_active_with_all_skills_and_agent(self):
        opencode = shutil.which("opencode")
        with tempfile.TemporaryDirectory() as scratch_name:
            scratch = Path(scratch_name).resolve()
            _make_scratch(scratch)
            environment = _scratch_environment(scratch)
            self._require_opencode(opencode, environment)
            bash_calls = _write_bash_stub(scratch)
            subprocess.run(["bash", "-c", ":"], env=environment, check=True)
            self.assertTrue(bash_calls.exists(), "the recording bash stub is not reached through PATH")
            bash_calls.unlink()
            port = _free_port()
            log_path = scratch / "serve.log"
            with open(log_path, "wb") as log:
                server = subprocess.Popen(
                    [opencode, "serve", "--hostname", LOOPBACK, "--port", str(port)], cwd=scratch / "work",
                    env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            try:
                try:
                    plugin, skills, agents, commands = self._settled(server, port)
                    self._assert_gate(scratch, bash_calls, plugin, skills, agents, commands)
                except AssertionError as failure:
                    tail = log_path.read_text(encoding="utf-8", errors="replace")[-LOG_TAIL_CHARACTERS:]
                    raise AssertionError("%s\nserver log tail:\n%s" % (failure, tail)) from None
            finally:
                self._stop(server)

    def _assert_gate(self, scratch, bash_calls, plugin, skills, agents, commands):
        self.assertEqual("active", plugin["state"]["status"], plugin)
        expected_skills = {path.parent.name: path for path in (REPO / "skills").glob("*/SKILL.md")}
        self.assertIn(SESSION_LOG_SKILL, expected_skills)
        self.assertLessEqual(set(expected_skills), set(skills))
        for skill_id, path in expected_skills.items():
            self.assertEqual(str(path), skills[skill_id], "skill %s is not registered with its path" % skill_id)
        expected_agents = {_front_matter_name(path, path.stem) for path in (REPO / "agents").glob("*.md")}
        self.assertTrue(expected_agents)
        self.assertLessEqual(expected_agents, agents)
        self.assertTrue(commands, "no command listed at all, so the next check would prove nothing")
        self.assertFalse([name for name in commands if SESSION_LOG_SKILL in name], commands)
        time.sleep(SETTLE_SECONDS)
        started = bash_calls.read_text(encoding="utf-8") if bash_calls.exists() else ""
        self.assertEqual("", started, "a background bash started")
        written = [p for p in scratch.rglob("*") if p.name in SESSION_LOG_ARTIFACTS]
        self.assertEqual([], written, "session-log files appeared")

    def test_package_json_exports_files_and_dependencies_cover_what_the_plugin_loads(self):
        package = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
        self.assertEqual(PLUGIN_ENTRY, package["exports"])
        for shipped in (PLUGIN_ENTRY.removeprefix("./"), "skills/", "agents/"):
            self.assertIn(shipped, package["files"])
        self.assertIn("yaml", package["dependencies"])

    def _require_opencode(self, opencode, environment):
        if opencode is None:
            cause = "no opencode on PATH"
        else:
            version = _installed_version(opencode, environment)
            if version is None:
                cause = "the version of %s cannot be read" % opencode
            elif version < MINIMUM_VERSION:
                cause = "opencode %s is older than %s" % (version, MINIMUM_VERSION)
            else:
                return
        if os.environ.get(REQUIRE_OPENCODE_VARIABLE) == "1":
            self.fail(cause)
        self.skipTest(cause)

    @staticmethod
    def _stop(server):
        try:
            os.killpg(server.pid, signal.SIGTERM)
            server.wait(timeout=STOP_TIMEOUT_SECONDS)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass
        try:
            os.killpg(server.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        server.wait()

    @staticmethod
    def _get(port, path):
        token = base64.b64encode(("%s:%s" % (SERVER_USER, SERVER_PASSWORD)).encode()).decode()
        request = urllib.request.Request("http://%s:%d%s" % (LOOPBACK, port, path), headers={"Authorization": "Basic " + token})
        with NO_PROXY.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.load(response)["data"]

    def _settled(self, server, port):
        """Poll until the plugin is listed; return its entry (read last) plus skill paths by id, agent ids and command names."""
        deadline = time.monotonic() + LOAD_TIMEOUT_SECONDS
        last_error = None
        listing = None
        while time.monotonic() < deadline:
            self.assertIsNone(server.poll(), "opencode serve exited early with code %s" % server.returncode)
            try:
                listing = self._get(port, "/api/plugin")
                if any(p.get("id") == PLUGIN_ID for p in listing):
                    skills = {s["id"]: s["path"] for s in self._get(port, "/api/skill")}
                    agents = {a["id"] for a in self._get(port, "/api/agent")}
                    commands = [c["name"] for c in self._get(port, "/api/command")]
                    final = [p for p in self._get(port, "/api/plugin") if p.get("id") == PLUGIN_ID]
                    if final:
                        return final[0], skills, agents, commands
            except urllib.error.HTTPError as failure:
                if failure.code in REJECTED_HTTP_CODES:
                    raise AssertionError("OpenCode answered HTTP %d for %s" % (failure.code, failure.url)) from failure
                last_error = failure
            except (urllib.error.URLError, ConnectionError, OSError, ValueError, KeyError) as failure:
                last_error = failure
            time.sleep(POLL_SECONDS)
        self.fail(
            "plugin %s never appeared within %ds; last error: %r; last plugin listing: %s"
            % (PLUGIN_ID, LOAD_TIMEOUT_SECONDS, last_error, json.dumps(listing)[:LOG_TAIL_CHARACTERS]),
        )


class OpenCodeEnabledWording(unittest.TestCase):
    """Release 2 logs OpenCode sessions (S30, S66 replaced): no document may still tell the model or the reader to refuse there."""

    def test_skill_md_lists_opencode_as_a_supported_identity_and_has_no_refusal(self):
        skill = SKILL_MD.read_text(encoding="utf-8")
        self.assertIn("`claude`, `codex`, `cursor`, `opencode` (OpenCode V2), and `omp`", skill)
        self.assertIn('python3 "<skill dir>/session_log.py" <harness> <arguments>', skill)
        for refusal in REFUSAL_MARKERS:
            self.assertNotIn(refusal.lower(), skill.lower(), "SKILL.md")
        step_one = [line for line in skill.splitlines() if line.startswith("1. ")]
        self.assertEqual(1, len(step_one))
        self.assertNotRegex(step_one[0], r"(?i)opencode")

    def test_readme_and_both_handouts_do_not_say_opencode_is_refused(self):
        for path in (README, HANDOUT_EN, HANDOUT_HU):
            text = path.read_text(encoding="utf-8")
            for refusal in REFUSAL_MARKERS + HANDOUT_REFUSAL_MARKERS:
                self.assertNotIn(refusal.lower(), text.lower(), "%s says %r" % (path.name, refusal))

    def test_readme_still_says_the_plugin_registers_no_session_log_command(self):
        self.assertIn("registers no `/session-log` command", README.read_text(encoding="utf-8"))


class KeptDeviationsDocumented(unittest.TestCase):
    """Facts of the new release that differ from the old one and are kept: each must be written where a user looks."""

    ENGLISH_FACTS = ("est. used token", "switched:", "sub-agent finished", "**Session file:**", "**Resume:**", "--- separators",
                     "older than 3.14", ".log_enabled", "transcript path", "final context", "user_email", "0600", "prunes",
                     "session-log-old", "may still load", "SESSION_LOG_OWNER=universal-session-log", "between the plugin update")
    HUNGARIAN_FACTS = ("est. used token", "switched:", "sub-agent finished", "**Session file:**", "**Resume:**", "--- elválasztók",
                       "3.14-nél régebbi", ".log_enabled", "átirat-útvonalat", "final context", "user_email", "0600", "nem törli",
                       "session-log-old", "betölti a rendszer", "SESSION_LOG_OWNER=universal-session-log", "első új")

    @staticmethod
    def _plain(path):
        """The text of a document without Markdown code ticks and HTML tags, so one fact string fits README and handouts alike."""
        return html.unescape(re.sub(r"<[^>]+>", "", path.read_text(encoding="utf-8")).replace("`", ""))

    def test_each_kept_deviation_is_written_in_the_readme_and_in_the_matching_handout(self):
        for path, facts in ((README, self.ENGLISH_FACTS), (HANDOUT_EN, self.ENGLISH_FACTS), (HANDOUT_HU, self.HUNGARIAN_FACTS)):
            text = self._plain(path)
            for fact in facts:
                self.assertIn(fact, text, "%s does not mention %r" % (path.name, fact))


if __name__ == "__main__":
    unittest.main()
