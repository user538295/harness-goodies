"""`save <entry-json | ->` (C3): which feature an entry updates, the latest-value rules and their exceptions, and the derived usage."""
import json
import os
import unittest

from tests.support import (ESTIMATION_FILE, EXIT_BAD_ARGUMENTS, EXIT_OK, EXIT_RUNTIME_ERROR, TODAY, ProjectTestCase, document,
                           new_feature)

HASH_A = "a" * 40
HASH_B = "b" * 40
HASH_C = "c" * 40


def session(harness, session_id, minutes, tokens=None, cost=1.25, path=None, log=None, prompts=None):
    """A C2 session entry; `tokens` overrides some token counts of the default 10/20/None/30/40 = 100 split."""
    counts = {"input": 10, "output": 20, "reasoning": None, "cacheRead": 30, "cacheWrite": 40, "total": 100}
    counts.update(tokens or {})
    return {"harness": harness, "id": session_id, "path": path, "log": log, "prompts": prompts, "agentTimeMinutes": minutes,
            "tokens": counts, "costUsd": cost}


class SaveTestCase(ProjectTestCase):
    def save(self, entry, **keywords):
        return self.run_estimate("save", json.dumps(entry), **keywords)

    def assert_saved(self, result):
        self.assertEqual(EXIT_OK, result.code, result.err)
        return result.json


class NewFeatureTest(SaveTestCase):
    def test_a_new_feature_gets_the_defaults_and_today_as_its_estimate_date(self):
        saved = self.assert_saved(self.save({"name": "Export", "description": "PDF export", "storyPoints": 8,
                                             "sources": {"docs": ["docs/export-brief.md"]}}))
        expected = new_feature("Export", description="PDF export", storyPoints=8, estimatedAt=TODAY.isoformat(),
                               sources={"docs": ["docs/export-brief.md"], "commits": [], "sessions": []})
        self.assertEqual(expected, saved)
        self.assertEqual(document(expected), self.estimation())

    def test_the_stored_file_keeps_the_brief_key_order(self):
        self.save({"name": "Export", "description": "PDF export"})
        with open(self.path(ESTIMATION_FILE), encoding="utf-8") as handle:
            feature = json.load(handle)["features"][0]
        self.assertEqual(["name", "description", "status", "storyPoints", "updatedStoryPoints", "estimatedAt", "plan", "git",
                          "usage", "sources"], list(feature))

    def test_a_new_feature_without_a_description_is_refused(self):
        self.assert_error(self.save({"name": "Export"}), EXIT_RUNTIME_ERROR, "a new feature needs name and description")
        self.assertFalse(os.path.exists(self.path(ESTIMATION_FILE)))

    def test_the_entry_can_come_from_stdin(self):
        result = self.run_estimate("save", "-", stdin=json.dumps({"name": "Export", "description": "PDF export"}))
        self.assertEqual("Export", self.assert_saved(result)["name"])

    def test_an_entry_that_is_not_json_is_a_bad_argument(self):
        self.assert_error(self.run_estimate("save", "{name"), EXIT_BAD_ARGUMENTS, "entry is not JSON")

    def test_an_unknown_field_is_a_bad_argument(self):
        self.assert_error(self.save({"name": "Export", "description": "x", "points": 3}), EXIT_BAD_ARGUMENTS,
                          "unknown field 'points'")


class TargetTest(SaveTestCase):
    def test_a_shared_doc_selects_the_feature_even_under_another_name(self):
        self.seed(document(new_feature("Export", sources={"docs": ["export-brief.md"], "commits": [], "sessions": []}),
                           new_feature("Other")))
        saved = self.assert_saved(self.save({"name": "Export plan", "storyPoints": 13,
                                             "sources": {"docs": ["export-plan.md", "export-brief.md"]}}))
        self.assertEqual("Export plan", saved["name"])
        self.assertEqual(["export-brief.md", "export-plan.md"], saved["sources"]["docs"])
        self.assertEqual(["Export plan", "Other"], [feature["name"] for feature in self.estimation()["features"]])

    def test_without_a_shared_doc_the_same_name_selects_the_feature(self):
        self.seed(document(new_feature("Export", storyPoints=5)))
        saved = self.assert_saved(self.save({"name": "Export", "storyPoints": 8}))
        self.assertEqual(8, saved["storyPoints"])
        self.assertEqual(1, len(self.estimation()["features"]))

    def test_no_shared_doc_and_a_new_name_adds_a_feature(self):
        self.seed(document(new_feature("Export", sources={"docs": ["export-brief.md"], "commits": [], "sessions": []})))
        self.assert_saved(self.save({"name": "Import", "description": "CSV import", "sources": {"docs": ["import-brief.md"]}}))
        self.assertEqual(["Export", "Import"], [feature["name"] for feature in self.estimation()["features"]])

    def test_docs_shared_with_two_features_are_ambiguous_and_change_nothing(self):
        stored = document(new_feature("A", sources={"docs": ["a.md"], "commits": [], "sessions": []}),
                          new_feature("B", sources={"docs": ["b.md"], "commits": [], "sessions": []}))
        self.seed(stored)
        self.assert_error(self.save({"name": "A", "sources": {"docs": ["a.md", "b.md"]}}), EXIT_RUNTIME_ERROR,
                          "matches 2 features")
        self.assertEqual(stored, self.estimation())

    def test_renaming_onto_another_features_name_is_refused(self):
        self.seed(document(new_feature("A", sources={"docs": ["a.md"], "commits": [], "sessions": []}), new_feature("B")))
        self.assert_error(self.save({"name": "B", "sources": {"docs": ["a.md"]}}), EXIT_RUNTIME_ERROR,
                          "another feature is named 'B'")


class LatestValueRulesTest(SaveTestCase):
    def test_given_fields_replace_and_plan_keys_merge(self):
        self.seed(document(new_feature("A", description="old", plan={"phases": 2, "tasks": 9, "estimatedHours": None})))
        saved = self.assert_saved(self.save({"name": "A", "description": "new", "status": "in-progress",
                                             "plan": {"estimatedHours": 12.5}}))
        self.assertEqual(("new", "in-progress"), (saved["description"], saved["status"]))
        self.assertEqual({"phases": 2, "tasks": 9, "estimatedHours": 12.5}, saved["plan"])

    def test_an_empty_plan_count_never_overwrites_a_known_one(self):
        # A brief estimated after its tasks file has no counts of its own: it must not erase the tasks file's.
        self.seed(document(new_feature("A", plan={"phases": 4, "tasks": 17, "estimatedHours": 38.5})))
        saved = self.assert_saved(self.save({"name": "A", "plan": {"phases": None, "tasks": 20, "estimatedHours": None}}))
        self.assertEqual({"phases": 4, "tasks": 20, "estimatedHours": 38.5}, saved["plan"])

    def test_re_estimating_before_work_replaces_the_size_and_the_date(self):
        self.seed(document(new_feature("A", storyPoints=5, estimatedAt="2026-10-01")))
        saved = self.assert_saved(self.save({"name": "A", "storyPoints": 8}))
        self.assertEqual((8, TODAY.isoformat()), (saved["storyPoints"], saved["estimatedAt"]))

    def test_an_explicit_estimate_date_wins_over_today(self):
        saved = self.assert_saved(self.save({"name": "A", "description": "x", "storyPoints": 3, "estimatedAt": "2026-10-30"}))
        self.assertEqual("2026-10-30", saved["estimatedAt"])

    def test_size_and_date_are_frozen_once_work_started_and_each_ignored_value_is_named(self):
        for status in ("in-progress", "delivered"):
            with self.subTest(status=status):
                self.seed(document(new_feature("A", status=status, storyPoints=8, estimatedAt="2026-10-01")))
                result = self.save({"name": "A", "storyPoints": 13, "estimatedAt": "2026-11-04", "description": "new"})
                saved = self.assert_saved(result)
                self.assertEqual((8, "2026-10-01", "new"), (saved["storyPoints"], saved["estimatedAt"], saved["description"]))
                self.assertEqual(2, result.err.count("estimate: "), result.err)
                self.assertIn("storyPoints 13 ignored", result.err)
                self.assertIn("estimatedAt 2026-11-04 ignored", result.err)

    def test_a_set_updated_size_is_never_changed(self):
        self.seed(document(new_feature("A", status="delivered", updatedStoryPoints=8)))
        result = self.save({"name": "A", "updatedStoryPoints": 13})
        self.assertEqual(8, self.assert_saved(result)["updatedStoryPoints"])
        self.assertIn("updatedStoryPoints 13 ignored", result.err)

    def test_an_empty_updated_size_is_filled(self):
        self.seed(document(new_feature("A", status="delivered")))
        self.assertEqual(13, self.assert_saved(self.save({"name": "A", "updatedStoryPoints": 13}))["updatedStoryPoints"])

    def test_a_size_outside_the_scale_is_refused(self):
        self.seed(document(new_feature("A")))
        for field, value in (("storyPoints", 4), ("updatedStoryPoints", "M"), ("storyPoints", True)):
            with self.subTest(field=field, value=value):
                self.assert_error(self.save({"name": "A", field: value}), EXIT_RUNTIME_ERROR, "not in the scale")

    def test_a_string_scale_takes_its_own_values(self):
        self.seed(document(new_feature("A"), scale=["S", "M", "L"]))
        self.assertEqual("M", self.assert_saved(self.save({"name": "A", "storyPoints": "M"}))["storyPoints"])

    def test_an_unknown_status_is_refused(self):
        self.seed(document(new_feature("A")))
        self.assert_error(self.save({"name": "A", "status": "done"}), EXIT_RUNTIME_ERROR, "unknown status 'done'")


class PathTest(SaveTestCase):
    def test_docs_are_stored_root_relative_from_any_folder_and_outside_paths_absolute(self):
        os.makedirs(self.path("src"))
        outside = os.path.join(self.base, "notes.md")
        saved = self.assert_saved(self.save({"name": "A", "description": "x",
                                             "sources": {"docs": ["../docs/a-brief.md", self.path("docs/a-plan.md"),
                                                                  "./b.md", outside]}},
                                            cwd=self.path("src")))
        self.assertEqual(["docs/a-brief.md", "docs/a-plan.md", "src/b.md", outside], saved["sources"]["docs"])

    def test_a_doc_given_relative_to_a_subfolder_finds_the_feature_that_lists_it(self):
        self.seed(document(new_feature("A", sources={"docs": ["docs/a-brief.md"], "commits": [], "sessions": []})))
        os.makedirs(self.path("docs"))
        saved = self.assert_saved(self.save({"name": "A2", "sources": {"docs": ["a-brief.md"]}}, cwd=self.path("docs")))
        self.assertEqual("A2", saved["name"])


class GitTest(SaveTestCase):
    def test_git_and_commits_replace_together(self):
        self.seed(document(new_feature("A", git={"commits": 2, "linesAdded": 10, "linesRemoved": 1},
                                       sources={"docs": [], "commits": [HASH_A, HASH_B], "sessions": []})))
        saved = self.assert_saved(self.save({"name": "A", "git": {"commits": 1, "linesAdded": 7, "linesRemoved": 3},
                                             "sources": {"commits": [HASH_C]}}))
        self.assertEqual({"commits": 1, "linesAdded": 7, "linesRemoved": 3}, saved["git"])
        self.assertEqual([HASH_C], saved["sources"]["commits"])

    def test_git_without_its_commits_is_refused(self):
        self.seed(document(new_feature("A")))
        self.assert_error(self.save({"name": "A", "git": {"commits": 1, "linesAdded": 7, "linesRemoved": 3}}),
                          EXIT_RUNTIME_ERROR, "git and sources.commits are saved together")


class SessionTest(SaveTestCase):
    def test_given_sessions_replace_the_stored_ones_and_the_usage_is_their_sum(self):
        stored = [session("claude", "s1", 10, cost=1.10), session("omp", "no-longer-matched", 99)]
        self.seed(document(new_feature("A", sources={"docs": [], "commits": [], "sessions": stored},
                                       usage={"matchedBy": "prompt", "sessions": 2, "agentTimeMinutes": 109,
                                              "tokens": session("claude", "s1", 10)["tokens"], "costUsd": 2.35})))
        newer = session("claude", "s1", 300, tokens={"input": 1000, "total": 1090}, cost=20.20, path="/abs/s1.jsonl", prompts=[2, 3])
        other = session("codex", "s1", 45, cost=0.15)
        saved = self.assert_saved(self.save({"name": "A", "sources": {"sessions": [newer, other]}}))
        self.assertEqual([newer, other], saved["sources"]["sessions"])
        # 300 + 45 minutes; input 1000 + 10; output 20 + 20; reasoning None in both; total 1090 + 100; cost 20.20 + 0.15.
        self.assertEqual({"matchedBy": "prompt", "sessions": 2, "agentTimeMinutes": 345,
                          "tokens": {"input": 1010, "output": 40, "reasoning": None, "cacheRead": 60, "cacheWrite": 80,
                                     "total": 1190},
                          "costUsd": 20.35}, saved["usage"])

    def test_a_total_is_null_when_any_session_lacks_it(self):
        sessions = [session("omp", "s1", 10, tokens={"reasoning": 5}), session("omp", "s2", None, tokens={"reasoning": None},
                                                                              cost=None)]
        saved = self.assert_saved(self.save({"name": "A", "description": "x", "usage": {"matchedBy": "prompt"},
                                             "sources": {"sessions": sessions}}))
        self.assertIsNone(saved["usage"]["agentTimeMinutes"])
        self.assertIsNone(saved["usage"]["tokens"]["reasoning"])
        self.assertIsNone(saved["usage"]["costUsd"])
        self.assertEqual(200, saved["usage"]["tokens"]["total"])

    def test_the_matched_by_label_comes_from_the_entry_and_given_totals_are_ignored(self):
        self.seed(document(new_feature("A", sources={"docs": [], "commits": [], "sessions": [session("claude", "s1", 10)]},
                                       usage={"matchedBy": "prompt", "sessions": 1, "agentTimeMinutes": 10,
                                              "tokens": session("claude", "s1", 10)["tokens"], "costUsd": 1.25})))
        result = self.save({"name": "A", "usage": {"matchedBy": "implement", "agentTimeMinutes": 999}})
        saved = self.assert_saved(result)
        self.assertEqual(("implement", 10), (saved["usage"]["matchedBy"], saved["usage"]["agentTimeMinutes"]))
        self.assertIn("usage.agentTimeMinutes is derived from sources.sessions; ignored", result.err)

    def test_matching_that_found_no_session_is_recorded_as_none_with_empty_totals(self):
        saved = self.assert_saved(self.save({"name": "A", "description": "x", "usage": {"matchedBy": "none"}}))
        self.assertEqual({"matchedBy": "none", "sessions": 0, "agentTimeMinutes": None,
                          "tokens": {"input": None, "output": None, "reasoning": None, "cacheRead": None, "cacheWrite": None,
                                     "total": None},
                          "costUsd": None}, saved["usage"])

    def test_the_first_sessions_need_a_matched_by_label(self):
        self.assert_error(self.save({"name": "A", "description": "x", "sources": {"sessions": [session("claude", "s1", 1)]}}),
                          EXIT_RUNTIME_ERROR, "usage.matchedBy")

    def test_usage_stays_null_before_any_matching(self):
        self.assertIsNone(self.assert_saved(self.save({"name": "A", "description": "x"}))["usage"])


if __name__ == "__main__":
    unittest.main()
