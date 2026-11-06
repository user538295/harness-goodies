"""The C3 commands. Each returns (payload printed as JSON on stdout, warnings printed as `estimate:` lines on stderr); every command
that reads the estimate files also warns about each name, id and doc that several feature files hold."""
import json
import math
import os

from estimation.anchor import ANCHOR_KEY, hours_size, off_reason
from estimation.calculation import calculate, check_cards
from estimation.doc_sets import doc_sets
from estimation.doc_stats import doc_stats
from estimation.errors import ArgumentError, EstimateError
from estimation.evidence import Evidence, FeatureClaim, feature_commits
from estimation.features import (DELIVERED, EFFORT_KEY, FILE_KEY, IN_PROGRESS, MATCH_IMPLEMENT, MATCH_NONE, Upsert,
                                 check_entry_shape, derive_usage, duplicates, files_of, is_number, is_reference, merge_plan,
                                 scale_element, stored_matched_by, upsert_session, weakest)
from estimation.findings import texts
from estimation.first_day import first_day
from estimation.git_history import GitHistory
from estimation.neighbours import pick
from estimation.placement import place
from estimation.report_data import analyse
from estimation.report_page import render
from estimation.sessions import NO_ROWS, SessionLog, SessionMatcher, SessionUsage
from estimation.store import DATA_FOLDER
from estimation.text_rules import check_texts, problems

STDIN_MARK = "-"
NO_PROMPT_OF_FEATURE = "the latest %s session %s shows no prompt of %s"
SHARED_COMMITS = "commits that several features claim count for none: %s"
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


def _check_size(scale, value, owner=""):
    """Refuses a size the scale does not hold; `owner` (e.g. " of pool item 'A'") says whose size it is."""
    if scale_element(scale, value) is None:
        raise ArgumentError(OFF_SCALE % (json.dumps(value, ensure_ascii=False), owner, json.dumps(scale, ensure_ascii=False)))


def _pool_item(reference):
    """A stored reference as `neighbours` compares it: its after-delivery SP, and its plan's tasks and hours."""
    return {"name": reference["name"], "id": reference["id"], "description": reference["description"],
            "effort": reference.get(EFFORT_KEY), "sp": reference["updatedStoryPoints"], "tasks": reference["plan"]["tasks"],
            "estimatedHours": reference["plan"]["estimatedHours"]}


class Commands:
    def __init__(self, project, store, environment, plan_progress, report_assets):
        """`plan_progress` is the default `plan-progress.sh` (the /implement skill's, next to this skill); `report_assets` is the
        folder of the report's page template and its vendored Chart.js."""
        self.project = project
        self.store = store
        self.environment = environment
        self.plan_progress = plan_progress
        self.report_assets = report_assets

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
        if sources and "docs" in sources:
            sources["docs"] = [self.project.stored(doc) for doc in sources["docs"]]
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
                  "candidates": [{"hash": commit.hash, "subject": commit.message.split("\n", 1)[0]}
                                 for commit in claimed.candidates[name]]}
                 for name, commits in claimed.found.items()]
        shared = ", ".join(commit.hash[:SHORT_HASH] for commit in claimed.shared)
        return {"features": stats}, [SHARED_COMMITS % shared] if shared else []

    def _session_log(self, script, session_dir=None):
        """session-log run in the project root, or in `session_dir` (relative to the working folder): the folder the running
        session was started in, where `usage --latest` finds that session even when the project is another folder."""
        folder = self.project.root if session_dir is None else self.project.absolute(session_dir)
        if not os.path.isdir(folder):
            raise ArgumentError("--session-dir %s is not a folder" % session_dir)
        return SessionLog(self.project.absolute(script), folder, self.environment.run)

    def match_sessions(self, features_text, session_log):
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
    def _stored_evidence(document, history):
        """The evidence of every stored feature: its docs and its recorded commits (no task IDs are stored)."""
        features = document["features"]
        times = history.authored_at([commit for feature in features for commit in feature["sources"]["commits"]])
        return Evidence([FeatureClaim(feature["name"], tuple(feature["sources"]["docs"]), ()) for feature in features],
                        {feature["name"]: {commit: times[commit] for commit in feature["sources"]["commits"] if commit in times}
                         for feature in features})

    def _record_latest_session(self, document, feature, history, harness, session_log, session_dir, missing):
        """Upserts the feature's slice of the harness's latest session of the session folder (matched by implement); returns the
        feature's new label."""
        matched_by = stored_matched_by(feature)
        if session_log is None:
            missing.append("session numbers not recorded: no --session-log")
            return matched_by
        usage, failure = self._session_log(session_log, session_dir).latest_usage(harness)
        session_id = usage.get("id") if isinstance(usage, dict) else None
        if failure or not isinstance(session_id, str):
            missing.append(failure or LATEST_NAMED_NO_SESSION % harness)
            return matched_by
        reply = SessionUsage(LATEST_USAGE % harness, harness, session_id, usage)
        if reply.rows is None:
            entry = reply.whole()
            missing.append(NO_ROWS % (reply.label, feature["name"]))
        else:
            entry = reply.slices(self._stored_evidence(document, history)).get(feature["name"])
        if entry is None:
            missing.append(NO_PROMPT_OF_FEATURE % (harness, session_id, feature["name"]))
            return matched_by
        upsert_session(feature, entry)
        return MATCH_IMPLEMENT if matched_by in (None, MATCH_NONE) else weakest([matched_by, MATCH_IMPLEMENT])

    def record_commit(self, plan, commit, harness, session_log=None, session_dir=None):
        stored_plan = self.project.stored(plan)
        document, warnings = self._load()
        feature = self._plan_owner(document, stored_plan, plan)
        history = self._history()
        commit_hash = history.resolve(commit)
        if commit_hash not in feature["sources"]["commits"]:
            added, removed = history.line_counts(commit_hash)
            feature["sources"]["commits"].append(commit_hash)
            feature["git"]["commits"] += 1
            feature["git"]["linesAdded"] += added
            feature["git"]["linesRemoved"] += removed
        stats = doc_stats(self.project.absolute(plan), self.plan_progress, self.environment.run)
        merge_plan(feature["plan"], stats)
        missing = list(stats["missing"])
        derive_usage(feature, self._record_latest_session(document, feature, history, harness, session_log, session_dir, missing))
        all_ticked = stats["tasks"] is not None and stats["completed"] == stats["tasks"]
        feature["status"] = DELIVERED if all_ticked else IN_PROGRESS
        feature[FILE_KEY] = self.store.update(feature)
        return {"feature": feature, "needsResize": all_ticked and feature["updatedStoryPoints"] is None,
                "missing": missing}, warnings

    def estimator(self, harness, session_log=None, session_dir=None):
        """Who estimates now (`{"estimator": {"harness", "models"}, "missing"}`): `harness` and the models of the last prompt of
        its latest session in the session folder (the project root by default), the prompt running now; null models when they
        cannot be read, named in `missing`."""
        models, missing = None, []
        if session_log is None:
            missing.append(NO_MODELS % "no --session-log")
        else:
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
