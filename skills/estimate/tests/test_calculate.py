"""`calculate <features-json | ->` (C3): the SP of every feature card at the scale file's anchor, as `anchor <estimatedHours>` gives
it, and the pool of the features that got one, each in the shape `neighbours` and `place` accept. A card without planned hours, or
any card while the anchor is off, gets no SP, the reason in `missing`, and stays out of the pool; malformed cards are refused."""
import json
import unittest

from tests.support import DEFAULT_SCALE, EXIT_BAD_ARGUMENTS, EXIT_OK, SCALE_PATH, ProjectTestCase

OFF = "no anchor in .harness-goodies/estimation.json"
NO_HOURS = "no planned hours"
SPRINT_HOURS = 75
SMALL_HOURS = 6
CUSTOM_ANCHOR = {"sp": 8, "hours": 40}
CUSTOM_SCALE = [1, 2, 4, 8, 16]
CUSTOM_HOURS = 20
CUSTOM_SIZE = 4  # 20 h at 8 SP = 40 h is 4 SP


def card(name, hours, tasks=3, **fields):
    return {"name": name, "id": name.lower(), "description": "%s description" % name, "effort": "%s effort" % name,
            "tasks": tasks, "estimatedHours": hours, **fields}


class CalculateTest(ProjectTestCase):
    def settings(self, anchor=None, scale=None):
        settings = {"version": 1, "scale": DEFAULT_SCALE if scale is None else scale}
        if anchor is not None:
            settings["anchor"] = anchor
        self.write(SCALE_PATH, json.dumps(settings))

    def calculate(self, *cards):
        result = self.run_estimate("calculate", "-", stdin=json.dumps({"features": list(cards)}))
        self.assertEqual(EXIT_OK, result.code, result.err)
        return result.json

    def anchor_size(self, hours):
        result = self.run_estimate("anchor", json.dumps(hours))
        self.assertEqual(EXIT_OK, result.code, result.err)
        return result.json["sp"]

    def assert_bad(self, features_json, fragment):
        self.assert_error(self.run_estimate("calculate", features_json), EXIT_BAD_ARGUMENTS, fragment)

    def test_each_feature_with_planned_hours_gets_the_size_anchor_gives_for_them(self):
        hours = (SMALL_HOURS, 30, 40.5, SPRINT_HOURS, 150)
        result = self.calculate(*(card("F%d" % index, value) for index, value in enumerate(hours)))
        self.assertEqual([{"name": "F%d" % index, "hours": value, "sp": self.anchor_size(value), "missing": []}
                          for index, value in enumerate(hours)], result["features"])

    def test_the_pool_holds_each_sized_card_in_the_shape_neighbours_compares(self):
        alpha = card("Alpha", SPRINT_HOURS)
        result = self.calculate(alpha)
        self.assertEqual([{**alpha, "sp": self.anchor_size(SPRINT_HOURS)}], result["pool"])

    def test_a_feature_without_planned_hours_gets_no_size_and_stays_out_of_the_pool(self):
        result = self.calculate(card("Alpha", None, tasks=None), card("Beta", SPRINT_HOURS))
        self.assertEqual({"name": "Alpha", "hours": None, "sp": None, "missing": [NO_HOURS]}, result["features"][0])
        self.assertEqual(["Beta"], [item["name"] for item in result["pool"]])

    def test_features_and_pool_keep_the_input_order(self):
        names = ["Zeta", "Alpha", "Mu"]
        result = self.calculate(*(card(name, SPRINT_HOURS) for name in names))
        self.assertEqual((names, names), ([item["name"] for item in result["features"]],
                                          [item["name"] for item in result["pool"]]))

    def test_with_the_anchor_off_no_feature_gets_a_size_and_the_pool_is_empty(self):
        self.settings()
        result = self.calculate(card("Alpha", SPRINT_HOURS), card("Beta", None))
        self.assertEqual({"features": [{"name": "Alpha", "hours": SPRINT_HOURS, "sp": None, "missing": [OFF]},
                                       {"name": "Beta", "hours": None, "sp": None, "missing": [NO_HOURS]}],
                          "pool": []}, result)

    def test_the_scale_files_own_anchor_and_scale_are_used(self):
        self.settings(CUSTOM_ANCHOR, CUSTOM_SCALE)
        result = self.calculate(card("Alpha", CUSTOM_HOURS))
        self.assertEqual((CUSTOM_SIZE, CUSTOM_SIZE), (result["features"][0]["sp"], self.anchor_size(CUSTOM_HOURS)))

    def test_neighbours_accepts_the_pool_unchanged(self):
        result = self.calculate(card("Alpha", SMALL_HOURS), card("Beta", SPRINT_HOURS), card("Gamma", SPRINT_HOURS))
        picked = self.run_estimate("neighbours", "-", stdin=json.dumps({"name": "Alpha", "pool": result["pool"]}))
        self.assertEqual(EXIT_OK, picked.code, picked.err)
        self.assertEqual(["Beta", "Gamma"], [item["name"] for item in picked.json["picks"]])

    def test_an_empty_list_gives_no_features_and_an_empty_pool(self):
        self.assertEqual({"features": [], "pool": []}, self.calculate())

    def test_input_that_is_not_json_is_a_bad_argument(self):
        self.assert_bad("{", "features is not JSON")

    def test_input_that_is_not_an_object_with_a_features_list_is_a_bad_argument(self):
        for features_json in ("[]", "{}", '{"features": {}}'):
            with self.subTest(features_json=features_json):
                self.assert_bad(features_json, "must be a JSON object with a features list")

    def test_an_unknown_field_is_a_bad_argument(self):
        cases = (({"features": [], "pool": []}, "pool"), ({"features": [card("Alpha", 1, size=3)]}, "size"))
        for request, key in cases:
            with self.subTest(key=key):
                self.assert_bad(json.dumps(request), "unknown field '%s'" % key)

    def test_a_feature_without_a_name_is_a_bad_argument(self):
        for item in ("Alpha", {"estimatedHours": 1}, card("  ", 1), {**card("Alpha", 1), "name": 7}):
            with self.subTest(item=item):
                self.assert_bad(json.dumps({"features": [item]}), "each feature needs a name")

    def test_a_duplicate_name_is_a_bad_argument(self):
        self.assert_bad(json.dumps({"features": [card("Alpha", 1), card("Alpha", 2)]}), "duplicate feature name 'Alpha'")

    def test_hours_that_are_not_a_number_above_zero_or_null_are_a_bad_argument(self):
        for hours in (0, -3, "75", True, float("nan"), float("inf")):
            with self.subTest(hours=hours):
                self.assert_bad(json.dumps({"features": [card("Alpha", hours)]}),
                                "estimatedHours of 'Alpha' must be a number above 0 or null")

    def test_tasks_that_are_not_an_integer_of_zero_or_more_or_null_are_a_bad_argument(self):
        for tasks in (-1, 2.5, "3", False):
            with self.subTest(tasks=tasks):
                self.assert_bad(json.dumps({"features": [card("Alpha", 1, tasks=tasks)]}),
                                "tasks of 'Alpha' must be an integer of 0 or more or null")


if __name__ == "__main__":
    unittest.main()
