"""OpenCode adapter of the C2 reader port: reads the database `opencode.db` strictly read-only (a `mode=ro` URI), where
sessions are the rows of `session_v2` (`parent_id` marks a sub-agent) and their messages the rows of `session_message`, read by `seq`
(`opencode_messages` parses them, `opencode_family` makes the session). Every database problem becomes an unreadable source; nothing
raises: a database that does not exist is `NO_DATABASE_REASON`, one a writer holds locked `DATABASE_BUSY_REASON` (read again by the next
scan and read), one without the tables of OpenCode v2 (a v1-only database) `UNSUPPORTED_DATABASE_REASON`, a FIFO or another special file
in its place (never opened, as SQLite would block on it) "not a regular file", any other failure `CANNOT_BE_OPENED_REASON`. Which
database file is chosen is not this module's business (`opencode_database` does): the reader is built
with one path. No row is ever written; for a WAL-mode database whose `-wal` and `-shm` files are gone SQLite itself creates those two
bookkeeping files on the first read.

A unit is one session row; its key is the session id and its fingerprint the messages' row count, newest `seq` and newest
`time_updated` (OpenCode updates an assistant row in place while it streams, so the count and `seq` alone would miss a finish, tokens
and `time.completed` arriving). The unit was modified at the newest of that and the session row's own `time_updated`; a time that is no
time counts as the epoch (the unit stays listed, and holds no activity). A session row whose `time_created` is no time gives no session
when it is a root, and starts a sub-agent when the root does.

A unit's session is learned from the `parent_id` chain: the topmost session row is the root. A session in a parent loop, and every
session hanging from one, is its own session key: `read` lists it unreadable (`PARENT_CYCLE_REASON`) and builds no session from it, and
nothing walks a loop. The parent links of the last scan serve `session_of` and `read`, so a family is judged on what the scan saw."""
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, NamedTuple, Optional, Set, Tuple

from .agent_tree import nodes_entering_a_loop
from .json_records import parse_epoch_milliseconds, whole_count
from .model import Harness, is_safe_id
from .opencode_family import ParentOf, SessionRow, build_session, root_id_for
from .opencode_messages import Transcript, parse_messages
from .reader_port import (CANNOT_BE_OPENED_REASON, NOT_A_REGULAR_FILE_REASON, PARENT_CYCLE_REASON, UNSAFE_ID_REASON, ReadResult, ScanResult,
                          SourceUnit, UnreadableSource, build_session_key, root_id_of)
from .store_files import NotARegularFileError, require_regular_file

NO_DATABASE_REASON = "no OpenCode database"
DATABASE_BUSY_REASON = "database busy"
UNSUPPORTED_DATABASE_REASON = "unsupported OpenCode database"
# How long a query waits for a lock before the database counts as busy. Short on purpose: nothing is remembered about a busy database, so
# every session of a sync would wait this long again.
BUSY_TIMEOUT_SECONDS = 1.0
_PRIMARY_RESULT_CODE_MASK = 0xFF  # SQLite's extended result codes keep the primary code in the low byte
_BUSY_CODES = (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
_UNSUPPORTED_SCHEMA_MESSAGES = ("no such table", "no such column")

_UNKNOWN_TIME = datetime.fromtimestamp(0, timezone.utc)
_SCAN_SQL = (
    "SELECT session.id, session.parent_id, session.time_updated, COUNT(message.id), IFNULL(MAX(message.seq), 0),"
    " IFNULL(MAX(message.time_updated), 0)"
    " FROM session_v2 AS session LEFT JOIN session_message AS message ON message.session_id = session.id"
    " GROUP BY session.id ORDER BY session.id")
_PARENTS_SQL = "SELECT id, parent_id FROM session_v2"
_SESSION_SQL = "SELECT id, parent_id, time_created, directory, agent FROM session_v2 WHERE id = ?"
_MESSAGES_SQL = "SELECT type, CAST(data AS TEXT) FROM session_message WHERE session_id = ? ORDER BY seq"


class _ParentLinks(NamedTuple):
    """The `parent_id` of every session of the database and the sessions whose chain of parents loops or enters a loop."""

    parent_of: ParentOf
    looping: Set[str]


def _links_of(pairs: Iterable[Tuple[object, object]]) -> _ParentLinks:
    """The links of `(id, parent id)` pairs."""
    parent_of = {str(session_id): _text_or_none(parent_id) for session_id, parent_id in pairs}
    return _ParentLinks(parent_of, nodes_entering_a_loop(parent_of))


def _decode_text(raw: bytes) -> str:
    """A text cell as text; bytes that are no UTF-8 become replacement characters, so one bad row cannot make a session unreadable."""
    return raw.decode("utf-8", errors="replace")


def _text_or_none(value: object) -> Optional[str]:
    return str(value) if value is not None and value != "" else None


def _unreadable(path: str, reason: str) -> UnreadableSource:
    return UnreadableSource(Harness.OPENCODE, path, reason)


def _failure_reason(path: str, error: sqlite3.Error) -> str:
    """Why the database at `path` could not be queried."""
    try:
        require_regular_file(path)
    except FileNotFoundError:  # also a dangling link; any other failure (say no permission) is not "no database"
        return NO_DATABASE_REASON
    except NotARegularFileError:
        return NOT_A_REGULAR_FILE_REASON
    except OSError:
        return CANNOT_BE_OPENED_REASON
    if (getattr(error, "sqlite_errorcode", 0) & _PRIMARY_RESULT_CODE_MASK) in _BUSY_CODES:
        return DATABASE_BUSY_REASON
    if isinstance(error, sqlite3.OperationalError) and str(error).startswith(_UNSUPPORTED_SCHEMA_MESSAGES):
        return UNSUPPORTED_DATABASE_REASON
    return CANNOT_BE_OPENED_REASON


def _fingerprint(message_count: object, newest_seq: object, newest_update: object) -> str:
    """The messages' row count, newest `seq` and newest `time_updated`; a value that is no count from zero up counts as 0."""
    return ":".join(str(whole_count(value)) for value in (message_count, newest_seq, newest_update))


def _modified_at(session_updated: object, newest_update: object) -> datetime:
    """The newer of the session row's and its newest message's `time_updated`; the epoch when neither is a time."""
    return parse_epoch_milliseconds(max(whole_count(session_updated), whole_count(newest_update))) or _UNKNOWN_TIME


def _row_of(database: sqlite3.Connection, session_id: str) -> Optional[SessionRow]:
    found = database.execute(_SESSION_SQL, (session_id,)).fetchone()
    if found is None:
        return None
    found_id, parent_id, created_at, directory, agent = found
    return SessionRow(str(found_id), _text_or_none(parent_id), parse_epoch_milliseconds(created_at), _text_or_none(directory),
                      _text_or_none(agent))


def _transcript_of(database: sqlite3.Connection, session_id: str) -> Transcript:
    return parse_messages(database.execute(_MESSAGES_SQL, (session_id,)))


class OpenCodeReader:
    """Reads the OpenCode database at `database_path`. A database that is missing, busy, without the tables of OpenCode v2 or otherwise
    cannot be opened or queried makes `scan` list it unreadable under its path with the reason (see the module text), and `read` every unit
    it was given; `session_of` is then None (not learnable now). Nothing is remembered about a failure: the next call tries again."""

    def __init__(self, database_path: str) -> None:
        self._path = database_path
        self._uri = Path(database_path).absolute().as_uri() + "?mode=ro"
        self._links: Optional[_ParentLinks] = None  # the parent links of the last scan; loaded by the first call that needs them otherwise

    def _connect(self) -> sqlite3.Connection:
        """A read-only connection. A FIFO or another special file in the database's place is refused before SQLite could open (and block
        on) it, as an error `_failure_reason` words as "not a regular file"."""
        try:
            require_regular_file(self._path)
        except NotARegularFileError as error:
            raise sqlite3.OperationalError(str(error)) from error
        except OSError:
            pass  # SQLite reports a missing or unreachable file itself
        connection = sqlite3.connect(self._uri, uri=True, timeout=BUSY_TIMEOUT_SECONDS)
        connection.text_factory = _decode_text
        return connection

    def _links_in(self, database: sqlite3.Connection) -> _ParentLinks:
        """The parent links of the last scan; loaded from `database` when no scan has run on this reader."""
        if self._links is None:
            self._links = _links_of(database.execute(_PARENTS_SQL))
        return self._links

    def scan(self) -> ScanResult:
        """Every session row as a unit, and the database itself as an unreadable source when it cannot be queried."""
        try:
            with closing(self._connect()) as database:
                rows = database.execute(_SCAN_SQL).fetchall()
        except sqlite3.Error as error:
            self._links = None
            return ScanResult(unreadable=(_unreadable(self._path, _failure_reason(self._path, error)),))
        self._links = _links_of((row[0], row[1]) for row in rows)
        units = [SourceUnit(key=str(session_id), fingerprint=_fingerprint(message_count, newest_seq, newest_update),
                            modified_at=_modified_at(session_updated, newest_update))
                 for session_id, _, session_updated, message_count, newest_seq, newest_update in rows]
        return ScanResult(units=tuple(units))

    def session_of(self, unit: SourceUnit) -> Optional[str]:
        """The key of the session of the root of the unit's `parent_id` chain; the unit's own id when it is in or hangs from a parent
        loop. None when the database cannot be queried."""
        links = self._links
        if links is None:
            try:
                with closing(self._connect()) as database:
                    links = self._links_in(database)
            except sqlite3.Error:
                return None
        return build_session_key(Harness.OPENCODE, root_id_for(unit.key, links.parent_of, links.looping))

    def read(self, session_key: str, units: Tuple[SourceUnit, ...]) -> ReadResult:
        """The session of the root `session_key` names with its sub-agents, built from the sessions `units` name, and the units in a parent
        loop as unreadable; the units that cannot be read at all when the database cannot be queried."""
        root_id = root_id_of(session_key)
        if not root_id or session_key != build_session_key(Harness.OPENCODE, root_id) or not units:
            return ReadResult()
        keys = sorted(unit.key for unit in units)
        if not is_safe_id(root_id):
            return ReadResult(unreadable=(_unreadable(keys[0], UNSAFE_ID_REASON),))
        try:
            with closing(self._connect()) as database:
                return self._read_family(database, self._links_in(database), root_id, keys)
        except sqlite3.Error as error:
            reason = _failure_reason(self._path, error)
            return ReadResult(unreadable=tuple(_unreadable(key, reason) for key in keys))

    @staticmethod
    def _read_family(database: sqlite3.Connection, links: _ParentLinks, root_id: str, keys: Iterable[str]) -> ReadResult:
        family = [row for row in (_row_of(database, key) for key in keys) if row is not None]  # a session deleted since the scan is gone
        unreadable = tuple(_unreadable(row.id, PARENT_CYCLE_REASON) for row in family if row.id in links.looping)
        root = next((row for row in family if row.id == root_id), None) or _row_of(database, root_id)
        if root is None or root.id in links.looping:
            return ReadResult(unreadable=unreadable)
        children = [(row, _transcript_of(database, row.id)) for row in family if row.id != root_id and row.id not in links.looping]
        session = build_session(root, _transcript_of(database, root_id), links.parent_of, children)
        return ReadResult(session=session, unreadable=unreadable)
