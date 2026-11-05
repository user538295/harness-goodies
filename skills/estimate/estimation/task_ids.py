"""The C3 ID boundary rule shared by `git-stats` (a feature's own id anywhere in a commit message, in any case, which makes the
commit only a candidate for the feature) and `match-sessions` (task IDs in prompts): an ID counts when it is not preceded by a
word character, `.` or `-`, and not followed by a word character, `-` or `.<digit>` — so `BE-1` is found in `(BE-1)` and `BE-1.`
but not in `BE-10`, `BE-1.2` or `pre-BE-1`."""
import re

BEFORE = r"(?<![\w.-])"
AFTER = r"(?![\w-]|\.\d)"


def finder(ids, ignore_case=False):
    """A function giving the IDs of `ids` that a text mentions (as the text writes them), by one pattern; where two IDs start at
    the same place the longer one wins (`A.b` over `A`)."""
    if not ids:
        return lambda text: set()
    alternatives = "|".join(re.escape(found_id) for found_id in sorted(set(ids), key=len, reverse=True))
    pattern = re.compile(BEFORE + "(" + alternatives + ")" + AFTER, re.IGNORECASE if ignore_case else 0)
    return lambda text: set(pattern.findall(text))
