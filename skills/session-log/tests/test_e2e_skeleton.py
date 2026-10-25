"""E2E of the walking skeleton (S1-S4): `session_log.py claude on|off|status|usage` run as real subprocesses in a sandbox `HOME`.

The command runs for real: real clock, real file system, real interpreter, a Claude transcript tree built under the sandbox.
Only the environment is shaped: a cp1252 stdio and the C locale (the entry must still write UTF-8, S54), a fixed UTC+5:30 time
zone (so a local time is never mistaken for UTC) and Python-named stubs first on `PATH` that record any call (S1: no Python
probe off Windows). Most transcripts carry year-2099 times and a 2099 file time, so they count as activity after the real
on-time; the old session carries a 2020 file time and the stale one a fresh file time but 2020 prompts, and neither may be
archived; the current session carries the real time and proves the gate is the stored on-time, not the clock of the sync.

`python3` must resolve to Python 3.14 or newer (the entry file's floor).

On macOS, from the repository root:

    python3 -m unittest discover -s skills/session-log/tests -t skills/session-log -p "test_e2e_skeleton.py" -v

On Linux in Docker, from the repository root (the Linux floor image; the repository is mounted read-only, nothing is written to it):

    docker run --rm -v "$PWD":/repo:ro -w /repo/skills/session-log -e PYTHONDONTWRITEBYTECODE=1 python:3.14-slim \\
        python3 -m unittest discover -s tests -t . -p "test_e2e_skeleton.py" -v
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ENTRY = SKILL_DIR / "session_log.py"
CLAUDE_FOLDER = ".claude"
PROJECTS_FOLDER = "projects"
LOG_FOLDER = "prompt-logs"
ON_FLAG = ".log_enabled"
SYNC_STATE = ".sync.json"
SYNC_LOCK = ".sync.lock"
LOG_SUFFIX = ".md"
REPORT_SUFFIX = ".usage.txt"
PROJECT_FOLDER = "proj"
ACTIVE_PROMPT_AT = "2099-06-01T10:00:00.000Z"
NEWER_PROMPT_AT = "2099-06-02T10:00:00.000Z"
NEWEST_PROMPT_AT = "2099-06-04T10:00:00.000Z"
EARLIEST_PROMPT_AT = "2099-05-01T10:00:00.000Z"
OLD_PROMPT_AT = "2020-01-01T10:00:00.000Z"
REPLY_DELAY = timedelta(seconds=1)
FUTURE_FILE_TIME = datetime(2099, 6, 5, tzinfo=timezone.utc).timestamp()
OLD_FILE_TIME = datetime(2020, 1, 2, tzinfo=timezone.utc).timestamp()
# Session ids are chosen so that neither id order nor sync order equals prompt order, and the newest prompt sits in the middle of
# the id order: `usage` must rank by prompt time. Ids of these tests never hold `_` (a saved file name is `session_<stamp>_<id>`).
ACTIVE_SESSION = "active1"
NEWER_SESSION = "a-newer2"
EARLIEST_SESSION = "z-earliest3"
NEWEST_SESSION = "m-newest4"
CURRENT_SESSION = "current5"
OLD_SESSION = "old0"
STALE_SESSION = "stale6"
BROKEN_SESSION = "broken9"
NON_ASCII_MARK = "\u2265"
PROMPT_TEXT = "needs %s 3.14 for \u0151" % NON_ASCII_MARK
MODEL = "claude-opus-5-5"
TOKENS_PER_REQUEST = 1000
CLOCK_TOLERANCE_SECONDS = 0.1  # the test and the child read the same machine clock
SUBPROCESS_TIMEOUT = 60
SUBPROCESS_ENV_NAMES = ("PATH",)
TEST_TIME_ZONE = "XXX-5:30"  # POSIX form: UTC+5:30, no tzdata needed
TEST_ZONE_OFFSET = "+0530"
# a hostile console: the entry must print and write UTF-8 regardless (S54)
HOSTILE_CONSOLE_ENV = {"TZ": TEST_TIME_ZONE, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0", "LC_ALL": "C", "LANG": "C",
                       "PYTHONDONTWRITEBYTECODE": "1"}
UNREADABLE_HEADING = "Unreadable or skipped items:"
UNDECODABLE_REASON = "cannot be parsed"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S %z"
EXECUTABLE_MODE = 0o755
PYTHON_COMMANDS = ("python3", "python", "py", "python3.14")
PYTHON_CALLED_MARKER = "python.called"
PYTHON_STUB = "#!/bin/sh\necho called >> '%s'\nexit 1\n"


def shown(moment):
    """The local time text a command prints for a time."""
    return moment.astimezone().strftime(TIME_FORMAT)


def parse_time(timestamp):
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))


@unittest.skipIf(sys.platform == "win32", "the Python stubs are POSIX shell scripts; the Windows probe is S24")
class SkeletonCase(unittest.TestCase):
    def setUp(self):
        self.home = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        self.use_test_time_zone()
        self.project = os.path.join(self.home, PROJECT_FOLDER)
        os.makedirs(self.project)
        self.claude = os.path.join(self.home, CLAUDE_FOLDER)
        self.log_root = os.path.join(self.claude, LOG_FOLDER)
        self.on_flag = os.path.join(self.log_root, ON_FLAG)
        self.sync_state = os.path.join(self.log_root, SYNC_STATE)
        self.stub_dir = os.path.join(self.home, "stub-bin")
        self.python_called = os.path.join(self.stub_dir, PYTHON_CALLED_MARKER)
        self.install_python_stubs()

    def use_test_time_zone(self):
        """Sets the zone of this process (`shown` reads it) for the test; the child gets the same one in `run_command`."""
        self.addCleanup(self.restore_time_zone, os.environ.get("TZ"))
        os.environ["TZ"] = TEST_TIME_ZONE
        time.tzset()

    @staticmethod
    def restore_time_zone(previous):
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()

    def install_python_stubs(self):
        os.makedirs(self.stub_dir)
        for name in PYTHON_COMMANDS:
            stub = os.path.join(self.stub_dir, name)
            Path(stub).write_text(PYTHON_STUB % self.python_called, encoding="utf-8")
            os.chmod(stub, EXECUTABLE_MODE)
        subprocess.run([os.path.join(self.stub_dir, PYTHON_COMMANDS[0])], timeout=SUBPROCESS_TIMEOUT)
        self.assertTrue(os.path.exists(self.python_called), "the Python stub cannot run here, so it proves nothing")
        os.remove(self.python_called)

    def run_command(self, *arguments):
        env = {name: os.environ[name] for name in SUBPROCESS_ENV_NAMES if name in os.environ}
        env["PATH"] = self.stub_dir + os.pathsep + env.get("PATH", "")
        env.update(HOSTILE_CONSOLE_ENV, HOME=self.home)
        result = subprocess.run([sys.executable, str(ENTRY), "claude"] + list(arguments), env=env, cwd=self.project,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=SUBPROCESS_TIMEOUT)
        return result.returncode, result.stdout.decode("utf-8"), result.stderr.decode("utf-8")

    def run_ok(self, *arguments):
        code, stdout, stderr = self.run_command(*arguments)
        self.assertEqual((0, ""), (code, stderr), msg=stdout)
        return stdout

    def project_file(self, session_id):
        return os.path.join(self.claude, PROJECTS_FOLDER, "-" + PROJECT_FOLDER, session_id + ".jsonl")

    def write_transcript_file(self, session_id, content, file_time=None):
        """`file_time` None keeps the real modification time."""
        path = self.project_file(session_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        Path(path).write_bytes(content)
        if file_time is not None:
            os.utime(path, (file_time, file_time))
        return path

    def write_session(self, session_id, prompt_at=ACTIVE_PROMPT_AT, file_time=FUTURE_FILE_TIME):
        """One prompt at `prompt_at` and its reply a second later; `file_time` None keeps the real modification time."""
        reply_at = (parse_time(prompt_at) + REPLY_DELAY).isoformat()
        records = [
            {"type": "user", "promptSource": "typed", "cwd": self.project, "timestamp": prompt_at,
             "message": {"role": "user", "content": PROMPT_TEXT}},
            {"type": "assistant", "timestamp": reply_at,
             "message": {"id": "m-" + session_id, "model": MODEL, "content": [{"type": "text", "text": "reply"}],
                         "usage": {"input_tokens": TOKENS_PER_REQUEST, "output_tokens": TOKENS_PER_REQUEST}}},
        ]
        text = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
        self.write_transcript_file(session_id, text.encode("utf-8"), file_time)

    def write_current_session(self, session_id):
        """A session active right now: real prompt time and real file time."""
        self.write_session(session_id, datetime.now(timezone.utc).isoformat(), file_time=None)

    def write_undecodable_session(self, session_id):
        return self.write_transcript_file(session_id, b"\xff\xfe not utf-8 \xff\n", FUTURE_FILE_TIME)

    def saved_files(self, suffix):
        """Session id -> text of the one saved file with `suffix`, read as UTF-8; two files of one session fail."""
        saved = {}
        for path in Path(self.log_root).rglob("*" + suffix):
            stem = path.name[:-len(suffix)]
            self.assertIn("_", stem, "unexpected saved file name %s" % path.name)
            session_id = stem.rsplit("_", 1)[1]
            self.assertNotIn(session_id, saved, "two %s files for session %s" % (suffix, session_id))
            saved[session_id] = path.read_bytes().decode("utf-8")
        return saved

    def saved_reports(self):
        return self.saved_files(REPORT_SUFFIX)

    def saved_logs(self):
        return self.saved_files(LOG_SUFFIX)

    def log_tree(self):
        """Every file under the log folder except the sync lock, with its bytes: the full state of the logs."""
        return {str(path): path.read_bytes() for path in sorted(Path(self.log_root).rglob("*"))
                if path.is_file() and path.name != SYNC_LOCK}

    def stored_on_time(self):
        return datetime.fromisoformat(Path(self.on_flag).read_text(encoding="utf-8").strip())

    def stored_sync_state(self):
        return json.loads(Path(self.sync_state).read_text(encoding="utf-8"))

    def assert_python_never_called(self):
        self.assertFalse(os.path.exists(self.python_called), "a command ran a Python interpreter by name (a probe)")

    def assert_between(self, before, after, moment):
        self.assertTrue(before - CLOCK_TOLERANCE_SECONDS <= moment.timestamp() <= after + CLOCK_TOLERANCE_SECONDS,
                        "%s is not between %s and %s" % (moment, before, after))


class OnTests(SkeletonCase):
    def test_on_records_time_syncs_and_prints_log_folder(self):
        self.write_session(ACTIVE_SESSION)
        self.write_session(OLD_SESSION, OLD_PROMPT_AT, OLD_FILE_TIME)
        self.write_session(STALE_SESSION, OLD_PROMPT_AT, file_time=None)
        before = time.time()
        stdout = self.run_ok("on")
        after = time.time()

        lines = stdout.splitlines()
        on_time = self.stored_on_time()
        self.assert_between(before, after, on_time)
        self.assertEqual("Session logging is on since %s." % shown(on_time), lines[0])
        self.assertTrue(lines[0].endswith("%s." % TEST_ZONE_OFFSET), lines[0])  # local time, not UTC
        self.assertEqual(["Logs are saved under %s." % self.log_root], lines[1:])  # nothing else: no probe, no leftovers
        self.assert_python_never_called()  # the Python probe runs only on Windows (S24)

        self.assertEqual({ACTIVE_SESSION}, set(self.saved_reports()))  # `on` synced the session active since the on-time ...
        self.assertEqual({ACTIVE_SESSION}, set(self.saved_logs()))  # ... and neither the old nor the stale one
        self.assertIn(PROMPT_TEXT, self.saved_logs()[ACTIVE_SESSION])

    def test_on_and_status_name_an_old_release_leftover_with_its_fix_and_delete_nothing(self):
        old_script = os.path.join(self.claude, "scripts", "prompt_log_save.sh")
        os.makedirs(os.path.dirname(old_script))
        with open(old_script, "w", encoding="utf-8") as handle:
            handle.write("old")
        kept = os.path.join(self.claude, "session-log-old", "scripts", "prompt_log_save.sh")
        expected = ("  %s: move it outside the skills folders: `mkdir -p %s && mv %s %s` "
                    "(a copy moved inside a skills folder may still load)"
                    % (old_script, shlex.quote(os.path.dirname(kept)), shlex.quote(old_script), shlex.quote(kept)))

        for command in ("on", "status"):
            lines = self.run_ok(command).splitlines()
            self.assertEqual(["Old-release leftovers (not deleted):", expected], lines[-2:], command)
        self.assertTrue(os.path.exists(old_script))


class OffTests(SkeletonCase):
    def test_off_syncs_once_deletes_log_enabled_keeps_logs_and_nothing_syncs_after(self):
        self.run_ok("on")
        self.write_session(ACTIVE_SESSION)
        self.assertEqual({}, self.saved_reports())  # nothing synced since `on`

        stdout = self.run_ok("off")
        self.assertEqual("Session logging is off.\nLogs under %s are kept.\n" % self.log_root, stdout)
        self.assertFalse(os.path.exists(self.on_flag))
        self.assertEqual({ACTIVE_SESSION}, set(self.saved_reports()))  # `off` synced before turning off ...
        self.assertIn(PROMPT_TEXT, self.saved_logs()[ACTIVE_SESSION])  # ... and the logs stay
        kept = self.log_tree()
        synced_at = self.stored_sync_state()["lastSyncAt"]

        # nothing syncs while off, whichever command runs
        self.write_session(NEWER_SESSION, NEWER_PROMPT_AT)
        self.assertEqual(["Session logging is off.", "Log root: %s" % self.log_root, "Last sync: %s" % shown(parse_time(synced_at)),
                          "Archived sessions: 1"], self.run_ok("status").splitlines())
        self.assertEqual(self.saved_reports()[ACTIVE_SESSION], self.run_ok("usage", ACTIVE_SESSION))  # the saved report of an archived one
        live = self.run_ok("usage")  # the newest prompt of the project is the never-archived session: computed live, saved nowhere
        self.assertIn(NEWER_SESSION, live)
        self.assertEqual(kept, self.log_tree())
        self.assertEqual("Session logging is already off.\nLogs under %s are kept.\n" % self.log_root, self.run_ok("off"))
        self.assertEqual(kept, self.log_tree())

        # only turning logging on again syncs again
        self.run_ok("on")
        self.assertEqual({ACTIVE_SESSION, NEWER_SESSION}, set(self.saved_reports()))
        self.assertEqual(live, self.saved_reports()[NEWER_SESSION])  # the live report is the one a sync saves
        self.assert_python_never_called()


class StatusTests(SkeletonCase):
    def test_status_prints_state_folder_last_sync_count_and_unreadable_file(self):
        self.run_ok("on")
        self.write_session(ACTIVE_SESSION)
        self.write_current_session(CURRENT_SESSION)  # active after the on-time but before this sync
        broken = self.write_undecodable_session(BROKEN_SESSION)

        before = time.time()
        lines = self.run_ok("status").splitlines()
        after = time.time()

        self.assertEqual("Session logging is on since %s." % shown(self.stored_on_time()), lines[0])
        self.assertEqual("Log root: %s" % self.log_root, lines[1])
        synced_at = parse_time(self.stored_sync_state()["lastSyncAt"])
        self.assert_between(before, after, synced_at)
        self.assertEqual("Last sync: %s" % shown(synced_at), lines[2])
        self.assertEqual("Archived sessions: 2", lines[3])
        self.assertEqual([UNREADABLE_HEADING, "  %s: %s" % (broken, UNDECODABLE_REASON)], lines[4:])
        self.assertEqual({ACTIVE_SESSION, CURRENT_SESSION}, set(self.saved_reports()))
        self.assert_python_never_called()


class UsageTests(SkeletonCase):
    def test_usage_no_argument_equals_latest_and_equals_saved_usage_txt(self):
        self.run_ok("on")
        self.write_session(ACTIVE_SESSION)
        self.write_session(NEWER_SESSION, NEWER_PROMPT_AT)
        self.write_session(EARLIEST_SESSION, EARLIEST_PROMPT_AT)

        plain = self.run_ok("usage")  # syncs, then prints the session with the newest prompt
        saved = self.saved_reports()
        self.assertEqual({ACTIVE_SESSION, NEWER_SESSION, EARLIEST_SESSION}, set(saved))
        self.assertEqual(saved[NEWER_SESSION], plain)
        self.assertIn(NON_ASCII_MARK, plain)

        self.write_session(NEWEST_SESSION, NEWEST_PROMPT_AT)
        latest = self.run_ok("usage", "--latest")  # syncs too
        self.assertEqual(self.saved_reports()[NEWEST_SESSION], latest)
        self.assertEqual(latest, self.run_ok("usage"))  # no argument is --latest


if __name__ == "__main__":
    unittest.main()
