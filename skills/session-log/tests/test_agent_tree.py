"""The shared nesting rules of sub-agents (agent_tree): parent indexes, depths and the deepest-first tree build."""
import unittest
from datetime import datetime, timezone

from sessionlog.agent_tree import build_tree, depths_of, parent_indexes
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
