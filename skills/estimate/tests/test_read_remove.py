"""`read`, `remove` and the write path every command shares (C3): the project root, the whole-file replace and the schema check."""
import os
import stat
import unittest

from tests.support import (DEFAULT_SCALE, ESTIMATION_FILE, EXIT_OK, EXIT_RUNTIME_ERROR, ProjectTestCase, document, new_feature)

FAILING_VALIDATOR = """#!/bin/sh
echo "Schema validation errors were encountered."
echo "  $3::\\$.features[0].status: 'done' is not one of ['estimated']"
exit 1
"""


class ReadTest(ProjectTestCase):
    def test_without_a_file_it_prints_the_empty_document_and_writes_nothing(self):
        result = self.run_estimate("read")
        self.assertEqual(EXIT_OK, result.code, result.err)
        self.assertEqual({"version": 1, "scale": DEFAULT_SCALE, "features": []}, result.json)
        self.assertFalse(os.path.exists(self.path(ESTIMATION_FILE)))

    def test_it_prints_the_file_of_the_git_top_level_from_a_subfolder(self):
        stored = document(new_feature("A"))
        self.seed(stored)
        os.makedirs(self.path("src/deep"))
        result = self.run_estimate("read", cwd=self.path("src/deep"))
        self.assertEqual(stored, result.json)

    def test_references_are_only_delivered_features_with_an_updated_size(self):
        reference = new_feature("Delivered and sized", status="delivered", updatedStoryPoints=8)
        self.seed(document(new_feature("Estimated", storyPoints=5, updatedStoryPoints=None),
                           new_feature("Delivered unsized", status="delivered"),
                           new_feature("In progress", status="in-progress", updatedStoryPoints=3),
                           reference))
        result = self.run_estimate("read", "--references")
        self.assertEqual(document(reference), result.json)

    def test_an_unreadable_file_is_a_runtime_error(self):
        self.write(ESTIMATION_FILE, "{not json")
        self.assert_error(self.run_estimate("read"), EXIT_RUNTIME_ERROR, ESTIMATION_FILE)


class RemoveTest(ProjectTestCase):
    def test_it_deletes_the_feature_by_exact_name_and_prints_it(self):
        kept, removed = new_feature("Keep"), new_feature("Drop")
        self.seed(document(kept, removed))
        result = self.run_estimate("remove", "Drop")
        self.assertEqual(EXIT_OK, result.code, result.err)
        self.assertEqual(removed, result.json)
        self.assertEqual(document(kept), self.estimation())

    def test_an_unknown_name_is_a_runtime_error_and_changes_nothing(self):
        stored = document(new_feature("Keep"))
        self.seed(stored)
        self.assert_error(self.run_estimate("remove", "keep"), EXIT_RUNTIME_ERROR, "no feature named 'keep'")
        self.assertEqual(stored, self.estimation())


class ValidatedWriteTest(ProjectTestCase):
    def install_failing_validator(self):
        path = os.path.join(self.base, "check-jsonschema")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(FAILING_VALIDATOR)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        return lambda name: path if name == "check-jsonschema" else None

    def test_a_failed_validation_leaves_the_old_file_and_no_temp_file(self):
        stored = document(new_feature("Keep"), new_feature("Drop"))
        self.seed(stored)
        result = self.run_estimate("remove", "Drop", which=self.install_failing_validator())
        self.assert_error(result, EXIT_RUNTIME_ERROR, "$.features[0].status: 'done' is not one of ['estimated']")
        self.assertEqual(stored, self.estimation())
        self.assertEqual([".estimation.json", ".git"], sorted(os.listdir(self.root)))

    def test_without_check_jsonschema_the_write_is_not_validated(self):
        self.seed(document(new_feature("Keep"), new_feature("Drop")))
        result = self.run_estimate("remove", "Drop", which=lambda name: None)
        self.assertEqual(EXIT_OK, result.code, result.err)
        self.assertEqual(["Keep"], [feature["name"] for feature in self.estimation()["features"]])


if __name__ == "__main__":
    unittest.main()
