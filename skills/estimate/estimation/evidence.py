"""The C3 evidence rule shared by `git-stats`, `match-sessions` and `record-commit`: what ties a commit or a prompt to a feature.

A commit belongs to a feature when it changes one of the feature's docs or names one of its task IDs (the `task_ids` boundary
rule). A prompt is evidence for the features its text names (a doc path, a doc basename or a task ID), else for the feature(s) with
the most commits authored while it ran (from its submission to its last reply). A name, task ID or commit that more than one
feature claims ties nothing to any of them: a task ID every plan reuses (`BE-1`) or a commit that touches two features' docs is no
evidence. A prompt without evidence belongs to the features of the prompt before it, and prompts before the first evidence to the
first evidence's features; a sub-agent belongs to the prompt it started under."""
import os
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass

from estimation.task_ids import finder

SHARED_TASK_IDS = "task IDs more than one feature lists find no commits: %s"


@dataclass(frozen=True)
class FeatureClaim:
    """One feature as the evidence rule sees it; docs in their stored form."""
    name: str
    docs: tuple
    task_ids: tuple


def _sole_owners(claims):
    """{value: feature} of every value exactly one feature claims; `claims` maps each feature name to its values."""
    owners = {}
    for name, values in claims.items():
        for value in set(values):
            owners[value] = None if value in owners else name
    return {value: name for value, name in owners.items() if name is not None}


def shared_task_ids(features):
    """The task IDs more than one of `features` lists, sorted."""
    listed = {task_id for feature in features for task_id in feature.task_ids}
    return sorted(listed - _sole_owners({feature.name: feature.task_ids for feature in features}).keys())


def feature_commits(history, features):
    """{feature name: its commits, oldest first} in the order of `features`: the commits changing one of its docs or naming one of
    its task IDs that no other feature lists."""
    commits = history.commits()
    shared = set(shared_task_ids(features))
    found = {}
    for feature in features:
        changing = history.changing(list(feature.docs))
        mentions = finder([task_id for task_id in feature.task_ids if task_id not in shared])
        found[feature.name] = [commit for commit in commits if commit.hash in changing or mentions(commit.message)]
    return found


def attribute(prompt_evidence):
    """The features of each prompt, from the features its own evidence names (`prompt_evidence`, one set per prompt in order)."""
    current = next((features for features in prompt_evidence if features), frozenset())
    attributed = []
    for features in prompt_evidence:
        current = features or current
        attributed.append(current)
    return attributed


def prompt_under(prompt_starts, started_at):
    """The index of the last prompt submitted at or before `started_at` (`prompt_starts` ascending); 0 for an earlier start."""
    return max(bisect_right(prompt_starts, started_at) - 1, 0)


class Evidence:
    """The evidence of `features` (FeatureClaim list) given their commits' author times (`authored`: {feature name: {hash: time}})."""

    def __init__(self, features, authored):
        self._names = _sole_owners({feature.name: [*feature.docs, *map(os.path.basename, feature.docs)] for feature in features})
        self._task_ids = _sole_owners({feature.name: feature.task_ids for feature in features})
        self._find_task_ids = finder(list(self._task_ids))
        times = {commit_hash: moment for found in authored.values() for commit_hash, moment in found.items()}
        owned = sorted((times[commit_hash], name) for commit_hash, name in _sole_owners(authored).items())
        self._owned_times = [moment for moment, _ in owned]
        self._owners = [name for _, name in owned]
        self._all_times = sorted(times.values())

    def named(self, text):
        """The features `text` names."""
        return ({name for needle, name in self._names.items() if needle in text}
                | {self._task_ids[task_id] for task_id in self._find_task_ids(text)})

    def of_prompt(self, text, start, end):
        """The features a prompt (or a whole session) is evidence for: those its text names, else those with the most commits
        authored from `start` to `end` (none without an end)."""
        named = self.named(text)
        if named or end is None:
            return frozenset(named)
        made = Counter(self._owners[bisect_left(self._owned_times, start):bisect_right(self._owned_times, end)])
        most = max(made.values(), default=0)
        return frozenset(name for name, count in made.items() if count == most)

    def touches(self, start, end):
        """Whether any feature commit, shared ones too, was authored from `start` to `end`."""
        return bisect_right(self._all_times, end) > bisect_left(self._all_times, start)
