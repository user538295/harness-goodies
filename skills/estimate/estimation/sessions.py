"""Sessions through session-log's C1 JSON (`list --json`, `usage ... --json`), run as a subprocess in the project root, and
`match-sessions`: each feature gets its slice of a session (the prompts the `evidence` rule gives it, each with its number, time,
first 60 characters and reason, and the sub-agents started under them) as a C2 session entry, and the `usage` totals `save` derives
from those entries. Every failed call, every session
counted whole for want of prompt rows and every feature commit made while no listed prompt ran is named in `missing`, never guessed
around."""
import json
import os
import re
import sys
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from estimation.evidence import attribute, prompt_under
from estimation.features import HARNESSES, MATCH_NONE, MATCH_PROMPT, TOKEN_KEYS, derive_usage, session_entry

SECONDS_PER_MINUTE = Decimal(60)
WHOLE = Decimal(1)
CENT = Decimal("0.01")
SESSION_LOG_SUCCESS = 0
TOKEN_FIELDS = "tokens"
PROMPT_SEPARATOR = "\n"
PROMPT_HEAD_LENGTH = 60
_TAG = re.compile(r"<[^>]*>")
_LINE_BREAKS_AND_TABS = re.compile(r"[\r\n\t]")
_SPACE_RUN = re.compile(r" +")
NO_ROWS = "%s has no prompt rows; the whole session is counted for %s"
UNCOVERED_COMMITS = "%s: no session found for %d of %d commits"


def prompt_head(text):
    """The prompt as session-log's report shows it: every `<...>` tag and every line break or tab becomes a space, runs of spaces
    collapse, and the first 60 characters are kept without the spaces at either end."""
    spaced = _SPACE_RUN.sub(" ", _LINE_BREAKS_AND_TABS.sub(" ", _TAG.sub(" ", text)))
    return spaced.lstrip(" ")[:PROMPT_HEAD_LENGTH].rstrip(" ")


def _sessions_with_ids(listed):
    """The sessions of a `list --json` reply that carry an id (anything else cannot be read further)."""
    sessions = listed if isinstance(listed, list) else []
    return [session for session in sessions if isinstance(session, dict) and isinstance(session.get("id"), str)]


def _moment(text):
    """The time of a C1 UTC ISO text; None for a missing or unreadable one."""
    try:
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def minutes(seconds):
    """Whole minutes, rounded half up; null stays null."""
    if seconds is None:
        return None
    return int((Decimal(str(seconds)) / SECONDS_PER_MINUTE).quantize(WHOLE, rounding=ROUND_HALF_UP))


def _known_sum(values):
    """The sum of the known values; null when none is known."""
    known = [value for value in values if value is not None]
    return sum(known) if known else None


@dataclass(frozen=True)
class RowNumbers:
    tokens: Optional[dict]
    cost: Decimal
    complete: bool
    models: Optional[tuple]  # None when the row does not say


@dataclass(frozen=True)
class PromptRow:
    at: datetime
    reply_at: Optional[datetime]
    text: str
    seconds: int
    numbers: RowNumbers
    after_reset: bool = False  # the user cleared the context before this prompt
    written: tuple = ()  # the files its tool calls wrote


@dataclass(frozen=True)
class AgentRow:
    started_at: datetime
    numbers: RowNumbers


def _model_list(value):
    """The model names of a C1 `models` value; None when it is no list of names."""
    if not isinstance(value, list) or not all(isinstance(name, str) and name for name in value):
        return None
    return tuple(value)


def _numbers(row):
    tokens = row.get(TOKEN_FIELDS)
    return RowNumbers(tokens if isinstance(tokens, dict) else None, Decimal(str(row["costUsd"])), row["costComplete"] is True,
                      _model_list(row.get("models")))


def _written_paths(row):
    """The `writtenPaths` of a C1 prompt row; none when the row has none or it is no list; entries that are no text are skipped."""
    paths = row.get("writtenPaths")
    return tuple(path for path in paths if isinstance(path, str) and path) if isinstance(paths, list) else ()


def _rows(usage):
    """(prompt rows, sub-agent rows) of a C1 usage reply; None when it has none or they cannot be read."""
    prompts, agents = usage.get("prompts"), usage.get("subAgents")
    if not isinstance(prompts, list) or not isinstance(agents, list):
        return None
    try:
        return ([PromptRow(datetime.fromisoformat(row["at"]), _moment(row.get("replyAt")), row["text"], int(row["agentTimeSeconds"]),
                           _numbers(row), row.get("afterReset") is True, _written_paths(row)) for row in prompts],
                [AgentRow(datetime.fromisoformat(row["startedAt"]), _numbers(row)) for row in agents])
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return None


class SessionUsage:
    """One session's C1 usage reply (None: the call failed): where the session is, its totals and its rows. `label` names the call
    in `missing`; `listed` (the session's `list` entry) fills a path or log the reply lacks."""

    def __init__(self, label, harness, session_id, usage, listed=None):
        self.label = label
        self.harness = harness
        self.session_id = session_id
        self.usage = usage or {}
        self.listed = listed or {}
        self.rows = _rows(self.usage)

    def _entry(self, models, prompts, agent_time_minutes, tokens, cost):
        return session_entry({"harness": self.harness, "models": models, "id": self.session_id,
                              "path": self.usage.get("path") or self.listed.get("path"),
                              "log": self.usage.get("log") or self.listed.get("log"), "prompts": prompts,
                              "agentTimeMinutes": agent_time_minutes, TOKEN_FIELDS: tokens, "costUsd": cost})

    def whole(self):
        """The C2 entry of the whole session from its totals (every number null when the call failed)."""
        complete = self.usage.get("costComplete") is True
        models = _model_list(self.usage.get("models"))
        return self._entry(None if models is None else list(models), None, minutes(self.usage.get("agentTimeSeconds")),
                           self.usage.get(TOKEN_FIELDS), self.usage.get("costUsd") if complete else None)

    def prompt_spans(self):
        """(submitted, last reply) of each prompt."""
        return [(prompt.at, prompt.reply_at) for prompt in self.rows[0]]

    def last_prompt_models(self):
        """The models of the session's last prompt (in a latest session, the prompt running now); None without one or without
        names."""
        if not self.rows or not self.rows[0] or not self.rows[0][-1].numbers.models:
            return None
        return list(self.rows[0][-1].numbers.models)

    def slices(self, evidence):
        """{feature name: the C2 entry of its slice} of a session with rows, by the `evidence` rule."""
        prompts, agents = self.rows
        owners = attribute([evidence.of_prompt(prompt.text, prompt.at, prompt.reply_at, prompt.written) for prompt in prompts],
                           [prompt.after_reset for prompt in prompts])
        members = {}
        for index, reasons in enumerate(owners):
            for name, reason in reasons.items():
                members.setdefault(name, ([], []))[0].append((index, reason))
        starts = [prompt.at for prompt in prompts]
        for agent in agents if prompts else []:
            for name in owners[prompt_under(starts, agent.started_at)]:
                members[name][1].append(agent)
        return {name: self._slice(counted, chosen_agents) for name, (counted, chosen_agents) in members.items()}

    def _slice(self, counted, agents):
        """The C2 entry of the prompts `counted` ([(index, why it counts)]) and the sub-agents `agents`."""
        prompts = [self.rows[0][index] for index, _ in counted]
        numbers = [row.numbers for row in [*prompts, *agents]]
        tokens = {key: _known_sum([row.tokens.get(key) for row in numbers if row.tokens is not None]) for key in TOKEN_KEYS}
        complete = self.usage.get("costComplete") is True and all(row.complete for row in numbers)
        cost = float(sum((row.cost for row in numbers), Decimal(0)).quantize(CENT, rounding=ROUND_HALF_UP)) if complete else None
        known = [row.models for row in numbers if row.models is not None]
        models = sorted({name for names in known for name in names}) if known else None
        entries = [{"number": index + 1, "at": prompt.at.isoformat(), "text": prompt_head(prompt.text), "reason": reason}
                   for (index, reason), prompt in zip(counted, prompts)]
        return self._entry(models, entries, minutes(sum(prompt.seconds for prompt in prompts)), tokens, cost)


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


class Coverage:
    """The time spans in which the prompts of the sessions read so far ran."""

    def __init__(self):
        self._spans = []

    def add(self, start, end):
        if start is not None:
            self._spans.append((start, end or start))

    def covers(self):
        """A function telling whether a moment lies in any span."""
        merged = []
        for start, end in sorted(self._spans):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        starts = [start for start, _ in merged]
        return lambda moment: (index := bisect_right(starts, moment) - 1) >= 0 and moment <= merged[index][1]


class SessionMatcher:
    def __init__(self, session_log, evidence):
        self.session_log = session_log
        self.evidence = evidence
        self.missing = []
        self.coverage = Coverage()

    def match(self, authored):
        """The C3 `match-sessions` output for the features of `authored` ({feature name: {commit hash: author time}}, in input
        order)."""
        found = {name: [] for name in authored}
        for harness in HARNESSES:
            listed, failure = self.session_log.listed(harness)
            if failure:
                self.missing.append(failure)
                continue
            for session in _sessions_with_ids(listed):
                self._read(harness, session, found)
        covered = self.coverage.covers()
        for name, times in authored.items():
            uncovered = sum(not covered(moment) for moment in times.values())
            if uncovered:
                self.missing.append(UNCOVERED_COMMITS % (name, uncovered, len(times)))
        return {"features": [_matched(name, sessions) for name, sessions in found.items()], "missing": self.missing}

    def _read(self, harness, session, found):
        """Reads the usage of a session that names a feature or ran while a feature commit was made, and files its slices."""
        prompts = PROMPT_SEPARATOR.join(prompt for prompt in session.get("prompts") or [] if isinstance(prompt, str))
        start, last_activity = _moment(session.get("startedAt")), _moment(session.get("lastActivityAt"))
        ran_over_commit = None not in (start, last_activity) and self.evidence.touches(start, last_activity)
        if not ran_over_commit and not self.evidence.named(prompts):
            return
        session_id = session["id"]
        usage, failure = self.session_log.usage(harness, session_id)
        if failure:
            self.missing.append(failure)
        reply = SessionUsage("session-log %s usage %s" % (harness, session_id), harness, session_id, usage, session)
        if reply.rows is not None:
            for prompt_start, reply_at in reply.prompt_spans():
                self.coverage.add(prompt_start, reply_at)
            for name, entry in reply.slices(self.evidence).items():
                found[name].append(entry)
            return
        self.coverage.add(start, last_activity)
        names = [name for name in found if name in self.evidence.of_prompt(prompts, start, last_activity)]
        if names:
            entry = reply.whole()
            for name in names:
                found[name].append(entry)
            self.missing.append(NO_ROWS % (reply.label, ", ".join(names)))


def _matched(name, sessions):
    """A `match-sessions` feature: its slices and the usage `save` derives from them for its label."""
    feature = {"name": name, "matchedBy": MATCH_PROMPT if sessions else MATCH_NONE, "sessions": sessions}
    derived = {"sources": {"sessions": sessions}}
    derive_usage(derived, feature["matchedBy"])
    return feature | {"usage": derived["usage"]}
