"""The file headers of an `apply_patch` patch, shared by the Codex and OpenCode readers."""
import unittest

from sessionlog.patch_headers import opens_a_patch, patch_paths


class PatchPathsTest(unittest.TestCase):
    def test_add_update_and_move_headers_give_their_paths_in_order_as_often_as_named(self):
        patch = "*** Begin Patch\n*** Add File: /a.md\n+x\n*** Update File: /b.py\n*** Move to: /c.py\n*** Update File: /a.md\n*** End Patch"
        self.assertEqual(("/a.md", "/b.py", "/c.py", "/a.md"), patch_paths(patch))

    def test_a_delete_header_and_a_body_line_that_looks_like_a_header_give_nothing(self):
        patch = "*** Begin Patch\n*** Delete File: /gone.md\n+*** Add File: /fake.md\n+ *** Update File: /fake.py\n  *** Move to: /fake\n*** End Patch"
        self.assertEqual((), patch_paths(patch))

    def test_trailing_blank_space_of_a_header_is_not_part_of_its_path(self):
        self.assertEqual(("/a b.md",), patch_paths("*** Add File: /a b.md \t\r\n+x"))

    def test_anything_but_text_names_nothing(self):
        for patch in (None, 5, ["*** Add File: /a.md"], b"*** Add File: /a.md"):
            with self.subTest(patch=patch):
                self.assertEqual((), patch_paths(patch))


class OpensAPatchTest(unittest.TestCase):
    def test_text_that_opens_with_begin_patch_after_blank_space_is_a_patch(self):
        for text in ("*** Begin Patch\n", "  \n*** Begin Patch\n*** Add File: /a"):
            with self.subTest(text=text):
                self.assertTrue(opens_a_patch(text))

    def test_other_text_and_anything_but_text_is_none(self):
        for text in ("Docs\n*** Begin Patch\n", "*** Add File: /a.md", "", None, 5, ["*** Begin Patch"]):
            with self.subTest(text=text):
                self.assertFalse(opens_a_patch(text))


if __name__ == "__main__":
    unittest.main()
