"""The project's git history through the injected process runner: every commit reachable from HEAD with its author date, message and
numstat line counts; the commits that change given paths; one commit resolved to its full hash."""
from dataclasses import dataclass

from estimation.errors import EstimateError

GIT_SUCCESS = 0
RECORD_MARK = "\x1e"
FIELD_MARK = "\x1f"
LOG_FORMAT = "--format=%x1e%H%x1f%ad%x1f%B%x1f"
BINARY_COUNT = "-"  # numstat's count of a binary file
NUMSTAT_FIELDS = 3


@dataclass(frozen=True)
class Commit:
    hash: str
    date: str
    message: str
    added: int
    removed: int


def _count(text):
    return 0 if text == BINARY_COUNT else int(text)


def _line_counts(numstat):
    """Summed (added, removed) of `git log --numstat` lines."""
    added = removed = 0
    for line in numstat.splitlines():
        fields = line.split("\t", NUMSTAT_FIELDS - 1)
        if len(fields) == NUMSTAT_FIELDS:
            added += _count(fields[0])
            removed += _count(fields[1])
    return added, removed


class GitHistory:
    def __init__(self, root, run):
        self.root = root
        self.run = run

    def _git(self, *arguments):
        result = self.run(["git", "-c", "core.quotepath=off", *arguments], self.root)
        if result.code != GIT_SUCCESS:
            raise EstimateError("git %s failed: %s" % (arguments[0], " ".join(result.stderr.split()) or "exit %d" % result.code))
        return result.stdout

    def _has_commits(self):
        return self.run(["git", "rev-parse", "-q", "--verify", "HEAD"], self.root).code == GIT_SUCCESS

    def commits(self):
        """Every commit reachable from HEAD, oldest first."""
        if not self._has_commits():
            self._git("rev-parse", "--git-dir")  # outside a repository this names the real problem
            return []
        output = self._git("log", "--reverse", "--numstat", "--date=short", LOG_FORMAT)
        commits = []
        for record in output.split(RECORD_MARK)[1:]:
            commit_hash, date, message, numstat = record.split(FIELD_MARK, NUMSTAT_FIELDS)
            commits.append(Commit(commit_hash, date, message, *_line_counts(numstat)))
        return commits

    def changing(self, paths):
        """The hashes of the commits reachable from HEAD that change any of `paths` (root-relative)."""
        if not paths or not self._has_commits():
            return set()
        return set(self._git("log", "--format=%H", "--", *paths).split())

    def resolve(self, revision):
        """The full hash of the commit `revision` names."""
        return self._git("rev-parse", "--verify", "--end-of-options", revision + "^{commit}").strip()

    def line_counts(self, commit_hash):
        """(added, removed) of one commit."""
        return _line_counts(self._git("show", "--numstat", "--format=", commit_hash))
