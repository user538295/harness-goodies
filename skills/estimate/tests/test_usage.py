"""`usage <file.md> --session-log <path>` (C3): attaches the sessions that worked on a feature to its stored file before any commit
exists. It runs the `match-sessions` rule over every stored feature (docs and recorded commits; a name two features share is no
evidence), saves only the feature that lists the file, as `init` saves it (`sources.sessions`, `usage.matchedBy`), only ever adds
or refreshes sessions (a stored session is never dropped, nor shrunk, nor are those `implement` recorded), saves nothing without a
matched session, describes the saved file in its output, and gives the same result when run again."""
import os
import unittest

from estimation.features import derive_usage
from tests.support import (EXIT_BAD_ARGUMENTS, EXIT_OK, EXIT_RUNTIME_ERROR, ProjectTestCase, agent_row, document, list_entry,
                           listed_with_rows, new_feature, prompt_row, tokens_of, usage_payload)

HARNESSES = ("claude", "codex", "cursor", "omp", "opencode")
BRIEF = "docs/x-brief.md"
OTHER_BRIEF = "docs/y-brief.md"
PLAN = "docs/x-plan.md"
TEN_MINUTES = 600


def at(clock):
    return "2026-10-01T%s+00:00" % clock


def listing(*docs):
    return {"docs": list(docs), "commits": [], "sessions": []}


def recorded(session_id, numbers=(), minutes=3, harness="claude"):
    """A stored session entry counting the prompts `numbers`."""
    return {"harness": harness, "models": None, "id": session_id, "path": None, "log": None,
            "prompts": [{"number": number, "at": at("09:00:00"), "text": "t", "reason": "names " + BRIEF} for number in numbers],
            "agentTimeMinutes": minutes, "tokens": tokens_of(100), "costUsd": 1.0}


def stored_feature(*sessions, matched_by="prompt"):
    """The feature X listing the brief with `sessions` stored and the usage derived from them."""
    feature = new_feature("X", sources={"docs": [BRIEF], "commits": [], "sessions": list(sessions)})
    derive_usage(feature, matched_by)
    return feature


class UsageTest(ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.session_log = self.fake_session_log()
        self.lists = {harness: [] for harness in HARNESSES}
        for harness in HARNESSES:
            self.session_log.reply(harness + " list --json", [])

    def session(self, harness, session_id, rows, path=None, agents=()):
        self.lists[harness].append(listed_with_rows(harness, session_id, rows, path))
        self.session_log.reply(harness + " list --json", self.lists[harness])
        self.session_log.reply("%s usage %s --json" % (harness, session_id),
                               usage_payload(harness, session_id, 0, path=path, prompts=list(rows), sub_agents=list(agents)))

    def usage(self, doc=BRIEF):
        return self.run_estimate("usage", doc, "--session-log", self.session_log.path)

    def run_usage(self, doc=BRIEF):
        result = self.usage(doc)
        self.assertEqual(EXIT_OK, result.code, result.err)
        return result.json

    def test_a_session_whose_prompts_name_the_brief_is_attached(self):
        self.seed(document(new_feature("X", sources=listing(BRIEF))))
        self.session("claude", "s1", [prompt_row(at("10:00:00"), "estimate docs/x-brief.md", seconds=TEN_MINUTES)],
                     path="/abs/s1.jsonl")
        output = self.run_usage()
        feature = self.feature("X")
        self.assertEqual("prompt", output["matchedBy"])
        self.assertEqual(["s1"], [session["id"] for session in output["sessions"]])
        self.assertEqual(output["sessions"], feature["sources"]["sessions"])
        self.assertEqual(("prompt", 1, 10), (feature["usage"]["matchedBy"], feature["usage"]["sessions"],
                                             feature["usage"]["agentTimeMinutes"]))
        self.assertEqual(output["usage"], feature["usage"])
        self.assertEqual({"feature": feature, "matchedBy": "prompt", "sessions": output["sessions"], "usage": feature["usage"],
                          "missing": []}, output)

    def test_a_prompt_that_wrote_the_brief_without_naming_it_counts_for_the_feature(self):
        self.seed(document(new_feature("X", sources=listing(BRIEF))))
        self.session("omp", "writer", [prompt_row(at("10:00:00"), "write the brief", written=["/abs/project/" + BRIEF]),
                                       prompt_row(at("10:30:00"), "check docs/x-brief.md again")])
        self.session("omp", "bystander", [prompt_row(at("11:00:00"), "write something", written=["/abs/project/docs/z.md"])])
        output = self.run_usage()
        self.assertEqual(["writer"], [session["id"] for session in output["sessions"]])
        self.assertEqual(["writes " + BRIEF, "names " + BRIEF], [prompt["reason"] for prompt in output["sessions"][0]["prompts"]])
        self.assertEqual(output["sessions"], self.feature("X")["sources"]["sessions"])

    def test_without_a_matching_session_nothing_is_saved(self):
        feature = new_feature("X", sources=listing(BRIEF))
        self.seed(document(feature))
        self.session("claude", "other", [prompt_row(at("10:00:00"), "unrelated")])
        output = self.run_usage()
        self.assertEqual(("none", []), (output["matchedBy"], output["sessions"]))
        self.assertIn("nothing saved", output["missing"][0])
        self.assertEqual(feature, self.feature("X"))

    def test_sessions_already_stored_are_not_lost_when_none_match(self):
        feature = stored_feature(recorded("old"))
        self.seed(document(feature))
        output = self.run_usage()
        self.assertEqual(("prompt", ["old"]), (output["matchedBy"], [session["id"] for session in output["sessions"]]))
        self.assertEqual(feature, self.feature("X"))

    def test_a_new_session_is_added_to_the_stored_ones_and_the_output_describes_the_saved_file(self):
        self.seed(document(stored_feature(recorded("rotated-out", minutes=3))))
        self.session("claude", "s1", [prompt_row(at("10:00:00"), "docs/x-brief.md", seconds=TEN_MINUTES)])
        output = self.run_usage()
        feature = self.feature("X")
        self.assertEqual(["rotated-out", "s1"], [session["id"] for session in feature["sources"]["sessions"]])
        self.assertEqual((2, 13), (feature["usage"]["sessions"], feature["usage"]["agentTimeMinutes"]))
        self.assertEqual({"feature": feature, "matchedBy": "prompt", "sessions": feature["sources"]["sessions"],
                          "usage": feature["usage"], "missing": []}, output)

    def test_stored_sessions_survive_a_failed_session_log_call(self):
        self.seed(document(stored_feature(recorded("stored-by-codex", harness="codex"))))
        self.session_log.reply("codex list --json", None, code=EXIT_RUNTIME_ERROR, stderr="codex store unreadable")
        self.session("claude", "s1", [prompt_row(at("10:00:00"), "docs/x-brief.md")])
        output = self.run_usage()
        self.assertEqual([("codex", "stored-by-codex"), ("claude", "s1")],
                         [(session["harness"], session["id"]) for session in self.feature("X")["sources"]["sessions"]])
        self.assertIn("session-log codex list failed (exit 1): codex store unreadable", output["missing"])

    def test_a_stored_session_is_refreshed_with_the_fresh_numbers_when_it_keeps_every_counted_prompt(self):
        self.seed(document(stored_feature(recorded("s1", numbers=[1], minutes=1))))
        self.session("claude", "s1", [prompt_row(at("10:00:00"), "docs/x-brief.md", seconds=60),
                                      prompt_row(at("10:10:00"), "go", seconds=TEN_MINUTES)])
        self.run_usage()
        counted = self.feature("X")["sources"]["sessions"]
        self.assertEqual([("s1", [1, 2], 11)], [(session["id"], [prompt["number"] for prompt in session["prompts"]],
                                                 session["agentTimeMinutes"]) for session in counted])
        self.assertEqual((1, 11), (self.feature("X")["usage"]["sessions"], self.feature("X")["usage"]["agentTimeMinutes"]))

    def test_a_stored_session_is_kept_when_the_fresh_slice_lacks_a_prompt_it_counts(self):
        feature = stored_feature(recorded("s1", numbers=[1, 2, 3], minutes=9), recorded("other", numbers=[1]))
        self.seed(document(feature))
        self.session("claude", "s1", [prompt_row(at("10:00:00"), "docs/x-brief.md", seconds=60)])
        output = self.run_usage()
        self.assertEqual(feature, self.feature("X"))
        self.assertEqual(feature["sources"]["sessions"], output["sessions"])

    def test_sessions_recorded_by_implement_are_kept(self):
        feature = stored_feature(recorded("recorded", numbers=[1, 2]), matched_by="implement")
        self.seed(document(feature))
        self.session("claude", "s1", [prompt_row(at("10:00:00"), "estimate docs/x-brief.md")])
        output = self.run_usage()
        self.assertEqual("implement", output["matchedBy"])
        self.assertEqual(["recorded"], [session["id"] for session in output["sessions"]])
        self.assertIn("implement", output["missing"][0])
        self.assertEqual(feature, self.feature("X"))

    def test_a_brief_named_by_two_features_is_no_evidence_but_the_plan_one_owns_is(self):
        self.seed(document(new_feature("X", sources=listing(BRIEF, PLAN)), new_feature("Y", sources=listing(BRIEF))))
        self.session("claude", "brief", [prompt_row(at("10:00:00"), "read docs/x-brief.md")])
        self.session("claude", "plan", [prompt_row(at("11:00:00"), "read docs/x-plan.md")])
        output = self.run_usage(PLAN)
        self.assertEqual("X", output["feature"]["name"])
        self.assertEqual(["plan"], [session["id"] for session in output["sessions"]])
        self.assertEqual([], self.feature("Y")["sources"]["sessions"])

    def test_another_feature_is_never_written(self):
        self.seed(document(new_feature("X", sources=listing(BRIEF)), new_feature("Y", sources=listing(OTHER_BRIEF))))
        self.session("claude", "s1", [prompt_row(at("10:00:00"), "docs/x-brief.md and docs/y-brief.md")])
        self.run_usage()
        self.assertEqual(["s1"], [session["id"] for session in self.feature("X")["sources"]["sessions"]])
        self.assertEqual(new_feature("Y", sources=listing(OTHER_BRIEF)), self.feature("Y"))

    def test_a_session_that_wrote_the_brief_without_being_named_or_committed_is_found_from_the_first_day_on(self):
        self.seed(document(new_feature("X", sources=listing(BRIEF))))
        self.session("omp", "writer", [prompt_row("2026-09-30T20:00:00+00:00", "go", written=["/abs/project/docs/z.md"]),
                                       prompt_row("2026-10-02T09:00:00+00:00", "write the brief", written=["/abs/project/" + BRIEF])])
        output = self.run_usage()
        self.assertEqual(["writer"], [session["id"] for session in output["sessions"]])
        self.assertEqual(["before prompt 2", "writes " + BRIEF], [prompt["reason"] for prompt in output["sessions"][0]["prompts"]])

    def test_a_session_that_ended_before_the_first_day_is_not_read(self):
        self.seed(document(new_feature("X", sources=listing(BRIEF))))
        self.session("omp", "early", [prompt_row("2026-09-25T09:00:00+00:00", "write", written=["/abs/project/" + BRIEF])])
        output = self.run_usage()
        self.assertEqual([], output["sessions"])
        self.assertEqual([], [call for call in self.session_log.calls() if call["argv"][1] == "usage"])

    def test_a_session_whose_last_reply_is_exactly_the_first_day_less_the_timezone_margin_is_read(self):
        self.seed(document(new_feature("X", sources=listing(BRIEF))))
        row = prompt_row("2026-09-30T09:59:00+00:00", "write", seconds=60, written=["/abs/project/" + BRIEF])  # replies at 10:00 UTC
        self.session("omp", "inside", [row])
        self.assertEqual(["inside"], [session["id"] for session in self.run_usage()["sessions"]])

    def test_a_session_whose_last_reply_is_a_second_before_the_first_day_less_the_timezone_margin_is_not_read(self):
        self.seed(document(new_feature("X", sources=listing(BRIEF))))
        self.session("omp", "outside", [prompt_row("2026-09-30T09:58:59+00:00", "write", seconds=60, written=["/abs/project/" + BRIEF])])
        self.assertEqual([], self.run_usage()["sessions"])
        self.assertEqual([], [call for call in self.session_log.calls() if call["argv"][1] == "usage"])

    def test_a_sub_agent_that_wrote_the_brief_makes_the_prompt_it_started_under_count_as_writing_it(self):
        self.seed(document(new_feature("X", sources=listing(BRIEF))))
        self.session("omp", "delegator", [prompt_row(at("10:00:00"), "write the brief", seconds=TEN_MINUTES)],
                     agents=[agent_row(at("10:01:00"), written=["/abs/project/" + BRIEF])])
        self.run_usage()
        counted = self.feature("X")["sources"]["sessions"]
        self.assertEqual([["writes " + BRIEF]], [[prompt["reason"] for prompt in session["prompts"]] for session in counted])

    def test_a_recent_session_without_a_start_is_not_attributed_by_commit_times(self):
        commit = self.commit("brief", {BRIEF: "brief\n"}, when="2026-10-01T12:00:00+00:00")
        self.seed(document(new_feature("X", sources={"docs": [BRIEF], "commits": [commit], "sessions": []})))
        self.lists["claude"].append(list_entry("claude", "no-start", None, at("13:00:00"), ["unrelated"]))
        self.session_log.reply("claude list --json", self.lists["claude"])
        output = self.run_usage()
        self.assertEqual([], output["sessions"])
        self.assertIn("session-log claude usage no-start failed", output["missing"][0])

    def test_a_second_run_changes_nothing(self):
        self.seed(document(new_feature("X", sources=listing(BRIEF))))
        self.session("claude", "s1", [prompt_row(at("10:00:00"), "estimate docs/x-brief.md")])
        first = self.run_usage()
        stored = self.feature("X")
        self.assertEqual(first, self.run_usage())
        self.assertEqual(stored, self.feature("X"))
        self.assertEqual(1, self.feature("X")["usage"]["sessions"])

    def test_a_session_found_later_is_added_to_the_stored_one(self):
        self.seed(document(new_feature("X", sources=listing(BRIEF))))
        self.session("claude", "s1", [prompt_row(at("10:00:00"), "docs/x-brief.md", seconds=60)])
        self.run_usage()
        self.session("claude", "s2", [prompt_row(at("11:00:00"), "docs/x-brief.md", seconds=TEN_MINUTES)])
        self.run_usage()
        self.assertEqual((2, 11), (self.feature("X")["usage"]["sessions"], self.feature("X")["usage"]["agentTimeMinutes"]))

    def test_a_file_no_feature_lists_is_refused(self):
        self.seed(document(new_feature("X", sources=listing(BRIEF))))
        self.assert_error(self.usage("docs/none.md"), EXIT_RUNTIME_ERROR,
                          "no feature lists docs/none.md; run /estimate docs/none.md first")

    def test_a_file_two_features_list_is_refused_naming_both(self):
        self.seed(document(new_feature("X", file="2026-10-01-x.json", sources=listing(BRIEF)),
                           new_feature("Y", file="2026-10-02-y.json", sources=listing(BRIEF))))
        self.assert_error(self.usage(), EXIT_RUNTIME_ERROR, "2 features list docs/x-brief.md: 2026-10-01-x.json, 2026-10-02-y.json")

    def test_a_missing_session_log_script_is_refused(self):
        self.seed(document(new_feature("X", sources=listing(BRIEF))))
        missing = os.path.join(self.base, "nowhere", "session_log.py")
        self.assert_error(self.run_estimate("usage", BRIEF, "--session-log", missing), EXIT_RUNTIME_ERROR, "session-log not found")

    def test_the_session_log_option_is_required(self):
        self.assert_error(self.run_estimate("usage", BRIEF), EXIT_BAD_ARGUMENTS, "usage needs --session-log <value>")

    def test_the_file_is_required(self):
        self.assert_error(self.run_estimate("usage", "--session-log", "x"), EXIT_BAD_ARGUMENTS, "usage needs <file.md>")


if __name__ == "__main__":
    unittest.main()
