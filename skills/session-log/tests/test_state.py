"""C6 persistence behavior against real temporary files."""
import json
import os
import multiprocessing
from stat import S_IMODE, S_ISREG
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from sessionlog.safe_paths import LINK_REASON
from sessionlog.state import FileStateStore
from sessionlog.state_port import OnFlag

CONCURRENT_ROUNDS = 50
RACE_WAIT_SECONDS = 5
RESULT_WAIT_SECONDS = 10
MOMENT = datetime(2026, 3, 4, tzinfo=timezone.utc)
EARLIER_MOMENT = datetime(2026, 1, 1, tzinfo=timezone.utc)
LATER_MOMENT = datetime(2026, 2, 1, tzinfo=timezone.utc)


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

    @unittest.skipIf(os.name == "nt", "POSIX file modes")
    def test_new_root_and_its_new_parents_are_0700_under_umask_022_and_state_files_0600(self):
        self.addCleanup(os.umask, os.umask(0o022))
        root = self.root / 'new-parent' / 'prompt-logs'
        store = FileStateStore(root)
        store.write_on_flag(OnFlag(datetime(2026, 1, 1, tzinfo=timezone.utc)))
        store.save_sync(store.load_sync())
        modes = {path: S_IMODE(path.stat().st_mode) for path in [root.parent, root, *root.iterdir()]}
        self.assertEqual(['.log_enabled', '.sync.json', '.sync.lock'], sorted(path.name for path in root.iterdir()))
        self.assertTrue(all(mode == (0o700 if path.is_dir() else 0o600) for path, mode in modes.items()), modes)

    @unittest.skipUnless(hasattr(os, "symlink") and os.name != "nt", "needs symlinks")
    def test_symlinked_sync_lock_is_refused_naming_it_and_nothing_is_created_through_it(self):
        target = self.root / 'elsewhere.lock'
        lock = self.root / '.sync.lock'
        lock.symlink_to(target)
        with self.assertRaises(OSError) as raised:
            self.store.save_sync(self.store.load_sync())
        self.assertEqual('%s %s' % (lock, LINK_REASON), str(raised.exception))
        self.assertFalse(target.exists())

    @unittest.skipUnless(hasattr(os, "symlink") and os.name != "nt", "needs symlinks")
    def test_dangling_log_enabled_link_is_refused_and_a_valid_one_is_a_flag_read_through(self):
        target = self.root / 'elsewhere.flag'
        flag = self.root / '.log_enabled'
        flag.symlink_to(target)
        with self.assertRaises(OSError) as raised:
            self.store.write_on_flag(OnFlag(MOMENT))
        self.assertEqual('%s %s' % (flag, LINK_REASON), str(raised.exception))
        self.assertFalse(target.exists())
        flag.unlink()
        target.write_text(MOMENT.isoformat() + '\n')
        flag.symlink_to(target)
        self.store.write_on_flag(OnFlag(LATER_MOMENT))
        self.assertEqual(self.store.read_on_flag().on_at, MOMENT)
        self.assertEqual(target.read_text(), MOMENT.isoformat() + '\n')

    @unittest.skipUnless(hasattr(os, "symlink") and os.name != "nt", "needs symlinks")
    def test_dangling_log_enabled_link_keeps_the_old_enabled_flag_so_logging_is_not_switched_off(self):
        (self.root / '.log_enabled').symlink_to(self.root / 'elsewhere.flag')
        (self.root / '.enabled').write_text('old\n')
        with self.assertRaises(OSError):
            self.store.migrate_enabled()
        self.assertTrue((self.root / '.enabled').exists())

    @unittest.skipUnless(hasattr(os, "symlink") and os.name != "nt", "needs symlinks")
    def test_symlinked_sync_json_is_replaced_by_a_regular_file_and_its_target_is_left_alone(self):
        target = self.root / 'elsewhere.json'
        target.write_text('{"units": {}, "sessions": {}}\n')
        (self.root / '.sync.json').symlink_to(target)
        self.store.save_sync(self.store.load_sync())
        self.assertTrue(S_ISREG((self.root / '.sync.json').lstat().st_mode))
        self.assertEqual(target.read_text(), '{"units": {}, "sessions": {}}\n')

    def test_unparsable_log_enabled_falls_back_to_file_time(self):
        flag = self.root / '.log_enabled'
        flag.write_text('broken')
        os.utime(flag, (1000, 1000))
        self.assertEqual(self.store.read_on_flag().on_at.timestamp(), 1000)
        self.assertTrue(self.store.read_on_flag().from_file_time)
        self.assertEqual(flag.read_text(), 'broken')

    def test_migrate_enabled_creates_log_enabled_with_the_injected_time_then_removes_enabled(self):
        (self.root / '.enabled').write_text('old flag')
        FileStateStore(self.root, lambda: MOMENT).migrate_enabled()
        self.assertFalse((self.root / '.enabled').exists())
        self.assertEqual((self.root / '.log_enabled').read_text(), MOMENT.isoformat() + '\n')
        self.assertEqual(self.store.read_on_flag(), OnFlag(MOMENT))
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ['.log_enabled'])

    def test_migrate_enabled_keeps_an_existing_log_enabled_byte_for_byte_and_still_removes_enabled(self):
        for content in ('2020-01-01T00:00:00+00:00\n', 'broken'):
            with self.subTest(content=content):
                (self.root / '.log_enabled').write_text(content)
                (self.root / '.enabled').write_text('old flag')
                FileStateStore(self.root, lambda: MOMENT).migrate_enabled()
                self.assertEqual((self.root / '.log_enabled').read_text(), content)
                self.assertFalse((self.root / '.enabled').exists())

    def test_migrate_enabled_without_enabled_writes_nothing_and_never_asks_the_clock(self):
        def clock():
            raise AssertionError('the clock is for a migration that happens')

        missing = self.root / 'not-created'
        FileStateStore(missing, clock).migrate_enabled()
        self.assertFalse(missing.exists())
        self.store.write_on_flag(OnFlag(EARLIER_MOMENT))
        before = (self.root / '.log_enabled').stat()
        FileStateStore(self.root, clock).migrate_enabled()
        after = (self.root / '.log_enabled').stat()
        self.assertEqual((before.st_mtime_ns, before.st_size), (after.st_mtime_ns, after.st_size))
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ['.log_enabled'])

    def test_migrate_enabled_tolerates_enabled_removed_by_another_run_meanwhile(self):
        (self.root / '.enabled').write_text('old flag')

        def clock():  # another run removes `.enabled` after this run saw it and before this run removes it
            (self.root / '.enabled').unlink()
            return MOMENT

        FileStateStore(self.root, clock).migrate_enabled()
        self.assertEqual(self.store.read_on_flag(), OnFlag(MOMENT))

    def test_migrate_enabled_keeps_the_log_enabled_another_run_created_meanwhile(self):
        (self.root / '.enabled').write_text('old flag')

        def clock():  # another run converts after this run saw `.enabled`: its flag exists before this run creates its own
            FileStateStore(self.root, lambda: EARLIER_MOMENT).migrate_enabled()
            return MOMENT

        FileStateStore(self.root, clock).migrate_enabled()
        self.assertEqual(self.store.read_on_flag(), OnFlag(EARLIER_MOMENT))
        self.assertEqual((self.root / '.log_enabled').read_text(), EARLIER_MOMENT.isoformat() + '\n')
        self.assertFalse((self.root / '.enabled').exists())

    def test_migrate_enabled_raises_when_enabled_stays_but_the_log_enabled_is_created_first(self):
        (self.root / '.enabled').write_text('old flag')
        with patch.object(Path, 'unlink', side_effect=PermissionError('denied')):
            with self.assertRaises(PermissionError):
                FileStateStore(self.root, lambda: MOMENT).migrate_enabled()
        self.assertEqual(self.store.read_on_flag(), OnFlag(MOMENT))  # the old flag is only ever removed after the new one exists
        self.assertTrue((self.root / '.enabled').exists())

    def test_migrate_enabled_keeps_enabled_when_the_log_enabled_cannot_be_created(self):
        (self.root / '.enabled').write_text('old flag')
        with patch.object(FileStateStore, 'write_on_flag', side_effect=PermissionError('denied')):
            with self.assertRaises(PermissionError):
                FileStateStore(self.root, lambda: MOMENT).migrate_enabled()
        self.assertTrue((self.root / '.enabled').exists())  # logging is never switched off by a failed conversion
        self.assertIsNone(self.store.read_on_flag())

    def test_migrate_enabled_ignores_an_enabled_that_is_not_a_regular_file(self):
        """A directory, a FIFO or a link is not what an old release created: nothing is converted, followed or removed. The link
        and FIFO kinds need POSIX."""
        def clock():
            raise AssertionError('nothing to convert')

        legacy = self.root / '.enabled'
        target = self.root / 'target'
        target.write_text('old flag')
        makers = {'directory': legacy.mkdir}
        if os.name == 'posix':
            makers['link to a file'] = lambda: legacy.symlink_to(target)
            makers['dangling link'] = lambda: legacy.symlink_to('no-such-target')
            makers['FIFO'] = lambda: os.mkfifo(legacy)
        for kind, make in makers.items():
            with self.subTest(kind=kind):
                make()
                try:
                    FileStateStore(self.root, clock).migrate_enabled()
                    self.assertTrue(os.path.lexists(legacy))
                    self.assertIsNone(self.store.read_on_flag())
                    self.assertEqual(target.read_text(), 'old flag')
                finally:
                    if legacy.is_dir() and not legacy.is_symlink():
                        legacy.rmdir()
                    else:
                        legacy.unlink()

    def test_migrate_enabled_without_an_injected_clock_uses_the_current_time(self):
        (self.root / '.enabled').write_text('old flag')
        before = datetime.now(timezone.utc)
        FileStateStore(self.root).migrate_enabled()
        after = datetime.now(timezone.utc)
        flag = self.store.read_on_flag()
        self.assertFalse(flag.from_file_time)
        self.assertTrue(before <= flag.on_at <= after, flag)

    def test_concurrent_migrations_create_one_log_enabled_and_none_fails(self):
        barrier = threading.Barrier(2)

        def clock(moment):
            def now():  # both runs have seen `.enabled` before either one creates `.log_enabled`
                barrier.wait(timeout=RACE_WAIT_SECONDS)
                return moment
            return now

        def migrate(moment):
            FileStateStore(self.root, clock(moment)).migrate_enabled()

        with patch.object(FileStateStore, 'write_on_flag', autospec=True, side_effect=FileStateStore.write_on_flag) as create, \
                ThreadPoolExecutor(max_workers=2) as pool:
            for _ in range(CONCURRENT_ROUNDS):
                (self.root / '.log_enabled').unlink(missing_ok=True)
                (self.root / '.enabled').write_text('old flag')
                for result in [pool.submit(migrate, moment) for moment in (EARLIER_MOMENT, LATER_MOMENT)]:
                    result.result(timeout=RESULT_WAIT_SECONDS)  # raises what the run raised
                self.assertFalse((self.root / '.enabled').exists())
                self.assertIn((self.root / '.log_enabled').read_text(),
                              (EARLIER_MOMENT.isoformat() + '\n', LATER_MOMENT.isoformat() + '\n'))
                self.assertEqual(sorted(path.name for path in self.root.iterdir()), ['.log_enabled'])
        self.assertEqual(2 * CONCURRENT_ROUNDS, create.call_count)  # in every round both runs tried to create the flag

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
