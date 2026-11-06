"""`save <entry-json | ->` (C3): which feature an entry updates, the latest-value rules and their exceptions, the derived usage, and
the feature's file: its name from the first day, the id and the name, and the one file a save writes."""
import json
import os
import unittest

from tests.support import (DATA_FOLDER, DEFAULT_ANCHOR, DEFAULT_SCALE, EXIT_BAD_ARGUMENTS, EXIT_OK, EXIT_RUNTIME_ERROR,
                           FEATURES_PATH, SCALE_PATH, TODAY, ProjectTestCase, before_effort, date_file, document, git, new_feature)

MAX_FILE_NAME_LENGTH = len("2026-11-04-") + 60 + len(".json")  # the day, a name part cut to 60 characters, the suffix
LONG_NAME_LENGTH = 300

HASH_A = "a" * 40
HASH_B = "b" * 40
HASH_C = "c" * 40


def session(harness, session_id, minutes, tokens=None, cost=1.25, path=None, log=None, prompts=None, models=None):
    """A C2 session entry; `tokens` overrides some token counts of the default 10/20/None/30/40 = 100 split."""
    counts = {"input": 10, "output": 20, "reasoning": None, "cacheRead": 30, "cacheWrite": 40, "total": 100}
    counts.update(tokens or {})
    return {"harness": harness, "models": models, "id": session_id, "path": path, "log": log, "prompts": prompts,
            "agentTimeMinutes": minutes, "tokens": counts, "costUsd": cost}


class SaveTestCase(ProjectTestCase):
    def save(self, entry, **keywords):
        return self.run_estimate("save", json.dumps(entry), **keywords)

    def assert_saved(self, result):
        self.assertEqual(EXIT_OK, result.code, result.err)
        return result.json


class NewFeatureTest(SaveTestCase):
    def test_a_new_feature_gets_the_defaults_today_as_its_estimate_date_and_its_own_file(self):
        saved = self.assert_saved(self.save({"name": "Export", "description": "PDF export", "effort": "One renderer",
                                             "storyPoints": 8, "sources": {"docs": ["docs/export-brief.md"]}}))
        expected = new_feature("Export", file="2026-11-04-export.json", description="PDF export", effort="One renderer",
                               storyPoints=8, estimatedAt=TODAY.isoformat(),
                               sources={"docs": ["docs/export-brief.md"], "commits": [], "sessions": []})
        self.assertEqual(expected, saved)
        self.assertEqual(document(expected, anchor=DEFAULT_ANCHOR), self.estimation())

    def test_the_first_save_writes_the_default_scale_file_with_its_anchor(self):
        self.save({"name": "Export", "description": "PDF export", "effort": "x"})
        self.assertEqual({"version": 1, "scale": DEFAULT_SCALE, "anchor": DEFAULT_ANCHOR}, self.raw(SCALE_PATH))

    def test_a_stored_scale_file_is_never_rewritten(self):
        text = '{"version": 1,\n "scale": ["S", "M", "L"]}\n'
        self.write(SCALE_PATH, text)
        self.assert_saved(self.save({"name": "Export", "description": "x", "effort": "x", "storyPoints": "M"}))
        with open(self.path(SCALE_PATH), encoding="utf-8") as handle:
            self.assertEqual(text, handle.read())

    def test_the_stored_file_starts_with_its_version_and_keeps_the_brief_key_order(self):
        self.save({"name": "Export", "description": "PDF export", "effort": "x"})
        self.assertEqual(["version", "id", "name", "description", "effort", "status", "storyPoints", "updatedStoryPoints",
                          "estimatedAt", "estimatedBy", "updatedBy", "plan", "git", "usage", "sources"],
                         list(self.raw(FEATURES_PATH + "/2026-11-04-export.json")))

    def test_saving_one_feature_leaves_the_other_files_untouched(self):
        self.seed(document(new_feature("A"), new_feature("B")))
        other = self.path(FEATURES_PATH + "/" + new_feature("B")["file"])
        with open(other, encoding="utf-8") as handle:
            before = handle.read()
        self.assert_saved(self.save({"name": "A", "storyPoints": 3, "effort": "x"}))
        with open(other, encoding="utf-8") as handle:
            self.assertEqual(before, handle.read())

    def test_a_new_feature_without_a_description_is_refused(self):
        self.assert_error(self.save({"name": "Export"}), EXIT_RUNTIME_ERROR, "a new feature needs name and description")
        self.assertFalse(os.path.exists(self.path(DATA_FOLDER)))

    def test_the_entry_can_come_from_stdin(self):
        result = self.run_estimate("save", "-", stdin=json.dumps({"name": "Export", "description": "PDF export", "effort": "x"}))
        self.assertEqual("Export", self.assert_saved(result)["name"])

    def test_an_entry_that_is_not_json_is_a_bad_argument(self):
        self.assert_error(self.run_estimate("save", "{name"), EXIT_BAD_ARGUMENTS, "entry is not JSON")

    def test_an_unknown_field_is_a_bad_argument(self):
        self.assert_error(self.save({"name": "Export", "description": "x", "points": 3}), EXIT_BAD_ARGUMENTS,
                          "unknown field 'points'")

    def test_a_name_that_is_not_text_is_a_bad_argument(self):
        for value in (12, " "):
            with self.subTest(value=value):
                self.assert_error(self.save({"name": value, "description": "x"}), EXIT_BAD_ARGUMENTS, "name must be text")
                self.assertFalse(os.path.exists(self.path(DATA_FOLDER)))


class TargetTest(SaveTestCase):
    def test_a_shared_doc_selects_the_feature_even_under_another_name_and_renames_its_file(self):
        self.seed(document(new_feature("Export", sources={"docs": ["export-brief.md"], "commits": [], "sessions": []}),
                           new_feature("Other")))
        saved = self.assert_saved(self.save({"name": "Export plan", "storyPoints": 13, "effort": "x",
                                             "sources": {"docs": ["export-plan.md", "export-brief.md"]}}))
        self.assertEqual(("Export plan", "2026-10-01-export-plan.json"), (saved["name"], saved["file"]))
        self.assertEqual(["export-brief.md", "export-plan.md"], saved["sources"]["docs"])
        self.assertEqual(["2026-10-01-export-plan.json", "2026-10-01-other.json"], self.feature_files())
        self.assertEqual(["Export plan", "Other"], [feature["name"] for feature in self.estimation()["features"]])

    def test_without_a_shared_doc_the_same_name_selects_the_feature(self):
        self.seed(document(new_feature("Export", storyPoints=5)))
        saved = self.assert_saved(self.save({"name": "Export", "storyPoints": 8, "effort": "x"}))
        self.assertEqual(8, saved["storyPoints"])
        self.assertEqual(1, len(self.estimation()["features"]))

    def test_no_shared_doc_and_a_new_name_adds_a_feature(self):
        self.seed(document(new_feature("Export", sources={"docs": ["export-brief.md"], "commits": [], "sessions": []})))
        self.assert_saved(self.save({"name": "Import", "description": "CSV import", "effort": "x",
                                     "sources": {"docs": ["import-brief.md"]}}))
        self.assertEqual(["Export", "Import"], [feature["name"] for feature in self.estimation()["features"]])

    def test_docs_shared_with_two_features_are_ambiguous_and_change_nothing(self):
        stored = document(new_feature("A", sources={"docs": ["a.md"], "commits": [], "sessions": []}),
                          new_feature("B", sources={"docs": ["b.md"], "commits": [], "sessions": []}))
        self.seed(stored)
        self.assert_error(self.save({"name": "A", "sources": {"docs": ["a.md", "b.md"]}}), EXIT_RUNTIME_ERROR,
                          "matches 2 features")
        self.assertEqual(stored, self.estimation())

    def test_renaming_onto_another_features_name_is_refused_naming_its_file(self):
        self.seed(document(new_feature("A", sources={"docs": ["a.md"], "commits": [], "sessions": []}), new_feature("B")))
        self.assert_error(self.save({"name": "B", "sources": {"docs": ["a.md"]}}), EXIT_RUNTIME_ERROR,
                          "another feature is named 'B': 2026-10-01-b.json")

    def test_a_save_whose_target_is_clear_leaves_a_name_two_files_already_share_to_the_warning(self):
        self.seed(document(new_feature("X", file="2026-10-01-x.json", sources={"docs": ["a.md"], "commits": [], "sessions": []}),
                           new_feature("X", file="2026-10-02-x.json", sources={"docs": ["b.md"], "commits": [], "sessions": []})))
        result = self.save({"name": "X", "storyPoints": 3, "effort": "x", "sources": {"docs": ["a.md"]}})
        self.assertEqual("2026-10-01-x.json", self.assert_saved(result)["file"])
        self.assertEqual(3, self.raw(FEATURES_PATH + "/2026-10-01-x.json")["storyPoints"])
        self.assertIsNone(self.raw(FEATURES_PATH + "/2026-10-02-x.json")["storyPoints"])
        self.assertIn("duplicate name 'X': 2026-10-01-x.json, 2026-10-02-x.json", result.err)

    def test_the_entrys_name_held_by_two_features_is_ambiguous_and_changes_nothing(self):
        stored = document(new_feature("X", file="2026-10-01-x.json"), new_feature("X", file="2026-10-02-x.json"))
        self.seed(stored)
        self.assert_error(self.save({"name": "X", "storyPoints": 3, "effort": "x"}), EXIT_RUNTIME_ERROR,
                          "2 features are named 'X': 2026-10-01-x.json, 2026-10-02-x.json")
        self.assertEqual(stored, self.estimation())

    def test_a_save_names_the_duplicates_it_found_and_still_saves(self):
        self.seed(document(new_feature("X", file="2026-10-01-x.json"), new_feature("X", file="2026-10-02-x.json"),
                           new_feature("Y")))
        result = self.save({"name": "Y", "storyPoints": 3, "effort": "x"})
        self.assertEqual(3, self.assert_saved(result)["storyPoints"])
        self.assertIn("duplicate name 'X': 2026-10-01-x.json, 2026-10-02-x.json", result.err)


class FileNameTest(SaveTestCase):
    def test_the_name_part_is_lowercase_ascii_without_accents(self):
        saved = self.assert_saved(self.save({"name": "Számla export / PDF (v2)", "description": "x", "effort": "x"}))
        self.assertEqual("2026-11-04-szamla-export-pdf-v2.json", saved["file"])

    def test_letters_that_are_not_accented_ascii_become_a_separator(self):
        saved = self.assert_saved(self.save({"name": "Straße", "description": "x", "effort": "x"}))
        self.assertEqual("2026-11-04-stra-e.json", saved["file"])

    def test_a_long_name_part_is_cut(self):
        saved = self.assert_saved(self.save({"name": "a b" * (LONG_NAME_LENGTH // 3), "description": "x", "effort": "x"}))
        self.assertLessEqual(len(saved["file"]), MAX_FILE_NAME_LENGTH)
        self.assertEqual(saved["file"], self.feature_files()[0])
        self.assertFalse(saved["file"].endswith("-.json"), saved["file"])

    def test_a_name_without_ascii_letters_or_digits_is_called_feature(self):
        saved = self.assert_saved(self.save({"name": "Отчёт", "description": "x", "effort": "x"}))
        self.assertEqual("2026-11-04-feature.json", saved["file"])

    def test_a_file_name_taken_by_another_feature_is_refused_and_writes_nothing(self):
        stored = document(new_feature("CSV import", file="2026-11-04-csv-import.json"))
        self.seed(stored)
        self.assert_error(self.save({"name": "CSV-Import", "description": "x", "effort": "x"}), EXIT_RUNTIME_ERROR,
                          "2026-11-04-csv-import.json is the file of 'CSV import'")
        self.assertEqual(["2026-11-04-csv-import.json"], self.feature_files())
        self.assertEqual(stored, self.estimation())

    def test_a_stored_file_name_without_its_first_day_is_a_runtime_error(self):
        self.seed(document(new_feature("CSV import", file="csv-import.json")))
        self.assert_error(self.save({"name": "CSV import", "storyPoints": 3, "effort": "x"}), EXIT_RUNTIME_ERROR,
                          "csv-import.json does not start with its first day")

    def test_a_feature_file_that_is_not_a_json_object_is_a_runtime_error_naming_it(self):
        self.write(FEATURES_PATH + "/2026-10-01-a.json", "[]")
        self.assert_error(self.save({"name": "A", "description": "x"}), EXIT_RUNTIME_ERROR,
                          FEATURES_PATH + "/2026-10-01-a.json: not a JSON object")


class FirstDayTest(SaveTestCase):
    def save_new(self, *docs, **fields):
        return self.assert_saved(self.save({"name": "A", "description": "x", "effort": "x", "sources": {"docs": list(docs)},
                                            **fields}))

    def test_a_committed_doc_dates_the_feature_by_the_commit_that_added_it(self):
        self.commit("brief", {"docs/a-brief.md": "# A\n"}, when="2026-09-15T12:00:00+00:00")
        self.assertEqual("2026-09-15-a.json", self.save_new("docs/a-brief.md")["file"])

    def test_a_renamed_doc_keeps_the_day_it_was_first_added(self):
        self.commit("brief", {"old-brief.md": "# A\n"}, when="2026-08-01T12:00:00+00:00")
        os.makedirs(self.path("docs"))
        git(self.root, "mv", "old-brief.md", "docs/a-brief.md")
        self.commit("move", when="2026-09-15T12:00:00+00:00")
        self.assertEqual("2026-08-01-a.json", self.save_new("docs/a-brief.md")["file"])

    def test_an_uncommitted_doc_dates_the_feature_by_its_file_time(self):
        date_file(self.write("docs/a-brief.md", "# A\n"), "2026-07-04")
        self.assertEqual("2026-07-04-a.json", self.save_new("docs/a-brief.md")["file"])

    @unittest.skipUnless(hasattr(os.stat_result, "st_birthtime"), "the file system keeps no creation time")
    def test_a_doc_changed_after_its_creation_dates_the_feature_by_its_creation(self):
        path = self.write("docs/a-brief.md", "# A\n")
        date_file(path, "2026-07-04")
        date_file(path, "2026-08-01")
        self.assertEqual("2026-07-04-a.json", self.save_new("docs/a-brief.md")["file"])

    def test_a_doc_outside_the_project_dates_the_feature_by_its_file_time(self):
        outside = os.path.join(self.base, "notes.md")
        with open(outside, "w", encoding="utf-8") as handle:
            handle.write("# A\n")
        date_file(outside, "2026-06-01")
        self.assertEqual("2026-06-01-a.json", self.save_new(outside)["file"])

    def test_the_earliest_of_its_docs_and_its_commits_wins(self):
        self.commit("brief", {"docs/a-brief.md": "# A\n"}, when="2026-09-15T12:00:00+00:00")
        first = self.commit("BE-1", {"src/a.py": "a\n"}, when="2026-09-01T09:00:00+00:00")
        saved = self.save_new("docs/a-brief.md", git={"commits": 1, "linesAdded": 1, "linesRemoved": 0},
                              sources={"docs": ["docs/a-brief.md"], "commits": [first]})
        self.assertEqual("2026-09-01-a.json", saved["file"])

    def test_a_commit_dates_the_feature_by_its_author_day_not_its_committer_day(self):
        first = self.commit("BE-1", {"src/a.py": "a\n"}, when="2026-09-01T09:00:00+00:00",
                            committed="2026-09-20T09:00:00+00:00")
        saved = self.save_new(git={"commits": 1, "linesAdded": 1, "linesRemoved": 0}, sources={"docs": [], "commits": [first]})
        self.assertEqual("2026-09-01-a.json", saved["file"])

    def test_the_day_of_a_stored_feature_never_changes(self):
        self.commit("brief", {"docs/a-brief.md": "# A\n"}, when="2026-09-01T12:00:00+00:00")
        self.seed(document(new_feature("A", file="2026-10-01-a.json")))
        self.assert_saved(self.save({"name": "A", "storyPoints": 3, "effort": "x", "sources": {"docs": ["docs/a-brief.md"]}}))
        self.assertEqual(["2026-10-01-a.json"], self.feature_files())


class IdTest(SaveTestCase):
    def seed_with_id(self):
        self.seed(document(new_feature("A", file="2026-10-01-pm-12-a.json", id="PM-12"), new_feature("B")))

    def test_a_new_features_id_goes_into_its_file_name(self):
        saved = self.assert_saved(self.save({"name": "Export", "description": "x", "effort": "x", "id": "PM-12"}))
        self.assertEqual(("PM-12", "2026-11-04-pm-12-export.json"), (saved["id"], saved["file"]))

    def test_an_entry_without_an_id_keeps_the_stored_one(self):
        for entry in ({"name": "A", "storyPoints": 3, "effort": "x"}, {"name": "A", "id": None}):
            with self.subTest(entry=entry):
                self.seed_with_id()
                result = self.save(entry)
                self.assertEqual("PM-12", self.assert_saved(result)["id"])
                self.assertEqual("", result.err)
                self.assertEqual(["2026-10-01-b.json", "2026-10-01-pm-12-a.json"], self.feature_files())

    def test_an_id_found_later_renames_the_file(self):
        self.seed(document(new_feature("A")))
        result = self.save({"name": "A", "id": "PM-12"})
        self.assertEqual("PM-12", self.assert_saved(result)["id"])
        self.assertEqual("", result.err)
        self.assertEqual(["2026-10-01-pm-12-a.json"], self.feature_files())

    def test_another_id_replaces_the_stored_one_with_a_warning(self):
        self.seed_with_id()
        result = self.save({"name": "A", "id": "PM-13"})
        self.assertEqual("PM-13", self.assert_saved(result)["id"])
        self.assertEqual("estimate: id PM-13 replaces PM-12 for 'A'\n", result.err)
        self.assertEqual(["2026-10-01-b.json", "2026-10-01-pm-13-a.json"], self.feature_files())

    def test_the_id_of_another_feature_is_refused_naming_its_file(self):
        for given in ("PM-12", "pm-12"):
            with self.subTest(given=given):
                self.seed_with_id()
                self.assert_error(self.save({"name": "B", "id": given}), EXIT_RUNTIME_ERROR,
                                  "another feature has the id '%s': 2026-10-01-pm-12-a.json" % given)

    def test_an_id_without_a_letter_or_digit_is_a_bad_argument(self):
        for value in ("#", "---"):
            with self.subTest(value=value):
                self.assert_error(self.save({"name": "A", "description": "x", "id": value}), EXIT_BAD_ARGUMENTS,
                                  "id '%s' has no letter or digit for its file name" % value)

    def test_an_id_that_is_not_text_is_a_bad_argument(self):
        for value in (12, ""):
            with self.subTest(value=value):
                self.assert_error(self.save({"name": "A", "description": "x", "id": value}), EXIT_BAD_ARGUMENTS,
                                  "id must be text or null")


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
        saved = self.assert_saved(self.save({"name": "A", "storyPoints": 8, "effort": "x"}))
        self.assertEqual((8, TODAY.isoformat()), (saved["storyPoints"], saved["estimatedAt"]))

    def test_an_explicit_estimate_date_wins_over_today(self):
        saved = self.assert_saved(self.save({"name": "A", "description": "x", "effort": "x", "storyPoints": 3,
                                             "estimatedAt": "2026-10-30"}))
        self.assertEqual("2026-10-30", saved["estimatedAt"])

    def test_size_and_date_are_frozen_once_work_started_and_each_ignored_value_is_named(self):
        for status in ("in-progress", "delivered"):
            with self.subTest(status=status):
                self.seed(document(new_feature("A", status=status, storyPoints=8, estimatedAt="2026-10-01")))
                result = self.save({"name": "A", "storyPoints": 13, "effort": "x", "estimatedAt": "2026-11-04",
                                    "description": "new"})
                saved = self.assert_saved(result)
                self.assertEqual((8, "2026-10-01", "new"), (saved["storyPoints"], saved["estimatedAt"], saved["description"]))
                self.assertEqual(2, result.err.count("estimate: "), result.err)
                self.assertIn("storyPoints 13 ignored", result.err)
                self.assertIn("estimatedAt 2026-11-04 ignored", result.err)

    def test_a_set_updated_size_is_never_changed(self):
        self.seed(document(new_feature("A", status="delivered", updatedStoryPoints=8)))
        result = self.save({"name": "A", "updatedStoryPoints": 13, "effort": "x"})
        self.assertEqual(8, self.assert_saved(result)["updatedStoryPoints"])
        self.assertIn("updatedStoryPoints 13 ignored", result.err)

    def test_an_empty_updated_size_is_filled(self):
        self.seed(document(new_feature("A", status="delivered")))
        saved = self.assert_saved(self.save({"name": "A", "updatedStoryPoints": 13, "effort": "x"}))
        self.assertEqual(13, saved["updatedStoryPoints"])

    def test_a_size_outside_the_scale_is_refused(self):
        self.seed(document(new_feature("A")))
        for field, value in (("storyPoints", 4), ("updatedStoryPoints", "M"), ("storyPoints", True)):
            with self.subTest(field=field, value=value):
                self.assert_error(self.save({"name": "A", field: value, "effort": "x"}), EXIT_RUNTIME_ERROR, "not in the scale")

    def test_a_string_scale_takes_its_own_values(self):
        self.seed(document(new_feature("A"), scale=["S", "M", "L"]))
        self.assertEqual("M", self.assert_saved(self.save({"name": "A", "storyPoints": "M", "effort": "x"}))["storyPoints"])

    def test_an_unknown_status_is_refused(self):
        self.seed(document(new_feature("A")))
        self.assert_error(self.save({"name": "A", "status": "done"}), EXIT_RUNTIME_ERROR, "unknown status 'done'")


OPUS = {"harness": "claude", "models": ["claude-opus-5-5"]}
CODEX = {"harness": "codex", "models": ["gpt-5.5"]}


class EstimatorTest(SaveTestCase):
    def test_an_estimate_keeps_who_made_it_and_a_new_estimate_without_one_clears_it(self):
        self.seed(document(new_feature("A")))
        saved = self.assert_saved(self.save({"name": "A", "storyPoints": 5, "effort": "x", "estimatedBy": OPUS}))
        self.assertEqual(OPUS, saved["estimatedBy"])
        self.assertIsNone(self.assert_saved(self.save({"name": "A", "storyPoints": 8, "effort": "x"}))["estimatedBy"])

    def test_a_frozen_estimate_keeps_who_made_it(self):
        self.seed(document(new_feature("A", status="in-progress", storyPoints=8, estimatedBy=OPUS)))
        saved = self.assert_saved(self.save({"name": "A", "storyPoints": 13, "effort": "x", "estimatedBy": CODEX}))
        self.assertEqual((8, OPUS), (saved["storyPoints"], saved["estimatedBy"]))

    def test_the_after_delivery_size_keeps_who_set_it_and_neither_changes_after(self):
        self.seed(document(new_feature("A", status="delivered")))
        self.assert_saved(self.save({"name": "A", "updatedStoryPoints": 13, "effort": "x", "updatedBy": OPUS}))
        saved = self.assert_saved(self.save({"name": "A", "updatedStoryPoints": 8, "effort": "x", "updatedBy": CODEX}))
        self.assertEqual((13, OPUS), (saved["updatedStoryPoints"], saved["updatedBy"]))

    def test_who_made_a_size_without_the_size_is_a_bad_argument(self):
        for field, size in (("estimatedBy", "storyPoints"), ("updatedBy", "updatedStoryPoints")):
            with self.subTest(field=field):
                self.assert_error(self.save({"name": "A", "description": "x", field: OPUS}), EXIT_BAD_ARGUMENTS,
                                  "%s is saved with %s" % (field, size))

    def test_who_made_a_size_in_another_shape_is_a_bad_argument(self):
        for value in ({"harness": "vim", "models": None}, {"harness": "claude"}, {"harness": "claude", "models": [3]},
                      {**OPUS, "effort": "high"}, "claude"):
            with self.subTest(value=value):
                self.assert_error(self.save({"name": "A", "description": "x", "storyPoints": 5, "estimatedBy": value}),
                                  EXIT_BAD_ARGUMENTS, "estimatedBy must be")


IGNORED_SIZES = (("storyPoints", {"status": "in-progress", "storyPoints": 5}),
                 ("updatedStoryPoints", {"status": "delivered", "updatedStoryPoints": 5}))


class EffortTest(SaveTestCase):
    def test_a_new_feature_without_an_effort_is_a_bad_argument_and_writes_nothing(self):
        self.assert_error(self.save({"name": "Export", "description": "PDF export"}), EXIT_BAD_ARGUMENTS,
                          "a size needs its effort: 'Export'")
        self.assertFalse(os.path.exists(self.path(DATA_FOLDER)))

    def test_a_size_without_an_effort_is_a_bad_argument_and_changes_nothing(self):
        stored = document(new_feature("A"))
        self.seed(stored)
        for field in ("storyPoints", "updatedStoryPoints"):
            with self.subTest(field=field):
                self.assert_error(self.save({"name": "A", field: 5}), EXIT_BAD_ARGUMENTS, "a size needs its effort: 'A'")
                self.assertEqual(stored, self.estimation())

    def test_an_effort_that_is_not_text_is_a_bad_argument(self):
        for value in (None, 3, " "):
            with self.subTest(value=value):
                self.assert_error(self.save({"name": "A", "description": "x", "effort": value}), EXIT_BAD_ARGUMENTS,
                                  "effort must be text")

    def test_a_save_of_commits_or_sessions_without_a_size_needs_no_effort(self):
        self.seed(document(new_feature("A", status="delivered", updatedStoryPoints=5, effort="explains five")))
        saved = self.assert_saved(self.save({"name": "A", "git": {"commits": 1, "linesAdded": 7, "linesRemoved": 3},
                                             "usage": {"matchedBy": "prompt"},
                                             "sources": {"commits": [HASH_A], "sessions": [session("claude", "s1", 10)]}}))
        self.assertEqual(([HASH_A], 1, "explains five"), (saved["sources"]["commits"], saved["usage"]["sessions"], saved["effort"]))

    def test_the_effort_is_replaced_with_its_size(self):
        for field, stored in (("storyPoints", {"storyPoints": 5}), ("updatedStoryPoints", {"status": "delivered"})):
            with self.subTest(field=field):
                self.seed(document(new_feature("A", effort="explains the old size", **stored)))
                saved = self.assert_saved(self.save({"name": "A", field: 8, "effort": "explains eight"}))
                self.assertEqual((8, "explains eight"), (saved[field], saved["effort"]))

    def test_a_save_whose_size_is_ignored_keeps_the_effort_of_the_kept_size(self):
        for field, stored in IGNORED_SIZES:
            with self.subTest(field=field):
                self.seed(document(new_feature("A", effort="explains five", **stored)))
                result = self.save({"name": "A", field: 8, "effort": "explains eight"})
                saved = self.assert_saved(result)
                self.assertEqual((5, "explains five"), (saved[field], saved["effort"]))
                self.assertIn("%s 8 ignored" % field, result.err)

    def test_a_save_whose_size_is_ignored_gives_a_feature_saved_before_efforts_its_first(self):
        for field, stored in IGNORED_SIZES:
            with self.subTest(field=field):
                self.seed(document(before_effort(new_feature("A", **stored))))
                saved = self.assert_saved(self.save({"name": "A", field: 5, "effort": "explains five"}))
                self.assertEqual((5, "explains five"), (saved[field], saved["effort"]))

    def test_a_feature_saved_before_efforts_takes_a_save_without_a_size(self):
        self.seed(document(before_effort(new_feature("A"))))
        saved = self.assert_saved(self.save({"name": "A", "description": "new"}))
        self.assertEqual(before_effort(new_feature("A", description="new")), saved)


class PathTest(SaveTestCase):
    def test_docs_are_stored_root_relative_from_any_folder_and_outside_paths_absolute(self):
        os.makedirs(self.path("src"))
        outside = os.path.join(self.base, "notes.md")
        saved = self.assert_saved(self.save({"name": "A", "description": "x", "effort": "x",
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

    def test_a_commit_that_is_not_a_full_hash_is_a_bad_argument_and_writes_nothing(self):
        for value in ("--output=x", HASH_A[:10], HASH_A.upper()):
            with self.subTest(value=value):
                self.assert_error(self.save({"name": "A", "description": "x",
                                             "git": {"commits": 1, "linesAdded": 0, "linesRemoved": 0},
                                             "sources": {"commits": [value]}}),
                                  EXIT_BAD_ARGUMENTS, "sources.commits must hold full commit hashes")
                self.assertFalse(os.path.exists(self.path(DATA_FOLDER)))
                self.assertFalse(os.path.exists(self.path("x")))


class SessionTest(SaveTestCase):
    def test_given_sessions_replace_the_stored_ones_and_the_usage_is_their_sum(self):
        stored = [session("claude", "s1", 10, cost=1.10), session("omp", "no-longer-matched", 99)]
        self.seed(document(new_feature("A", sources={"docs": [], "commits": [], "sessions": stored},
                                       usage={"matchedBy": "prompt", "sessions": 2, "agentTimeMinutes": 109,
                                              "tokens": session("claude", "s1", 10)["tokens"], "costUsd": 2.35})))
        prompts = [{"number": 2, "at": "2026-10-01T10:00:00+00:00", "text": "go on", "reason": "follows prompt 1"}]
        newer = session("claude", "s1", 300, tokens={"input": 1000, "total": 1090}, cost=20.20, path="/abs/s1.jsonl", prompts=prompts,
                        models=["claude-opus-5-5"])
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
        saved = self.assert_saved(self.save({"name": "A", "description": "x", "effort": "x", "usage": {"matchedBy": "prompt"},
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
        saved = self.assert_saved(self.save({"name": "A", "description": "x", "effort": "x", "usage": {"matchedBy": "none"}}))
        self.assertEqual({"matchedBy": "none", "sessions": 0, "agentTimeMinutes": None,
                          "tokens": {"input": None, "output": None, "reasoning": None, "cacheRead": None, "cacheWrite": None,
                                     "total": None},
                          "costUsd": None}, saved["usage"])

    def test_the_first_sessions_need_a_matched_by_label(self):
        self.assert_error(self.save({"name": "A", "description": "x", "effort": "x",
                                     "sources": {"sessions": [session("claude", "s1", 1)]}}),
                          EXIT_RUNTIME_ERROR, "usage.matchedBy")

    def test_usage_stays_null_before_any_matching(self):
        self.assertIsNone(self.assert_saved(self.save({"name": "A", "description": "x", "effort": "x"}))["usage"])


if __name__ == "__main__":
    unittest.main()
