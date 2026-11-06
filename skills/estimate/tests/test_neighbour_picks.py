"""`pick(pool, scale, name)`: the features a feature is compared with — up to three from every value of the scale that holds a pool
feature, drawn at random but repeatably from the feature's name — shown to the judge without their sizes, and the `missing` lines
naming every value with fewer than three, every empty value inside the held range, and every pick without an effort."""
import unittest

from estimation.neighbours import pick
from tests.support import DEFAULT_SCALE

NAME = "Feature"


def item(name, sp, **fields):
    """A pool item: a feature of size `sp` with every field a pick shows."""
    return {"name": name, "id": None, "description": "About " + name, "effort": "Work of " + name, "sp": sp, "tasks": 4,
            "estimatedHours": 6.5, **fields}


def seen(feature):
    """A pool item as the judge sees it: without its size."""
    return {key: value for key, value in feature.items() if key != "sp"}


def full(sp, count):
    return [item("F%d at %s" % (number, sp), sp) for number in range(count)]


class GroupTest(unittest.TestCase):
    def test_every_size_that_holds_a_feature_gives_up_to_three_picks_in_scale_order(self):
        pool = full(1, 1) + full(5, 5) + full(13, 3) + full(40, 2)
        groups = pick(pool, DEFAULT_SCALE, NAME)["groups"]
        self.assertEqual([1, 5, 13, 40], [group["sp"] for group in groups])
        self.assertEqual([1, 3, 3, 2], [len(group["names"]) for group in groups])
        self.assertEqual(3, len(set(groups[1]["names"])))
        self.assertTrue(set(groups[1]["names"]) <= {feature["name"] for feature in full(5, 5)})

    def test_the_judge_sees_every_pick_in_name_order_without_its_size(self):
        pool = [{**item("Zeta", 3, id="Z1"), "storyPoints": 3, "plan": {}}, item("Mid", 5), item("Alpha", 8)]
        result = pick(pool, DEFAULT_SCALE, NAME)
        self.assertEqual([seen(item("Alpha", 8)), seen(item("Mid", 5)), seen(item("Zeta", 3, id="Z1"))], result["picks"])
        self.assertEqual([{"sp": 3, "names": ["Zeta"]}, {"sp": 5, "names": ["Mid"]}, {"sp": 8, "names": ["Alpha"]}], result["groups"])

    def test_the_feature_itself_is_never_picked_and_never_fills_a_size(self):
        pool = [item("Small", 2), item(NAME, 3), item("Same", 5)]
        result = pick(pool, DEFAULT_SCALE, NAME)
        self.assertEqual([2, 5], [group["sp"] for group in result["groups"]])
        self.assertNotIn(NAME, [shown["name"] for shown in result["picks"]])


class RepeatableTest(unittest.TestCase):
    def setUp(self):
        self.pool = full(3, 8) + full(5, 8) + full(8, 8)

    def test_the_same_name_gets_the_same_picks_from_a_pool_in_any_order(self):
        self.assertEqual(pick(self.pool, DEFAULT_SCALE, NAME), pick(self.pool[::-1], DEFAULT_SCALE, NAME))

    def test_another_name_gets_other_picks(self):
        self.assertNotEqual(pick(self.pool, DEFAULT_SCALE, "Feature A")["picks"], pick(self.pool, DEFAULT_SCALE, "Feature B")["picks"])


class MissingTest(unittest.TestCase):
    def test_short_sizes_and_empty_sizes_inside_the_held_range_are_named(self):
        pool = full(2, 1) + full(5, 3) + full(20, 2)
        self.assertEqual(["only 1 reference at 2 SP", "no reference at 3 SP", "no reference at 8 SP", "no reference at 13 SP",
                          "only 2 references at 20 SP"], pick(pool, DEFAULT_SCALE, NAME)["missing"])

    def test_an_empty_pool_gives_no_picks_and_says_so(self):
        self.assertEqual({"picks": [], "groups": [], "missing": ["no reference to compare with"]},
                         pick([item(NAME, 5)], DEFAULT_SCALE, NAME))

    def test_a_pick_without_an_effort_is_named(self):
        pool = [item("Saved before efforts", 5, effort=None), item("Same", 5), item("Other", 5)]
        self.assertIn("no effort for 'Saved before efforts'", pick(pool, DEFAULT_SCALE, NAME)["missing"])


if __name__ == "__main__":
    unittest.main()
