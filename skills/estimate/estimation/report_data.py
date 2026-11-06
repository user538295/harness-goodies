"""The report's DATA: `analyse(document, changes, project, today)` turns the stored features (the `read` shape) and the git
changes of every commit the reference features list (`GitHistory.changes`) into one JSON-serialisable dict, the only contract
between the data, the findings and the page. No I/O happens here.

A reference is a delivered feature with an after-delivery SP; each becomes one row. A commit listed by k references gives each of
them 1/k of its lines per kind (line_kinds), so the per-kind lines add up to the commits' lines; the vendored kind (committed
dependency files) is shown but never counted, so git's counted lines, the stored churn's check and the line totals leave it out,
as `git-stats` does. Per metric come the Spearman/Pearson/concordance statistics (also on the "core": without the largest SP
buckets that together hold at most a tenth of the rows), box summaries and adjacent-bucket separation per SP, and a power fit in
log space whose reverse fit predicts an SP from the metric; the combined fit averages those predictions over FIT_METRICS.
Forecasts grade the SP estimated before work (storyPoints) against the delivered one in scale steps. Every other feature that
has an estimate (`pending`: estimated, in progress, or delivered but not re-sized) gets a row of its estimated SP and plan values
only; no statistic uses it. Floats are rounded: ratios and correlations to RATIO_DECIMALS, lines to LINE_DECIMALS, fit
coefficients to COEFFICIENT_DECIMALS."""
import math
from collections import Counter

from estimation.errors import EstimateError
from estimation.features import DELIVERED, TOKEN_KEYS, is_reference
from estimation.line_kinds import KINDS, RULES, VENDORED, kind_of
from estimation.statistics import (bootstrap_interval, box, concordance, least_squares, median, nearest_step, pearson,
                                   separation, spearman)
from estimation.store import DATA_FOLDER, FEATURES_FOLDER

RATIO_DECIMALS = 3
LINE_DECIMALS = 1
COEFFICIENT_DECIMALS = 6
SHORT_HASH = 8
LABEL_SEPARATOR = " · "
BUCKET_ARROW = "→"
METRICS = (("tasks", "Tasks"), ("hours", "Estimated hours"), ("commits_excl", "Commits (exclusive)"), ("code", "Code lines"),
           ("phases", "Phases"), ("churn", "Lines changed (raw)"), ("commits", "Commits (raw)"), ("src", "Source lines"))
MATRIX_VARIABLES = (("sp", "Story points"), ("phases", "Phases"), ("tasks", "Tasks"), ("hours", "Est. hours"),
                    ("commits", "Commits (raw)"), ("commits_excl", "Commits (excl.)"), ("churn", "Lines (raw)"),
                    ("code", "Code lines"), ("src", "Source lines"))
LINE_METRICS = frozenset(("code", "src", "churn"))
FIT_METRICS = ("tasks", "commits_excl", "code", "hours")
CODE_KINDS = ("src", "tests")
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 7
BOOTSTRAP_LEVEL = 0.95
CORE_DROP_SHARE = 0.10
MIDSIZE_SP = 5  # the skill's "a mid-size feature being 5"
MIN_BUCKET = 3
SHARED_TOP = 8
PARTIAL_FACTOR = 3
MIN_PARTIAL_ROWS = 3
MINUTES_PER_HOUR = 60
PALETTE = ("#14b8a6", "#0ea5e9", "#3b82f6", "#6366f1", "#8b5cf6", "#a855f7", "#d946ef", "#ec4899", "#f43f5e")
SINGLE_COLOR = 4  # the middle anchor
HALF_UP = 0.5
NUMERIC_SCALE = "report needs a numeric scale (every value a number); found %s"
NO_REFERENCE = "no delivered feature has an after-delivery SP; run /estimate init"
OFF_SCALE = "SP %s of '%s' is not on the scale"


def _round(value, decimals):
    return None if value is None else round(value, decimals)


def _ratio(value):
    return _round(value, RATIO_DECIMALS)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def sp_key(value):
    """The JavaScript number-to-string form of an SP: "5" for 5 or 5.0, "0.5" for 0.5."""
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def _label(feature):
    return feature["name"] if feature.get("id") is None else feature["id"] + LABEL_SEPARATOR + feature["name"]


def _step(scale, value, label):
    try:
        return scale.index(value)
    except ValueError:
        raise EstimateError(OFF_SCALE % (value, label)) from None


# ---- rows -------------------------------------------------------------------------------------------------------------------

def _listers(references):
    """{commit hash: labels of the references listing it}, in store order."""
    listers = {}
    for feature in references:
        for commit_hash in dict.fromkeys(feature["sources"]["commits"]):
            listers.setdefault(commit_hash, []).append(_label(feature))
    return listers


def _kind_lines(commit):
    lines = dict.fromkeys(KINDS, 0)
    for file in commit.files:
        lines[kind_of(file.path)] += file.added + file.removed
    return lines


def _counted_lines(commit):
    """The commit's lines changed without committed dependency files (the vendored kind), as `git-stats` counts them."""
    return sum(file.added + file.removed for file in commit.files if kind_of(file.path) != VENDORED)


def _usage_fields(usage):
    if usage is None:
        return {"sessions": 0, "agent_min": None, "cost": None, "tokens": None, "cache_read": None, "matched": "none"}
    tokens = usage.get("tokens") or {}
    return {"sessions": usage.get("sessions") or 0, "agent_min": usage.get("agentTimeMinutes"), "cost": usage.get("costUsd"),
            "tokens": tokens.get("total"), "cache_read": tokens.get("cacheRead"), "matched": usage.get("matchedBy") or "none"}


def _row(feature, listers, changes, kind_lines):
    commits = list(dict.fromkeys(feature["sources"]["commits"]))
    held = [changes[commit_hash] for commit_hash in commits if commit_hash in changes]
    kinds = dict.fromkeys(KINDS, 0.0)
    for commit in held:
        for kind, lines in kind_lines[commit.hash].items():
            kinds[kind] += lines / len(listers[commit.hash])
    plan, git = feature.get("plan") or {}, feature.get("git") or {}
    days = sorted(commit.authored_at.date().isoformat() for commit in held)
    estimated_by = feature.get("estimatedBy")
    row = {"label": _label(feature), "name": feature["name"], "id": feature.get("id"), "desc": feature.get("description"),
           "sp": feature["updatedStoryPoints"], "est": feature.get("storyPoints"), "estimated_at": feature.get("estimatedAt"),
           "estimated_by": estimated_by["harness"] if estimated_by else None,
           "phases": plan.get("phases"), "tasks": plan.get("tasks"), "hours": plan.get("estimatedHours"),
           "commits": git.get("commits", 0),
           "commits_excl": sum(1 for commit_hash in commits if len(listers[commit_hash]) == 1),
           "added": git.get("linesAdded", 0), "removed": git.get("linesRemoved", 0)}
    row["churn"] = row["added"] + row["removed"]
    row.update({kind: round(kinds[kind], LINE_DECIMALS) for kind in KINDS})
    row["code"] = round(sum(kinds[kind] for kind in CODE_KINDS), LINE_DECIMALS)
    row["git_churn"] = sum(_counted_lines(commit) for commit in held)
    row["missing"] = len(commits) - len(held)
    row.update(_usage_fields(feature.get("usage")))
    row.update({"first": days[0] if days else None, "last": days[-1] if days else None})
    return row


def _pending_row(feature):
    """A feature that is only an estimate (no after-delivery SP): its estimated SP and the plan values; no git or usage."""
    plan, estimated_by = feature.get("plan") or {}, feature.get("estimatedBy")
    return {"label": _label(feature), "name": feature["name"], "id": feature.get("id"), "desc": feature.get("description"),
            "status": feature["status"], "sp": feature["storyPoints"], "estimated_at": feature.get("estimatedAt"),
            "estimated_by": estimated_by["harness"] if estimated_by else None,
            "phases": plan.get("phases"), "tasks": plan.get("tasks"), "hours": plan.get("estimatedHours")}


# ---- per-metric statistics --------------------------------------------------------------------------------------------------

def _pairs(rows, metric, keep=lambda row: True):
    chosen = [row for row in rows if row[metric] is not None and keep(row)]
    return [row["sp"] for row in chosen], [row[metric] for row in chosen]


def _core(rows):
    """The SP buckets left after dropping the largest ones while the dropped rows stay within CORE_DROP_SHARE; None when none."""
    sizes = Counter(row["sp"] for row in rows)
    dropped, count = [], 0
    for sp in sorted(sizes, reverse=True)[:-1]:
        if count + sizes[sp] > CORE_DROP_SHARE * len(rows):
            break
        dropped.append(sp)
        count += sizes[sp]
    if not dropped:
        return None
    return {"max_sp": max(sp for sp in sizes if sp not in dropped), "dropped": sorted(dropped), "n_dropped": count}


def _log_pearson(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x > 0 and y > 0]
    return pearson([math.log(x) for x, _ in pairs], [math.log(y) for _, y in pairs])


def _result(rows, metric, label, core):
    sizes, values = _pairs(rows, metric)
    interval = bootstrap_interval(sizes, values, spearman, BOOTSTRAP_RESAMPLES, BOOTSTRAP_SEED, BOOTSTRAP_LEVEL)
    result = {"label": label, "n": len(values), "spearman": _ratio(spearman(sizes, values)),
              "ci": None if interval is None else [_ratio(bound) for bound in interval],
              "pearson": _ratio(pearson(sizes, values)), "pearson_log": _ratio(_log_pearson(sizes, values)),
              "conc": _ratio(concordance(sizes, values)), "n_core": None, "spearman_core": None, "conc_core": None}
    if core is not None:
        core_sizes, core_values = _pairs(rows, metric, lambda row: row["sp"] <= core["max_sp"])
        result.update({"n_core": len(core_values), "spearman_core": _ratio(spearman(core_sizes, core_values)),
                       "conc_core": _ratio(concordance(core_sizes, core_values))})
    return result


def _prediction(rows, metric, scale):
    chosen = [row for row in rows if row[metric] is not None and row[metric] > 0 and row["sp"] > 0]
    log_sizes = [math.log(row["sp"]) for row in chosen]
    log_values = [math.log(row[metric]) for row in chosen]
    forward, reverse = least_squares(log_sizes, log_values), least_squares(log_values, log_sizes)
    if forward is None or reverse is None:
        return None
    (intercept, exponent, r2), (intercept_rev, slope_rev, r2_rev) = forward, reverse
    distances = [abs(nearest_step(scale, math.exp(intercept_rev + slope_rev * value)) - _step(scale, row["sp"], row["label"]))
                 for row, value in zip(chosen, log_values)]
    return {"exp": _round(exponent, COEFFICIENT_DECIMALS), "a": _round(intercept, COEFFICIENT_DECIMALS), "r2": _ratio(r2),
            "a2": _round(intercept_rev, COEFFICIENT_DECIMALS), "b2": _round(slope_rev, COEFFICIENT_DECIMALS),
            "r2rev": _ratio(r2_rev),
            "exact": _ratio(distances.count(0) / len(chosen)),
            "within1": _ratio(sum(1 for distance in distances if distance <= 1) / len(chosen)), "n": len(chosen)}


def _buckets(rows, metric):
    """{SP: non-null values of `metric`} in ascending SP order."""
    buckets = {}
    for row in sorted(rows, key=lambda row: row["sp"]):
        if row[metric] is not None:
            buckets.setdefault(row["sp"], []).append(row[metric])
    return buckets


def _box(values, metric):
    decimals = LINE_DECIMALS if metric in LINE_METRICS else RATIO_DECIMALS
    return {key: _round(value, decimals) if key != "n" else value for key, value in box(values).items()}


def _separations(buckets):
    sizes = list(buckets)
    return {sp_key(lower) + BUCKET_ARROW + sp_key(higher):
            [_ratio(separation(buckets[lower], buckets[higher])), len(buckets[lower]), len(buckets[higher])]
            for lower, higher in zip(sizes, sizes[1:])}


def _matrix(rows):
    names = [name for name, _ in MATRIX_VARIABLES]
    corr, counts = [], []
    for first in names:
        corr.append([])
        counts.append([])
        for second in names:
            chosen = [row for row in rows if row[first] is not None and row[second] is not None]
            counts[-1].append(len(chosen))
            corr[-1].append(1.0 if first == second else
                            _ratio(spearman([row[first] for row in chosen], [row[second] for row in chosen])))
    return corr, counts


# ---- sizes, fit, palette ----------------------------------------------------------------------------------------------------

def _most_common(values):
    """The most common value, ties to the smaller; None for no values."""
    counts = Counter(values)
    return min(counts, key=lambda value: (-counts[value], value)) if counts else None


def _base_sp(rows):
    sizes = [row["sp"] for row in rows]
    if sizes.count(MIDSIZE_SP) >= MIN_BUCKET:
        return MIDSIZE_SP
    return _most_common([size for size in sizes if size > 0])


def _apply_fit(rows, predictions, scale):
    """Sets each row's combined fit (fit_sp, fit_step, resid, resid_log); None where no FIT_METRICS value can be used."""
    for row in rows:
        estimates = [predictions[metric]["a2"] + predictions[metric]["b2"] * math.log(row[metric]) for metric in FIT_METRICS
                     if predictions[metric] is not None and row[metric] is not None and row[metric] > 0]
        row.update(dict.fromkeys(("fit_sp", "fit_step", "resid", "resid_log")))
        if row["sp"] <= 0 or not estimates:
            continue
        fitted = math.exp(sum(estimates) / len(estimates))
        row["fit_step"] = nearest_step(scale, fitted)
        row.update({"fit_sp": _ratio(fitted), "resid": _step(scale, row["sp"], row["label"]) - row["fit_step"],
                    "resid_log": _ratio(math.log(row["sp"]) - math.log(fitted))})


def _fit_summary(rows, scale):
    fitted = [row for row in rows if row["resid"] is not None]
    baseline = _most_common([row["sp"] for row in fitted])
    baseline_distances = [] if baseline is None else [
        abs(_step(scale, row["sp"], row["label"]) - scale.index(baseline)) for row in fitted]
    return {"n": len(fitted), "exact": sum(1 for row in fitted if row["resid"] == 0),
            "within1": sum(1 for row in fitted if abs(row["resid"]) <= 1),
            "max_steps": max((abs(row["resid"]) for row in fitted), default=None), "baseline_sp": baseline,
            "baseline_exact": baseline_distances.count(0), "baseline_within1": sum(1 for gap in baseline_distances if gap <= 1)}


def _hex(rgb):
    return "#" + "".join("%02x" % math.floor(channel + HALF_UP) for channel in rgb)


def _rgb(color):
    return [int(color[index:index + 2], 16) for index in (1, 3, 5)]


def _color(index, count):
    if count == 1:
        return PALETTE[SINGLE_COLOR]
    position = index * (len(PALETTE) - 1) / (count - 1)
    if count <= len(PALETTE):
        return PALETTE[round(position)]
    below = math.floor(position)
    above = min(below + 1, len(PALETTE) - 1)
    share = position - below
    return _hex([low + (high - low) * share for low, high in zip(_rgb(PALETTE[below]), _rgb(PALETTE[above]))])


def _colors(rows, pending):
    values = sorted({row[key] for row in rows for key in ("sp", "est") if row[key] is not None}
                    | {row["sp"] for row in pending})
    return {sp_key(value): _color(index, len(values)) for index, value in enumerate(values)}


# ---- lines, usage, forecast -------------------------------------------------------------------------------------------------

def _shared_commit(commit, listers, lines):
    return {"hash": commit.hash[:SHORT_HASH], "n": len(listers[commit.hash]), "subj": commit.subject, "lines": lines,
            "date": commit.authored_at.date().isoformat(), "labels": listers[commit.hash]}


def _lines(rows, references, listers, changes, kind_lines):
    held = [changes[commit_hash] for commit_hash in listers if commit_hash in changes]
    unique_total = sum(_counted_lines(commit) for commit in held)
    recorded_git = sum(row["git_churn"] for row in rows)
    shared = sorted((commit for commit in held if len(listers[commit.hash]) > 1), key=lambda commit: (
        -(len(listers[commit.hash]) - 1) * _counted_lines(commit), -len(listers[commit.hash]), commit.hash))
    vendored = max(held, key=lambda commit: kind_lines[commit.hash][VENDORED], default=None)
    return {"cats": {kind: sum(kind_lines[commit.hash][kind] for commit in held) for kind in KINDS},
            "raw_total": sum(row["churn"] for row in rows), "recorded_git": recorded_git, "unique_total": unique_total,
            "inflation": recorded_git - unique_total,
            "shared_count": sum(1 for labels in listers.values() if len(labels) > 1),
            "commit_refs": sum(len(feature["sources"]["commits"]) for feature in references),
            "unique_commits": len(listers),
            "missing_commits": sorted(commit_hash[:SHORT_HASH] for commit_hash in listers if commit_hash not in changes),
            "mismatches": [{"label": row["label"], "stored": row["churn"], "git": row["git_churn"], "missing": row["missing"]}
                           for row in rows if row["churn"] != row["git_churn"]],
            "shared_top": [_shared_commit(commit, listers, _counted_lines(commit)) for commit in shared[:SHARED_TOP]],
            "vendored_commit": None if vendored is None or kind_lines[vendored.hash][VENDORED] == 0 else {
                key: value for key, value in _shared_commit(vendored, listers, kind_lines[vendored.hash][VENDORED]).items()
                if key in ("hash", "subj", "lines", "labels")}}


def _tokens(references):
    totals = dict.fromkeys(TOKEN_KEYS, 0)
    for feature in references:
        tokens = (feature.get("usage") or {}).get("tokens") or {}
        for key in TOKEN_KEYS:
            totals[key] += tokens.get(key) or 0
    return totals


def _partial(rows):
    """Usage rows whose planned hours per agent hour exceed PARTIAL_FACTOR × the median (a session matched only in part)."""
    usable = [row for row in rows if row["sessions"] > 0 and row["hours"] and row["agent_min"] and row["agent_min"] > 0]
    if len(usable) < MIN_PARTIAL_ROWS:
        return []
    ratios = [row["hours"] / (row["agent_min"] / MINUTES_PER_HOUR) for row in usable]
    limit = PARTIAL_FACTOR * median(ratios)
    return [row["label"] for row, ratio in zip(usable, ratios) if ratio > limit]


def _forecast(rows, scale):
    graded = [row for row in rows if row["est"] is not None]
    steps = [_step(scale, row["sp"], row["label"]) - _step(scale, row["est"], row["label"]) for row in graded]
    count = len(steps)
    estimates, delivered = sorted({row["est"] for row in graded}), sorted({row["sp"] for row in graded})
    grid = [[0] * len(delivered) for _ in estimates]
    for row in graded:
        grid[estimates.index(row["est"])][delivered.index(row["sp"])] += 1
    errors = {str(step): steps.count(step) for step in range(min(steps), max(steps) + 1)} if steps else {}
    listed = sorted(({"label": row["label"], "est": row["est"], "sp": row["sp"], "steps": step,
                      "estimated_at": row["estimated_at"], "by": row["estimated_by"]} for row, step in zip(graded, steps)),
                    key=lambda item: (-abs(item["steps"]), item["label"]))
    return {"n": count, "exact": steps.count(0), "within1": sum(1 for step in steps if abs(step) <= 1),
            "under": sum(1 for step in steps if step > 0), "over": sum(1 for step in steps if step < 0),
            "mean_abs_steps": _ratio(sum(abs(step) for step in steps) / count) if count else None,
            "bias": _ratio(sum(steps) / count) if count else None, "errors": errors,
            "grid": {"est": estimates, "sp": delivered, "counts": grid}, "rows": listed}


# ---- DATA -------------------------------------------------------------------------------------------------------------------

def _check_scale(scale):
    wrong = [value for value in scale if not _is_number(value)]
    if wrong:
        raise EstimateError(NUMERIC_SCALE % ", ".join(map(repr, wrong)))


def _counts(features, references):
    return {"stored": len(features), "references": len(references),
            "not_delivered": sum(1 for feature in features if feature.get("status") != DELIVERED),
            "unsized": sum(1 for feature in features if feature.get("status") == DELIVERED and not is_reference(feature))}


def analyse(document, changes, project, today):
    """DATA of the report (see the module docstring) for `project` on `today`."""
    scale, features = document["scale"], document["features"]
    _check_scale(scale)
    references = [feature for feature in features if is_reference(feature)]
    if not references:
        raise EstimateError(NO_REFERENCE)
    listers = _listers(references)
    kind_lines = {commit_hash: _kind_lines(changes[commit_hash]) for commit_hash in listers if commit_hash in changes}
    rows = [_row(feature, listers, changes, kind_lines) for feature in references]
    pending = [_pending_row(feature) for feature in features
               if not is_reference(feature) and feature.get("storyPoints") is not None]
    core = _core(rows)
    predictions = {metric: _prediction(rows, metric, scale) for metric, _ in METRICS}
    _apply_fit(rows, predictions, scale)
    buckets = {metric: _buckets(rows, metric) for metric, _ in METRICS}
    corr, corrn = _matrix(rows)
    days = [row[key] for row in rows for key in ("first", "last") if row[key] is not None]
    return {
        "project": project, "generated_on": today.isoformat(), "store": "/".join((DATA_FOLDER, FEATURES_FOLDER)),
        "scale": scale, "counts": _counts(features, references), "colors": _colors(rows, pending), "rows": rows, "pending": pending,
        "metrics": [list(metric) for metric in METRICS],
        "res": {metric: _result(rows, metric, label, core) for metric, label in METRICS},
        "pred": predictions,
        "bstats": {metric: {sp_key(sp): _box(values, metric) for sp, values in buckets[metric].items()} for metric, _ in METRICS},
        "sep": {metric: _separations(buckets[metric]) for metric, _ in METRICS},
        "mvars": [list(variable) for variable in MATRIX_VARIABLES], "corr": corr, "corrn": corrn,
        "core": core, "base_sp": _base_sp(rows), "fit": _fit_summary(rows, scale),
        "lines": _lines(rows, references, listers, changes, kind_lines), "kind_rules": dict(RULES),
        "tokens": _tokens(references), "date_range": [min(days), max(days)] if days else None,
        "partial": _partial(rows), "forecast": _forecast(rows, scale),
    }
