"""The shipped schemas (C2) accept the example files and reject what a consumer would get wrong: `estimation.schema.json` for the
scale file `.harness-goodies/estimation.json`, `feature.schema.json` for a feature file in `.harness-goodies/estimations/`.

The examples are in `fixtures/` in the same layout: `estimation.json` and `estimations/` with a delivered and an estimated feature.
Every case runs the real `check-jsonschema`, so the suite skips when it is not installed."""
import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tests.support import FEATURE_SCHEMA_PATH, SCALE_SCHEMA_PATH

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
SCALE_FIXTURE = os.path.join(FIXTURES, "estimation.json")
DELIVERED_FIXTURE = os.path.join(FIXTURES, "estimations", "2026-09-14-inv-7-invoice-pdf-export.json")
ESTIMATED_FIXTURE = os.path.join(FIXTURES, "estimations", "2026-11-03-estimate-skill.json")
DESCRIPTION_LIMIT = 300
EFFORT_LIMIT = 300
VALIDATOR = shutil.which("check-jsonschema")


def _load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


@unittest.skipUnless(VALIDATOR, "check-jsonschema is not installed")
class SchemaTestCase(unittest.TestCase):
    schema = None

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder)

    def validate(self, document):
        path = os.path.join(self.folder, "candidate.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        return subprocess.run([VALIDATOR, "--schemafile", self.schema, path], capture_output=True, text=True)

    def assert_valid(self, document):
        result = self.validate(document)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def assert_rejected(self, document):
        result = self.validate(document)
        self.assertNotEqual(0, result.returncode, result.stdout)


class ScaleSchemaTest(SchemaTestCase):
    schema = SCALE_SCHEMA_PATH

    def setUp(self):
        super().setUp()
        self.example = _load(SCALE_FIXTURE)

    def test_the_example_is_valid(self):
        self.assert_valid(self.example)

    def test_an_emoji_scale_is_valid(self):
        self.assert_valid({"version": 1, "scale": ["😇", "😉", "🙂"]})

    def test_an_anchor_may_be_off(self):
        for anchor in (None, {}, {"sp": 13, "hours": 0}, {"sp": 13, "hours": None}):
            with self.subTest(anchor=anchor):
                self.assert_valid({**self.example, "anchor": anchor})

    def test_a_malformed_anchor_is_rejected(self):
        for anchor in ("", 75, {"sp": 13, "hours": "75"}, {"sp": 13, "hours": -1}, {"sp": 0, "hours": 75},
                       {"sp": 13, "hours": 75, "days": 10}):
            with self.subTest(anchor=anchor):
                self.assert_rejected({**self.example, "anchor": anchor})

    def test_an_empty_or_repeating_scale_is_rejected(self):
        for scale in ([], [1, 2, 2]):
            with self.subTest(scale=scale):
                self.assert_rejected({"version": 1, "scale": scale})

    def test_a_missing_field_is_rejected(self):
        for field in ("version", "scale"):
            with self.subTest(field=field):
                self.assert_rejected({key: value for key, value in self.example.items() if key != field})

    def test_features_in_the_scale_file_are_rejected(self):
        self.assert_rejected({**self.example, "features": []})

    def test_another_version_is_rejected(self):
        self.assert_rejected({**self.example, "version": 2})


class FeatureSchemaTest(SchemaTestCase):
    schema = FEATURE_SCHEMA_PATH

    def setUp(self):
        super().setUp()
        self.delivered = _load(DELIVERED_FIXTURE)
        self.estimated = _load(ESTIMATED_FIXTURE)

    def test_the_examples_are_valid(self):
        self.assert_valid(self.delivered)
        self.assert_valid(self.estimated)

    def test_emoji_story_points_are_valid(self):
        self.assert_valid({**self.estimated, "storyPoints": "😉", "updatedStoryPoints": "🙂"})

    def test_an_id_is_text_or_null(self):
        self.assert_valid({**self.estimated, "id": "PM-12"})
        for value in ("", " ", 12):
            with self.subTest(value=value):
                self.assert_rejected({**self.estimated, "id": value})

    def test_a_scale_or_a_scale_link_in_a_feature_file_is_rejected(self):
        self.assert_rejected({**self.estimated, "scale": [1, 2, 3]})
        self.assert_rejected({**self.estimated, "scaleLink": "../estimation.json"})

    def test_a_description_over_the_limit_is_rejected(self):
        self.assert_rejected({**self.delivered, "description": "x" * (DESCRIPTION_LIMIT + 1)})

    def test_an_effort_is_optional_text_up_to_the_limit(self):
        self.assert_valid({**self.delivered, "effort": "x" * EFFORT_LIMIT})
        for value in ("x" * (EFFORT_LIMIT + 1), None, 3):
            with self.subTest(value=value):
                self.assert_rejected({**self.delivered, "effort": value})

    def test_an_unknown_status_is_rejected(self):
        self.assert_rejected({**self.delivered, "status": "done"})

    def test_a_missing_feature_field_is_rejected(self):
        for field in ("id", "plan"):
            with self.subTest(field=field):
                feature = copy.deepcopy(self.estimated)
                del feature[field]
                self.assert_rejected(feature)

    def test_an_unknown_matched_by_is_rejected(self):
        feature = copy.deepcopy(self.delivered)
        feature["usage"]["matchedBy"] = "guess"
        self.assert_rejected(feature)

    def test_a_date_in_another_format_is_rejected(self):
        self.assert_rejected({**self.estimated, "estimatedAt": "03/11/2026"})

    def test_a_session_of_an_unknown_harness_is_rejected(self):
        feature = copy.deepcopy(self.delivered)
        feature["sources"]["sessions"][0]["harness"] = "vim"
        self.assert_rejected(feature)

    def test_a_session_slice_lists_each_counted_prompt_from_one_with_its_reason(self):
        feature = copy.deepcopy(self.delivered)
        session = feature["sources"]["sessions"][0]
        prompt = {"number": 1, "at": "2026-09-01T10:00:00+00:00", "text": "/implement docs/x.md", "reason": "names docs/x.md"}
        session["prompts"] = [prompt]
        self.assert_valid(feature)
        for broken in ({**prompt, "number": 0}, {key: value for key, value in prompt.items() if key != "reason"}, 1):
            with self.subTest(broken=broken):
                session["prompts"] = [broken]
                self.assert_rejected(feature)

    def test_who_made_a_size_names_a_known_harness_and_its_models(self):
        maker = {"harness": "claude", "models": ["claude-opus-5-5"]}
        self.assert_valid({**self.delivered, "estimatedBy": maker, "updatedBy": {**maker, "models": None}})
        for broken in ({**maker, "harness": "vim"}, {"harness": "claude"}, {**maker, "models": [""]}, "claude"):
            with self.subTest(broken=broken):
                self.assert_rejected({**self.delivered, "updatedBy": broken})

    def test_a_fractional_line_count_is_rejected(self):
        feature = copy.deepcopy(self.delivered)
        feature["git"]["linesAdded"] = 1.5
        self.assert_rejected(feature)

    def test_another_version_is_rejected(self):
        self.assert_rejected({**self.delivered, "version": 2})


if __name__ == "__main__":
    unittest.main()
