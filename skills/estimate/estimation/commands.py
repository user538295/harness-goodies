"""The C3 commands. Each returns (payload printed as JSON on stdout, warnings printed as `estimate:` lines on stderr); every command
that reads the estimate files also warns about each name, id and doc that several feature files hold."""
import json
import math
import os
from datetime import datetime, time, timedelta, timezone

from estimation.anchor import ANCHOR_KEY, hours_size, off_reason
from estimation.calculation import calculate, check_cards
from estimation.doc_sets import doc_sets
from estimation.doc_stats import doc_stats
from estimation.errors import ArgumentError, EstimateError
from estimation.evidence import Evidence, FeatureClaim, feature_commits
from estimation.features import (DELIVERED, EFFORT_KEY, FILE_KEY, FULL_HASH, ID_KEY, IN_PROGRESS, MATCH_PROMPT, Upsert,
                                 check_entry_shape, derive_usage, duplicates, files_of, is_number, is_reference, merge_plan,
                                 scale_element, session_entry, upsert_session)
from estimation.findings import texts
from estimation.first_day import first_day
from estimation.git_history import GitHistory
from estimation.naming import day_of_file
from estimation.neighbours import pick
from estimation.placement import place
from estimation.related_files import linked_files
from estimation.report_data import analyse
from estimation.report_page import render
from estimation.sessions import NO_ROWS, SessionLog, SessionMatcher, SessionUsage
from estimation.store import DATA_FOLDER
from estimation.text_rules import check_texts, problems

STDIN_MARK = "-"
NO_PROMPT_OF_FEATURE = "the latest %s session %s shows no prompt of %s"
SHARED_COMMITS = "commits that several features claim count for none: %s"
NO_SESSION_OF_FEATURE = "no session names or wrote a document of %s: no session attached"
NO_SESSION_LOG = "no session data: session-log not found (%s)"
DROPPED_COMMITS = "%s: %d recorded commits are no longer in git and were dropped: %s"
SHORT_HASH = 8
LATEST_USAGE = "session-log %s usage --latest"
LATEST_NAMED_NO_SESSION = LATEST_USAGE + " named no session"
LAST_PROMPT_WITHOUT_MODELS = LATEST_USAGE + " names none for its last prompt"
NO_MODELS = "models not recorded: %s"
REPORT_FILE = "estimation-report.html"  # under DATA_FOLDER when no --out is given
REPORT_TEMPLATE = "estimation-report.html"
CHART_SOURCE = "chart.umd.js"
MISSING_COMMITS = "%d %s not found in git: %s"
PICK_FIELDS = ("name", "pool")
PLACE_FIELDS = ("hours", "verdicts", "pickVerdicts")
NUMERIC_SCALE = "%s needs a numeric scale (every value a number); found %s"
OFF_SCALE = "sp %s%s is not in the scale %s"
TIMEZONE_MARGIN = timedelta(hours=14)  # a file name's day is local; session times are UTC (UTC+14 is the furthest zone ahead)


def _check_size(scale, value, owner=""):
    """Refuses a size the scale does not hold; `owner` (e.g. " of pool item 'A'") says whose size it is."""
    if scale_element(scale, value) is None:
        raise ArgumentError(OFF_SCALE % (json.dumps(value, ensure_ascii=False), owner, json.dumps(scale, ensure_ascii=False)))


def _pool_item(reference):
    """A stored reference as `neighbours` compares it: its after-delivery SP, and its plan's tasks and hours."""
    return {"name": reference["name"], "id": reference["id"], "description": reference["description"],
            "effort": reference.get(EFFORT_KEY), "sp": reference["updatedStoryPoints"], "tasks": reference["plan"]["tasks"],
            "estimatedHours": reference["plan"]["estimatedHours"]}


def _prompt_numbers(session):
    return {prompt["number"] for prompt in session["prompts"] or ()}


def _may_replace(stored, entry):
    """Whether `entry` may replace the `stored` session of its harness and id without losing anything: a stored whole session (no
    prompts) yields to any entry, a slice that counts prompts replaces a whole count; a stored slice yields only to an entry that
    counts every prompt it counts, never to a whole session."""
    if stored["prompts"] is None:
        return True
    if entry["prompts"] is None:
        return False
    return _prompt_numbers(stored) <= _prompt_numbers(entry)


def _candidate(commit):
    """A commit as `git-stats` and `collect` list a candidate."""
    return {"hash": commit.hash, "subject": commit.message.split("\n", 1)[0]}


def _not_shrinking(feature, entries):
    """The `entries` to merge into the feature: new sessions, and refreshes of stored ones that `_may_replace` them. This also
    keeps the sessions an earlier version recorded under another label, which are stored sessions like any other."""
    stored = {(session["harness"], session["id"]): session for session in feature["sources"]["sessions"]}
    return [entry for entry in entries
            if (entry["harness"], entry["id"]) not in stored or _may_replace(stored[(entry["harness"], entry["id"])], entry)]


class Commands:
    def __init__(self, project, store, environment, plan_progress, report_assets, session_log):
        """`plan_progress` is the default `plan-progress.sh` (the /implement skill's, next to this skill); `report_assets` is the
        folder of the report's page template and its vendored Chart.js; `session_log` is the absolute path of the sibling
        session-log skill's `session_log.py`, which runs unless a command is given another."""
        self.project = project
        self.store = store
        self.environment = environment
        self.plan_progress = plan_progress
        self.report_assets = report_assets
        self.session_log = session_log

    def _json_argument(self, text, what):
        """The JSON of an argument (`-` reads stdin); unparseable JSON is a bad argument."""
        if text == STDIN_MARK:
            text = self.environment.stdin.read()
        try:
            return json.loads(text)
        except ValueError as error:
            raise ArgumentError("%s is not JSON: %s" % (what, error)) from error

    def _load(self):
        """The stored document and the duplicate warnings of its features."""
        document = self.store.load()
        return document, duplicates(document["features"])

    def read(self, references):
        document, warnings = self._load()
        if references:
            document["features"] = [feature for feature in document["features"] if is_reference(feature)]
        return document, warnings

    def save(self, entry_text):
        entry = self._json_argument(entry_text, "entry")
        check_entry_shape(entry)
        sources = entry.get("sources")
        if sources:
            for key in ("docs", "files"):
                if key in sources:
                    sources[key] = [self.project.stored(path) for path in sources[key]]
        document, warnings = self._load()
        today = self.environment.today()
        upsert = Upsert(document, entry, today)
        feature = upsert.apply()
        if FILE_KEY in feature:
            feature[FILE_KEY] = self.store.update(feature)
        else:
            feature[FILE_KEY] = self.store.create(feature, first_day(feature, self.project, self._history(), today))
        return feature, warnings + upsert.warnings

    def remove(self, file):
        """Deletes one feature's file; the warnings name the duplicates the remaining files still hold."""
        features = self.store.load()["features"]
        removed = next((feature for feature in features if feature[FILE_KEY] == file), None)
        if removed is None:
            raise EstimateError("no feature file '%s'" % file)
        self.store.remove(removed)
        return removed, duplicates([feature for feature in features if feature is not removed])

    def doc_stats(self, doc, plan_progress=None):
        script = self.plan_progress if plan_progress is None else self.project.absolute(plan_progress)
        return doc_stats(self.project.absolute(doc), script, self.environment.run), []

    def _features(self, features_text):
        """The FeatureClaims of a `{"features": [{"name", "id", "docs", "taskIds", "commits"}]}` argument, in its order; `commits`
        (optional) holds the full hashes a checker accepted for the feature."""
        payload = self._json_argument(features_text, "features input")
        items = payload.get("features") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise ArgumentError('features input must be {"features": [...]}')
        features = []
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"]:
                raise ArgumentError("each feature needs a name")
            feature_id, docs, task_ids = item.get("id"), item.get("docs") or [], item.get("taskIds") or []
            if feature_id is not None and not (isinstance(feature_id, str) and feature_id.strip()):
                raise ArgumentError("id of '%s' must be text or null" % item["name"])
            if not all(isinstance(value, str) and value for value in [*docs, *task_ids]):
                raise ArgumentError("docs and taskIds of '%s' must be lists of strings" % item["name"])
            commits = item.get("commits") or []
            if not (isinstance(commits, list) and all(isinstance(value, str) and value for value in commits)):
                raise ArgumentError("commits of '%s' must be a list of strings" % item["name"])
            features.append(FeatureClaim(item["name"], tuple(self.project.stored(doc) for doc in docs), tuple(task_ids),
                                         feature_id, tuple(commits)))
        return features

    def _history(self):
        return GitHistory(self.project.root, self.environment.run)

    def git_stats(self, features_text):
        claimed = feature_commits(self._history(), self._features(features_text))
        stats = [{"name": name, "commits": [commit.hash for commit in commits], "count": len(commits),
                  "linesAdded": sum(commit.added for commit in commits),
                  "linesRemoved": sum(commit.removed for commit in commits),
                  "candidates": [_candidate(commit) for commit in claimed.candidates[name]]}
                 for name, commits in claimed.found.items()]
        shared = ", ".join(commit.hash[:SHORT_HASH] for commit in claimed.shared)
        return {"features": stats}, [SHARED_COMMITS % shared] if shared else []

    def _session_script(self, given):
        """The absolute path of the `session_log.py` to run: `given` (relative to the working folder), else the sibling skill's."""
        return self.project.absolute(given) if given else self.session_log

    def _session_log(self, given=None, session_dir=None):
        """session-log run in the project root, or in `session_dir` (relative to the working folder): the folder the running
        session was started in, where `usage --latest` finds that session even when the project is another folder."""
        folder = self.project.root if session_dir is None else self.project.absolute(session_dir)
        if not os.path.isdir(folder):
            raise ArgumentError("--session-dir %s is not a folder" % session_dir)
        return SessionLog(self._session_script(given), folder, self.environment.run)

    def match_sessions(self, features_text, session_log=None):
        features = self._features(features_text)
        authored = {name: {commit.hash: commit.authored_at for commit in commits}
                    for name, commits in feature_commits(self._history(), features).found.items()}
        return SessionMatcher(self._session_log(session_log), Evidence(features, authored)).match(authored), []

    def _plan_owner(self, document, stored_plan, given_plan):
        owners = [feature for feature in document["features"] if stored_plan in feature["sources"]["docs"]]
        if not owners:
            raise EstimateError("no feature lists %s; run /estimate %s first" % (stored_plan, given_plan))
        if len(owners) > 1:
            raise EstimateError("%d features list %s: %s" % (len(owners), stored_plan, files_of(owners)))
        return owners[0]

    @staticmethod
    def _entries_without_repeats(entries):
        """`entries` with one entry for each session: of two for the same harness and id, the one that counts every prompt the other
        counts."""
        kept = {}
        for entry in entries:
            key = (entry["harness"], entry["id"])
            if key not in kept or _may_replace(kept[key], entry):
                kept[key] = entry
        return list(kept.values())

    def _work_start(self, feature, history):
        """The moment from which a feature's sessions are read and commits touching its related files count: the earliest of its file
        name's day, the day each of its docs was first added to git or created and the author day of each of its commits, at 00:00
        UTC, less the margin."""
        day = min(day_of_file(feature[FILE_KEY]), first_day(feature, self.project, history, self.environment.today()))
        return datetime.combine(day, time.min, timezone.utc) - TIMEZONE_MARGIN

    def _read_docs(self, feature, doc, history, document):
        """Refreshes the related files of the feature's documents in its sources: `doc` and the other documents it lists. The files
        are rebuilt from the documents' links when every listed document is readable; when one is gone (an archived plan) the
        stored files stay, since its links are unknown, and the new ones are added. (`doc_stats` already failed on an unreadable
        `doc`.)"""
        sources = feature["sources"]
        stored_doc = self.project.stored(doc)
        paths = [self.project.absolute(doc)] + [self.project.located(stored) for stored in sources["docs"] if stored != stored_doc]
        tracked = set(history.files()) if self.project.in_git else set()
        documents = {stored for other in document["features"] for stored in other["sources"]["docs"]}
        files, all_read = [], True
        for absolute in paths:
            try:
                with open(absolute, encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
            except OSError:
                all_read = False
                continue
            files += [path for path in linked_files(text, os.path.dirname(absolute), self.project, tracked, documents)
                      if path not in files]
        sources["files"] = files if all_read else sources["files"] + [path for path in files if path not in sources["files"]]

    def _claims(self, document, target, accepted, since):
        """The FeatureClaims of every stored feature: its docs and recorded commits, no task IDs (a feature missing from the store
        would not take its share of the prompts that name `BE-1`); only the `target` also has its id (so unclaimed commits naming it
        become candidates), the `accepted` commits and, from `since`, its related files (so commits touching them become
        candidates)."""
        claims = []
        for feature in document["features"]:
            sources = feature["sources"]
            is_target = feature is target
            claims.append(FeatureClaim(feature["name"], tuple(sources["docs"]), (), feature[ID_KEY] if is_target else None,
                                       tuple(dict.fromkeys([*sources["commits"], *(accepted if is_target else ())])),
                                       tuple(sources["files"]) if is_target else (), since if is_target else None))
        return claims

    @staticmethod
    def _accepted_commits(accept):
        """The full hashes of an `--accept` value (comma-separated; none without one)."""
        hashes = [] if accept is None else [value.strip() for value in accept.split(",") if value.strip()]
        for value in hashes:
            if not FULL_HASH.fullmatch(value):
                raise ArgumentError("--accept holds '%s', which is not a full commit hash" % value)
        return hashes

    def _take_commits(self, document, feature, accepted, history, since, missing, warnings):
        """Takes the feature's commits (the ones that change its docs, the recorded ones and the `accepted` ones that no other
        feature claims) and their lines into `feature`, adds to `warnings` the shared commits the feature claims and to `missing`
        the recorded commits of the feature that git no longer holds (no other feature is written, so none of theirs are named), and
        returns (the evidence of every stored feature, {feature name: {commit: author time}}, the candidate commits of the feature)."""
        claims = self._claims(document, feature, accepted, since)
        if not self.project.in_git:
            missing.append("not a git repository: no commits collected")
            authored = {claim.name: {} for claim in claims}
            return Evidence(claims, authored), authored, []
        name = feature["name"]
        claimed = feature_commits(history, claims, drop_unreachable=True, follow=name)
        mine = claimed.found[name]
        feature["sources"]["commits"] = [commit.hash for commit in mine]
        feature["git"] = {"commits": len(mine), "linesAdded": sum(commit.added for commit in mine),
                          "linesRemoved": sum(commit.removed for commit in mine)}
        if claimed.shared_by[name]:
            warnings.append(SHARED_COMMITS % ", ".join(commit.hash[:SHORT_HASH] for commit in claimed.shared_by[name]))
        if name in claimed.dropped:
            missing.append(DROPPED_COMMITS % (name, len(claimed.dropped[name]),
                                              ", ".join(value[:SHORT_HASH] for value in claimed.dropped[name])))
        authored = {name: {commit.hash: commit.authored_at for commit in commits} for name, commits in claimed.found.items()}
        return Evidence(claims, authored), authored, claimed.candidates[name]

    def _latest_session_entry(self, feature, evidence, harness, log, missing):
        """(the feature's slice of the harness's latest session of the session folder (the running one), None without one; the
        time spans in which its prompts ran)."""
        usage, failure = log.latest_usage(harness)
        session_id = usage.get("id") if isinstance(usage, dict) else None
        if failure or not isinstance(session_id, str):
            missing.append(failure or LATEST_NAMED_NO_SESSION % harness)
            return None, []
        reply = SessionUsage(LATEST_USAGE % harness, harness, session_id, usage)
        if reply.rows is None:
            missing.append(NO_ROWS % (reply.label, feature["name"]))
            return reply.whole(), []
        entry = reply.slices(evidence).get(feature["name"])
        if entry is None:
            missing.append(NO_PROMPT_OF_FEATURE % (harness, session_id, feature["name"]))
        return entry, reply.prompt_spans()

    def _own_sessions(self, feature, evidence, authored, since, harness, session_log, session_dir, missing):
        """Adds the sessions that worked on the feature to it: every session of the project active since `since`, by the evidence
        rule over every stored feature, and the running session. Never drops or shrinks a stored session (so none is lost to a
        failed call or a session the harness rotated out), and derives the usage."""
        script = self._session_script(session_log)
        if not os.path.isfile(script):
            missing.append(NO_SESSION_LOG % script)
            return
        folder_log = self._session_log(session_log, session_dir)
        latest, spans = self._latest_session_entry(feature, evidence, harness, folder_log, missing)
        log = folder_log if session_dir is None else self._session_log(session_log)  # the same folder: one SessionLog
        matched = SessionMatcher(log, evidence, since, spans).match(authored, feature["name"])
        missing.extend(matched["missing"])
        found = next(item for item in matched["features"] if item["name"] == feature["name"])
        entries = [session_entry(session) for session in found["sessions"]]
        if latest is not None:
            entries.append(latest)
        for entry in _not_shrinking(feature, self._entries_without_repeats(entries)):
            upsert_session(feature, entry)
        if feature["sources"]["sessions"]:
            derive_usage(feature, MATCH_PROMPT)
        else:
            missing.append(NO_SESSION_OF_FEATURE % feature["name"])

    @staticmethod
    def _set_status(feature, stats):
        """Moves the feature's status forward from the ticks of its tasks file: `in-progress` with the first ticked task,
        `delivered` once every task is ticked. It never moves back (a feature with no ticked task keeps the status it has), and
        counts the document does not give leave it as it is."""
        if stats["tasks"] is None or stats["completed"] is None:
            return
        if stats["completed"] == stats["tasks"]:
            feature["status"] = DELIVERED
        elif stats["completed"]:
            feature["status"] = IN_PROGRESS

    @staticmethod
    def _needs_resize(feature, stats):
        """Whether the feature was just delivered with no size set after delivery: every task ticked, no `updatedStoryPoints`."""
        known = stats["tasks"] is not None and stats["completed"] is not None
        return known and stats["completed"] == stats["tasks"] and feature["updatedStoryPoints"] is None

    def collect(self, doc, harness, session_log=None, session_dir=None, accept=None):
        """Refreshes everything the project knows about the feature listing `doc`: the plan's counts, its related files,
        its commits and their lines (the commits that change its docs, the recorded and `accept`ed ones, each only when no other
        feature claims it), its status from the ticked tasks, and the sessions that worked on it (every session since the feature
        began, plus the running one) with their usage. `candidates` are the unclaimed commits that name its id or touch its related
        files: they count only once a checker accepts them and a later call is given them as `accept`."""
        document, warnings = self._load()
        feature = self._plan_owner(document, self.project.stored(doc), doc)
        accepted = self._accepted_commits(accept)
        history = self._history()
        missing = []
        stats = doc_stats(self.project.absolute(doc), self.plan_progress, self.environment.run)
        merge_plan(feature["plan"], stats)
        missing.extend(stats["missing"])
        self._read_docs(feature, doc, history, document)
        since = self._work_start(feature, history)
        evidence, authored, candidates = self._take_commits(document, feature, accepted, history, since, missing, warnings)
        self._own_sessions(feature, evidence, authored, since, harness, session_log, session_dir, missing)
        self._set_status(feature, stats)
        feature[FILE_KEY] = self.store.update(feature)
        return {"feature": feature, "needsResize": self._needs_resize(feature, stats),
                "candidates": [_candidate(commit) for commit in candidates], "missing": missing}, warnings

    def estimator(self, harness, session_log=None, session_dir=None):
        """Who estimates now (`{"estimator": {"harness", "models"}, "missing"}`): `harness` and the models of the last prompt of
        its latest session in the session folder (the project root by default), the prompt running now; null models when they
        cannot be read, named in `missing`."""
        models, missing = None, []
        usage, failure = self._session_log(session_log, session_dir).latest_usage(harness)
        if failure:
            missing.append(failure)
        else:
            models = SessionUsage(LATEST_USAGE % harness, harness, None, usage).last_prompt_models()
            if models is None:
                missing.append(NO_MODELS % (LAST_PROMPT_WITHOUT_MODELS % harness))
        return {"estimator": {"harness": harness, "models": models}, "missing": missing}, []

    def _picked(self, command, request_text, fields):
        """The request, its stored document's scale and `pick()`'s result for a `{"name", "pool"?}` request that may also hold
        `fields`: `pool` (each item `{"name", "id", "description", "effort", "sp", "tasks", "estimatedHours"}`) takes the place of
        the stored references."""
        request = self._json_argument(request_text, "request")
        if not isinstance(request, dict):
            raise ArgumentError("request must be a JSON object")
        unknown = [key for key in request if key not in (*PICK_FIELDS, *fields)]
        if unknown:
            raise ArgumentError("unknown field '%s'" % unknown[0])
        name = request.get("name")
        if not (isinstance(name, str) and name.strip()):
            raise ArgumentError("request needs a name")
        document, warnings = self._load()
        scale = document["scale"]
        if "pool" not in request:
            pool = [_pool_item(feature) for feature in document["features"] if is_reference(feature)]
        elif not isinstance(request["pool"], list):
            raise ArgumentError("pool must be a list")
        else:
            pool = request["pool"]
            for item in pool:
                if not (isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"].strip()):
                    raise ArgumentError("each pool item needs a name")
                _check_size(scale, item.get("sp"), " of pool item '%s'" % item["name"])
        wrong = [value for value in scale if not is_number(value)]
        if wrong:
            raise EstimateError(NUMERIC_SCALE % (command, ", ".join(map(repr, wrong))))
        return request, scale, pick(pool, scale, name), warnings

    def neighbours(self, request_text):
        """The features to compare a feature with (`pick`), from a `{"name", "pool"?}` request."""
        _, _, picked, warnings = self._picked("neighbours", request_text, ())
        return picked, warnings

    def place(self, request_text):
        """The size of a feature among its picks (`place`), from a `{"name", "pool"?, "hours"?, "verdicts", "pickVerdicts"}`
        request: the same picks as `neighbours` gives, and the two judge calls' verdicts on them."""
        request, scale, picked, warnings = self._picked("place", request_text, PLACE_FIELDS)
        hours = request.get("hours")
        if hours is not None and not (is_number(hours) and math.isfinite(hours) and hours > 0):
            raise ArgumentError("hours must be a number above 0 or null: %s" % json.dumps(hours, ensure_ascii=False))
        return place(scale, picked, request.get("verdicts"), request.get("pickVerdicts"), hours), warnings

    def anchor(self, hours_text):
        """The value of the scale that `hours_text` planned hours give at the scale file's anchor (`{"hours", "sp", "missing"}`);
        `sp` is null, with the reason in `missing`, when the anchor is off."""
        try:
            hours = json.loads(hours_text)
        except ValueError:
            hours = None
        if not (is_number(hours) and math.isfinite(hours) and hours > 0):
            raise ArgumentError("hours must be a number above 0: %s" % hours_text)
        document, warnings = self._load()
        anchor, scale = document[ANCHOR_KEY], document["scale"]
        sp = hours_size(anchor, scale, hours)
        return {"hours": hours, "sp": sp, "missing": [] if sp is not None else [off_reason(anchor, scale)]}, warnings

    def find_sets(self):
        """The planning-doc sets of the project and whether each is delivered (`doc_sets`)."""
        return doc_sets(self.project, self._history(), self.plan_progress, self.environment.run), []

    def check_text(self, texts_text):
        """The problems of a `{"description"?, "effort"?}` request's texts (`{"problems": [...]}`, [] when they pass)."""
        texts = self._json_argument(texts_text, "texts")
        check_texts(texts)
        return {"problems": problems(texts)}, []

    def calculate(self, features_text):
        """The SP that each feature card's planned hours give at the scale file's anchor, and the pool of the sized cards that
        `neighbours` and `place` accept (`calculate`), from a `{"features": [{"name", "id", "description", "effort", "tasks",
        "estimatedHours"}]}` request."""
        cards = check_cards(self._json_argument(features_text, "features"))
        document, warnings = self._load()
        return calculate(cards, document[ANCHOR_KEY], document["scale"]), warnings

    def _asset(self, name):
        with open(os.path.join(self.report_assets, name), encoding="utf-8") as handle:
            return handle.read()

    def report(self, out=None):
        """Writes the HTML report of the references to `out` (relative to the working folder), by default REPORT_FILE under the
        project's DATA_FOLDER; prints its stored path, the counts and the commits git lacks. Nothing is written on a failure."""
        document, warnings = self._load()
        hashes = sorted({commit for feature in document["features"] if is_reference(feature)
                         for commit in feature["sources"]["commits"]})
        data = analyse(document, self._history().changes(hashes), os.path.basename(self.project.root), self.environment.today())
        page = render(self._asset(REPORT_TEMPLATE), self._asset(CHART_SOURCE), data, texts(data))
        target = self.project.absolute(out) if out else os.path.join(self.project.root, DATA_FOLDER, REPORT_FILE)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(page)
        absent = data["lines"]["missing_commits"]
        noun = "commit" if len(absent) == 1 else "commits"
        missing = [MISSING_COMMITS % (len(absent), noun, ", ".join(absent))] if absent else []
        return {"report": self.project.stored(target), "features": data["counts"]["references"],
                "forecasts": data["forecast"]["n"], "missing": missing}, warnings
