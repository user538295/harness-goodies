"""`doc-stats`: phases, tasks and ticked tasks of a planning doc from `plan-progress.sh --counts` (the /implement skill's parser of
both the plan-maker and the create-tasks format), plus the sum of the `· <n>h` estimates on its task lines. A value that cannot be
found stays null and its reason goes to `missing`."""
import os
import re

from estimation.errors import EstimateError

HOURS_LINE = re.compile(r"^\s*-\s.*·\s*(\d+(?:\.\d+)?)h\s*$", re.MULTILINE)
HOURS_DECIMALS = 2
COUNTS_SUCCESS = 0
COUNT_KEYS = (("PHASES", "phases"), ("TASKS", "tasks"), ("COMPLETED", "completed"))
COUNTS_LABEL = "phases, tasks, completed"
SCRIPT_NAME = "plan-progress.sh"


def _hours(text):
    hours = [float(match) for match in HOURS_LINE.findall(text)]
    return round(sum(hours), HOURS_DECIMALS) if hours else None


def _counts(path, plan_progress, run):
    """Returns ({phases, tasks, completed}, missing reasons)."""
    unknown = dict.fromkeys(key for _, key in COUNT_KEYS)
    if not os.path.isfile(plan_progress):
        return unknown, ["%s: %s not found at %s" % (COUNTS_LABEL, SCRIPT_NAME, plan_progress)]
    result = run(["bash", plan_progress, "--counts", path], os.path.dirname(path))
    if result.code != COUNTS_SUCCESS:
        reason = " ".join(result.stderr.split()) or "no output"
        return unknown, ["%s: %s exit %d: %s" % (COUNTS_LABEL, SCRIPT_NAME, result.code, reason)]
    lines = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    try:
        counts = {key: int(lines[name]) if lines[name] else None for name, key in COUNT_KEYS}
    except (KeyError, ValueError):
        return unknown, ["%s: %s printed no counts: %s" % (COUNTS_LABEL, SCRIPT_NAME, " ".join(result.stdout.split()))]
    missing = [] if counts["phases"] is not None else ["phases: a flat task list (no ### phase headings)"]
    return counts, missing


def doc_stats(path, plan_progress, run):
    """The C3 `doc-stats` object of the doc at absolute `path`."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError as error:
        raise EstimateError("cannot read %s: %s" % (path, error.strerror or error)) from error
    counts, missing = _counts(path, plan_progress, run)
    hours = _hours(text)
    if hours is None:
        missing.append("estimatedHours: no task line ends with '· <hours>h'")
    return {**counts, "estimatedHours": hours, "missing": missing}
