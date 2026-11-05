"""The C3 evidence rule shared by `git-stats`, `match-sessions` and `record-commit`: what ties a commit or a prompt to a feature.

A commit belongs to a feature when it changes one of the feature's docs, followed back through their moves and copies, or names
the feature's own id in the scope of its subject (`feat(A7): …`; the `task_ids` boundary rule, in any case). An id anywhere else
in a message is no evidence (`e2e` is also a word, `C1` also a contract), and task IDs find no commit, since every plan reuses
`BE-1`. A prompt is evidence for the features its text names (a doc path, a doc basename or a task ID), else for the feature(s)
with the most commits authored while it ran (from its submission to its last reply). A name, task ID or commit that more than one
feature claims ties nothing to any of them: a task ID two plans list, or a commit that archives two features' docs, is no
evidence. A prompt without evidence belongs to the features of the prompt before it, and prompts before the first evidence to the
first evidence's features; a sub-agent belongs to the prompt it started under. Each prompt carries why it counts for each of its
features."""
import os
import re
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass
from typing import Optional

from estimation.task_ids import finder

NAMES = "names %s"
MADE_COMMITS = "made %d of its commits"
FOLLOWS_PROMPT = "follows prompt %d"
BEFORE_PROMPT = "before prompt %d"
_SUBJECT_SCOPE = re.compile(r"[\w-]+\(([^)\n]*)\)!?:")  # the Conventional Commits `type(scope):` that opens a subject


def subject_scope(message):
    """The scope of a commit message's subject (`A7` of `feat(A7): …`); empty when the subject has none."""
    found = _SUBJECT_SCOPE.match(message)
    return found.group(1) if found else ""


@dataclass(frozen=True)
class FeatureClaim:
    """One feature as the evidence rule sees it; docs in their stored form, `feature_id` its own id (None without one)."""
    name: str
    docs: tuple
    task_ids: tuple
    feature_id: Optional[str] = None


@dataclass(frozen=True)
class FeatureCommits:
    """`found`: {feature name: its commits, oldest first} in the order of the features; `shared`: the commits that more than one
    feature claims, oldest first, which count for none of them."""
    found: dict
    shared: list


def _sole_owners(claims):
    """{value: feature} of every value exactly one feature claims; `claims` maps each feature name to its values."""
    owners = {}
    for name, values in claims.items():
        for value in set(values):
            owners[value] = None if value in owners else name
    return {value: name for value, name in owners.items() if name is not None}


def feature_commits(history, features):
    """The FeatureCommits of `features`: each claims the commits changing one of its docs (followed back through their moves and
    copies) or naming its own id in their subject's scope, and keeps those no other feature claims."""
    commits = history.commits()
    claims = {}
    for feature in features:
        followed = set()
        for doc in feature.docs:
            followed |= history.following(doc)
        names_id = finder([feature.feature_id] if feature.feature_id else [], ignore_case=True)
        claims[feature.name] = [commit.hash for commit in commits
                                if commit.hash in followed or names_id(subject_scope(commit.message))]
    owners = _sole_owners(claims)
    claimed = {commit_hash for hashes in claims.values() for commit_hash in hashes}
    return FeatureCommits({feature.name: [commit for commit in commits if owners.get(commit.hash) == feature.name]
                           for feature in features},
                          [commit for commit in commits if commit.hash in claimed and commit.hash not in owners])


def attribute(prompt_evidence):
    """{feature: why it counts} of each prompt, from each prompt's own evidence (`prompt_evidence`: one such dict per prompt in
    order): a prompt without evidence takes the features of the last earlier prompt that has some (`follows prompt N`), prompts
    before the first evidence take the first evidence's (`before prompt N`); N counts from 1."""
    first = next((index for index, evidence in enumerate(prompt_evidence) if evidence), None)
    attributed = []
    source = first
    for index, evidence in enumerate(prompt_evidence):
        if evidence:
            source = index
            attributed.append(dict(evidence))
        elif first is None:
            attributed.append({})
        else:
            why = (BEFORE_PROMPT if index < first else FOLLOWS_PROMPT) % (source + 1)
            attributed.append(dict.fromkeys(prompt_evidence[source], why))
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

    def naming(self, text):
        """{feature: what of it `text` names, sorted}: doc paths, doc basenames (not one inside a named path) and task IDs."""
        found = {}
        for needle, name in self._names.items():
            if needle in text:
                found.setdefault(name, set()).add(needle)
        for task_id in self._find_task_ids(text):
            found.setdefault(self._task_ids[task_id], set()).add(task_id)
        return {name: sorted(needle for needle in needles if not any(needle != other and needle in other for other in needles))
                for name, needles in found.items()}

    def named(self, text):
        """The features `text` names."""
        return set(self.naming(text))

    def of_prompt(self, text, start, end):
        """{feature: why} of the features a prompt (or a whole session) is evidence for: those its text names (`names <what>`),
        else those with the most commits authored from `start` to `end` (`made <n> of its commits`; none without an end)."""
        naming = self.naming(text)
        if naming or end is None:
            return {name: NAMES % ", ".join(needles) for name, needles in naming.items()}
        made = Counter(self._owners[bisect_left(self._owned_times, start):bisect_right(self._owned_times, end)])
        most = max(made.values(), default=0)
        return {name: MADE_COMMITS % count for name, count in made.items() if count == most}

    def touches(self, start, end):
        """Whether any feature commit, shared ones too, was authored from `start` to `end`."""
        return bisect_right(self._all_times, end) > bisect_left(self._all_times, start)
