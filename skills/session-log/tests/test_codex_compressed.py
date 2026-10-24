"""Compressed Codex rollouts (BE-26; S9, S47): a `.jsonl.zst` rollout is read with `compression.zstd` like a plain one, a corrupt one is
listed unreadable without harming the others, and compression keeps the unit key and fingerprint so a compressed rollout is not
learned or read again. Every `.zst` fixture is generated here from text; none is committed."""
import json
import os
import shutil
import tempfile
import unittest
from collections import Counter
from compression import zstd
from datetime import datetime, timedelta, timezone
from unittest import mock

from sessionlog.codex_reader import PARENT_UNREADABLE_REASON, CodexReader
from sessionlog.model import Harness
from sessionlog.reader_port import CANNOT_BE_DECODED_REASON, CANNOT_BE_OPENED_REASON, UnreadableSource
from sessionlog.sync import UNKNOWN_SESSION_REASON
from tests.store_fakes import compress_in_place, write_compressed
from tests.test_codex_entry import CodexEntryTestCase
from tests.test_codex_reader import CodexReaderTestCase
from tests.test_codex_subagents import CHILD, GRAND, MISSING, ONE_SECOND, ONE_SECOND_NS, ROOT, SECOND, _name, _prompt, _rollout, _used

FILE_TIME_NS = 5 * ONE_SECOND_NS
MANY_RECORDS = 3000  # over half a megabyte of text: more than one zstd block and more than one decoded chunk


def _big_rollout() -> str:
    """A root rollout with non-ASCII prompts and a body long enough to span several compression blocks."""
    records = [_prompt(10, "árvíztűrő tükörfúrógép ask"), _prompt(11, "日本語 ask")]
    records += [_used(12 + number, 100 + number, 10) for number in range(MANY_RECORDS)]
    return _rollout(ROOT, records=records)


def _units_of(reader):
    return {unit.key: unit for unit in reader.scan().units}


class CompressedReadTest(CodexReaderTestCase):
    def _store(self, files, compressed=()):
        """A fresh Codex home holding the rollouts `files` (name -> text), those named in `compressed` as `.zst`, all with one file
        time; returns its reader."""
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, True)
        folder = os.path.join(home, "sessions", "2026", "09", "11")
        os.makedirs(folder)
        for name, text in files.items():
            path = os.path.join(folder, name)
            if name in compressed:
                write_compressed(path + ".zst", text, FILE_TIME_NS)
            else:
                with open(path, "w", encoding="utf-8", newline="") as handle:
                    handle.write(text)
                os.utime(path, ns=(FILE_TIME_NS, FILE_TIME_NS))
        return CodexReader(home)

    def test_zst_rollout_read_like_plain_and_corrupt_zst_listed_unreadable(self):
        files = {_name(ROOT): _big_rollout()}
        results = []
        for compressed in ((), tuple(files)):
            reader = self._store(files, compressed)
            (unit,) = reader.scan().units
            key = reader.session_of(unit)
            results.append((os.path.basename(unit.key), unit.fingerprint, key, reader.read(key, (unit,))))
        plain, zst = results
        self.assertEqual("codex:" + ROOT, zst[2])
        self.assertEqual(plain, zst)  # the key (name without .zst), the fingerprint, the session and the problems are the same
        self.assertEqual(("árvíztűrő tükörfúrógép ask", "日本語 ask"), tuple(prompt.text for prompt in zst[3].session.prompts))
        self.assertEqual(MANY_RECORDS, sum(len(prompt.requests) for prompt in zst[3].session.prompts))

        good_text = _rollout(CHILD, root=CHILD, records=[_prompt(10, "child ask")])
        garbage = self._write_bytes("rollout-2026-09-11T10-00-00-bad.jsonl.zst", b"this is not zstandard data at all")
        truncated = self._write_bytes("rollout-2026-09-11T10-00-00-cut.jsonl.zst", zstd.compress(_big_rollout().encode("utf-8"))[:40])
        broken_body = self._write_bytes("rollout-2026-09-11T10-00-00-body.jsonl.zst",
                                        zstd.compress(good_text.encode("utf-8")) + b"garbage after the first frame")
        good = os.path.join(self.day, _name(CHILD) + ".zst")
        write_compressed(good, good_text)
        units = _units_of(self.reader)
        for corrupt in (garbage, truncated, broken_body):
            with self.subTest(corrupt=os.path.basename(corrupt)):
                key = corrupt[: -len(".zst")]
                result = self.reader.read("codex:" + CHILD, (units[key],))
                self.assertEqual((None, (UnreadableSource(Harness.CODEX, key, CANNOT_BE_DECODED_REASON),)), (result.session, result.unreadable))
        self.assertIsNone(self.reader.session_of(units[garbage[: -len(".zst")]]))
        self.assertIsNone(self.reader.session_of(units[truncated[: -len(".zst")]]))
        good_unit = units[good[: -len(".zst")]]  # the corrupt ones leave the other rollouts alone
        self.assertEqual("codex:" + CHILD, self.reader.session_of(good_unit))
        self.assertEqual(("child ask",), tuple(prompt.text for prompt in self.reader.read("codex:" + CHILD, (good_unit,)).session.prompts))

    def test_a_rollout_compressed_after_the_scan_is_read_through_its_unit(self):
        path = self._write(_name(ROOT), _rollout(ROOT, records=[_prompt(10, "ask root"), _used(11, 7, 1)]))
        units = self.reader.scan().units
        compress_in_place(path)  # the unit was listed as the plain file; Codex compressed it before `read`
        result = self.reader.read("codex:" + ROOT, units)
        self.assertEqual((("ask root",), 1, ()), (tuple(prompt.text for prompt in result.session.prompts), sum(len(prompt.requests) for prompt in result.session.prompts), result.unreadable))

    def test_a_plain_entry_that_is_no_regular_file_beside_a_zst_rollout_is_never_opened(self):
        os.mkdir(os.path.join(self.day, _name(ROOT)))  # a folder named like the rollout: the scan skips it, the `.zst` is the unit
        write_compressed(os.path.join(self.day, _name(ROOT) + ".zst"), _rollout(ROOT, records=[_prompt(10, "ask root")]))
        (unit,) = self.reader.scan().units
        result = self.reader.read("codex:" + ROOT, (unit,))
        self.assertEqual((("ask root",), ()), (tuple(prompt.text for prompt in result.session.prompts), result.unreadable))

    def test_the_plain_file_is_read_when_a_rollout_exists_plain_and_compressed(self):
        self._write(_name(ROOT), _rollout(ROOT, records=[_prompt(10, "plain ask")]))
        self._write_bytes(_name(ROOT) + ".zst", b"a compressed copy that is not even zstd data")
        (unit,) = self.reader.scan().units
        result = self.reader.read(self.reader.session_of(unit), (unit,))
        self.assertEqual((("plain ask",), ()), (tuple(prompt.text for prompt in result.session.prompts), result.unreadable))

    def test_a_python_without_compression_zstd_lists_a_compressed_rollout_not_openable_and_still_reads_plain_ones(self):
        zst = os.path.join(self.day, _name(ROOT) + ".zst")
        write_compressed(zst, _rollout(ROOT, records=[_prompt(10, "zipped ask")]))
        plain = self._write(_name(CHILD), _rollout(CHILD, root=CHILD, records=[_prompt(10, "plain ask")]))
        units = _units_of(self.reader)
        with mock.patch("sessionlog.codex_reader.zstd", None):
            self.assertIsNone(self.reader.session_of(units[zst[: -len(".zst")]]))
            zipped = self.reader.read("codex:" + ROOT, (units[zst[: -len(".zst")]],))
            self.assertEqual((None, (UnreadableSource(Harness.CODEX, zst[: -len(".zst")], CANNOT_BE_OPENED_REASON),)), (zipped.session, zipped.unreadable))
            self.assertEqual("codex:" + CHILD, self.reader.session_of(units[plain]))

    def test_read_opens_a_compressed_unit_once_with_zstd_and_never_as_a_plain_file(self):
        write_compressed(os.path.join(self.day, _name(ROOT) + ".zst"), _rollout(ROOT, records=[_prompt(10, "ask root")]))
        self._write(_name(CHILD), _rollout(CHILD, parent=ROOT, start=20, records=[_used(21, 3, 1)]))
        units = self.reader.scan().units
        zstd_opened, plain_opened = [], []
        real_zstd_open, real_open = zstd.open, open

        def counting_zstd(name, *args, **kwargs):
            zstd_opened.append(name)
            return real_zstd_open(name, *args, **kwargs)

        def counting_open(name, *args, **kwargs):
            plain_opened.append(name)
            return real_open(name, *args, **kwargs)

        with mock.patch.object(zstd, "open", counting_zstd), mock.patch("sessionlog.store_files.open", counting_open, create=True):
            result = self.reader.read("codex:" + ROOT, units)
        self.assertEqual((ROOT, ()), (result.session.session_id, result.unreadable))
        self.assertEqual(([os.path.join(self.day, _name(ROOT) + ".zst")], [os.path.join(self.day, _name(CHILD))]), (zstd_opened, plain_opened))

    def test_compressed_threads_make_the_same_session_as_plain_ones_with_a_plain_grandchild_nested_under_a_compressed_middle_thread(self):
        family = {_name(ROOT): _rollout(ROOT, records=[_prompt(10, "ask root"), _used(11, 100, 10)]),
                  _name(CHILD): _rollout(CHILD, parent=ROOT, start=20, records=[_used(21, 3, 1)]),
                  _name(SECOND): _rollout(SECOND, parent=ROOT, start=25, records=[_used(26, 2, 1)]),
                  _name(GRAND): _rollout(GRAND, parent=CHILD, start=30, records=[_used(31, 4, 1)])}
        plain_reader = self._store(family)
        plain_units = tuple(plain_reader.scan().units)
        expected = plain_reader.read("codex:" + ROOT, plain_units)
        # root, child and second are compressed, the grandchild stays plain
        mixed = self._store(family, compressed=[name for name in family if name != _name(GRAND)])
        units = tuple(mixed.scan().units)
        self.assertEqual({"codex:" + ROOT}, {mixed.session_of(unit) for unit in units})
        result = mixed.read("codex:" + ROOT, units)
        self.assertEqual(((), expected.session), (result.unreadable, result.session))
        self.assertEqual([CHILD, SECOND], [agent.agent_id for agent in result.session.sub_agents])
        self.assertEqual([[GRAND], []], [[grand.agent_id for grand in agent.sub_agents] for agent in result.session.sub_agents])

    def test_a_family_with_a_readable_compressed_middle_thread_and_no_root_rollout_is_an_orphan_with_the_grandchild_under_it(self):
        write_compressed(os.path.join(self.day, _name(CHILD) + ".zst"), _rollout(CHILD, parent=MISSING, start=20, records=[_prompt(21, "middle ask")]))
        self._write(_name(GRAND), _rollout(GRAND, parent=CHILD, start=30, records=[_used(31, 4, 1)]))
        units = self.reader.scan().units
        result = self.reader.read("codex:" + ROOT, units)
        self.assertEqual(((), MISSING, ("middle ask",), [GRAND]),
                         (result.unreadable, result.session.orphan_of_parent_id, tuple(prompt.text for prompt in result.session.prompts),
                          [agent.agent_id for agent in result.session.sub_agents]))

    def test_a_family_whose_parent_rollout_is_a_corrupt_zst_is_listed_unreadable_not_archived_as_orphans(self):
        self._write_bytes(_name(ROOT) + ".zst", b"not zstd")
        child = self._write(_name(CHILD), _rollout(CHILD, parent=ROOT, start=20, records=[_prompt(21, "child ask")]))
        reader = CodexReader(self.root)
        (child_unit,) = [unit for unit in reader.scan().units if unit.key == child]
        result = reader.read("codex:" + ROOT, (child_unit,))
        self.assertEqual((None, (UnreadableSource(Harness.CODEX, child, PARENT_UNREADABLE_REASON),)), (result.session, result.unreadable))


class CompressedSyncTest(CodexEntryTestCase):
    """Sync end to end over compressed rollouts (S9, S47); the clock is before every record, so every rollout is active since `on`."""

    NOW = datetime(2026, 3, 1, tzinfo=timezone.utc)
    ACTIVE = datetime(2026, 9, 12, 8, 0, 0, tzinfo=timezone.utc)

    def setUp(self):
        super().setUp()
        self.sessions = os.path.join(self.codex, "sessions", "2026", "08", "20")
        os.makedirs(self.sessions)
        self.zstd_opened = Counter()
        real_open = zstd.open

        def counting(name, *args, **kwargs):
            self.zstd_opened[str(name)] += 1
            return real_open(name, *args, **kwargs)

        patcher = mock.patch.object(zstd, "open", counting)
        patcher.start()
        self.addCleanup(patcher.stop)

    @property
    def active_ns(self):
        return int(self.ACTIVE.timestamp()) * ONE_SECOND_NS

    def install(self, thread, text):
        """Writes a plain rollout whose modification time is ACTIVE."""
        path = os.path.join(self.sessions, _name(thread))
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.utime(path, ns=(0, self.active_ns))
        return path

    def install_compressed(self, thread, text):
        path = os.path.join(self.sessions, _name(thread) + ".zst")
        write_compressed(path, text, self.active_ns)
        return path

    def archived_text(self, session_id, suffix):
        (path,) = [path for path in self.archived_files(suffix) if session_id in os.path.basename(path)]
        return self.read(path)

    def snapshot(self):
        return {os.path.relpath(path, self.log_root): self.read(path) for path in self.archived_files(".md") + self.archived_files(".usage.txt")}

    def state(self):
        with open(os.path.join(self.log_root, ".sync.json"), encoding="utf-8") as handle:
            return json.load(handle)

    def rollouts(self):
        """A root rollout and its child."""
        return (_rollout(ROOT, start=self.ACTIVE, records=[_prompt(self.ACTIVE + ONE_SECOND, "zipped ask"), _used(self.ACTIVE + 2 * ONE_SECOND, 20, 2)]),
                _rollout(CHILD, root=ROOT, parent=ROOT, start=self.ACTIVE + 5 * ONE_SECOND, records=[_used(self.ACTIVE + 6 * ONE_SECOND, 700, 70)]))

    def test_compressed_rollout_keeps_key_and_fingerprint_and_no_zst_file_opened_twice(self):
        """"Twice" is a second sync opening a `.zst` of a known unit: a new file is opened once to learn its session and once to read it."""
        root_text, child_text = self.rollouts()
        root_path, child_path = self.install(ROOT, root_text), self.install(CHILD, child_text)
        self.assertEqual(0, self.run_main(["codex", "on"])[0])
        before_files, before_state = self.snapshot(), self.state()
        self.assertEqual({}, dict(self.zstd_opened))  # nothing compressed yet: the counter below counts only what follows

        # Codex compresses both rollouts after the sync, keeping their file times
        compress_in_place(root_path)
        compress_in_place(child_path)
        later = self.ACTIVE + timedelta(hours=2)
        self.assertEqual(0, self.run_main(["codex", "status"], now=later)[0])

        self.assertEqual(later.isoformat(), self.state()["lastSyncAt"])  # the second sync did run
        self.assertEqual({}, dict(self.zstd_opened))  # a known unit is neither learned nor read again
        self.assertEqual(before_files, self.snapshot())
        self.assertEqual(before_state["units"], self.state()["units"])  # the same keys and fingerprints
        self.assertEqual([root_path, child_path], sorted(before_state["units"]))

        # a negative control: touching the compressed root changes its fingerprint, so its session is read again (never re-learned)
        os.utime(root_path + ".zst", ns=(0, self.active_ns + ONE_SECOND_NS))
        self.assertEqual(0, self.run_main(["codex", "status"], now=later + timedelta(hours=1))[0])
        self.assertEqual({root_path + ".zst": 1, child_path + ".zst": 1}, dict(self.zstd_opened))

    def test_a_new_compressed_rollout_is_learned_once_and_read_once_and_never_again(self):
        root_text, child_text = self.rollouts()
        root, child = self.install_compressed(ROOT, root_text), self.install_compressed(CHILD, child_text)
        self.assertEqual(0, self.run_main(["codex", "on"])[0])
        self.assertIn("zipped ask", self.archived_text(ROOT, ".md"))
        self.assertIn("sub-agent: unknown (%s)" % CHILD, self.archived_text(ROOT, ".usage.txt"))
        self.assertEqual({root: 2, child: 2}, dict(self.zstd_opened))  # once to learn the session, once to read it
        files, opened = self.snapshot(), dict(self.zstd_opened)
        self.assertEqual(0, self.run_main(["codex", "status"], now=self.ACTIVE + timedelta(hours=3))[0])
        self.assertEqual((files, opened), (self.snapshot(), dict(self.zstd_opened)))

    def test_corrupt_zst_is_listed_unreadable_in_status_and_the_other_rollouts_are_archived(self):
        root_text, _ = self.rollouts()
        self.install_compressed(ROOT, root_text)
        plain_id = "01a00000-0000-7000-8000-0000000000a1"
        self.install(plain_id, _rollout(plain_id, root=plain_id, start=self.ACTIVE, records=[_prompt(self.ACTIVE + ONE_SECOND, "plain ask")]))
        bad = os.path.join(self.sessions, _name(SECOND) + ".zst")
        with open(bad, "wb") as handle:
            handle.write(b"definitely not zstd")
        os.utime(bad, ns=(0, self.active_ns))
        self.assertEqual(0, self.run_main(["codex", "on"])[0])
        self.assertIn("zipped ask", self.archived_text(ROOT, ".md"))
        self.assertIn("plain ask", self.archived_text(plain_id, ".md"))
        code, stdout, stderr = self.run_main(["codex", "status"])
        self.assertEqual((0, ""), (code, stderr))
        self.assertIn("Archived sessions: 2", stdout)
        self.assertIn("  %s: %s\n" % (bad[: -len(".zst")], UNKNOWN_SESSION_REASON), stdout)

    def test_a_zst_with_a_good_first_frame_and_a_corrupt_body_is_listed_as_not_decodable_and_tried_again_each_sync(self):
        root_text, _ = self.rollouts()
        bad = os.path.join(self.sessions, _name(ROOT) + ".zst")
        with open(bad, "wb") as handle:
            handle.write(zstd.compress(root_text.encode("utf-8")) + b"garbage after the first frame")
        os.utime(bad, ns=(0, self.active_ns))
        self.assertEqual(0, self.run_main(["codex", "on"])[0])
        self.assertEqual([], self.archived_files(".md"))
        code, stdout, stderr = self.run_main(["codex", "status"])
        self.assertEqual((0, ""), (code, stderr))
        self.assertIn("  %s: %s\n" % (bad[: -len(".zst")], CANNOT_BE_DECODED_REASON), stdout)
        opened = self.zstd_opened[bad]
        self.assertEqual(0, self.run_main(["codex", "status"], now=self.ACTIVE + timedelta(hours=1))[0])
        self.assertGreater(self.zstd_opened[bad], opened)


if __name__ == "__main__":
    unittest.main()
