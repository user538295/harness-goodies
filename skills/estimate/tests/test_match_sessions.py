"""`match-sessions <features-json | -> --session-log <path>` (C3) against a fake `session_log.py` run as a real subprocess: prompt
matches beat date matches, the weakest label wins per feature, usage numbers become C2 session entries (minutes rounded half up)
and every failed session-log call is named in `missing`."""
import json
import os
import unittest

from tests.support import EXIT_BAD_ARGUMENTS, EXIT_OK, ProjectTestCase, list_entry, usage_payload

HARNESSES = ("claude", "codex", "cursor", "omp", "opencode")
EXPORT = {"name": "Export", "docs": ["docs/export-brief.md"], "taskIds": ["BE-1"], "firstDate": "2026-09-01",
          "lastDate": "2026-09-10"}
IMPORT = {"name": "Import", "docs": ["docs/import-brief.md"], "taskIds": ["IM-1"], "firstDate": "2026-09-05",
          "lastDate": "2026-09-20"}


class MatchSessionsTest(ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.session_log = self.fake_session_log()
        for harness in HARNESSES:
            self.session_log.reply(harness + " list --json", [])

    def listed(self, harness, *sessions):
        self.session_log.reply(harness + " list --json", list(sessions))
        for session in sessions:
            self.session_log.reply("%s usage %s --json" % (harness, session["id"]),
                                   usage_payload(harness, session["id"], 600, path=session["path"], log=session["log"]))

    def match(self, *features, cwd=None):
        result = self.run_estimate("match-sessions", json.dumps({"features": list(features)}), "--session-log",
                                   self.session_log.path, cwd=cwd)
        self.assertEqual(EXIT_OK, result.code, result.err)
        return result.json

    @staticmethod
    def ids(output):
        return {feature["name"]: [session["id"] for session in feature["sessions"]] for feature in output["features"]}

    @staticmethod
    def labels(output):
        return {feature["name"]: feature["matchedBy"] for feature in output["features"]}

    def test_prompts_naming_a_doc_path_a_doc_basename_or_a_task_id_match(self):
        self.listed("claude",
                    list_entry("claude", "by-path", "2026-08-01T12:00:00+00:00", "2026-08-01T13:00:00+00:00",
                               ["Read docs/export-brief.md first"]),
                    list_entry("claude", "by-basename", "2026-08-02T12:00:00+00:00", "2026-08-02T13:00:00+00:00",
                               ["/plan-maker export-brief.md"]),
                    list_entry("claude", "by-task-id", "2026-08-03T12:00:00+00:00", "2026-08-03T13:00:00+00:00",
                               ["hello", "/implement next (BE-1)"]),
                    list_entry("claude", "wider-id", "2026-08-04T12:00:00+00:00", "2026-08-04T13:00:00+00:00",
                               ["BE-10 please"]))
        output = self.match(EXPORT)
        self.assertEqual({"Export": ["by-path", "by-basename", "by-task-id"]}, self.ids(output))
        self.assertEqual({"Export": "prompt"}, self.labels(output))
        self.assertEqual([], output["missing"])

    def test_a_prompt_match_beats_dates_and_dates_need_start_and_last_activity_inside(self):
        self.listed("codex",
                    # Names Export and lies inside Import's dates: Export only, by prompt.
                    list_entry("codex", "prompted", "2026-09-12T09:00:00+00:00", "2026-09-12T10:00:00+00:00",
                               ["continue BE-1"]),
                    # No prompt match, inside both ranges (09-05 .. 09-10): both features, by dates.
                    list_entry("codex", "both-ranges", "2026-09-06T09:00:00+00:00", "2026-09-07T10:00:00+00:00", ["go"]),
                    # Starts inside Export's range, last activity after it: Import only.
                    list_entry("codex", "runs-over", "2026-09-09T09:00:00+00:00", "2026-09-11T10:00:00+00:00", ["go"]),
                    # Outside every range.
                    list_entry("codex", "too-late", "2026-09-21T09:00:00+00:00", "2026-09-21T10:00:00+00:00", ["go"]))
        output = self.match(EXPORT, IMPORT)
        self.assertEqual({"Export": ["prompted", "both-ranges"], "Import": ["both-ranges", "runs-over"]}, self.ids(output))
        # Export has a prompt and a dates session: the weakest label wins.
        self.assertEqual({"Export": "dates", "Import": "dates"}, self.labels(output))

    def test_local_dates_decide_the_range(self):
        # 23:30 at -02:00 is 01:30 UTC on the next day; the tests' local zone is UTC, so the session is on 09-11.
        self.listed("omp", list_entry("omp", "late-evening", "2026-09-10T23:30:00-02:00", "2026-09-10T23:50:00-02:00", ["go"]))
        self.assertEqual({"Export": [], "Import": ["late-evening"]}, self.ids(self.match(EXPORT, IMPORT)))

    def test_a_feature_without_sessions_is_matched_by_none(self):
        self.assertEqual({"Export": "none"}, self.labels(self.match(EXPORT)))

    def test_usage_becomes_a_c2_session_entry_with_minutes_rounded_half_up(self):
        session = list_entry("claude", "s1", "2026-08-01T12:00:00+00:00", "2026-08-01T13:00:00+00:00", ["BE-1"],
                             path="/abs/s1.jsonl", log="/abs/logs/s1.md")
        self.session_log.reply("claude list --json", [session])
        for seconds, minutes in ((11280, 188), (89, 1), (90, 2), (150, 3), (29, 0)):
            with self.subTest(seconds=seconds):
                self.session_log.reply("claude usage s1 --json", usage_payload("claude", "s1", seconds, total=1000, cost=2.5,
                                                                               path="/abs/s1.jsonl", log="/abs/logs/s1.md"))
                entry = self.match(EXPORT)["features"][0]["sessions"][0]
                self.assertEqual({"harness": "claude", "id": "s1", "path": "/abs/s1.jsonl", "log": "/abs/logs/s1.md",
                                  "agentTimeMinutes": minutes,
                                  "tokens": {"input": 250, "output": 250, "reasoning": None, "cacheRead": 250,
                                             "cacheWrite": 250, "total": 1000},
                                  "costUsd": 2.5}, entry)

    def test_an_incomplete_cost_is_null(self):
        self.session_log.reply("cursor list --json", [list_entry("cursor", "c1", "2026-08-01T12:00:00+00:00",
                                                                 "2026-08-01T13:00:00+00:00", ["BE-1"])])
        self.session_log.reply("cursor usage c1 --json", usage_payload("cursor", "c1", 60, cost=3.0, cost_complete=False))
        self.assertIsNone(self.match(EXPORT)["features"][0]["sessions"][0]["costUsd"])

    def test_a_session_matched_by_two_features_is_read_once(self):
        self.listed("claude", list_entry("claude", "s1", "2026-08-01T12:00:00+00:00", "2026-08-01T13:00:00+00:00",
                                         ["BE-1 and IM-1"]))
        self.assertEqual({"Export": ["s1"], "Import": ["s1"]}, self.ids(self.match(EXPORT, IMPORT)))
        usage_calls = [call for call in self.session_log.calls() if call["argv"][1] == "usage"]
        self.assertEqual([["claude", "usage", "s1", "--json"]], [call["argv"] for call in usage_calls])

    def test_failed_calls_are_named_and_a_failed_usage_keeps_the_session_with_null_numbers(self):
        self.session_log.reply("codex list --json", code=1, stderr="session-log: codex store unreadable\n")
        self.session_log.reply("omp list --json", [list_entry("omp", "o1", "2026-08-01T12:00:00+00:00",
                                                              "2026-08-01T13:00:00+00:00", ["BE-1"], path="/abs/o1.jsonl")])
        self.session_log.reply("omp usage o1 --json", code=1, stderr="session-log: no such session\n")
        output = self.match(EXPORT)
        self.assertEqual(["session-log codex list failed (exit 1): session-log: codex store unreadable",
                          "session-log omp usage o1 failed (exit 1): session-log: no such session"], output["missing"])
        self.assertEqual([{"harness": "omp", "id": "o1", "path": "/abs/o1.jsonl", "log": None, "agentTimeMinutes": None,
                           "tokens": {"input": None, "output": None, "reasoning": None, "cacheRead": None, "cacheWrite": None,
                                      "total": None},
                           "costUsd": None}], output["features"][0]["sessions"])

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
