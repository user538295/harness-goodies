"""The anchor of a project's scale (`anchor` in `.harness-goodies/estimation.json`): the SP that a number of planned hours equals,
by default 13 SP = 75 h, about one developer's two-week sprint. When it is on, a feature with planned hours gets its SP from those
hours, never by comparison. It is off when absent, null, {} or with hours null
or 0, and on a scale that is not all numbers."""
import json
import math

from estimation.errors import EstimateError
from estimation.features import is_number

ANCHOR_KEY = "anchor"
ANCHOR_KEYS = ("sp", "hours")
DEFAULT_ANCHOR = {"sp": 13, "hours": 75}
SHAPE = 'anchor must be null or {"sp": <SP>, "hours": <hours>}'
OFF = "no anchor in .harness-goodies/estimation.json"
NOT_NUMERIC = "the anchor needs a numeric scale"


def _numeric(scale):
    return all(is_number(value) for value in scale)


def _set(anchor):
    """Whether `anchor` has hours: absent, null, {} or hours null or 0 is off."""
    return isinstance(anchor, dict) and bool(anchor.get("hours"))


def check(anchor, scale):
    """Refuses an anchor of another shape, with negative hours, or, when it is set on a numeric scale, whose sp is not a value of
    the scale above 0."""
    if anchor is None:
        return
    if not (isinstance(anchor, dict) and set(anchor) <= set(ANCHOR_KEYS)
            and all(anchor.get(key) is None or is_number(anchor[key]) for key in ANCHOR_KEYS)):
        raise EstimateError(SHAPE)
    if (anchor.get("hours") or 0) < 0:
        raise EstimateError("anchor hours must be 0 or more")
    sp = anchor.get("sp")
    if _set(anchor) and _numeric(scale) and not (sp in scale and sp > 0):
        raise EstimateError("anchor sp %s is not a value of the scale above 0" % json.dumps(sp))


def off_reason(anchor, scale):
    """Why the anchor gives no size, None when it is in force."""
    if not _set(anchor):
        return OFF
    return None if _numeric(scale) else NOT_NUMERIC


def hours_size(anchor, scale, hours):
    """The value of the scale that `hours` planned hours give, the nearest by ratio (the scale's gaps grow with the size); None
    when the anchor is off or `hours` is not above 0."""
    if off_reason(anchor, scale) or not (is_number(hours) and hours > 0):
        return None
    size = hours * anchor["sp"] / anchor["hours"]
    return min((value for value in scale if value > 0), key=lambda value: abs(math.log(value / size)))
