"""`match-sessions <features-json | -> --session-log <path>` (C3) against a fake `session_log.py` run as a real subprocess and a real
repository: each prompt of a session goes to the features it names or whose commits (doc commits and the commits its input accepts,
never commits that only name its id) were made while it ran (a name or commit two features share is no evidence), a prompt without
evidence follows the prompt before it, sub-agents follow the prompt they started under, each feature gets its own slice of the
session's numbers with every counted prompt's number, time, first 60 characters and reason, nothing is matched by dates, and
every failed call and every commit made while no listed prompt ran is named in `missing`."""
import json
import os
import unittest

from tests.support import (EXIT_BAD_ARGUMENTS, EXIT_OK, ProjectTestCase, agent_row, list_entry, listed_with_rows, prompt_row,
                           tokens_of, usage_payload)

HARNESSES = ("claude", "codex", "cursor", "omp", "opencode")
EXPORT_BRIEF = "docs/export-brief.md"
IMPORT_BRIEF = "docs/import-brief.md"
EXPORT = {"name": "Export", "docs": [EXPORT_BRIEF], "taskIds": ["EX-1"]}
IMPORT = {"name": "Import", "docs": [IMPORT_BRIEF], "taskIds": ["IM-1"]}
NO_NUMBERS = {"agentTimeMinutes": None, "tokens": dict.fromkeys(tokens_of(0)), "costUsd": None}


def at(clock, day="2026-09-01"):
    return "%sT%s+00:00" % (day, clock)


class MatchSessionsTest(ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.session_log = self.fake_session_log()
        self.lists = {harness: [] for harness in HARNESSES}
        for harness in HARNESSES:
            self.session_log.reply(harness + " list --json", [])

    def session(self, harness, session_id, rows, agents=(), cost_complete=True, path=None, log=None):
        """Lists a session with prompt rows `rows` and answers its `usage --json` with them."""
        self.lists[harness].append(listed_with_rows(harness, session_id, rows, path, log))
        self.session_log.reply(harness + " list --json", self.lists[harness])
        self.session_log.reply("%s usage %s --json" % (harness, session_id),
                               usage_payload(harness, session_id, 0, cost_complete=cost_complete, path=path, log=log,
                                             prompts=list(rows), sub_agents=list(agents)))

    def edit(self, doc, when, message="docs"):
        """A commit at `when` that changes `doc` (each time differently)."""
        with_text = {doc: "%s %s\n" % (doc, when)}
        return self.commit(message, with_text, when=when)

    def match(self, *features, cwd=None):
        result = self.run_estimate("match-sessions", json.dumps({"features": list(features)}), "--session-log",
                                   self.session_log.path, cwd=cwd)
        self.assertEqual(EXIT_OK, result.code, result.err)
        return result.json

    @staticmethod
    def sessions(output):
        return {feature["name"]: feature["sessions"] for feature in output["features"]}

    @staticmethod
    def slices(output):
        """{feature: {session id: [(counted prompt number, why it counts)]}}."""
        return {feature["name"]: {session["id"]: [(prompt["number"], prompt["reason"]) for prompt in session["prompts"]]
                                  for session in feature["sessions"]}
                for feature in output["features"]}

    @staticmethod
    def labels(output):
        return {feature["name"]: feature["matchedBy"] for feature in output["features"]}

    def usage_calls(self):
        return [call["argv"] for call in self.session_log.calls() if call["argv"][1] == "usage"]

    def test_prompts_naming_a_doc_path_a_doc_basename_or_a_task_id_are_evidence(self):
        self.session("claude", "by-path", [prompt_row(at("10:00:00"), "Read docs/export-brief.md first")])
        self.session("claude", "by-basename", [prompt_row(at("11:00:00"), "/plan-maker export-brief.md")])
        self.session("claude", "by-task-id", [prompt_row(at("12:00:00"), "hello"), prompt_row(at("12:05:00"), "/implement (EX-1)")])
        self.session("claude", "wider-id", [prompt_row(at("13:00:00"), "EX-10 please")])
        output = self.match(EXPORT)
        self.assertEqual({"Export": {"by-path": [(1, "names docs/export-brief.md")], "by-basename": [(1, "names export-brief.md")],
                                     "by-task-id": [(1, "before prompt 2"), (2, "names EX-1")]}}, self.slices(output))
        self.assertEqual({"Export": "prompt"}, self.labels(output))
        self.assertEqual([], output["missing"])
        self.assertNotIn(["claude", "usage", "wider-id", "--json"], self.usage_calls())

    def test_a_session_is_split_between_the_features_its_prompts_worked_on(self):
        rows = [prompt_row(at("10:00:00"), "/implement docs/export-brief.md", seconds=600, total=100, cost=1.0),
                prompt_row(at("10:20:00"), "go", seconds=300, total=200, cost=2.0),
                prompt_row(at("11:00:00"), "/plan-maker import-brief.md", seconds=1200, total=400, cost=4.0),
                prompt_row(at("11:30:00"), "A", seconds=60, total=800, cost=8.0)]
        agents = [agent_row(at("10:21:00"), total=1000, cost=10.0, agent_id="a1"),
                  agent_row(at("10:22:00"), total=3000, cost=30.0, agent_id="a1.1", depth=1),
                  agent_row(at("11:31:00"), total=2000, cost=20.0, agent_id="a2", models=("review-model",))]
        self.session("omp", "s1", rows, agents, path="/abs/s1.jsonl", log="/abs/logs/s1.md")
        output = self.match(EXPORT, IMPORT)
        # Export: prompts 1-2 (600 + 300 s) and the two agents started under prompt 2; Import: prompts 3-4 (1200 + 60 s), agent a2.
        self.assertEqual({"Export": [{"harness": "omp", "models": ["agent-model", "main-model"], "id": "s1", "path": "/abs/s1.jsonl",
                                      "log": "/abs/logs/s1.md",
                                      "prompts": [{"number": 1, "at": at("10:00:00"), "text": "/implement docs/export-brief.md",
                                                   "reason": "names docs/export-brief.md"},
                                                  {"number": 2, "at": at("10:20:00"), "text": "go", "reason": "follows prompt 1"}],
                                      "agentTimeMinutes": 15, "tokens": tokens_of(4300), "costUsd": 43.0}],
                          "Import": [{"harness": "omp", "models": ["main-model", "review-model"], "id": "s1", "path": "/abs/s1.jsonl",
                                      "log": "/abs/logs/s1.md",
                                      "prompts": [{"number": 3, "at": at("11:00:00"), "text": "/plan-maker import-brief.md",
                                                   "reason": "names import-brief.md"},
                                                  {"number": 4, "at": at("11:30:00"), "text": "A", "reason": "follows prompt 3"}],
                                      "agentTimeMinutes": 21, "tokens": tokens_of(3200), "costUsd": 32.0}]},
                         self.sessions(output))
        self.assertEqual([["omp", "usage", "s1", "--json"]], self.usage_calls())

    def test_a_feature_commit_made_while_a_prompt_ran_is_evidence_and_earlier_prompts_join_the_first_evidence(self):
        self.edit(EXPORT_BRIEF, at("09:05:00"))
        self.edit(IMPORT_BRIEF, at("09:35:00"))
        self.session("codex", "s1", [prompt_row(at("09:00:00"), "hi"),
                                     prompt_row(at("09:02:00"), "go", seconds=600),
                                     prompt_row(at("09:30:00"), "continue", seconds=600),
                                     prompt_row(at("09:50:00"), "thanks")])
        output = self.match(EXPORT, IMPORT)
        self.assertEqual({"Export": {"s1": [(1, "before prompt 2"), (2, "made 1 of its commits")]},
                          "Import": {"s1": [(3, "made 1 of its commits"), (4, "follows prompt 3")]}}, self.slices(output))
        self.assertEqual([], output["missing"])

    def test_a_cleared_context_ends_the_run_of_a_feature_and_prompts_before_its_first_evidence_join_that_evidence(self):
        self.edit(EXPORT_BRIEF, at("09:05:00"))
        self.session("omp", "s1", [prompt_row(at("09:00:00"), "hi", seconds=600),
                                   prompt_row(at("10:00:00"), "unrelated", after_reset=True),
                                   prompt_row(at("10:05:00"), "more unrelated"),
                                   prompt_row(at("11:00:00"), "plan", after_reset=True),
                                   prompt_row(at("11:05:00"), "/implement docs/import-brief.md"),
                                   prompt_row(at("11:10:00"), "go on")])
        self.assertEqual({"Export": {"s1": [(1, "made 1 of its commits")]},
                          "Import": {"s1": [(4, "before prompt 5"), (5, "names docs/import-brief.md"), (6, "follows prompt 5")]}},
                         self.slices(self.match(EXPORT, IMPORT)))

    def test_a_segment_without_evidence_counts_for_no_feature_whatever_follows_it(self):
        self.edit(EXPORT_BRIEF, at("11:05:00"))
        self.session("omp", "s1", [prompt_row(at("10:00:00"), "hi", after_reset=True), prompt_row(at("10:05:00"), "more"),
                                   prompt_row(at("11:00:00"), "go", seconds=600, after_reset=True)])
        output = self.match(EXPORT)
        self.assertEqual({"Export": {"s1": [(3, "made 1 of its commits")]}}, self.slices(output))
        self.assertEqual(10, output["features"][0]["sessions"][0]["agentTimeMinutes"])

    def test_a_flag_on_the_first_prompt_changes_nothing_and_a_row_without_the_flag_never_ends_a_run(self):
        self.session("omp", "flagged", [prompt_row(at("10:00:00"), "docs/export-brief.md", after_reset=True), prompt_row(at("10:05:00"), "go")])
        self.session("omp", "old", [prompt_row(at("11:00:00"), "docs/export-brief.md"), prompt_row(at("11:05:00"), "go")])
        self.assertEqual({"Export": {"flagged": [(1, "names docs/export-brief.md"), (2, "follows prompt 1")],
                                     "old": [(1, "names docs/export-brief.md"), (2, "follows prompt 1")]}}, self.slices(self.match(EXPORT)))

    def test_only_a_true_flag_ends_a_run_and_only_a_list_of_texts_is_a_list_of_written_paths(self):
        self.session("omp", "s1", [prompt_row(at("10:00:00"), "docs/export-brief.md"),
                                   {**prompt_row(at("10:05:00"), "a"), "afterReset": "yes", "writtenPaths": "docs/import-brief.md"},
                                   {**prompt_row(at("10:10:00"), "b"), "afterReset": 1, "writtenPaths": [3, None, "docs/import-brief.md"]}])
        self.assertEqual({"Export": {"s1": [(1, "names docs/export-brief.md"), (2, "follows prompt 1")]},
                          "Import": {"s1": [(3, "writes docs/import-brief.md")]}}, self.slices(self.match(EXPORT, IMPORT)))

    def test_a_doc_a_prompts_tool_calls_wrote_is_evidence_even_when_its_text_names_nothing(self):
        self.edit(EXPORT_BRIEF, at("12:30:00"))
        self.session("omp", "s1", [prompt_row(at("10:00:00"), "let us talk", after_reset=True),
                                   prompt_row(at("10:10:00"), "C", written=["/abs/project/docs/import-brief.md", "src/other.py"]),
                                   prompt_row(at("10:20:00"), "thanks"),
                                   prompt_row(at("12:00:00"), "done?", seconds=3600)])
        self.assertEqual({"Export": {"s1": [(4, "made 1 of its commits")]},
                          "Import": {"s1": [(1, "before prompt 2"), (2, "writes docs/import-brief.md"), (3, "follows prompt 2")]}},
                         self.slices(self.match(EXPORT, IMPORT)))

    def test_a_prompt_that_names_a_doc_and_writes_another_counts_for_both_and_a_written_task_id_is_nothing(self):
        self.edit(EXPORT_BRIEF, at("12:30:00"))
        self.session("omp", "s1", [prompt_row(at("10:00:00"), "/implement docs/export-brief.md", written=["docs/import-brief.md", "src/EX-1.py"]),
                                   prompt_row(at("12:00:00"), "go", written=["docs/export-brief.md"], seconds=3600),
                                   prompt_row(at("13:10:00"), "ok", written=["src/EX-1.py"])])
        self.assertEqual({"Export": {"s1": [(1, "names docs/export-brief.md"), (2, "writes docs/export-brief.md"), (3, "follows prompt 2")]},
                          "Import": {"s1": [(1, "writes docs/import-brief.md")]}},
                         self.slices(self.match(EXPORT, IMPORT)))

    def test_a_written_path_counts_for_a_doc_only_as_the_whole_path_or_a_whole_trailing_part_of_it(self):
        self.edit(EXPORT_BRIEF, at("12:30:00"))
        self.session("omp", "s1", [prompt_row(at("10:00:00"), "docs/export-brief.md", written=["docs/import-brief.md.bak"]),
                                   prompt_row(at("10:10:00"), "next", written=["docs/old-import-brief.md", "other/my-import-brief.md"]),
                                   prompt_row(at("10:20:00"), "last", written=["/abs/project/docs/import-brief.md"]),
                                   prompt_row(at("12:00:00"), "end", seconds=3600)])
        self.assertEqual({"Export": {"s1": [(1, "names docs/export-brief.md"), (2, "follows prompt 1"), (4, "made 1 of its commits")]},
                          "Import": {"s1": [(3, "writes docs/import-brief.md")]}}, self.slices(self.match(EXPORT, IMPORT)))

    def test_a_doc_two_features_list_is_no_evidence_when_written_either(self):
        export = {"name": "Export", "docs": [EXPORT_BRIEF, "docs/shared-plan.md"], "taskIds": []}
        imported = {"name": "Import", "docs": [IMPORT_BRIEF, "docs/shared-plan.md"], "taskIds": []}
        self.edit("docs/shared-plan.md", at("09:05:00"))
        self.session("omp", "s1", [prompt_row(at("09:00:00"), "go", seconds=600, written=["docs/shared-plan.md"])])
        self.assertEqual({"Export": {}, "Import": {}}, self.slices(self.match(export, imported)))

    def test_an_accepted_commit_made_while_a_prompt_ran_is_evidence_and_a_candidate_is_not(self):
        accepted = self.commit("feat(EX): write the file", {"src/export.py": "e\n"}, when=at("09:05:00"))
        self.commit("feat(IM): parse the file", {"src/import.py": "i\n"}, when=at("10:05:00"))
        self.session("omp", "s1", [prompt_row(at("09:00:00"), "go", seconds=600),
                                   prompt_row(at("10:00:00"), "go on", seconds=600)])
        output = self.match({**EXPORT, "id": "EX", "commits": [accepted]}, {**IMPORT, "id": "IM"})
        self.assertEqual({"Export": {"s1": [(1, "made 1 of its commits"), (2, "follows prompt 1")]}, "Import": {}},
                         self.slices(output))

    def test_a_given_hash_that_is_no_commit_reachable_from_head_is_a_bad_argument(self):
        unknown = "f" * 40
        result = self.run_estimate("match-sessions", json.dumps({"features": [{**EXPORT, "commits": [unknown]}]}),
                                   "--session-log", self.session_log.path)
        self.assert_error(result, EXIT_BAD_ARGUMENTS, "'%s' of 'Export'" % unknown)

    def test_a_named_feature_beats_commits_and_otherwise_the_most_commits_win_a_tie_counting_for_each(self):
        self.edit(EXPORT_BRIEF, at("09:03:00"))
        self.edit(EXPORT_BRIEF, at("09:04:00"))
        self.edit(IMPORT_BRIEF, at("09:06:00"))
        self.edit(EXPORT_BRIEF, at("09:33:00"))
        self.edit(EXPORT_BRIEF, at("10:03:00"))
        self.edit(IMPORT_BRIEF, at("10:04:00"))
        self.session("omp", "s1", [prompt_row(at("09:00:00"), "go", seconds=600),
                                   prompt_row(at("09:30:00"), "/implement docs/import-brief.md", seconds=600),
                                   prompt_row(at("10:00:00"), "go on", seconds=600)])
        self.assertEqual({"Export": {"s1": [(1, "made 2 of its commits"), (3, "made 1 of its commits")]},
                          "Import": {"s1": [(2, "names docs/import-brief.md"), (3, "made 1 of its commits")]}},
                         self.slices(self.match(EXPORT, IMPORT)))

    def test_a_counted_prompt_shows_its_first_60_characters_without_tags_or_line_breaks(self):
        text = ("<command-name>/implement</command-name>\n<command-args>docs/export-brief.md\tthen run every remaining"
                " task</command-args>")
        self.session("claude", "s1", [prompt_row(at("10:00:00"), text)])
        (prompt,) = self.sessions(self.match(EXPORT))["Export"][0]["prompts"]
        self.assertEqual({"number": 1, "at": at("10:00:00"), "text": "/implement docs/export-brief.md then run every remaining tas",
                          "reason": "names docs/export-brief.md"}, prompt)

    def test_names_and_commits_two_features_share_are_no_evidence(self):
        export = {"name": "Export", "docs": [EXPORT_BRIEF, "docs/shared-plan.md"], "taskIds": ["EX-1", "BE-1"]}
        imported = {"name": "Import", "docs": [IMPORT_BRIEF, "docs/shared-plan.md"], "taskIds": ["IM-1", "BE-1"]}
        self.edit("docs/shared-plan.md", at("09:05:00"))
        self.session("claude", "shared", [prompt_row(at("09:00:00"), "read docs/shared-plan.md, then BE-1", seconds=600)])
        self.session("claude", "own", [prompt_row(at("10:00:00"), "EX-1")])
        output = self.match(export, imported)
        self.assertEqual({"Export": {"own": [(1, "names EX-1")]}, "Import": {}}, self.slices(output))
        self.assertEqual({"Export": "prompt", "Import": "none"}, self.labels(output))

    def test_a_session_inside_a_features_commit_dates_without_evidence_is_not_matched(self):
        self.edit(EXPORT_BRIEF, at("09:00:00", day="2026-09-01"))
        self.edit(EXPORT_BRIEF, at("09:00:00", day="2026-09-03"))
        self.session("cursor", "between", [prompt_row(at("09:00:00", day="2026-09-02"), "go", seconds=3600)])
        output = self.match(EXPORT)
        self.assertEqual({"Export": "none"}, self.labels(output))
        self.assertEqual([], self.usage_calls())

    def test_slice_minutes_round_half_up_and_an_incomplete_cost_is_null(self):
        self.session("claude", "s1", [prompt_row(at("09:00:00"), "EX-1", seconds=89, cost=1.0),
                                      prompt_row(at("09:10:00"), "IM-1", seconds=90, cost=2.0, complete=False)])
        self.session("cursor", "c1", [prompt_row(at("10:00:00"), "EX-1", seconds=150, cost=3.0)], cost_complete=False)
        sessions = self.sessions(self.match(EXPORT, IMPORT))
        self.assertEqual([("s1", 1, 1.0), ("c1", 3, None)],
                         [(entry["id"], entry["agentTimeMinutes"], entry["costUsd"]) for entry in sessions["Export"]])
        self.assertEqual([("s1", 2, None)],
                         [(entry["id"], entry["agentTimeMinutes"], entry["costUsd"]) for entry in sessions["Import"]])

    def test_a_feature_matched_by_prompt_gets_the_summed_usage_of_its_slices(self):
        self.session("claude", "s1", [prompt_row(at("09:00:00"), "EX-1", seconds=600, total=100, cost=1.0)])
        self.session("codex", "x1", [prompt_row(at("10:00:00"), "EX-1", seconds=300, total=200, cost=2.25)])
        export = self.match(EXPORT)["features"][0]
        self.assertEqual(["name", "matchedBy", "sessions", "usage"], list(export))
        self.assertEqual({"matchedBy": "prompt", "sessions": 2, "agentTimeMinutes": 15, "tokens": tokens_of(300), "costUsd": 3.25},
                         export["usage"])

    def test_a_feature_without_sessions_gets_the_usage_of_no_match(self):
        export = self.match(EXPORT)["features"][0]
        self.assertEqual({"matchedBy": "none", "sessions": 0, **NO_NUMBERS}, export["usage"])

    def test_a_session_with_an_unknown_cost_makes_the_cost_total_null(self):
        self.session("claude", "s1", [prompt_row(at("09:00:00"), "EX-1", cost=1.0)])
        self.session("cursor", "c1", [prompt_row(at("10:00:00"), "EX-1", cost=3.0)], cost_complete=False)
        self.assertIsNone(self.match(EXPORT)["features"][0]["usage"]["costUsd"])

    def test_save_stores_the_usage_match_sessions_printed(self):
        self.session("claude", "s1", [prompt_row(at("09:00:00"), "EX-1", seconds=600, total=100, cost=1.0)])
        export = self.match(EXPORT)["features"][0]
        entry = {"name": "Export", "description": "x", "effort": "x", "usage": {"matchedBy": export["matchedBy"]},
                 "sources": {"sessions": export["sessions"]}}
        result = self.run_estimate("save", json.dumps(entry))
        self.assertEqual(EXIT_OK, result.code, result.err)
        self.assertEqual(export["usage"], self.estimation()["features"][0]["usage"])

    def test_a_session_without_prompt_rows_is_counted_whole_and_named(self):
        self.session_log.reply("claude list --json", [list_entry("claude", "old", at("09:00:00"), at("10:00:00"),
                                                                 ["/implement docs/export-brief.md"], log="/abs/logs/old.md")])
        self.session_log.reply("claude usage old --json", usage_payload("claude", "old", 600, total=1000, cost=2.5,
                                                                        log="/abs/logs/old.md"))
        output = self.match(EXPORT, IMPORT)
        self.assertEqual({"Export": [{"harness": "claude", "models": ["main-model"], "id": "old", "path": None,
                                      "log": "/abs/logs/old.md", "prompts": None, "agentTimeMinutes": 10, "tokens": tokens_of(1000),
                                      "costUsd": 2.5}],
                          "Import": []}, self.sessions(output))
        self.assertEqual(["session-log claude usage old has no prompt rows; the whole session is counted for Export"],
                         output["missing"])

    def test_feature_commits_made_while_no_listed_prompt_ran_are_named(self):
        self.edit(EXPORT_BRIEF, at("08:00:00"))
        self.edit(EXPORT_BRIEF, at("09:05:00"))
        self.edit(IMPORT_BRIEF, at("12:00:00"))
        self.session("omp", "s1", [prompt_row(at("09:00:00"), "go", seconds=600)])
        output = self.match(EXPORT, IMPORT)
        self.assertEqual({"Export": {"s1": [(1, "made 1 of its commits")]}, "Import": {}}, self.slices(output))
        self.assertEqual(["Export: no session found for 1 of 2 commits", "Import: no session found for 1 of 1 commits"],
                         output["missing"])

    def test_failed_calls_are_named_and_a_failed_usage_counts_the_named_session_without_numbers(self):
        self.session_log.reply("codex list --json", code=1, stderr="session-log: codex store unreadable\n")
        self.session_log.reply("omp list --json", [list_entry("omp", "o1", at("09:00:00"), at("10:00:00"), ["EX-1"],
                                                              path="/abs/o1.jsonl")])
        self.session_log.reply("omp usage o1 --json", code=1, stderr="session-log: no such session\n")
        output = self.match(EXPORT)
        self.assertEqual(["session-log codex list failed (exit 1): session-log: codex store unreadable",
                          "session-log omp usage o1 failed (exit 1): session-log: no such session",
                          "session-log omp usage o1 has no prompt rows; the whole session is counted for Export"],
                         output["missing"])
        self.assertEqual({"Export": [{"harness": "omp", "models": None, "id": "o1", "path": "/abs/o1.jsonl", "log": None,
                                      "prompts": None, **NO_NUMBERS}]}, self.sessions(output))

    def test_session_log_runs_in_the_project_root_for_all_five_harnesses(self):
        os.makedirs(self.path("src"))
        self.match(EXPORT, cwd=self.path("src"))
        calls = self.session_log.calls()
        self.assertEqual([[harness, "list", "--json"] for harness in HARNESSES], [call["argv"] for call in calls])
        self.assertEqual({self.root}, {os.path.realpath(call["cwd"]) for call in calls})

    def test_the_features_can_come_from_stdin(self):
        result = self.run_estimate("match-sessions", "-", "--session-log", self.session_log.path,
                                   stdin=json.dumps({"features": [EXPORT]}))
        self.assertEqual(EXIT_OK, result.code, result.err)
        self.assertEqual(["Export"], [feature["name"] for feature in result.json["features"]])

    def test_features_without_names_are_a_bad_argument(self):
        result = self.run_estimate("match-sessions", json.dumps({"features": [{"docs": []}]}), "--session-log",
                                   self.session_log.path)
        self.assert_error(result, EXIT_BAD_ARGUMENTS, "each feature needs a name")

    def test_the_session_log_path_is_required(self):
        self.assert_error(self.run_estimate("match-sessions", "-"), EXIT_BAD_ARGUMENTS, "--session-log")


if __name__ == "__main__":
    unittest.main()
