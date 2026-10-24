"""The shared nesting rules of sub-agents (agent_tree): parent indexes, depths and the deepest-first tree build."""
import unittest
from datetime import datetime, timezone

from sessionlog.agent_tree import build_tree, depths_of, nodes_entering_a_loop, parent_indexes
from sessionlog.model import SubAgent

BASE = datetime(2026, 9, 11, 8, 0, 0, tzinfo=timezone.utc)


class ParentIndexesTest(unittest.TestCase):
    def test_each_agent_points_at_the_index_of_the_agent_its_parent_id_names(self):
        self.assertEqual({1: 0, 2: 1}, parent_indexes(["a", "b", "c"], [None, "a", "b"]))

    def test_the_first_agent_with_a_duplicate_id_is_the_parent(self):
        self.assertEqual({2: 0}, parent_indexes(["a", "a", "b"], [None, None, "a"]))

    def test_a_missing_parent_a_self_parent_and_every_member_of_a_parent_loop_are_left_out_but_their_children_stay(self):
        ids = ["gone-child", "self", "x", "y", "below", "root-child"]
        parents = ["missing", "self", "y", "x", "x", ""]
        self.assertEqual({4: 2}, parent_indexes(ids, parents))

    def test_a_loop_of_three_is_left_out_whole(self):
        self.assertEqual({}, parent_indexes(["a", "b", "c"], ["b", "c", "a"]))

    def test_an_agent_that_names_itself_stays_at_the_top_level(self):
        self.assertEqual({}, parent_indexes(["a"], ["a"]))


class NodesEnteringALoopTest(unittest.TestCase):
    def test_the_members_of_a_loop_a_node_that_is_its_own_parent_and_every_node_hanging_from_one_enter_a_loop(self):
        parent_of = {"a": "b", "b": "a", "below": "a", "deeper": "below", "self": "self", "top": None, "chain": "top", "lost": "gone"}
        self.assertEqual({"a", "b", "below", "deeper", "self"}, nodes_entering_a_loop(parent_of))

    def test_a_longer_loop_is_found_whatever_the_order_of_the_nodes(self):
        for rotation in range(4):
            with self.subTest(rotation=rotation):
                names = "abcd"[rotation:] + "abcd"[:rotation]
                self.assertEqual(set("abcd"), nodes_entering_a_loop({name: "abcd"[("abcd".index(name) + 1) % 4] for name in names}))

    def test_a_forest_without_a_loop_and_no_nodes_at_all_have_none(self):
        self.assertEqual(set(), nodes_entering_a_loop({"a": None, "b": "a", "c": "b", "d": "missing"}))
        self.assertEqual(set(), nodes_entering_a_loop({}))

    def test_a_node_judged_once_gives_its_verdict_to_a_chain_that_ends_at_it(self):
        parent_of = {"x": "y", "y": "x", **{"n%d" % number: "n%d" % (number + 1) for number in range(50)}, "n50": "x"}
        self.assertEqual({"x", "y"} | {"n%d" % number for number in range(51)}, nodes_entering_a_loop(parent_of))


class DepthAndTreeTest(unittest.TestCase):
    def test_the_depth_of_an_agent_is_the_number_of_its_ancestors(self):
        self.assertEqual([0, 1, 2, 0], depths_of({1: 0, 2: 1}, 4))

    def test_the_tree_is_built_deepest_first_so_a_parent_holds_its_finished_children_and_the_top_level_comes_with_its_index_in_index_order(self):
        parent_of = {0: 1, 1: 3, 2: 3}  # 3 is the top of 1 and 2; 0 hangs below 1; 4 is a second top-level agent
        built = []

        def build(index, children):
            built.append(index)
            return SubAgent(agent_id=str(index), type="t", started_at=BASE, sub_agents=children)

        top = build_tree(parent_of, depths_of(parent_of, 5), build)
        self.assertEqual([(3, "3"), (4, "4")], [(index, agent.agent_id) for index, agent in top])
        self.assertEqual([("1", ["0"]), ("2", [])],
                         [(agent.agent_id, [grand.agent_id for grand in agent.sub_agents]) for agent in top[0][1].sub_agents])
        self.assertTrue(all(built.index(child) < built.index(parent) for child, parent in parent_of.items()))

    def test_no_agents_no_tree(self):
        self.assertEqual((), build_tree({}, [], lambda index, children: None))


if __name__ == "__main__":
    unittest.main()
