"""`record-commit <plan-file> <commit> --harness <h> [--session-log <path>] [--session-dir <folder>]` (C3), the /implement step after
each task commit: the commit's lines are added once per hash, the plan is refreshed from doc-stats, the feature's slice of the latest
session of the session folder (the folder the running session was started in; the project root when not given; the prompts that
name its docs or made its commits, by the `match-sessions` rule over every stored feature) replaces its older numbers, and the
status follows the ticked tasks."""
import os
import unittest

from tests.support import (EXIT_BAD_ARGUMENTS, EXIT_OK, EXIT_RUNTIME_ERROR, FEATURES_PATH, ProjectTestCase, agent_row,
                           before_effort, document, new_feature, prompt_row, tokens_of, usage_payload)

PLAN = "docs/x-tasks.md"
TASKS_OPEN = """## Task Breakdown

### Phase 1 · Core
- [ ] **BE-1** — Store the set #backend-role
    - Entity · 2.0h
- [ ] **BE-2** — Render the set #backend-role
    - Presentation · 3.0h
"""
TASKS_ONE_DONE = TASKS_OPEN.replace("- [ ] **BE-1**", "- [x] **BE-1**")
TASKS_ALL_DONE = TASKS_ONE_DONE.replace("- [ ] **BE-2**", "- [x] **BE-2**")
TEN_MINUTES = 600
THREE_HUNDRED_MINUTES = 18000
OTHER_PLAN = "docs/y-tasks.md"


def at(clock):
    return "2026-10-01T%s+00:00" % clock


class RecordCommitTest(ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.commit("plan", {PLAN: TASKS_OPEN})
        self.session_log = self.fake_session_log()

    def seed_feature(self, *others, **fields):
        self.seed(document(new_feature("X", storyPoints=5, estimatedAt="2026-10-01",
                                       sources={"docs": ["docs/x-brief.md", PLAN], "commits": [], "sessions": []}, **fields),
                           *others))

    def latest_usage(self, seconds, session_id="s1", rows=None, agents=(), **keywords):
        """The latest session; by default one prompt naming the plan that ran `seconds` around the commits' time."""
        rows = [prompt_row(at("11:59:00"), "/implement " + PLAN, seconds=seconds)] if rows is None else rows
        self.session_log.reply("claude usage --latest --json",
                               usage_payload("claude", session_id, seconds, path="/abs/%s.jsonl" % session_id, prompts=rows,
                                             sub_agents=list(agents), **keywords))

    def record(self, commit, *extra, plan=PLAN, cwd=None):
        result = self.run_estimate("record-commit", plan, commit, "--harness", "claude", *extra, cwd=cwd)
        self.assertEqual(EXIT_OK, result.code, result.err)
        return result.json

    def record_with_session(self, commit):
        return self.record(commit, "--session-log", self.session_log.path)

    def test_the_first_task_commit_adds_its_lines_plan_and_session_and_starts_the_work(self):
        self.seed_feature()
        c1 = self.commit("BE-1 store", {"src/store.py": "a\nb\nc\n", PLAN: TASKS_ONE_DONE})
        self.latest_usage(TEN_MINUTES)
        output = self.record_with_session(c1)
        feature = output["feature"]
        # store.py +3; ticking BE-1 is +1 -1.
        self.assertEqual({"commits": 1, "linesAdded": 4, "linesRemoved": 1}, feature["git"])
        self.assertEqual([c1], feature["sources"]["commits"])
        # 1 phase, 2 tasks, 2.0 + 3.0 hours.
        self.assertEqual({"phases": 1, "tasks": 2, "estimatedHours": 5.0}, feature["plan"])
        self.assertEqual("in-progress", feature["status"])
        self.assertEqual(("implement", 1, 10), (feature["usage"]["matchedBy"], feature["usage"]["sessions"],
                                                feature["usage"]["agentTimeMinutes"]))
        self.assertEqual("/abs/s1.jsonl", feature["sources"]["sessions"][0]["path"])
        self.assertEqual({"feature": feature, "needsResize": False, "missing": []}, output)
        self.assertEqual(feature, self.feature("X"))

    def test_only_the_features_own_file_is_written(self):
        self.seed_feature(new_feature("Y", sources={"docs": [OTHER_PLAN], "commits": [], "sessions": []}))
        other = self.path(FEATURES_PATH + "/" + new_feature("Y")["file"])
        with open(other, encoding="utf-8") as handle:
            before = handle.read()
        self.record(self.commit("BE-1 store", {PLAN: TASKS_ONE_DONE}))
        with open(other, encoding="utf-8") as handle:
            self.assertEqual(before, handle.read())

    def test_a_recorded_commit_names_the_duplicates_of_the_other_files(self):
        self.seed_feature(new_feature("Y", file="2026-10-01-y.json"), new_feature("Y", file="2026-10-02-y.json"))
        result = self.run_estimate("record-commit", PLAN, self.commit("BE-1 store", {PLAN: TASKS_ONE_DONE}), "--harness", "claude")
        self.assertEqual(EXIT_OK, result.code, result.err)
        self.assertIn("estimate: duplicate name 'Y': 2026-10-01-y.json, 2026-10-02-y.json\n", result.err)

    def test_a_plan_two_feature_files_list_is_refused_naming_both(self):
        listing = {"docs": [PLAN], "commits": [], "sessions": []}
        self.seed(document(new_feature("X", file="2026-10-01-x.json", sources=listing),
                           new_feature("X", file="2026-10-02-x.json", sources=listing)))
        c1 = self.commit("BE-1", {PLAN: TASKS_ONE_DONE})
        self.assert_error(self.run_estimate("record-commit", PLAN, c1, "--harness", "claude"), EXIT_RUNTIME_ERROR,
                          "2 features list docs/x-tasks.md: 2026-10-01-x.json, 2026-10-02-x.json")

    def test_a_session_read_again_keeps_its_newer_numbers_and_the_last_tick_delivers(self):
        self.seed_feature()
        c1 = self.commit("BE-1 store", {"src/store.py": "a\nb\nc\n", PLAN: TASKS_ONE_DONE})
        self.latest_usage(TEN_MINUTES)
        self.record_with_session(c1)
        c2 = self.commit("BE-2 render", {"src/render.py": "r\n", PLAN: TASKS_ALL_DONE})
        self.latest_usage(THREE_HUNDRED_MINUTES)
        output = self.record_with_session(c2)
        feature = output["feature"]
        self.assertEqual(1, feature["usage"]["sessions"])
        self.assertEqual(300, feature["usage"]["agentTimeMinutes"])
        self.assertEqual(300, feature["sources"]["sessions"][0]["agentTimeMinutes"])
        # c1 +4 -1; c2 render.py +1, ticking BE-2 +1 -1.
        self.assertEqual({"commits": 2, "linesAdded": 6, "linesRemoved": 2}, feature["git"])
        self.assertEqual("delivered", feature["status"])
        self.assertTrue(output["needsResize"])
        self.assertEqual(5, feature["storyPoints"])

    def test_a_repeated_hash_is_counted_once(self):
        self.seed_feature()
        c1 = self.commit("BE-1 store", {"src/store.py": "a\nb\nc\n", PLAN: TASKS_ONE_DONE})
        self.record(c1)
        feature = self.record(c1[:10])["feature"]
        self.assertEqual({"commits": 1, "linesAdded": 4, "linesRemoved": 1}, feature["git"])
        self.assertEqual([c1], feature["sources"]["commits"])

    def test_a_delivered_feature_with_an_after_delivery_size_needs_no_resize(self):
        self.seed_feature(updatedStoryPoints=8)
        c1 = self.commit("all", {PLAN: TASKS_ALL_DONE})
        output = self.record(c1)
        self.assertEqual("delivered", output["feature"]["status"])
        self.assertFalse(output["needsResize"])

    def test_a_feature_saved_before_efforts_still_takes_its_commits(self):
        self.seed(document(before_effort(new_feature("X", storyPoints=5, sources={"docs": [PLAN], "commits": [], "sessions": []}))))
        c1 = self.commit("BE-1 store", {PLAN: TASKS_ONE_DONE})
        feature = self.record(c1)["feature"]
        self.assertEqual(([c1], "in-progress"), (feature["sources"]["commits"], feature["status"]))
        self.assertNotIn("effort", self.feature("X"))

    def test_the_label_stays_the_weakest_of_prompt_and_implement(self):
        self.seed_feature(usage={"matchedBy": "prompt", "sessions": 0, "agentTimeMinutes": None,
                                 "tokens": dict.fromkeys(("input", "output", "reasoning", "cacheRead", "cacheWrite", "total")),
                                 "costUsd": None})
        c1 = self.commit("BE-1", {PLAN: TASKS_ONE_DONE})
        self.latest_usage(TEN_MINUTES)
        self.assertEqual("prompt", self.record_with_session(c1)["feature"]["usage"]["matchedBy"])

    def test_only_the_prompts_of_the_feature_are_recorded_from_a_session_that_also_worked_on_another(self):
        self.seed_feature(new_feature("Y", sources={"docs": [OTHER_PLAN], "commits": [], "sessions": []}))
        c1 = self.commit("BE-1", {PLAN: TASKS_ONE_DONE}, when=at("11:40:00"))
        self.latest_usage(0, rows=[prompt_row(at("10:00:00"), "/implement " + OTHER_PLAN, seconds=3000, total=4000, cost=4.0),
                                   prompt_row(at("11:00:00"), "go", seconds=600, total=400, cost=0.4),
                                   prompt_row(at("11:30:00"), "/implement docs/x-tasks.md", seconds=600, total=200, cost=0.2),
                                   prompt_row(at("11:45:00"), "A", seconds=60, total=100, cost=0.1)],
                          agents=[agent_row(at("11:31:00"), total=1000, cost=1.0)])
        feature = self.record_with_session(c1)["feature"]
        # Prompts 3-4 (600 + 60 s) and the agent started under prompt 3; prompts 1-2 worked on Y.
        self.assertEqual([{"harness": "claude", "models": ["agent-model", "main-model"], "id": "s1", "path": "/abs/s1.jsonl", "log": None,
                           "prompts": [{"number": 3, "at": at("11:30:00"), "text": "/implement docs/x-tasks.md",
                                        "reason": "names docs/x-tasks.md"},
                                       {"number": 4, "at": at("11:45:00"), "text": "A", "reason": "follows prompt 3"}],
                           "agentTimeMinutes": 11, "tokens": tokens_of(1300), "costUsd": 1.3}], feature["sources"]["sessions"])
        self.assertEqual(("implement", 11), (feature["usage"]["matchedBy"], feature["usage"]["agentTimeMinutes"]))

    def test_a_prompt_that_made_the_commit_belongs_to_the_feature_without_naming_it(self):
        self.seed_feature(new_feature("Y", sources={"docs": [OTHER_PLAN], "commits": [], "sessions": []}))
        c1 = self.commit("BE-1", {PLAN: TASKS_ONE_DONE}, when=at("11:05:00"))
        self.latest_usage(0, rows=[prompt_row(at("10:00:00"), "/implement " + OTHER_PLAN, seconds=600),
                                   prompt_row(at("11:00:00"), "continue", seconds=600)])
        self.assertEqual([(2, "made 1 of its commits")],
                         [(prompt["number"], prompt["reason"])
                          for prompt in self.record_with_session(c1)["feature"]["sources"]["sessions"][0]["prompts"]])

    def test_prompts_before_a_cleared_context_are_not_recorded_for_the_feature_that_follows_it(self):
        self.seed_feature()
        c1 = self.commit("BE-1", {PLAN: TASKS_ONE_DONE}, when=at("11:40:00"))
        self.latest_usage(0, rows=[prompt_row(at("10:00:00"), "tidy my notes", seconds=3000, total=4000, cost=4.0),
                                   prompt_row(at("11:30:00"), "/implement docs/x-tasks.md", seconds=600, total=200, cost=0.2, after_reset=True),
                                   prompt_row(at("11:45:00"), "A", seconds=60, total=100, cost=0.1)])
        feature = self.record_with_session(c1)["feature"]
        self.assertEqual([(2, "names docs/x-tasks.md"), (3, "follows prompt 2")],
                         [(prompt["number"], prompt["reason"]) for prompt in feature["sources"]["sessions"][0]["prompts"]])
        self.assertEqual(11, feature["usage"]["agentTimeMinutes"])

    def test_a_latest_session_without_a_prompt_of_the_feature_records_no_session(self):
        self.seed_feature(new_feature("Y", sources={"docs": [OTHER_PLAN], "commits": [], "sessions": []}))
        c1 = self.commit("BE-1", {PLAN: TASKS_ONE_DONE}, when=at("09:00:00"))
        self.latest_usage(0, rows=[prompt_row(at("10:00:00"), "/implement " + OTHER_PLAN, seconds=600)])
        output = self.record_with_session(c1)
        self.assertEqual([], output["feature"]["sources"]["sessions"])
        self.assertIsNone(output["feature"]["usage"])
        self.assertEqual(["the latest claude session s1 shows no prompt of X"], output["missing"])

    def test_a_latest_session_without_prompt_rows_is_recorded_whole_and_named(self):
        self.seed_feature()
        c1 = self.commit("BE-1", {PLAN: TASKS_ONE_DONE})
        self.session_log.reply("claude usage --latest --json", usage_payload("claude", "s1", TEN_MINUTES))
        output = self.record_with_session(c1)
        self.assertEqual((None, 10), (output["feature"]["sources"]["sessions"][0]["prompts"],
                                      output["feature"]["usage"]["agentTimeMinutes"]))
        self.assertEqual(["session-log claude usage --latest has no prompt rows; the whole session is counted for X"],
                         output["missing"])

    def test_without_session_log_the_session_numbers_are_reported_missing(self):
        self.seed_feature()
        c1 = self.commit("BE-1", {PLAN: TASKS_ONE_DONE})
        output = self.record(c1)
        self.assertIsNone(output["feature"]["usage"])
        self.assertEqual(["session numbers not recorded: no --session-log"], output["missing"])

    def test_a_plan_doc_stats_cannot_count_keeps_the_known_counts(self):
        self.seed_feature(plan={"phases": 1, "tasks": 2, "estimatedHours": 5.0})
        c1 = self.commit("BE-1", {PLAN: "# X tasks\n\nNo task section any more.\n"})
        output = self.record(c1)
        self.assertEqual({"phases": 1, "tasks": 2, "estimatedHours": 5.0}, output["feature"]["plan"])
        self.assertEqual("in-progress", output["feature"]["status"])

    def test_a_failed_usage_call_is_named_and_adds_no_session(self):
        self.seed_feature()
        c1 = self.commit("BE-1", {PLAN: TASKS_ONE_DONE})
        self.session_log.reply("claude usage --latest --json", code=1, stderr="session-log: no session of this project\n")
        output = self.record_with_session(c1)
        self.assertEqual([], output["feature"]["sources"]["sessions"])
        self.assertEqual(["session-log claude usage --latest failed (exit 1): session-log: no session of this project"],
                         output["missing"])
        self.assertEqual(self.root, os.path.realpath(self.session_log.calls()[0]["cwd"]))

    def test_the_latest_session_is_read_in_the_session_folder(self):
        self.seed_feature()
        c1 = self.commit("BE-1", {PLAN: TASKS_ONE_DONE})
        self.latest_usage(TEN_MINUTES)
        session_folder = os.path.join(self.base, "session-home")
        os.makedirs(session_folder)
        output = self.record(c1, "--session-log", self.session_log.path, "--session-dir", session_folder)
        self.assertEqual("s1", output["feature"]["sources"]["sessions"][0]["id"])
        self.assertEqual([session_folder], [os.path.realpath(call["cwd"]) for call in self.session_log.calls()])

    def test_the_plan_path_is_relative_to_the_working_folder(self):
        self.seed_feature()
        c1 = self.commit("BE-1", {PLAN: TASKS_ONE_DONE})
        self.assertEqual([c1], self.record(c1, plan="x-tasks.md", cwd=self.path("docs"))["feature"]["sources"]["commits"])

    def test_a_plan_no_feature_lists_is_refused(self):
        self.seed(document(new_feature("Other")))
        c1 = self.commit("BE-1", {PLAN: TASKS_ONE_DONE})
        result = self.run_estimate("record-commit", PLAN, c1, "--harness", "claude")
        self.assert_error(result, EXIT_RUNTIME_ERROR, "no feature lists docs/x-tasks.md; run /estimate docs/x-tasks.md first")

    def test_an_unknown_commit_is_refused_and_changes_nothing(self):
        self.seed_feature()
        before = self.estimation()
        self.assert_error(self.run_estimate("record-commit", PLAN, "deadbeef", "--harness", "claude"), EXIT_RUNTIME_ERROR,
                          "git rev-parse failed")
        self.assertEqual(before, self.estimation())

    def test_an_unknown_harness_is_a_bad_argument(self):
        self.assert_error(self.run_estimate("record-commit", PLAN, "HEAD", "--harness", "vim"), EXIT_BAD_ARGUMENTS,
                          "unsupported harness 'vim'")


if __name__ == "__main__":
    unittest.main()
