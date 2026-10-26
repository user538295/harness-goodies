"""C6 filesystem adapter. Callers supply a checked prompt-logs root.

Sync documents use the contract's JSON field names. Each store tracks the last loaded snapshot to detect competing writes; create one store per sync run.
The persistent .sync.lock serializes re-read/merge/replace across processes and is released by the OS on exit. sync_reset stays true for the run after rebuilding corrupt state.
A save merges by key: this run's entries win, other runs' entries are kept, and an entry this run dropped since it loaded the state is dropped from the
file too unless another run changed it meanwhile.
New folders are 0700 and every file written is 0600 (`safe_paths`, `temp_files`; `.sync.json` gets 0600 from `tempfile.mkstemp`).
Nothing is written through a link: a `.sync.lock`, or a `.log_enabled` that is a dangling link, fails the write with an error naming it.
`.sync.json` is always replaced whole, so a link in its place is replaced by a regular file and its target is left alone. Reading through a link stays allowed.
A temp a killed save left (`.sync-*.tmp`) is removed by the first save of a later run once it is stale (`temp_files.STALE_TEMP_SECONDS`).
`.sync.json` is UTF-8: a lone surrogate in a stored string (a cwd or a file name that is not valid text) is written as its JSON escape and read back as itself.
Log paths are absolute, so the file records the log root they were written under (`logRoot`); a load under another root (the folder was moved or renamed)
moves every log path recorded under the old root to the new one, so the logs that moved with the root are used where they are and no second log appears.
`migrate_enabled` converts an old release's `.enabled` flag; commands and hooks run it before they read the flag, which stays a pure read. Flag fallback is reported by OnFlag.from_file_time; reading never repairs the flag.
"""
import copy
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from stat import S_ISREG
from typing import Callable, Optional

from .safe_paths import make_folders, require_no_link
from .temp_files import FILE_MODE, remove_stale_temps
from .state_port import OnFlag

MAX_INT32 = 2**31 - 1
NO_FOLLOW = getattr(os, 'O_NOFOLLOW', 0)  # where it exists, it closes the gap between the link check and the open of `.sync.lock`
LOG_ROOT_FIELD = 'logRoot'
_SYNC_TEMP = re.compile(r'\.sync-[a-z0-9_]+\.tmp')  # the names `tempfile.mkstemp` gives the temp of a save
_LONE_SURROGATE = re.compile('[\ud800-\udfff]')


def _utc_now():
    return datetime.now(timezone.utc)


def _folded(path):
    return os.path.normcase(os.path.normpath(path))


def _encode(document):
    """The document as UTF-8 JSON bytes: valid text stays as it is, a lone surrogate (which UTF-8 cannot hold) becomes the JSON escape that reads back as itself."""
    text = json.dumps(document, ensure_ascii=False, allow_nan=False)
    return (_LONE_SURROGATE.sub(lambda match: '\\u%04x' % ord(match.group()), text) + '\n').encode('utf-8')


def _empty_state():
    return {'units': {}, 'sessions': {}}


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError('invalid timestamp')
    moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if moment.utcoffset() is None:
        raise ValueError('missing timezone')


def _validate_state(state):
    if not isinstance(state, dict):
        raise ValueError('not an object')
    if 'lastSyncAt' in state:
        _timestamp(state['lastSyncAt'])
    if LOG_ROOT_FIELD in state and (not isinstance(state[LOG_ROOT_FIELD], str) or not state[LOG_ROOT_FIELD]):
        raise ValueError('invalid log root')
    for section in ('units', 'sessions'):
        if not isinstance(state.get(section), dict):
            raise ValueError('missing entry map')
        for entry in state[section].values():
            if not isinstance(entry, dict):
                raise ValueError('invalid entry')
            allowed = ('read', 'inactive', 'unreadable') if section == 'units' else ('written', 'skipped', 'withheld')
            if entry.get('state') not in allowed:
                raise ValueError('invalid entry state')
            for field in ('fingerprint', 'sessionKey', 'reason', 'projectDir'):
                if field in entry and not isinstance(entry[field], str):
                    raise ValueError('invalid ' + field)
            if section == 'sessions':
                if not isinstance(entry.get('logPath'), str) or not entry['logPath']:
                    raise ValueError('missing log path')
                _timestamp(entry.get('lastActivityAt'))
                if 'lastPromptAt' in entry:
                    _timestamp(entry['lastPromptAt'])
                if type(entry.get('orphan')) is not bool:
                    raise ValueError('invalid orphan flag')
                for field in ('renderVersion', 'unitCount'):
                    if type(entry.get(field)) is not int or not 0 <= entry[field] <= MAX_INT32:
                        raise ValueError('invalid ' + field)
                for field in ('promptCount', 'subAgentCount'):
                    if field in entry and (type(entry[field]) is not int or not 0 <= entry[field] <= MAX_INT32):
                        raise ValueError('invalid ' + field)
                if 'priceFileProblem' in entry and type(entry['priceFileProblem']) is not bool:
                    raise ValueError('invalid priceFileProblem')


@contextmanager
def _sync_lock(root):
    """OS lock released on process exit; never unlink the shared lock inode."""
    lock = root / '.sync.lock'
    require_no_link(lock)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | NO_FOLLOW, FILE_MODE)
    with os.fdopen(fd, 'r+b') as stream:
        if os.name == 'nt':
            import msvcrt
            if os.fstat(fd).st_size == 0:
                stream.write(b'\\0')
                stream.flush()
            stream.seek(0)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)


class FileStateStore:
    """Exclusive on flag (with the one-time `.enabled` conversion) and replace-based, key-merged sync state."""

    def __init__(self, root, now: Callable[[], datetime] = _utc_now):
        """`now` returns a timezone-aware datetime: the on-time of a migrated flag, and the time a temp's age is measured against."""
        self.root = Path(root)
        self._root_text = os.fspath(root)
        self.sync_reset = False
        self._baseline = _empty_state()
        self._swept = False
        self._now = now

    def migrate_enabled(self) -> None:
        """`.enabled` -> `.log_enabled`, once and for good. Only a regular file counts, not a directory, a FIFO or a link: that is
        what an old release created, and a link is never followed. The conversion is `write_on_flag`'s exclusive create (a
        `.log_enabled` that already exists, and its on-time, win), then the removal of `.enabled`, which another run may have
        done first. A failed creation leaves `.enabled` in place, so logging is never switched off; a failed removal raises with
        the new flag in place: an `.enabled` that stays keeps the old release's hooks logging beside the new sync."""
        legacy = self.root / '.enabled'
        try:
            if not S_ISREG(legacy.lstat().st_mode):
                return
        except FileNotFoundError:
            return
        self.write_on_flag(OnFlag(self._now()))
        legacy.unlink(missing_ok=True)

    def read_on_flag(self) -> Optional[OnFlag]:
        try:
            with (self.root / '.log_enabled').open(encoding='utf-8') as stream:
                stat = os.fstat(stream.fileno())
                try:
                    moment = datetime.fromisoformat(stream.read().strip().replace('Z', '+00:00'))
                    if moment.utcoffset() is None:
                        raise ValueError('missing timezone')
                    return OnFlag(moment)
                except (ValueError, UnicodeError):
                    return OnFlag(datetime.fromtimestamp(stat.st_mtime, timezone.utc), True)
        except FileNotFoundError:
            return None

    def write_on_flag(self, flag: OnFlag):
        on_at = flag.on_at
        if on_at.utcoffset() is None:
            raise ValueError('on-time must have a timezone')
        make_folders(self.root)
        flag_path = self.root / '.log_enabled'
        try:
            fd = os.open(flag_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
        except FileExistsError:
            if not flag_path.exists():
                require_no_link(flag_path)  # a dangling link is no flag, and nothing is written through it
            return
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(on_at.isoformat() + '\n')

    def delete_on_flag(self):
        try:
            (self.root / '.log_enabled').unlink()
        except FileNotFoundError:
            pass

    def _read_sync(self):
        try:
            with (self.root / '.sync.json').open(encoding='utf-8') as stream:
                state = json.load(stream)
            _validate_state(state)
            self._follow_moved_root(state)
            return state
        except FileNotFoundError:
            return _empty_state()
        except (ValueError, UnicodeError):
            self.sync_reset = True
            return _empty_state()

    def _follow_moved_root(self, state):
        """Takes the recorded log root out of `state`; when it is not this store's root any more, every log path recorded under it
        becomes the same path under this root. A path that was not recorded under it is left as it is."""
        recorded = state.pop(LOG_ROOT_FIELD, None)
        if recorded is None or _folded(recorded) == _folded(self._root_text):
            return
        old_prefix = os.path.normcase(os.path.join(recorded, ''))
        for entry in state['sessions'].values():
            path = entry['logPath']
            if os.path.normcase(path).startswith(old_prefix):
                entry['logPath'] = os.path.join(self._root_text, path[len(old_prefix):])

    def load_sync(self):
        state = self._read_sync()
        self._baseline = copy.deepcopy(state)
        return state

    def save_sync(self, state):
        _validate_state(state)
        make_folders(self.root)
        with _sync_lock(self.root):
            self._remove_stale_temps()
            self._save_locked(state)

    def _remove_stale_temps(self):
        """Once per store, which is once per run: a temp a killed save left behind is removed when it is stale; a younger one may be a save in progress."""
        if not self._swept:
            self._swept = True
            remove_stale_temps(self._root_text, _SYNC_TEMP, self._now().timestamp())

    def _save_locked(self, state):
        """`merged` is what the file holds now, updated with this run's entries; it is built in place, so a save costs one read, one
        pass over the entries and one write, however many entries there are."""
        merged = self._read_sync()
        for section in ('units', 'sessions'):
            entries, baseline = merged[section], self._baseline[section]
            for key in baseline.keys() - state[section].keys():
                if entries.get(key) == baseline[key]:  # dropped by this run, and no other run changed it meanwhile
                    del entries[key]
            for key, entry in state[section].items():
                current = entries.get(key)
                if section == 'units' and current != baseline.get(key) and current is not None:
                    if current.get('fingerprint') != entry.get('fingerprint'):
                        entry.pop('fingerprint', None)
                if section == 'sessions' and current is not None and 'logPath' in current:
                    entry['logPath'] = current['logPath']
                entries[key] = entry
        if 'lastSyncAt' in state:
            merged['lastSyncAt'] = state['lastSyncAt']
        data = _encode({**merged, LOG_ROOT_FIELD: self._root_text})
        fd, name = tempfile.mkstemp(prefix='.sync-', suffix='.tmp', dir=self.root)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
            os.replace(name, self.root / '.sync.json')
        finally:
            try:
                os.unlink(name)
            except FileNotFoundError:
                pass
        self._baseline = json.loads(data)  # an independent copy, much cheaper than `copy.deepcopy`
        del self._baseline[LOG_ROOT_FIELD]
        state.clear()
        state.update(merged)
