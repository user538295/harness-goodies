"""Where a feature fits among its picks (C3 `place`): two judge calls read each pick as a score s = v(verdict) − v(pickVerdict)
(bigger = +1, same = 0, smaller = −1), so s runs from +2 (the feature is bigger) to −2 (smaller). A candidate size x expects
+2 from each pick below it, 0 from each pick at it and −2 from each above it; the candidate whose expectations miss the scores the
least wins. Candidates run from one scale step below the smallest picked size to one step above the biggest. A tie goes to the size
whose picks' median planned hours are nearest by ratio to the feature's, when those hours are all known; else up. Pure."""
import json
import math
import statistics

from estimation.errors import ArgumentError

VALUES = {"bigger": 1, "same": 0, "smaller": -1}
EXPECTED = 2  # the score a pick gets from both calls when the feature is clearly bigger
BY_HOURS = "hours"
ROUND_UP = "round up"
RELATIONS = {"bigger": "bigger than", "same": "same as", "smaller": "smaller than"}


def _check(verdicts, names, call):
    """Refuses `verdicts` (judge call `call`) unless they name every pick once with a known word."""
    if not isinstance(verdicts, dict):
        raise ArgumentError("%s must be an object" % call)
    for name in names:
        if name not in verdicts:
            raise ArgumentError("%s: missing '%s'" % (call, name))
    for name, word in verdicts.items():
        if name not in names:
            raise ArgumentError("%s: unknown '%s'" % (call, name))
        if word not in VALUES:
            raise ArgumentError("%s: '%s' for '%s'" % (call, word, name))


def _expected(sp, size):
    return EXPECTED if sp < size else 0 if sp == size else -EXPECTED


def _median_hours(picks):
    hours = [pick["estimatedHours"] for pick in picks if isinstance(pick.get("estimatedHours"), (int, float))
             and not isinstance(pick["estimatedHours"], bool) and pick["estimatedHours"] > 0]
    return statistics.median(hours) if hours else None


def _break_tie(tied, sizes_of, hours):
    """(size, how) for the tied candidates, in scale order."""
    if hours:
        medians = {size: _median_hours(sizes_of.get(size, [])) for size in tied}
        if all(median is not None for median in medians.values()):
            return min(tied, key=lambda size: (abs(math.log(medians[size] / hours)), -tied.index(size))), BY_HOURS
    return tied[-1], ROUND_UP


def _phrase(verdict, pick_verdict):
    """How the feature relates to a pick; the second call is read from the feature's side, and "X / Y" marks a disagreement."""
    first, second = RELATIONS[verdict], RELATIONS[{"bigger": "smaller", "same": "same", "smaller": "bigger"}[pick_verdict]]
    return first if first == second else "%s / %s" % (first, second)


def place(scale, picked, verdicts, pick_verdicts, hours):
    """`{"sp", "tie", "report", "verdicts", "missing"}` for a feature among `picked` (`pick()`'s result)."""
    size_of = {name: group["sp"] for group in picked["groups"] for name in group["names"]}
    names = [pick["name"] for pick in picked["picks"]]
    _check(verdicts, names, "verdicts")
    _check(pick_verdicts, names, "pickVerdicts")
    if not names:
        return {"sp": None, "tie": None, "report": "", "verdicts": [], "missing": picked["missing"]}
    scores = {name: VALUES[verdicts[name]] - VALUES[pick_verdicts[name]] for name in names}
    low, high = scale.index(picked["groups"][0]["sp"]), scale.index(picked["groups"][-1]["sp"])
    candidates = scale[max(low - 1, 0):high + 2]
    costs = {size: sum(abs(scores[name] - _expected(size_of[name], size)) for name in names) for size in candidates}
    best = min(costs.values())
    tied = [size for size in candidates if costs[size] == best]
    tie = None
    if len(tied) == 1:
        sp = tied[0]
    else:
        sizes_of = {}
        for pick in picked["picks"]:
            sizes_of.setdefault(size_of[pick["name"]], []).append(pick)
        sp, how = _break_tie(tied, sizes_of, hours)
        tie = {"between": [tied[0], tied[-1]], "by": how}
    rows = [{"name": pick["name"], "id": pick["id"], "sp": size_of[pick["name"]], "verdict": verdicts[pick["name"]],
             "pickVerdict": pick_verdicts[pick["name"]]}
            for pick in sorted(picked["picks"], key=lambda pick: scale.index(size_of[pick["name"]]))]
    report = ", ".join("%s %s (%s)" % (_phrase(row["verdict"], row["pickVerdict"]), row["id"] or row["name"], json.dumps(row["sp"]))
                       for row in rows)
    return {"sp": sp, "tie": tie, "report": report, "verdicts": rows, "missing": picked["missing"]}
