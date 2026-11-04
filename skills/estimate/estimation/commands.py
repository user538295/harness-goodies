"""The C3 commands. Each returns (payload printed as JSON on stdout, warnings printed as `estimate:` lines on stderr); every command
that reads the estimate files also warns about each name, id and doc that several feature files hold."""
import json

from estimation.doc_stats import doc_stats
from estimation.errors import ArgumentError, EstimateError
from estimation.evidence import SHARED_TASK_IDS, Evidence, FeatureClaim, feature_commits, shared_task_ids
from estimation.features import (DELIVERED, FILE_KEY, IN_PROGRESS, MATCH_IMPLEMENT, MATCH_NONE, Upsert, check_entry_shape,
                                 derive_usage, duplicates, files_of, merge_plan, stored_matched_by, upsert_session, weakest)
from estimation.first_day import first_day
from estimation.git_history import GitHistory
from estimation.sessions import NO_ROWS, SessionLog, SessionMatcher, SessionUsage

STDIN_MARK = "-"
NO_PROMPT_OF_FEATURE = "the latest %s session %s shows no prompt of %s"


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

    def _load(self):
        """The stored document and the duplicate warnings of its features."""
        document = self.store.load()
        return document, duplicates(document["features"])

    def read(self, references):
        document, warnings = self._load()
        if references:
            document["features"] = [feature for feature in document["features"]
                                    if feature.get("status") == DELIVERED and feature.get("updatedStoryPoints") is not None]
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
        """The FeatureClaims of a `{"features": [{"name", "docs", "taskIds"}]}` argument, in its order."""
        payload = self._json_argument(features_text, "features input")
        items = payload.get("features") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise ArgumentError('features input must be {"features": [...]}')
        features = []
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"]:
                raise ArgumentError("each feature needs a name")
            docs, task_ids = item.get("docs") or [], item.get("taskIds") or []
            if not all(isinstance(value, str) and value for value in [*docs, *task_ids]):
                raise ArgumentError("docs and taskIds of '%s' must be lists of strings" % item["name"])
            features.append(FeatureClaim(item["name"], tuple(self.project.stored(doc) for doc in docs), tuple(task_ids)))
        return features

    def _history(self):
        return GitHistory(self.project.root, self.environment.run)

    def git_stats(self, features_text):
        features = self._features(features_text)
        found = feature_commits(self._history(), features)
        shared = shared_task_ids(features)
        stats = [{"name": name, "commits": [commit.hash for commit in commits], "count": len(commits),
                  "linesAdded": sum(commit.added for commit in commits), "linesRemoved": sum(commit.removed for commit in commits)}
                 for name, commits in found.items()]
        return {"features": stats}, [SHARED_TASK_IDS % ", ".join(shared)] if shared else []

    def _session_log(self, script):
        return SessionLog(self.project.absolute(script), self.project.root, self.environment.run)

    def match_sessions(self, features_text, session_log):
        features = self._features(features_text)
        authored = {name: {commit.hash: commit.authored_at for commit in commits}
                    for name, commits in feature_commits(self._history(), features).items()}
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

    def _record_latest_session(self, document, feature, history, harness, session_log, missing):
        """Upserts the feature's slice of the harness's latest session of the project (matched by implement); returns the feature's
        new label."""
        matched_by = stored_matched_by(feature)
        if session_log is None:
            missing.append("session numbers not recorded: no --session-log")
            return matched_by
        usage, failure = self._session_log(session_log).latest_usage(harness)
        session_id = usage.get("id") if isinstance(usage, dict) else None
        if failure or not isinstance(session_id, str):
            missing.append(failure or "session-log %s usage --latest named no session" % harness)
            return matched_by
        reply = SessionUsage("session-log %s usage --latest" % harness, harness, session_id, usage)
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

    def record_commit(self, plan, commit, harness, session_log=None):
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
        derive_usage(feature, self._record_latest_session(document, feature, history, harness, session_log, missing))
        all_ticked = stats["tasks"] is not None and stats["completed"] == stats["tasks"]
        feature["status"] = DELIVERED if all_ticked else IN_PROGRESS
        feature[FILE_KEY] = self.store.update(feature)
        return {"feature": feature, "needsResize": all_ticked and feature["updatedStoryPoints"] is None,
                "missing": missing}, warnings
