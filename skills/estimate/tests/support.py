"""Shared fixtures of the estimate tests: a temporary project that is a real git repository (real `git`, identity passed with `-c`),
an in-process run of the entry script with injected boundaries (today's date, the local-time conversion, `which`), and a fake
`session_log.py` that a real subprocess runs: it answers each argument list with a canned C1 reply and records its calls."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRY_PATH = os.path.join(SKILL_DIR, "estimate.py")
SCALE_SCHEMA_PATH = os.path.join(SKILL_DIR, "estimation.schema.json")
FEATURE_SCHEMA_PATH = os.path.join(SKILL_DIR, "feature.schema.json")
DATA_FOLDER = ".harness-goodies"
SCALE_PATH = DATA_FOLDER + "/estimation.json"
FEATURES_PATH = DATA_FOLDER + "/estimations"
SEEDED_DAY = "2026-10-01"
GIT_IDENTITY = ("-c", "user.name=Estimate Test", "-c", "user.email=estimate@example.invalid")
TODAY = date(2026, 11, 4)
DEFAULT_SCALE = [0, 0.5, 1, 2, 3, 5, 8, 13, 20, 40, 100, 200, 500, 1000]
DEFAULT_ANCHOR = {"sp": 13, "hours": 75}
EXIT_OK = 0
EXIT_RUNTIME_ERROR = 1
EXIT_BAD_ARGUMENTS = 2

# The fake session-log: `responses.json` next to it maps the joined arguments to {"code", "stdout", "stderr"}; an argument list
# with no reply fails like a real error. Every call is appended to `calls.jsonl` with its working folder.
FAKE_SESSION_LOG = '''import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "calls.jsonl"), "a", encoding="utf-8") as calls:
    calls.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd()}) + "\\n")
with open(os.path.join(here, "responses.json"), encoding="utf-8") as handle:
    reply = json.load(handle).get(" ".join(sys.argv[1:]))
if reply is None:
    sys.stderr.write("session-log: no canned reply for %s\\n" % " ".join(sys.argv[1:]))
    sys.exit(1)
sys.stdout.write(reply.get("stdout", ""))
sys.stderr.write(reply.get("stderr", ""))
sys.exit(reply.get("code", 0))
'''


def git(repo, *arguments, env=None):
    """Runs real `git` in `repo` with the test identity and returns its stdout."""
    return subprocess.run(["git", *GIT_IDENTITY, *arguments], cwd=repo, env=env, check=True, capture_output=True,
                          text=True).stdout


class Result:
    def __init__(self, code, out, err):
        self.code = code
        self.out = out
        self.err = err

    @property
    def json(self):
        return json.loads(self.out)


class FakeSessionLog:
    """A `session_log.py` stand-in in its own folder."""

    def __init__(self, folder):
        self.folder = folder
        self.path = os.path.join(folder, "session_log.py")
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(FAKE_SESSION_LOG)
        self.responses = {}
        self._save()

    def _save(self):
        with open(os.path.join(self.folder, "responses.json"), "w", encoding="utf-8") as handle:
            json.dump(self.responses, handle)

    def reply(self, arguments, payload=None, code=EXIT_OK, stderr=""):
        """Answers `arguments` (a string) with `payload` as JSON on stdout, or fails with `code` and `stderr`."""
        self.responses[arguments] = {"code": code, "stdout": "" if payload is None else json.dumps(payload), "stderr": stderr}
        self._save()

    def calls(self):
        path = os.path.join(self.folder, "calls.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle]


def tokens_of(total):
    """C1 tokens split so that `total` = input + output + cache read + cache write; reasoning not recorded."""
    quarter = total // 4
    return {"input": quarter, "output": quarter, "reasoning": None, "cacheRead": quarter, "cacheWrite": total - 3 * quarter,
            "total": total}


MAIN_MODEL = "main-model"
AGENT_MODEL = "agent-model"


def usage_payload(harness, session_id, seconds, total=1000, cost=1.25, cost_complete=True, path=None, log=None, prompts=None,
                  sub_agents=None, models=(MAIN_MODEL,)):
    """A C1 `usage --json` reply; `prompts` and `sub_agents` are its rows (null: the session's prompts cannot be read)."""
    return {"harness": harness, "id": session_id, "projectDir": "/abs/project", "path": path, "log": log,
            "agentTimeSeconds": seconds, "tokens": tokens_of(total), "costUsd": cost, "costComplete": cost_complete,
            "models": list(models), "prompts": prompts, "subAgents": sub_agents}


def prompt_row(at, text, seconds=60, total=100, cost=0.5, complete=True, models=(MAIN_MODEL,)):
    """One C1 prompt row submitted at `at` (UTC ISO) and answered `seconds` later."""
    reply_at = (datetime.fromisoformat(at) + timedelta(seconds=seconds)).isoformat()
    return {"at": at, "replyAt": reply_at, "text": text, "agentTimeSeconds": seconds, "tokens": tokens_of(total), "costUsd": cost,
            "costComplete": complete, "models": list(models)}


def agent_row(started_at, total=100, cost=0.5, complete=True, agent_id="agent", depth=0, models=(AGENT_MODEL,)):
    """One C1 sub-agent row started at `started_at` (UTC ISO)."""
    return {"id": agent_id, "type": "task", "startedAt": started_at, "depth": depth, "tokens": tokens_of(total), "costUsd": cost,
            "costComplete": complete, "models": list(models)}


def list_entry(harness, session_id, started, last_activity, prompts, path=None, log=None):
    """One session of a C1 `list --json` reply."""
    return {"harness": harness, "id": session_id, "projectDir": "/abs/project", "startedAt": started,
            "lastActivityAt": last_activity, "path": path, "log": log, "prompts": prompts}


def listed_with_rows(harness, session_id, rows, path=None, log=None):
    """The `list --json` entry of a session whose prompt rows are `rows`: it starts at the first and ends at the last reply."""
    return list_entry(harness, session_id, rows[0]["at"], rows[-1]["replyAt"], [row["text"] for row in rows], path, log)


class ProjectTestCase(unittest.TestCase):
    """A fresh git repository as the project root (real path, so macOS `/var` symlinks cannot split it)."""

    def setUp(self):
        self.base = os.path.realpath(tempfile.mkdtemp(prefix="estimate-test-"))
        self.addCleanup(shutil.rmtree, self.base)
        self.root = os.path.join(self.base, "project")
        os.makedirs(self.root)
        git(self.root, "init", "-q", "-b", "main")
        # /implement leaves the estimate files uncommitted; keep them out of the test commits so line counts stay hand-computable.
        with open(os.path.join(self.root, ".git", "info", "exclude"), "a", encoding="utf-8") as exclude:
            exclude.write(DATA_FOLDER + "/\n")
        self.which = shutil.which

    def path(self, relative):
        return os.path.join(self.root, relative)

    def write(self, relative, text):
        path = self.path(relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def commit(self, message, files=None, when="2026-10-01T12:00:00+00:00", committed=None):
        """Writes `files` ({relative: text}), commits everything with author date `when` and committer date `committed`
        (default `when`), returns the full hash."""
        for relative, text in (files or {}).items():
            self.write(relative, text)
        environment = dict(os.environ, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=committed or when)
        git(self.root, "add", "-A", env=environment)
        git(self.root, "commit", "-q", "--allow-empty", "-m", message, env=environment)
        return git(self.root, "rev-parse", "HEAD").strip()

    def fake_session_log(self):
        folder = os.path.join(self.base, "session-log")
        os.makedirs(folder, exist_ok=True)
        return FakeSessionLog(folder)

    def seed(self, document):
        """Writes `document` (the `read` shape) as the scale file, with its anchor only when it has one, and one file per feature,
        named by its `file`."""
        settings = {"version": document["version"], "scale": document["scale"]}
        if document["anchor"] is not None:
            settings["anchor"] = document["anchor"]
        self.write(SCALE_PATH, json.dumps(settings))
        for feature in document["features"]:
            stored = {"version": 1, **{key: value for key, value in feature.items() if key != "file"}}
            self.write(FEATURES_PATH + "/" + feature["file"], json.dumps(stored))

    def raw(self, relative):
        with open(self.path(relative), encoding="utf-8") as handle:
            return json.load(handle)

    def feature_files(self):
        folder = self.path(FEATURES_PATH)
        return sorted(os.listdir(folder)) if os.path.isdir(folder) else []

    def estimation(self):
        """The stored data in the `read` shape: the scale file, then each feature file in name order without its `version`
        and with its `file`."""
        scale = self.raw(SCALE_PATH)
        features = [{**{key: value for key, value in self.raw(FEATURES_PATH + "/" + name).items() if key != "version"},
                     "file": name} for name in self.feature_files()]
        return {"version": scale["version"], "scale": scale["scale"], "anchor": scale.get("anchor"), "features": features}

    def feature(self, name):
        return next(feature for feature in self.estimation()["features"] if feature["name"] == name)

    def run_estimate(self, *argv, cwd=None, stdin="", which=None, version_info=None):
        """Runs the entry's `main` in-process; the working folder defaults to the project root."""
        import estimate
        from estimation.environment import Environment
        stdout, stderr = io.StringIO(), io.StringIO()
        environment = Environment(stdout=stdout, stderr=stderr, stdin=io.StringIO(stdin), get_cwd=lambda: cwd or self.root,
                                  today=lambda: TODAY, which=which or self.which, version_info=version_info or sys.version_info)
        code = estimate.main(list(argv), environment)
        return Result(code, stdout.getvalue(), stderr.getvalue())

    def assert_error(self, result, code, fragment):
        """One `estimate:` line on stderr naming `fragment`, the exit code, and nothing on stdout."""
        self.assertEqual(code, result.code, result.err)
        self.assertEqual("", result.out)
        self.assertEqual(1, result.err.count("\n"), result.err)
        self.assertTrue(result.err.startswith("estimate: "), result.err)
        self.assertIn(fragment, result.err)


def date_file(path, day):
    """Sets the modification time of `path` to noon of `day` (local time); macOS then moves its creation time back to it too."""
    moment = datetime.fromisoformat(day + "T12:00:00").timestamp()
    os.utime(path, (moment, moment))


def new_feature(name, file=None, **fields):
    """A complete C2 feature in the `read` shape with the `save` defaults, overridden by `fields`; its `file` defaults to a
    seeded name of the feature."""
    feature = {"id": None, "name": name, "description": "About " + name, "effort": "Work of " + name, "status": "estimated",
               "storyPoints": None, "updatedStoryPoints": None, "estimatedAt": None, "estimatedBy": None, "updatedBy": None,
               "plan": {"phases": None, "tasks": None, "estimatedHours": None},
               "git": {"commits": 0, "linesAdded": 0, "linesRemoved": 0}, "usage": None,
               "sources": {"docs": [], "commits": [], "sessions": []}}
    feature.update(fields)
    feature["file"] = file or "%s-%s.json" % (SEEDED_DAY, name.lower().replace(" ", "-"))
    return feature


def before_effort(feature):
    """`feature` as a file saved before features had an `effort` holds it."""
    return {key: value for key, value in feature.items() if key != "effort"}


def document(*features, scale=None, anchor=None):
    """The `read` shape; `anchor` None is a scale file without one."""
    return {"version": 1, "scale": DEFAULT_SCALE if scale is None else scale, "anchor": anchor, "features": list(features)}
