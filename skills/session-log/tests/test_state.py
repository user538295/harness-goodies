"""C6 persistence behavior against real temporary files."""
import json
import os
import multiprocessing
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from sessionlog.state import FileStateStore
from sessionlog.state_port import OnFlag


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = FileStateStore(self.root)

    def test_log_enabled_exclusive_create_keeps_existing_on_time(self):
        first = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.store.write_on_flag(OnFlag(first))
        self.store.write_on_flag(OnFlag(datetime(2026, 2, 1, tzinfo=timezone.utc)))
        self.assertEqual(self.store.read_on_flag().on_at, first)
        self.store.delete_on_flag()
        self.store.delete_on_flag()
        self.assertIsNone(self.store.read_on_flag())

    def test_unparsable_log_enabled_falls_back_to_file_time(self):
        flag = self.root / '.log_enabled'
        flag.write_text('broken')
        os.utime(flag, (1000, 1000))
        self.assertEqual(self.store.read_on_flag().on_at.timestamp(), 1000)
        self.assertTrue(self.store.read_on_flag().from_file_time)
        self.assertEqual(flag.read_text(), 'broken')

    def test_corrupt_sync_json_rebuilt_and_reported(self):
        for content in ('', '{', '{}', '{"units": [], "sessions": {}}',
                        '{"units": {}, "sessions": {"s": {}}}',
                        '{"units": {"u": {"state": "invalid"}}, "sessions": {}}'):
            with self.subTest(content=content):
                (self.root / '.sync.json').write_text(content)
                self.store = FileStateStore(self.root)
                state = self.store.load_sync()
                self.assertTrue(self.store.sync_reset)
                self.assertEqual(state, {'units': {}, 'sessions': {}})
                self.store.save_sync(state)
                self.assertEqual(json.loads((self.root / '.sync.json').read_text()), state)

    def test_merge_keeps_other_keys_invalidates_conflicts_and_preserves_path(self):
        initial = {'units': {'u': {'fingerprint': 'old', 'state': 'read'}},
                   'sessions': {'s': session_entry('first.md')}}
        self.store.save_sync(initial)
        stale = self.store.load_sync()
        other = FileStateStore(self.root)
        latest = other.load_sync()
        latest['units']['u']['fingerprint'] = 'new'
        latest['units']['v'] = {'state': 'unreadable', 'reason': 'busy'}
        other.save_sync(latest)
        stale['sessions']['s']['logPath'] = 'wrong.md'
        self.store.save_sync(stale)
        result = FileStateStore(self.root).load_sync()
        self.assertNotIn('fingerprint', result['units']['u'])
        self.assertEqual(result['units']['v']['reason'], 'busy')
        self.assertEqual(result['sessions']['s']['logPath'], 'first.md')

    def test_failed_replace_keeps_old_file_and_removes_temp(self):
        self.store.save_sync({'units': {}, 'sessions': {}})
        before = (self.root / '.sync.json').read_bytes()
        with patch('sessionlog.state.os.replace', side_effect=PermissionError('busy')):
            with self.assertRaises(PermissionError):
                self.store.save_sync({'units': {'u': {'state': 'read'}}, 'sessions': {}})
        self.assertEqual((self.root / '.sync.json').read_bytes(), before)
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_invalid_session_fields_are_rebuilt_and_reported(self):
        for field, value in (('lastActivityAt', 'not a time'),
                             ('lastPromptAt', '2026-01-01T00:00:00'),
                             ('projectDir', 12), ('orphan', 0),
                             ('renderVersion', True), ('unitCount', -1)):
            with self.subTest(field=field):
                entry = session_entry('session.md')
                entry[field] = value
                (self.root / '.sync.json').write_text(json.dumps({'units': {}, 'sessions': {'s': entry}}))
                store = FileStateStore(self.root)
                self.assertEqual(store.load_sync(), {'units': {}, 'sessions': {}})
                self.assertTrue(store.sync_reset)

    def test_process_saves_do_not_lose_entries(self):
        context = multiprocessing.get_context('spawn')
        ready = context.Barrier(4)
        processes = [context.Process(target=save_process, args=(str(self.root), str(i), ready))
                     for i in range(4)]
        try:
            for process in processes:
                process.start()
            for process in processes:
                process.join(timeout=10)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(self.store.load_sync()['sessions'],
                             {str(i): session_entry(str(i) + '.md') for i in range(4)})
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join()

    def test_session_integer_metadata_obeys_int32_bounds(self):
        for field in ('renderVersion', 'unitCount'):
            entry = session_entry('session.md')
            entry[field] = 2147483647
            state = {'units': {}, 'sessions': {'s': entry}}
            self.store.save_sync(state)
            self.assertEqual(self.store.load_sync()['sessions']['s'][field], 2147483647)
            entry[field] = 2147483648
            (self.root / '.sync.json').write_text(json.dumps({'units': {}, 'sessions': {'s': entry}}))
            fresh = FileStateStore(self.root)
            self.assertEqual(fresh.load_sync(), {'units': {}, 'sessions': {}})
            self.assertTrue(fresh.sync_reset)

    def test_simultaneous_saves_keep_both_sessions(self):
        barrier = threading.Barrier(2)

        def save(key):
            store = FileStateStore(self.root)
            state = store.load_sync()
            state['sessions'][key] = session_entry(key + '.md')
            barrier.wait(timeout=5)
            store.save_sync(state)

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(save, ('one', 'two')))
        self.assertEqual(self.store.load_sync()['sessions'],
                         {key: session_entry(key + '.md') for key in ('one', 'two')})


def session_entry(path):
    return {'logPath': path, 'state': 'written', 'projectDir': '/project',
            'lastPromptAt': '2026-01-01T00:00:00+00:00',
            'lastActivityAt': '2026-01-01T00:00:01+00:00',
            'orphan': False, 'renderVersion': 1, 'unitCount': 2}


def save_process(root, key, ready):
    import time

    class SlowStore(FileStateStore):
        def _read_sync(self):
            state = super()._read_sync()
            time.sleep(0.1)
            return state

    store = SlowStore(root)
    state = store.load_sync()
    state['sessions'][key] = session_entry(key + '.md')
    ready.wait(timeout=5)
    store.save_sync(state)
