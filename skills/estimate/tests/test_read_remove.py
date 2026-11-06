"""`read`, `remove` and the write path every command shares (C3): the project root, the folder of one file per feature, the
duplicate warnings, the whole-file replace and the schema checks."""
import json
import os
import stat
import unittest

from tests.support import (DATA_FOLDER, DEFAULT_ANCHOR, DEFAULT_SCALE, EXIT_OK, EXIT_RUNTIME_ERROR, FEATURES_PATH, SCALE_PATH,
                           ProjectTestCase, before_effort, document, new_feature)

# Fails for the schema file named in place of `%s`, passes any other; check-jsonschema is run as `--schemafile <schema> <file>`.
FAILING_VALIDATOR = """#!/bin/sh
case "$2" in
  */%s) echo "Schema validation errors were encountered."
        echo "  $3::\\$.status: 'done' is not one of ['estimated']"
        exit 1;;
esac
exit 0
"""


class ReadTest(ProjectTestCase):
    def test_without_a_folder_it_prints_the_empty_document_and_writes_nothing(self):
        result = self.run_estimate("read")
        self.assertEqual(EXIT_OK, result.code, result.err)
        self.assertEqual({"version": 1, "scale": DEFAULT_SCALE, "anchor": DEFAULT_ANCHOR, "features": []}, result.json)
        self.assertFalse(os.path.exists(self.path(DATA_FOLDER)))

    def test_it_prints_the_folder_of_the_git_top_level_from_a_subfolder(self):
        stored = document(new_feature("A"), scale=["S", "M", "L"])
        self.seed(stored)
        os.makedirs(self.path("src/deep"))
        result = self.run_estimate("read", cwd=self.path("src/deep"))
        self.assertEqual(stored, result.json)

    def test_features_come_in_file_name_order_each_with_its_file(self):
        later, earlier = new_feature("B", file="2026-10-02-b.json"), new_feature("A", file="2026-09-30-a.json")
        self.seed(document(later, earlier))
        self.assertEqual(document(earlier, later), self.run_estimate("read").json)

    def test_references_are_only_delivered_features_with_an_updated_size(self):
        reference = new_feature("Delivered and sized", status="delivered", updatedStoryPoints=8)
        self.seed(document(new_feature("Estimated", storyPoints=5, updatedStoryPoints=None),
                           new_feature("Delivered unsized", status="delivered"),
                           new_feature("In progress", status="in-progress", updatedStoryPoints=3),
                           reference))
        result = self.run_estimate("read", "--references")
        self.assertEqual(document(reference), result.json)

    def test_a_feature_saved_before_efforts_still_loads(self):
        stored = document(before_effort(new_feature("A", status="delivered", updatedStoryPoints=8)))
        self.seed(stored)
        self.assertEqual(stored, self.run_estimate("read", "--references").json)

    def test_an_unreadable_feature_file_is_a_runtime_error_naming_it(self):
        self.seed(document(new_feature("A")))
        self.write(FEATURES_PATH + "/2026-10-02-b.json", "{not json")
        self.assert_error(self.run_estimate("read"), EXIT_RUNTIME_ERROR, FEATURES_PATH + "/2026-10-02-b.json")

    def test_an_unreadable_scale_file_is_a_runtime_error_naming_it(self):
        self.write(SCALE_PATH, json.dumps({"version": 1, "scale": "1-2-3"}))
        self.assert_error(self.run_estimate("read"), EXIT_RUNTIME_ERROR, SCALE_PATH)

    def test_a_feature_file_of_another_version_is_refused(self):
        self.write(FEATURES_PATH + "/2026-10-01-a.json", json.dumps({"version": 2, "name": "A"}))
        self.assert_error(self.run_estimate("read"), EXIT_RUNTIME_ERROR, "2026-10-01-a.json: version 2 (expected 1)")

    def test_a_left_over_temp_file_and_other_files_are_ignored(self):
        stored = document(new_feature("A"))
        self.seed(stored)
        self.write(FEATURES_PATH + "/.estimation.abc123.json", "{")
        self.write(FEATURES_PATH + "/notes.md", "# notes")
        self.assertEqual(stored, self.run_estimate("read").json)

    def test_features_sharing_a_name_an_id_or_a_doc_are_each_named_as_a_warning(self):
        shared_doc = {"docs": ["docs/x.md"], "commits": [], "sessions": []}
        self.seed(document(new_feature("X", file="2026-10-01-x.json", id="PM-1"),
                           new_feature("X", file="2026-10-02-x.json", id="PM-1", sources=shared_doc),
                           new_feature("Y", file="2026-10-03-y.json", sources=shared_doc)))
        result = self.run_estimate("read")
        self.assertEqual(EXIT_OK, result.code, result.err)
        self.assertEqual(["estimate: duplicate name 'X': 2026-10-01-x.json, 2026-10-02-x.json",
                          "estimate: duplicate id 'PM-1': 2026-10-01-x.json, 2026-10-02-x.json",
                          "estimate: duplicate doc docs/x.md: 2026-10-02-x.json, 2026-10-03-y.json"], result.err.splitlines())
        self.assertEqual(3, len(result.json["features"]))

    def test_ids_with_the_same_file_name_form_are_duplicates(self):
        self.seed(document(new_feature("A", file="2026-10-01-pm-1-a.json", id="PM-1"),
                           new_feature("B", file="2026-10-01-pm-1-b.json", id="pm-1")))
        result = self.run_estimate("read")
        self.assertEqual("estimate: duplicate id 'PM-1': 2026-10-01-pm-1-a.json, 2026-10-01-pm-1-b.json\n", result.err)


class RemoveTest(ProjectTestCase):
    def test_it_deletes_the_feature_file_and_prints_the_feature(self):
        kept, removed = new_feature("Keep"), new_feature("Drop")
        self.seed(document(kept, removed))
        result = self.run_estimate("remove", removed["file"])
        self.assertEqual(EXIT_OK, result.code, result.err)
        self.assertEqual(removed, result.json)
        self.assertEqual(document(kept), self.estimation())

    def test_removing_one_of_two_features_of_the_same_name_ends_the_warning(self):
        first, second = new_feature("X", file="2026-10-01-x.json"), new_feature("X", file="2026-10-02-x.json")
        self.seed(document(first, second))
        result = self.run_estimate("remove", "2026-10-02-x.json")
        self.assertEqual((EXIT_OK, ""), (result.code, result.err))
        self.assertEqual(document(first), self.estimation())

    def test_removing_a_feature_still_names_the_duplicates_the_other_files_hold(self):
        self.seed(document(new_feature("X", file="2026-10-01-x.json"), new_feature("X", file="2026-10-02-x.json"),
                           new_feature("Drop")))
        result = self.run_estimate("remove", new_feature("Drop")["file"])
        self.assertEqual(EXIT_OK, result.code, result.err)
        self.assertEqual("estimate: duplicate name 'X': 2026-10-01-x.json, 2026-10-02-x.json\n", result.err)

    def test_an_unknown_file_is_a_runtime_error_and_changes_nothing(self):
        stored = document(new_feature("Keep"))
        self.seed(stored)
        self.assert_error(self.run_estimate("remove", "Keep"), EXIT_RUNTIME_ERROR, "no feature file 'Keep'")
        self.assertEqual(stored, self.estimation())


class ValidatedWriteTest(ProjectTestCase):
    def install_failing_validator(self, schema):
        path = os.path.join(self.base, "check-jsonschema")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(FAILING_VALIDATOR % schema)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        return lambda name: path if name == "check-jsonschema" else None

    def test_a_failed_feature_validation_leaves_the_old_file_and_no_temp_file(self):
        stored = document(new_feature("Keep", storyPoints=3))
        self.seed(stored)
        result = self.run_estimate("save", json.dumps({"name": "Keep", "storyPoints": 5, "effort": "x"}),
                                   which=self.install_failing_validator("feature.schema.json"))
        self.assert_error(result, EXIT_RUNTIME_ERROR, "$.status: 'done' is not one of ['estimated']")
        self.assertIn("fails feature.schema.json", result.err)
        self.assertEqual(stored, self.estimation())
        self.assertEqual([stored["features"][0]["file"]], self.feature_files())

    def test_a_first_save_failing_either_schema_writes_nothing(self):
        for schema in ("estimation.schema.json", "feature.schema.json"):
            with self.subTest(schema=schema):
                result = self.run_estimate("save", json.dumps({"name": "New", "description": "x", "effort": "x"}),
                                           which=self.install_failing_validator(schema))
                self.assert_error(result, EXIT_RUNTIME_ERROR, "fails " + schema)
                self.assertEqual([], [name for _, _, names in os.walk(self.path(DATA_FOLDER)) for name in names])

    def test_without_check_jsonschema_the_write_is_not_validated(self):
        self.seed(document(new_feature("Keep")))
        result = self.run_estimate("save", json.dumps({"name": "Keep", "storyPoints": 5, "effort": "x"}),
                                   which=lambda name: None)
        self.assertEqual(EXIT_OK, result.code, result.err)
        self.assertEqual(5, self.feature("Keep")["storyPoints"])


if __name__ == "__main__":
    unittest.main()
