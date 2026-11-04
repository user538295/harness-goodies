"""Sessions through session-log's C1 JSON (`list --json`, `usage ... --json`), run as a subprocess in the project root: the C2
session entry of a usage reply, and `match-sessions` (prompt matches beat date matches; each feature is labelled by its weakest
match). Every failed call is named in `missing`, never guessed around."""
import json
import os
import sys
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from estimation.features import HARNESSES, MATCH_DATES, MATCH_NONE, MATCH_PROMPT, session_entry, weakest
from estimation.task_ids import mentions_any

SECONDS_PER_MINUTE = Decimal(60)
WHOLE = Decimal(1)
SESSION_LOG_SUCCESS = 0
TOKEN_FIELDS = "tokens"


def _sessions_with_ids(listed):
    """The sessions of a `list --json` reply that carry an id (anything else cannot be read further)."""
    sessions = listed if isinstance(listed, list) else []
    return [session for session in sessions if isinstance(session, dict) and isinstance(session.get("id"), str)]


def minutes(seconds):
    """Whole minutes, rounded half up; null stays null."""
    if seconds is None:
        return None
    return int((Decimal(str(seconds)) / SECONDS_PER_MINUTE).quantize(WHOLE, rounding=ROUND_HALF_UP))


def session_from_usage(harness, session_id, usage, listed=None):
    """The C2 session entry of a C1 usage reply (None: the call failed, every number null); `listed` (the session's `list`
    entry) fills a path or log the usage reply lacks."""
    usage = usage or {}
    listed = listed or {}
    complete = usage.get("costComplete") is True
    return session_entry({
        "harness": harness, "id": session_id,
        "path": usage.get("path") or listed.get("path"),
        "log": usage.get("log") or listed.get("log"),
        "agentTimeMinutes": minutes(usage.get("agentTimeSeconds")),
        TOKEN_FIELDS: usage.get(TOKEN_FIELDS),
        "costUsd": usage.get("costUsd") if complete else None,
    })


class SessionLog:
    """Runs `<python> <session_log.py> <arguments>` in the project root and parses its JSON stdout."""

    def __init__(self, script, root, run, python=sys.executable):
        self.script = script
        self.root = root
        self.run = run
        self.python = python

    def call(self, *arguments):
        """Returns (parsed JSON, None) or (None, the failure as one `missing` line)."""
        label = "session-log " + " ".join(argument for argument in arguments if argument != "--json")
        if not os.path.isfile(self.script):
            return None, "%s failed: %s not found" % (label, self.script)
        result = self.run([self.python, self.script, *arguments], self.root)
        if result.code != SESSION_LOG_SUCCESS:
            return None, "%s failed (exit %d): %s" % (label, result.code, " ".join(result.stderr.split()) or "no message")
        try:
            return json.loads(result.stdout), None
        except ValueError:
            return None, "%s failed: its output is not JSON" % label

    def listed(self, harness):
        return self.call(harness, "list", "--json")

    def usage(self, harness, session_id):
        return self.call(harness, "usage", session_id, "--json")

    def latest_usage(self, harness):
        return self.call(harness, "usage", "--latest", "--json")


class FeatureQuery:
    """One feature of the `match-sessions` input; docs already in their stored form."""

    def __init__(self, name, docs, task_ids, first_date, last_date):
        self.name = name
        self.needles = set(docs) | {os.path.basename(doc) for doc in docs}
        self.task_ids = task_ids
        self.first_date = first_date
        self.last_date = last_date

    def prompt_matches(self, prompts):
        return any(needle in prompt for prompt in prompts for needle in self.needles) \
            or any(mentions_any(prompt, self.task_ids) for prompt in prompts)

    def date_matches(self, start, last_activity):
        if None in (self.first_date, self.last_date, start, last_activity):
            return False
        return self.first_date <= start <= self.last_date and self.first_date <= last_activity <= self.last_date


class SessionMatcher:
    def __init__(self, session_log, to_local):
        self.session_log = session_log
        self.to_local = to_local
        self.missing = []

    def _local_date(self, moment):
        try:
            return self.to_local(datetime.fromisoformat(moment)).date().isoformat()
        except (TypeError, ValueError):
            return None

    def _assign(self, session, features):
        """[(feature, label)] of one listed session: every prompt match, else every date match."""
        prompts = [prompt for prompt in session.get("prompts") or [] if isinstance(prompt, str)]
        prompted = [(feature, MATCH_PROMPT) for feature in features if feature.prompt_matches(prompts)]
        if prompted:
            return prompted
        start = self._local_date(session.get("startedAt"))
        last_activity = self._local_date(session.get("lastActivityAt"))
        return [(feature, MATCH_DATES) for feature in features if feature.date_matches(start, last_activity)]

    def match(self, features):
        """The C3 `match-sessions` output for `features` (FeatureQuery list)."""
        found = {feature.name: [] for feature in features}
        labels = {feature.name: [] for feature in features}
        for harness in HARNESSES:
            listed, failure = self.session_log.listed(harness)
            if failure:
                self.missing.append(failure)
                continue
            for session in _sessions_with_ids(listed):
                assignments = self._assign(session, features)
                if not assignments:
                    continue
                entry = self._entry(harness, session)
                for feature, label in assignments:
                    found[feature.name].append(entry)
                    labels[feature.name].append(label)
        return {"features": [{"name": feature.name,
                              "matchedBy": weakest(labels[feature.name]) if labels[feature.name] else MATCH_NONE,
                              "sessions": found[feature.name]} for feature in features],
                "missing": self.missing}

    def _entry(self, harness, session):
        session_id = session.get("id")
        usage, failure = self.session_log.usage(harness, session_id)
        if failure:
            self.missing.append(failure)
        return session_from_usage(harness, session_id, usage, session)
