"""The C3 commands. Each returns (payload printed as JSON on stdout, warnings printed as `estimate:` lines on stderr)."""
import json

from estimation.doc_stats import doc_stats
from estimation.errors import ArgumentError, EstimateError
from estimation.features import (DELIVERED, IN_PROGRESS, MATCH_IMPLEMENT, MATCH_NONE, Upsert, check_entry_shape, derive_usage,
                                 merge_plan, stored_matched_by, upsert_session, weakest)
from estimation.git_history import GitHistory
from estimation.sessions import FeatureQuery, SessionLog, SessionMatcher, session_from_usage
from estimation.task_ids import mentions_any

STDIN_MARK = "-"


class Commands:
    def __init__(self, project, store, environment, plan_progress):
        """`plan_progress` is the default `plan-progress.sh` (the /implement skill's, next to this skill)."""
        self.project = project
        self.store = store
        self.environment = environment
        self.plan_progress = plan_progress

    def _json_argument(self, text, what):
        """The JSON of an argument (`-` reads stdin); unparseable JSON is a bad argument."""
        if text == STDIN_MARK:
            text = self.environment.stdin.read()
        try:
            return json.loads(text)
        except ValueError as error:
            raise ArgumentError("%s is not JSON: %s" % (what, error)) from error

    def read(self, references):
        document = self.store.load()
        if references:
            document["features"] = [feature for feature in document["features"]
                                    if feature.get("status") == DELIVERED and feature.get("updatedStoryPoints") is not None]
        return document, []

    def save(self, entry_text):
        entry = self._json_argument(entry_text, "entry")
        check_entry_shape(entry)
        sources = entry.get("sources")
        if sources and "docs" in sources:
            sources["docs"] = [self.project.stored(doc) for doc in sources["docs"]]
        document = self.store.load()
        upsert = Upsert(document, entry, self.environment.today())
        feature = upsert.apply()
        self.store.save(document)
        return feature, upsert.warnings

    def remove(self, name):
        document = self.store.load()
        removed = next((feature for feature in document["features"] if feature.get("name") == name), None)
        if removed is None:
            raise EstimateError("no feature named '%s'" % name)
        document["features"].remove(removed)
        self.store.save(document)
        return removed, []

    def doc_stats(self, doc, plan_progress=None):
        script = self.plan_progress if plan_progress is None else self.project.absolute(plan_progress)
        return doc_stats(self.project.absolute(doc), script, self.environment.run), []

    def git_stats(self, docs, task_ids):
        if not docs and not task_ids:
            raise ArgumentError("git-stats needs --doc or --task-id")
        history = GitHistory(self.project.root, self.environment.run)
        commits = history.commits()
        changing_docs = history.changing([self.project.stored(doc) for doc in docs])
        matched = [commit for commit in commits if commit.hash in changing_docs or mentions_any(commit.message, task_ids)]
        dates = [commit.date for commit in matched]
        return {"commits": [commit.hash for commit in matched], "count": len(matched),
                "linesAdded": sum(commit.added for commit in matched),
                "linesRemoved": sum(commit.removed for commit in matched),
                "firstDate": min(dates, default=None), "lastDate": max(dates, default=None)}, []

    def _feature_queries(self, payload):
        items = payload.get("features") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise ArgumentError('features input must be {"features": [...]}')
        queries = []
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"]:
                raise ArgumentError("each feature needs a name")
            docs, task_ids = item.get("docs") or [], item.get("taskIds") or []
            if not all(isinstance(value, str) and value for value in [*docs, *task_ids]):
                raise ArgumentError("docs and taskIds of '%s' must be lists of strings" % item["name"])
            queries.append(FeatureQuery(item["name"], [self.project.stored(doc) for doc in docs], task_ids,
                                        item.get("firstDate"), item.get("lastDate")))
        return queries

    def _session_log(self, script):
        return SessionLog(self.project.absolute(script), self.project.root, self.environment.run)

    def match_sessions(self, features_text, session_log):
        queries = self._feature_queries(self._json_argument(features_text, "features input"))
        return SessionMatcher(self._session_log(session_log), self.environment.to_local).match(queries), []

    def _plan_owner(self, document, stored_plan, given_plan):
        owners = [feature for feature in document["features"] if stored_plan in feature["sources"]["docs"]]
        if not owners:
            raise EstimateError("no feature lists %s; run /estimate %s first" % (stored_plan, given_plan))
        if len(owners) > 1:
            raise EstimateError("%d features list %s: %s" % (len(owners), stored_plan,
                                                             ", ".join(feature["name"] for feature in owners)))
        return owners[0]

    def _record_latest_session(self, feature, harness, session_log, missing):
        """Upserts the harness's latest session of the project (matched by implement); returns the feature's new label."""
        matched_by = stored_matched_by(feature)
        if session_log is None:
            missing.append("session numbers not recorded: no --session-log")
            return matched_by
        usage, failure = self._session_log(session_log).latest_usage(harness)
        session_id = usage.get("id") if isinstance(usage, dict) else None
        if failure or not isinstance(session_id, str):
            missing.append(failure or "session-log %s usage --latest named no session" % harness)
            return matched_by
        upsert_session(feature, session_from_usage(harness, session_id, usage))
        return MATCH_IMPLEMENT if matched_by in (None, MATCH_NONE) else weakest([matched_by, MATCH_IMPLEMENT])

    def record_commit(self, plan, commit, harness, session_log=None):
        stored_plan = self.project.stored(plan)
        document = self.store.load()
        feature = self._plan_owner(document, stored_plan, plan)
        history = GitHistory(self.project.root, self.environment.run)
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
        derive_usage(feature, self._record_latest_session(feature, harness, session_log, missing))
        all_ticked = stats["tasks"] is not None and stats["completed"] == stats["tasks"]
        feature["status"] = DELIVERED if all_ticked else IN_PROGRESS
        self.store.save(document)
        return {"feature": feature, "needsResize": all_ticked and feature["updatedStoryPoints"] is None, "missing": missing}, []
