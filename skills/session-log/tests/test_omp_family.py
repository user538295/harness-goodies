"""The rules of one OMP session family (S10, S42): parent loops, the start and working time of a sub-agent, how children nest under their
parent by stem, and the root session or the orphan session built from them."""
import unittest
from datetime import timedelta

from sessionlog.model import Harness
from sessionlog.omp_family import Member, build_session, cyclic_keys
from tests.omp_records import assistant_record, at, header_record, helper_record, init_record, parse, usage_of, user_record

ROOT_STEM = "2026-10-13T08-42-00-000Z_root-id"
ROOT_ID = "root-id"
PARENT_PATH = "/home/u/.omp/agent/sessions/-p/%s/%s.jsonl"


def member(stem, *records, parent=None, key=None, modified_at=at(0)) -> Member:
    """A child file: `parent` is the stem its header names as parent (under the family folder)."""
    parent_session = PARENT_PATH % (ROOT_STEM, parent) if parent else None
    header = header_record(stem + "-id", second=3, parent=parent_session)
    return Member(key or "/store/" + stem, stem, modified_at, parse(header, *records))


def root_member(*records, header=None) -> Member:
    return Member("/store/root", ROOT_STEM, at(0), parse(header if header is not None else header_record(ROOT_ID, second=2), *records))


class CyclicKeysTest(unittest.TestCase):
    def test_the_members_of_a_loop_a_child_that_is_its_own_parent_and_every_child_hanging_from_one_are_cyclic(self):
        children = [member("A", parent="B"), member("B", parent="A"), member("Below", parent="A"), member("Self", parent="Self"),
                    member("Deeper", parent="Below"), member("Fine", parent=None), member("Chain", parent="Fine"), member("Lost", parent="Gone")]
        self.assertEqual({"/store/A", "/store/B", "/store/Below", "/store/Self", "/store/Deeper"}, cyclic_keys(children))

    def test_a_longer_loop_is_cyclic_all_round_whatever_the_order(self):
        names = ["A", "B", "C", "D"]
        for rotation in range(4):
            with self.subTest(rotation=rotation):
                ordered = names[rotation:] + names[:rotation]
                children = [member(name, parent=names[(names.index(name) + 1) % 4]) for name in ordered]
                self.assertEqual({"/store/" + name for name in names}, cyclic_keys(children))

    def test_a_child_whose_parent_is_the_root_or_no_child_is_not_cyclic(self):
        children = [member("One", parent=ROOT_STEM), member("Two", parent="Missing"), member("Three")]
        self.assertEqual(set(), cyclic_keys(children))

    def test_files_that_share_a_stem_are_one_node_the_first_of_them_counts_and_a_loop_through_it_makes_them_all_cyclic(self):
        children = [member("A", parent="B", key="/store/a1"), member("A", key="/store/a2"), member("B", parent="A")]
        self.assertEqual({"/store/a1", "/store/a2", "/store/B"}, cyclic_keys(children))

    def test_no_children_are_no_loops(self):
        self.assertEqual(set(), cyclic_keys([]))


class MemberTest(unittest.TestCase):
    def test_a_sub_agent_starts_with_its_first_record_after_the_header_and_works_until_its_newest_record(self):
        child = member("Task", init_record("explore"), user_record(20, "task text", attribution="agent"), helper_record(22, usage_of(1, 1)),
                       assistant_record(30, "done", usage=usage_of(2, 2), done=5))
        self.assertEqual((at(20), timedelta(seconds=15)), (child.started_at, child.working_time))

    def test_a_sub_agent_with_only_a_header_starts_at_its_header_and_works_no_time(self):
        child = member("Task")
        self.assertEqual((at(3), timedelta(0)), (child.started_at, child.working_time))

    def test_a_sub_agent_without_any_time_has_no_start_and_one_with_only_requests_starts_at_its_file_time(self):
        timeless = Member("/store/t", "t", at(100), parse({"type": "session", "id": "t"}))
        undated = Member("/store/u", "u", at(100), parse({"type": "session", "id": "u"}, {"type": "model_usage", "usage": usage_of(4, 4)}))
        self.assertEqual(((None, timedelta(0)), (at(100), timedelta(0))), ((timeless.started_at, timeless.working_time), (undated.started_at, undated.working_time)))

    def test_the_working_time_is_never_negative(self):
        child = Member("/store/n", "n", at(0), parse(header_record("n", second=50), {"type": "model_usage", "timestamp": "2026-10-13T08:42:10.000Z"}))
        self.assertEqual((at(10), timedelta(0)), (child.started_at, child.working_time))

    def test_the_agent_id_is_the_header_id_else_the_file_stem_and_the_parent_stem_is_that_of_the_parent_path(self):
        named = member("Task", parent="Other")
        unnamed = Member("/store/s", "Stemmed", at(0), parse({"type": "session", "timestamp": "2026-10-13T08:42:03.000Z", "parentSession": "C:\\x\\Dad.jsonl"}))
        self.assertEqual((("Task-id", "Other"), ("Stemmed", "Dad")), ((named.agent_id, named.parent_stem), (unnamed.agent_id, unnamed.parent_stem)))
        self.assertIsNone(member("Top").parent_stem)


def activity(second):
    return assistant_record(second, usage=usage_of(1, 1))


class RootSessionTest(unittest.TestCase):
    def test_the_session_has_the_roots_prompts_start_and_folder_and_every_child_nested_under_its_parent_by_the_parents_header_id(self):
        root = root_member(user_record(10, "ask"))
        children = [member("Late", activity(20), parent=ROOT_STEM), member("Early", activity(8), parent=ROOT_STEM), member("Inner", activity(9), parent="Early")]
        session = build_session(ROOT_ID, ROOT_STEM, root, children)
        self.assertEqual((Harness.OMP, ROOT_ID, at(2), "/work/proj", None, ["ask"]),
                         (session.harness, session.session_id, session.started_at, session.project_dir, session.orphan_of_parent_id, [p.text for p in session.prompts]))
        self.assertEqual([("Early-id", ["Inner-id"]), ("Late-id", [])], [(a.agent_id, [g.agent_id for g in a.sub_agents]) for a in session.sub_agents])

    def test_sub_agents_that_start_together_are_ordered_by_id(self):
        children = [member(name, activity(10), parent=ROOT_STEM) for name in ("B", "A", "C")]
        session = build_session(ROOT_ID, ROOT_STEM, root_member(user_record(10, "ask")), children)
        self.assertEqual(["A-id", "B-id", "C-id"], [a.agent_id for a in session.sub_agents])

    def test_a_child_without_a_parent_or_with_a_missing_parent_stays_at_the_top_level_and_makes_no_orphan(self):
        children = [member("NoParent", activity(13)), member("Lost", activity(14), parent="Gone"), member("Leaf", activity(15), parent="Lost")]
        session = build_session(ROOT_ID, ROOT_STEM, root_member(user_record(10, "ask")), children)
        self.assertEqual((None, [("Lost-id", ["Leaf-id"]), ("NoParent-id", [])]),
                         (session.orphan_of_parent_id, sorted((a.agent_id, [g.agent_id for g in a.sub_agents]) for a in session.sub_agents)))

    def test_a_child_is_nested_under_the_first_file_of_a_stem_that_several_files_share(self):
        children = [member("Dup", activity(13), parent=ROOT_STEM, key="/store/1"), Member("/store/2", "Dup", at(0), parse(header_record("other-dup", second=3), activity(14))),
                    member("Leaf", activity(15), parent="Dup")]
        session = build_session(ROOT_ID, ROOT_STEM, root_member(user_record(10, "ask")), children)
        self.assertEqual([("Dup-id", ["Leaf-id"]), ("other-dup", [])], [(a.agent_id, [g.agent_id for g in a.sub_agents]) for a in session.sub_agents])

    def test_a_child_with_no_start_is_left_out_and_the_roots_start_is_its_header_else_its_first_record_else_none(self):
        timeless = Member("/store/t", "t", at(0), parse({"type": "session", "id": "t"}))
        session = build_session(ROOT_ID, ROOT_STEM, root_member(user_record(10, "ask")), [timeless])
        self.assertEqual(((), at(2)), (session.sub_agents, session.started_at))
        self.assertEqual(at(10), build_session(ROOT_ID, ROOT_STEM, root_member(user_record(10, "ask"), header={"type": "title"}), []).started_at)
        self.assertIsNone(build_session(ROOT_ID, ROOT_STEM, root_member(header={"type": "session"}), []))

    def test_a_root_without_a_start_gives_no_session_whatever_its_children(self):
        self.assertIsNone(build_session(ROOT_ID, ROOT_STEM, root_member(header={"type": "session"}), [member("Task", activity(13), parent=ROOT_STEM)]))


class OrphanSessionTest(unittest.TestCase):
    def test_without_the_root_the_first_child_with_a_prompt_is_the_session_and_the_others_are_its_sub_agents_labelled_with_its_parent(self):
        promoted = member("Asked", user_record(12, "child prompt"), activity(13), parent=ROOT_STEM)
        other = member("Other", user_record(14, "hidden prompt"), activity(15), parent=ROOT_STEM)
        child_of_promoted = member("Leaf", activity(16), parent="Asked")
        session = build_session(ROOT_ID, ROOT_STEM, None, [promoted, other, child_of_promoted])
        self.assertEqual((ROOT_ID, ROOT_ID, "/work/proj", ["child prompt"], at(12), ["Other-id", "Leaf-id"]),
                         (session.session_id, session.orphan_of_parent_id, session.project_dir, [p.text for p in session.prompts], session.started_at,
                          [a.agent_id for a in session.sub_agents]))

    def test_the_label_is_the_parent_stem_when_no_file_of_the_family_has_it_and_the_root_id_when_it_is_the_root(self):
        gone_middle = build_session(ROOT_ID, ROOT_STEM, None, [member("Leaf", activity(13), parent="Missing")])
        gone_root = build_session(ROOT_ID, ROOT_STEM, None, [member("Task", activity(13), parent=ROOT_STEM)])
        no_parent = build_session(ROOT_ID, ROOT_STEM, None, [member("Task", activity(13))])
        self.assertEqual(("Missing", ROOT_ID, ROOT_ID), (gone_middle.orphan_of_parent_id, gone_root.orphan_of_parent_id, no_parent.orphan_of_parent_id))

    def test_without_the_root_and_without_any_child_with_a_start_there_is_no_session(self):
        self.assertIsNone(build_session(ROOT_ID, ROOT_STEM, None, []))
        self.assertIsNone(build_session(ROOT_ID, ROOT_STEM, None, [Member("/store/t", "t", at(0), parse({"type": "session", "id": "t"}))]))


if __name__ == "__main__":
    unittest.main()
