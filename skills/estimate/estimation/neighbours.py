"""The features a feature is compared with (C3 `neighbours` and `place`): up to PICKS from every value of the scale that holds a pool
feature, never the feature itself. The draw is random but repeatable: each value's features, sorted by name, are sampled without
replacement by a `random.Random` seeded with the first SEED_BYTES of the SHA-256 of the feature's name (never Python's salted
`hash()`), so the same name always gets the same picks and a renamed feature new ones. The judge sees the picks without their sizes;
the groups name the picks of each value. `missing` names each value with fewer than PICKS, each empty value between the smallest
and the biggest held one, and each pick without an effort. Pure: every size is a value of the scale."""
import hashlib
import json
import random

PICKS = 3
SEED_BYTES = 8
SHOWN_KEYS = ("name", "id", "description", "effort", "tasks", "estimatedHours")  # what the judge sees of a pick: never its size
NONE_AT = "no reference at %s SP"
NO_REFERENCE = "no reference to compare with"
ONLY = "only %d %s at %s SP"
NO_EFFORT = "no effort for '%s'"


def _by_name(feature):
    return feature["name"]


def _draw(features, name):
    """Up to PICKS of `features` in name order, the same ones for the same `name` whatever their order."""
    seed = int.from_bytes(hashlib.sha256(name.encode("utf-8")).digest()[:SEED_BYTES], "big")
    drawn = random.Random(seed).sample(sorted(features, key=_by_name), min(PICKS, len(features)))
    return sorted(drawn, key=_by_name)


def pick(pool, scale, name):
    """`{"picks", "groups", "missing"}` for the feature `name`: `picks` holds every pick in name order without its size (what the
    judge sees); `groups` holds `{"sp", "names"}` for every value of the scale that holds a pool feature, in scale order."""
    others = [feature for feature in pool if feature["name"] != name]
    held = [value for value in scale if any(feature["sp"] == value for feature in others)]
    if not held:
        return {"picks": [], "groups": [], "missing": [NO_REFERENCE]}
    groups, picks, missing = [], [], []
    for value in scale[scale.index(held[0]):scale.index(held[-1]) + 1]:
        members = [feature for feature in others if feature["sp"] == value]
        if not members:
            missing.append(NONE_AT % json.dumps(value))
            continue
        drawn = _draw(members, name)
        groups.append({"sp": value, "names": [feature["name"] for feature in drawn]})
        picks += drawn
        if len(members) < PICKS:
            missing.append(ONLY % (len(members), "reference" if len(members) == 1 else "references", json.dumps(value)))
        missing += [NO_EFFORT % feature["name"] for feature in drawn if not feature.get("effort")]
    shown = [{key: feature.get(key) for key in SHOWN_KEYS} for feature in sorted(picks, key=_by_name)]
    return {"picks": shown, "groups": groups, "missing": missing}
