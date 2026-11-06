"""The SP of feature cards at the scale file's anchor (C3 `calculate`): each card `{"name", "id", "description", "effort", "tasks",
"estimatedHours"}` gets what `anchor <estimatedHours>` gives, and the cards that get one form the pool that `neighbours` and `place`
compare the others with. Pure: the anchor and the scale come from the caller."""
import math

from estimation.anchor import hours_size, off_reason
from estimation.errors import ArgumentError
from estimation.features import is_number

FEATURES_KEY = "features"
CARD_KEYS = ("name", "id", "description", "effort", "tasks", "estimatedHours")
NO_HOURS = "no planned hours"
SHAPE = "features must be a JSON object with a features list"


def _unknown(keys, known):
    unknown = [key for key in keys if key not in known]
    if unknown:
        raise ArgumentError("unknown field '%s'" % unknown[0])


def _check_card(card, names):
    """Refuses a card that is not an object, holds an unknown key, has no name or one of `names`, or malformed hours or tasks."""
    if not (isinstance(card, dict) and isinstance(card.get("name"), str) and card["name"].strip()):
        raise ArgumentError("each feature needs a name")
    _unknown(card, CARD_KEYS)
    name = card["name"]
    if name in names:
        raise ArgumentError("duplicate feature name '%s'" % name)
    hours = card.get("estimatedHours")
    if hours is not None and not (is_number(hours) and math.isfinite(hours) and hours > 0):
        raise ArgumentError("estimatedHours of '%s' must be a number above 0 or null" % name)
    tasks = card.get("tasks")
    if tasks is not None and not (isinstance(tasks, int) and not isinstance(tasks, bool) and tasks >= 0):
        raise ArgumentError("tasks of '%s' must be an integer of 0 or more or null" % name)


def check_cards(request):
    """The cards of a `{"features": [...]}` request, refused as a bad argument when malformed."""
    if not (isinstance(request, dict) and isinstance(request.get(FEATURES_KEY), list)):
        raise ArgumentError(SHAPE)
    _unknown(request, (FEATURES_KEY,))
    names = set()
    for card in request[FEATURES_KEY]:
        _check_card(card, names)
        names.add(card["name"])
    return request[FEATURES_KEY]


def calculate(cards, anchor, scale):
    """`{"features": [{"name", "hours", "sp", "missing"}], "pool": [...]}`, both in card order: `sp` is null, with the reason in
    `missing`, when a card has no hours or the anchor is off; `pool` holds each card with an `sp`, as `neighbours` compares it."""
    features, pool = [], []
    for card in cards:
        hours = card.get("estimatedHours")
        sp = hours_size(anchor, scale, hours)
        reason = NO_HOURS if hours is None else off_reason(anchor, scale)
        features.append({"name": card["name"], "hours": hours, "sp": sp, "missing": [] if sp is not None else [reason]})
        if sp is not None:
            pool.append({"name": card["name"], "id": card.get("id"), "description": card.get("description"),
                         "effort": card.get("effort"), "sp": sp, "tasks": card.get("tasks"), "estimatedHours": hours})
    return {"features": features, "pool": pool}
