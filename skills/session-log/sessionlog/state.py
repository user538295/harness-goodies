"""C6 filesystem adapter. Callers supply a checked prompt-logs root.

Sync documents use the contract's JSON field names. Each store tracks the last loaded snapshot to detect competing writes; create one store per sync run.
The persistent .sync.lock serializes re-read/merge/replace across processes and is released by the OS on exit. sync_reset stays true for the run after rebuilding corrupt state.
Legacy-flag migration belongs to the separate command/hook migration task. Flag fallback is reported by OnFlag.from_file_time; reading never repairs the flag.
"""
import copy
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .state_port import OnFlag

MAX_INT32 = 2**31 - 1


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
    for section in ('units', 'sessions'):
        if not isinstance(state.get(section), dict):
            raise ValueError('missing entry map')
        for entry in state[section].values():
            if not isinstance(entry, dict):
                raise ValueError('invalid entry')
            allowed = ('read', 'inactive', 'unreadable') if section == 'units' else ('written', 'skipped')
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


@contextmanager
def _sync_lock(root):
    """OS lock released on process exit; never unlink the shared lock inode."""
    fd = os.open(root / '.sync.lock', os.O_RDWR | os.O_CREAT, 0o600)
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
    """Exclusive on flag and replace-based, key-merged sync state."""

    def __init__(self, root):
        self.root = Path(root)
        self.sync_reset = False
        self._baseline = _empty_state()

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
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            fd = os.open(self.root / '.log_enabled', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
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
            return state
        except FileNotFoundError:
            return _empty_state()
        except (ValueError, UnicodeError):
            self.sync_reset = True
            return _empty_state()

    def load_sync(self):
        state = self._read_sync()
        self._baseline = copy.deepcopy(state)
        return state

    def save_sync(self, state):
        _validate_state(state)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with _sync_lock(self.root):
            self._save_locked(state)

    def _save_locked(self, state):
        latest = self._read_sync()
        merged = copy.deepcopy(latest)
        for section in ('units', 'sessions'):
            for key, entry in state[section].items():
                result = copy.deepcopy(entry)
                current = latest[section].get(key)
                baseline = self._baseline[section].get(key)
                if section == 'units' and current != baseline and current is not None:
                    if current.get('fingerprint') != entry.get('fingerprint'):
                        result.pop('fingerprint', None)
                if section == 'sessions' and current is not None and 'logPath' in current:
                    result['logPath'] = current['logPath']
                merged[section][key] = result
        if 'lastSyncAt' in state:
            merged['lastSyncAt'] = state['lastSyncAt']
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(prefix='.sync-', suffix='.tmp', dir=self.root)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
                json.dump(merged, stream, ensure_ascii=False, allow_nan=False)
                stream.write('\n')
            os.replace(name, self.root / '.sync.json')
        finally:
            try:
                os.unlink(name)
            except FileNotFoundError:
                pass
        self._baseline = copy.deepcopy(merged)
        state.clear()
        state.update(merged)
