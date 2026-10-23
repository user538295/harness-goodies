"""What the writers under the log root and the Cursor reader share: private folders and the refusal of links. A new folder is created 0700
(Windows turns that mode into an access list for its owner and the administrators); an existing folder keeps its mode. Nothing is written
through a symbolic link or a Windows junction: a writer asks `make_real_folders`, `is_link` or `require_no_link` before it writes and reports
`LINK_REASON` (`FOLDER_LINK_REASON` for its folder) for what it skipped, which `status` names; the Cursor reader lists the links it finds
under `.records` with the same words. The check and the write are two steps, so a link put in place between them by another program of the
same user is not caught, which is the trust the plan gives a user's own folders (a file replaced by `os.replace` is swapped as a link, never
written through)."""
import os
from typing import Union

FOLDER_MODE = 0o700
LINK_REASON = "is a symbolic link or junction, so it is left alone and nothing is written through it"
FOLDER_LINK_REASON = "its folder " + LINK_REASON


def is_link(path: Union[str, os.PathLike]) -> bool:
    """Whether `path` itself is a symbolic link (dangling too) or a Windows junction."""
    return os.path.islink(path) or os.path.isjunction(path)


def is_real_folder(path: Union[str, os.PathLike]) -> bool:
    """Whether `path` is a folder itself, neither a link nor a junction to one."""
    return os.path.isdir(path) and not is_link(path)


def require_no_link(path: Union[str, os.PathLike]) -> None:
    """Raises an `OSError` naming `path` when it is a link, for a file the caller cannot do without."""
    if is_link(path):
        raise OSError("%s %s" % (path, LINK_REASON))


def _make_folder(path: str) -> None:
    """Creates the folder `path` with mode `FOLDER_MODE`; a folder that exists is kept as it is. A file or a dangling link in its place
    raises."""
    try:
        os.mkdir(path, FOLDER_MODE)
    except FileExistsError:
        if not os.path.isdir(path):
            raise


def make_folders(path: Union[str, os.PathLike]) -> None:
    """Creates the folder `path` and every missing parent, each new one with mode `FOLDER_MODE` (`os.makedirs` would give the parents the
    default mode)."""
    path = os.fspath(path)
    if os.path.isdir(path):
        return
    parent = os.path.dirname(path)
    if parent and parent != path:
        make_folders(parent)
    _make_folder(path)


def make_real_folders(path: Union[str, os.PathLike]) -> bool:
    """Like `make_folders`, but a `path` that is a link (or junction) is left alone and creates nothing: the answer is False. True when
    `path` is a folder itself, created now or before."""
    if is_link(path):
        return False
    make_folders(path)
    return True
