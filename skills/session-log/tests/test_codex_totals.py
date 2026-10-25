"""Codex totals and times that were wrong: a sub-agent of an older Codex whose header names its parent only in the spawn source is
nested under that parent (and counted in its TOTAL), a fork's replayed history is no work of the fork, and a `token_count` that
carries only `total_tokens` is counted. Rollout parsing is pure; the nesting is tested over a store and through sync."""
import json
import os
import unittest
from datetime import datetime, timedelta, timezone

from sessionlog.codex_rollout import parse_rollout, parse_session_meta
from sessionlog.pricing import PricingRules
from tests.test_claude_subagents import _Book
from tests.test_codex_entry import CodexEntryTestCase
from tests.test_codex_subagents import CHILD, GRAND, ROOT, _FamilyReaderTestCase, _name, _prompt, _rollout, _used

THREAD = "11111111-1111-4111-8111-111111111111"
PARENT = "33333333-3333-4333-8333-333333333333"
GUARD_ROOT = "01a00000-0000-7000-8000-0000000000a0"
GUARD = "01a00000-0000-7000-8000-0000000000a1"
HEADER_AT = datetime(2026, 8, 28, 10, 0, 0, tzinfo=timezone.utc)
ONE_SECOND = timedelta(seconds=1)
SECONDS_BEFORE_HEADER = 10
IMPORTED_TOTAL = 257  # the one `total_tokens` of a real rollout imported from another agent: input and output are 0


def _spawn(parent, role="explorer") -> dict:
    return {"subagent": {"thread_spawn": {"parent_thread_id": parent, "depth": 1, "agent_role": role}}}


def _legacy_rollout(thread, parent, start, records=()) -> str:
    """A header of an older Codex: the thread is its own `session_id` and only the spawn source names the parent."""
    return _rollout(thread, root=thread, start=start, records=records, source=_spawn(parent))


def _line(record) -> str:
    return json.dumps(record) + "\n"


def _stamp(second) -> str:
    return (HEADER_AT + second * ONE_SECOND).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _header(**payload) -> dict:
    fields = {"id": THREAD, "session_id": THREAD, "timestamp": _stamp(0), "cwd": "/work/proj"}
    return {"timestamp": _stamp(0), "type": "session_meta", "payload": {**fields, **payload}}


def _fork_header(**payload) -> dict:
    return _header(session_id=PARENT, parent_thread_id=PARENT, forked_from_id=PARENT, **payload)


def _event(second, payload) -> dict:
    return {"timestamp": _stamp(second), "type": "event_msg", "payload": payload}


def _turn(second, turn_id, started_at=None) -> dict:
    payload = {"type": "task_started", "turn_id": turn_id}
    if started_at is not None:
        payload["started_at"] = int((HEADER_AT + started_at * ONE_SECOND).timestamp())
    return _event(second, payload)


def _asked(second, text) -> dict:
    return _event(second, {"type": "item_completed", "item": {"type": "UserMessage", "content": [{"type": "text", "text": text}]}})


def _total(second, input_tokens=0, output=0, total=None) -> dict:
    usage = {"input_tokens": input_tokens, "cached_input_tokens": 0, "cache_write_input_tokens": 0, "output_tokens": output,
             "reasoning_output_tokens": 0, "total_tokens": input_tokens + output if total is None else total}
    return _event(second, {"type": "token_count", "info": {"total_token_usage": usage, "last_token_usage": usage}})


def _context(second, model="gpt-x") -> dict:
    return {"timestamp": _stamp(second), "type": "turn_context", "payload": {"model": model}}


def _recorded(second, input_tokens, output=0) -> dict:
    usage = {"input_tokens": input_tokens, "cached_input_tokens": 0, "cache_write_input_tokens": 0, "output_tokens": output}
    return {"timestamp": _stamp(second), "type": "token_usage_record", "payload": {"usage": usage}}


def _parse(header, records):
    return parse_rollout([_line(header)] + [_line(record) for record in records])


def _meta(**payload):
    return parse_session_meta(_line(_header(**payload)))


def _tokens(rollout):
    return [(request.input_tokens, request.output_tokens) for request in rollout.requests]


class LegacyHeaderTest(unittest.TestCase):
    """Older Codex headers: `session_id` is the thread's own id, the parent is only in `source.subagent.thread_spawn`."""

    def test_the_parent_of_a_spawn_source_is_the_parent_and_the_root_of_a_thread_that_is_its_own_session_id(self):
        meta = _meta(source=_spawn(PARENT))
        self.assertEqual((PARENT, PARENT, THREAD, "explorer"), (meta.parent_thread_id, meta.root_id, meta.thread_id, meta.agent_type))

    def test_a_top_level_parent_thread_id_with_the_threads_own_session_id_names_the_root_too(self):
        meta = _meta(parent_thread_id=PARENT, source={"subagent": {"other": "guardian"}})
        self.assertEqual((PARENT, PARENT, "guardian"), (meta.parent_thread_id, meta.root_id, meta.agent_type))

    def test_a_sub_agent_forked_from_a_thread_without_any_other_parent_hangs_from_it(self):
        meta = _meta(forked_from_id=PARENT, source={"subagent": {"other": "guardian"}})
        self.assertEqual((PARENT, PARENT, True), (meta.parent_thread_id, meta.root_id, meta.forked))

    def test_a_sub_agent_naming_no_parent_at_all_is_its_own_root(self):
        meta = _meta(source={"subagent": {"other": "guardian"}})
        self.assertEqual((None, THREAD, "guardian"), (meta.parent_thread_id, meta.root_id, meta.agent_type))

    def test_a_fork_of_a_thread_that_is_no_sub_agent_stays_its_own_root(self):
        meta = _meta(forked_from_id=PARENT, source="cli")
        self.assertEqual((None, THREAD, True), (meta.parent_thread_id, meta.root_id, meta.forked))

    def test_a_session_id_other_than_the_threads_own_still_names_the_root_whatever_the_parent(self):
        meta = _meta(session_id=ROOT, source=_spawn(PARENT))
        self.assertEqual((PARENT, ROOT), (meta.parent_thread_id, meta.root_id))

    def test_a_top_level_parent_wins_over_the_spawn_source_when_they_differ(self):
        meta = _meta(parent_thread_id=CHILD, source=_spawn(PARENT))
        self.assertEqual(CHILD, meta.parent_thread_id)


class LegacyChildNestingTest(_FamilyReaderTestCase):
    def test_a_legacy_child_is_one_session_with_its_parent_and_its_tokens_are_in_the_total(self):
        self._write(_name(ROOT), _rollout(ROOT, start=0, records=[_prompt(10, "ask root"), _used(11, 100, 10)]))
        self._write(_name(CHILD), _legacy_rollout(CHILD, ROOT, 20, [_used(21, 700, 70)]))
        units = self.reader.scan().units
        self.assertEqual({"codex:" + ROOT}, {self.reader.session_of(unit) for unit in units})
        session = self.reader.read("codex:" + ROOT, units).session
        self.assertEqual([CHILD], [agent.agent_id for agent in session.sub_agents])
        self.assertEqual("explorer", session.sub_agents[0].type)
        total = PricingRules(_Book()).build(session).total
        self.assertEqual((880, 1), (total.total_tokens, total.sub_agent_count))
        self.assertIsNone(session.orphan_of_parent_id)

    def test_a_legacy_grandchild_of_a_modern_child_nests_under_the_thread_its_spawn_source_names(self):
        self._write(_name(ROOT), _rollout(ROOT, start=0, records=[_prompt(10, "ask root")]))
        self._put(CHILD, parent=ROOT, start=20, records=[_used(21, 5, 1)])
        self._write(_name(GRAND), _rollout(GRAND, root=ROOT, start=30, records=[_used(31, 7, 1)], source=_spawn(CHILD)))
        session = self._read().session
        (child,) = session.sub_agents
        self.assertEqual([GRAND], [agent.agent_id for agent in child.sub_agents])

    def test_a_legacy_child_whose_parent_rollout_is_gone_is_an_orphan_session_labelled_with_that_parent(self):
        self._write(_name(CHILD), _legacy_rollout(CHILD, ROOT, 20, [_prompt(21, "child ask"), _used(22, 700, 70)]))
        (unit,) = self.reader.scan().units
        self.assertEqual("codex:" + ROOT, self.reader.session_of(unit))
        session = self._read().session
        self.assertEqual((ROOT, ["child ask"]), (session.orphan_of_parent_id, [prompt.text for prompt in session.prompts]))

    def test_a_sub_agent_without_any_parent_is_archived_alone_under_its_own_id_and_is_no_orphan_label(self):
        self._write(_name(GUARD), _rollout(GUARD, root=GUARD, start=20, records=[_prompt(21, "review"), _used(22, 5, 1)],
                                           source={"subagent": {"other": "guardian"}}))
        (unit,) = self.reader.scan().units
        self.assertEqual("codex:" + GUARD, self.reader.session_of(unit))
        session = self._read(GUARD).session
        self.assertEqual((None, 6), (session.orphan_of_parent_id, PricingRules(_Book()).build(session).total.total_tokens))


class LegacyChildSyncTest(CodexEntryTestCase):
    NOW = datetime(2026, 3, 1, tzinfo=timezone.utc)
    ACTIVE = datetime(2026, 9, 12, 8, 0, 0, tzinfo=timezone.utc)

    def test_on_archives_the_parent_once_with_its_legacy_child_in_the_total_and_no_session_of_the_child(self):
        folder = os.path.join(self.codex, "sessions", "2026", "09", "12")
        os.makedirs(folder)
        started = self.ACTIVE
        for thread, text in ((ROOT, _rollout(ROOT, start=started, records=[_prompt(started + ONE_SECOND, "ask"), _used(started + 2 * ONE_SECOND, 100, 10)])),
                             (CHILD, _legacy_rollout(CHILD, ROOT, started + 3 * ONE_SECOND, [_used(started + 4 * ONE_SECOND, 700, 70)]))):
            with open(os.path.join(folder, _name(thread)), "w", encoding="utf-8") as handle:
                handle.write(text)
        self.assertEqual(0, self.run_main(["codex", "on"])[0])
        (log,) = self.archived_files(".md")
        self.assertIn(ROOT, os.path.basename(log))
        (report,) = self.archived_files(".usage.txt")
        text = self.read(report)
        self.assertIn("TOTAL (1 requests, 1 sub-agents)", text)
        self.assertIn("sub-agent: explorer (%s)" % CHILD, text)
        self.assertIn("total_tokens: 880", text)


class ForkReplayTest(unittest.TestCase):
    """The history a fork replays when it starts is the parent's work, never the fork's own prompts, requests or tokens."""

    REPLAY = [_turn(0.1, "rollout-2", started_at=0), _asked(0.1, "parent asked"), _total(0.1, 1000, 100), _total(0.1, 3000, 300),
              _event(0.1, {"type": "task_complete", "turn_id": "rollout-2"})]

    def _own(self, first=1):
        return [_turn(first, "own-turn", started_at=first), _context(first), _asked(first, "fork asked"),
                _total(first + 1, 3400, 330), _total(first + 2, 3900, 380)]

    def test_a_replayed_wrapper_turn_adds_no_request_and_no_prompt_and_the_forks_own_growth_starts_from_its_total(self):
        rollout = _parse(_fork_header(), self.REPLAY + self._own())
        self.assertEqual([(400, 30), (500, 50)], _tokens(rollout))
        self.assertEqual(["fork asked"], [prompt.text for prompt in rollout.prompts])
        self.assertEqual({"gpt-x"}, {request.model_name for request in rollout.requests})

    def test_replayed_turns_that_started_before_the_fork_were_the_parents_even_with_a_real_turn_id(self):
        earlier = [_turn(0.2, "parent-turn", started_at=-SECONDS_BEFORE_HEADER), _asked(0.2, "older ask"), _total(0.2, 3200, 320)]
        rollout = _parse(_fork_header(), self.REPLAY + earlier + self._own())
        self.assertEqual([(200, 10), (500, 50)], _tokens(rollout))
        self.assertEqual(["fork asked"], [prompt.text for prompt in rollout.prompts])

    def test_a_turn_started_in_the_second_of_the_header_or_later_is_the_forks_own(self):
        own = [_turn(0.5, "own-turn", started_at=0), _asked(0.5, "fork asked"), _total(0.6, 100, 10)]
        rollout = _parse(_fork_header(), own)
        self.assertEqual(([(100, 10)], ["fork asked"]), (_tokens(rollout), [prompt.text for prompt in rollout.prompts]))

    def test_token_usage_records_of_a_replayed_turn_are_not_requests_of_the_fork(self):
        records = [_turn(0.1, "rollout-3", started_at=0), _recorded(0.1, 900, 90), _event(0.1, {"type": "task_complete", "turn_id": "rollout-3"}),
                   _turn(1, "own-turn", started_at=1), _asked(1, "fork asked"), _recorded(2, 40, 4)]
        rollout = _parse(_fork_header(), records)
        self.assertEqual([(40, 4)], _tokens(rollout))

    def test_a_fork_with_an_inherited_total_before_its_replay_counts_the_same_own_requests(self):
        records = [_total(0.05, 3000, 300)] + self.REPLAY + self._own()
        self.assertEqual([(400, 30), (500, 50)], _tokens(_parse(_fork_header(), records)))

    def test_the_same_turn_ids_in_a_rollout_that_is_no_fork_are_its_own_work(self):
        rollout = _parse(_header(), self.REPLAY + self._own())
        self.assertEqual(["parent asked", "fork asked"], [prompt.text for prompt in rollout.prompts])
        self.assertEqual([(1000, 100), (2000, 200), (400, 30), (500, 50)], _tokens(rollout))

    def test_a_fork_without_any_replay_mark_still_counts_every_request_as_its_own(self):
        rollout = _parse(_fork_header(), [_turn(1, "own-turn"), _total(2, 1000, 100), _total(3, 3000, 300)])
        self.assertEqual([(1000, 100), (2000, 200)], _tokens(rollout))


class UnsplitTotalTest(unittest.TestCase):
    """A `token_count` of an imported rollout carries only `total_tokens`: its input and output fields are 0."""

    def _count(self, rollout_records):
        return _parse(_header(), [_turn(1, "external-import-turn-1")] + rollout_records)

    def test_a_total_that_only_the_total_tokens_field_gives_is_the_tokens_of_the_request_in_the_input_class(self):
        rollout = self._count([_total(2, total=IMPORTED_TOTAL)])
        self.assertEqual([(IMPORTED_TOTAL, 0)], _tokens(rollout))
        (request,) = rollout.requests
        self.assertEqual(("", 0, 0, 0), (request.model_name, request.output_tokens, request.cache_read_tokens, request.cache_write_5m_tokens))

    def test_the_unsplit_total_reaches_the_prompt_and_the_report_total(self):
        rollout = self._count([_asked(1.5, "imported ask"), _total(2, total=IMPORTED_TOTAL)])
        (prompt,) = rollout.prompts
        self.assertEqual(IMPORTED_TOTAL, sum(request.input_tokens for request in prompt.requests))

    def test_a_growing_unsplit_total_gives_the_growth_of_each_event_and_a_repeat_adds_nothing(self):
        rollout = self._count([_total(2, total=100), _total(3, total=100), _total(4, total=250)])
        self.assertEqual([(100, 0), (150, 0)], _tokens(rollout))

    def test_an_event_whose_total_tokens_equals_input_plus_output_gets_nothing_extra(self):
        rollout = self._count([_total(2, 1000, 100), _total(3, 1500, 160)])
        self.assertEqual([(1000, 100), (500, 60)], _tokens(rollout))

    def test_a_total_tokens_below_input_plus_output_or_not_a_number_adds_nothing(self):
        for total in (5, "many", None, True, -1):
            with self.subTest(total=total):
                usage = {"input_tokens": 100, "output_tokens": 10, "total_tokens": total}
                rollout = self._count([_event(2, {"type": "token_count", "info": {"total_token_usage": usage}})])
                self.assertEqual([(100, 10)], _tokens(rollout))

    def test_an_event_with_only_total_tokens_and_no_split_field_is_counted(self):
        rollout = self._count([_event(2, {"type": "token_count", "info": {"total_token_usage": {"total_tokens": 40}}})])
        self.assertEqual([(40, 0)], _tokens(rollout))


if __name__ == "__main__":
    unittest.main()
