"""The C3 evidence rule shared by `git-stats`, `match-sessions` and `record-commit`: what ties a commit or a prompt to a feature.

A feature claims the commits that change one of its docs, followed back through their moves and copies, and the commits a checker
accepted for it (given by full hash, each reachable from HEAD). An id never ties a commit: the commits whose message (subject or
body) names the feature's own id (the `task_ids` boundary rule, in any case) and that no feature claims are only its candidates,
for a checker to accept or reject by the feature's name and description (`test(e2e): …` names the id `e2e` but is other work).
Task IDs find no commit, since every plan reuses `BE-1`. A prompt is evidence for the features its text names (a doc path, a doc
basename or a task ID), else for the feature(s) with the most claimed commits authored while it ran (from its submission to its
last reply). A prompt is also evidence for the features whose docs its tool calls wrote (never task IDs, and never a doc it only
read). A name, task ID or commit that more than one feature claims ties nothing to any of them: a task ID two plans list, or
a commit that archives two features' docs, is no evidence. A prompt without evidence belongs to the features of the prompt before
it, and prompts before the first evidence to the first evidence's features; a sub-agent belongs to the prompt it started under.
Clearing the context ends a run: the first prompt after a clear opens a segment, and a prompt follows or precedes evidence only
inside its own segment; a segment without any evidence counts for no feature.
Each prompt carries why it counts for each of its features."""
import os
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass
from typing import Optional

from estimation.errors import ArgumentError
from estimation.task_ids import finder

NAMES = "names %s"
MADE_COMMITS = "made %d of its commits"
WRITES = "writes %s"
REASON_SEPARATOR = "; "
FOLLOWS_PROMPT = "follows prompt %d"
BEFORE_PROMPT = "before prompt %d"
UNREACHABLE_COMMIT = "commit '%s' of '%s' is not a full hash of a commit reachable from HEAD"


@dataclass(frozen=True)
class FeatureClaim:
    """One feature as the evidence rule sees it; docs in their stored form, `feature_id` its own id (None without one), `commits`
    the full hashes a checker accepted for it."""
    name: str
    docs: tuple
    task_ids: tuple
    feature_id: Optional[str] = None
    commits: tuple = ()


@dataclass(frozen=True)
class FeatureCommits:
    """`found`: {feature name: its commits, oldest first} in the order of the features; `shared`: the commits that more than one
    feature claims, oldest first, which count for none of them; `candidates`: {feature name: the commits naming its id that no
    feature claims, oldest first} in the order of the features."""
    found: dict
    shared: list
    candidates: dict


def _sole_owners(claims):
    """{value: feature} of every value exactly one feature claims; `claims` maps each feature name to its values."""
    owners = {}
    for name, values in claims.items():
        for value in set(values):
            owners[value] = None if value in owners else name
    return {value: name for value, name in owners.items() if name is not None}


def _given(feature, reachable):
    """The hashes `feature` was given; one that is not a commit reachable from HEAD is a bad argument."""
    for commit_hash in feature.commits:
        if commit_hash not in reachable:
            raise ArgumentError(UNREACHABLE_COMMIT % (commit_hash, feature.name))
    return set(feature.commits)


def _candidates(commits, feature, claimed):
    """The commits naming `feature`'s own id in their message (the boundary rule, any case) that are not `claimed`."""
    if feature.feature_id is None:
        return []
    names_id = finder([feature.feature_id], ignore_case=True)
    return [commit for commit in commits if commit.hash not in claimed and names_id(commit.message)]


def feature_commits(history, features):
    """The FeatureCommits of `features`: each claims the commits changing one of its docs (followed back through their moves and
    copies) and the commits it was given, keeps those no other feature claims, and gets as candidates the unclaimed commits
    naming its id."""
    commits = history.commits()
    reachable = {commit.hash for commit in commits}
    claims = {}
    for feature in features:
        hashes = _given(feature, reachable)
        for doc in feature.docs:
            hashes |= history.following(doc)
        claims[feature.name] = [commit.hash for commit in commits if commit.hash in hashes]
    owners = _sole_owners(claims)
    claimed = {commit_hash for hashes in claims.values() for commit_hash in hashes}
    return FeatureCommits({feature.name: [commit for commit in commits if owners.get(commit.hash) == feature.name]
                           for feature in features},
                          [commit for commit in commits if commit.hash in claimed and commit.hash not in owners],
                          {feature.name: _candidates(commits, feature, claimed) for feature in features})


def _attribute_segment(prompt_evidence, offset):
    """`attribute` for the prompts of one context (`prompt_evidence`), the first of them being prompt `offset` + 1 of the session."""
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
            why = (BEFORE_PROMPT if index < first else FOLLOWS_PROMPT) % (offset + source + 1)
            attributed.append(dict.fromkeys(prompt_evidence[source], why))
    return attributed


def attribute(prompt_evidence, after_reset):
    """{feature: why it counts} of each prompt, from each prompt's own evidence (`prompt_evidence`: one such dict per prompt in
    order; `after_reset`: whether each prompt is the first after the user cleared the context). Within a context, a prompt without
    evidence takes the features of the last earlier prompt of that context that has some (`follows prompt N`), prompts before the
    first evidence take the first evidence's (`before prompt N`), and a context without evidence gives none; N counts from 1 in the
    session."""
    starts = [index for index, cleared in enumerate(after_reset) if cleared and index]
    attributed = []
    for start, end in zip([0, *starts], [*starts, len(prompt_evidence)]):
        attributed += _attribute_segment(prompt_evidence[start:end], start)
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

    @staticmethod
    def _outermost(found):
        """{feature: its needles sorted, without one inside another needle of the same feature}."""
        return {name: sorted(needle for needle in needles if not any(needle != other and needle in other for other in needles))
                for name, needles in found.items()}

    def naming(self, text):
        """{feature: what of it `text` names, sorted}: doc paths, doc basenames (not one inside a named path) and task IDs."""
        found = {}
        for needle, name in self._names.items():
            if needle in text:
                found.setdefault(name, set()).add(needle)
        for task_id in self._find_task_ids(text):
            found.setdefault(self._task_ids[task_id], set()).add(task_id)
        return self._outermost(found)

    def named(self, text):
        """The features `text` names."""
        return set(self.naming(text))

    def wrote(self, paths):
        """{feature: the docs of it that `paths` (the files written) hold, sorted}: a doc is held by a path that is it or ends with
        it after a `/` (a doc path under a folder, or a doc basename), never by a longer name that merely contains it."""
        found = {}
        for needle, name in self._names.items():
            if any(path == needle or path.endswith("/" + needle) for path in paths):
                found.setdefault(name, set()).add(needle)
        return self._outermost(found)

    def of_prompt(self, text, start, end, written=()):
        """{feature: why} of the features a prompt (or a whole session) is evidence for: those its text names (`names <what>`) and
        those whose docs its tool calls wrote (`writes <what>`; `written` holds the written paths; both reasons when both hold),
        else those with the most commits authored from `start` to `end` (`made <n> of its commits`; none without an end)."""
        reasons = {name: NAMES % ", ".join(needles) for name, needles in self.naming(text).items()}
        for name, needles in self.wrote(written).items():
            wrote = WRITES % ", ".join(needles)
            reasons[name] = REASON_SEPARATOR.join((reasons[name], wrote)) if name in reasons else wrote
        if reasons or end is None:
            return reasons
        made = Counter(self._owners[bisect_left(self._owned_times, start):bisect_right(self._owned_times, end)])
        most = max(made.values(), default=0)
        return {name: MADE_COMMITS % count for name, count in made.items() if count == most}

    def touches(self, start, end):
        """Whether any feature commit, shared ones too, was authored from `start` to `end`."""
        return bisect_right(self._all_times, end) > bisect_left(self._all_times, start)
