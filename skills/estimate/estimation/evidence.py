"""The C3 evidence rule shared by `git-stats`, `match-sessions` and `collect`: what ties a commit or a prompt to a feature.

A feature claims the commits that change one of its docs, followed back through their moves and copies, and the commits a checker
accepted for it (given by full hash, each reachable from HEAD). An id never ties a commit: the commits whose message (subject or
body) names the feature's own id (the `task_ids` boundary rule, in any case) and that no feature claims are only its candidates,
for a checker to accept or reject by the feature's name and description (`test(e2e): …` names the id `e2e` but is other work).
So are the unclaimed commits made since `files_since` that touch one of the feature's related files (`files`, the non-Markdown files
its docs link). A related file is never evidence for a prompt: one shared source file is named and written by every feature that
touches it, and a feature still missing from the store would not take its share. Task IDs find no commit, since every plan reuses
`BE-1`; only `init`, which sees every feature at once, gives them as evidence for prompts, `collect` gives none. A prompt is
evidence for the features its text names (a doc path, a doc basename or a task ID), else for the feature(s) with the most claimed
commits authored while it ran (from its submission to its last reply). A prompt is also evidence for the features whose docs its
tool calls, or those of the sub-agents that started under it, wrote (never task IDs, and never a doc only read). A name, task ID
or commit that more than one feature claims ties nothing to any of them: a task ID two plans list, or a commit that archives two
features' docs, is no evidence. A prompt without evidence belongs to the features of the prompt before
it, and prompts before the first evidence to the first evidence's features; a sub-agent belongs to the prompt it started under.
Clearing the context ends a run: the first prompt after a clear opens a segment, and a prompt follows or precedes evidence only
inside its own segment; a segment without any evidence counts for no feature.
Each prompt carries why it counts for each of its features. A prompt that counts for several features is shared by them (see
`sessions.py`: its tokens add up exactly, its minutes and cents are rounded per slice)."""
import os
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
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
    the full hashes a checker accepted for it, `files` its related files (stored form, none of them a doc) and `files_since` the
    moment from which commits touching them are candidates (None: none are)."""
    name: str
    docs: tuple
    task_ids: tuple
    feature_id: Optional[str] = None
    commits: tuple = ()
    files: tuple = ()
    files_since: Optional[datetime] = None


@dataclass(frozen=True)
class FeatureCommits:
    """`found`: {feature name: its commits, oldest first} in the order of the features; `shared`: the commits that more than one
    feature claims, oldest first, which count for none of them; `candidates`: {feature name: the unclaimed commits naming its id
    or, since its `files_since`, touching one of its related files, oldest first} in the order of the features; `dropped`:
    {feature name: the given hashes git no longer holds} (only with `drop_unreachable`); `shared_by`: {feature name: the `shared`
    commits it claims}."""
    found: dict
    shared: list
    candidates: dict
    dropped: dict
    shared_by: dict


def _sole_owners(claims):
    """{value: feature} of every value exactly one feature claims; `claims` maps each feature name to its values."""
    owners = {}
    for name, values in claims.items():
        for value in set(values):
            owners[value] = None if value in owners else name
    return {value: name for value, name in owners.items() if name is not None}


def _given(feature, reachable, drop_unreachable):
    """(the hashes `feature` was given that count, the ones dropped): one that is not a commit reachable from HEAD is a bad
    argument, or with `drop_unreachable` dropped."""
    kept = [commit_hash for commit_hash in feature.commits if commit_hash in reachable]
    dropped = [commit_hash for commit_hash in feature.commits if commit_hash not in reachable]
    if dropped and not drop_unreachable:
        raise ArgumentError(UNREACHABLE_COMMIT % (dropped[0], feature.name))
    return set(kept), dropped


def _candidates(commits, feature, claimed, history):
    """The commits that are not `claimed` and either name `feature`'s own id in their message (the boundary rule, any case) or,
    since its `files_since`, touch one of its related files."""
    names_id = finder([feature.feature_id], ignore_case=True) if feature.feature_id is not None else None
    touching = history.touching(feature.files) if feature.files and feature.files_since is not None else set()
    return [commit for commit in commits if commit.hash not in claimed
            and (names_id is not None and names_id(commit.message)
                 or commit.hash in touching and commit.authored_at >= feature.files_since)]


def feature_commits(history, features, drop_unreachable=False, follow=None):
    """The FeatureCommits of `features`: each claims the commits changing one of its docs (followed back through their moves and
    copies) and the commits it was given, keeps those no other feature claims, and gets as candidates the unclaimed commits
    naming its id or touching its related files. With `follow` (a feature's name) only that feature's docs are followed through
    git's history, which costs one git call per doc; every other feature claims the commits it was given and those of the followed
    feature (followed or given) that change one of its docs (judged by the paths git lists for them, so a commit that changed its
    doc only under an older path is missed)."""
    commits = history.commits()
    reachable = {commit.hash for commit in commits}
    given, dropped = {}, {}
    for feature in features:
        given[feature.name], dropped[feature.name] = _given(feature, reachable, drop_unreachable)
    followed = {}
    for feature in features:
        followed[feature.name] = set()
        if follow is None or feature.name == follow:
            for doc in feature.docs:
                followed[feature.name] |= history.following(doc)
    if follow is not None:
        checked = sorted(followed[follow] | given[follow])  # the followed feature's given hashes may change another's docs too
        changed = history.changed_paths(checked)
        for feature in features:
            if feature.name != follow:
                followed[feature.name] = {commit_hash for commit_hash in checked
                                          if changed.get(commit_hash, set()) & set(feature.docs)}
    held = {feature.name: given[feature.name] | followed[feature.name] for feature in features}
    claims = {name: [commit.hash for commit in commits if commit.hash in hashes] for name, hashes in held.items()}
    owners = _sole_owners(claims)
    claimed = {commit_hash for hashes in claims.values() for commit_hash in hashes}
    shared = [commit for commit in commits if commit.hash in claimed and commit.hash not in owners]
    return FeatureCommits({feature.name: [commit for commit in commits if owners.get(commit.hash) == feature.name]
                           for feature in features},
                          shared,
                          {feature.name: _candidates(commits, feature, claimed, history) for feature in features},
                          {name: hashes for name, hashes in dropped.items() if hashes},
                          {feature.name: [commit for commit in shared if commit.hash in held[feature.name]] for feature in features})


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
        it after a `/` (a doc path under a folder, or a doc basename), never by a longer name that merely contains it; a Windows
        path's backslashes count as slashes."""
        slashed = [path.replace("\\", "/") for path in paths]
        found = {}
        for needle, name in self._names.items():
            if any(path == needle or path.endswith("/" + needle) for path in slashed):
                found.setdefault(name, set()).add(needle)
        return self._outermost(found)

    def of_prompt(self, text, start, end, written=()):
        """{feature: why} of the features a prompt (or a whole session) is evidence for: those its text names (`names <what>`) and
        those whose docs its tool calls (or its sub-agents') wrote (`writes <what>`; `written` holds the written paths; both reasons
        when both hold), else those with the most commits authored from `start` to `end` (`made <n> of its commits`; none without
        an end)."""
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
