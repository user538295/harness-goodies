"""Codex sub-agents (BE-25; S8, S13, S43): child rollouts nest under their parent thread, the root in `archived_sessions/` is found, an
active child makes its parent archived whole, an old child is linked from its first line, an orphan child is archived alone and labelled,
and a child whose parent cannot be read is unreadable with it. Pure nesting rules, the reader over files, and sync end to end."""
import json
import os
import re
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from sessionlog.codex_reader import PARENT_UNREADABLE_REASON, CodexReader
from sessionlog.codex_rollout import Rollout, RolloutMeta
from sessionlog.codex_subagents import Thread, copy_rank, nest_threads, split_orphan_family, thread_of_rollout
from sessionlog.log_usage_writer import LogUsageWriter
from sessionlog.model import Harness, Prompt, RequestUsage
from sessionlog.pricing import PricingRules
from sessionlog.reader_port import CANNOT_BE_OPENED_REASON, UnreadableSource
from sessionlog.sync import UNKNOWN_SESSION_REASON
from tests.store_fakes import FakeEntry, scandir_wrapping
from tests.test_claude_subagents import _Book
from tests.test_codex_entry import CodexEntryTestCase
from tests.test_codex_reader import CWD, ROOT_FIXTURE, CodexReaderTestCase
from tests.test_codex_reader import ROOT_ID as ROOT_FIXTURE_ID

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "codex")
CHILD_FIXTURE = os.path.join(FIXTURES, "forked_child_with_inherited_total.jsonl")
COUNT_ONLY_FIXTURE = os.path.join(FIXTURES, "root_with_token_count_only.jsonl")
COUNT_ONLY_ID = "1a100001-0000-7000-8000-000000000001"
CHILD_FIXTURE_ROOT_ID = "1a200001-0000-7000-8000-000000000001"
ROOT = "01a00000-0000-7000-8000-000000000001"
CHILD = "01a00000-0000-7000-8000-000000000002"
GRAND = "01a00000-0000-7000-8000-000000000003"
SECOND = "01a00000-0000-7000-8000-000000000004"
OLD_ROOT = "01a00000-0000-7000-8000-000000000005"
OLD_CHILD = "01a00000-0000-7000-8000-000000000006"
MISSING = "01a00000-0000-7000-8000-0000000000ff"
BASE = datetime(2026, 9, 11, 8, 0, 0, tzinfo=timezone.utc)
FILE_TIME = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)
ONE_SECOND = timedelta(seconds=1)
EST_LINE = re.compile(r"est\. used token: input: (\d+), output: (\d+), cache_create: (\d+), cache_read: (\d+), total_tokens: (\d+)")
ONE_SECOND_NS = 1_000_000_000
FILE_SECOND = 1_700_000_000  # a file time (2023-11-14 22:13:20 UTC) that no record of a test rollout gives
# The sums the sync test expects come from the raw usage fields of the anonymized fixtures (summed or differenced once with a
# scratch script); every total below is (input including cached input, cached input, output).
ROOT_FIXTURE_TOTAL = (688467, 559744, 9814)  # `root_with_token_usage_records.jsonl`: the sum of its 7 `token_usage_record` usages
COUNT_ONLY_FINAL_TOTAL = (46553, 34176, 1373)  # `root_with_token_count_only.jsonl`: its last `token_count` total, none comes before its first `task_started`
# `forked_child_with_inherited_total.jsonl`: the total of its `token_count` before the first `task_started` is inherited from the parent
# and its last total is the end of its own work, so its own tokens are the growth between the two.
FORK_INHERITED_TOTAL = (8170838, 7931904, 38984)
FORK_FINAL_TOTAL = (8724643, 8390016, 44369)
# Usages the test writes itself, (input, output) and (input, output, cached input).
PARENT_USED = (20, 2)
CHILD_USED = (700, 70)
CHILD_A_USED = ((1000, 100, 200), (500, 50, 0))


def _stamp(at) -> str:
    """`at` is a datetime or a number of seconds after BASE."""
    moment = at if isinstance(at, datetime) else BASE + timedelta(seconds=at)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _text(records) -> str:
    return "".join(json.dumps(record) + "\n" for record in records)


def _meta(thread, root, parent, start, **payload) -> dict:
    fields = {"id": thread, "session_id": root, "timestamp": _stamp(start), "cwd": CWD}
    if parent is not None:
        fields["parent_thread_id"] = parent
    return {"timestamp": _stamp(start), "type": "session_meta", "payload": {**fields, **payload}}


def _context(at, model="gpt-x") -> dict:
    return {"timestamp": _stamp(at), "type": "turn_context", "payload": {"model": model}}


def _prompt(at, text) -> dict:
    return {"timestamp": _stamp(at), "type": "event_msg",
            "payload": {"type": "item_completed", "item": {"type": "UserMessage", "content": [{"type": "text", "text": text}]}}}


def _used(at, input_tokens, output_tokens, cached=0) -> dict:
    usage = {"input_tokens": input_tokens, "cached_input_tokens": cached, "cache_write_input_tokens": 0, "output_tokens": output_tokens}
    return {"timestamp": _stamp(at), "type": "token_usage_record", "payload": {"usage": usage}}


def _rollout(thread, root=ROOT, parent=None, start=0, records=(), **payload) -> str:
    return _text([_meta(thread, root, parent, start, **payload), _context(start)] + list(records))


def _name(thread) -> str:
    return "rollout-2026-09-11T10-00-00-%s.jsonl" % thread


def _asked(text) -> Prompt:
    return Prompt(submitted_at=BASE, text=text)


def _agent(agent_id, start=0, parent=None, requests=(), prompts=(), project_dir=None) -> Thread:
    return Thread(thread_id=agent_id, parent_thread_id=parent, agent_type="worker", started_at=BASE + timedelta(seconds=start),
                  working_time=ONE_SECOND, requests=tuple(requests), prompts=tuple(prompts), project_dir=project_dir)


def _request(input_tokens) -> RequestUsage:
    return RequestUsage("gpt-x", input_tokens, 0, 0, 0, 0)


def _report_numbers(final, inherited=(0, 0, 0)) -> tuple:
    """The (input, output, cache_create, cache_read, total) of an est. line for what a total grew by since `inherited`; a total is
    (input including cached, cached input, output) as the rollout writes it, and the report lists input without cache reads."""
    uncached = (final[0] - final[1]) - (inherited[0] - inherited[1])
    cached = final[1] - inherited[1]
    output = final[2] - inherited[2]
    return uncached, output, 0, cached, uncached + output + cached


def _report_lines(session) -> list:
    writer = LogUsageWriter("/logs", lambda moment: moment)
    return writer.render_report(PricingRules(_Book()).build(session)).splitlines()


class NestingTest(unittest.TestCase):
    def test_threads_nest_under_the_thread_their_parent_thread_id_names_and_are_ordered_by_start_then_id(self):
        threads = [_agent("g1", 5, parent="c1"), _agent("c2", 3, parent=ROOT), _agent("c1", 3, parent=ROOT), _agent("g0", 4, parent="c1")]
        top = nest_threads(threads)
        self.assertEqual(["c1", "c2"], [agent.agent_id for agent in top])
        self.assertEqual({"g0", "g1"}, {agent.agent_id for agent in top[0].sub_agents})  # a SubAgent keeps its children by start, then id itself
        self.assertEqual(((), False), (top[1].sub_agents, top[1].tokens_not_recorded))

    def test_a_thread_whose_parent_is_not_in_the_session_or_is_itself_or_sits_in_a_parent_loop_is_at_the_top_level(self):
        threads = [_agent("a", 1, parent="gone"), _agent("self", 2, parent="self"), _agent("x", 3, parent="y"), _agent("y", 4, parent="x"),
                   _agent("below", 5, parent="x"), _agent("none", 6)]
        top = nest_threads(threads)
        self.assertEqual(["a", "self", "x", "y", "none"], [agent.agent_id for agent in top])
        self.assertEqual(["below"], [agent.agent_id for agent in top[2].sub_agents])

    def test_an_agent_carries_its_type_start_working_time_and_requests_and_the_type_defaults_to_unknown(self):
        typed = _agent("t", 7, requests=[_request(1), _request(2)])
        untyped = Thread("u", None, None, BASE, ONE_SECOND, ())
        (first, second) = nest_threads([typed, untyped])
        self.assertEqual(("u", "unknown"), (first.agent_id, first.type))
        self.assertEqual(("t", "worker", BASE + 7 * ONE_SECOND, ONE_SECOND, [1, 2]),
                         (second.agent_id, second.type, second.started_at, second.working_time, [r.input_tokens for r in second.requests]))

    def test_no_threads_no_agents(self):
        self.assertEqual((), nest_threads([]))

    def test_a_chain_of_three_levels_nests_each_thread_under_the_one_above(self):
        top = nest_threads([_agent("c", 3, parent="b"), _agent("a", 1, parent=ROOT), _agent("b", 2, parent="a")])
        (a,) = top
        (b,) = a.sub_agents
        (c,) = b.sub_agents
        self.assertEqual((["a", "b", "c"], ()), ([a.agent_id, b.agent_id, c.agent_id], c.sub_agents))


def _header(started_at=BASE, parent=ROOT) -> RolloutMeta:
    return RolloutMeta(root_id=ROOT, thread_id=CHILD, parent_thread_id=parent, agent_type="worker", forked=True, started_at=started_at,
                       project_dir="/child")


def _parsed(meta, first=None, last=None, requests=(), prompts=()) -> Rollout:
    return Rollout(meta=meta, prompts=tuple(prompts), unparsable=False, requests=tuple(requests), last_moment=last, first_moment=first)


class ThreadOfRolloutTest(unittest.TestCase):
    """`thread_of_rollout` on parsed rollouts: no file is involved."""

    def test_a_thread_carries_the_ids_type_folder_prompts_and_requests_of_its_rollout(self):
        rollout = _parsed(_header(), first=BASE + 5 * ONE_SECOND, last=BASE + 9 * ONE_SECOND, requests=[_request(1)], prompts=[_asked("ask")])
        thread = thread_of_rollout(rollout, FILE_TIME)
        self.assertEqual((CHILD, ROOT, "worker", "/child", (_asked("ask"),), (_request(1),)),
                         (thread.thread_id, thread.parent_thread_id, thread.agent_type, thread.project_dir, thread.prompts, thread.requests))

    def test_a_thread_starts_with_its_first_record_after_the_header_and_works_until_its_newest_record(self):
        thread = thread_of_rollout(_parsed(_header(BASE + 20 * ONE_SECOND), first=BASE + 140 * ONE_SECOND, last=BASE + 150 * ONE_SECOND), FILE_TIME)
        self.assertEqual((BASE + 140 * ONE_SECOND, 10 * ONE_SECOND), (thread.started_at, thread.working_time))

    def test_a_thread_with_no_record_time_after_its_header_starts_at_the_header_and_works_no_time_even_when_the_header_line_is_later(self):
        header_line_time = BASE + 100 * ONE_SECOND  # the newest time of any record, here only the header's own line
        thread = thread_of_rollout(_parsed(_header(BASE + 50 * ONE_SECOND), last=header_line_time), FILE_TIME)
        self.assertEqual((BASE + 50 * ONE_SECOND, timedelta(0)), (thread.started_at, thread.working_time))

    def test_a_header_without_a_time_gives_the_file_time_when_the_rollout_has_requests_else_no_thread(self):
        thread = thread_of_rollout(_parsed(_header(None), requests=[_request(1)]), FILE_TIME)
        self.assertEqual((FILE_TIME, timedelta(0)), (thread.started_at, thread.working_time))
        self.assertIsNone(thread_of_rollout(_parsed(_header(None)), FILE_TIME))

    def test_a_header_without_a_time_does_not_hide_the_first_record_time(self):
        thread = thread_of_rollout(_parsed(_header(None), first=BASE, last=BASE + ONE_SECOND), FILE_TIME)
        self.assertEqual((BASE, ONE_SECOND), (thread.started_at, thread.working_time))

    def test_a_rollout_without_a_header_makes_no_thread(self):
        self.assertIsNone(thread_of_rollout(_parsed(None, first=BASE, last=BASE), FILE_TIME))


class CopyRankTest(unittest.TestCase):
    """`copy_rank` on keys and header starts: no file is involved."""

    def test_the_latest_header_start_ranks_highest_whatever_the_name(self):
        self.assertGreater(copy_rank(BASE + ONE_SECOND, "/h/sessions/rollout-a-X.jsonl"), copy_rank(BASE, "/h/sessions/rollout-b-X.jsonl"))

    def test_a_missing_start_ranks_under_every_real_start_and_two_missing_starts_go_by_name(self):
        self.assertGreater(copy_rank(BASE, "/h/sessions/rollout-a-X.jsonl"), copy_rank(None, "/h/sessions/rollout-b-X.jsonl"))
        self.assertGreater(copy_rank(None, "/h/sessions/rollout-b-X.jsonl"), copy_rank(None, "/h/sessions/rollout-a-X.jsonl"))

    def test_equal_starts_go_by_the_file_name_before_the_folder(self):
        in_archived = "/h/archived_sessions/rollout-b-X.jsonl"  # a smaller whole key than the one in `sessions`, a greater name
        in_sessions = "/h/sessions/rollout-a-X.jsonl"
        self.assertLess(in_archived, in_sessions)
        self.assertGreater(copy_rank(BASE, in_archived), copy_rank(BASE, in_sessions))

    def test_equal_starts_and_names_go_by_the_whole_key(self):
        self.assertGreater(copy_rank(BASE, "/h/sessions/b/rollout-X.jsonl"), copy_rank(BASE, "/h/sessions/a/rollout-X.jsonl"))


class OrphanFamilySplitTest(unittest.TestCase):
    """`split_orphan_family` on plain threads: no file is involved."""

    def test_the_first_top_level_thread_with_a_prompt_is_the_session_its_children_stand_beside_its_siblings_and_their_subtrees_stay_nested(self):
        threads = [_agent("a", 2, parent=MISSING), _agent("b", 3, parent=MISSING, prompts=[_asked("b ask")], project_dir="/b"),
                   _agent("c", 4, parent="b"), _agent("d", 5, parent=MISSING, prompts=[_asked("d ask")]), _agent("e", 6, parent="c")]
        family = split_orphan_family(ROOT, threads)
        self.assertEqual(((_asked("b ask"),), "/b"), (family.prompts, family.project_dir))  # only the first one with a prompt is promoted
        self.assertEqual({"a", "c", "d"}, {agent.agent_id for agent in family.sub_agents})  # the session keeps them by start itself
        (c,) = [agent for agent in family.sub_agents if agent.agent_id == "c"]
        self.assertEqual(["e"], [agent.agent_id for agent in c.sub_agents])

    def test_the_session_starts_with_the_earliest_top_level_thread_and_takes_the_folder_of_the_promoted_one(self):
        threads = [_agent("a", 2, parent=MISSING, project_dir="/a"), _agent("b", 3, parent=MISSING, prompts=[_asked("b")], project_dir="/b"),
                   _agent("c", 1, parent="b")]  # a nested thread's start does not count
        family = split_orphan_family(ROOT, threads)
        self.assertEqual((BASE + 2 * ONE_SECOND, "/b"), (family.started_at, family.project_dir))

    def test_the_folder_is_the_promoted_threads_else_the_first_top_level_thread_that_has_one(self):
        threads = [_agent("a", 1, parent=MISSING), _agent("b", 2, parent=MISSING, prompts=[_asked("b")]),
                   _agent("c", 3, parent=MISSING, project_dir="/c"), _agent("d", 4, parent=MISSING, project_dir="/d")]
        self.assertEqual("/c", split_orphan_family(ROOT, threads).project_dir)  # the promoted `b` has none: the first sibling with one
        no_prompt = [_agent("a", 1, parent=MISSING), _agent("b", 2, parent=MISSING, project_dir="/b")]
        self.assertEqual("/b", split_orphan_family(ROOT, no_prompt).project_dir)  # nothing is promoted: the first thread that has one

    def test_top_level_threads_are_taken_by_start_then_id(self):
        threads = [_agent("y", 1, parent=MISSING, prompts=[_asked("y")]), _agent("x", 1, parent=MISSING, prompts=[_asked("x")]),
                   _agent("early", 0, parent=MISSING)]
        family = split_orphan_family(ROOT, threads)
        self.assertEqual(((_asked("x"),), ["early", "y"]), (family.prompts, [agent.agent_id for agent in family.sub_agents]))

    def test_a_family_without_a_prompt_has_none_and_keeps_every_top_level_thread_as_a_sub_agent_the_first_giving_folder_and_label(self):
        threads = [_agent("b", 3, parent=ROOT, requests=[_request(2)]), _agent("a", 2, parent=MISSING, requests=[_request(1)], project_dir="/a"),
                   _agent("c", 4, parent="b", requests=[_request(3)])]
        family = split_orphan_family(ROOT, threads)
        self.assertEqual(("/a", MISSING, (), BASE + 2 * ONE_SECOND), (family.project_dir, family.orphan_of_parent_id, family.prompts, family.started_at))
        self.assertEqual(["a", "b"], [agent.agent_id for agent in family.sub_agents])
        self.assertEqual(["c"], [agent.agent_id for agent in family.sub_agents[1].sub_agents])

    def test_a_chain_of_three_levels_under_the_promoted_thread_stays_nested(self):
        threads = [_agent("c", 3, parent="b"), _agent("a", 1, parent=MISSING, prompts=[_asked("a")]), _agent("b", 2, parent="a")]
        family = split_orphan_family(ROOT, threads)
        (b,) = family.sub_agents
        (c,) = b.sub_agents
        self.assertEqual((MISSING, ["b", "c"], ()), (family.orphan_of_parent_id, [b.agent_id, c.agent_id], c.sub_agents))

    def test_the_label_is_the_parent_of_the_promoted_thread_unless_it_names_no_parent_or_a_thread_of_the_family(self):
        loop = [_agent("a", 1, parent="b", prompts=[_asked("a")]), _agent("b", 2, parent="c", prompts=[_asked("b")]),
                _agent("c", 3, parent="a", prompts=[_asked("c")])]
        cases = (("no parent", [_agent("a", prompts=[_asked("a")])], ROOT), ("a parent that is gone", [_agent("a", parent=MISSING)], MISSING),
                 ("the root", [_agent("a", parent=ROOT)], ROOT), ("itself", [_agent("a", parent="a", prompts=[_asked("a")])], ROOT),
                 ("a loop of two", [_agent("x", 1, parent="y", prompts=[_asked("x")]), _agent("y", 2, parent="x", prompts=[_asked("y")])], ROOT),
                 ("a loop of three", loop, ROOT),
                 ("the promoted thread's parent, not the first thread's",
                  [_agent("a", 2, parent=MISSING), _agent("b", 3, parent="other", prompts=[_asked("b")])], "other"))
        for case, threads, label in cases:
            with self.subTest(case):
                self.assertEqual(label, split_orphan_family(ROOT, threads).orphan_of_parent_id)

    def test_every_thread_of_a_loop_is_top_level_so_the_rest_of_it_becomes_sub_agents_of_the_promoted_one(self):
        loop = [_agent("a", 1, parent="b", prompts=[_asked("a")]), _agent("b", 2, parent="c"), _agent("c", 3, parent="a")]
        family = split_orphan_family(ROOT, loop)
        self.assertEqual((["b", "c"], [(), ()]), ([agent.agent_id for agent in family.sub_agents], [agent.sub_agents for agent in family.sub_agents]))

    def test_no_threads_no_family(self):
        self.assertIsNone(split_orphan_family(ROOT, []))


class _FamilyReaderTestCase(CodexReaderTestCase):
    """A store whose rollouts are written by thread id, and the session read from it."""

    def _put(self, thread, **kwargs):
        return self._write(_name(thread), _rollout(thread, **kwargs))

    def _read(self, root=ROOT):
        return self.reader.read("codex:" + root, self.reader.scan().units)


class CodexSubAgentReadTest(_FamilyReaderTestCase):
    def test_children_and_grandchild_nest_as_subagents_of_root_and_are_in_total(self):
        self._put(ROOT, records=[_prompt(10, "ask root"), _used(11, 100, 10)])
        self._put(CHILD, parent=ROOT, start=20, agent_role="explorer", records=[_used(21, 200, 20), _used(25, 300, 30)])
        self._put(SECOND, parent=ROOT, start=30, agent_role="worker", records=[_used(31, 50, 5)])
        self._put(GRAND, parent=CHILD, start=22, agent_role="reviewer", records=[_used(23, 7, 1)])  # `session_id` still names the root
        result = self._read()
        session = result.session
        self.assertEqual(((), ROOT, None, ["ask root"]), (result.unreadable, session.session_id, session.orphan_of_parent_id,
                                                          [prompt.text for prompt in session.prompts]))
        child, second = session.sub_agents
        (grand,) = child.sub_agents
        self.assertEqual([(CHILD, "explorer"), (SECOND, "worker"), (GRAND, "reviewer")],
                         [(child.agent_id, child.type), (second.agent_id, second.type), (grand.agent_id, grand.type)])
        self.assertEqual(((), ()), (second.sub_agents, grand.sub_agents))
        self.assertEqual(([200, 300], BASE + 20 * ONE_SECOND, 5 * ONE_SECOND),
                         ([request.input_tokens for request in child.requests], child.started_at, child.working_time))
        report = _report_lines(session)
        self.assertIn("TOTAL (1 requests, 3 sub-agents)", report)
        total_line = [line for line in report if line.startswith("est. used token:")][-1]
        self.assertTrue(total_line.startswith("est. used token: input: 657, output: 66, cache_create: 0, cache_read: 0, total_tokens: 723,"), total_line)

    def test_a_sub_agent_without_any_prompt_keeps_its_tokens_and_the_session_activity_follows_its_last_record(self):
        self._put(ROOT, records=[_prompt(10, "ask"), _used(11, 1, 1)])
        self._put(CHILD, parent=ROOT, start=20, records=[_used(21, 5, 1), {"timestamp": _stamp(400), "type": "future_record", "payload": {}}])
        session = self._read().session
        (child,) = session.sub_agents
        self.assertEqual(([5], 380 * ONE_SECOND, "unknown"), ([r.input_tokens for r in child.requests], child.working_time, child.type))
        self.assertEqual(BASE + 400 * ONE_SECOND, session.last_activity_at)

    def test_a_grandchild_whose_parent_is_not_in_the_session_sits_at_the_top_level(self):
        self._put(ROOT, records=[_prompt(10, "ask")])
        self._put(GRAND, parent=MISSING, start=22, records=[_used(23, 7, 1)])
        session = self._read().session
        self.assertEqual([GRAND], [agent.agent_id for agent in session.sub_agents])
        self.assertIsNone(session.orphan_of_parent_id)

    def test_a_sub_agent_without_a_start_time_takes_the_file_time_when_it_has_requests_and_has_no_row_otherwise(self):
        def untimed(thread, records):
            meta = _meta(thread, ROOT, ROOT, 0, timestamp="not a time")
            del meta["timestamp"]
            return self._write(_name(thread), _text([meta] + records))

        self._put(ROOT, records=[_prompt(10, "ask")])
        timed = untimed(CHILD, [{"type": "token_usage_record", "payload": {"usage": {"input_tokens": 9}}}])
        untimed(SECOND, [])
        os.utime(timed, ns=(0, FILE_SECOND * ONE_SECOND_NS))
        (child,) = self._read().session.sub_agents
        self.assertEqual((CHILD, datetime.fromtimestamp(FILE_SECOND, timezone.utc), timedelta(0)),
                         (child.agent_id, child.started_at, child.working_time))

    def test_two_rollouts_of_one_thread_are_one_sub_agent_the_one_with_the_newest_header_time_whatever_the_file_times(self):
        self._put(ROOT, records=[_prompt(10, "ask")])
        edited = self._write("rollout-a-%s_01a0aaaa.jsonl" % CHILD, _rollout(CHILD, parent=ROOT, start=30, records=[_used(31, 2, 1)]))  # newest header, smallest key
        original = self._write("rollout-b-%s.jsonl" % CHILD, _rollout(CHILD, parent=ROOT, start=20, records=[_used(21, 1, 1)]))
        for original_second in (1, 9):  # a touched or restored older file must not take over
            with self.subTest(original_file_second=original_second):
                os.utime(edited, ns=(0, 5 * ONE_SECOND_NS))
                os.utime(original, ns=(0, original_second * ONE_SECOND_NS))
                (child,) = self._read().session.sub_agents
                self.assertEqual(([2], BASE + 30 * ONE_SECOND), ([request.input_tokens for request in child.requests], child.started_at))

    def test_two_rollouts_of_one_thread_with_the_same_header_time_take_the_greatest_file_name_whatever_the_folder_file_times_and_unit_order(self):
        self._put(ROOT, records=[_prompt(10, "ask")])
        lower_name = self._write("rollout-a-%s.jsonl" % CHILD, _text([_meta(CHILD, ROOT, ROOT, 20), _used(21, 1, 1)]))  # the greater whole key
        greater_name = self._write("rollout-b-%s.jsonl" % CHILD, _text([_meta(CHILD, ROOT, ROOT, 20), _used(21, 2, 1)]), self.archived)
        os.utime(lower_name, ns=(0, 9 * ONE_SECOND_NS))  # and the newest file
        os.utime(greater_name, ns=(0, ONE_SECOND_NS))
        units = self.reader.scan().units
        for ordered in (units, tuple(reversed(units))):
            with self.subTest(reversed=ordered is not units):
                (child,) = self.reader.read("codex:" + ROOT, ordered).session.sub_agents
                self.assertEqual([2], [request.input_tokens for request in child.requests])

    def test_a_rollout_copy_whose_header_has_no_time_is_the_oldest_of_its_thread_whatever_its_name(self):
        self._put(ROOT, records=[_prompt(10, "ask")])
        untimed = _meta(CHILD, ROOT, ROOT, 20)
        del untimed["timestamp"]
        untimed["payload"]["timestamp"] = "not a time"
        self._write("rollout-a-%s.jsonl" % CHILD, _text([_meta(CHILD, ROOT, ROOT, 20), _used(21, 1, 1)]))
        self._write("rollout-b-%s.jsonl" % CHILD, _text([untimed, _used(21, 2, 1)]))  # the greater name
        (child,) = self._read().session.sub_agents
        self.assertEqual([1], [request.input_tokens for request in child.requests])

    def test_a_unit_of_another_session_is_not_read_into_the_session(self):
        self._put(ROOT, records=[_prompt(10, "ask")])
        self._put(CHILD, root=SECOND, parent=SECOND, start=20, records=[_used(21, 1, 1)])
        self.assertEqual((), self._read().session.sub_agents)

    def test_the_root_in_archived_sessions_is_found_with_children_in_sessions(self):
        self._write(_name(ROOT), _rollout(ROOT, records=[_prompt(10, "ask root")]), self.archived)
        self._put(CHILD, parent=ROOT, start=20, records=[_used(21, 3, 1)])
        session = self._read().session
        self.assertEqual((["ask root"], [CHILD]), ([p.text for p in session.prompts], [agent.agent_id for agent in session.sub_agents]))

    def test_read_opens_every_unit_of_the_session_once(self):
        paths = [self._put(ROOT, records=[_prompt(10, "ask")]), self._put(CHILD, parent=ROOT, start=20, records=[_used(21, 3, 1)]),
                 self._put(GRAND, parent=CHILD, start=22, records=[_used(23, 3, 1)])]
        opened = []
        real_open = open

        def counting(name, *args, **kwargs):
            opened.append(name)
            return real_open(name, *args, **kwargs)

        with mock.patch("sessionlog.store_files.open", counting, create=True):
            self._read()
        self.assertEqual(sorted(paths), sorted(opened))

    def test_a_sub_agent_that_cannot_be_read_is_listed_and_the_rest_of_the_session_is_built(self):
        self._put(ROOT, records=[_prompt(10, "ask")])
        gone = self._put(CHILD, parent=ROOT, start=20, records=[_used(21, 3, 1)])
        units = self.reader.scan().units
        os.remove(gone)
        result = self.reader.read("codex:" + ROOT, units)
        self.assertEqual((ROOT, (), (UnreadableSource(Harness.CODEX, gone, CANNOT_BE_OPENED_REASON),)),
                         (result.session.session_id, result.session.sub_agents, result.unreadable))

    def _child_text(self, thread, header_at, records, parent=ROOT):
        """A child with no record at its header time: its work begins later."""
        return _text([_meta(thread, ROOT, parent, header_at)] + records)

    def test_a_sub_agent_starts_with_its_first_record_not_with_its_header_when_real_work_begins_later(self):
        self._put(ROOT, records=[_prompt(10, "ask")])
        self._write(_name(CHILD), self._child_text(CHILD, 20, [_used(150, 6, 1), _used(140, 5, 1)]))
        session = self._read().session
        (child,) = session.sub_agents
        self.assertEqual((BASE + 140 * ONE_SECOND, 10 * ONE_SECOND, BASE), (child.started_at, child.working_time, session.started_at))

    def test_sub_agents_are_ordered_by_the_start_of_their_work_not_by_their_header_time(self):
        self._put(ROOT, records=[_prompt(10, "ask")])
        self._write(_name(CHILD), self._child_text(CHILD, 0, [_used(300, 1, 1)]))
        self._write(_name(SECOND), self._child_text(SECOND, 100, [_used(120, 1, 1)]))
        self.assertEqual([SECOND, CHILD], [agent.agent_id for agent in self._read().session.sub_agents])

    def test_a_sub_agent_with_no_record_time_after_its_header_starts_at_the_header_and_works_no_time(self):
        self._put(ROOT, records=[_prompt(10, "ask")])
        self._write(_name(CHILD), self._child_text(CHILD, 40, []))
        (child,) = self._read().session.sub_agents
        self.assertEqual((BASE + 40 * ONE_SECOND, timedelta(0)), (child.started_at, child.working_time))

    def test_the_working_time_is_never_negative_when_the_header_time_is_later_than_every_record(self):
        self._put(ROOT, records=[_prompt(10, "ask")])
        self._write(_name(CHILD), _text([_meta(CHILD, ROOT, ROOT, 50, timestamp=_stamp(100))]))  # the payload says 100, the line itself 50
        (child,) = self._read().session.sub_agents
        self.assertEqual((BASE + 100 * ONE_SECOND, timedelta(0)), (child.started_at, child.working_time))

    def test_a_sub_agent_whose_records_carry_no_time_has_the_header_start_and_no_working_time(self):
        self._put(ROOT, records=[_prompt(10, "ask")])
        meta = _meta(CHILD, ROOT, ROOT, 60)
        del meta["timestamp"]  # the payload still says 60
        self._write(_name(CHILD), _text([meta, {"type": "token_usage_record", "payload": {"usage": {"input_tokens": 9}}}]))
        (child,) = self._read().session.sub_agents
        self.assertEqual((BASE + 60 * ONE_SECOND, timedelta(0), [9]), (child.started_at, child.working_time, [r.input_tokens for r in child.requests]))


class CodexOrphanTest(_FamilyReaderTestCase):
    def test_an_orphan_child_is_the_session_with_its_prompts_labelled_with_its_missing_parent(self):
        self._put(CHILD, parent=MISSING, start=20, records=[_prompt(21, "child ask"), _used(22, 40, 4)])
        self._put(GRAND, parent=CHILD, start=30, agent_role="reviewer", records=[_used(31, 9, 1)])
        result = self._read()
        session = result.session
        self.assertEqual((ROOT, MISSING, CWD, BASE + 20 * ONE_SECOND, ["child ask"], [[40]], ()),
                         (session.session_id, session.orphan_of_parent_id, session.project_dir, session.started_at,
                          [p.text for p in session.prompts], [[r.input_tokens for r in p.requests] for p in session.prompts], result.unreadable))
        self.assertEqual([GRAND], [agent.agent_id for agent in session.sub_agents])

    def test_an_orphan_whose_meta_names_no_parent_is_labelled_with_the_missing_root(self):
        self._put(CHILD, start=20, records=[_prompt(21, "child ask")])
        self.assertEqual(ROOT, self._read().session.orphan_of_parent_id)

    def test_an_orphan_family_without_a_prompt_keeps_every_thread_as_a_sub_agent_and_is_labelled_by_the_first(self):
        self._put(SECOND, parent=ROOT, start=30, records=[_used(31, 2, 1)])
        self._put(CHILD, parent=MISSING, start=20, records=[_used(21, 1, 1)])
        session = self._read().session
        self.assertEqual(((), [CHILD, SECOND], MISSING, BASE + 20 * ONE_SECOND), (session.prompts, [a.agent_id for a in session.sub_agents],
                                                                              session.orphan_of_parent_id, session.started_at))

    def test_the_first_top_level_thread_with_a_prompt_is_the_orphan_session_its_siblings_sub_agents_and_it_starts_with_the_earliest_one(self):
        self._put(CHILD, parent=ROOT, start=20, records=[_used(21, 1, 1)])
        self._put(SECOND, parent=ROOT, start=30, records=[_prompt(31, "second ask")])
        self._put(GRAND, parent=SECOND, start=40, records=[_used(41, 1, 1)])
        session = self._read().session
        self.assertEqual((["second ask"], {CHILD, GRAND}, BASE + 20 * ONE_SECOND), ([p.text for p in session.prompts],
                          {agent.agent_id for agent in session.sub_agents}, session.started_at))

    def test_a_session_whose_root_is_missing_and_a_unit_cannot_be_read_is_unreadable_not_an_orphan(self):
        self._put(CHILD, parent=ROOT, start=20, records=[_prompt(21, "child ask")])
        gone = self._put(SECOND, parent=ROOT, start=30, records=[_used(31, 1, 1)])
        units = self.reader.scan().units
        os.remove(gone)
        result = self.reader.read("codex:" + ROOT, units)
        self.assertEqual((None, (UnreadableSource(Harness.CODEX, gone, CANNOT_BE_OPENED_REASON),)), (result.session, result.unreadable))

    def test_a_child_whose_root_rollout_is_there_but_cannot_be_learned_is_unreadable_not_an_orphan(self):
        parent = self._write(_name(ROOT) + ".zst", "not zstd at all")
        child = self._put(CHILD, parent=ROOT, start=20, records=[_prompt(21, "child ask")])
        scan = self.reader.scan()
        (child_unit,) = [unit for unit in scan.units if unit.key == child]
        self.assertIsNone(self.reader.session_of(next(unit for unit in scan.units if unit.key == parent[: -len(".zst")])))
        result = self.reader.read("codex:" + ROOT, (child_unit,))
        self.assertEqual((None, (UnreadableSource(Harness.CODEX, child, PARENT_UNREADABLE_REASON),)), (result.session, result.unreadable))

    def test_a_rollout_named_after_the_root_id_that_belongs_to_the_session_does_not_make_the_child_unreadable(self):
        self._put(ROOT, records=[_prompt(10, "ask root")])
        self._put(CHILD, parent=ROOT, start=20, records=[_used(21, 1, 1)])
        result = self._read()
        self.assertEqual((ROOT, None, ()), (result.session.session_id, result.session.orphan_of_parent_id, result.unreadable))

    def test_a_child_is_an_orphan_when_no_unit_of_the_store_carries_the_root_id(self):
        self._put(CHILD, parent=ROOT, start=20, records=[_prompt(21, "child ask")])
        self._put(SECOND, parent=MISSING, start=30)
        self.assertEqual(ROOT, self._read().session.orphan_of_parent_id)

    def test_a_fresh_reader_that_never_scanned_still_lists_a_child_unreadable_when_its_root_rollout_is_compressed(self):
        self._write(_name(ROOT) + ".zst", "not zstd at all")
        child = self._put(CHILD, parent=ROOT, start=20, records=[_prompt(21, "child ask")])
        (child_unit,) = [unit for unit in CodexReader(self.root).scan().units if unit.key == child]  # another reader scanned: this one never did
        result = self.reader.read("codex:" + ROOT, (child_unit,))
        self.assertEqual((None, (UnreadableSource(Harness.CODEX, child, PARENT_UNREADABLE_REASON),)), (result.session, result.unreadable))

    def test_a_root_rollout_the_scan_could_not_examine_makes_the_child_unreadable_not_an_orphan(self):
        child = self._put(CHILD, parent=ROOT, start=20, records=[_prompt(21, "child ask")])
        locked = FakeEntry(os.path.join(self.day, _name(ROOT)), PermissionError())
        with scandir_wrapping({self.day: [locked]}):
            (child_unit,) = self.reader.scan().units
        result = self.reader.read("codex:" + ROOT, (child_unit,))
        self.assertEqual((None, (UnreadableSource(Harness.CODEX, child, PARENT_UNREADABLE_REASON),)), (result.session, result.unreadable))

    def test_only_a_rollout_named_with_the_root_id_at_the_end_or_before_an_underscore_can_be_the_missing_root(self):
        root = "abc"
        child = self._write(_name(CHILD), _rollout(CHILD, root=root, parent=root, start=20, records=[_prompt(21, "child ask")]))
        cases = (("rollout-2026-09-11T10-00-00-abc.jsonl.zst", True), ("rollout-2026-09-11T10-00-00-abc_01a0aaaa.jsonl.zst", True),
                 ("rollout-2026-09-11T10-00-00-abcdef.jsonl.zst", False), ("rollout-2026-09-11T10-00-00-xabc.jsonl.zst", False),
                 ("rollout-2026-09-11T10-00-00-abc-more.jsonl.zst", False), ("rollout-abc2026.jsonl.zst", False))
        for name, is_the_root in cases:
            with self.subTest(name=name):
                other = self._write(name, "not zstd at all")
                try:
                    reader = CodexReader(self.root)
                    (unit,) = [unit for unit in reader.scan().units if unit.key == child]
                    result = reader.read("codex:" + root, (unit,))
                finally:
                    os.remove(other)
                if is_the_root:
                    self.assertEqual((None, (UnreadableSource(Harness.CODEX, child, PARENT_UNREADABLE_REASON),)), (result.session, result.unreadable))
                else:
                    self.assertEqual((root, ()), (result.session.orphan_of_parent_id, result.unreadable))

    def test_an_orphan_that_names_itself_as_its_parent_is_labelled_with_the_missing_root(self):
        self._put(CHILD, parent=CHILD, start=20, records=[_prompt(21, "child ask")])
        self.assertEqual(ROOT, self._read().session.orphan_of_parent_id)

    def test_the_orphan_label_is_the_parent_of_the_promoted_thread_not_of_an_earlier_sibling(self):
        self._put(CHILD, parent=MISSING, start=20, records=[_used(21, 1, 1)])
        self._put(SECOND, parent=GRAND, start=30, records=[_prompt(31, "second ask")])
        session = self._read().session
        self.assertEqual((GRAND, ["second ask"]), (session.orphan_of_parent_id, [prompt.text for prompt in session.prompts]))

    def test_a_family_whose_root_is_missing_and_whose_middle_parent_cannot_be_learned_is_unreadable_not_an_orphan(self):
        middle = self._put(CHILD, parent=ROOT, start=20, records=[_prompt(21, "middle ask")])
        grand = self._put(GRAND, parent=CHILD, start=30, records=[_prompt(31, "grand ask")])
        os.rename(middle, middle + ".zst")
        (grand_unit,) = [unit for unit in self.reader.scan().units if unit.key == grand]
        result = self.reader.read("codex:" + ROOT, (grand_unit,))
        self.assertEqual((None, (UnreadableSource(Harness.CODEX, grand, PARENT_UNREADABLE_REASON),)), (result.session, result.unreadable))

    def test_a_rollout_the_scan_found_that_is_named_after_no_parent_of_the_family_does_not_make_it_unreadable(self):
        child = self._put(CHILD, parent=MISSING, start=20, records=[_prompt(21, "child ask")])
        self._write(_name(SECOND) + ".zst", "not zstd at all")
        result = self.reader.read("codex:" + ROOT, tuple(unit for unit in self.reader.scan().units if unit.key == child))
        self.assertEqual((MISSING, ()), (result.session.orphan_of_parent_id, result.unreadable))

    def test_a_unit_that_cannot_be_read_and_an_unlearned_root_rollout_together_give_one_reason_per_path(self):
        self._write(_name(ROOT) + ".zst", "not zstd at all")
        self._put(CHILD, parent=ROOT, start=20, records=[_prompt(21, "child ask")])
        gone = self._put(SECOND, parent=ROOT, start=30, records=[_used(31, 1, 1)])
        units = tuple(unit for unit in self.reader.scan().units if not unit.key.endswith(_name(ROOT)))
        os.remove(gone)
        result = self.reader.read("codex:" + ROOT, units)
        self.assertEqual((None, (UnreadableSource(Harness.CODEX, gone, CANNOT_BE_OPENED_REASON),)), (result.session, result.unreadable))

    def test_a_plain_rollout_named_after_the_root_whose_first_line_is_garbled_makes_the_family_unreadable_not_an_orphan(self):
        garbled = self._write(_name(ROOT), "not json\n")
        child = self._put(CHILD, parent=ROOT, start=20, records=[_prompt(21, "child ask")])
        units = self.reader.scan().units
        self.assertIsNone(self.reader.session_of(next(unit for unit in units if unit.key == garbled)))
        result = self.reader.read("codex:" + ROOT, tuple(unit for unit in units if unit.key == child))
        self.assertEqual((None, (UnreadableSource(Harness.CODEX, child, PARENT_UNREADABLE_REASON),)), (result.session, result.unreadable))

    def test_read_runs_one_scan_when_none_has_run_on_the_reader_and_none_once_one_has(self):
        child = self._put(CHILD, parent=ROOT, start=20, records=[_prompt(21, "child ask")])
        (child_unit,) = [unit for unit in CodexReader(self.root).scan().units if unit.key == child]  # another reader scanned: this one never did
        with mock.patch.object(self.reader, "scan", wraps=self.reader.scan) as scan:
            self.reader.read("codex:" + ROOT, (child_unit,))
            self.reader.read("codex:" + ROOT, (child_unit,))
        self.assertEqual(1, scan.call_count)


class CodexSubAgentSyncTest(CodexEntryTestCase):
    NOW = datetime(2026, 3, 1, tzinfo=timezone.utc)  # the injected clock: `on` is switched on at this time
    OLD = datetime(2026, 2, 15, 8, 0, 0, tzinfo=timezone.utc)  # before `on`
    ACTIVE = datetime(2026, 9, 12, 8, 0, 0, tzinfo=timezone.utc)  # after `on` and after the last record of the root fixture (2026-09-11), whose file takes it

    def setUp(self):
        super().setUp()
        self.sessions = os.path.join(self.codex, "sessions", "2026", "08", "20")
        self.archived = os.path.join(self.codex, "archived_sessions")
        os.makedirs(self.sessions)

    def install(self, thread, text, when, folder=None):
        """Writes a rollout file whose modification time is `when`."""
        folder = folder or self.sessions
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, _name(thread))
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        self.touch(path, when)
        return path

    @staticmethod
    def touch(path, when):
        os.utime(path, ns=(0, int(when.timestamp()) * ONE_SECOND_NS))

    def archived_text(self, session_id, suffix):
        (path,) = [path for path in self.archived_files(suffix) if session_id in os.path.basename(path)]
        return self.read(path)

    def snapshot(self, log_root=None):
        """Every archived log and report of a log root by its path below the root."""
        root = log_root or self.log_root
        return {os.path.relpath(path, root): self.read(path) for path in self.archived_files(".md", root) + self.archived_files(".usage.txt", root)}

    def _archived_in_a_fresh_home(self):
        """What `on` archives from a copy of the sessions folder in a home where nothing was archived before."""
        fresh_home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, fresh_home, True)
        shutil.copytree(os.path.join(self.codex, "sessions"), os.path.join(fresh_home, ".codex", "sessions"), copy_function=shutil.copy2)
        self.assertEqual(0, self.run_main(["codex", "on"], home=fresh_home)[0])
        return self.snapshot(os.path.join(fresh_home, ".codex", "prompt-logs"))

    def _estimates(self, report):
        return [tuple(int(number) for number in match.groups()) for match in map(EST_LINE.search, report.splitlines()) if match]

    def _install_active_parent_with_old_child(self):
        """An active parent whose first turn is older than `on` (a later turn is active) and a child older than `on`: both are
        coherent in time (the child starts after the parent), and the child's file time is before `on` like its last record."""
        started = self.OLD - timedelta(days=1)
        records = [_prompt(started + ONE_SECOND, "first ask"), _used(started + 2 * ONE_SECOND, *PARENT_USED),
                   _prompt(self.ACTIVE + ONE_SECOND, "second ask")]
        self.install(ROOT, _rollout(ROOT, start=started, records=records), self.ACTIVE)
        self.install(CHILD, _rollout(CHILD, root=ROOT, parent=ROOT, start=self.OLD, records=[_used(self.OLD + ONE_SECOND, *CHILD_USED)]), self.OLD)

    def test_active_child_archives_parent_whole_old_child_linked_from_first_line_orphan_archived_alone_labelled(self):
        root_b, root_c, root_e, root_g = ("01a1000%d-0000-7000-8000-000000000001" % number for number in (2, 3, 4, 5))
        child_a, child_b, child_c, child_e, child_g = ("01a1000%d-0000-7000-8000-00000000000a" % number for number in (1, 2, 3, 4, 5))
        old, active = self.OLD, self.ACTIVE
        before_old = old - timedelta(days=1)
        # A: the parent sits in archived_sessions/ and is a real rollout; its child is in sessions/
        with open(ROOT_FIXTURE, encoding="utf-8") as handle:
            self.install(ROOT_FIXTURE_ID, handle.read(), active, self.archived)
        self.install(child_a, _rollout(child_a, root=ROOT_FIXTURE_ID, parent=ROOT_FIXTURE_ID, start=active + 5 * ONE_SECOND,
                                       records=[_used(active + 6 * ONE_SECOND, *CHILD_A_USED[0]), _used(active + 7 * ONE_SECOND, *CHILD_A_USED[1])]),
                     active)
        # B: a parent older than `on` with a child active since it
        self.install(root_b, _rollout(root_b, root=root_b, start=old, records=[_prompt(old + ONE_SECOND, "old parent prompt"), _used(old + 2 * ONE_SECOND, 10, 1)]), old)
        self.install(child_b, _rollout(child_b, root=root_b, parent=root_b, start=active, records=[_used(active + ONE_SECOND, 70, 7)]), active)
        # C: an active parent (its first turn is older than `on`, a later one is not) with a child older than `on`, linked by its first line alone
        self.install(root_c, _rollout(root_c, root=root_c, start=before_old, records=[
            _prompt(before_old + ONE_SECOND, "first parent prompt"), _prompt(active + ONE_SECOND, "active parent prompt"),
            _used(active + 2 * ONE_SECOND, *PARENT_USED)]), active)
        self.install(child_c, _rollout(child_c, root=root_c, parent=root_c, start=old, agent_role="explorer", records=[_used(old + ONE_SECOND, *CHILD_USED)]), old)
        # D: a real forked child whose root rollout was deleted
        with open(CHILD_FIXTURE, encoding="utf-8") as handle:
            self.install("1a200002-0000-7000-8000-000000000002", handle.read(), datetime(2026, 8, 7, 14, 0, tzinfo=timezone.utc))
        # E: a child whose parent is a rollout that cannot be read (compressed)
        parent_e = self.install(root_e, "not zstd at all", old)
        os.rename(parent_e, parent_e + ".zst")
        broken = self.install(child_e, _rollout(child_e, root=root_e, parent=root_e, start=active, records=[_prompt(active + ONE_SECOND, "orphaned by error")]), active)
        with open(COUNT_ONLY_FIXTURE, encoding="utf-8") as handle:
            self.install(COUNT_ONLY_ID, handle.read(), datetime(2026, 3, 5, 13, 0, tzinfo=timezone.utc))
        # G: an active parent whose old child is compressed. Reading a compressed rollout is BE-26: until then the child is linked to no
        # session (listed unreadable) and the parent is archived without it.
        self.install(root_g, _rollout(root_g, root=root_g, start=before_old, records=[
            _prompt(before_old + ONE_SECOND, "first g prompt"), _prompt(active + ONE_SECOND, "active g prompt")]), active)
        child_g_path = self.install(child_g, _rollout(child_g, root=root_g, parent=root_g, start=old, records=[_used(old + ONE_SECOND, 5, 1)]), old)
        os.rename(child_g_path, child_g_path + ".zst")

        code, stdout, stderr = self.run_main(["codex", "on"])
        self.assertEqual((0, ""), (code, stderr))

        with self.subTest("A: the parent in archived_sessions is archived whole with its active child"):
            parent_report = self.archived_text(ROOT_FIXTURE_ID, ".usage.txt")
            self.assertIn("TOTAL (2 requests, 1 sub-agents)", parent_report)
            child_a_total = tuple(map(sum, zip(*[(input_tokens, cached, output) for input_tokens, output, cached in CHILD_A_USED])))
            self.assertEqual(_report_numbers(tuple(map(sum, zip(ROOT_FIXTURE_TOTAL, child_a_total)))), self._estimates(parent_report)[-1])
            self.assertIn("sub-agent: unknown (%s)" % child_a, parent_report)
            self.assertIn("prompt 1", self.archived_text(ROOT_FIXTURE_ID, ".md"))

        with self.subTest("B: an old parent is archived with its active child"):
            self.assertIn("old parent prompt", self.archived_text(root_b, ".md"))
            self.assertIn("sub-agent: unknown (%s)" % child_b, self.archived_text(root_b, ".usage.txt"))

        with self.subTest("C: an old child is linked to its active parent by its first line"):
            report_c = self.archived_text(root_c, ".usage.txt")
            self.assertIn("sub-agent: explorer (%s)" % child_c, report_c)
            self.assertEqual(tuple(map(sum, zip(PARENT_USED, CHILD_USED))), self._estimates(report_c)[-1][:2])

        with self.subTest("D: an orphan fork is archived alone, labelled, and shows only its own tokens"):
            orphan_log = self.archived_text(CHILD_FIXTURE_ROOT_ID, ".md")
            self.assertIn("- sub-agent of %s (parent not found)\n" % CHILD_FIXTURE_ROOT_ID, orphan_log)
            self.assertIn("prompt 1", orphan_log)
            self.assertEqual(_report_numbers(FORK_FINAL_TOTAL, FORK_INHERITED_TOTAL),
                             self._estimates(self.archived_text(CHILD_FIXTURE_ROOT_ID, ".usage.txt"))[-1])

        with self.subTest("F: an older rollout with only `token_count` events: the growth of the total is its tokens"):
            self.assertEqual(_report_numbers(COUNT_ONLY_FINAL_TOTAL), self._estimates(self.archived_text(COUNT_ONLY_ID, ".usage.txt"))[-1])

        with self.subTest("G: an active parent is archived without its compressed old child"):
            self.assertIn("active g prompt", self.archived_text(root_g, ".md"))
            self.assertNotIn("sub-agent:", self.archived_text(root_g, ".usage.txt"))

        with self.subTest("the rows add up to TOTAL"):
            for session_id in (ROOT_FIXTURE_ID, root_b, root_c, CHILD_FIXTURE_ROOT_ID, COUNT_ONLY_ID, root_g):
                estimates = self._estimates(self.archived_text(session_id, ".usage.txt"))
                self.assertEqual(tuple(map(sum, zip(*estimates[:-1]))), estimates[-1], session_id)

        with self.subTest("E: a child whose parent cannot be read is not archived, and status lists what could not be read"):
            self.assertEqual(6, len(self.archived_files(".md")))
            self.assertFalse([path for path in self.archived_files(".md") if root_e in path])
            code, stdout, stderr = self.run_main(["codex", "status"])
            self.assertEqual((0, ""), (code, stderr))
            self.assertIn("Archived sessions: 6", stdout)
            self.assertIn("  %s: %s\n" % (broken, PARENT_UNREADABLE_REASON), stdout)
            self.assertIn("  %s: %s\n" % (parent_e, UNKNOWN_SESSION_REASON), stdout)
            self.assertIn("  %s: %s\n" % (child_g_path, UNKNOWN_SESSION_REASON), stdout)

    def test_two_children_of_an_unlearned_root_rollout_are_both_unreadable_and_neither_is_archived_as_an_orphan(self):
        self.install(ROOT, "not zstd at all", self.OLD)
        os.rename(os.path.join(self.sessions, _name(ROOT)), os.path.join(self.sessions, _name(ROOT)) + ".zst")
        first = self.install(CHILD, _rollout(CHILD, root=ROOT, parent=ROOT, start=self.ACTIVE, records=[_prompt(self.ACTIVE + ONE_SECOND, "first ask")]), self.ACTIVE)
        second = self.install(SECOND, _rollout(SECOND, root=ROOT, parent=ROOT, start=self.ACTIVE + ONE_SECOND,
                                               records=[_prompt(self.ACTIVE + 2 * ONE_SECOND, "second ask")]), self.ACTIVE)
        self.assertEqual(0, self.run_main(["codex", "on"])[0])
        self.assertEqual([], self.archived_files(".md"))
        code, stdout, stderr = self.run_main(["codex", "status"])
        self.assertEqual((0, ""), (code, stderr))
        # The reader names only the newest unit; sync's `_mark` gives every other unit of the session that first reason.
        self.assertIn("  %s: %s\n" % (first, PARENT_UNREADABLE_REASON), stdout)
        self.assertIn("  %s: %s\n" % (second, PARENT_UNREADABLE_REASON), stdout)

    def test_old_child_of_active_parent_pulled_in_on_second_sync_identical_output(self):
        started = self.OLD - timedelta(days=1)
        records = [_prompt(started + ONE_SECOND, "first ask"), _used(started + 2 * ONE_SECOND, *PARENT_USED), _prompt(self.ACTIVE + ONE_SECOND, "second ask")]
        root_path = self.install(ROOT, _rollout(ROOT, start=started, records=records), self.ACTIVE)
        self.assertEqual(0, self.run_main(["codex", "on"])[0])
        self.assertNotIn("sub-agent:", self.archived_text(ROOT, ".usage.txt"))
        parent_before = (self.read(root_path), os.stat(root_path).st_mtime_ns)
        files_without_child = self.snapshot()
        # The old child appears and the parent stays byte- and mtime-unchanged. Sync reads a session only when one of its units may hold
        # activity since `on` (a changed unit modified before `on` cannot), so a lone old child does not make the second sync read it.
        self.install(CHILD, _rollout(CHILD, root=ROOT, parent=ROOT, start=self.OLD, records=[_used(self.OLD + ONE_SECOND, *CHILD_USED)]), self.OLD)
        later = self.ACTIVE + timedelta(hours=2)
        self.assertEqual(0, self.run_main(["codex", "status"], now=later)[0])
        self.assertEqual((files_without_child, parent_before), (self.snapshot(), (self.read(root_path), os.stat(root_path).st_mtime_ns)))
        # The parent's next turn makes the session a candidate again: it is read whole, so the old child is pulled in.
        with open(root_path, "a", encoding="utf-8", newline="") as handle:
            handle.write(_text([_prompt(self.ACTIVE + 3 * ONE_SECOND, "third ask")]))
        self.touch(root_path, self.ACTIVE + timedelta(hours=1))
        self.assertEqual(0, self.run_main(["codex", "status"], now=later)[0])
        log, report = self.archived_text(ROOT, ".md"), self.archived_text(ROOT, ".usage.txt")
        self.assertIn("sub-agent: unknown (%s)" % CHILD, report)
        self.assertIn("third ask", log)
        self.assertEqual(tuple(map(sum, zip(PARENT_USED, CHILD_USED))), self._estimates(report)[-1][:2])

        files = self.snapshot()
        parent_after = (self.read(root_path), os.stat(root_path).st_mtime_ns)
        self.assertEqual(0, self.run_main(["codex", "status"], now=later + timedelta(hours=1))[0])
        self.assertEqual((files, parent_after), (self.snapshot(), (self.read(root_path), os.stat(root_path).st_mtime_ns)))
        self.assertEqual(files, self._archived_in_a_fresh_home())

    def test_old_child_present_from_the_first_sync_is_archived_whole_and_the_second_sync_changes_nothing(self):
        self._install_active_parent_with_old_child()
        self.assertFalse(os.path.exists(os.path.join(self.log_root, ".sync.json")))
        self.assertEqual(0, self.run_main(["codex", "on"])[0])
        report = self.archived_text(ROOT, ".usage.txt")
        self.assertIn("sub-agent: unknown (%s)" % CHILD, report)
        self.assertEqual(tuple(map(sum, zip(PARENT_USED, CHILD_USED))), self._estimates(report)[-1][:2])
        files = self.snapshot()
        self.assertEqual(0, self.run_main(["codex", "status"], now=self.ACTIVE + timedelta(hours=1))[0])
        self.assertEqual(files, self.snapshot())
        self.assertEqual(files, self._archived_in_a_fresh_home())

    def test_a_grandchild_naming_the_root_as_its_session_is_no_separate_log_and_nests_under_its_child(self):
        active = self.ACTIVE
        self.install(ROOT, _rollout(ROOT, start=active, records=[_prompt(active + ONE_SECOND, "ask")]), active)
        for thread, parent, start in ((CHILD, ROOT, 20), (SECOND, ROOT, 30), (GRAND, CHILD, 40)):  # `session_id` of each one names the root
            self.install(thread, _rollout(thread, root=ROOT, parent=parent, start=active + start * ONE_SECOND,
                                          records=[_used(active + (start + 1) * ONE_SECOND, 5, 1)]), active)
        self.assertEqual(0, self.run_main(["codex", "on"])[0])
        self.assertEqual(1, len(self.archived_files(".md")))
        report = self.archived_text(ROOT, ".usage.txt")
        self.assertIn("TOTAL (1 requests, 3 sub-agents)", report)
        # the report lists the sub-agents depth first: a grandchild comes right after its parent, before that parent's later sibling
        self.assertEqual([CHILD, GRAND, SECOND], re.findall(r"sub-agent: \w+ \(([^)]+)\)", report))

    def test_an_old_grandchild_is_linked_to_its_active_session_by_session_id_and_nested_under_the_thread_its_parent_thread_id_names(self):
        started = self.OLD - timedelta(days=1)
        self.install(ROOT, _rollout(ROOT, start=started, records=[_prompt(self.ACTIVE + ONE_SECOND, "active ask")]), self.ACTIVE)
        # `session_id` of each old child names the root; GRAND's `parent_thread_id` names CHILD, and it starts after SECOND
        for thread, parent, second in ((CHILD, ROOT, 1), (SECOND, ROOT, 2), (GRAND, CHILD, 3)):
            self.install(thread, _rollout(thread, root=ROOT, parent=parent, start=self.OLD + second * ONE_SECOND,
                                          records=[_used(self.OLD + (second + 10) * ONE_SECOND, 5, 1)]), self.OLD)
        self.assertEqual(0, self.run_main(["codex", "on"])[0])
        self.assertEqual(1, len(self.archived_files(".md")))
        report = self.archived_text(ROOT, ".usage.txt")
        self.assertEqual([CHILD, GRAND, SECOND], re.findall(r"sub-agent: \w+ \(([^)]+)\)", report))
        self.assertEqual((15, 3), self._estimates(report)[-1][:2])

    def test_a_session_wholly_before_on_is_not_archived_and_neither_is_its_old_child_pulled_in_on_its_own(self):
        started = self.OLD - timedelta(days=1)
        self.install(ROOT, _rollout(ROOT, start=self.ACTIVE, records=[_prompt(self.ACTIVE + ONE_SECOND, "active ask")]), self.ACTIVE)
        self.install(OLD_ROOT, _rollout(OLD_ROOT, root=OLD_ROOT, start=started, records=[
            _prompt(started + ONE_SECOND, "old ask"), _used(started + 2 * ONE_SECOND, *PARENT_USED)]), self.OLD)
        self.install(OLD_CHILD, _rollout(OLD_CHILD, root=OLD_ROOT, parent=OLD_ROOT, start=self.OLD,
                                         records=[_used(self.OLD + ONE_SECOND, *CHILD_USED)]), self.OLD)
        self.assertEqual(0, self.run_main(["codex", "on"])[0])
        archived = [os.path.basename(path) for path in self.archived_files(".md")]
        self.assertEqual([True], [ROOT in name for name in archived])  # the active session only
        before = self.snapshot()
        self.install(SECOND, _rollout(SECOND, root=OLD_ROOT, parent=OLD_ROOT, start=self.OLD + ONE_SECOND,
                                      records=[_used(self.OLD + 2 * ONE_SECOND, 1, 1)]), self.OLD)  # another old child of the old session
        self.assertEqual(0, self.run_main(["codex", "status"], now=self.ACTIVE + timedelta(hours=1))[0])
        self.assertEqual(before, self.snapshot())


if __name__ == "__main__":
    unittest.main()
