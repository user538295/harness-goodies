"""The C2 feature rules: a new feature's defaults, the `save` upsert (target selection, latest value, the freeze of the pre-work
size, the never-changed after-delivery size, scale membership), the replacement of the sessions and the usage totals derived from
them. Everything here works on plain JSON data; paths arrive already in their stored form."""
import json

from estimation.errors import ArgumentError, EstimateError

ESTIMATED, IN_PROGRESS, DELIVERED = "estimated", "in-progress", "delivered"
STATUSES = (ESTIMATED, IN_PROGRESS, DELIVERED)
FROZEN_STATUSES = (IN_PROGRESS, DELIVERED)
HARNESSES = ("claude", "codex", "cursor", "omp", "opencode")
MATCH_NONE = "none"
MATCH_PROMPT = "prompt"
MATCH_IMPLEMENT = "implement"
MATCH_STRENGTH = (MATCH_PROMPT, MATCH_IMPLEMENT)  # weakest first
MATCHED_BY_LABELS = MATCH_STRENGTH + (MATCH_NONE,)
TOKEN_KEYS = ("input", "output", "reasoning", "cacheRead", "cacheWrite", "total")
PLAN_KEYS = ("phases", "tasks", "estimatedHours")
GIT_KEYS = ("commits", "linesAdded", "linesRemoved")
SOURCE_KEYS = ("docs", "commits", "sessions")
SESSION_KEYS = ("harness", "id", "path", "log", "prompts", "agentTimeMinutes", "tokens", "costUsd")
ENTRY_KEYS = ("name", "description", "status", "storyPoints", "updatedStoryPoints", "estimatedAt", "plan", "git", "usage",
              "sources")
REPLACED_KEYS = ("name", "description", "status")
MATCHED_BY_KEY = "matchedBy"
COST_DECIMALS = 2


def new_feature(name, description):
    return {"name": name, "description": description, "status": ESTIMATED, "storyPoints": None, "updatedStoryPoints": None,
            "estimatedAt": None, "plan": dict.fromkeys(PLAN_KEYS), "git": dict.fromkeys(GIT_KEYS, 0), "usage": None,
            "sources": {key: [] for key in SOURCE_KEYS}}


def weakest(labels):
    """The weakest of the match labels (implement > prompt)."""
    return min(labels, key=MATCH_STRENGTH.index)


def session_entry(fields):
    """A C2 session entry from `fields`, every missing number null."""
    tokens = fields.get("tokens") or {}
    entry = {key: fields.get(key) for key in SESSION_KEYS}
    entry["tokens"] = {key: tokens.get(key) for key in TOKEN_KEYS}
    return entry


def merge_plan(plan, given):
    """Takes each count `given` knows into `plan`: a null (the document does not say) never overwrites a known count."""
    plan.update({key: given[key] for key in PLAN_KEYS if given.get(key) is not None})


def upsert_session(feature, session):
    """Adds `session`, or replaces the stored one of the same harness and id with its newer numbers."""
    sessions = feature["sources"]["sessions"]
    key = (session["harness"], session["id"])
    for index, stored in enumerate(sessions):
        if (stored["harness"], stored["id"]) == key:
            sessions[index] = session
            return
    sessions.append(session)


def _total(values):
    """The sum, or null when there is nothing to add or any value is unknown."""
    return None if not values or any(value is None for value in values) else sum(values)


def derive_usage(feature, matched_by):
    """Sets the feature's usage from its sessions (C2: totals are never taken from input); null without a match label."""
    if matched_by is None:
        feature["usage"] = None
        return
    sessions = feature["sources"]["sessions"]
    cost = _total([session["costUsd"] for session in sessions])
    feature["usage"] = {
        "matchedBy": matched_by,
        "sessions": len(sessions),
        "agentTimeMinutes": _total([session["agentTimeMinutes"] for session in sessions]),
        "tokens": {key: _total([session["tokens"][key] for session in sessions]) for key in TOKEN_KEYS},
        "costUsd": None if cost is None else round(cost, COST_DECIMALS),
    }


def stored_matched_by(feature):
    return None if feature["usage"] is None else feature["usage"][MATCHED_BY_KEY]


def _require(condition, message):
    if not condition:
        raise ArgumentError(message)


def check_entry_shape(entry):
    """Refuses an entry whose containers have the wrong type, so the rules below can rely on them."""
    _require(isinstance(entry, dict), "entry must be a JSON object")
    unknown = [key for key in entry if key not in ENTRY_KEYS]
    if unknown:
        raise ArgumentError("unknown field '%s' (expected %s)" % (unknown[0], ", ".join(ENTRY_KEYS)))
    for key in ("plan", "git", "sources"):
        _require(key not in entry or isinstance(entry[key], dict), "%s must be an object" % key)
    _require(entry.get("usage") is None or isinstance(entry["usage"], dict), "usage must be an object or null")
    sources = entry.get("sources", {})
    for key in SOURCE_KEYS:
        _require(isinstance(sources.get(key, []), list), "sources.%s must be a list" % key)
    _require(all(isinstance(doc, str) and doc for doc in sources.get("docs", [])), "sources.docs must hold paths")
    _require(all(isinstance(session, dict) and isinstance(session.get("harness"), str) and isinstance(session.get("id"), str)
                 for session in sources.get("sessions", [])), "each session needs a harness and an id")


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _scale_value(scale, value, field):
    """The scale's own element equal to `value` (null stays null); a boolean is never a number and 8 never equals "8"."""
    if value is None:
        return None
    for element in scale:
        same_kind = _is_number(value) and _is_number(element) or isinstance(value, str) and isinstance(element, str)
        if same_kind and element == value:
            return element
    raise EstimateError("%s %s is not in the scale %s" % (field, _text(value), _text(scale)))


def _text(value):
    """A value as a message shows it: a string as is, anything else as JSON."""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _target(features, entry):
    """The one feature sharing a doc with the entry, else the one of the same name, else None (a new feature)."""
    docs = set(entry.get("sources", {}).get("docs", []))
    sharing = [feature for feature in features if docs.intersection(feature["sources"]["docs"])]
    if len(sharing) > 1:
        raise EstimateError("the entry's sources.docs matches %d features: %s" % (
            len(sharing), ", ".join(feature["name"] for feature in sharing)))
    if sharing:
        return sharing[0]
    return next((feature for feature in features if feature["name"] == entry.get("name")), None)


class Upsert:
    """One `save`: applies the entry to its target feature in `document` and collects the warnings of ignored values."""

    def __init__(self, document, entry, today):
        self.document = document
        self.entry = entry
        self.today = today
        self.warnings = []

    def apply(self):
        entry = self.entry
        feature = _target(self.document["features"], entry)
        is_new = feature is None
        if is_new:
            if not entry.get("name") or "description" not in entry:
                raise EstimateError("a new feature needs name and description")
            feature = new_feature(entry["name"], entry["description"])
        frozen = feature["status"] in FROZEN_STATUSES
        for key in REPLACED_KEYS:
            if key in entry:
                feature[key] = entry[key]
        if feature["status"] not in STATUSES:
            raise EstimateError("unknown status '%s' (expected %s)" % (feature["status"], ", ".join(STATUSES)))
        self._sizes(feature, frozen)
        if "plan" in entry:
            merge_plan(feature["plan"], entry["plan"])
        self._sources(feature)
        self._usage(feature)
        if any(other is not feature and other["name"] == feature["name"] for other in self.document["features"]):
            raise EstimateError("another feature is named '%s'" % feature["name"])
        if is_new:
            self.document["features"].append(feature)
        return feature

    def _ignore(self, feature, key, value, reason):
        self.warnings.append("%s %s ignored for '%s': %s (kept %s)" % (key, _text(value), feature["name"], reason,
                                                                      _text(feature[key])))

    def _sizes(self, feature, frozen):
        entry, scale = self.entry, self.document["scale"]
        frozen_reason = "frozen since the feature is %s" % feature["status"]
        if "storyPoints" in entry:
            value = _scale_value(scale, entry["storyPoints"], "storyPoints")
            if frozen and value != feature["storyPoints"]:
                self._ignore(feature, "storyPoints", value, frozen_reason)
            elif not frozen:
                feature["storyPoints"] = value
                if value is not None and "estimatedAt" not in entry:
                    feature["estimatedAt"] = self.today.isoformat()
        if "estimatedAt" in entry:
            if frozen and entry["estimatedAt"] != feature["estimatedAt"]:
                self._ignore(feature, "estimatedAt", entry["estimatedAt"], frozen_reason)
            elif not frozen:
                feature["estimatedAt"] = entry["estimatedAt"]
        if "updatedStoryPoints" in entry:
            value = _scale_value(scale, entry["updatedStoryPoints"], "updatedStoryPoints")
            if feature["updatedStoryPoints"] is None:
                feature["updatedStoryPoints"] = value
            elif value != feature["updatedStoryPoints"]:
                self._ignore(feature, "updatedStoryPoints", value, "a set after-delivery size is never changed")

    def _sources(self, feature):
        given = self.entry.get("sources", {})
        stored = feature["sources"]
        stored["docs"] += [doc for doc in dict.fromkeys(given.get("docs", [])) if doc not in stored["docs"]]
        if ("git" in self.entry) != ("commits" in given):
            raise EstimateError("git and sources.commits are saved together")
        if "git" in self.entry:
            feature["git"] = {key: self.entry["git"].get(key, 0) for key in GIT_KEYS}
            stored["commits"] = list(dict.fromkeys(given["commits"]))
        if "sessions" in given:
            stored["sessions"] = []
            for session in given["sessions"]:
                upsert_session(feature, session_entry(session))

    def _usage(self, feature):
        given = self.entry.get("usage") or {}
        for key in given:
            if key != MATCHED_BY_KEY:
                self.warnings.append("usage.%s is derived from sources.sessions; ignored" % key)
        matched_by = given.get(MATCHED_BY_KEY, stored_matched_by(feature))
        if matched_by is not None and matched_by not in MATCHED_BY_LABELS:
            raise EstimateError("unknown usage.matchedBy '%s' (expected %s)" % (matched_by, ", ".join(MATCHED_BY_LABELS)))
        if matched_by is None and feature["sources"]["sessions"]:
            raise EstimateError("sessions need usage.matchedBy (%s) the first time" % ", ".join(MATCHED_BY_LABELS))
        derive_usage(feature, matched_by)
