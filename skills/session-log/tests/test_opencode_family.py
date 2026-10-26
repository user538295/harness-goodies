"""Pure rules of one OpenCode family: the root a `parent_id` chain ends at, and the session its root and sub-agent sessions make. No database
is opened here."""
import unittest
from datetime import timedelta

from sessionlog.model import Harness, Prompt, RequestUsage
from sessionlog.opencode_family import SessionRow, build_session, root_id_for
from sessionlog.opencode_messages import Transcript
from sessionlog.prompt_turns import NO_PROMPT_TEXT
from tests.opencode_db import BASE


def at(second):
    return BASE + timedelta(seconds=second)


def row(session_id, parent=None, created=0, directory="/work/oc", agent=None):
    return SessionRow(session_id, parent, None if created is None else at(created), directory, agent)


def request(tokens, cost=None):
    return RequestUsage(model_name="m", input_tokens=tokens, output_tokens=0, cache_read_tokens=0, cache_write_5m_tokens=0,
                        cache_write_1h_tokens=0, recorded_cost_usd=cost)


def transcript(*prompt_texts, last=None, requests=(), unprompted=()):
    prompts = tuple(Prompt(submitted_at=at(10 + number), text=text) for number, text in enumerate(prompt_texts))
    return Transcript(prompts=prompts, requests=requests, unprompted_requests=unprompted, last_moment=None if last is None else at(last))


class RootIdForTest(unittest.TestCase):
    PARENTS = {"root": None, "child": "root", "grand": "child", "stray": "deleted", "a": "b", "b": "a", "hanging": "a"}

    def test_the_root_is_the_topmost_session_of_the_chain(self):
        self.assertEqual(["root", "root", "root"], [root_id_for(name, self.PARENTS, set()) for name in ("root", "child", "grand")])

    def test_a_session_whose_parent_is_no_session_is_the_root_of_its_own_family(self):
        self.assertEqual("stray", root_id_for("stray", self.PARENTS, set()))

    def test_a_session_that_is_not_known_yet_is_its_own_root(self):
        self.assertEqual("brand-new", root_id_for("brand-new", self.PARENTS, set()))

    def test_a_loop_the_caller_did_not_name_ends_the_walk_instead_of_hanging(self):
        self.assertIn(root_id_for("a", self.PARENTS, set()), ("a", "b"))
        self.assertIn(root_id_for("hanging", self.PARENTS, set()), ("a", "b", "hanging"))

    def test_a_looping_session_is_its_own_root_and_is_never_walked(self):
        self.assertEqual(["a", "b", "hanging"], [root_id_for(name, self.PARENTS, {"a", "b", "hanging"}) for name in ("a", "b", "hanging")])


class BuildSessionTest(unittest.TestCase):
    def test_the_root_gives_the_prompts_and_its_folder_and_the_children_nest_by_the_session_their_parent_id_names(self):
        rows = [(row("child", "root", created=20, agent="general"), transcript("task", last=26)),
                (row("grand", "child", created=22, agent="explore"), transcript(last=24)),
                (row("sibling", "root", created=21), transcript(last=21))]
        session = build_session(row("root", created=10), transcript("first", "second"), {"root": None, "child": "root", "grand": "child", "sibling": "root"}, rows)
        self.assertEqual((Harness.OPENCODE, "root", at(10), "/work/oc", None, ["first", "second"]),
                         (session.harness, session.session_id, session.started_at, session.project_dir, session.orphan_of_parent_id,
                          [prompt.text for prompt in session.prompts]))
        self.assertEqual(["child", "sibling"], [agent.agent_id for agent in session.sub_agents])
        child = session.sub_agents[0]
        self.assertEqual((timedelta(seconds=6), "general", ["grand"]), (child.working_time, child.type, [agent.agent_id for agent in child.sub_agents]))

    def test_a_sub_agent_without_an_agent_is_unknown_and_one_without_a_start_starts_when_the_root_does(self):
        session = build_session(row("root", created=10), transcript(), {"root": None, "child": "root"}, [(row("child", "root", created=None), transcript(last=15))])
        (child,) = session.sub_agents
        self.assertEqual(("unknown", at(10), timedelta(seconds=5)), (child.type, child.started_at, child.working_time))

    def test_a_root_whose_parent_is_no_session_is_labelled_with_the_parent_it_names(self):
        session = build_session(row("stray", "deleted"), transcript("ask"), {"stray": "deleted"}, [])
        self.assertEqual("deleted", session.orphan_of_parent_id)

    def test_a_root_whose_parent_is_a_session_or_none_is_not_labelled(self):
        self.assertIsNone(build_session(row("root"), transcript(), {"root": None}, []).orphan_of_parent_id)
        self.assertIsNone(build_session(row("child", "root"), transcript(), {"root": None, "child": "root"}, []).orphan_of_parent_id)

    def test_a_root_whose_row_holds_no_start_time_gives_no_session(self):
        self.assertIsNone(build_session(row("root", created=None), transcript("ask"), {"root": None}, []))


class UnpromptedRequestsTest(unittest.TestCase):
    def test_requests_that_fall_under_no_prompt_lead_the_prompts_as_a_row_dated_by_the_session_start(self):
        session = build_session(row("root", created=5), transcript("first", unprompted=(request(1000, 3.5),)), {"root": None}, [])
        lead, first = session.prompts
        self.assertEqual((at(5), NO_PROMPT_TEXT, [1000], [3.5], "first"),
                         (lead.submitted_at, lead.text, [r.input_tokens for r in lead.requests], [r.recorded_cost_usd for r in lead.requests], first.text))

    def test_a_session_with_no_prompt_at_all_still_reports_its_requests(self):
        requests = (request(1000, 3.5),)
        session = build_session(row("root", created=5), transcript(requests=requests, unprompted=requests), {"root": None}, [])
        (lead,) = session.prompts
        self.assertEqual((NO_PROMPT_TEXT, requests), (lead.text, lead.requests))

    def test_a_session_without_such_requests_has_only_its_own_prompts(self):
        session = build_session(row("root", created=5), transcript("first"), {"root": None}, [])
        self.assertEqual(["first"], [prompt.text for prompt in session.prompts])


if __name__ == "__main__":
    unittest.main()
