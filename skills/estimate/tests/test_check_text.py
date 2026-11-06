"""`check-text <texts-json | ->` and the same rules at `save`: a feature's `description` and `effort` are at most 300 characters
each, neither is blank, and the effort has no digits. `check-text` lists the problems of the texts it is given; `save` refuses an
entry whose given texts have one, before anything is written, and never re-checks the texts already stored."""
import json
import os
import unittest

from tests.support import DATA_FOLDER, EXIT_BAD_ARGUMENTS, EXIT_OK, ProjectTestCase, document, new_feature

TEXT_LIMIT = 300
CLEAN = {"description": "Exports the invoices as PDF", "effort": "One renderer and its layout rules"}
PLANNED_TASKS = 4


class CheckTextTest(ProjectTestCase):
    def problems(self, texts):
        result = self.run_estimate("check-text", json.dumps(texts))
        self.assertEqual(EXIT_OK, result.code, result.err)
        return result.json["problems"]

    def test_clean_texts_have_no_problems(self):
        self.assertEqual([], self.problems(CLEAN))

    def test_either_text_alone_is_checked(self):
        self.assertEqual([], self.problems({"description": CLEAN["description"]}))
        self.assertEqual([], self.problems({"effort": CLEAN["effort"]}))

    def test_a_text_of_exactly_the_limit_passes_and_one_more_character_fails(self):
        self.assertEqual([], self.problems({"description": "x" * TEXT_LIMIT, "effort": "x" * TEXT_LIMIT}))
        self.assertEqual(["description has 301 characters (at most 300)"],
                         self.problems({"description": "x" * (TEXT_LIMIT + 1)}))
        self.assertEqual(["effort has 301 characters (at most 300)"], self.problems({"effort": "x" * (TEXT_LIMIT + 1)}))

    def test_each_word_of_the_effort_with_a_digit_is_listed_once_in_order(self):
        self.assertEqual(["effort has digits: e2e, A4/A3, 10x"],
                         self.problems({"effort": "Wide e2e suite over A4/A3 pages, e2e again, 10x the cases"}))

    def test_digits_in_the_description_are_allowed(self):
        self.assertEqual([], self.problems({"description": "Phase 2 of the e2e work"}))

    def test_a_blank_text_is_empty(self):
        self.assertEqual(["description is empty"], self.problems({"description": ""}))
        self.assertEqual(["effort is empty"], self.problems({"effort": "  "}))

    def test_several_problems_come_in_order(self):
        self.assertEqual(["description has 301 characters (at most 300)", "effort has 303 characters (at most 300)",
                          "effort has digits: 1x"],
                         self.problems({"effort": "1x " + "y" * TEXT_LIMIT, "description": "x" * (TEXT_LIMIT + 1)}))
        self.assertEqual(["effort has digits: v2", "description is empty"], self.problems({"description": " ", "effort": "v2"}))

    def test_the_texts_can_come_from_stdin(self):
        result = self.run_estimate("check-text", "-", stdin=json.dumps({"effort": "Step 3"}))
        self.assertEqual(EXIT_OK, result.code, result.err)
        self.assertEqual({"problems": ["effort has digits: 3"]}, result.json)

    def test_bad_texts_are_a_bad_argument(self):
        cases = (("{effort", "texts is not JSON"),
                 (json.dumps(["x"]), 'texts must be {"description"?, "effort"?}'),
                 (json.dumps({}), "texts need description or effort"),
                 (json.dumps({"name": "A", "effort": "x"}), "unknown field 'name'"),
                 (json.dumps({"description": 3}), "description must be a string"),
                 (json.dumps({"effort": None}), "effort must be a string"))
        for text, fragment in cases:
            with self.subTest(text=text):
                self.assert_error(self.run_estimate("check-text", text), EXIT_BAD_ARGUMENTS, fragment)


class SaveTextTest(ProjectTestCase):
    def assert_refused(self, entry, message):
        self.assert_error(self.run_estimate("save", json.dumps(entry)), EXIT_BAD_ARGUMENTS, message)
        self.assertFalse(os.path.exists(self.path(DATA_FOLDER)))

    def test_a_description_over_the_limit_is_refused_and_nothing_is_written(self):
        self.assert_refused({"name": "A", "description": "x" * (TEXT_LIMIT + 1), "effort": "x"},
                            "description has 301 characters (at most 300)")

    def test_an_effort_over_the_limit_is_refused_and_nothing_is_written(self):
        self.assert_refused({"name": "A", "description": "x", "effort": "x" * (TEXT_LIMIT + 1)},
                            "effort has 301 characters (at most 300)")

    def test_an_effort_with_digits_is_refused_and_nothing_is_written(self):
        self.assert_refused({"name": "A", "description": "x", "effort": "A4/A3 e2e"}, "effort has digits: A4/A3, e2e")

    def test_every_problem_of_the_entry_is_named(self):
        self.assert_refused({"name": "A", "description": "x" * (TEXT_LIMIT + 1), "effort": "v2"},
                            "description has 301 characters (at most 300); effort has digits: v2")

    def test_an_entry_without_texts_is_saved_even_when_the_stored_effort_has_digits(self):
        self.seed(document(new_feature("A", effort="An e2e suite")))
        result = self.run_estimate("save", json.dumps({"name": "A", "plan": {"tasks": PLANNED_TASKS}}))
        self.assertEqual(EXIT_OK, result.code, result.err)
        self.assertEqual(("An e2e suite", PLANNED_TASKS), (self.feature("A")["effort"], self.feature("A")["plan"]["tasks"]))


if __name__ == "__main__":
    unittest.main()
