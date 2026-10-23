"""Private folders and link refusal shared by the writers (BE-34; S50, S62)."""
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sessionlog import safe_paths
from sessionlog.safe_paths import (FOLDER_LINK_REASON, FOLDER_MODE, LINK_REASON, is_link, is_real_folder, make_folders, make_real_folders,
                                   require_no_link)

CAN_LINK = hasattr(os, "symlink") and os.name != "nt"
POSIX_ONLY = unittest.skipIf(os.name == "nt", "file modes are POSIX")


class SafePathsTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

    def path(self, *names):
        return os.path.join(self.root, *names)


class WordingTests(unittest.TestCase):
    def test_reasons_are_the_words_status_prints(self):
        self.assertEqual("is a symbolic link or junction, so it is left alone and nothing is written through it", LINK_REASON)
        self.assertEqual("its folder is a symbolic link or junction, so it is left alone and nothing is written through it", FOLDER_LINK_REASON)
        self.assertEqual(0o700, FOLDER_MODE)


class LinkTests(SafePathsTestCase):
    @unittest.skipUnless(CAN_LINK, "needs symlinks")
    def test_a_symlink_to_a_folder_a_file_or_nothing_is_a_link_and_a_real_folder_or_file_is_not(self):
        folder, file = self.path("folder"), self.path("file")
        os.mkdir(folder)
        Path(file).write_text("x")
        for name, target in (("to-folder", folder), ("to-file", file), ("dangling", self.path("missing"))):
            os.symlink(target, self.path(name))
            self.assertTrue(is_link(self.path(name)), name)
            self.assertFalse(is_real_folder(self.path(name)), name)
        self.assertEqual([False, False, False], [is_link(self.path(name)) for name in ("folder", "file", "missing")])
        self.assertTrue(is_real_folder(folder))
        self.assertFalse(is_real_folder(file))

    def test_a_junction_is_a_link_and_no_real_folder(self):
        folder = self.path("junction")
        os.mkdir(folder)
        with mock.patch.object(safe_paths.os.path, "isjunction", side_effect=lambda path: path == folder):
            self.assertTrue(is_link(folder))
            self.assertFalse(is_real_folder(folder))
            self.assertTrue(is_real_folder(self.root))
            with self.assertRaises(OSError) as raised:
                require_no_link(folder)
        self.assertEqual("%s %s" % (folder, LINK_REASON), str(raised.exception))

    def test_require_no_link_accepts_a_missing_path_and_a_real_file(self):
        require_no_link(self.path("missing"))
        Path(self.path("file")).write_text("x")
        require_no_link(self.path("file"))


class MakeFoldersTests(SafePathsTestCase):
    @POSIX_ONLY
    def test_every_new_level_is_0700_under_umask_022_and_an_existing_folder_keeps_its_mode(self):
        self.addCleanup(os.umask, os.umask(0o022))
        os.chmod(self.root, 0o755)
        make_folders(self.path("a", "b", "c"))
        for names in (("a",), ("a", "b"), ("a", "b", "c")):
            self.assertEqual(0o700, stat.S_IMODE(os.stat(self.path(*names)).st_mode), names)
        self.assertEqual(0o755, stat.S_IMODE(os.stat(self.root).st_mode))
        os.chmod(self.path("a"), 0o750)
        make_folders(self.path("a", "b", "c"))
        make_folders(self.path("a", "d"))
        self.assertEqual((0o750, 0o700), (stat.S_IMODE(os.stat(self.path("a")).st_mode), stat.S_IMODE(os.stat(self.path("a", "d")).st_mode)))

    def test_a_relative_path_is_made_below_the_current_folder(self):
        previous = os.getcwd()
        self.addCleanup(os.chdir, previous)
        os.chdir(self.root)
        make_folders(os.path.join("one", "two"))
        self.assertTrue(os.path.isdir(self.path("one", "two")))

    def test_a_path_object_is_accepted(self):
        make_folders(Path(self.path("x", "y")))
        self.assertTrue(os.path.isdir(self.path("x", "y")))

    def test_a_file_in_the_way_raises_and_so_does_a_file_as_a_parent(self):
        Path(self.path("file")).write_text("x")
        with self.assertRaises(OSError):
            make_folders(self.path("file"))
        with self.assertRaises(OSError):
            make_folders(self.path("file", "inside"))

    @unittest.skipUnless(CAN_LINK, "needs symlinks")
    def test_a_dangling_link_in_the_way_raises_and_a_link_to_a_folder_is_kept_as_it_is(self):
        os.symlink(self.path("missing"), self.path("dangling"))
        with self.assertRaises(OSError):
            make_folders(self.path("dangling"))
        self.assertFalse(os.path.exists(self.path("missing")))
        os.mkdir(self.path("real"))
        os.symlink(self.path("real"), self.path("linked"))
        make_folders(self.path("linked"))
        self.assertTrue(os.path.islink(self.path("linked")))

    @unittest.skipUnless(CAN_LINK, "needs symlinks")
    def test_make_real_folders_creates_a_folder_and_leaves_any_link_alone_creating_nothing(self):
        self.assertTrue(make_real_folders(self.path("new", "deep")))
        self.assertTrue(os.path.isdir(self.path("new", "deep")))
        self.assertTrue(make_real_folders(self.path("new", "deep")))
        os.symlink(self.path("missing"), self.path("dangling"))
        os.mkdir(self.path("real"))
        os.symlink(self.path("real"), self.path("linked"))
        self.assertEqual([False, False], [make_real_folders(self.path(name)) for name in ("dangling", "linked")])
        self.assertFalse(os.path.exists(self.path("missing")))

    def test_make_real_folders_refuses_a_junction_without_creating_anything(self):
        folder = self.path("junction")
        os.mkdir(folder)
        with mock.patch.object(safe_paths.os.path, "isjunction", side_effect=lambda path: path == folder):
            self.assertFalse(make_real_folders(folder))

    def test_two_runs_creating_the_same_folder_both_succeed(self):
        real_isdir = os.path.isdir
        target = self.path("raced")
        calls = []

        def isdir_after_the_other_run_created_it(path):
            if path == target and not calls:
                calls.append(path)
                os.mkdir(target)
                return False
            return real_isdir(path)

        with mock.patch.object(safe_paths.os.path, "isdir", isdir_after_the_other_run_created_it):
            make_folders(target)
        self.assertTrue(real_isdir(target))


if __name__ == "__main__":
    unittest.main()
