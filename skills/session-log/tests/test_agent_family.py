"""The sub-agent family rules shared by the Codex and OMP readers (agent_family): threads nest under the thread their parent names, and a
family whose root transcript is gone becomes one orphan session; plain threads, no file is involved."""
import unittest
from datetime import datetime, timedelta, timezone

from sessionlog.agent_family import Thread, nest_threads, split_orphan_family
from sessionlog.model import Prompt, RequestUsage

ROOT = "01a00000-0000-7000-8000-000000000001"
MISSING = "01a00000-0000-7000-8000-0000000000ff"
BASE = datetime(2026, 9, 11, 8, 0, 0, tzinfo=timezone.utc)
ONE_SECOND = timedelta(seconds=1)


def _asked(text) -> Prompt:
    return Prompt(submitted_at=BASE, text=text)


def _agent(agent_id, start=0, parent=None, requests=(), prompts=(), project_dir=None) -> Thread:
    return Thread(thread_id=agent_id, parent_thread_id=parent, agent_type="worker", started_at=BASE + timedelta(seconds=start),
                  working_time=ONE_SECOND, requests=tuple(requests), prompts=tuple(prompts), project_dir=project_dir)


def _request(input_tokens) -> RequestUsage:
    return RequestUsage("gpt-x", input_tokens, 0, 0, 0, 0)


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


if __name__ == "__main__":
    unittest.main()
