"""OMP adapter of the reader port (S10, S42, S47): sessions under `sessions/` and gzipped ones under `archive/sessions/`, the root id from
the file stem, children and grandchildren by the folders of their parent, the link of a child to its parent by file stem in either
folder, orphans, parent cycles and the files that cannot be read. The rules of the transcript are tested in `test_omp_transcript`, those
of the family in `test_omp_family` and the path rules in `test_omp_layout`."""
import gzip
import os
import shutil
import tempfile
import unittest
from datetime import timedelta
from unittest import mock

from sessionlog.model import Harness
from sessionlog.omp_reader import PARENT_CYCLE_REASON, PARENT_UNREADABLE_REASON, OmpReader
from sessionlog.reader_port import (CANNOT_BE_DECODED_REASON, CANNOT_BE_LISTED_REASON, CANNOT_BE_OPENED_REASON,
                                    CANNOT_BE_PARSED_REASON, UNSAFE_ID_REASON, SourceUnit, UnreadableSource)
from tests.omp_records import (CHILD_FIXTURE, CWD, FIXTURE_CHILD_ID, FIXTURE_ROOT_ID, FIXTURE_ROOT_STEM, PROJECT, ROOT_FIXTURE, assistant_record,
                               at, custom_record, effort_record, header_record, init_record, jsonl, read_text, root_of, usage_of,
                               user_record)
from tests.store_fakes import FakeEntry, OmpOpenCounter, scandir_wrapping, write_gzip


class OmpReaderTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        self.sessions = os.path.join(self.root, "sessions", PROJECT)
        self.archive = os.path.join(self.root, "archive", "sessions", PROJECT)
        self.reader = OmpReader(self.root)

    def _path(self, *names, archived=False):
        """The file of a session: `names` are the folders below the project folder and, last, the stem of the file."""
        folder = self.archive if archived else self.sessions
        return os.path.join(folder, *names[:-1], names[-1] + (".jsonl.gz" if archived else ".jsonl"))

    def _parent(self, *names):
        """The `parentSession` value OMP writes for a session: the plain path where the parent was created."""
        return os.path.join(self.sessions, *names[:-1], names[-1] + ".jsonl")

    def _store(self, *names, records, archived=False, mtime_ns=None):
        path = self._path(*names, archived=archived)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        text = records if isinstance(records, str) else jsonl(*records)
        if archived:
            write_gzip(path, text, mtime_ns)
        else:
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
            if mtime_ns is not None:
                os.utime(path, ns=(mtime_ns, mtime_ns))
        return path

    def _store_bytes(self, *names, data, archived=False):
        path = self._path(*names, archived=archived)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def _unit(self, path):
        (unit,) = [unit for unit in self.reader.scan().units if unit.key == path]
        return unit

    def _read_all(self):
        """Every session of the store as sync reads it: the units grouped by `session_of`, each group read whole."""
        grouped = {}
        for unit in self.reader.scan().units:
            grouped.setdefault(self.reader.session_of(unit), []).append(unit)
        return {key: self.reader.read(key, tuple(units)) for key, units in grouped.items()}

    def _read_session(self, session_id, *paths):
        return self.reader.read("omp:" + session_id, tuple(self._unit(path) for path in paths))

    def _read_one(self, records, **kwargs):
        """The result of reading one root session made of `records`."""
        stem, session_id = root_of(1)
        path = self._store(stem, records=records, **kwargs)
        return self._read_session(session_id, path)

    def _prompts(self, records):
        return self._read_one([header_record(root_of(1)[1]), *records]).session.prompts


class OmpScanTest(OmpReaderTestCase):
    def test_scan_lists_jsonl_under_sessions_and_gz_under_archive_sessions_at_any_depth(self):
        stem, _ = root_of(1)
        archived_stem, _ = root_of(2)
        paths = [self._store(stem, records=""), self._store(stem, "Task", records=""), self._store(stem, "Task", "Sub", records=""),
                 self._store(archived_stem, records="", archived=True), self._store(archived_stem, "Task", records="", archived=True)]
        result = self.reader.scan()
        self.assertEqual((sorted(paths), ()), ([unit.key for unit in result.units], result.unreadable))

    def test_scan_ignores_other_names_and_the_wrong_suffix_for_the_folder(self):
        stem, _ = root_of(1)
        kept = self._store(stem, records="")
        os.makedirs(self.archive)
        for folder, name in ((self.sessions, "zipped.jsonl.gz"), (self.archive, "plain.jsonl"), (self.sessions, ".%s.jsonl.lock.os" % stem),
                             (self.sessions, "notes.txt"), (self.archive, "notes.txt"), (self.sessions, "x.jsonl.zst")):
            with open(os.path.join(folder, name), "w", encoding="utf-8") as handle:
                handle.write("x")
        os.mkdir(os.path.join(self.sessions, "dir.jsonl"))
        os.mkdir(os.path.join(self.archive, "dir.jsonl.gz"))
        with open(os.path.join(self.root, "stray.jsonl"), "w", encoding="utf-8") as handle:
            handle.write("{}\n")
        self.assertEqual([kept], [unit.key for unit in self.reader.scan().units])

    def test_scan_fingerprint_is_the_size_and_the_mtime_so_either_change_shows(self):
        stem, _ = root_of(1)
        path = self._store(stem, records="abc\n", mtime_ns=5_000_000_000)
        self.assertEqual("4:5000000000", self._unit(path).fingerprint)
        os.utime(path, ns=(0, 6_000_000_000))
        self.assertEqual("4:6000000000", self._unit(path).fingerprint)
        self._store(stem, records="abcd\n", mtime_ns=6_000_000_000)
        self.assertEqual("5:6000000000", self._unit(path).fingerprint)

    def test_scan_modified_at_is_the_file_mtime_as_aware_utc(self):
        stem, _ = root_of(1)
        path = self._store(stem, records="", mtime_ns=int(at(0).timestamp()) * 1_000_000_000)
        self.assertEqual(at(0), self._unit(path).modified_at)

    def test_scan_missing_folders_give_an_empty_result(self):
        result = self.reader.scan()
        self.assertEqual(((), ()), (result.units, result.unreadable))

    def test_scan_only_one_of_the_two_folders_existing_is_fine(self):
        stem, _ = root_of(1)
        path = self._store(stem, records="", archived=True)
        self.assertEqual([path], [unit.key for unit in self.reader.scan().units])

    @unittest.skipIf(os.name == "nt", "needs POSIX symlinks")
    def test_scan_does_not_walk_into_a_symlinked_folder(self):
        stem, _ = root_of(1)
        path = self._store(stem, records="")
        os.symlink(os.path.join(self.root, "sessions"), os.path.join(self.sessions, "loop"))
        self.assertEqual([path], [unit.key for unit in self.reader.scan().units])

    @unittest.skipIf(os.name == "nt", "needs POSIX symlinks")
    def test_scan_lists_a_symlink_to_a_file_and_ignores_a_dangling_one(self):
        stem, _ = root_of(1)
        self._store(stem, records="")
        target = os.path.join(self.root, "target.jsonl")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("{}\n")
        os.symlink(target, os.path.join(self.sessions, "link.jsonl"))
        os.symlink(os.path.join(self.root, "nowhere.jsonl"), os.path.join(self.sessions, "dangling.jsonl"))
        self.assertEqual([self._path(stem), os.path.join(self.sessions, "link.jsonl")],
                         [unit.key for unit in self.reader.scan().units])

    def test_scan_sorted_by_key_whatever_the_listing_order(self):
        for number in (3, 1, 2):
            self._store(root_of(number)[0], records="")
        self._store(root_of(4)[0], records="", archived=True)
        with scandir_wrapping(reverse=True):
            keys = [unit.key for unit in self.reader.scan().units]
        self.assertEqual((sorted(keys), 4), (keys, len(keys)))

    def test_scan_skips_a_file_that_vanished_before_stat(self):
        stem, _ = root_of(1)
        self._store(stem, records="")
        gone = FakeEntry(os.path.join(self.sessions, "gone.jsonl"), FileNotFoundError())
        with scandir_wrapping({self.sessions: [gone]}):
            result = self.reader.scan()
        self.assertEqual(([stem + ".jsonl"], ()), ([os.path.basename(unit.key) for unit in result.units], result.unreadable))
        self.assertTrue(gone.queried)

    def test_scan_reports_a_file_that_cannot_be_examined_under_its_key_and_lists_the_rest(self):
        stem, _ = root_of(1)
        self._store(stem, records="")
        os.makedirs(self.archive)
        for folder, name, error in ((self.sessions, "locked.jsonl", PermissionError()), (self.archive, "locked.jsonl.gz", OSError("I/O error"))):
            with self.subTest(name=name):
                locked = FakeEntry(os.path.join(folder, name), error)
                with scandir_wrapping({folder: [locked]}):
                    result = self.reader.scan()
                self.assertEqual(1, len(result.units))
                self.assertEqual((UnreadableSource(Harness.OMP, locked.path, CANNOT_BE_LISTED_REASON),), result.unreadable)

    def test_scan_reports_a_folder_entry_that_cannot_be_examined_and_skips_one_that_vanished(self):
        stem, _ = root_of(1)
        self._store(stem, records="")
        denied = FakeEntry(os.path.join(self.sessions, "denied"), PermissionError())
        vanished = FakeEntry(os.path.join(self.sessions, "vanished"), FileNotFoundError())
        with scandir_wrapping({self.sessions: [denied, vanished]}):
            result = self.reader.scan()
        self.assertEqual((1, (UnreadableSource(Harness.OMP, denied.path, CANNOT_BE_LISTED_REASON),)), (len(result.units), result.unreadable))
        self.assertTrue(vanished.queried)

    def test_scan_reports_an_unlistable_folder_and_scans_the_rest(self):
        stem, _ = root_of(1)
        self._store(stem, records="")
        locked = os.path.join(self.sessions, stem)
        os.makedirs(locked)
        with scandir_wrapping(fail={locked: PermissionError()}):
            result = self.reader.scan()
        self.assertEqual(1, len(result.units))
        self.assertEqual((UnreadableSource(Harness.OMP, locked, CANNOT_BE_LISTED_REASON),), result.unreadable)

    def test_scan_reports_each_unlistable_top_folder_sorted_by_path_and_a_vanished_one_is_not_a_problem(self):
        top_sessions, top_archive = os.path.join(self.root, "sessions"), os.path.join(self.root, "archive", "sessions")
        with scandir_wrapping(fail={top_sessions: PermissionError(), top_archive: PermissionError()}):
            result = self.reader.scan()
        self.assertEqual(((), (UnreadableSource(Harness.OMP, top_archive, CANNOT_BE_LISTED_REASON),
                               UnreadableSource(Harness.OMP, top_sessions, CANNOT_BE_LISTED_REASON))), (result.units, result.unreadable))
        with scandir_wrapping(fail={top_sessions: FileNotFoundError()}):
            result = self.reader.scan()
        self.assertEqual(((), ()), (result.units, result.unreadable))


class OmpSessionOfTest(OmpReaderTestCase):
    def test_session_of_a_root_is_the_stem_after_its_first_underscore_plain_or_gzipped(self):
        plain_stem, plain_id = root_of(1)
        archived_stem, archived_id = root_of(2)
        plain = self._store(plain_stem, records="")
        archived = self._store(archived_stem, records="", archived=True)
        self.assertEqual(("omp:" + plain_id, "omp:" + archived_id),
                         (self.reader.session_of(self._unit(plain)), self.reader.session_of(self._unit(archived))))

    def test_session_of_a_child_and_a_grandchild_is_the_session_of_the_folder_they_are_in(self):
        stem, session_id = root_of(1)
        child = self._store(stem, "Task", records="")
        grandchild = self._store(stem, "Task", "Sub", records="")
        archived_child = self._store(root_of(2)[0], "Task", records="", archived=True)
        self.assertEqual(["omp:" + session_id, "omp:" + session_id, "omp:" + root_of(2)[1]],
                         [self.reader.session_of(self._unit(path)) for path in (child, grandchild, archived_child)])

    def test_session_of_a_root_stem_without_an_underscore_is_the_whole_stem_and_one_with_nothing_after_it_is_empty(self):
        whole = self._store("plain-name", records="")
        empty = self._store("stem_", records="")
        self.assertEqual(["omp:plain-name", "omp:"], [self.reader.session_of(self._unit(path)) for path in (whole, empty)])

    def test_session_of_a_file_straight_in_sessions_without_a_project_folder_is_its_own_root(self):
        os.makedirs(os.path.join(self.root, "sessions"))
        stem, session_id = root_of(1)
        path = os.path.join(self.root, "sessions", stem + ".jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("")
        self.assertEqual("omp:" + session_id, self.reader.session_of(self._unit(path)))

    def test_session_of_returns_the_key_of_an_unsafe_root_id_for_sync_to_list(self):
        path = self._store("2026-10-13T08-42-00-000Z_not safe", records="")
        self.assertEqual("omp:not safe", self.reader.session_of(self._unit(path)))

    def test_session_of_is_told_by_the_path_alone_and_opens_no_file(self):
        stem, session_id = root_of(1)
        plain, archived = self._store(stem, "Task", records=""), self._store(root_of(2)[0], records="", archived=True)
        units = [self._unit(plain), self._unit(archived)]
        with OmpOpenCounter() as opened:
            sessions = [self.reader.session_of(unit) for unit in units]
        self.assertEqual((["omp:" + session_id, "omp:" + root_of(2)[1]], {}), (sessions, dict(opened)))

    def test_session_of_a_unit_outside_both_store_folders_is_none(self):
        stray = os.path.join(self.root, "elsewhere", "x.jsonl")
        os.makedirs(os.path.dirname(stray))
        with open(stray, "w", encoding="utf-8") as handle:
            handle.write("")
        self.assertIsNone(self.reader.session_of(SourceUnit(key=stray, fingerprint="0:0", modified_at=at(0))))


class OmpFixtureReadTest(OmpReaderTestCase):
    def _install_fixtures(self):
        root = self._store(FIXTURE_ROOT_STEM, records=read_text(ROOT_FIXTURE))
        child = self._store(FIXTURE_ROOT_STEM, "ChildTask", records=read_text(CHILD_FIXTURE))
        return root, child

    @staticmethod
    def _request_fields(prompt):
        return [(r.model_name, r.input_tokens, r.output_tokens, r.cache_read_tokens, r.cache_write_5m_tokens, r.cache_write_1h_tokens, r.effort,
                 r.recorded_cost_usd, r.fast) for r in prompt.requests]

    def test_read_builds_the_session_of_the_fixture_root_with_its_child_as_a_sub_agent(self):
        root, child = self._install_fixtures()
        result = self._read_session(FIXTURE_ROOT_ID, root, child)
        session = result.session
        self.assertEqual(((), Harness.OMP, FIXTURE_ROOT_ID, CWD, None), (result.unreadable, session.harness, session.session_id,
                                                                          session.project_dir, session.orphan_of_parent_id))
        self.assertEqual(at(2.142), session.started_at)
        first, second = session.prompts
        self.assertEqual((at(10), "first prompt", at(25), "first reply", timedelta(seconds=15)),
                         (first.submitted_at, first.text, first.reply_at, first.reply_text, first.working_time))
        self.assertEqual((at(31.0), "skill prompt text", at(40), "second reply", timedelta(seconds=9)),
                         (second.submitted_at, second.text, second.reply_at, second.reply_text, second.working_time))

    def test_read_fixture_requests_carry_tokens_recorded_cost_model_effort_and_the_one_hour_cache_split(self):
        root, child = self._install_fixtures()
        first, second = self._read_session(FIXTURE_ROOT_ID, root, child).session.prompts
        self.assertEqual([("anthropic/claude-opus-5-5", 4, 100, 1000, 0, 200, "medium", 0.01, None),
                          ("anthropic/claude-opus-5-5", 6, 60, 1200, 0, 0, "medium", 0.02, None)], self._request_fields(first))
        self.assertEqual([("anthropic/claude-haiku-5", 2, 3, 0, 0, 0, "high", 0.001, None),
                          ("anthropic/claude-opus-5-5", 8, 80, 1500, 100, 0, "high", 0.03, None)], self._request_fields(second))

    def test_read_fixture_child_is_a_sub_agent_of_its_type_with_its_own_id_start_and_working_time(self):
        root, child = self._install_fixtures()
        (agent,) = self._read_session(FIXTURE_ROOT_ID, root, child).session.sub_agents
        self.assertEqual((FIXTURE_CHILD_ID, "task", at(12.5), timedelta(seconds=7.5), False, ()),
                         (agent.agent_id, agent.type, agent.started_at, agent.working_time, agent.tokens_not_recorded, agent.sub_agents))
        (request,) = agent.requests
        self.assertEqual((50, 20, 400, 0.005), (request.input_tokens, request.output_tokens, request.cache_read_tokens, request.recorded_cost_usd))

    def test_read_tolerates_a_byte_order_mark_before_the_header_in_a_plain_and_a_gzipped_file(self):
        stem, session_id = root_of(1)
        archived_stem, archived_id = root_of(2)
        plain = self._store(stem, records="\ufeff" + jsonl(header_record(session_id, second=7), user_record(10, "plain ask")))
        archived = self._store(archived_stem, records="\ufeff" + jsonl(header_record(archived_id, second=8), user_record(10, "zipped ask")), archived=True)
        self.assertEqual((at(7), ["plain ask"]), (lambda s: (s.started_at, [p.text for p in s.prompts]))(self._read_session(session_id, plain).session))
        self.assertEqual((at(8), ["zipped ask"]), (lambda s: (s.started_at, [p.text for p in s.prompts]))(self._read_session(archived_id, archived).session))


class OmpReadSessionsTest(OmpReaderTestCase):
    def test_sessions_and_gz_archive_both_archived_children_are_subagents_recorded_cost_used(self):
        plain_stem, plain_id = root_of(1)
        archived_stem, archived_id = root_of(2)
        self._store(plain_stem, records=[header_record(plain_id), user_record(10, "plain ask"), assistant_record(20, "plain reply", usage=usage_of(10, 5, cost=0.25), done=2)])
        self._store(plain_stem, "Task", records=[header_record("c1", parent=self._parent(plain_stem)), init_record("task"), assistant_record(13, usage=usage_of(3, 3, cost=0.5))])
        self._store(plain_stem, "Task", "Sub", records=[header_record("c2", parent=self._parent(plain_stem, "Task")), assistant_record(14, usage=usage_of(1, 1, cost=0.125))])
        self._store(archived_stem, records=[header_record(archived_id), user_record(10, "zipped ask"), assistant_record(20, "zipped reply", usage=usage_of(7, 2, cost=0.0), done=2)],
                    archived=True)
        self._store(archived_stem, "Task", records=[header_record("c3", parent=self._parent(archived_stem)), assistant_record(13, usage=usage_of(4, 4, cost=1.5))], archived=True)

        results = self._read_all()
        self.assertEqual({"omp:" + plain_id, "omp:" + archived_id}, set(results))
        plain, archived = results["omp:" + plain_id].session, results["omp:" + archived_id].session
        self.assertEqual((["plain ask"], ["zipped ask"]), ([p.text for p in plain.prompts], [p.text for p in archived.prompts]))
        self.assertEqual([0.25], [r.recorded_cost_usd for r in plain.prompts[0].requests])
        self.assertEqual([0.0], [r.recorded_cost_usd for r in archived.prompts[0].requests])  # a recorded 0 is free, never priced
        (task,) = plain.sub_agents
        self.assertEqual(("c1", [0.5], [("c2", [0.125])]),
                         (task.agent_id, [r.recorded_cost_usd for r in task.requests],
                          [(sub.agent_id, [r.recorded_cost_usd for r in sub.requests]) for sub in task.sub_agents]))
        (archived_task,) = archived.sub_agents
        self.assertEqual(("c3", "unknown", [1.5]), (archived_task.agent_id, archived_task.type, [r.recorded_cost_usd for r in archived_task.requests]))
        self.assertEqual((), results["omp:" + plain_id].unreadable + results["omp:" + archived_id].unreadable)

    def test_custom_message_user_attribution_or_skill_prompt_counts_other_types_skipped(self):
        records = [
            custom_record(10, "user attribution", custom_type="note", attribution="user"),
            custom_record(11, "skill without attribution"),
            custom_record(12, "skill agent attribution", attribution="agent"),
            custom_record(13, "other type no attribution", custom_type="background-note"),
            custom_record(14, "other type agent attribution", custom_type="background-note", attribution="agent"),
            custom_record(15, "user and skill", attribution="user"),
            custom_record(16, [{"type": "text", "text": "list one"}, {"type": "text", "text": "list two"}], attribution="user"),
            user_record(17, "plain message"),
            user_record(18, "agent message", attribution="agent"),
            user_record(19, "no attribution", attribution=None),
            user_record(20, "", attribution="user", content=[{"type": "image"}]),
        ]
        self.assertEqual(["user attribution", "skill without attribution", "skill agent attribution", "user and skill", "list one\nlist two",
                          "plain message", "no attribution", ""], [p.text for p in self._prompts(records)])

    def test_root_id_parent_link_by_stem_orphan_label_and_cyclic_pair_unreadable_reply_and_effort_rules(self):
        self._check_reply_time_and_effort_rules()
        self._check_parent_link_by_stem_in_either_folder()
        self._check_orphan_child_labelled_with_its_parent()
        self._check_cyclic_pair_listed_unreadable()

    def _check_reply_time_and_effort_rules(self):
        """The root id is the stem after its first `_`, equal to the header id; the reply is the last `stop` message with its `completedAt`."""
        stem, root_id = root_of(1)
        self._store(stem, records=[header_record(root_id), effort_record(5, "low"), user_record(10, "ask one"),
                                   assistant_record(11, "working", stop="toolUse", usage=usage_of(1, 1)),
                                   assistant_record(20, "answer", stop="stop", usage=usage_of(1, 1), done=3),
                                   assistant_record(30, "after stop", stop="toolUse", usage=usage_of(1, 1)),
                                   effort_record(35, "high"), user_record(40, "ask two"), assistant_record(50, "aborted", stop="aborted", usage=usage_of(1, 1)),
                                   user_record(60, "ask three"), assistant_record(61, "last words", stop="stop", usage=usage_of(1, 1), done=None)])
        with self.subTest("reply, time and effort"):
            session = self._read_session(root_id, self._path(stem)).session
            one, two, three = session.prompts
            self.assertEqual(root_id, session.session_id)
            self.assertEqual(("answer", at(23), timedelta(seconds=13), ["low", "low", "low"]),
                             (one.reply_text, one.reply_at, one.working_time, [r.effort for r in one.requests]))
            self.assertEqual((None, None, timedelta(0), ["high"]), (two.reply_text, two.reply_at, two.working_time, [r.effort for r in two.requests]))
            self.assertEqual(("last words", at(61)), (three.reply_text, three.reply_at))  # no `completedAt`: the record time

    def _check_parent_link_by_stem_in_either_folder(self):
        """The root and a middle child moved to `archive/sessions/` as gz while their children stay in `sessions/`."""
        stem, session_id = root_of(2)
        self._store(stem, records=[header_record(session_id), user_record(10, "moved ask")], archived=True)
        self._store(stem, "Mid", records=[header_record("mid", parent=self._parent(stem)), assistant_record(13, usage=usage_of(2, 2))], archived=True)
        self._store(stem, "Mid", "Leaf", records=[header_record("leaf", parent=self._parent(stem, "Mid")), assistant_record(14, usage=usage_of(3, 3))])
        self._store(stem, "Other", records=[header_record("other", parent=self._parent(stem)), assistant_record(15, usage=usage_of(4, 4))])
        with self.subTest("link by stem in either folder"):
            session = self._read_all()["omp:" + session_id].session
            self.assertEqual((["moved ask"], ["mid", "other"], [["leaf"], []]),
                             ([p.text for p in session.prompts], [a.agent_id for a in session.sub_agents], [[g.agent_id for g in a.sub_agents] for a in session.sub_agents]))

    def _check_orphan_child_labelled_with_its_parent(self):
        """A child whose parent is gone is archived alone, labelled with the parent's id; one whose parent is a missing child names that child."""
        gone_stem, gone_id = root_of(3)
        self._store(gone_stem, "Task", records=[header_record("orphan", parent=self._parent(gone_stem)), assistant_record(13, usage=usage_of(2, 2))])
        deep_stem, deep_id = root_of(4)
        self._store(deep_stem, "Missing", "Leaf", records=[header_record("deep", parent=self._parent(deep_stem, "Missing")), assistant_record(13, usage=usage_of(2, 2))])
        results = self._read_all()
        with self.subTest("orphan labelled with its parent"):
            orphan = results["omp:" + gone_id]
            self.assertEqual((gone_id, gone_id, (), ["orphan"], ()), (orphan.session.session_id, orphan.session.orphan_of_parent_id, orphan.session.prompts,
                                                                   [a.agent_id for a in orphan.session.sub_agents], orphan.unreadable))
            self.assertEqual("Missing", results["omp:" + deep_id].session.orphan_of_parent_id)

    def _check_cyclic_pair_listed_unreadable(self):
        """Two children pointing at each other, one that hangs from them, and a child that is its own parent; the rest is read."""
        stem, session_id = root_of(5)
        self._store(stem, records=[header_record(session_id), user_record(10, "cycle ask")])
        first = self._store(stem, "A", records=[header_record("a", parent=self._parent(stem, "B")), assistant_record(13, usage=usage_of(1, 1))])
        second = self._store(stem, "B", records=[header_record("b", parent=self._parent(stem, "A")), assistant_record(14, usage=usage_of(1, 1))])
        below = self._store(stem, "A", "Below", records=[header_record("below", parent=self._parent(stem, "A")), assistant_record(15, usage=usage_of(1, 1))])
        itself = self._store(stem, "Self", records=[header_record("self", parent=self._parent(stem, "Self")), assistant_record(16, usage=usage_of(1, 1))])
        self._store(stem, "Fine", records=[header_record("fine", parent=self._parent(stem)), assistant_record(17, usage=usage_of(1, 1))])
        result = self._read_all()["omp:" + session_id]
        with self.subTest("cyclic pair unreadable"):
            self.assertEqual(sorted((path, PARENT_CYCLE_REASON) for path in (first, second, below, itself)),
                             sorted((source.path, source.reason) for source in result.unreadable))
            self.assertEqual((["cycle ask"], ["fine"]), ([p.text for p in result.session.prompts], [a.agent_id for a in result.session.sub_agents]))
            self.assertTrue(all(source.harness is Harness.OMP for source in result.unreadable))

    def test_a_family_that_is_only_a_cycle_has_no_session_and_lists_the_pair_unreadable(self):
        stem, session_id = root_of(1)
        first = self._store(stem, "A", records=[header_record("a", parent=self._parent(stem, "B")), assistant_record(13, usage=usage_of(1, 1))])
        second = self._store(stem, "B", records=[header_record("b", parent=self._parent(stem, "A")), assistant_record(14, usage=usage_of(1, 1))])
        result = self._read_session(session_id, first, second)
        self.assertEqual((None, [(first, PARENT_CYCLE_REASON), (second, PARENT_CYCLE_REASON)]),
                         (result.session, [(source.path, source.reason) for source in result.unreadable]))

    def test_an_orphan_family_beside_a_cycle_is_still_archived_and_the_cycle_listed(self):
        stem, session_id = root_of(1)
        orphan = self._store(stem, "Task", records=[header_record("t", parent=self._parent(stem)), assistant_record(13, usage=usage_of(1, 1))])
        first = self._store(stem, "A", records=[header_record("a", parent=self._parent(stem, "B")), assistant_record(13, usage=usage_of(1, 1))])
        second = self._store(stem, "B", records=[header_record("b", parent=self._parent(stem, "A")), assistant_record(14, usage=usage_of(1, 1))])
        result = self._read_session(session_id, orphan, first, second)
        self.assertEqual((session_id, ["t"], [first, second]), (result.session.orphan_of_parent_id, [a.agent_id for a in result.session.sub_agents],
                                                               [source.path for source in result.unreadable]))

    def test_a_child_hanging_from_an_unreadable_parent_makes_the_orphan_family_unreadable_and_a_present_root_still_archives(self):
        stem, session_id = root_of(1)
        broken = self._store_bytes(stem, "Broken", data=b"\xff\xfe not utf-8 \xff")
        leaf = self._store(stem, "Broken", "Leaf", records=[header_record("leaf", parent=self._parent(stem, "Broken")), assistant_record(13, usage=usage_of(1, 1))])
        result = self._read_session(session_id, broken, leaf)
        self.assertEqual((None, [(broken, CANNOT_BE_DECODED_REASON)]), (result.session, [(s.path, s.reason) for s in result.unreadable]))
        root = self._store(stem, records=[header_record(session_id), user_record(10, "ask")])
        result = self._read_session(session_id, root, broken, leaf)
        self.assertEqual((["ask"], [(broken, CANNOT_BE_DECODED_REASON)]), ([p.text for p in result.session.prompts], [(s.path, s.reason) for s in result.unreadable]))

    def test_a_root_that_cannot_be_read_gives_no_session_even_with_readable_children(self):
        stem, session_id = root_of(1)
        root = self._store_bytes(stem, data=b"\xff\xfe")
        child = self._store(stem, "Task", records=[header_record("t", parent=self._parent(stem)), assistant_record(13, usage=usage_of(1, 1))])
        result = self._read_session(session_id, root, child)
        self.assertEqual((None, [(root, CANNOT_BE_DECODED_REASON)]), (result.session, [(s.path, s.reason) for s in result.unreadable]))

    def test_a_root_without_a_start_gives_no_session_and_no_problem_whatever_its_children(self):
        stem, session_id = root_of(1)
        root = self._store(stem, records=[{"type": "session"}, {"type": "custom", "customType": "x"}])
        child = self._store(stem, "Task", records=[header_record("t", parent=self._parent(stem)), assistant_record(13, usage=usage_of(1, 1))])
        result = self._read_session(session_id, root, child)
        self.assertEqual((None, ()), (result.session, result.unreadable))

    def test_the_session_id_is_the_one_of_the_root_stem_whatever_the_header_says(self):
        stem, session_id = root_of(1)
        self._store(stem, records=[header_record("some-other-id"), user_record(10, "ask")])
        results = self._read_all()
        self.assertEqual((["omp:" + session_id], session_id), (list(results), results["omp:" + session_id].session.session_id))

    def test_files_of_one_stem_in_different_folders_of_a_family_are_different_files_and_all_are_read(self):
        stem, session_id = root_of(1)
        paths = [self._store(stem, records=[header_record(session_id), user_record(10, "ask")]),
                 self._store(stem, "Twin", records=[header_record("shallow", parent=self._parent(stem)), assistant_record(13, usage=usage_of(1, 1))]),
                 self._store(stem, "Twin", "Twin", records=[header_record("deep", parent=self._parent(stem)), assistant_record(14, usage=usage_of(2, 2))])]
        session = self._read_session(session_id, *paths).session
        self.assertEqual(["shallow", "deep"], [a.agent_id for a in session.sub_agents])

    def test_the_files_of_a_family_are_read_and_listed_in_key_order_whatever_their_file_times(self):
        stem, session_id = root_of(1)
        root = self._store(stem, records=[header_record(session_id), user_record(10, "ask")])
        later_key = self._store_bytes(stem, "B", data=b"\xff\xfe")
        earlier_key = self._store_bytes(stem, "A", data=b"\xff\xfe")
        os.utime(later_key, ns=(0, 1_000_000_000))
        os.utime(earlier_key, ns=(0, 2_000_000_000))
        result = self._read_session(session_id, root, later_key, earlier_key)
        self.assertEqual([earlier_key, later_key], [source.path for source in result.unreadable])

    def test_of_two_copies_with_one_file_time_the_greater_key_wins_whatever_the_order_of_the_units(self):
        stem, session_id = root_of(1)
        plain = self._store(stem, records=[header_record(session_id), user_record(10, "plain copy")], mtime_ns=1_000_000_000)
        archived = self._store(stem, records=[header_record(session_id), user_record(10, "archived copy")], archived=True, mtime_ns=1_000_000_000)
        for paths in ((plain, archived), (archived, plain)):
            with self.subTest(order=[os.path.basename(os.path.dirname(os.path.dirname(path))) for path in paths]):
                self.assertEqual(["plain copy"], [p.text for p in self._read_session(session_id, *paths).session.prompts])  # `sessions` > `archive`

    def test_two_root_files_of_one_id_are_copies_and_the_newest_is_read(self):
        older_stem, session_id = root_of(1)
        newer_stem = "2026-10-13T08-59-00-000Z_" + session_id
        older = self._store(older_stem, records=[header_record(session_id), user_record(10, "older")], mtime_ns=1_000_000_000)
        newer = self._store(newer_stem, records=[header_record(session_id), user_record(10, "newer")], mtime_ns=2_000_000_000)
        self.assertEqual(["newer"], [p.text for p in self._read_session(session_id, older, newer).session.prompts])


class OmpRootLoopTest(OmpReaderTestCase):
    """Root-level files (not in a family folder) that name each other as parent, as a fork names the root it was forked from: S42's two
    sessions whose `parentSession`s point at each other are listed unreadable and not archived."""

    def _root_file(self, number, parent_number=None, archived=False, **kwargs):
        stem, session_id = root_of(number)
        parent = None if parent_number is None else self._parent(root_of(parent_number)[0])
        records = [header_record(session_id, parent=parent), user_record(10, "ask %d" % number)]
        return self._store(stem, records=records, archived=archived, **kwargs), session_id

    def _read_root(self, session_id, *paths):
        self.reader.scan()
        return self._read_session(session_id, *paths)

    def test_two_root_files_naming_each_other_are_each_unreadable_as_a_parent_cycle_and_no_session(self):
        (first, first_id), (second, second_id) = self._root_file(1, 2), self._root_file(2, 1, archived=True)
        for session_id, path in ((first_id, first), (second_id, second)):
            with self.subTest(session=session_id):
                result = self._read_root(session_id, path)
                self.assertEqual((None, (UnreadableSource(Harness.OMP, path, PARENT_CYCLE_REASON),)), (result.session, result.unreadable))

    def test_a_root_file_that_names_itself_or_hangs_from_a_loop_is_unreadable_too(self):
        alone, alone_id = self._root_file(1, 1)
        self._root_file(2, 3)
        self._root_file(3, 2)
        below, below_id = self._root_file(4, 2)
        for session_id, path in ((alone_id, alone), (below_id, below)):
            with self.subTest(session=session_id):
                result = self._read_root(session_id, path)
                self.assertEqual((None, (UnreadableSource(Harness.OMP, path, PARENT_CYCLE_REASON),)), (result.session, result.unreadable))

    def test_a_loop_at_the_root_blocks_its_whole_family_and_lists_the_root_file(self):
        first, first_id = self._root_file(1, 2)
        self._root_file(2, 1)
        stem, _ = root_of(1)
        child = self._store(stem, "Task", records=[header_record("t", parent=self._parent(stem)), assistant_record(13, usage=usage_of(1, 1))])
        result = self._read_root(first_id, first, child)
        self.assertEqual((None, [(first, PARENT_CYCLE_REASON)]), (result.session, [(s.path, s.reason) for s in result.unreadable]))

    def test_a_fork_chain_without_a_loop_is_read_as_separate_sessions_and_a_missing_or_unreadable_ancestor_ends_the_chain(self):
        (parent, parent_id), (fork, fork_id), (orphan_fork, orphan_id) = self._root_file(1), self._root_file(2, 1), self._root_file(3, 9)
        self._store_bytes(root_of(4)[0], data=b"\xff\xfe")
        child_of_broken, child_id = self._root_file(5, 4)
        for session_id, path in ((parent_id, parent), (fork_id, fork), (orphan_id, orphan_fork), (child_id, child_of_broken)):
            with self.subTest(session=session_id):
                result = self._read_root(session_id, path)
                self.assertEqual((session_id, ()), (result.session.session_id, result.unreadable))

    def test_the_ancestors_of_a_fork_are_opened_for_their_headers_once_per_scan_however_many_files_name_them(self):
        parent, _ = self._root_file(1)
        fork, fork_id = self._root_file(2, 1)
        other, other_id = self._root_file(3, 1)
        fork_unit, other_unit = self._unit(fork), self._unit(other)
        self.reader.scan()
        with OmpOpenCounter() as opened:
            self.reader.read("omp:" + fork_id, (fork_unit,))
            self.reader.read("omp:" + other_id, (other_unit,))
        self.assertEqual({fork: 1, other: 1, parent: 1}, dict(opened))
        self.reader.scan()  # a new scan reads the header again
        with OmpOpenCounter() as opened:
            self.reader.read("omp:" + fork_id, (fork_unit,))
        self.assertEqual({fork: 1, parent: 1}, dict(opened))

    def test_a_loop_opens_each_file_of_it_once(self):
        first, first_id = self._root_file(1, 2)
        second, _ = self._root_file(2, 1)
        unit = self._unit(first)
        self.reader.scan()
        with OmpOpenCounter() as opened:
            result = self.reader.read("omp:" + first_id, (unit,))
        self.assertEqual(({first: 1, second: 1}, [first]), (dict(opened), [source.path for source in result.unreadable]))

    def test_a_loop_of_three_and_a_long_chain_without_a_loop(self):
        paths = [self._root_file(number, number % 3 + 1)[0] for number in (1, 2, 3)]  # 1 -> 2 -> 3 -> 1
        ids = [root_of(number)[1] for number in (1, 2, 3)]
        results = [self._read_root(session_id, path) for session_id, path in zip(ids, paths)]
        self.assertEqual([(None, (path,)) for path in paths], [(result.session, tuple(source.path for source in result.unreadable)) for result in results])
        chain = [self._root_file(number, number - 1 if number > 10 else None)[0] for number in range(10, 20)]  # 10 <- 11 <- ... <- 19
        result = self._read_root(root_of(19)[1], chain[-1])
        self.assertEqual((root_of(19)[1], ()), (result.session.session_id, result.unreadable))

    def test_the_chain_follows_the_newest_copy_of_an_ancestor_and_a_tie_goes_to_the_greater_key(self):
        fork, fork_id = self._root_file(2, 1)
        stem, parent_id = root_of(1)
        plain_to_fork = [header_record(parent_id, parent=self._parent(root_of(2)[0])), user_record(10, "plain")]
        no_parent = [header_record(parent_id), user_record(10, "archived")]
        for name, plain_time, archived_time, loops in (("archived is newer", 1_000_000_000, 2_000_000_000, False), ("plain is newer", 2_000_000_000, 1_000_000_000, True),
                                                       ("a tie goes to `sessions`", 1_000_000_000, 1_000_000_000, True)):
            with self.subTest(name):
                self._store(stem, records=plain_to_fork, mtime_ns=plain_time)
                self._store(stem, records=no_parent, archived=True, mtime_ns=archived_time)
                result = self._read_root(fork_id, fork)
                self.assertEqual(loops, result.session is None)

    def test_a_loop_at_the_root_with_a_child_loop_and_an_unreadable_child_lists_them_all_and_builds_no_session(self):
        first, first_id = self._root_file(1, 2)
        self._root_file(2, 1)
        stem, _ = root_of(1)
        broken = self._store_bytes(stem, "Broken", data=b"\xff\xfe")
        loop_a = self._store(stem, "A", records=[header_record("a", parent=self._parent(stem, "B")), assistant_record(13, usage=usage_of(1, 1))])
        loop_b = self._store(stem, "B", records=[header_record("b", parent=self._parent(stem, "A")), assistant_record(14, usage=usage_of(1, 1))])
        result = self._read_root(first_id, first, broken, loop_a, loop_b)
        self.assertEqual((None, sorted([(broken, CANNOT_BE_DECODED_REASON), (first, PARENT_CYCLE_REASON), (loop_a, PARENT_CYCLE_REASON), (loop_b, PARENT_CYCLE_REASON)])),
                         (result.session, sorted((source.path, source.reason) for source in result.unreadable)))

    def test_an_ancestor_that_vanished_since_the_scan_ends_the_chain_so_no_loop_is_seen(self):
        first, first_id = self._root_file(1, 2)
        second, _ = self._root_file(2, 1)
        unit = self._unit(first)
        self.reader.scan()
        os.remove(second)
        result = self.reader.read("omp:" + first_id, (unit,))
        self.assertEqual((first_id, ()), (result.session.session_id, result.unreadable))

    def test_a_reader_that_has_not_scanned_scans_once_and_finds_the_loop(self):
        first, first_id = self._root_file(1, 2)
        self._root_file(2, 1)
        unit = self._unit(first)
        fresh = OmpReader(self.root)
        with mock.patch("os.scandir", wraps=os.scandir) as scandir:
            first_result = fresh.read("omp:" + first_id, (unit,))
            fresh.read("omp:" + first_id, (unit,))
        top_sessions = os.path.join(self.root, "sessions")
        self.assertEqual((None, 1), (first_result.session, [call.args[0] for call in scandir.call_args_list].count(top_sessions)))

    def test_a_root_file_without_a_parent_reads_no_other_file(self):
        alone, alone_id = self._root_file(1)
        self.reader.scan()
        unit = self._unit(alone)
        with OmpOpenCounter() as opened:
            self.reader.read("omp:" + alone_id, (unit,))
        self.assertEqual({alone: 1}, dict(opened))


class OmpHiddenRootTest(OmpReaderTestCase):
    """A scan that could not examine the root's file, or a folder it may sit in, must not turn its family into an orphan."""

    def setUp(self):
        super().setUp()
        self.stem, self.session_id = root_of(1)
        self.child = self._store(self.stem, "Task", records=[header_record("t", parent=self._parent(self.stem)), assistant_record(13, usage=usage_of(1, 1))])
        os.makedirs(self.archive)

    def _result(self, fail=None, extra=None):
        unit = self._unit(self.child)  # before the failing scan: a scan of its own would replace the problems it found
        with scandir_wrapping(extra, fail):
            self.reader.scan()
        return self.reader.read("omp:" + self.session_id, (unit,))

    def test_a_root_file_that_cannot_be_examined_in_either_folder_makes_the_family_unreadable_not_an_orphan(self):
        for folder, name in ((self.sessions, self.stem + ".jsonl"), (self.archive, self.stem + ".jsonl.gz")):
            with self.subTest(name=name):
                locked = FakeEntry(os.path.join(folder, name), PermissionError())
                result = self._result(extra={folder: [locked]})
                self.assertEqual((None, (UnreadableSource(Harness.OMP, self.child, PARENT_UNREADABLE_REASON),)), (result.session, result.unreadable))

    def test_a_folder_the_root_may_sit_in_that_cannot_be_listed_makes_the_family_unreadable_too(self):
        top_archive = os.path.join(self.root, "archive", "sessions")
        for folder in (self.archive, top_archive):
            with self.subTest(folder=folder):
                result = self._result(fail={folder: PermissionError()})
                self.assertEqual((None, (UnreadableSource(Harness.OMP, self.child, PARENT_UNREADABLE_REASON),)), (result.session, result.unreadable))

    def test_a_folder_named_like_the_root_file_without_its_suffix_does_not_hide_the_root_file(self):
        archived_child = self._store(self.stem, "Late", records=[header_record("late", parent=self._parent(self.stem)), assistant_record(13, usage=usage_of(1, 1))], archived=True)
        family_folder = os.path.join(self.sessions, self.stem)  # where the plain children would be; the root file is `<stem>.jsonl` beside it
        unit = self._unit(archived_child)
        with scandir_wrapping(fail={family_folder: PermissionError()}):
            self.reader.scan()
        result = self.reader.read("omp:" + self.session_id, (unit,))
        self.assertEqual((self.session_id, ()), (result.session.orphan_of_parent_id, result.unreadable))

    def test_a_hidden_root_is_reported_without_reading_any_file_and_with_the_newest_unit_of_the_session_alone(self):
        os.utime(self.child, ns=(0, 1_000_000_000))
        broken = self._store_bytes(self.stem, "Broken", data=b"\xff\xfe")  # the newest of the session, though not the greatest key
        os.utime(broken, ns=(0, 2_000_000_000))
        other_stem, other_id = root_of(2)
        foreign = self._store(other_stem, records=[header_record(other_id)], mtime_ns=9_000_000_000)  # newer than all, but of another session
        units = (self._unit(self.child), self._unit(broken), self._unit(foreign))
        locked = FakeEntry(os.path.join(self.sessions, self.stem + ".jsonl"), PermissionError())
        with scandir_wrapping({self.sessions: [locked]}):
            self.reader.scan()
        with OmpOpenCounter() as opened:
            result = self.reader.read("omp:" + self.session_id, units)
        self.assertEqual((None, (UnreadableSource(Harness.OMP, broken, PARENT_UNREADABLE_REASON),), {}), (result.session, result.unreadable, dict(opened)))

    def test_a_session_without_any_unit_is_an_empty_result_even_with_a_problem_in_the_scan(self):
        locked = FakeEntry(os.path.join(self.sessions, self.stem + ".jsonl"), PermissionError())
        with scandir_wrapping({self.sessions: [locked]}):
            self.reader.scan()
        result = self.reader.read("omp:" + self.session_id, ())
        self.assertEqual((None, ()), (result.session, result.unreadable))

    def test_a_problem_elsewhere_leaves_the_orphan_family_archived(self):
        other = os.path.join(self.sessions, "unrelated-folder")
        os.makedirs(other)
        locked = FakeEntry(os.path.join(self.sessions, "other_stem.jsonl"), PermissionError())
        for fail, extra in (({other: PermissionError()}, None), (None, {self.sessions: [locked]})):
            with self.subTest(fail=fail, extra=extra):
                result = self._result(fail=fail, extra=extra)
                self.assertEqual((self.session_id, ()), (result.session.orphan_of_parent_id, result.unreadable))

    def test_the_root_that_is_there_is_read_whatever_the_scan_could_not_examine(self):
        root = self._store(self.stem, records=[header_record(self.session_id), user_record(10, "ask")])
        top_archive = os.path.join(self.root, "archive", "sessions")
        units = (self._unit(root), self._unit(self.child))
        with scandir_wrapping(fail={top_archive: PermissionError()}):
            self.reader.scan()
        result = self.reader.read("omp:" + self.session_id, units)
        self.assertEqual((["ask"], None, ()), ([p.text for p in result.session.prompts], result.session.orphan_of_parent_id, result.unreadable))

    def test_a_reader_that_has_not_scanned_yet_scans_once_to_know_the_problems(self):
        locked = FakeEntry(os.path.join(self.sessions, self.stem + ".jsonl"), PermissionError())
        unit = self._unit(self.child)
        fresh = OmpReader(self.root)
        with scandir_wrapping({self.sessions: [locked]}):
            result = fresh.read("omp:" + self.session_id, (unit,))
        self.assertEqual((None, (UnreadableSource(Harness.OMP, self.child, PARENT_UNREADABLE_REASON),)), (result.session, result.unreadable))


class OmpReadErrorsTest(OmpReaderTestCase):
    def test_read_of_an_unparsable_unit_is_unreadable_as_not_parsed_and_an_empty_or_blank_one_is_no_session_and_no_problem(self):
        stem, session_id = root_of(1)
        path = self._store(stem, records="this is not json\nneither is this\n")
        result = self._read_session(session_id, path)
        self.assertEqual((None, (UnreadableSource(Harness.OMP, path, CANNOT_BE_PARSED_REASON),)), (result.session, result.unreadable))
        for text in ("", "\n  \n\t\n"):
            with self.subTest(text=text):
                self._store(stem, records=text)
                result = self._read_session(session_id, path)
                self.assertEqual((None, ()), (result.session, result.unreadable))

    def test_read_of_an_undecodable_unit_is_unreadable_as_not_decodable_and_a_vanished_one_as_not_openable(self):
        stem, session_id = root_of(1)
        path = self._store_bytes(stem, data=b"\xff\xfe\xfd")
        unit = self._unit(path)
        result = self.reader.read("omp:" + session_id, (unit,))
        self.assertEqual((None, (UnreadableSource(Harness.OMP, path, CANNOT_BE_DECODED_REASON),)), (result.session, result.unreadable))
        os.remove(path)
        result = self.reader.read("omp:" + session_id, (unit,))
        self.assertEqual((None, (UnreadableSource(Harness.OMP, path, CANNOT_BE_OPENED_REASON),)), (result.session, result.unreadable))

    def test_read_of_a_gzip_that_is_not_gzip_data_or_is_cut_or_corrupt_is_unreadable_as_not_decodable(self):
        good = gzip.compress(jsonl(header_record(root_of(1)[1]), user_record(10, "ask")).encode("utf-8"))
        for name, data in (("not gzip", b"definitely not gzip"), ("cut", good[:-12]), ("corrupt body", good[:12] + b"\x00" * 8 + good[20:])):
            with self.subTest(name):
                stem, session_id = root_of(1)
                path = self._store_bytes(stem, data=data, archived=True)
                result = self._read_session(session_id, path)
                self.assertEqual((None, (UnreadableSource(Harness.OMP, path, CANNOT_BE_DECODED_REASON),)), (result.session, result.unreadable))

    def test_read_of_an_unsafe_root_id_is_unreadable_and_names_the_newest_unit_then_the_greatest_key(self):
        stem = "2026-10-13T08-42-00-000Z_not safe"
        older = self._store(stem, records=jsonl(header_record("x")), mtime_ns=1_000_000_000)
        newer = self._store(stem, "Task", records="", mtime_ns=2_000_000_000)
        result = self.reader.read("omp:not safe", (self._unit(older), self._unit(newer)))
        self.assertEqual((None, (UnreadableSource(Harness.OMP, newer, UNSAFE_ID_REASON),)), (result.session, result.unreadable))
        tied = self._store(stem, "Zed", records="", mtime_ns=2_000_000_000)
        result = self.reader.read("omp:not safe", (self._unit(older), self._unit(newer), self._unit(tied)))
        self.assertEqual(tied, result.unreadable[0].path)
        self.assertEqual(((), None), (self.reader.read("omp:not safe", ()).unreadable, self.reader.read("omp:not safe", ()).session))

    def test_read_of_a_key_of_another_harness_or_without_an_id_is_an_empty_result(self):
        stem, session_id = root_of(1)
        unit = self._unit(self._store(stem, records=[header_record(session_id)]))
        for key in ("codex:" + session_id, "omp:", "", "omp"):
            with self.subTest(key=key):
                result = self.reader.read(key, (unit,))
                self.assertEqual((None, ()), (result.session, result.unreadable))

    def test_read_leaves_out_the_units_of_other_sessions_and_foreign_paths(self):
        stem, session_id = root_of(1)
        other_stem, other_id = root_of(2)
        mine = self._store(stem, records=[header_record(session_id), user_record(10, "mine")])
        theirs = self._store(other_stem, records=[header_record(other_id), user_record(10, "theirs")])
        stray = os.path.join(self.root, "elsewhere.jsonl")
        with open(stray, "w", encoding="utf-8") as handle:
            handle.write(jsonl(header_record(session_id), user_record(10, "stray")))
        foreign = SourceUnit(key=stray, fingerprint="0:0", modified_at=at(0))
        result = self.reader.read("omp:" + session_id, (self._unit(mine), self._unit(theirs), foreign))
        self.assertEqual((["mine"], ()), ([p.text for p in result.session.prompts], result.unreadable))

    def test_read_opens_every_unit_it_is_given_exactly_once_and_never_learns_a_session_again(self):
        stem, session_id = root_of(1)
        paths = [self._store(stem, records=[header_record(session_id), user_record(10, "ask")]), self._store(stem, "Task", records=[header_record("t", parent=self._parent(stem))]),
                 self._store(stem, "Task", "Sub", records=[header_record("s", parent=self._parent(stem, "Task"))])]
        archived = self._store(root_of(2)[0], records=[header_record(root_of(2)[1])], archived=True)
        units = tuple(self._unit(path) for path in paths)
        archived_unit = self._unit(archived)
        with OmpOpenCounter() as opened, mock.patch.object(self.reader, "session_of", side_effect=AssertionError("learned again")):
            self.reader.read("omp:" + session_id, units)
            self.reader.read("omp:" + root_of(2)[1], (archived_unit,))
        self.assertEqual({path: 1 for path in paths + [archived]}, dict(opened))

    def test_read_takes_only_the_newest_copy_of_a_file_found_in_both_folders_and_opens_the_other_never(self):
        stem, session_id = root_of(1)
        copies = {}
        for name, names, parent in (("root", (stem,), None), ("child", (stem, "Task"), self._parent(stem))):
            old = self._store(*names, records=[header_record("old-" + name, parent=parent), user_record(10, "old copy")], mtime_ns=1_000_000_000)
            new = self._store(*names, records=[header_record("new-" + name, parent=parent), user_record(10, "new copy")], archived=True, mtime_ns=2_000_000_000)
            copies[name] = (old, new)
        units = tuple(self._unit(path) for pair in copies.values() for path in pair)
        with OmpOpenCounter() as opened:
            result = self.reader.read("omp:" + session_id, units)
        self.assertEqual(({copies["root"][1]: 1, copies["child"][1]: 1}, ["new-child"], ()),
                         (dict(opened), [a.agent_id for a in result.session.sub_agents], result.unreadable))
        os.utime(copies["child"][0], ns=(0, 3_000_000_000))
        result = self.reader.read("omp:" + session_id, tuple(self._unit(path) for pair in copies.values() for path in pair))
        self.assertEqual(["old-child"], [a.agent_id for a in result.session.sub_agents])

    def test_read_reports_a_corrupt_archived_parent_and_the_session_is_not_archived_as_orphans(self):
        stem, session_id = root_of(1)
        parent = self._store_bytes(stem, data=b"not gzip", archived=True)
        child = self._store(stem, "Task", records=[header_record("t", parent=self._parent(stem)), assistant_record(13, usage=usage_of(1, 1))])
        result = self._read_session(session_id, parent, child)
        self.assertEqual((None, [(parent, CANNOT_BE_DECODED_REASON)]), (result.session, [(s.path, s.reason) for s in result.unreadable]))


if __name__ == "__main__":
    unittest.main()
