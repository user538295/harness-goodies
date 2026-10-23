"""Builder of the OpenCode SQLite database the OpenCode tests read. The database is made at test time from the SQL text below (the
tables of OpenCode v2.0.30 `session_v2` and `session_message`, without their foreign keys and the columns no reader uses) and from the
rows a test adds; no database file is committed and the real one is never opened. Times are seconds after `BASE`; `ms` gives them as the
epoch milliseconds OpenCode stores."""
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "opencode")
CWD = "/work/oc"
BASE = datetime(2026, 10, 13, 8, 42, 0, tzinfo=timezone.utc)
ROOT_ID = "ses_root0001"
CHILD_ID = "ses_child001"

SCHEMA = """
CREATE TABLE `session_v2` (
  `id` text PRIMARY KEY,
  `project_id` text NOT NULL,
  `parent_id` text,
  `slug` text NOT NULL,
  `directory` text NOT NULL,
  `title` text,
  `version` text NOT NULL,
  `cost` real DEFAULT 0 NOT NULL,
  `agent` text,
  `time_created` integer NOT NULL,
  `time_updated` integer NOT NULL
);
CREATE INDEX `session_v2_parent_idx` ON `session_v2` (`parent_id`);
CREATE TABLE `session_message` (
  `id` text PRIMARY KEY,
  `session_id` text NOT NULL,
  `type` text NOT NULL,
  `seq` integer NOT NULL,
  `time_created` integer NOT NULL,
  `time_updated` integer NOT NULL,
  `data` text NOT NULL
);
CREATE UNIQUE INDEX `session_message_session_seq_idx` ON `session_message` (`session_id`, `seq`);
"""
V1_SCHEMA = """
CREATE TABLE `session` (`id` text PRIMARY KEY, `parent_id` text, `time_created` integer NOT NULL, `time_updated` integer NOT NULL);
"""


NO_SEQ_INDEX_SCHEMA = SCHEMA.replace("CREATE UNIQUE INDEX `session_message_session_seq_idx` ON `session_message` (`session_id`, `seq`);\n", "")


def read_text(path):
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def folder_snapshot(folder):
    """Every file below `folder` with the hash of its bytes and its modification time, by relative path."""
    found = {}
    for root, _, names in os.walk(folder):
        for name in names:
            path = os.path.join(root, name)
            with open(path, "rb") as handle:
                found[os.path.relpath(path, folder)] = (hashlib.sha256(handle.read()).hexdigest(), os.stat(path).st_mtime_ns)
    return found


def ms(second) -> int:
    return int((BASE + timedelta(seconds=second)).timestamp() * 1000)


def assistant_data(second, text="", finish="stop", tokens=None, cost=0, done=1, model=("anthropic", "claude-opus-5-5"), agent="build"):
    """The `data` of an assistant row started at `second` that completed `done` seconds later (None: still running)."""
    data = {"agent": agent, "model": {"providerID": model[0], "id": model[1], "variant": "default"},
            "content": [{"type": "text", "text": text}] if text else [], "time": {"created": ms(second)}}
    if done is not None:
        data["time"]["completed"] = ms(second + done)
    if finish is not None:
        data["finish"] = finish
    if tokens is not None:
        data["tokens"] = {"input": tokens[0], "output": tokens[1], "reasoning": tokens[2] if len(tokens) > 2 else 0,
                          "cache": {"read": tokens[3] if len(tokens) > 3 else 0, "write": tokens[4] if len(tokens) > 4 else 0}}
    if cost is not None:
        data["cost"] = cost
    return data


def user_data(second, text="ask"):
    return {"text": text, "time": {"created": ms(second)}}


class OpenCodeDatabase:
    """One database file at `path`; each method opens it, writes and closes it, as OpenCode's own short transactions do."""

    def __init__(self, path, schema=SCHEMA, journal_mode="delete"):
        """`journal_mode="wal"` makes the database a WAL one, as OpenCode's own is (SQLite removes `-wal` and `-shm` when the last connection closes)."""
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("PRAGMA journal_mode = %s" % journal_mode)
            connection.executescript(schema)

    def execute(self, sql, values=()):
        """Runs one write statement of a test that needs a row or a value `add_*` cannot make."""
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(sql, values)
            connection.commit()

    def add_session(self, session_id, parent=None, created=0, updated=None, directory=CWD, agent=None):
        self.execute("INSERT INTO session_v2 (id, project_id, parent_id, slug, directory, title, version, agent, time_created, time_updated)"
                    " VALUES (?, 'prj', ?, 'slug', ?, 'title', '2.0.30', ?, ?, ?)",
                    (session_id, parent, directory, agent, ms(created), ms(created if updated is None else updated)))

    def add_message(self, session_id, seq, kind, data, updated=None, text=None):
        """A `session_message` row; `text` replaces the JSON of `data` (a row whose data is not JSON)."""
        created = (data.get("time") or {}).get("created", ms(0)) if isinstance(data, dict) else ms(0)
        updated_at = created if updated is None else ms(updated)
        self.execute("INSERT INTO session_message (id, session_id, type, seq, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ("msg_%s_%d" % (session_id, seq), session_id, kind, seq, created, updated_at, text if text is not None else json.dumps(data)))

    def add_user(self, session_id, seq, second, text="ask"):
        self.add_message(session_id, seq, "user", user_data(second, text))

    def add_assistant(self, session_id, seq, second, **options):
        self.add_message(session_id, seq, "assistant", assistant_data(second, **options))

    def replace_data(self, session_id, seq, data, updated):
        """What OpenCode does to an assistant row while it streams: its `data` and `time_updated` change in place."""
        self.execute("UPDATE session_message SET data = ?, time_updated = ? WHERE session_id = ? AND seq = ?",
                    (json.dumps(data), ms(updated), session_id, seq))

    def load_sql(self, path):
        """Runs the INSERT statements of the text fixture at `path`."""
        with closing(sqlite3.connect(self.path)) as connection:
            connection.executescript(read_text(path))

    def fixture(self):
        """The fixture family: a root session with two prompts and a child (sub-agent) session with one request."""
        self.add_session(ROOT_ID, created=10, updated=60, agent="build")
        self.add_user(ROOT_ID, 1, 10, "first prompt")
        self.add_assistant(ROOT_ID, 2, 11, text="first reply", tokens=(10, 100, 20, 200, 50), cost=0.5, done=4)
        self.add_user(ROOT_ID, 3, 30, "second prompt")
        self.add_assistant(ROOT_ID, 4, 31, text="second reply", tokens=(5, 30, 0, 100, 0), cost=0, done=9, model=("openai", "gpt-5"))
        self.add_session(CHILD_ID, parent=ROOT_ID, created=20, updated=40, agent="general")
        self.add_user(CHILD_ID, 1, 20, "task text for the sub-agent")
        self.add_assistant(CHILD_ID, 2, 21, text="done", tokens=(7, 8, 1, 9, 3), cost=0.25, done=5, model=("openai", "gpt-5"), agent="general")
        return self
