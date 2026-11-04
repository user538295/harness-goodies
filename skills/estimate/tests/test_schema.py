"""The shipped `estimation.schema.json` (C2) accepts the brief's example file and rejects what a consumer would get wrong.

The example is a verbatim copy of the brief's "Example `.estimation.json`" block (`fixtures/brief-example.estimation.json`). Every
case runs the real `check-jsonschema`, so the suite skips when it is not installed."""
import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tests.support import SCHEMA_PATH

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "brief-example.estimation.json")
DESCRIPTION_LIMIT = 300
VALIDATOR = shutil.which("check-jsonschema")


@unittest.skipUnless(VALIDATOR, "check-jsonschema is not installed")
class SchemaTest(unittest.TestCase):
    def setUp(self):
        with open(FIXTURE, encoding="utf-8") as handle:
            self.example = json.load(handle)
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder)

    def validate(self, document):
        path = os.path.join(self.folder, "candidate.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        return subprocess.run([VALIDATOR, "--schemafile", SCHEMA_PATH, path], capture_output=True, text=True)

    def assert_rejected(self, document):
        result = self.validate(document)
        self.assertNotEqual(0, result.returncode, result.stdout)

    def test_the_brief_example_is_valid(self):
        result = self.validate(self.example)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_an_emoji_scale_is_valid(self):
        document = copy.deepcopy(self.example)
        document["scale"] = ["😇", "😉", "🙂"]
        document["features"][0]["updatedStoryPoints"] = "🙂"
        document["features"][1]["storyPoints"] = "😉"
        self.assertEqual(0, self.validate(document).returncode)

    def test_a_description_over_the_limit_is_rejected(self):
        document = copy.deepcopy(self.example)
        document["features"][0]["description"] = "x" * (DESCRIPTION_LIMIT + 1)
        self.assert_rejected(document)

    def test_an_unknown_status_is_rejected(self):
        document = copy.deepcopy(self.example)
        document["features"][0]["status"] = "done"
        self.assert_rejected(document)

    def test_a_missing_feature_field_is_rejected(self):
        document = copy.deepcopy(self.example)
        del document["features"][1]["plan"]
        self.assert_rejected(document)

    def test_an_unknown_matched_by_is_rejected(self):
        document = copy.deepcopy(self.example)
        document["features"][0]["usage"]["matchedBy"] = "guess"
        self.assert_rejected(document)

    def test_a_date_in_another_format_is_rejected(self):
        document = copy.deepcopy(self.example)
        document["features"][1]["estimatedAt"] = "03/11/2026"
        self.assert_rejected(document)

    def test_a_session_of_an_unknown_harness_is_rejected(self):
        document = copy.deepcopy(self.example)
        document["features"][0]["sources"]["sessions"][0]["harness"] = "vim"
        self.assert_rejected(document)

    def test_a_session_slice_names_its_prompts_from_one(self):
        document = copy.deepcopy(self.example)
        session = document["features"][0]["sources"]["sessions"][0]
        session["prompts"] = [1, 2]
        self.assertEqual(0, self.validate(document).returncode)
        session["prompts"] = [0]
        self.assert_rejected(document)

    def test_a_fractional_line_count_is_rejected(self):
        document = copy.deepcopy(self.example)
        document["features"][0]["git"]["linesAdded"] = 1.5
        self.assert_rejected(document)

    def test_another_version_is_rejected(self):
        document = copy.deepcopy(self.example)
        document["version"] = 2
        self.assert_rejected(document)


if __name__ == "__main__":
    unittest.main()
