"""The C3 task-ID boundary rule shared by `git-stats` (commit messages) and `match-sessions` (prompts): an ID counts when it is not
preceded by a word character, `.` or `-`, and not followed by a word character, `-` or `.<digit>` — so `BE-1` is found in
`(BE-1)` and `BE-1.` but not in `BE-10`, `BE-1.2` or `pre-BE-1`."""
import re

BEFORE = r"(?<![\w.-])"
AFTER = r"(?![\w-]|\.\d)"


def finder(task_ids):
    """A function giving the IDs of `task_ids` that a text mentions, by one pattern; where two IDs start at the same place the longer
    one wins (`A.b` over `A`)."""
    if not task_ids:
        return lambda text: set()
    alternatives = "|".join(re.escape(task_id) for task_id in sorted(set(task_ids), key=len, reverse=True))
    pattern = re.compile(BEFORE + "(" + alternatives + ")" + AFTER)
    return lambda text: set(pattern.findall(text))
