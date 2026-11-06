"""`place(scale, picked, verdicts, pick_verdicts, hours)`: the size where a feature fits among its picks, from two judge calls — the
feature against each pick, and each pick against the feature — read as the scale value that contradicts the fewest verdicts; a tie
goes to the size whose picks' planned hours are nearest to the feature's, else up."""
import unittest

from estimation.errors import ArgumentError
from estimation.neighbours import pick
from estimation.placement import place
from tests.support import DEFAULT_SCALE

NAME = "Feature"
FLIP = {"bigger": "smaller", "same": "same", "smaller": "bigger"}


def item(name, sp, hours=None):
    return {"name": name, "id": None, "description": "About " + name, "effort": "Work of " + name, "sp": sp, "tasks": 4,
            "estimatedHours": hours}


def placed(pool, verdicts, pick_verdicts=None, hours=None):
    """Places NAME among `pool`; the second call agrees with `verdicts` unless `pick_verdicts` says otherwise."""
    picked = pick(pool, DEFAULT_SCALE, NAME)
    pick_verdicts = {name: FLIP[verdict] for name, verdict in verdicts.items()} if pick_verdicts is None else pick_verdicts
    return place(DEFAULT_SCALE, picked, verdicts, pick_verdicts, hours)


class FitTest(unittest.TestCase):
    def test_the_size_whose_picks_it_matches(self):
        result = placed([item("S", 5), item("M", 8), item("L", 13)], {"S": "bigger", "M": "same", "L": "smaller"})
        self.assertEqual((8, None), (result["sp"], result["tie"]))

    def test_a_size_without_picks_between_two_held_sizes_can_be_the_fit(self):
        self.assertEqual(13, placed([item("M", 8), item("XL", 20)], {"M": "bigger", "XL": "smaller"})["sp"])

    def test_beyond_the_smallest_or_biggest_picks_it_goes_one_step_further(self):
        pool = [item("XS", 1), item("XXL", 40)]
        self.assertEqual(100, placed(pool, {"XS": "bigger", "XXL": "bigger"})["sp"])
        self.assertEqual(0.5, placed(pool, {"XS": "smaller", "XXL": "smaller"})["sp"])

    def test_with_conflicting_verdicts_the_size_contradicting_the_fewest_wins(self):
        pool = [item(name, 3) for name in ("A3", "B3", "C3")] + [item(name, 5) for name in ("A5", "B5", "C5")] + \
               [item(name, 8) for name in ("A8", "B8", "C8")]
        verdicts = {"A3": "bigger", "B3": "same", "C3": "bigger", "A5": "same", "B5": "same", "C5": "smaller",
                    "A8": "smaller", "B8": "smaller", "C8": "same"}
        self.assertEqual(5, placed(pool, verdicts)["sp"])

    def test_the_second_call_is_read_from_the_picks_side(self):
        pool = [item("S", 5), item("M", 8), item("L", 13)]
        result = placed(pool, {"S": "bigger", "M": "same", "L": "smaller"}, {"S": "smaller", "M": "same", "L": "bigger"})
        self.assertEqual(8, result["sp"])

    def test_without_picks_there_is_no_size(self):
        self.assertEqual(None, placed([], {}, {})["sp"])


class TieTest(unittest.TestCase):
    def test_between_two_sizes_without_hours_it_rounds_up(self):
        result = placed([item("S", 5, 30), item("M", 8, 50)], {"S": "bigger", "M": "smaller"})
        self.assertEqual((8, {"between": [5, 8], "by": "round up"}), (result["sp"], result["tie"]))

    def test_between_two_sizes_with_hours_the_size_with_the_nearer_planned_hours_wins(self):
        pool = [item("S", 5, 30), item("M", 8, 50)]
        self.assertEqual((5, {"between": [5, 8], "by": "hours"}),
                         tuple(placed(pool, {"S": "bigger", "M": "smaller"}, hours=33)[key] for key in ("sp", "tie")))
        self.assertEqual(8, placed(pool, {"S": "bigger", "M": "smaller"}, hours=45)["sp"])

    def test_with_hours_but_a_tied_size_without_any_rounds_up(self):
        result = placed([item("S", 5), item("M", 8, 50)], {"S": "bigger", "M": "smaller"}, hours=33)
        self.assertEqual((8, "round up"), (result["sp"], result["tie"]["by"]))


class ReportTest(unittest.TestCase):
    def test_each_verdict_names_the_pick_and_its_size_and_shows_disagreeing_calls(self):
        pool = [item("S", 5), {**item("M", 8), "id": "M-1"}]
        result = placed(pool, {"S": "bigger", "M": "same"}, {"S": "smaller", "M": "bigger"})
        self.assertEqual("bigger than S (5), same as / smaller than M-1 (8)", result["report"])
        self.assertEqual([{"name": "S", "id": None, "sp": 5, "verdict": "bigger", "pickVerdict": "smaller"},
                          {"name": "M", "id": "M-1", "sp": 8, "verdict": "same", "pickVerdict": "bigger"}], result["verdicts"])

    def test_verdicts_that_skip_a_pick_name_another_or_use_another_word_are_refused(self):
        pool = [item("S", 5), item("M", 8)]
        for verdicts, fragment in (({"S": "bigger"}, "missing 'M'"), ({"S": "bigger", "M": "same", "X": "same"}, "unknown 'X'"),
                                   ({"S": "bigger", "M": "huge"}, "'huge' for 'M'")):
            with self.subTest(verdicts=verdicts):
                with self.assertRaises(ArgumentError) as raised:
                    placed(pool, verdicts, {"S": "smaller", "M": "same"})
                self.assertIn(fragment, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
