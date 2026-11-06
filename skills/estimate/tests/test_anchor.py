"""`anchor <hours>` (C3) and the anchor of the scale file: the SP that a feature's planned hours give at the project's anchor (by
default 13 SP = 75 h, one developer's two-week sprint), where its comparison starts; off when the scale file sets none or the scale
is not all numbers, refused when it is malformed."""
import json
import unittest

from tests.support import DEFAULT_SCALE, EXIT_BAD_ARGUMENTS, EXIT_OK, EXIT_RUNTIME_ERROR, SCALE_PATH, ProjectTestCase

ABSENT = object()
OFF = "no anchor in .harness-goodies/estimation.json"


class AnchorTest(ProjectTestCase):
    def settings(self, anchor=ABSENT, scale=None):
        settings = {"version": 1, "scale": DEFAULT_SCALE if scale is None else scale}
        if anchor is not ABSENT:
            settings["anchor"] = anchor
        self.write(SCALE_PATH, json.dumps(settings))

    def anchor(self, hours):
        result = self.run_estimate("anchor", hours)
        self.assertEqual(EXIT_OK, result.code, result.err)
        return result.json

    def test_without_a_scale_file_the_default_anchor_gives_the_size_of_the_hours(self):
        for hours, size in (("75", 13), ("30", 5), ("6", 1), ("150", 20), ("40.5", 8)):
            with self.subTest(hours=hours):
                self.assertEqual({"hours": json.loads(hours), "sp": size, "missing": []}, self.anchor(hours))

    def test_hours_between_two_sizes_go_to_the_nearer_one_by_ratio(self):
        # 58 h = 10.05 SP and 60 h = 10.4 SP at 13 SP = 75 h; the ratio midpoint of 8 and 13 is 10.2.
        self.assertEqual((8, 13), (self.anchor("58")["sp"], self.anchor("60")["sp"]))

    def test_the_scale_files_own_anchor_is_used(self):
        self.settings({"sp": 8, "hours": 40})
        self.assertEqual((8, 5), (self.anchor("40")["sp"], self.anchor("20")["sp"]))

    def test_an_anchor_that_is_absent_null_empty_or_without_hours_is_off(self):
        for anchor in (ABSENT, None, {}, {"sp": 13, "hours": 0}, {"sp": 13, "hours": None}):
            with self.subTest(anchor=anchor):
                self.settings(anchor)
                self.assertEqual({"hours": 75, "sp": None, "missing": [OFF]}, self.anchor("75"))

    def test_on_a_scale_that_is_not_all_numbers_the_anchor_is_off(self):
        self.settings({"sp": 13, "hours": 75}, scale=["S", "M", "L"])
        self.assertEqual({"hours": 75, "sp": None, "missing": ["the anchor needs a numeric scale"]}, self.anchor("75"))

    def test_hours_that_are_not_a_number_above_zero_are_a_bad_argument(self):
        for hours in ("abc", "0", "-3", "true", "NaN"):
            with self.subTest(hours=hours):
                self.assert_error(self.run_estimate("anchor", hours), EXIT_BAD_ARGUMENTS, "hours must be a number above 0")

    def test_a_malformed_anchor_is_a_runtime_error_naming_the_scale_file(self):
        cases = (({"sp": 7, "hours": 75}, "anchor sp 7 is not a value of the scale above 0"),
                 ({"sp": None, "hours": 75}, "anchor sp null is not a value of the scale above 0"),
                 ({"sp": 13, "hours": -1}, "anchor hours must be 0 or more"),
                 ({"sp": 13, "hours": "75"}, 'anchor must be null or {"sp": <SP>, "hours": <hours>}'),
                 ({"sp": 13, "hours": 75, "days": 10}, 'anchor must be null or {"sp": <SP>, "hours": <hours>}'),
                 ("", 'anchor must be null or {"sp": <SP>, "hours": <hours>}'))
        for anchor, fragment in cases:
            with self.subTest(anchor=anchor):
                self.settings(anchor)
                result = self.run_estimate("read")
                self.assert_error(result, EXIT_RUNTIME_ERROR, fragment)
                self.assertIn(SCALE_PATH, result.err)


if __name__ == "__main__":
    unittest.main()
