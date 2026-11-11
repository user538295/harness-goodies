"""The C2 feature rules: a new feature's defaults, the `save` upsert (target selection, latest value, the effort saved with each
size, the freeze of the pre-work size, the never-changed after-delivery size, scale membership, the kept id), the replacement of
the sessions, the usage totals derived from them, and the duplicates that several feature files hold. Everything here works on
plain JSON data; paths arrive already in their stored form, and each stored feature carries the name of its file under FILE_KEY."""
import json
import re

from estimation.errors import ArgumentError, EstimateError
from estimation.naming import slug
from estimation.text_rules import TEXT_KEYS, problems

ESTIMATED, IN_PROGRESS, DELIVERED = "estimated", "in-progress", "delivered"
STATUSES = (ESTIMATED, IN_PROGRESS, DELIVERED)
FROZEN_STATUSES = (IN_PROGRESS, DELIVERED)
HARNESSES = ("claude", "codex", "cursor", "omp", "opencode")
MATCH_NONE = "none"
MATCH_PROMPT = "prompt"
MATCH_IMPLEMENT = "implement"  # only in files an earlier version wrote; `collect` labels what it matches `prompt`
MATCHED_BY_LABELS = (MATCH_PROMPT, MATCH_IMPLEMENT, MATCH_NONE)
TOKEN_KEYS = ("input", "output", "reasoning", "cacheRead", "cacheWrite", "total")
PLAN_KEYS = ("phases", "tasks", "estimatedHours")
GIT_KEYS = ("commits", "linesAdded", "linesRemoved")
SOURCE_KEYS = ("docs", "files", "commits", "sessions")
SESSION_KEYS = ("harness", "models", "id", "path", "log", "prompts", "agentTimeMinutes", "tokens", "costUsd")
ENTRY_KEYS = ("id", "name", "description", "effort", "status", "storyPoints", "updatedStoryPoints", "estimatedAt", "estimatedBy",
              "updatedBy", "plan", "git", "usage", "sources")
SIZE_MAKERS = (("estimatedBy", "storyPoints"), ("updatedBy", "updatedStoryPoints"))  # who made a size, saved with that size
SIZES = tuple(size for _, size in SIZE_MAKERS)
MAKER_KEYS = ("harness", "models")
EFFORT_KEY = "effort"  # what makes the work big or small; explains the size saved with it
REPLACED_KEYS = ("name", "description", EFFORT_KEY, "status")
MATCHED_BY_KEY = "matchedBy"
ID_KEY = "id"
FILE_KEY = "file"  # the stored feature's file name; never part of the file itself
COST_DECIMALS = 2
FULL_HASH = re.compile(r"[0-9a-f]{40,64}")  # a SHA-1 or SHA-256 commit hash


def new_feature(name, description, effort):
    return {ID_KEY: None, "name": name, "description": description, EFFORT_KEY: effort, "status": ESTIMATED, "storyPoints": None,
            "updatedStoryPoints": None, "estimatedAt": None, "estimatedBy": None, "updatedBy": None,
            "plan": dict.fromkeys(PLAN_KEYS), "git": dict.fromkeys(GIT_KEYS, 0), "usage": None,
            "sources": {key: [] for key in SOURCE_KEYS}}


def files_of(features):
    """The file names of `features`, as messages list them."""
    return ", ".join(feature[FILE_KEY] for feature in features)


def _id_form(feature_id):
    """The id as its file name holds it (None without one): "PM-12" and "pm-12" are one id."""
    return None if feature_id is None else slug(feature_id)


def duplicates(features):
    """One warning for each name, id (by its file-name form) and doc that more than one feature holds (e.g. two merged
    branches), naming their files."""
    kinds = (("name '%s'", lambda feature: [feature["name"]], str),
             ("id '%s'", lambda feature: [] if feature.get(ID_KEY) is None else [feature[ID_KEY]], _id_form),
             ("doc %s", lambda feature: feature["sources"]["docs"], str))
    warnings = []
    for label, values, form in kinds:
        holders = {}
        for feature in features:
            for value in dict.fromkeys(values(feature)):
                holders.setdefault(form(value), (value, []))[1].append(feature)
        warnings += ["duplicate %s: %s" % (label % value, files_of(held)) for value, held in holders.values() if len(held) > 1]
    return warnings


def is_reference(feature):
    """Whether `feature` is a reference: delivered, with its size re-assessed after delivery."""
    return feature.get("status") == DELIVERED and feature.get("updatedStoryPoints") is not None


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
    """Refuses an entry whose containers have the wrong type, so the rules below can rely on them, and one whose given texts
    break the text rules (texts already stored are never re-checked)."""
    _require(isinstance(entry, dict), "entry must be a JSON object")
    unknown = [key for key in entry if key not in ENTRY_KEYS]
    if unknown:
        raise ArgumentError("unknown field '%s' (expected %s)" % (unknown[0], ", ".join(ENTRY_KEYS)))
    for key in ("plan", "git", "sources"):
        _require(key not in entry or isinstance(entry[key], dict), "%s must be an object" % key)
    _require(entry.get("usage") is None or isinstance(entry["usage"], dict), "usage must be an object or null")
    _require("name" not in entry or isinstance(entry["name"], str) and entry["name"].strip(), "name must be text")
    _require(EFFORT_KEY not in entry or isinstance(entry[EFFORT_KEY], str) and entry[EFFORT_KEY].strip(), "effort must be text")
    _require("description" not in entry or isinstance(entry["description"], str), "description must be text")
    found = problems({key: entry[key] for key in TEXT_KEYS if key in entry})
    _require(not found, "; ".join(found))
    _check_id(entry)
    _check_makers(entry)
    sources = entry.get("sources", {})
    for key in SOURCE_KEYS:
        _require(isinstance(sources.get(key, []), list), "sources.%s must be a list" % key)
    for key in ("docs", "files"):
        _require(all(isinstance(value, str) and value for value in sources.get(key, [])), "sources.%s must hold paths" % key)
    _require(all(isinstance(commit, str) and FULL_HASH.fullmatch(commit) for commit in sources.get("commits", [])),
             "sources.commits must hold full commit hashes")
    _require(all(isinstance(session, dict) and isinstance(session.get("harness"), str) and isinstance(session.get("id"), str)
                 for session in sources.get("sessions", [])), "each session needs a harness and an id")


def _check_id(entry):
    """Refuses an id that is not text, or that leaves nothing for its file name."""
    given = entry.get(ID_KEY)
    if given is None:
        return
    _require(isinstance(given, str) and given.strip(), "id must be text or null")
    _require(_id_form(given), "id '%s' has no letter or digit for its file name" % given)


def _is_maker(value):
    """Whether `value` names who made a size: {"harness": one of HARNESSES, "models": a list of names or null}."""
    if not isinstance(value, dict) or sorted(value) != sorted(MAKER_KEYS) or value["harness"] not in HARNESSES:
        return False
    models = value["models"]
    return models is None or isinstance(models, list) and all(isinstance(name, str) and name for name in models)


def _check_makers(entry):
    """Refuses who made a size without that size, or in another shape than `estimator` prints."""
    for maker, size in SIZE_MAKERS:
        if maker not in entry:
            continue
        _require(size in entry, "%s is saved with %s" % (maker, size))
        _require(entry[maker] is None or _is_maker(entry[maker]),
                 '%s must be {"harness": <harness>, "models": [<model>, ...] or null} or null' % maker)


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def scale_element(scale, value):
    """The scale's own element equal to `value`, None when it holds none; a boolean is never a number and 8 never equals "8"."""
    for element in scale:
        same_kind = is_number(value) and is_number(element) or isinstance(value, str) and isinstance(element, str)
        if same_kind and element == value:
            return element
    return None


def _scale_value(scale, value, field):
    """The scale's own element equal to `value` (null stays null)."""
    if value is None:
        return None
    element = scale_element(scale, value)
    if element is None:
        raise EstimateError("%s %s is not in the scale %s" % (field, _text(value), _text(scale)))
    return element


def _text(value):
    """A value as a message shows it: a string as is, anything else as JSON."""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _target(features, entry):
    """The one feature sharing a doc with the entry, else the one of the same name, else None (a new feature)."""
    docs = set(entry.get("sources", {}).get("docs", []))
    sharing = [feature for feature in features if docs.intersection(feature["sources"]["docs"])]
    if len(sharing) > 1:
        raise EstimateError("the entry's sources.docs matches %d features: %s" % (len(sharing), files_of(sharing)))
    if sharing:
        return sharing[0]
    named = [feature for feature in features if feature["name"] == entry.get("name")]
    if len(named) > 1:
        raise EstimateError("%d features are named '%s': %s" % (len(named), entry["name"], files_of(named)))
    return named[0] if named else None


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
        before = None if is_new else (feature["name"], _id_form(feature[ID_KEY]))
        if is_new and (not entry.get("name") or "description" not in entry):
            raise EstimateError("a new feature needs name and description")
        if (is_new or any(size in entry for size in SIZES)) and EFFORT_KEY not in entry:
            raise ArgumentError("a size needs its effort: '%s'" % (entry.get("name") or feature["name"]))
        if is_new:
            feature = new_feature(entry["name"], entry["description"], entry[EFFORT_KEY])
        frozen = feature["status"] in FROZEN_STATUSES
        kept_effort = feature.get(EFFORT_KEY) if self._ignores_its_sizes(feature, frozen) else None
        for key in REPLACED_KEYS:
            if key in entry:
                feature[key] = entry[key]
        if kept_effort is not None:
            feature[EFFORT_KEY] = kept_effort
        if feature["status"] not in STATUSES:
            raise EstimateError("unknown status '%s' (expected %s)" % (feature["status"], ", ".join(STATUSES)))
        self._id(feature)
        self._sizes(feature, frozen)
        if "plan" in entry:
            merge_plan(feature["plan"], entry["plan"])
        self._sources(feature)
        self._usage(feature)
        self._check_unique(feature, before)
        if is_new:
            self.document["features"].append(feature)
        return feature

    def _ignores_its_sizes(self, feature, frozen):
        """Whether the entry carries a size and the save ignores each one it carries (a frozen `storyPoints`, an
        `updatedStoryPoints` already set): the stored effort then keeps explaining the size it was saved with."""
        ignored = {"storyPoints": frozen, "updatedStoryPoints": feature["updatedStoryPoints"] is not None}
        carried = [size for size in SIZES if size in self.entry]
        return bool(carried) and all(ignored[size] for size in carried)

    def _check_unique(self, feature, before):
        """Refuses a name or id (by its file-name form) that this save gives the feature while another feature holds it;
        `before` is the stored (name, id form), None for a new feature. One the feature already shared stays a duplicate warning."""
        others = [other for other in self.document["features"] if other is not feature]
        if before is None or feature["name"] != before[0]:
            named = [other for other in others if other["name"] == feature["name"]]
            if named:
                raise EstimateError("another feature is named '%s': %s" % (feature["name"], files_of(named)))
        form = _id_form(feature[ID_KEY])
        if form is not None and (before is None or form != before[1]):
            held = [other for other in others if _id_form(other.get(ID_KEY)) == form]
            if held:
                raise EstimateError("another feature has the id '%s': %s" % (feature[ID_KEY], files_of(held)))

    def _id(self, feature):
        """Takes the entry's id; a missing or null one never erases the stored id, a different one replaces it with a warning."""
        given = self.entry.get(ID_KEY)
        if given is None or given == feature[ID_KEY]:
            return
        if feature[ID_KEY] is not None:
            self.warnings.append("id %s replaces %s for '%s'" % (given, feature[ID_KEY], feature["name"]))
        feature[ID_KEY] = given

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
                feature["estimatedBy"] = None if value is None else entry.get("estimatedBy")
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
                feature["updatedBy"] = None if value is None else entry.get("updatedBy")
            elif value != feature["updatedStoryPoints"]:
                self._ignore(feature, "updatedStoryPoints", value, "a set after-delivery size is never changed")

    def _sources(self, feature):
        given = self.entry.get("sources", {})
        stored = feature["sources"]
        for key in ("docs", "files"):
            stored[key] += [value for value in dict.fromkeys(given.get(key, [])) if value not in stored[key]]
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
