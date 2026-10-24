"""Pure path rules of the OMP session store, used by the reader and the family rules; no file access.

A root session is the file `<project>/<ts>_<uuid>.jsonl`; its id is the stem after the first `_`. Its children are the files in the
folder named by its stem, `<project>/<stem>/<Child>.jsonl`, and theirs are in `<project>/<stem>/<Child>/` (omp `task/executor.ts`).
`omp gc --apply` moves a file to `archive/sessions/<same relative path>.gz`, so the same relative path marks the copies of one
file. A file in a folder below the project folder belongs to the family of the first such folder."""
from typing import Hashable, NamedTuple, Sequence, Tuple

SESSIONS_FOLDER = ("sessions",)
ARCHIVE_FOLDER = ("archive", "sessions")
SESSION_SUFFIX = ".jsonl"
ARCHIVED_SUFFIX = ".jsonl.gz"

_ID_SEPARATOR = "_"
_PATH_SEPARATORS = ("/", "\\")
_ROOT_FILE_DEPTH = 2  # `<project>/<file>`; a file straight in the store folder (depth 1) is a root too
_FAMILY_FOLDER_INDEX = 1  # the folder below the project folder that holds a family


class Place(NamedTuple):
    """Where a session file sits, told by its path alone. `root_stem` is the stem of the root session of its family, `where` the
    folders below the store folder and, last, its own stem (the same for the two copies `omp gc` leaves), `is_root` whether it is
    the root session's own file."""

    root_stem: str
    where: Tuple[str, ...]
    is_root: bool

    @property
    def stem(self) -> str:
        return self.where[-1]

    @property
    def identity(self) -> Tuple[Hashable, ...]:
        """What tells the copies of one file from other files: the root session's own file is one per session whatever its stem,
        any other file is told by its folders and stem."""
        return (True,) if self.is_root else self.where


def place_of(relative_parts: Sequence[str], suffix: str) -> Place:
    """The place of the file whose path below its store folder is `relative_parts` (folders, then the file name ending in `suffix`)."""
    stem = relative_parts[-1][: -len(suffix)]
    where = tuple(relative_parts[:-1]) + (stem,)
    if len(relative_parts) <= _ROOT_FILE_DEPTH:
        return Place(stem, where, True)
    return Place(relative_parts[_FAMILY_FOLDER_INDEX], where, False)


def root_id_of_stem(root_stem: str) -> str:
    """The session id of a root file stem `<timestamp>_<uuid>`: what follows the first `_`; a stem without one is its own id."""
    head, separator, tail = root_stem.partition(_ID_SEPARATOR)
    return tail if separator else head


def stem_of_path(path: str) -> str:
    """The file stem of a session path written with `/` or `\\` separators, without `.jsonl` or `.jsonl.gz`."""
    name = path
    for separator in _PATH_SEPARATORS:
        name = name.rsplit(separator, 1)[-1]
    for suffix in (ARCHIVED_SUFFIX, SESSION_SUFFIX):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name
