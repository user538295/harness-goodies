"""OpenCode adapter of the reader port (S11, S14): one unit per session row of `session_v2` with a fingerprint made of the messages' row
count, newest `seq` and newest `time_updated`, the session learned from the `parent_id` chain, sub-agents walked recursively, parent
cycles listed unreadable, and the database opened read-only. The rules of the messages are tested in `test_opencode_messages`."""
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from sessionlog.model import Harness
from sessionlog.opencode_reader import OpenCodeReader
from sessionlog.reader_port import (CANNOT_BE_OPENED_REASON, PARENT_CYCLE_REASON, UNSAFE_ID_REASON, SourceUnit, UnreadableSource,
                                    build_session_key)
from tests.opencode_db import (BASE, CHILD_ID, CWD, FIXTURES, NO_SEQ_INDEX_SCHEMA, ROOT_ID, V1_SCHEMA, OpenCodeDatabase, assistant_data, folder_snapshot,
                               ms, user_data)

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def key_of(session_id):
    return build_session_key(Harness.OPENCODE, session_id)


def outcome(result):
    return result.session, result.unreadable


class OpenCodeReaderTestCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.database = OpenCodeDatabase(os.path.join(self.folder, "opencode.db"))
        self.reader = OpenCodeReader(self.database.path)

    def read_family(self, root_id=ROOT_ID):
        """What sync does for one session: scan, group the units by `session_of`, read the group of `root_id`."""
        scan = self.reader.scan()
        units = tuple(unit for unit in scan.units if self.reader.session_of(unit) == key_of(root_id))
        return self.reader.read(key_of(root_id), units)

    def groups(self):
        """The scan's units grouped by session key, as sync groups them."""
        grouped = {}
        for unit in self.reader.scan().units:
            grouped.setdefault(self.reader.session_of(unit), []).append(unit)
        return grouped


class ScanTest(OpenCodeReaderTestCase):
    def test_every_session_row_is_a_unit_keyed_by_its_id_with_a_fingerprint_of_its_messages(self):
        self.database.fixture()
        scan = self.reader.scan()
        self.assertEqual(((), [CHILD_ID, ROOT_ID]), (scan.unreadable, [unit.key for unit in scan.units]))
        units = {unit.key: unit for unit in scan.units}
        self.assertEqual("4:4:%d" % ms(31), units[ROOT_ID].fingerprint)
        self.assertEqual("2:2:%d" % ms(21), units[CHILD_ID].fingerprint)

    def test_the_unit_was_modified_at_the_newer_of_its_session_row_and_its_newest_message(self):
        self.database.add_session("ses_session_newer", created=10, updated=50)
        self.database.add_user("ses_session_newer", 1, 10)
        self.database.add_session("ses_message_newer", created=10, updated=20)
        self.database.add_message("ses_message_newer", 1, "assistant", assistant_data(11, tokens=(1, 1)), updated=70)
        units = {unit.key: unit.modified_at for unit in self.reader.scan().units}
        self.assertEqual({"ses_session_newer": BASE + timedelta(seconds=50), "ses_message_newer": BASE + timedelta(seconds=70)}, units)

    def test_a_session_without_messages_is_a_unit_too(self):
        self.database.add_session("ses_empty", created=5)
        (unit,) = self.reader.scan().units
        self.assertEqual(("ses_empty", "0:0:0"), (unit.key, unit.fingerprint))

    def test_a_row_updated_in_place_changes_the_fingerprint_and_a_new_row_changes_it_too(self):
        self.database.fixture()
        before = {unit.key: unit.fingerprint for unit in self.reader.scan().units}
        self.database.replace_data(ROOT_ID, 4, assistant_data(31, "second reply, finished later", tokens=(6, 40), done=12), updated=70)
        after = {unit.key: unit.fingerprint for unit in self.reader.scan().units}
        self.assertEqual((True, False), (after[ROOT_ID] != before[ROOT_ID], after[CHILD_ID] != before[CHILD_ID]))
        self.database.add_user(CHILD_ID, 3, 50)
        self.assertNotEqual(after[CHILD_ID], {unit.key: unit.fingerprint for unit in self.reader.scan().units}[CHILD_ID])

    def test_an_empty_database_lists_nothing(self):
        self.assertEqual(((), ()), (self.reader.scan().units, self.reader.scan().unreadable))

    def test_times_and_counts_the_database_holds_that_are_no_numbers_leave_the_unit_listed_at_the_epoch_and_raise_nothing(self):
        self.database.execute("INSERT INTO session_v2 (id, project_id, slug, directory, version, time_created, time_updated)"
                              " VALUES ('ses_odd', 'prj', 's', '/w', '1', 'soon', 'later')")
        self.database.execute("INSERT INTO session_message (id, session_id, type, seq, time_created, time_updated, data)"
                              " VALUES ('m1', 'ses_odd', 'user', 'first', 'x', 'y', '{}')")
        scan = self.reader.scan()
        self.assertEqual(((), [("ses_odd", "1:0:0", EPOCH)]), (scan.unreadable, [(unit.key, unit.fingerprint, unit.modified_at) for unit in scan.units]))

    def test_a_root_whose_row_holds_no_start_time_gives_no_session(self):
        self.database.execute("INSERT INTO session_v2 (id, project_id, slug, directory, version, time_created, time_updated)"
                              " VALUES ('ses_odd', 'prj', 's', '/w', '1', 'soon', 5)")
        self.assertEqual((None, ()), outcome(self.read_family("ses_odd")))


class SessionOfTest(OpenCodeReaderTestCase):
    def test_a_child_and_a_grandchild_belong_to_the_session_of_their_root(self):
        self.database.fixture()
        self.database.add_session("ses_grand", parent=CHILD_ID, created=25)
        self.assertEqual({key_of(ROOT_ID): [CHILD_ID, "ses_grand", ROOT_ID]}, {key: [unit.key for unit in units] for key, units in self.groups().items()})

    def test_a_session_whose_parent_row_is_gone_is_its_own_root_so_it_loses_nothing(self):
        self.database.add_session("ses_orphan", parent="ses_deleted", created=5)
        self.assertEqual([key_of("ses_orphan")], list(self.groups()))

    def test_a_session_in_a_parent_loop_and_one_hanging_from_it_are_each_their_own_session(self):
        self.database.add_session("ses_a", parent="ses_b", created=1)
        self.database.add_session("ses_b", parent="ses_a", created=2)
        self.database.add_session("ses_c", parent="ses_a", created=3)
        self.database.add_session("ses_self", parent="ses_self", created=4)
        self.assertEqual({key_of(name) for name in ("ses_a", "ses_b", "ses_c", "ses_self")}, set(self.groups()))

    def test_a_unit_that_is_in_no_scan_yet_is_asked_without_one_and_a_session_that_is_new_is_its_own_root(self):
        self.database.fixture()
        unit = SourceUnit(key=CHILD_ID, fingerprint="x", modified_at=BASE)
        self.assertEqual(key_of(ROOT_ID), OpenCodeReader(self.database.path).session_of(unit))
        self.assertEqual(key_of("ses_new"), OpenCodeReader(self.database.path).session_of(SourceUnit("ses_new", "x", BASE)))


class ReadTest(OpenCodeReaderTestCase):
    def test_parent_id_subagents_walked_cost_used_cyclic_sessions_listed_unreadable_and_sync_ends(self):
        self.database.fixture()
        self.database.add_session("ses_grand", parent=CHILD_ID, created=25, updated=45, agent="explore")
        self.database.add_assistant("ses_grand", 1, 26, tokens=(2, 2), cost=1.5, done=3, model=("openai", "gpt-5"), agent="explore")
        self.database.add_session("ses_cyc_a", parent="ses_cyc_b", created=70)
        self.database.add_session("ses_cyc_b", parent="ses_cyc_a", created=71)
        self.database.add_session("ses_cyc_c", parent="ses_cyc_b", created=72)
        self.database.add_session("ses_self", parent="ses_self", created=73)
        self.database.add_user("ses_cyc_a", 1, 70, "cyclic")
        grouped = self.groups()  # every group is read once, as sync does: nothing loops
        result = self.reader.read(key_of(ROOT_ID), tuple(grouped[key_of(ROOT_ID)]))
        session = result.session
        self.assertEqual(((), Harness.OPENCODE, ROOT_ID, CWD), (result.unreadable, session.harness, session.session_id, session.project_dir))
        self.assertEqual(["first prompt", "second prompt"], [prompt.text for prompt in session.prompts])
        self.assertEqual(["first reply", "second reply"], [prompt.reply_text for prompt in session.prompts])
        first_request = session.prompts[0].requests[0]
        self.assertEqual((0.5, 120), (first_request.recorded_cost_usd, first_request.output_tokens))
        self.assertEqual(0.0, session.prompts[1].requests[0].recorded_cost_usd)  # a recorded zero is a free request
        (child,) = session.sub_agents
        self.assertEqual((CHILD_ID, "general", BASE + timedelta(seconds=20), timedelta(seconds=6), 0.25),
                         (child.agent_id, child.type, child.started_at, child.working_time, child.requests[0].recorded_cost_usd))
        (grandchild,) = child.sub_agents
        self.assertEqual(("ses_grand", "explore", 1.5), (grandchild.agent_id, grandchild.type, grandchild.requests[0].recorded_cost_usd))
        for name in ("ses_cyc_a", "ses_cyc_b", "ses_cyc_c", "ses_self"):
            cyclic = self.reader.read(key_of(name), tuple(grouped[key_of(name)]))
            self.assertEqual((None, (UnreadableSource(Harness.OPENCODE, name, PARENT_CYCLE_REASON),)), (cyclic.session, cyclic.unreadable))

    def test_rows_in_the_shapes_of_a_real_store_give_prompts_replies_requests_and_a_sub_agent(self):
        """`fixtures/opencode/session_rows.sql` has the keys and value types of a real v2.0.30 store, with placeholder content."""
        self.database.load_sql(os.path.join(FIXTURES, "session_rows.sql"))
        result = self.read_family("ses_fixture_root")
        session = result.session
        first, second = session.prompts
        self.assertEqual(((), "/work/fixture", BASE + timedelta(seconds=10)), (result.unreadable, session.project_dir, session.started_at))
        self.assertEqual(("placeholder question", "placeholder answer part one\n\nplaceholder answer part two", BASE + timedelta(seconds=25), timedelta(seconds=15)),
                         (first.text, first.reply_text, first.reply_at, first.working_time))  # reasoning and tool blocks are no reply text
        self.assertEqual([("fixture-provider/fixture-model", 1000, 60, 200, 300, 0.01), ("fixture-provider/fixture-model", 2000, 80, 1200, 0, 0.02)],
                         [(r.model_name, r.input_tokens, r.output_tokens, r.cache_read_tokens, r.cache_write_5m_tokens, r.recorded_cost_usd) for r in first.requests])
        self.assertEqual(("second placeholder question", "second placeholder answer", timedelta(seconds=40), [0, 300]),
                         (second.text, second.reply_text, second.working_time, [r.input_tokens for r in second.requests]))
        (child,) = session.sub_agents
        self.assertEqual(("ses_fixture_child", "general", BASE + timedelta(seconds=30), timedelta(seconds=40), [(400, 60, 50, 20, 0.005)]),
                         (child.agent_id, child.type, child.started_at, child.working_time,
                          [(r.input_tokens, r.output_tokens, r.cache_read_tokens, r.cache_write_5m_tokens, r.recorded_cost_usd) for r in child.requests]))

    def test_the_session_starts_at_the_row_and_its_reply_times_come_from_completed(self):
        self.database.fixture()
        session = self.read_family().session
        self.assertEqual(BASE + timedelta(seconds=10), session.started_at)
        first = session.prompts[0]
        self.assertEqual((BASE + timedelta(seconds=15), timedelta(seconds=5)), (first.reply_at, first.working_time))

    def test_a_session_without_a_project_folder_has_none(self):
        self.database.add_session("ses_a", created=1, directory="")
        self.assertIsNone(self.read_family("ses_a").session.project_dir)

    def test_a_child_without_a_type_is_unknown(self):
        self.database.add_session(ROOT_ID, created=1)
        self.database.add_session("ses_typeless", parent=ROOT_ID, created=2)
        session = self.read_family(ROOT_ID).session
        self.assertEqual([("ses_typeless", "unknown")], [(agent.agent_id, agent.type) for agent in session.sub_agents])
        self.assertIsNone(session.orphan_of_parent_id)

    def test_a_session_whose_parent_row_is_gone_is_archived_alone_labelled_with_the_parent_it_names(self):
        self.database.add_session("ses_stray", parent="ses_deleted", created=3)
        self.database.add_user("ses_stray", 1, 4, "stray ask")
        session = self.read_family("ses_stray").session
        self.assertEqual(("ses_stray", "ses_deleted", ["stray ask"]), (session.session_id, session.orphan_of_parent_id, [p.text for p in session.prompts]))

    def test_a_session_gone_from_the_database_since_the_scan_reads_as_nothing(self):
        self.database.fixture()
        unit = SourceUnit(key="ses_vanished", fingerprint="x", modified_at=BASE)
        self.assertEqual((None, ()), outcome(self.reader.read(key_of("ses_vanished"), (unit,))))

    def test_a_session_key_of_another_harness_or_without_units_reads_as_nothing(self):
        self.database.fixture()
        unit = SourceUnit(key=ROOT_ID, fingerprint="x", modified_at=BASE)
        for key, units in (("claude:" + ROOT_ID, (unit,)), (key_of(ROOT_ID), ()), ("opencode", (unit,))):
            with self.subTest(key=key):
                self.assertEqual((None, ()), outcome(self.reader.read(key, units)))

    def test_an_unsafe_session_id_is_listed_unreadable_and_never_built(self):
        self.database.add_session("ses.unsafe/id", created=1)
        unit = SourceUnit(key="ses.unsafe/id", fingerprint="x", modified_at=BASE)
        result = self.reader.read(key_of("ses.unsafe/id"), (unit,))
        self.assertEqual((None, (UnreadableSource(Harness.OPENCODE, "ses.unsafe/id", UNSAFE_ID_REASON),)), outcome(result))

    def test_a_message_row_that_is_no_json_is_skipped_and_the_rest_of_the_session_is_read(self):
        self.database.add_session(ROOT_ID, created=1)
        self.database.add_message(ROOT_ID, 1, "user", {}, text="not json at all")
        self.database.add_user(ROOT_ID, 2, 10, "kept")
        self.assertEqual(["kept"], [prompt.text for prompt in self.read_family().session.prompts])

    def test_a_message_row_with_bytes_that_are_no_utf8_is_read_with_replacement_characters_and_does_not_make_the_session_unreadable(self):
        self.database.add_session(ROOT_ID, created=1)
        self.database.execute("INSERT INTO session_message (id, session_id, type, seq, time_created, time_updated, data) VALUES"
                              " ('bad', ?, 'user', 1, 1, 1, CAST(CAST('{\"text\": \"a' AS BLOB) || X'ff' || CAST('b\", \"time\": {\"created\": %d}}' AS BLOB) AS TEXT))"
                              % ms(10), (ROOT_ID,))
        self.database.add_user(ROOT_ID, 2, 20, "kept")
        result = self.read_family()
        self.assertEqual(((), ["a\ufffdb", "kept"]), (result.unreadable, [prompt.text for prompt in result.session.prompts]))

    def test_the_messages_are_read_by_seq_whatever_the_order_they_were_written_in(self):
        """On a database without the `(session_id, seq)` index, whose rows would otherwise come back in write order (here reversed)."""
        database = OpenCodeDatabase(os.path.join(self.folder, "no-index", "opencode.db"), schema=NO_SEQ_INDEX_SCHEMA)
        database.add_session(ROOT_ID, created=1)
        database.add_assistant(ROOT_ID, 14, 31, text="second reply", tokens=(1, 1), done=1)
        database.add_user(ROOT_ID, 13, 30, "second")
        database.add_assistant(ROOT_ID, 12, 11, text="first reply", tokens=(1, 1), done=1)
        database.add_user(ROOT_ID, 11, 10, "first")
        reader = OpenCodeReader(database.path)
        prompts = reader.read(key_of(ROOT_ID), tuple(reader.scan().units)).session.prompts
        self.assertEqual([("first", "first reply"), ("second", "second reply")], [(prompt.text, prompt.reply_text) for prompt in prompts])

    def test_a_listed_unit_in_a_parent_loop_read_together_with_a_healthy_root_is_left_out_of_its_session(self):
        self.database.fixture()
        self.database.add_session("ses_a", parent="ses_b", created=70)
        self.database.add_session("ses_b", parent="ses_a", created=71)
        self.database.add_user("ses_a", 1, 70, "cyclic")
        scan = self.reader.scan()
        units = tuple(unit for unit in scan.units if unit.key in (ROOT_ID, CHILD_ID, "ses_a"))
        result = self.reader.read(key_of(ROOT_ID), units)
        self.assertEqual(([CHILD_ID], (UnreadableSource(Harness.OPENCODE, "ses_a", PARENT_CYCLE_REASON),)),
                         ([agent.agent_id for agent in result.session.sub_agents], result.unreadable))

    def test_a_message_cell_stored_as_a_blob_is_read_like_text_with_replacement_characters_for_bytes_that_are_no_utf8(self):
        self.database.add_session(ROOT_ID, created=1)
        prefix = b'{"text": "blob a'
        suffix = ('b", "time": {"created": %d}}' % ms(10)).encode("utf-8")
        self.database.execute("INSERT INTO session_message (id, session_id, type, seq, time_created, time_updated, data)"
                              " VALUES ('blob', ?, 'user', 1, 1, 1, X'%s')" % (prefix + b"\xff" + suffix).hex(), (ROOT_ID,))
        self.assertEqual(["blob a\ufffdb"], [prompt.text for prompt in self.read_family().session.prompts])

    def test_a_sub_agent_whose_rows_end_before_its_start_worked_no_time_and_one_without_a_start_time_starts_when_its_root_does(self):
        self.database.add_session(ROOT_ID, created=50)
        self.database.add_session("ses_early", parent=ROOT_ID, created=40)
        self.database.add_assistant("ses_early", 1, 5, tokens=(1, 1), done=1)
        self.database.execute("INSERT INTO session_v2 (id, project_id, parent_id, slug, directory, version, time_created, time_updated)"
                              " VALUES ('ses_timeless', 'prj', ?, 's', '/w', '1', 'soon', 5)", (ROOT_ID,))
        agents = {agent.agent_id: agent for agent in self.read_family().session.sub_agents}
        self.assertEqual((timedelta(0), BASE + timedelta(seconds=50)), (agents["ses_early"].working_time, agents["ses_timeless"].started_at))


class DatabaseFailureTest(unittest.TestCase):
    """A database that cannot be opened or queried never raises: scan lists it unreadable and read names every unit it was given."""

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.path = os.path.join(self.folder, "opencode.db")

    def test_a_database_file_that_is_missing_is_listed_unreadable_not_raised(self):
        scan = OpenCodeReader(self.path).scan()
        self.assertEqual(((), (UnreadableSource(Harness.OPENCODE, self.path, CANNOT_BE_OPENED_REASON),)), (scan.units, scan.unreadable))
        self.assertFalse(os.path.exists(self.path))  # looking for a database never makes one

    def test_a_file_that_is_no_database_is_listed_unreadable_by_scan_and_by_read(self):
        with open(self.path, "wb") as handle:
            handle.write(b"this is no database " * 100)
        reader = OpenCodeReader(self.path)
        self.assertEqual((UnreadableSource(Harness.OPENCODE, self.path, CANNOT_BE_OPENED_REASON),), reader.scan().unreadable)
        unit = SourceUnit(key=ROOT_ID, fingerprint="x", modified_at=BASE)
        self.assertEqual((UnreadableSource(Harness.OPENCODE, ROOT_ID, CANNOT_BE_OPENED_REASON),), reader.read(key_of(ROOT_ID), (unit,)).unreadable)
        self.assertIsNone(reader.session_of(unit))

    def test_a_failed_scan_forgets_what_an_earlier_one_saw_so_session_of_is_not_answered_from_it(self):
        database = OpenCodeDatabase(self.path).fixture()
        reader = OpenCodeReader(database.path)
        units = reader.scan().units
        self.assertEqual(key_of(ROOT_ID), reader.session_of(units[0]))
        with open(self.path, "wb") as handle:
            handle.write(b"this is no database " * 100)
        self.assertEqual(1, len(reader.scan().unreadable))
        self.assertIsNone(reader.session_of(units[0]))

    def test_a_database_without_session_v2_is_listed_unreadable(self):
        OpenCodeDatabase(self.path, schema=V1_SCHEMA)
        self.assertEqual((UnreadableSource(Harness.OPENCODE, self.path, CANNOT_BE_OPENED_REASON),), OpenCodeReader(self.path).scan().unreadable)


class ReadOnlyTest(OpenCodeReaderTestCase):
    def test_database_opened_read_only(self):
        """No write to the store: every connection of the reader is opened with a `mode=ro` URI that refuses a write itself, and the folder
        (bytes, times, file list) is the same after a scan, session_of and read."""
        self.database.fixture()
        before = folder_snapshot(self.folder)
        real_connect = sqlite3.connect
        opened = []

        def connect(database, *args, **kwargs):
            connection = real_connect(database, *args, **kwargs)
            opened.append(database)
            with self.assertRaises(sqlite3.OperationalError, msg=database):  # before the reader asks for `query_only`: the URI alone must refuse
                connection.execute("CREATE TABLE intruder (a)")
            return connection

        with mock.patch("sqlite3.connect", connect):
            reader = OpenCodeReader(self.database.path)
            scan = reader.scan()
            units = tuple(unit for unit in scan.units if reader.session_of(unit) == key_of(ROOT_ID))
            self.assertIsNotNone(reader.read(key_of(ROOT_ID), units).session)
        self.assertTrue(opened)
        self.assertEqual(before, folder_snapshot(self.folder))

    def test_a_wal_database_is_read_without_a_byte_of_it_changing_and_leaves_at_most_sqlites_own_wal_files(self):
        wal_path = os.path.join(self.folder, "wal", "opencode.db")
        wal = OpenCodeDatabase(wal_path, journal_mode="wal").fixture()
        with open(wal.path, "rb") as handle:
            before = hashlib.sha256(handle.read()).hexdigest()
        reader = OpenCodeReader(wal.path)
        units = tuple(unit for unit in reader.scan().units if reader.session_of(unit) == key_of(ROOT_ID))
        self.assertEqual(["first prompt", "second prompt"], [prompt.text for prompt in reader.read(key_of(ROOT_ID), units).session.prompts])
        with open(wal.path, "rb") as handle:
            self.assertEqual(before, hashlib.sha256(handle.read()).hexdigest())
        self.assertLessEqual(set(os.listdir(os.path.dirname(wal.path))), {"opencode.db", "opencode.db-wal", "opencode.db-shm"})

    def test_a_wal_database_with_rows_still_in_its_wal_file_is_read_with_them(self):
        """A live OpenCode keeps a writer open and rows in `-wal` that no checkpoint has moved into the database file yet."""
        wal = OpenCodeDatabase(os.path.join(self.folder, "live", "opencode.db"), journal_mode="wal")
        writer = sqlite3.connect(wal.path)
        self.addCleanup(writer.close)
        writer.execute("PRAGMA wal_autocheckpoint = 0")
        writer.executescript("INSERT INTO session_v2 (id, project_id, slug, directory, version, time_created, time_updated)"
                             " VALUES ('ses_live', 'prj', 's', '/w', '1', %d, %d);"
                             " INSERT INTO session_message (id, session_id, type, seq, time_created, time_updated, data)"
                             " VALUES ('m1', 'ses_live', 'user', 1, 1, 1, '%s');" % (ms(1), ms(1), json.dumps(user_data(10, "only in the wal"))))
        writer.commit()
        reader = OpenCodeReader(wal.path)
        units = tuple(reader.scan().units)
        self.assertEqual(["only in the wal"], [prompt.text for prompt in reader.read(key_of("ses_live"), units).session.prompts])

    def test_a_database_in_a_folder_whose_name_needs_escaping_in_a_uri_is_read(self):
        odd = OpenCodeDatabase(os.path.join(self.folder, "my data #1 %20 é", "opencode.db")).fixture()
        reader = OpenCodeReader(odd.path)
        self.assertEqual(2, len(reader.scan().units))


if __name__ == "__main__":
    unittest.main()
