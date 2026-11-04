"""The C3 task-ID boundary rule shared by `git-stats` (commit messages) and `match-sessions` (prompts): an ID counts when it is not
preceded by a word character, `.` or `-`, and not followed by a word character, `-` or `.<digit>` — so `BE-1` is found in
`(BE-1)` and `BE-1.` but not in `BE-10`, `BE-1.2` or `pre-BE-1`."""
import re

BEFORE = r"(?<![\w.-])"
AFTER = r"(?![\w-]|\.\d)"


def mentions_any(text, task_ids):
    return any(re.search(BEFORE + re.escape(task_id) + AFTER, text) for task_id in task_ids)
