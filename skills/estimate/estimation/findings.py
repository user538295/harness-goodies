"""`texts(DATA)`: every sentence of the `/estimate report` page, produced by fixed rules from the DATA of `report_data.analyse`
(no agent-written text). Returns {"html": {marker: html}, "notes": {metric: html}}: one entry per template marker and one note
per metric for the explorer. Every data string is HTML-escaped; markup comes only from the templates here. Numbers are en-US
formatted, ρ has two decimals, shares are rounded percentages, growth factors read `×1.52`. A rule whose data is missing (too few
rows, no forecasts, no shared commits, no hours, no sessions, null statistics) degrades to a correct sentence or is left out."""
import html
import math

from estimation.anchor import DEFAULT_ANCHOR
from estimation.statistics import median, quantile

STRONG = 0.7
MODERATE = 0.5
BAND_ADVERBS = {"strong": "strongly", "moderate": "moderately", "weak": "weakly"}
CODE_GAIN = 0.1
CORE_SHIFT = 0.1
RHO_DECIMALS = 3
MIN_GRADED_FORECASTS = 5
GOOD_WITHIN_ONE = 0.8
WARN_WITHIN_ONE = 0.5
OVERLAP_AUC = 0.7
MIN_RANGE_BUCKET = 2
MIN_BUCKET = 3
PLANNING_METRICS = ("phases", "tasks", "hours")
LINE_METRICS = ("code", "src", "churn")
SCALING_METRICS = ("code", "hours", "commits_excl", "tasks", "phases", "churn")
OVERLAP_SHOWN = 2
MIN_HOURS_BUCKET = 3
DOUBLE_COUNT_SHARE = 0.05
VENDORED_SHARE = 0.5
GROWTH_METRICS = ("tasks", "commits_excl", "hours", "code")
COMMITS_PER_TASK_RULE = (0.8, 1.25)
FEW_SESSIONS = 8
COST_OUTLIER = 2
COST_SPREAD = 1.5  # usual costs per SP whose max ÷ min exceed this are reported as a median and range, not as "~$X each"
HUMAN_HOURS_FACTOR = 2
BUCKET_OUTLIER = 3
MIN_OUTLIER_BUCKET = 3
LINEAR_BAND = 0.1
MIN_UPPER_BUCKET = 2
SHARED_LABELS_NAMED = 4
THOUSAND = 1000
PERCENT = 100
DOUBLING = 2
MINUTES_PER_HOUR = 60
QUARTILES = (0.25, 0.75)
NUMBER_WORDS = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")
VOWEL_SOUND_NUMBERS = ("8", "11", "18")
STORE = "<code>.harness-goodies/estimations</code>"
INIT_STEP = 6
CHART_JS_VERSION = "4.4.7"
PROSE = {"tasks": "tasks", "hours": "planned hours", "commits_excl": "exclusive commits", "code": "code lines",
         "phases": "phases", "churn": "recorded lines", "commits": "raw commits", "src": "source lines"}
HALF = 0.5
DEPENDENCIES = "Dependencies (not counted)"
KIND_NAMES = {"src": "Source", "tests": "Tests", "docs": "Docs", "vendored": DEPENDENCIES, "other": "Other"}


# ---------------------------------------------------------------- formatting

def _e(value):
    return html.escape(str(value))


def _code(value):
    return "<code>%s</code>" % _e(value)


def _int(value):
    """Rounded half away from zero (392.5 → 393), not Python's half-to-even."""
    return "{:,}".format(int(math.copysign(math.floor(abs(value) + HALF), value)))


def _value(value):
    """A metric value: an integer when whole, else one decimal."""
    return _int(value) if float(value).is_integer() else "{:,.1f}".format(value)


def _pct(part, whole):
    return "%d%%" % round(PERCENT * part / whole)


def _rho(value):
    return "%.2f" % value


def _times(value):
    return "×%.2f" % value


def _sp(value):
    return _e(_int(value) if float(value).is_integer() else value)


def _a(sp):
    text = _sp(sp)
    return ("an " if text.startswith(VOWEL_SOUND_NUMBERS[0]) or text in VOWEL_SOUND_NUMBERS[1:] else "a ") + text


def _approx_k(value):
    return "%dk" % round(value / THOUSAND) if value >= THOUSAND else _int(value)


def _word(count):
    return NUMBER_WORDS[count] if 0 <= count < len(NUMBER_WORDS) else _int(count)


def _plural(count, word, words=None):
    return "%s %s" % (_int(count), word if count == 1 else (words or word + "s"))


def _join(items):
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _range(low, high, form):
    return form(low) if form(low) == form(high) else "%s–%s" % (form(low), form(high))


def _prose(data, key):
    return PROSE.get(key) or _e(_labels(data)[key].lower())


def _title(text):
    return text[:1].upper() + text[1:]


def _items(items):
    return "".join("<li>%s</li>" % item for item in items if item)


# ---------------------------------------------------------------- data access

def _labels(data):
    return dict(data["metrics"])


def _rhos(data):
    """{metric: ρ} of the metrics whose Spearman ρ with SP is known."""
    return {key: data["res"][key]["spearman"] for key, _ in data["metrics"]
            if data["res"].get(key) and data["res"][key]["spearman"] is not None}


def _band(rho):
    return "strong" if rho >= STRONG else "moderate" if rho >= MODERATE else "weak"


def _median_band(data):
    rhos = _rhos(data)
    return _band(median(list(rhos.values()))) if rhos else None


def _exps(data):
    return {key: pred["exp"] for key, pred in data["pred"].items() if pred}


def _values(data, key):
    return [row[key] for row in data["rows"] if row.get(key) is not None]


def _gap(first, second):
    """first − second at DATA's precision, so a 0.1 gap is not lost to binary rounding."""
    return round(first - second, RHO_DECIMALS)


def _without_estimate(data):
    return sum(1 for row in data["rows"] if row["est"] is None)


def _sessions(data):
    return [row for row in data["rows"] if row["sessions"]]


def _corr(data, first, second):
    keys = [key for key, _ in data["mvars"]]
    if first not in keys or second not in keys:
        return None
    return data["corr"][keys.index(first)][keys.index(second)]


def _iqr(values):
    ordered = sorted(values)
    return quantile(ordered, QUARTILES[0]), quantile(ordered, QUARTILES[1])


def _buckets(data, key):
    """[(sp, box)] of a metric, smallest SP first."""
    return sorted(((float(sp), box) for sp, box in data["bstats"].get(key, {}).items()), key=lambda item: item[0])


def _grows(exp):
    if abs(exp - 1) < LINEAR_BAND:
        return "about in step with SP"
    return "faster than SP" if exp > 1 else "slower than SP"


# ---------------------------------------------------------------- hero

def _eyebrow(data):
    span = "%s → %s" % tuple(_e(day) for day in data["date_range"]) if data["date_range"] else "no dated commits"
    return "%s · .harness-goodies/estimations + git history · %s" % (_e(data["project"]), span)


def _band_group(data, band, members, first):
    rhos = _rhos(data)
    values = [rhos[key] for key in members]
    names = _join(_prose(data, key) for key in members)
    return "%s with %s (%sρ %s)" % (BAND_ADVERBS[band], names, "Spearman " if first else "",
                                     _range(min(values), max(values), _rho))


def _lede(data):
    rhos = _rhos(data)
    if not rhos:
        sentences = ["There are too few features with both an SP and a measured size to compute a correlation yet."]
    else:
        groups = []
        for band in BAND_ADVERBS:
            members = sorted((key for key in rhos if _band(rhos[key]) == band), key=lambda key: -rhos[key])
            if members:
                groups.append(_band_group(data, band, members, not groups))
        sentences = ["Story points correlate %s." % _join(groups)]
        if "code" in rhos and "churn" in rhos and _gap(rhos["code"], rhos["churn"]) >= CODE_GAIN:
            sentences.append("Raw lines changed look weaker (ρ %s) only because of how lines are counted; with shared commits "
                             "split and docs and vendored files removed, code lines correlate at ρ %s."
                             % (_rho(rhos["churn"]), _rho(rhos["code"])))
    forecast = data["forecast"]
    if forecast["n"]:
        sentences.append("%s of %s had a forecast before work started, and %s of those landed within one scale step."
                         % (_int(forecast["n"]), _plural(len(data["rows"]), "feature"),
                            _pct(forecast["within1"], forecast["n"])))
    elif data["rows"] and _without_estimate(data) == len(data["rows"]):
        sentences.append("There's a catch, though: init calculated these SP values from the same planned hours, or placed them "
                         "by comparison after delivery.")
    return " ".join(sentences)


def _rho_note(data):
    agent = len([value for value in _values(data, "agent_min") if value])
    cost = len([value for value in _values(data, "cost") if value])
    return ("Bands: below %s weak · %s–%s moderate · %s and above strong. Agent minutes (n=%d) and cost (n=%d) are left out "
            "because there are too few data points." % (MODERATE, MODERATE, STRONG, STRONG, agent, cost))


# ---------------------------------------------------------------- verdict

def _circular_catch(data):
    count, total = _without_estimate(data), len(data["rows"])
    if not count:
        return None
    who = "All %s SP values are" % _int(total) if count == total else "%s of %s SP values are" % (_int(count), _int(total))
    return ("<b>Circular by construction.</b> %s <code>updatedStoryPoints</code>, assigned after delivery without an earlier "
            "estimate. <code>/estimate init</code> step %d calculates the SP of every feature with planned hours from those "
            "hours (the anchor, %s SP = %s h by default) and places the others by comparison with picked references, so the "
            "correlation with planned hours is built in. It shows consistency, not prediction."
            % (who, INIT_STEP, DEFAULT_ANCHOR["sp"], DEFAULT_ANCHOR["hours"]))


def _forecast_catch(data):
    forecast, total = data["forecast"], len(data["rows"])
    if not forecast["n"]:
        return ("<b>No forecasts to grade.</b> <code>storyPoints</code> and <code>estimatedAt</code> are <code>null</code> in "
                "all %s, so estimate-vs-actual accuracy can't be measured yet. The first features estimated with "
                "<code>/estimate &lt;plan&gt;</code> before work starts will give the first real test."
                % _plural(total, "feature"))
    return ("<b>%s graded.</b> %s exact and %s within one scale step; %s delivered bigger than estimated, %s smaller. See "
            "<a href=\"#forecast\">forecast accuracy</a>."
            % (_title(_plural(forecast["n"], "forecast")), _pct(forecast["exact"], forecast["n"]),
               _pct(forecast["within1"], forecast["n"]), _int(forecast["under"]), _int(forecast["over"])))


def _overlaps(data):
    """The weakest adjacent splits under OVERLAP_AUC, at most one per metric."""
    splits = []
    for key, _ in data["metrics"]:
        for pair, (auc, lower_n, higher_n) in data["sep"].get(key, {}).items():
            if auc is not None and auc < OVERLAP_AUC and min(lower_n, higher_n) >= MIN_BUCKET:
                splits.append((auc, key, pair))
    chosen = []
    for auc, key, pair in sorted(splits, key=lambda split: split[0]):
        if all(key != other for _, other, _ in chosen):
            chosen.append((auc, key, pair))
    return chosen[:OVERLAP_SHOWN]


def _code_inversions(data):
    sentences = []
    buckets = _buckets(data, "code")
    for (lower, low_box), (higher, high_box) in zip(buckets, buckets[1:]):
        if high_box["med"] < low_box["med"]:
            smallest = min((row for row in data["rows"] if row["sp"] == higher and row["code"] is not None),
                           key=lambda row: row["code"], default=None)
            text = ("The %s-pointers (n=%d) have a lower median code size than the %ss (%s vs. %s lines)."
                    % (_sp(higher), high_box["n"], _sp(lower), _value(high_box["med"]), _value(low_box["med"])))
            if smallest is not None and high_box["n"] > 1:
                text += " %s has only %s." % (_code(smallest["label"]), _value(smallest["code"]))
            sentences.append(text)
    return sentences


def _overlap_catch(data):
    sentences = []
    for auc, key, pair in _overlaps(data):
        lower, higher = pair.split("→")
        sentences.append("%s has more %s than %s only <b>%s</b> of the time."
                         % (_title(_a(float(higher))), _prose(data, key), _a(float(lower)), _pct(auc, 1)))
    sentences += _code_inversions(data)
    if not sentences:
        return None
    scale = sorted({row["sp"] for row in data["rows"]})
    lead = ("Over the whole %s–%s range the signal is strong, but " % (_sp(scale[0]), _sp(scale[-1]))
            if _median_band(data) == "strong" and len(scale) > 1 else "")
    first = sentences[0]
    sentences[0] = lead + (first[0].lower() + first[1:] if lead else first)
    return "<b>Neighbouring SP values overlap.</b> " + " ".join(sentences)


def _catches(data):
    return [catch for catch in (_circular_catch(data), _forecast_catch(data), _overlap_catch(data)) if catch]


def _catch_html(catches):
    return "".join('<div class="catch"><div class="n">%d</div><div>%s</div></div>' % (number, text)
                   for number, text in enumerate(catches, 1))


def _verdict(data, catches):
    """(tag class, tag text, big sentence)."""
    forecast = data["forecast"]
    if forecast["n"] >= MIN_GRADED_FORECASTS:
        share = forecast["within1"] / forecast["n"]
        big = ("SP forecasts land within one scale step of the delivered size %s of the time (%s of %s)."
               % (_pct(forecast["within1"], forecast["n"]), _int(forecast["within1"]), _plural(forecast["n"], "forecast")))
        if share >= GOOD_WITHIN_ONE:
            return "good", "Forecasts hold up", big
        if share >= WARN_WITHIN_ONE:
            return "warn", "Forecasts are rough", big
        return "bad", "Forecasts miss", big
    tested = ("it has never been tested as a forecast" if not forecast["n"]
              else "only %s can be graded so far" % _plural(forecast["n"], "forecast"))
    band = _median_band(data)
    if band == "strong":
        return "warn", "Consistent, not yet tested as a forecast", "SP is consistent with the delivered size, but %s." % tested
    if band == "moderate":
        return "warn", "Partly consistent, not yet tested as a forecast", \
            "SP only partly matches the delivered size, and %s." % tested
    if band == "weak":
        return "bad", "Weak link to the delivered size", "SP has little to do with the delivered size, and %s." % tested
    return "bad", "Not enough data", "There are too few measured features to judge SP, and %s." % tested


def _verdict_tag(data, catches):
    kind, text, _ = _verdict(data, catches)
    if catches:
        text += ", with %s %s" % (_word(len(catches)), "catch" if len(catches) == 1 else "catches")
    return '<span class="tag %s">%s</span>' % (kind, text)


# ---------------------------------------------------------------- key findings

def _best_finding(data):
    rhos = _rhos(data)
    if not rhos:
        return None
    best = max(rhos, key=lambda key: rhos[key])
    res = data["res"][best]
    text = "<b>%s are the best match for SP</b>: ρ %s (n=%d)" % (_title(_prose(data, best)), _rho(rhos[best]), res["n"])
    text += (", and %s of pairs are in the same order." % _pct(res["conc"], 1)) if res["conc"] is not None else "."
    rates = [(sp, box["med"] / sp) for sp, box in _buckets(data, "hours") if sp > 0 and box["n"] >= MIN_HOURS_BUCKET]
    if len(rates) == 1:
        text += " At SP %s the median of planned hours is %.1f per SP." % (_sp(rates[0][0]), rates[0][1])
    elif rates:
        text += (" In the SP %s–%s range the median of planned hours is <b>~%s per SP</b> (%s h per SP at SP %s)."
                 % (_sp(rates[0][0]), _sp(rates[-1][0]), _value(round(median([rate for _, rate in rates]))),
                    " / ".join("%.1f" % rate for _, rate in rates), " / ".join(_sp(sp) for sp, _ in rates)))
    return text


def _with_pearson(res):
    rho = "ρ %s" % _rho(res["spearman"])
    return rho + (" and Pearson %s" % _rho(res["pearson"]) if res["pearson"] is not None else "")


def _code_finding(data):
    rhos = _rhos(data)
    if "code" not in rhos or "churn" not in rhos:
        return None
    gain = _gap(rhos["code"], rhos["churn"])
    if gain >= CODE_GAIN:
        title = "Code lines track SP %s once the noise is removed." % ("well" if rhos["code"] >= STRONG else "better")
    else:
        title = "Removing the noise from lines changed makes little difference."
    return ("<b>%s</b> Raw <code>linesAdded+linesRemoved</code> gives %s. Code lines only (source and test files, shared "
            "commits split, docs and vendored files left out) give %s."
            % (title, _with_pearson(data["res"]["churn"]), _with_pearson(data["res"]["code"])))


def _double_count_finding(data):
    lines = data["lines"]
    if not lines["recorded_git"] or lines["inflation"] / lines["recorded_git"] < DOUBLE_COUNT_SHARE:
        return None
    text = ("<b>%s of the recorded lines are counted more than once.</b> %s attributed to more than one feature: %s recorded "
            "vs. %s unique changed lines."
            % (_pct(lines["inflation"], lines["recorded_git"]),
               _plural(lines["shared_count"], "commit is", "commits are"), _int(lines["recorded_git"]),
               _int(lines["unique_total"])))
    if lines["shared_top"]:
        top = lines["shared_top"][0]
        text += " One commit (%s, %s lines) is counted in %d features." % (_code(top["hash"]), _int(top["lines"]), top["n"])
    return text


def _vendored_finding(data):
    """The feature whose dependency lines are the largest share of its counted (git) plus dependency lines, at VENDORED_SHARE."""
    shares = [(row["vendored"] / (row["git_churn"] + row["vendored"]), row) for row in data["rows"] if row["vendored"]]
    if not shares:
        return None
    share, row = max(shares, key=lambda item: item[0])
    if share < VENDORED_SHARE:
        return None
    commit = data["lines"]["vendored_commit"]
    source = (" (commit %s)" % _code(commit["hash"])) if commit and row["label"] in commit["labels"] else ""
    return ("%s's commits also hold <b>%s lines of committed dependencies</b>%s, which are not counted."
            % (_code(row["label"]), _int(row["vendored"]), source))


def _growth_finding(data):
    exps = {key: exp for key, exp in _exps(data).items() if key in GROWTH_METRICS}
    if not exps:
        return None
    ordered = [key for key in GROWTH_METRICS if key in exps]
    rates = _join("%s grow %s" % (_prose(data, key), _times(DOUBLING ** exps[key])) for key in ordered)
    linear = min(exps, key=lambda key: abs(exps[key] - 1))
    text = "<b>The metrics grow at different rates as SP grows.</b> Each time SP doubles, %s." % rates
    text += " SP is close to linear in %s" % _prose(data, linear)
    faster = [_prose(data, key) for key in ordered if key != linear and exps[key] > 1 + LINEAR_BAND]
    text += (", and %s grow faster than SP." % _join(faster)) if faster else "."
    return text


def _weakest_finding(data):
    rhos = {key: rho for key, rho in _rhos(data).items() if key in PLANNING_METRICS}
    if not rhos:
        return None
    weakest = min(rhos, key=lambda key: rhos[key])
    if rhos[weakest] >= STRONG:
        return None
    exp = _exps(data).get(weakest)
    growth = (", %s per SP doubling" % _times(DOUBLING ** exp)) if exp is not None else ""
    verb = "tell you little about size" if _band(rhos[weakest]) == "weak" else "are the weakest link to size"
    text = "<b>%s %s</b> (ρ %s%s)." % (_title(_prose(data, weakest)), verb, _rho(rhos[weakest]), growth)
    values = _values(data, weakest)
    if values:
        low, high = _iqr(values)
        text += " Most features have %s %s at any size." % (_range(low, high, _value), _prose(data, weakest))
    return text


def _planning_finding(data):
    rho = _corr(data, "tasks", "hours")
    ratios = [row["commits_excl"] / row["tasks"] for row in data["rows"] if row["tasks"] and row["commits_excl"] is not None]
    if rho is None and not ratios:
        return None
    agree = rho is None or rho >= STRONG
    text = "<b>The planning numbers %s.</b>" % ("agree with each other" if agree else "only partly agree")
    if rho is not None:
        text += " Tasks vs. hours ρ %s." % _rho(rho)
    if ratios:
        middle = median(ratios)
        low, high = _iqr(ratios)
        low_rule, high_rule = COMMITS_PER_TASK_RULE
        verdict = ("the one-commit-per-task rule is followed" if low_rule <= middle <= high_rule
                   else "commits do not map one-to-one onto tasks")
        text += " Exclusive commits per task: median %.2f (IQR %.2f–%.2f), so %s." % (middle, low, high, verdict)
    return text


def _fit_finding(data):
    fit = data["fit"]
    if not fit["n"]:
        return None
    predictable = fit["within1"] / fit["n"] >= GOOD_WITHIN_ONE
    off = fit["n"] - fit["exact"]
    text = ("<b>SP is %s from the actuals.</b> A combined fit (tasks + exclusive commits + code lines + hours) gives back the "
            "exact SP for %s of %s (%s), and %s within ±1 scale step. Always answering \"%s\" scores %s / %s."
            % ("predictable" if predictable else "only partly predictable", _int(fit["exact"]),
               _plural(fit["n"], "feature"), _pct(fit["exact"], fit["n"]),
               ("all %s are" if fit["within1"] == fit["n"] else "%s are") % _int(fit["within1"]), _sp(fit["baseline_sp"]),
               _pct(fit["baseline_exact"], fit["n"]), _pct(fit["baseline_within1"], fit["n"])))
    if off:
        distance = "one step off" if fit["max_steps"] == 1 else "off"
        text += (" The %s that %s %s %s listed under <a href=\"#outliers\">re-size candidates</a>."
                 % (_plural(off, "feature"), "is" if off == 1 else "are", distance, "is" if off == 1 else "are"))
    return text


def _findings(data):
    rules = (_best_finding, _code_finding, _double_count_finding, _vendored_finding, _growth_finding, _weakest_finding,
             _planning_finding, _fit_finding)
    return _items(rule(data) for rule in rules)


# ---------------------------------------------------------------- matrix and scaling

def _spcore_note(data):
    core = data["core"]
    if core is None:
        return "r log = Pearson on log values. ρ SP≤… is not shown: no small group of largest features to leave out."
    dropped = sorted(row["sp"] for row in data["rows"] if row["sp"] > core["max_sp"])
    text = ("r log = Pearson on log values. SP ≤%s = without the %s largest features (%s SP), to check that a few big "
            "features are not driving the correlation."
            % (_sp(core["max_sp"]), _word(core["n_dropped"]), ", ".join(_sp(sp) for sp in dropped)))
    moves = []
    for key, _ in data["metrics"]:
        res = data["res"][key]
        if res["spearman"] is not None and res["spearman_core"] is not None \
                and abs(_gap(res["spearman"], res["spearman_core"])) >= CORE_SHIFT:
            moves.append("%s (ρ %s → %s)" % (_prose(data, key), _rho(res["spearman"]), _rho(res["spearman_core"])))
    return text + (" They are not." if not moves else " They do drive it for %s." % _join(moves))


def _upper_sp(data):
    if not data["base_sp"]:
        return None
    counts = {}
    for row in data["rows"]:
        counts[row["sp"]] = counts.get(row["sp"], 0) + 1
    above = [sp for sp, count in counts.items() if sp > data["base_sp"] and count >= MIN_UPPER_BUCKET]
    return max(above) if above else None


def _scaling_callout(data):
    exps = {key: exp for key, exp in _exps(data).items() if key in SCALING_METRICS}
    if not exps:
        return "Too few features with positive values to fit how the metrics grow with SP."
    rhos = _rhos(data)
    strong = {key: exp for key, exp in exps.items() if rhos.get(key) is not None and rhos[key] >= STRONG}
    if not strong:
        sentences = ["No metric with a power fit correlates strongly with SP, so SP is not a scale of any one of them."]
        linear = None
    else:
        linear = min(strong, key=lambda key: abs(strong[key] - 1))
        sentences = ["SP behaves like a <b>%s scale</b> (exponent ≈%.2f)."
                     % (_prose(data, linear).replace(" ", "-"), strong[linear])]
    faster = [key for key in strong if key != linear and strong[key] > 1 + LINEAR_BAND]
    upper = _upper_sp(data)
    if faster:
        fastest = max(faster, key=lambda key: exps[key])
        text = "%s grow faster than SP" % _title(_prose(data, fastest))
        if upper is not None and data["base_sp"]:
            ratio = upper / data["base_sp"]
            text += (": %s is %.1f× %s in SP but ~%.1f× in typical %s (from the fit)"
                     % (_a(upper), ratio, _a(data["base_sp"]), ratio ** exps[fastest], _prose(data, fastest)))
        sentences.append(text + ".")
    slower = [key for key, _ in data["metrics"] if key in exps and key != linear and exps[key] < 1 - LINEAR_BAND]
    if slower:
        text = "%s grow slower than SP" % _title(_join(_prose(data, key) for key in slower))
        if "tasks" in slower:
            text += ", so plans for big features have bigger tasks rather than proportionally more of them"
        sentences.append(text + ".")
    return " ".join(sentences)


# ---------------------------------------------------------------- data quality

def _quality_sub(data):
    lines, total = data["lines"], len(data["rows"])
    differ = len(lines["mismatches"])
    result = ("all %s match exactly" % _int(total)) if not differ else "%s of %s differ" % (_int(differ), _int(total))
    missing = len(lines["missing_commits"])
    text = ("Every stored <code>linesAdded</code>/<code>linesRemoved</code> value was checked against <code>git log "
            "--numstat</code> of the feature's listed commits without committed dependency files, and %s" % result)
    if missing:
        text += " (%s git does not hold)" % _plural(missing, "listed commit")
    return text + (". The noise comes from which commits are attributed to a feature and which files they touch.")


def _quality_note(data):
    lines = data["lines"]
    sentences = []
    if lines["recorded_git"]:
        sentences.append("Of the lines summed over all features, %s are double counts."
                         % _pct(max(lines["inflation"], 0), lines["recorded_git"]))
    cats = lines["cats"]
    if lines["unique_total"]:
        text = "Source code is %s of the unique changed lines" % _pct(cats["src"], lines["unique_total"])
        if cats["src"]:
            text += ", and test code is %.1f× the size of source code" % (cats["tests"] / cats["src"])
        sentences.append(text + ".")
    return " ".join(sentences) or "Git holds none of the listed commits, so the lines cannot be broken down."


def _quality_callout(data):
    lines = data["lines"]
    if not lines["shared_top"]:
        return ""
    top = lines["shared_top"][0]
    rows = {row["label"]: row for row in data["rows"]}
    churns = [rows[label]["churn"] for label in top["labels"] if label in rows and rows[label]["churn"] is not None]
    named = len(top["labels"]) <= SHARED_LABELS_NAMED
    who = (_join(_code(label) for label in top["labels"]) + " share " if named
           else "The %s features that share " % _int(len(top["labels"]))) + _code(top["hash"])
    rounded = churns and min(churns) >= THOUSAND
    spread = (" %seach record %s%s lines" % ("and " if named else "", "~" if rounded else "",
                                             _range(min(churns), max(churns), _approx_k if rounded else _int))
              if churns else "")
    sentences = [who + spread + "."]
    measured = [row for row in data["rows"] if row["churn"] and row["code"] is not None]
    if measured:
        middle = median([row["churn"] for row in measured])
        row = min((row for row in measured if row["churn"] >= middle), key=lambda row: row["code"] / row["churn"])
        code = "only %s lines of code" % _int(row["code"]) if round(row["code"]) else "no lines of code"
        sentences.append("%s records %s lines but has %s." % (_code(row["label"]), _int(row["churn"]), code))
    return " ".join(sentences)


# ---------------------------------------------------------------- re-size candidates

def _outliers_sub(data):
    fit = data["fit"]
    lead = ("The combined fit predicts SP from tasks, exclusive commits, code lines and (where present) hours, using the "
            "geometric mean of the single-metric log-log fits.")
    if not fit["n"]:
        return lead + " No feature has a metric the fit can use, so there are no candidates."
    off = sum(1 for row in data["rows"] if row["resid"])
    if not off:
        return lead + " Every feature sits exactly at that prediction, so there is nothing to re-size."
    if fit["max_steps"] == 1:
        where = ("Each feature below sits one scale step away from that prediction. None is further off." if off > 1
                 else "The feature below sits one scale step away from that prediction. None is further off.")
    else:
        where = "The %s below sit 1 to %d scale steps away from that prediction." % (_plural(off, "feature"), fit["max_steps"])
    return "%s %s \"SP high\" means the feature has more SP than its peers with similar actuals." % (lead, where)


# ---------------------------------------------------------------- agent usage

def _usage_title(data):
    count = len(_sessions(data))
    if count >= FEW_SESSIONS:
        return "Agent usage"
    return "Agent usage (%s)" % ("no features" if not count else _plural(count, "feature") + " only")


def _usage_sub(data):
    count, total = len(_sessions(data)), len(data["rows"])
    unmatched = sum(1 for row in data["rows"] if row["matched"] == "none")
    text = ("%s of %s have matched agent sessions. %s <code>matchedBy: \"none\"</code>."
            % (_int(count), _plural(total, "feature"), _plural(unmatched, "has", "have")))
    if count < FEW_SESSIONS:
        text = "Only " + text + " Take these numbers as examples, not statistics."
    return text


def _hours_point(data):
    partial = set(data["partial"])
    ratios = [row["hours"] / (row["agent_min"] / MINUTES_PER_HOUR) for row in _sessions(data)
              if row["hours"] and row["agent_min"] and row["label"] not in partial]
    if not ratios:
        return None
    text = ("Planned hours are <b>%s×</b> the recorded agent time for the %s with full matches."
            % (_range(min(ratios), max(ratios), lambda value: "%.1f" % value), _plural(len(ratios), "feature")))
    if min(ratios) >= HUMAN_HOURS_FACTOR:
        text += " The <code>· Nh</code> task estimates read like human-effort hours."
    return text


def _cost_point(data):
    costs = [(row["cost"] / row["sp"], row) for row in data["rows"] if row["cost"] and row["sp"]]
    if not costs:
        return None
    middle = median([cost for cost, _ in costs])
    usual = sorted(((cost, row) for cost, row in costs if cost <= COST_OUTLIER * middle), key=lambda pair: pair[1]["sp"])
    usual_costs = [cost for cost, _ in usual]
    sizes = _join(_sp(row["sp"]) for _, row in usual)
    if max(usual_costs) > COST_SPREAD * min(usual_costs):
        text = ("Cost per SP has a median of <b>~$%.0f</b> across %s ($%.1f–$%.1f per SP; %s SP)."
                % (median(usual_costs), _plural(len(usual), "feature"), min(usual_costs), max(usual_costs), sizes))
    else:
        text = "%s cost <b>~$%.0f per SP</b> (%s SP)." % (_title(_plural(len(usual), "feature")), median(usual_costs), sizes)
    for cost, row in costs:
        if cost > COST_OUTLIER * middle:
            text += " %s cost <b>$%.1f per SP</b>" % (_code(row["label"]), cost)
            if row["tokens"] and row["cache_read"]:
                text += ", with %s of its tokens cache reads" % _pct(row["cache_read"], row["tokens"])
            text += "."
    return text


def _cache_point(data):
    tokens = data["tokens"]
    if not tokens["total"]:
        return None
    return "<b>%s</b> of all tokens are cache reads." % _pct(tokens["cacheRead"], tokens["total"])


def _partial_points(data):
    rows = {row["label"]: row for row in data["rows"]}
    points = []
    for label in data["partial"]:
        row = rows[label]
        points.append("%s has %s of %s against %s planned hours, so its session match is likely incomplete."
                      % (_code(label), _plural(row["sessions"], "session"), _plural(round(row["agent_min"]), "minute"),
                         _value(row["hours"])))
    return points


def _usage_points(data):
    points = [_hours_point(data), _cost_point(data), _cache_point(data)] + _partial_points(data)
    return _items(points) or _items(["No feature has matched agent sessions yet."])


# ---------------------------------------------------------------- forecast accuracy

def _forecast_sub(data):
    forecast = data["forecast"]
    if not forecast["n"]:
        return ("No feature has a forecast yet: <code>storyPoints</code> is null everywhere. Estimate a feature with "
                "<code>/estimate &lt;file&gt;</code> before work starts; <code>/implement</code> records the delivered "
                "SP, and the next report grades the pair here.")
    return ("Each forecast (<code>storyPoints</code>, set before work starts) against the delivered "
            "<code>updatedStoryPoints</code>, in scale steps. %s of %s have both."
            % (_int(forecast["n"]), _plural(len(data["rows"]), "feature")))


def _forecast_points(data):
    forecast = data["forecast"]
    n = forecast["n"]
    if not n:
        return ""
    points = ["<b>%s</b> of the forecasts are exact and <b>%s</b> are within one scale step."
              % (_pct(forecast["exact"], n), _pct(forecast["within1"], n)),
              "%s delivered bigger than estimated, %s smaller." % (_plural(forecast["under"], "feature"),
                                                                 _int(forecast["over"]))]
    if forecast["bias"] is not None:
        direction = ("low (features grow)" if forecast["bias"] > 0
                     else "high (features shrink)" if forecast["bias"] < 0 else "neither high nor low")
        points.append("On average forecasts run %s: bias %+.2f steps, mean miss %.2f steps."
                      % (direction, forecast["bias"], forecast["mean_abs_steps"]))
    worst = forecast["rows"][0] if forecast["rows"] else None
    if worst and worst["steps"]:
        points.append("The biggest miss: %s was estimated %s and delivered %s (%+d steps)."
                      % (_code(worst["label"]), _sp(worst["est"]), _sp(worst["sp"]), worst["steps"]))
    return _items(points)


# ---------------------------------------------------------------- method and footer

def _method(data):
    counts, lines = data["counts"], data["lines"]
    kinds = " ".join("<b>%s</b> = %s." % (KIND_NAMES.get(kind, _e(kind)), _e(rule)) for kind, rule in data["kind_rules"].items())
    items = [
        "<b>Source.</b> %s (%s with an after-delivery SP, of %s stored) plus <code>git log --numstat</code> in %s for the "
        "%s the features reference." % (STORE, _plural(counts["references"], "delivered feature"), _int(counts["stored"]),
                                        _code(data["project"]), _plural(lines["unique_commits"], "unique commit")),
        "<b>SP</b> = <code>updatedStoryPoints</code>, set after delivery. <b>Forecast</b> = <code>storyPoints</code>, set "
        "before work starts; %s one." % _plural(len(data["rows"]) - _without_estimate(data), "feature has", "features have"),
        "<b>Commits (excl.)</b> = commits listed only under this feature. <b>Code lines</b> = added+removed lines in source "
        "and test files; a commit shared by k features adds 1/k of its lines to each. " + kinds,
        "<b>Lines changed</b> (stored and recomputed from git) leave out committed dependency files (%s). The report shows "
        "those lines apart as <b>%s</b>." % (_e(data["kind_rules"]["vendored"]), DEPENDENCIES),
        "<b>Spearman ρ</b> with average ranks for ties. The 95% CI is a percentile bootstrap with 2,000 resamples (seed 7). "
        "<b>Concordance</b> = (concordant + ½·ties in the metric) ÷ pairs with different SP. <b>Adjacent separation</b> = "
        "P(a random feature at the higher SP has a larger metric than one at the lower SP), i.e. the AUC.",
        "<b>Power fit</b>: ordinary least squares on ln(metric) ~ ln(SP). <b>Single-metric SP prediction</b>: OLS on "
        "ln(SP) ~ ln(metric), rounded to the nearest scale value in log space. <b>Combined fit</b>: the geometric mean of "
        "the single-metric predictions from tasks, exclusive commits, code lines and hours. All fits are in-sample.",
        "<b>Forecast steps</b> = scale index of the delivered SP − scale index of the forecast; above 0 means the feature "
        "came out bigger than estimated."]
    return _items(items)


def _footer(data):
    return ("Generated by <code>/estimate report</code> on %s from %s · Chart.js %s is bundled inline, so this file works "
            "offline." % (_e(data["generated_on"]), STORE, CHART_JS_VERSION))


# ---------------------------------------------------------------- per-metric notes

def _common_bucket(data, key):
    buckets = _buckets(data, key)
    return max(buckets, key=lambda item: (item[1]["n"], -item[0])) if buckets else None


def _bucket_outlier(data, key):
    medians = {sp: box["med"] for sp, box in _buckets(data, key) if box["n"] >= MIN_OUTLIER_BUCKET and box["med"] > 0}
    found = [(row[key] / medians[row["sp"]], row) for row in data["rows"]
             if row[key] is not None and row["sp"] in medians and row[key] > BUCKET_OUTLIER * medians[row["sp"]]]
    return max(found, key=lambda item: item[0])[1] if found else None


def _note(data, key):
    res, prose = data["res"][key], _prose(data, key)
    if res["spearman"] is None:
        sentences = ["Too few features with %s for a correlation (n=%d)." % (prose, res["n"])]
    else:
        sentences = ["ρ %s." % _rho(res["spearman"])]
        if res["conc"] is not None:
            sentences.append("%s of feature pairs are in the same order." % _pct(res["conc"], 1))
    common = _common_bucket(data, key)
    if common and common[1]["n"] >= MIN_RANGE_BUCKET:
        sp, box = common
        shown = _int if key in LINE_METRICS else _value
        spread = ("from %s to %s" % (shown(box["min"]), shown(box["max"])) if box["min"] != box["max"]
                  else "all at %s" % shown(box["min"]))
        sentences.append("SP-%s features range %s %s." % (_sp(sp), spread, prose))
    exp = _exps(data).get(key)
    if exp is not None:
        text = "%s grow %s (%s per doubling)" % (_title(prose), _grows(exp), _times(DOUBLING ** exp))
        if key == "tasks" and exp < 1 - LINEAR_BAND:
            text += ", so big features get bigger tasks rather than proportionally more of them"
        sentences.append(text + ".")
    splits = [(auc, pair) for pair, (auc, low_n, high_n) in data["sep"].get(key, {}).items()
              if auc is not None and min(low_n, high_n) >= MIN_BUCKET]
    if splits:
        auc, pair = max(splits)
        lower, higher = pair.split("→")
        sentences.append("The SP %s → %s split is the cleanest (AUC %.2f)." % (_e(lower), _e(higher), auc))
    outlier = _bucket_outlier(data, key)
    if outlier is not None:
        shown = _int if key in LINE_METRICS else _value
        sentences.append("The SP-%s outlier at %s %s is %s." % (_sp(outlier["sp"]), shown(outlier[key]), prose,
                                                                _code(outlier["label"])))
    zeros = sum(1 for row in data["rows"] if row[key] == 0)
    if zeros:
        sentences.append("%s no %s (hidden on this log axis)." % (_plural(zeros, "feature has", "features have"), prose))
    return " ".join(sentences)


# ---------------------------------------------------------------- entry point

def texts(data):
    """{"html": {marker: html}, "notes": {metric: html}} for one DATA dict."""
    catches = _catches(data)
    _, _, big = _verdict(data, catches)
    markers = {
        "eyebrow": _eyebrow(data), "lede": _lede(data), "rho_note": _rho_note(data),
        "verdict_tag": _verdict_tag(data, catches), "verdict_big": big, "catches": _catch_html(catches),
        "findings": _findings(data), "spcore_note": _spcore_note(data), "scaling_callout": _scaling_callout(data),
        "quality_sub": _quality_sub(data), "quality_note": _quality_note(data), "quality_callout": _quality_callout(data),
        "outliers_sub": _outliers_sub(data), "usage_title": _usage_title(data), "usage_sub": _usage_sub(data),
        "usage_points": _usage_points(data), "forecast_sub": _forecast_sub(data), "forecast_points": _forecast_points(data),
        "method": _method(data), "footer": _footer(data)}
    return {"html": markers, "notes": {key: _note(data, key) for key, _ in data["metrics"]}}
