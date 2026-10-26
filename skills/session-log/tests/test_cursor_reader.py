"""Cursor adapter of the reader port (S12, S14, S44): one unit per `.records/<SafeId>/` folder with a fingerprint of its record count and
newest record name, records joined by `generation_id` (usage from `stop` only), sub-agents per `child_conversation_id`, duplicates counted
once, `*.tmp` and anything that is no record ignored, `.records/.invalid/` listed once with its call count. Records are written the way
the hook writes them (`tests.cursor_record_files`); no real user content is used."""
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from sessionlog import cursor_reader
from sessionlog.cursor_conversation import NO_PROMPT_TEXT
from sessionlog.cursor_reader import INVALID_CALLS_REASON, CursorHookActivity, CursorReader
from sessionlog.cursor_records import INVALID_FOLDER, record_fields
from sessionlog.model import Harness, Prompt, RequestUsage, SubAgent
from sessionlog.reader_port import (CANNOT_BE_LISTED_REASON, CANNOT_BE_OPENED_REASON, CANNOT_BE_PARSED_REASON, NOT_A_REGULAR_FILE_REASON,
                                    UNSAFE_ID_REASON, SourceUnit, UnreadableSource,
                                    build_session_key)
from sessionlog.safe_paths import LINK_REASON
from tests.cursor_record_files import (CONVERSATION, WORKSPACE, at_ns, moment, prompt_input, response_input, stop_input, subagent_input,
                                       write_record)
from tests.store_fakes import JunctionEntry, scandir_wrapping

MODEL = "claude-opus-5-5"
TOKENS = {"input_tokens": 1000, "output_tokens": 200, "cache_read_tokens": 300, "cache_write_tokens": 100, "model": MODEL}
REQUEST = RequestUsage(model_name=MODEL, input_tokens=600, output_tokens=200, cache_read_tokens=300, cache_write_5m_tokens=100,
                       cache_write_1h_tokens=0)


def key_of(conversation=CONVERSATION):
    return build_session_key(Harness.CURSOR, conversation)


class CursorReaderTestCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.records = os.path.join(self.folder, ".records")
        self.reader = CursorReader(self.records)

    def write(self, event, seconds, hook_input, **kwargs):
        return write_record(self.records, event, seconds, hook_input, **kwargs)

    def read(self, conversation=CONVERSATION):
        """What sync does for one conversation: scan, group the units by `session_of`, read the group of `conversation`."""
        scan = self.reader.scan()
        units = tuple(unit for unit in scan.units if self.reader.session_of(unit) == key_of(conversation))
        return self.reader.read(key_of(conversation), units)

    def session(self, conversation=CONVERSATION):
        result = self.read(conversation)
        self.assertEqual((), result.unreadable)
        return result.session

    def write_text_file(self, path, text, mode="w"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, mode) as stream:
            stream.write(text)


class ScanTest(CursorReaderTestCase):
    def test_a_missing_records_folder_has_no_units_and_no_problem(self):
        scan = self.reader.scan()
        self.assertEqual(((), ()), (scan.units, scan.unreadable))

    def test_every_safe_conversation_folder_is_a_unit_keyed_by_its_path_with_record_count_and_newest_record_name(self):
        first = self.write("beforeSubmitPrompt", 0, prompt_input("g1"))
        newest = self.write("stop", 7, stop_input("g1", **TOKENS))
        self.write("beforeSubmitPrompt", 3, prompt_input("g2", conversation="conv-2"), conversation="conv-2")
        scan = self.reader.scan()
        units = {unit.key: unit for unit in scan.units}
        self.assertEqual(((), [os.path.dirname(first), os.path.join(self.records, "conv-2")]), (scan.unreadable, [unit.key for unit in scan.units]))
        unit = units[os.path.dirname(first)]
        self.assertEqual("2:%s" % os.path.basename(newest), unit.fingerprint)
        self.assertEqual(moment(7), unit.modified_at)

    def test_only_safe_id_folders_with_records_are_listed_and_temps_other_files_and_links_are_ignored(self):
        record = self.write("stop", 1, stop_input("g1"))
        self.write("stop", 1, stop_input("g1"), conversation="a.b")  # not a SafeId: never a unit
        os.makedirs(os.path.join(self.records, "empty"))
        self.write_text_file(os.path.join(self.records, "loose.json"), "{}")  # a file, not a folder
        folder = os.path.dirname(record)
        self.write_text_file(os.path.join(folder, ".1-stop-9.json.9.abcd1234.tmp"), "{")  # a record temp, fresh or old
        self.write_text_file(os.path.join(folder, "x.tmp"), "{")
        self.write_text_file(os.path.join(folder, "notes.json"), "{}")  # no record name
        os.makedirs(os.path.join(folder, "1-stop-9.json"))  # a folder named like a record
        scan = self.reader.scan()
        self.assertEqual([folder], [unit.key for unit in scan.units])
        self.assertEqual("1:%s" % os.path.basename(record), scan.units[0].fingerprint)
        self.assertEqual((), scan.unreadable)

    def test_a_symlinked_conversation_folder_is_no_unit(self):
        elsewhere = os.path.join(self.folder, "elsewhere")
        os.makedirs(elsewhere)
        os.makedirs(self.records)
        try:
            os.symlink(elsewhere, os.path.join(self.records, CONVERSATION), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("cannot make symlinks")
        self.assertEqual((), self.reader.scan().units)

    def test_a_new_record_changes_the_fingerprint_also_when_it_repeats_an_event(self):
        first = self.write("beforeSubmitPrompt", 0, prompt_input("g1"))
        before = self.reader.scan().units[0]
        self.write("beforeSubmitPrompt", 0, prompt_input("g1"), pid=77)
        self.assertNotEqual(before.fingerprint, self.reader.scan().units[0].fingerprint)
        self.assertEqual(os.path.dirname(first), before.key)

    def test_the_invalid_folder_is_listed_once_with_its_call_count_and_its_temps_do_not_count(self):
        self.write("stop", 1, {}, conversation=INVALID_FOLDER, raw_input="junk")
        self.write("stop", 2, {}, conversation=INVALID_FOLDER, raw_input="")
        self.write("subagentStop", 3, {}, conversation=INVALID_FOLDER, raw_input="[1]")
        invalid = os.path.join(self.records, INVALID_FOLDER)
        self.write_text_file(os.path.join(invalid, ".1-stop-9.json.9.abcd1234.tmp"), "{")
        self.write("stop", 4, stop_input("g1"))
        scan = self.reader.scan()
        self.assertEqual([os.path.join(self.records, CONVERSATION)], [unit.key for unit in scan.units])  # `.invalid` is no unit
        self.assertEqual((UnreadableSource(Harness.CURSOR, invalid, INVALID_CALLS_REASON % 3, hides_sessions=False),), scan.unreadable)

    def test_an_empty_invalid_folder_is_not_listed(self):
        os.makedirs(os.path.join(self.records, INVALID_FOLDER))
        self.assertEqual(((), ()), (self.reader.scan().units, self.reader.scan().unreadable))

    def test_a_folder_that_cannot_be_listed_is_unreadable_and_the_others_are_still_listed(self):
        bad = os.path.dirname(self.write("stop", 1, stop_input("g1")))
        good = os.path.dirname(self.write("stop", 1, stop_input("g2", conversation="conv-2"), conversation="conv-2"))
        with scandir_wrapping(fail={bad: PermissionError("denied")}):
            scan = self.reader.scan()
        self.assertEqual(([good], (UnreadableSource(Harness.CURSOR, bad, CANNOT_BE_LISTED_REASON),)), ([unit.key for unit in scan.units], scan.unreadable))

    def test_a_records_folder_that_cannot_be_listed_is_one_unreadable_source(self):
        self.write("stop", 1, stop_input("g1"))
        with scandir_wrapping(fail={self.records: PermissionError("denied")}):
            scan = self.reader.scan()
        self.assertEqual(((), (UnreadableSource(Harness.CURSOR, self.records, CANNOT_BE_LISTED_REASON),)), (scan.units, scan.unreadable))

    def test_session_of_names_the_conversation_by_its_folder(self):
        self.write("stop", 1, stop_input("g1"))
        unit = self.reader.scan().units[0]
        self.assertEqual("cursor:" + CONVERSATION, self.reader.session_of(unit))


@unittest.skipUnless(hasattr(os, "symlink") and os.name != "nt", "needs symlinks")
class LinkedFoldersTest(CursorReaderTestCase):
    def link(self, link, target):
        os.makedirs(target, exist_ok=True)
        os.symlink(target, link, target_is_directory=True)

    def test_a_links_records_folder_is_listed_unreadable_alone_and_not_read_through(self):
        target = os.path.join(self.folder, "target")
        write_record(target, "stop", 1, stop_input("g1"))
        os.symlink(target, self.records, target_is_directory=True)
        scan = self.reader.scan()
        self.assertEqual(((), (UnreadableSource(Harness.CURSOR, self.records, LINK_REASON),)), (scan.units, scan.unreadable))

    def test_links_named_like_a_conversation_folder_or_invalid_are_listed_unreadable_and_other_links_ignored(self):
        kept = self.write("stop", 1, stop_input("g1"), conversation="conv-2")
        os.makedirs(self.records, exist_ok=True)
        for name in (CONVERSATION, INVALID_FOLDER, "a.b"):
            self.link(os.path.join(self.records, name), os.path.join(self.folder, "target-" + name))
        scan = self.reader.scan()
        self.assertEqual([os.path.dirname(kept)], [unit.key for unit in scan.units])
        self.assertEqual([(os.path.join(self.records, name), LINK_REASON, name == CONVERSATION) for name in (INVALID_FOLDER, CONVERSATION)],
                         [(source.path, source.reason, source.hides_sessions) for source in scan.unreadable])  # a linked `.invalid` hides no session

    def test_a_dangling_link_named_like_a_conversation_folder_is_listed_unreadable(self):
        os.makedirs(self.records)
        dangling = os.path.join(self.records, CONVERSATION)
        os.symlink(os.path.join(self.folder, "missing"), dangling)
        scan = self.reader.scan()
        self.assertEqual(((), (UnreadableSource(Harness.CURSOR, dangling, LINK_REASON),)), (scan.units, scan.unreadable))

    def test_hook_activity_is_open_while_records_is_a_link_and_reads_nothing_through_it(self):
        target = os.path.join(self.folder, "target")
        write_record(target, "stop", 1, stop_input("g1"))
        os.symlink(target, self.records, target_is_directory=True)
        self.assertIsNone(CursorHookActivity(self.records).recorded_since(moment(0)))


class JunctionTest(CursorReaderTestCase):
    def test_a_junction_named_like_a_conversation_folder_is_listed_unreadable_and_no_unit(self):
        os.makedirs(self.records)
        junction = os.path.join(self.records, CONVERSATION)
        with scandir_wrapping({self.records: [JunctionEntry(junction)]}), \
                mock.patch.object(cursor_reader.os.path, "isjunction", side_effect=lambda path: path == junction):
            scan = self.reader.scan()
        self.assertEqual(((), (UnreadableSource(Harness.CURSOR, junction, LINK_REASON),)), (scan.units, scan.unreadable))

    def test_a_junctioned_records_folder_is_listed_unreadable_alone_and_hook_activity_stays_open(self):
        self.write("stop", 1, stop_input("g1"))
        with mock.patch.object(cursor_reader.os.path, "isjunction", side_effect=lambda path: path == self.records):
            scan = self.reader.scan()
            recorded = CursorHookActivity(self.records).recorded_since(moment(0))
        self.assertEqual(((), (UnreadableSource(Harness.CURSOR, self.records, LINK_REASON),)), (scan.units, scan.unreadable))
        self.assertIsNone(recorded)


class HookActivityTest(CursorReaderTestCase):
    def test_a_record_at_or_after_the_time_counts_in_a_conversation_or_in_invalid_and_an_older_one_or_none_does_not(self):
        activity = CursorHookActivity(self.records)
        self.assertIs(False, activity.recorded_since(moment(0)))  # no records folder
        self.write("beforeSubmitPrompt", 5, prompt_input("g1"))
        self.assertIs(True, activity.recorded_since(moment(5)))
        self.assertIs(False, activity.recorded_since(moment(6)))
        self.write("beforeSubmitPrompt", 9, {}, conversation=INVALID_FOLDER)
        self.assertIs(True, activity.recorded_since(moment(6)))

    def test_only_invalid_counts_and_a_temp_or_other_file_does_not(self):
        activity = CursorHookActivity(self.records)
        self.write_text_file(os.path.join(self.records, "conv-1", ".5-stop-1.json.1.abcd.tmp"), "{}")
        self.write_text_file(os.path.join(self.records, "conv-1", "notes.txt"), "x")
        self.assertIs(False, activity.recorded_since(moment(0)))
        self.write("beforeSubmitPrompt", 5, {}, conversation=INVALID_FOLDER)
        self.assertIs(True, activity.recorded_since(moment(0)))

    def test_a_folder_that_cannot_be_listed_leaves_the_answer_open_unless_a_record_was_found(self):
        self.write("beforeSubmitPrompt", 5, prompt_input("g1"), conversation="conv-a")
        self.write("beforeSubmitPrompt", 9, prompt_input("g2"), conversation="conv-b")
        broken = os.path.join(self.records, "conv-a")
        with scandir_wrapping(fail={broken: PermissionError()}):
            activity = CursorHookActivity(self.records)
            self.assertIsNone(activity.recorded_since(moment(10)))
            self.assertIs(True, activity.recorded_since(moment(9)))

    def test_an_unlistable_records_folder_cannot_be_told(self):
        self.write("beforeSubmitPrompt", 5, prompt_input("g1"))
        with scandir_wrapping(fail={self.records: PermissionError()}):
            self.assertIsNone(CursorHookActivity(self.records).recorded_since(moment(0)))


class ReadTest(CursorReaderTestCase):
    def test_records_join_by_generation_id_subagents_per_child_conversation_id(self):
        self.write("beforeSubmitPrompt", 0, prompt_input("g1", "first prompt"))
        self.write("afterAgentResponse", 1, response_input("g1", "first reply", **TOKENS))  # repeats the totals of `stop`
        self.write("stop", 2, stop_input("g1", **TOKENS, model_params=[{"id": "effort", "value": "high"}, {"id": "fast", "value": "true"}]))
        self.write("beforeSubmitPrompt", 10, prompt_input("g2", "second prompt"))
        self.write("subagentStop", 5, subagent_input("child-a", subagent_type="explore", duration_ms=2000))
        self.write("subagentStop", 11, subagent_input("child-b", subagent_type="generalPurpose", duration_ms=3000))
        self.write("afterAgentResponse", 12, response_input("g2", "second reply"))
        self.write("stop", 13, stop_input("g2", input_tokens=50, output_tokens=5, model=MODEL))
        session = self.session()
        self.assertEqual((Harness.CURSOR, CONVERSATION, moment(0), WORKSPACE, None), (session.harness, session.session_id, session.started_at,
                                                                                        session.project_dir, session.orphan_of_parent_id))
        fast_high = RequestUsage(model_name=MODEL, input_tokens=600, output_tokens=200, cache_read_tokens=300, cache_write_5m_tokens=100,
                                 cache_write_1h_tokens=0, effort="high", fast=True)
        plain = RequestUsage(model_name=MODEL, input_tokens=50, output_tokens=5, cache_read_tokens=0, cache_write_5m_tokens=0,
                             cache_write_1h_tokens=0)
        self.assertEqual((
            Prompt(submitted_at=moment(0), text="first prompt", reply_at=moment(2), reply_text="first reply",
                   working_time=timedelta(seconds=2), requests=(fast_high,)),
            Prompt(submitted_at=moment(10), text="second prompt", reply_at=moment(13), reply_text="second reply",
                   working_time=timedelta(seconds=3), requests=(plain,)),
        ), session.prompts)
        self.assertEqual((
            SubAgent(agent_id="child-a", type="explore", started_at=moment(3), working_time=timedelta(seconds=2), tokens_not_recorded=True),
            SubAgent(agent_id="child-b", type="generalPurpose", started_at=moment(8), working_time=timedelta(seconds=3), tokens_not_recorded=True),
        ), session.sub_agents)

    def test_a_later_response_without_text_never_erases_the_reply_of_its_generation(self):
        self.write("beforeSubmitPrompt", 0, prompt_input("g1"))
        self.write("afterAgentResponse", 1, response_input("g1", "real final"))
        self.write("afterAgentResponse", 2, response_input("g1", ""))
        self.write("afterAgentResponse", 3, {"conversation_id": CONVERSATION, "generation_id": "g1"})  # no `text` at all
        self.write("afterAgentResponse", 4, response_input("g1", " \n "))
        self.write("stop", 5, stop_input("g1"))
        self.write("beforeSubmitPrompt", 10, prompt_input("g2"))
        self.write("afterAgentResponse", 11, response_input("g2", "interim"))
        self.write("afterAgentResponse", 12, response_input("g2", "final"))
        self.write("stop", 13, stop_input("g2"))
        self.write("beforeSubmitPrompt", 20, prompt_input("g3"))
        self.write("afterAgentResponse", 21, response_input("g3", ""))
        self.write("stop", 22, stop_input("g3"))
        self.assertEqual(["real final", "final", None], [prompt.reply_text for prompt in self.session().prompts])

    def test_cancelled_prompt_lone_stop_duplicates_same_start_order_tmp_ignored_invalid_listed_once(self):
        self.write("beforeSubmitPrompt", 0, prompt_input("g-cancelled", "cancelled prompt"))
        self.write("afterAgentResponse", 1, response_input("g-cancelled", "partial reply", **TOKENS))  # no stop: no reply, no tokens
        self.write("stop", 5, stop_input("g-lone", **TOKENS))
        self.write("beforeSubmitPrompt", 10, prompt_input("g3", "twice"))
        self.write("beforeSubmitPrompt", 10, prompt_input("g3", "twice"), pid=77)
        self.write("stop", 13, stop_input("g3", **TOKENS))
        self.write("stop", 13, stop_input("g3", **TOKENS), pid=77)
        self.write("subagentStop", 16, subagent_input("child-z", subagent_type="plan", duration_ms=0))
        self.write("subagentStop", 18, subagent_input("child-a", subagent_type="explore", duration_ms=2000))
        self.write("subagentStop", 18, subagent_input("child-a", subagent_type="explore", duration_ms=2000), pid=77)
        folder = os.path.join(self.records, CONVERSATION)
        for temp in (".%d-stop-9.json.9.abcd1234.tmp" % at_ns(30), "stale.tmp"):  # two temps, each holding a whole record
            self.write_text_file(os.path.join(folder, temp), json.dumps(record_fields("stop", CONVERSATION, at_ns(30), 9, json.dumps(stop_input("g-temp", **TOKENS)))))
        self.write("stop", 1, {}, conversation=INVALID_FOLDER, raw_input="not json")
        self.write("stop", 2, {}, conversation=INVALID_FOLDER, raw_input="")
        result = self.read()
        self.assertEqual((), result.unreadable)
        self.assertEqual((
            Prompt(submitted_at=moment(0), text="cancelled prompt", working_time=timedelta(0)),
            Prompt(submitted_at=moment(5), text=NO_PROMPT_TEXT, reply_at=moment(5), working_time=timedelta(0), requests=(REQUEST,)),
            Prompt(submitted_at=moment(10), text="twice", reply_at=moment(13), working_time=timedelta(seconds=3), requests=(REQUEST,)),
        ), result.session.prompts)
        self.assertEqual([("child-a", moment(16)), ("child-z", moment(16))], [(agent.agent_id, agent.started_at) for agent in result.session.sub_agents])
        invalid = os.path.join(self.records, INVALID_FOLDER)
        self.assertEqual((UnreadableSource(Harness.CURSOR, invalid, INVALID_CALLS_REASON % 2, hides_sessions=False),), self.reader.scan().unreadable)

    def write_child_run(self, child="child-1", prompt_at=3):
        """What Cursor records for a sub-agent that runs as a conversation of its own: hooks filed under its own conversation id."""
        self.write("beforeSubmitPrompt", prompt_at, prompt_input("g-child", "child task", conversation_id=child), conversation=child)
        self.write("stop", prompt_at + 1, stop_input("g-child", conversation_id=child, **TOKENS), conversation=child)

    def test_a_conversation_the_parent_names_as_a_sub_agent_is_no_session_and_the_parent_keeps_its_row(self):
        self.write("beforeSubmitPrompt", 0, prompt_input("g1", "parent prompt"))
        self.write("stop", 2, stop_input("g1"))
        self.write_child_run()
        self.write("subagentStop", 5, subagent_input("child-1", subagent_type="explore", duration_ms=2000))
        child = self.read("child-1")
        self.assertEqual((None, ()), (child.session, child.unreadable))
        self.assertEqual(["child-1"], [agent.agent_id for agent in self.session().sub_agents])

    def test_a_child_is_a_session_until_its_parent_names_it_and_none_after(self):
        """Nothing says a conversation is a child before the parent's `subagentStop` arrives, so it is read like any conversation then."""
        self.write("beforeSubmitPrompt", 0, prompt_input("g1", "parent prompt"))
        self.write_child_run()
        self.assertEqual(["child task"], [prompt.text for prompt in self.session("child-1").prompts])
        self.write("subagentStop", 5, subagent_input("child-1", subagent_type="explore", duration_ms=2000))
        self.assertIsNone(self.session("child-1"))

    def test_a_subagent_record_that_cannot_be_opened_hides_no_conversation(self):
        self.write("beforeSubmitPrompt", 0, prompt_input("g1", "parent prompt"))
        self.write_child_run()
        denied = self.write("subagentStop", 5, subagent_input("child-1", subagent_type="explore"))
        real_open = cursor_reader.open_text

        def deny(path):
            if path == denied:
                raise PermissionError("denied")
            return real_open(path)

        with mock.patch.object(cursor_reader, "open_text", deny):
            self.assertEqual(["child task"], [prompt.text for prompt in self.session("child-1").prompts])

    def test_a_conversation_that_names_itself_is_still_a_session(self):
        self.write("beforeSubmitPrompt", 0, prompt_input("g1", "prompt"))
        self.write("subagentStop", 5, subagent_input(CONVERSATION, subagent_type="explore"))
        self.assertEqual(["prompt"], [prompt.text for prompt in self.session().prompts])

    def test_new_record_makes_conversation_read_again(self):
        self.write("beforeSubmitPrompt", 0, prompt_input("g1", "first"))
        before = self.reader.scan().units[0]
        self.assertEqual(1, len(self.session().prompts))
        self.write("beforeSubmitPrompt", 4, prompt_input("g2", "second"))
        after = self.reader.scan().units[0]
        self.assertNotEqual(before.fingerprint, after.fingerprint)
        self.assertEqual(after.key, before.key)
        self.assertEqual(["first", "second"], [prompt.text for prompt in self.session().prompts])
        self.assertEqual(moment(4), after.modified_at)

    def test_an_event_recorded_again_later_counts_once_and_its_last_record_wins(self):
        self.write("beforeSubmitPrompt", 0, prompt_input("g1", "first copy"))
        self.write("beforeSubmitPrompt", 4, prompt_input("g1", "second copy"))
        self.write("stop", 6, stop_input("g1", **TOKENS))
        self.write("stop", 9, stop_input("g1", **TOKENS))
        self.write("subagentStop", 12, subagent_input("child", subagent_type="plan", duration_ms=1000))
        self.write("subagentStop", 15, subagent_input("child", subagent_type="explore", duration_ms=2000))
        session = self.session()
        self.assertEqual([("second copy", moment(4), moment(9), (REQUEST,))],
                         [(prompt.text, prompt.submitted_at, prompt.reply_at, prompt.requests) for prompt in session.prompts])
        self.assertEqual([("explore", moment(13))], [(agent.type, agent.started_at) for agent in session.sub_agents])

    def test_a_stop_without_token_counts_adds_no_request_and_one_with_some_counts_the_rest_as_zero(self):
        self.write("beforeSubmitPrompt", 0, prompt_input("g1"))
        self.write("stop", 1, stop_input("g1", model=MODEL))
        self.write("beforeSubmitPrompt", 2, prompt_input("g2"))
        self.write("stop", 3, stop_input("g2", output_tokens=7, input_tokens="12", cache_read_tokens=-1, cache_write_tokens=True))
        first, second = self.session().prompts
        self.assertEqual(((), (RequestUsage(model_name="", input_tokens=0, output_tokens=7, cache_read_tokens=0, cache_write_5m_tokens=0,
                                           cache_write_1h_tokens=0),)), (first.requests, second.requests))

    def test_input_tokens_exclude_the_cache_tokens_they_include_and_never_go_below_zero(self):
        self.write("beforeSubmitPrompt", 0, prompt_input("g1"))
        self.write("stop", 1, stop_input("g1", input_tokens=10, output_tokens=1, cache_read_tokens=8, cache_write_tokens=8, model=MODEL))
        (request,) = self.session().prompts[0].requests
        self.assertEqual((0, 8, 8), (request.input_tokens, request.cache_read_tokens, request.cache_write_5m_tokens))

    def test_effort_and_fast_come_from_the_model_params_of_the_stop(self):
        self.write("beforeSubmitPrompt", 0, prompt_input("g1"))
        self.write("stop", 1, stop_input("g1", output_tokens=1, model_params=[{"id": "fast", "value": "false"}, "junk", {"id": "effort", "value": 3}]))
        (request,) = self.session().prompts[0].requests
        self.assertEqual((None, False), (request.effort, request.fast))

    def test_the_project_is_the_first_workspace_root_of_the_first_record_that_has_one(self):
        self.write("subagentStop", 0, subagent_input("child"))
        self.write("beforeSubmitPrompt", 1, prompt_input("g1", workspace_roots=["/first", "/second"]))
        self.write("beforeSubmitPrompt", 2, prompt_input("g2", workspace_roots=["/later"]))
        self.assertEqual("/first", self.session().project_dir)

    def test_without_a_workspace_root_the_session_has_no_project(self):
        self.write("beforeSubmitPrompt", 0, prompt_input("g1", workspace_roots=[]))
        self.write("beforeSubmitPrompt", 1, prompt_input("g2", workspace_roots="nope"))
        self.assertIsNone(self.session().project_dir)

    def test_the_transcript_paths_are_the_newest_one_the_hooks_named_for_the_session_and_for_each_subagent(self):
        """Cursor puts `transcript_path` on every hook input and `agent_transcript_path` on `subagentStop` (cursor.com/docs/hooks)."""
        self.write("beforeSubmitPrompt", 0, prompt_input("g1", transcript_path="/t/old.jsonl"))
        self.write("stop", 1, stop_input("g1", transcript_path="/t/new.jsonl"))
        self.write("beforeSubmitPrompt", 2, prompt_input("g2", transcript_path=None))
        self.write("stop", 3, stop_input("g2", transcript_path=""))
        self.write("subagentStop", 4, subagent_input("child-a", agent_transcript_path="/t/subagents/child-a.jsonl"))
        self.write("subagentStop", 5, subagent_input("child-b", agent_transcript_path=None))
        self.write("subagentStop", 6, subagent_input("child-c", agent_transcript_path=7))
        session = self.session()
        self.assertEqual("/t/new.jsonl", session.transcript_path)
        self.assertEqual(["/t/subagents/child-a.jsonl", None, None], [agent.transcript_path for agent in session.sub_agents])

    def test_a_session_whose_hooks_named_no_transcript_has_none(self):
        self.write("beforeSubmitPrompt", 0, prompt_input("g1", transcript_path=5))
        self.assertIsNone(self.session().transcript_path)

    def test_a_subagent_without_type_or_a_duration_keeps_its_row_and_an_unsafe_child_id_is_only_shown(self):
        self.write("beforeSubmitPrompt", 0, prompt_input("g1"))
        self.write("subagentStop", 5, subagent_input("../odd id", subagent_type="", duration_ms="soon"))
        self.write("subagentStop", 7, subagent_input("", subagent_type="x"))  # no id to join by
        self.write("subagentStop", 8, subagent_input(42, subagent_type="x"))
        (agent,) = self.session().sub_agents
        self.assertEqual(("../odd id", "unknown", moment(5), None, True),
                         (agent.agent_id, agent.type, agent.started_at, agent.working_time, agent.tokens_not_recorded))

    def test_a_duration_no_time_can_hold_or_that_reaches_back_past_the_clock_is_not_recorded_and_the_subagent_starts_at_its_arrival(self):
        self.write("subagentStop", 5, subagent_input("reaches-back", duration_ms=10 ** 15))
        self.write("subagentStop", 6, subagent_input("too-long", duration_ms=10 ** 30))
        reaches_back, too_long = self.session().sub_agents
        self.assertEqual(("reaches-back", moment(5), None), (reaches_back.agent_id, reaches_back.started_at, reaches_back.working_time))
        self.assertEqual(("too-long", moment(6), None), (too_long.agent_id, too_long.started_at, too_long.working_time))
        self.assertEqual(moment(6), self.session().last_activity_at)

    def test_a_conversation_with_only_a_response_has_no_prompt_and_a_stop_before_its_prompt_works_no_time(self):
        self.write("afterAgentResponse", 0, response_input("g0"))
        self.write("stop", 1, stop_input("g1", **TOKENS))
        self.write("beforeSubmitPrompt", 4, prompt_input("g1", "late prompt"))
        (prompt,) = self.session().prompts
        self.assertEqual(("late prompt", moment(4), moment(4), timedelta(0)), (prompt.text, prompt.submitted_at, prompt.reply_at, prompt.working_time))

    def test_a_lone_stop_keeps_the_reply_text_of_its_response(self):
        self.write("afterAgentResponse", 1, response_input("g1", "orphan reply"))
        self.write("stop", 2, stop_input("g1"))
        (prompt,) = self.session().prompts
        self.assertEqual((NO_PROMPT_TEXT, "orphan reply"), (prompt.text, prompt.reply_text))

    def test_records_in_any_order_of_arrival_names_give_the_same_session(self):
        self.write("stop", 2, stop_input("g1", **TOKENS))
        self.write("beforeSubmitPrompt", 0, prompt_input("g1", "p"))
        (prompt,) = self.session().prompts
        self.assertEqual((moment(0), moment(2)), (prompt.submitted_at, prompt.reply_at))

    def test_a_record_that_is_no_record_is_skipped_and_the_others_are_read(self):
        good = self.write("beforeSubmitPrompt", 0, prompt_input("g1", "kept"))
        folder = os.path.dirname(good)
        self.write_text_file(os.path.join(folder, "%d-stop-1.json" % at_ns(1)), "\xff not json")
        with open(os.path.join(folder, "%d-stop-3.json" % at_ns(3)), "wb") as stream:
            stream.write(b"\xff\xfe\x00")  # not UTF-8
        self.write_text_file(os.path.join(folder, "%d-stop-4.json" % at_ns(4)), "[1, 2]")
        self.write_text_file(os.path.join(folder, "%d-bogus-5.json" % at_ns(5)), '{"event": "bogus", "receivedAtNs": 1, "rawInput": "{}"}')
        self.write_text_file(os.path.join(folder, "%d-stop-6.json" % at_ns(6)), '{"event": "stop", "receivedAtNs": "x", "rawInput": "{}"}')
        self.write_text_file(os.path.join(folder, "%d-stop-7.json" % at_ns(7)), '{"event": "stop", "receivedAtNs": %d, "rawInput": 5}' % at_ns(7))
        self.write_text_file(os.path.join(folder, "%d-stop-8.json" % at_ns(8)), '{"event": "stop", "receivedAtNs": %d, "rawInput": "{bad"}' % at_ns(8))
        self.write_text_file(os.path.join(folder, "%d-stop-9.json" % at_ns(9)), '{"event": "stop", "receivedAtNs": %d, "rawInput": "[]"}' % at_ns(9))
        (prompt,) = self.session().prompts
        self.assertEqual("kept", prompt.text)

    def test_a_conversation_whose_records_are_all_unusable_is_unreadable_as_not_parsed(self):
        folder = os.path.join(self.records, CONVERSATION)
        self.write_text_file(os.path.join(folder, "%d-stop-1.json" % at_ns(1)), "junk")
        result = self.read()
        self.assertEqual((None, (UnreadableSource(Harness.CURSOR, folder, CANNOT_BE_PARSED_REASON),)), (result.session, result.unreadable))

    def test_a_record_name_with_an_arrival_no_datetime_holds_is_the_newest_and_one_with_too_many_digits_is_no_record(self):
        self.write("beforeSubmitPrompt", 0, prompt_input("g1"))
        folder = os.path.join(self.records, CONVERSATION)
        self.write_text_file(os.path.join(folder, "%s-stop-1.json" % ("9" * 30)), "{}")
        self.write_text_file(os.path.join(folder, "%s-stop-1.json" % ("9" * 31)), "{}")
        (unit,) = self.reader.scan().units
        self.assertEqual(("2:%s-stop-1.json" % ("9" * 30), datetime.max.replace(tzinfo=timezone.utc)), (unit.fingerprint, unit.modified_at))

    def test_a_record_that_cannot_be_opened_makes_the_whole_conversation_unreadable_under_its_folder(self):
        first = self.write("beforeSubmitPrompt", 0, prompt_input("g1"))
        second = self.write("stop", 1, stop_input("g1"))
        real_open = cursor_reader.open_text

        def deny(path):
            if path == second:
                raise PermissionError("denied")
            return real_open(path)

        with mock.patch.object(cursor_reader, "open_text", deny):
            result = self.read()
        self.assertEqual((None, (UnreadableSource(Harness.CURSOR, os.path.dirname(first), CANNOT_BE_OPENED_REASON),)), (result.session, result.unreadable))

    def test_a_folder_that_cannot_be_listed_at_read_time_is_unreadable_under_its_path(self):
        folder = os.path.dirname(self.write("stop", 1, stop_input("g1")))
        scan = self.reader.scan()
        with scandir_wrapping(fail={folder: PermissionError("denied")}):
            result = self.reader.read(key_of(), scan.units)
        self.assertEqual((None, (UnreadableSource(Harness.CURSOR, folder, CANNOT_BE_LISTED_REASON),)), (result.session, result.unreadable))

    def test_a_folder_that_vanished_before_the_read_gives_no_session(self):
        folder = os.path.dirname(self.write("stop", 1, stop_input("g1")))
        scan = self.reader.scan()
        shutil.rmtree(folder)
        result = self.reader.read(key_of(), scan.units)
        self.assertEqual((None, ()), (result.session, result.unreadable))

    def test_an_unsafe_conversation_id_is_listed_unreadable_and_never_read(self):
        unit = SourceUnit(key=os.path.join(self.records, "a.b"), fingerprint="1:x", modified_at=moment(0))
        self.assertEqual("cursor:a.b", self.reader.session_of(unit))
        result = self.reader.read("cursor:a.b", (unit,))
        self.assertEqual((None, (UnreadableSource(Harness.CURSOR, unit.key, UNSAFE_ID_REASON),)), (result.session, result.unreadable))

    def test_a_key_of_another_harness_or_without_units_reads_nothing(self):
        self.write("stop", 1, stop_input("g1"))
        units = self.reader.scan().units
        for key, given in (("codex:" + CONVERSATION, units), ("cursor:", units), (key_of(), ()), (key_of("other"), units)):
            with self.subTest(key=key):
                result = self.reader.read(key, given)
                self.assertEqual((None, ()), (result.session, result.unreadable))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs FIFOs")
    def test_a_fifo_named_like_a_record_is_listed_unreadable_and_never_opened(self):
        self.write("beforeSubmitPrompt", 0, prompt_input("g1", "kept"))
        fifo = os.path.join(self.records, CONVERSATION, "%d-stop-1.json" % at_ns(1))
        os.mkfifo(fifo)
        scan = self.reader.scan()
        self.assertEqual(("1", [UnreadableSource(Harness.CURSOR, fifo, NOT_A_REGULAR_FILE_REASON)]),
                         (scan.units[0].fingerprint.split(":")[0], list(scan.unreadable)))
        (prompt,) = self.reader.read(key_of(), scan.units).session.prompts
        self.assertEqual("kept", prompt.text)

    def test_the_record_text_is_read_as_utf8_with_a_byte_order_mark_dropped(self):
        path = self.write("beforeSubmitPrompt", 0, prompt_input("g1", "h\u00e9llo \u2265 \U0001f600"))
        with open(path, "rb") as stream:
            content = stream.read()
        with open(path, "wb") as stream:
            stream.write(b"\xef\xbb\xbf" + content)
        (prompt,) = self.session().prompts
        self.assertEqual("h\u00e9llo \u2265 \U0001f600", prompt.text)


if __name__ == "__main__":
    unittest.main()
