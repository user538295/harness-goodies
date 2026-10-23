"""The path rules of the OMP session store (S42): where a file sits in its family, the root id of a stem and the stem of a parent path."""
import unittest

from sessionlog.omp_layout import ARCHIVED_SUFFIX, SESSION_SUFFIX, Place, place_of, root_id_of_stem, stem_of_path

STEM = "2026-10-13T08-42-00-000Z_01a10000-0000-7000-8000-000000000001"


class PlaceTest(unittest.TestCase):
    def test_a_file_in_the_project_folder_is_the_root_of_its_own_family(self):
        self.assertEqual(Place(STEM, ("-proj", STEM), True), place_of(["-proj", STEM + SESSION_SUFFIX], SESSION_SUFFIX))

    def test_a_file_straight_in_the_store_folder_is_a_root_too(self):
        self.assertEqual(Place(STEM, (STEM,), True), place_of([STEM + SESSION_SUFFIX], SESSION_SUFFIX))

    def test_a_child_and_a_grandchild_belong_to_the_family_of_the_folder_below_the_project_folder(self):
        self.assertEqual(Place(STEM, ("-proj", STEM, "Task"), False), place_of(["-proj", STEM, "Task" + SESSION_SUFFIX], SESSION_SUFFIX))
        self.assertEqual(Place(STEM, ("-proj", STEM, "Task", "Sub"), False), place_of(["-proj", STEM, "Task", "Sub" + SESSION_SUFFIX], SESSION_SUFFIX))

    def test_the_stem_is_the_last_part_and_the_suffix_is_cut_whole(self):
        place = place_of(["-proj", STEM, "a.jsonl.b" + ARCHIVED_SUFFIX], ARCHIVED_SUFFIX)
        self.assertEqual("a.jsonl.b", place.stem)

    def test_the_two_copies_gc_leaves_share_their_identity_and_other_files_do_not(self):
        plain = place_of(["-proj", STEM, "Task" + SESSION_SUFFIX], SESSION_SUFFIX)
        archived = place_of(["-proj", STEM, "Task" + ARCHIVED_SUFFIX], ARCHIVED_SUFFIX)
        deeper = place_of(["-proj", STEM, "Task", "Task" + SESSION_SUFFIX], SESSION_SUFFIX)
        elsewhere = place_of(["-other", STEM, "Task" + SESSION_SUFFIX], SESSION_SUFFIX)
        self.assertEqual(plain.identity, archived.identity)
        self.assertEqual(3, len({plain.identity, deeper.identity, elsewhere.identity}))

    def test_the_root_session_is_one_file_per_session_whatever_its_stem(self):
        self.assertEqual(place_of(["-proj", "ts1_x" + SESSION_SUFFIX], SESSION_SUFFIX).identity, place_of(["-proj", "ts2_x" + ARCHIVED_SUFFIX], ARCHIVED_SUFFIX).identity)


class RootIdTest(unittest.TestCase):
    def test_the_id_is_what_follows_the_first_underscore(self):
        for stem, expected in ((STEM, "01a10000-0000-7000-8000-000000000001"), ("ts_a_b", "a_b"), ("plain-name", "plain-name"), ("stem_", ""), ("_id", "id")):
            with self.subTest(stem=stem):
                self.assertEqual(expected, root_id_of_stem(stem))


class StemOfPathTest(unittest.TestCase):
    def test_the_stem_is_the_file_name_without_its_suffix_whatever_the_separators(self):
        for path, expected in (("/home/u/.omp/agent/sessions/-p/%s.jsonl" % STEM, STEM), ("C:\\Users\\u\\.omp\\agent\\sessions\\-p\\Name.jsonl", "Name"),
                               ("archive/sessions/-p/Name.jsonl.gz", "Name"), ("/x/y/no-suffix", "no-suffix"), ("bare.jsonl", "bare"), ("/x/dir/", ""),
                               ("a/b\\c.jsonl", "c"), ("/x/.jsonl", "")):
            with self.subTest(path=path):
                self.assertEqual(expected, stem_of_path(path))


if __name__ == "__main__":
    unittest.main()
