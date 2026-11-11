"""The project's git history through the injected process runner: every commit reachable from HEAD with its author time, message
and numstat line counts; the commits that change a path, followed back through its moves and copies; the author times of given
commits; the per-file changes of given commits; the author time of the commit that first added a path; the tracked and
untracked-not-ignored files. Paths and hashes go to git in chunks of GIT_ARGUMENTS_PER_CALL. Line counts (`commits()`) leave out
committed dependency files (line_kinds' vendored kind), judging a renamed path by its new path; the per-file changes (`changes()`)
keep every file, so the report can show those lines as not counted."""
import re
from dataclasses import dataclass
from datetime import datetime

from estimation.errors import EstimateError
from estimation.line_kinds import VENDORED, kind_of

GIT_SUCCESS = 0
RECORD_MARK = "\x1e"
FIELD_MARK = "\x1f"
LOG_FORMAT = "--format=%x1e%H%x1f%aI%x1f%B%x1f"
CHANGES_FORMAT = "--format=%x1e%H%x1f%aI%x1f%s%x1f"
AUTHORED_FORMAT = "--format=%H%x1f%aI"
AUTHOR_TIME_FORMAT = "--format=%aI"
BINARY_COUNT = "-"  # numstat's count of a binary file
NUMSTAT_FIELDS = 3
RENAME_ARROW = " => "
BRACED_RENAME = re.compile(r"\{([^{}]*) => ([^{}]*)\}")  # numstat's `dir/{old => new}/file`
GIT_ARGUMENTS_PER_CALL = 500  # paths or hashes given to one git call


@dataclass(frozen=True)
class Commit:
    hash: str
    authored_at: datetime
    message: str
    added: int
    removed: int


@dataclass(frozen=True)
class FileChange:
    path: str
    added: int
    removed: int


@dataclass(frozen=True)
class CommitChanges:
    hash: str
    authored_at: datetime
    subject: str
    files: tuple[FileChange, ...]


def _count(text):
    return 0 if text == BINARY_COUNT else int(text)


def _line_counts(numstat):
    """Summed (added, removed) of `git log --numstat` lines, without committed dependency files (judged by their new path)."""
    added = removed = 0
    for line in numstat.splitlines():
        fields = line.split("\t", NUMSTAT_FIELDS - 1)
        if len(fields) == NUMSTAT_FIELDS and kind_of(_new_path(fields[2])) != VENDORED:
            added += _count(fields[0])
            removed += _count(fields[1])
    return added, removed


def _new_path(path):
    """The new path of a numstat path that may show a rename (`dir/{old => new}/f.py`, `old.py => new.py`)."""
    if BRACED_RENAME.search(path):
        return BRACED_RENAME.sub(lambda match: match.group(2), path).replace("//", "/").strip("/")
    return path.split(RENAME_ARROW)[-1]


def _file_changes(numstat):
    """The FileChanges of `git log --numstat` lines, in git's order; binary files count 0."""
    changes = []
    for line in numstat.splitlines():
        fields = line.split("\t", NUMSTAT_FIELDS - 1)
        if len(fields) == NUMSTAT_FIELDS:
            changes.append(FileChange(_new_path(fields[2]), _count(fields[0]), _count(fields[1])))
    return tuple(changes)


def _chunks(values):
    """`values` in lists of at most GIT_ARGUMENTS_PER_CALL, so one git call never gets more arguments than the system allows."""
    return [values[start:start + GIT_ARGUMENTS_PER_CALL] for start in range(0, len(values), GIT_ARGUMENTS_PER_CALL)]


def _record_paths(record):
    """(hash, the paths) of one `log --name-status -z` record: `<hash> NUL LF <status> NUL <path> [NUL <path>] NUL ...`, a
    rename or copy (status `R<score>`, `C<score>`) naming two paths, every other status one."""
    commit_hash, *fields = record.split("\0")
    if fields:
        fields[0] = fields[0].lstrip("\n")
    paths, index = set(), 0
    while index < len(fields) and fields[index]:
        count = 2 if fields[index][0] in "RC" else 1
        paths.update(fields[index + 1:index + 1 + count])
        index += 1 + count
    return commit_hash, paths


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
        output = self._git("log", "--reverse", "--numstat", LOG_FORMAT)
        commits = []
        for record in output.split(RECORD_MARK)[1:]:
            commit_hash, authored_at, message, numstat = record.split(FIELD_MARK, NUMSTAT_FIELDS)
            commits.append(Commit(commit_hash, datetime.fromisoformat(authored_at), message, *_line_counts(numstat)))
        return commits

    def following(self, path):
        """The hashes of the commits reachable from HEAD that change `path` (root-relative), followed back through its moves and
        copies (`git log --follow`), so a plan archived from `Backlog/` to `Completed/` keeps the commits made before the move."""
        if not self._has_commits():
            return set()
        return set(self._git("log", "--follow", "--format=%H", "--", path).split())

    def touching(self, paths):
        """The hashes of the commits reachable from HEAD that change any of `paths` (root-relative)."""
        if not paths or not self._has_commits():
            return set()
        return {commit_hash for chunk in _chunks(paths) for commit_hash in self._git("log", "--format=%H", "--", *chunk).split()}

    def changed_paths(self, hashes):
        """{hash: the root-relative paths the commit changes, both sides of a rename} of the given commits that the repository holds."""
        changed = {}
        for chunk in _chunks(hashes):
            output = self._git("log", "--no-walk=unsorted", "--ignore-missing", "-M", "--name-status", "-z", "--format=%x1e%H",
                               "--end-of-options", *chunk)
            changed.update(_record_paths(record) for record in output.split(RECORD_MARK)[1:])
        return changed

    def authored_at(self, hashes):
        """{hash: author time} of the given commits that the repository holds."""
        if not hashes:
            return {}
        lines = self._git("log", "--no-walk=unsorted", "--ignore-missing", AUTHORED_FORMAT, "--end-of-options", *hashes).splitlines()
        return {commit_hash: datetime.fromisoformat(moment) for commit_hash, moment in (line.split(FIELD_MARK) for line in lines)}

    def changes(self, hashes):
        """{hash: CommitChanges} of the given commits that the repository holds (git's default rename detection, so the per-file
        sum without dependency files equals `commits()`' counts); merge commits have no files."""
        if not hashes:
            return {}
        output = self._git("log", "--no-walk=unsorted", "--ignore-missing", "--numstat", CHANGES_FORMAT, "--end-of-options",
                           *hashes)
        changes = {}
        for record in output.split(RECORD_MARK)[1:]:
            commit_hash, authored_at, subject, numstat = record.split(FIELD_MARK, NUMSTAT_FIELDS)
            changes[commit_hash] = CommitChanges(commit_hash, datetime.fromisoformat(authored_at), subject,
                                                 _file_changes(numstat))
        return changes

    def added_at(self, path):
        """The author time of the commit reachable from HEAD that first added `path` (root-relative), following its renames;
        None when no such commit holds it."""
        if not self._has_commits():
            return None
        moments = self._git("log", "--follow", "--diff-filter=A", AUTHOR_TIME_FORMAT, "--", path).split()
        return min((datetime.fromisoformat(moment) for moment in moments), default=None)

    def files(self):
        """The root-relative paths of the tracked files and the untracked ones git does not ignore."""
        return [path for path in self._git("ls-files", "--cached", "--others", "--exclude-standard", "-z").split("\0") if path]
