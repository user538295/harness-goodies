"""`GitHistory.changes(hashes)` on a real repository: each listed commit's author time, subject and per-file numstat (binary files
count 0), a renamed path reported under its new path, commits git does not hold left out, and no git call for no hashes."""
import os
import unittest
from datetime import datetime

from estimation.environment import run_process
from estimation.git_history import CommitChanges, FileChange, GitHistory
from tests.support import ProjectTestCase

UNKNOWN_HASH = "0123456789abcdef0123456789abcdef01234567"


class GitChangesTest(ProjectTestCase):
    def history(self):
        return GitHistory(self.root, run_process)

    def test_per_file_numstat_with_subject_and_author_time(self):
        first = self.commit("feat: add app\n\nLonger body.", {"src/app.py": "a\nb\n", "README.md": "x\n"},
                            when="2026-10-02T09:30:00+02:00")
        second = self.commit("fix: change app", {"src/app.py": "a\nc\nd\n"})
        changes = self.history().changes([second, first])
        self.assertEqual(CommitChanges(first, datetime.fromisoformat("2026-10-02T09:30:00+02:00"), "feat: add app",
                                       (FileChange("README.md", 1, 0), FileChange("src/app.py", 2, 0))), changes[first])
        self.assertEqual((FileChange("src/app.py", 2, 1),), changes[second].files)

    def test_binary_files_count_zero(self):
        commit_hash = self.commit("add logo", {"logo.bin": "\0\1\2binary\0"})
        self.assertEqual((FileChange("logo.bin", 0, 0),), self.history().changes([commit_hash])[commit_hash].files)

    def test_a_renamed_path_is_reported_under_its_new_path(self):
        body = "".join("line %d\n" % number for number in range(20))
        self.commit("add", {"pkg/old/module.py": body, "top.py": body})
        os.makedirs(self.path("pkg/new"))
        os.rename(self.path("pkg/old/module.py"), self.path("pkg/new/module.py"))
        os.rename(self.path("top.py"), self.path("renamed.py"))
        moved = self.commit("move", {"pkg/new/module.py": body + "extra\n"})
        files = self.history().changes([moved])[moved].files
        self.assertEqual({FileChange("pkg/new/module.py", 1, 0), FileChange("renamed.py", 0, 0)}, set(files))

    def test_a_commit_git_does_not_hold_is_absent(self):
        known = self.commit("one", {"a.txt": "a\n"})
        self.assertEqual([known], list(self.history().changes([UNKNOWN_HASH, known])))

    def test_no_hashes_give_no_changes(self):
        self.assertEqual({}, self.history().changes([]))

    def test_an_empty_commit_has_no_files(self):
        empty = self.commit("empty")
        self.assertEqual((), self.history().changes([empty])[empty].files)


if __name__ == "__main__":
    unittest.main()
