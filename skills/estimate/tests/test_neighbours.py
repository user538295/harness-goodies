"""`neighbours <request-json | ->` and `place <request-json | ->` (C3): the picks to compare a feature with, from the stored references
(delivered, with an after-delivery SP) or from the request's own `pool`, shown to the judge without their sizes; and the size the two
judge calls place the feature at among the same picks. A malformed request is a bad argument, a scale that is not all numbers a
runtime error."""
import json
import unittest

from tests.support import (EXIT_BAD_ARGUMENTS, EXIT_OK, EXIT_RUNTIME_ERROR, ProjectTestCase, before_effort, document,
                           new_feature)


def reference(name, sp, **fields):
    return new_feature(name, status="delivered", updatedStoryPoints=sp, **fields)


def pool_item(name, sp, hours=None):
    return {"name": name, "id": None, "description": "d", "effort": "e", "sp": sp, "tasks": 3, "estimatedHours": hours}


BAD_REQUESTS = (("{name", "request is not JSON"),
                ("[]", "request must be a JSON object"),
                (json.dumps({}), "request needs a name"),
                (json.dumps({"name": " "}), "request needs a name"),
                (json.dumps({"name": "New", "sp": 5}), "unknown field 'sp'"),
                (json.dumps({"name": "New", "pool": {}}), "pool must be a list"),
                (json.dumps({"name": "New", "pool": [{"sp": 5}]}), "each pool item needs a name"),
                (json.dumps({"name": "New", "pool": [3]}), "each pool item needs a name"),
                (json.dumps({"name": "New", "pool": [{"name": "A", "sp": "5"}]}), 'sp "5" of pool item \'A\' is not in the scale'),
                (json.dumps({"name": "New", "pool": [{"name": "A", "sp": 4}]}), "sp 4 of pool item 'A' is not in the scale"))


class NeighboursTest(ProjectTestCase):
    def neighbours(self, request):
        result = self.run_estimate("neighbours", json.dumps(request))
        self.assertEqual(EXIT_OK, result.code, result.err)
        return result.json

    def test_the_stored_references_are_the_pool(self):
        self.seed(document(reference("Small", 3, id="A6", plan={"phases": 1, "tasks": 4, "estimatedHours": 6.5}),
                           before_effort(reference("Same", 5)),
                           reference("Big", 8, storyPoints=5),
                           new_feature("Estimated", storyPoints=5),
                           new_feature("Delivered unsized", status="delivered"),
                           new_feature("In progress", status="in-progress", updatedStoryPoints=5)))
        self.assertEqual({"picks": [{"name": "Big", "id": None, "description": "About Big", "effort": "Work of Big", "tasks": None,
                                     "estimatedHours": None},
                                    {"name": "Same", "id": None, "description": "About Same", "effort": None, "tasks": None,
                                     "estimatedHours": None},
                                    {"name": "Small", "id": "A6", "description": "About Small", "effort": "Work of Small",
                                     "tasks": 4, "estimatedHours": 6.5}],
                          "groups": [{"sp": 3, "names": ["Small"]}, {"sp": 5, "names": ["Same"]}, {"sp": 8, "names": ["Big"]}],
                          "missing": ["only 1 reference at 3 SP", "only 1 reference at 5 SP", "no effort for 'Same'",
                                      "only 1 reference at 8 SP"]},
                         self.neighbours({"name": "New"}))

    def test_a_given_pool_takes_the_place_of_the_references(self):
        self.seed(document(reference("Stored", 5)))
        shown = {"name": "In memory", "id": "B2", "description": "d", "effort": "e", "tasks": 3, "estimatedHours": 2.0}
        result = self.neighbours({"name": "New", "pool": [{**shown, "sp": 5}, {"name": "New", "sp": 8}]})
        self.assertEqual({"picks": [shown], "groups": [{"sp": 5, "names": ["In memory"]}], "missing": ["only 1 reference at 5 SP"]},
                         result)

    def test_without_references_there_is_nothing_to_compare_with(self):
        self.seed(document())
        self.assertEqual({"picks": [], "groups": [], "missing": ["no reference to compare with"]}, self.neighbours({"name": "New"}))

    def test_the_request_can_come_from_stdin(self):
        self.seed(document(reference("Same", 5)))
        result = self.run_estimate("neighbours", "-", stdin=json.dumps({"name": "New"}))
        self.assertEqual([{"sp": 5, "names": ["Same"]}], result.json["groups"])

    def test_a_malformed_request_is_a_bad_argument(self):
        self.seed(document())
        for text, fragment in BAD_REQUESTS:
            with self.subTest(text=text):
                self.assert_error(self.run_estimate("neighbours", text), EXIT_BAD_ARGUMENTS, fragment)

    def test_a_scale_that_is_not_all_numbers_is_a_runtime_error(self):
        self.seed(document(reference("Same", "M"), scale=["S", "M", "L"]))
        self.assert_error(self.run_estimate("neighbours", json.dumps({"name": "New"})), EXIT_RUNTIME_ERROR,
                          "needs a numeric scale")


class PlaceTest(ProjectTestCase):
    def place(self, request):
        result = self.run_estimate("place", json.dumps(request))
        self.assertEqual(EXIT_OK, result.code, result.err)
        return result.json

    def test_the_stored_references_are_the_pool(self):
        self.seed(document(reference("Small", 3, effort="w"), reference("Big", 8, id="B-1", effort="w"),
                           new_feature("Estimated", storyPoints=5)))
        self.assertEqual({"sp": 5, "tie": None, "report": "bigger than Small (3), smaller than B-1 (8)",
                          "verdicts": [{"name": "Small", "id": None, "sp": 3, "verdict": "bigger", "pickVerdict": "smaller"},
                                       {"name": "Big", "id": "B-1", "sp": 8, "verdict": "smaller", "pickVerdict": "bigger"}],
                          "missing": ["only 1 reference at 3 SP", "no reference at 5 SP", "only 1 reference at 8 SP"]},
                         self.place({"name": "New", "verdicts": {"Small": "bigger", "Big": "smaller"},
                                     "pickVerdicts": {"Small": "smaller", "Big": "bigger"}}))

    def test_a_given_pool_and_hours_break_a_tie(self):
        self.seed(document(reference("Stored", 13)))
        result = self.place({"name": "New", "pool": [pool_item("S", 5, 30), pool_item("M", 8, 50)], "hours": 33,
                             "verdicts": {"S": "bigger", "M": "smaller"}, "pickVerdicts": {"S": "smaller", "M": "bigger"}})
        self.assertEqual((5, {"between": [5, 8], "by": "hours"}), (result["sp"], result["tie"]))

    def test_without_hours_a_tie_rounds_up(self):
        self.seed(document())
        result = self.place({"name": "New", "pool": [pool_item("S", 5, 30), pool_item("M", 8, 50)], "hours": None,
                             "verdicts": {"S": "bigger", "M": "smaller"}, "pickVerdicts": {"S": "smaller", "M": "bigger"}})
        self.assertEqual((8, {"between": [5, 8], "by": "round up"}), (result["sp"], result["tie"]))

    def test_without_picks_there_is_no_size(self):
        self.seed(document())
        self.assertEqual({"sp": None, "tie": None, "report": "", "verdicts": [], "missing": ["no reference to compare with"]},
                         self.place({"name": "New", "verdicts": {}, "pickVerdicts": {}}))

    def test_the_request_can_come_from_stdin(self):
        self.seed(document(reference("Same", 5)))
        result = self.run_estimate("place", "-", stdin=json.dumps({"name": "New", "verdicts": {"Same": "same"},
                                                                   "pickVerdicts": {"Same": "same"}}))
        self.assertEqual(5, result.json["sp"])

    def test_a_malformed_request_is_a_bad_argument(self):
        self.seed(document(reference("Same", 5)))
        verdicts = {"verdicts": {"Same": "same"}, "pickVerdicts": {"Same": "same"}}
        cases = [(text, fragment) for text, fragment in BAD_REQUESTS if "unknown field" not in fragment] + [
            (json.dumps({"name": "New", "extra": 1, **verdicts}), "unknown field 'extra'"),
            (json.dumps({"name": "New", "hours": 0, **verdicts}), "hours must be a number above 0"),
            (json.dumps({"name": "New", "hours": "5", **verdicts}), "hours must be a number above 0"),
            (json.dumps({"name": "New", "hours": True, **verdicts}), "hours must be a number above 0"),
            (json.dumps({"name": "New", "pickVerdicts": {"Same": "same"}}), "verdicts must be an object"),
            (json.dumps({"name": "New", "verdicts": {}, "pickVerdicts": {"Same": "same"}}), "missing 'Same'"),
            (json.dumps({"name": "New", "verdicts": {"Same": "same", "X": "same"}, "pickVerdicts": {"Same": "same"}}),
             "unknown 'X'"),
            (json.dumps({"name": "New", "verdicts": {"Same": "same"}, "pickVerdicts": {"Same": "huge"}}), "'huge' for 'Same'")]
        for text, fragment in cases:
            with self.subTest(text=text):
                self.assert_error(self.run_estimate("place", text), EXIT_BAD_ARGUMENTS, fragment)

    def test_a_scale_that_is_not_all_numbers_is_a_runtime_error(self):
        self.seed(document(reference("Same", "M"), scale=["S", "M", "L"]))
        self.assert_error(self.run_estimate("place", json.dumps({"name": "New", "verdicts": {}, "pickVerdicts": {}})),
                          EXIT_RUNTIME_ERROR, "needs a numeric scale")


if __name__ == "__main__":
    unittest.main()
