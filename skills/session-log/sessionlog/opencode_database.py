"""Which OpenCode database file a run reads, inside the data folder `~/.local/share/opencode`:

1. `OPENCODE_DB` when it is set and not empty: a name relative to the data folder, or an absolute path; `:memory:` is refused.
2. else `OPENCODE_CHANNEL`: `latest`, `beta` and `prod` (and any channel while `OPENCODE_DISABLE_CHANNEL_DB` is `1` or `true`) give
   `opencode.db`; any other channel gives `opencode-<channel>.db`, each character outside `[A-Za-z0-9._ -]` replaced by `-`.
3. else `opencode.db` when it exists, else the newest (by modification time, the first by name among equals) regular `opencode-*.db`
   (never a link, a folder or another special file), else `opencode.db`, which does not exist: the reader lists it as "no OpenCode database".

Whichever way it was chosen, the file (links followed) must lie inside the data folder, so neither a variable nor a link can point the
reader at another SQLite file. The path handed back is the one that was checked, as spelled (never normalised: with a link before a `..`
the lexical and the real file differ, and the kernel resolves the spelled path the way `realpath` does).
Only the file system is looked at; nothing is opened or created."""
import os
import re
from stat import S_ISREG
from typing import Mapping, Optional

DATABASE_VARIABLE = "OPENCODE_DB"
CHANNEL_VARIABLE = "OPENCODE_CHANNEL"
DISABLE_CHANNEL_VARIABLE = "OPENCODE_DISABLE_CHANNEL_DB"
IN_MEMORY_DATABASE = ":memory:"
DATABASE_NAME = "opencode.db"
CHANNEL_DATABASE_PREFIX = "opencode-"
DATABASE_SUFFIX = ".db"
_DEFAULT_CHANNELS = ("latest", "beta", "prod")
_DISABLING_VALUES = ("1", "true")
_OTHER_CHANNEL_CHARACTERS = re.compile(r"[^A-Za-z0-9._ -]")


class InMemoryDatabaseError(Exception):
    """`OPENCODE_DB` is `:memory:`; the entry words the error."""


class DatabaseOutsideDataFolderError(Exception):
    """The chosen file, links followed, is not a file inside the data folder; `path` is the chosen path. The entry words the error."""

    def __init__(self, path: str) -> None:
        super().__init__(path)
        self.path = path


def choose_database(data_folder: str, environment: Mapping[str, str]) -> str:
    """The path of the database to read, inside `data_folder`; `environment` holds the variables of the run (unset and empty count the
    same). Raises `InMemoryDatabaseError` for `:memory:` and `DatabaseOutsideDataFolderError` for a file that is not inside the folder."""
    configured = environment.get(DATABASE_VARIABLE, "")
    if configured == IN_MEMORY_DATABASE:
        raise InMemoryDatabaseError()
    name = configured or _database_name(data_folder, environment)
    return _require_inside(data_folder, os.path.join(data_folder, name))


def _require_inside(data_folder: str, path: str) -> str:
    """`path` itself, when the file it names (links followed; case folded where the platform's path module does) lies inside the folder."""
    folder, chosen = (os.path.normcase(os.path.realpath(each)) for each in (data_folder, path))
    try:
        is_inside = chosen != folder and os.path.commonpath([folder, chosen]) == folder
    except ValueError:  # another drive
        is_inside = False
    if not is_inside:
        raise DatabaseOutsideDataFolderError(path)
    return path


def _database_name(data_folder: str, environment: Mapping[str, str]) -> str:
    channel = environment.get(CHANNEL_VARIABLE, "")
    if channel:
        if channel in _DEFAULT_CHANNELS or environment.get(DISABLE_CHANNEL_VARIABLE, "") in _DISABLING_VALUES:
            return DATABASE_NAME
        return CHANNEL_DATABASE_PREFIX + _OTHER_CHANNEL_CHARACTERS.sub("-", channel) + DATABASE_SUFFIX
    if os.path.isfile(os.path.join(data_folder, DATABASE_NAME)):
        return DATABASE_NAME
    return _newest_channel_database(data_folder) or DATABASE_NAME


def _newest_channel_database(data_folder: str) -> Optional[str]:
    """The name of the newest regular `opencode-*.db` of the folder (a link is not a regular file here); None when it has none or the
    folder cannot be listed."""
    try:
        names = sorted(os.listdir(data_folder))
    except OSError:
        return None
    newest, newest_time = None, None
    for name in names:
        if not (name.startswith(CHANNEL_DATABASE_PREFIX) and name.endswith(DATABASE_SUFFIX)):
            continue
        try:
            status = os.lstat(os.path.join(data_folder, name))
        except OSError:
            continue
        if S_ISREG(status.st_mode) and (newest_time is None or status.st_mtime_ns > newest_time):
            newest, newest_time = name, status.st_mtime_ns
    return newest
