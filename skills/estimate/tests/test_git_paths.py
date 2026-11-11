"""`GitHistory.changed_paths(hashes)` and `touching(paths)` on a real repository: the paths of a commit are read from NUL-delimited
output, so a name with a space or a quote still matches and both sides of a rename are listed; both hand git their arguments in
chunks, merging the answers."""
import os
import unittest
from unittest import mock

from estimation import git_history
from estimation.environment import run_process
from estimation.git_history import GitHistory
from tests.support import ProjectTestCase

QUOTED = 'docs/the "odd" doc.md'
CHUNK = 2


class GitPathsTest(ProjectTestCase):
    def history(self, run=run_process):
        return GitHistory(self.root, run)

    def test_paths_git_would_quote_are_listed_as_they_are(self):
        commit_hash = self.commit("odd names", {QUOTED: "a\n", "docs/with space.md": "b\n", "docs/naïve.md": "c\n"})
        self.assertEqual({commit_hash: {QUOTED, "docs/with space.md", "docs/naïve.md"}}, self.history().changed_paths([commit_hash]))

    def test_both_sides_of_a_rename_are_listed(self):
        body = "".join("line %d\n" % number for number in range(20))
        self.commit("add", {"docs/old name.md": body})
        os.rename(self.path("docs/old name.md"), self.path("docs/new name.md"))
        moved = self.commit("move", {"docs/new name.md": body + "extra\n"})
        self.assertEqual({moved: {"docs/old name.md", "docs/new name.md"}}, self.history().changed_paths([moved]))

    def test_a_commit_git_does_not_hold_is_left_out_and_no_hashes_make_no_call(self):
        calls = []
        history = self.history(lambda argv, cwd: calls.append(argv) or run_process(argv, cwd))
        self.assertEqual({}, history.changed_paths([]))
        self.assertEqual([], calls)
        self.assertEqual({}, history.changed_paths(["0123456789abcdef0123456789abcdef01234567"]))

    def test_hashes_and_paths_go_to_git_in_chunks_and_the_answers_are_merged(self):
        hashes = [self.commit("c%d" % number, {"f%d.txt" % number: "x\n"}) for number in range(5)]
        calls = []
        history = self.history(lambda argv, cwd: calls.append(argv) or run_process(argv, cwd))
        with mock.patch.object(git_history, "GIT_ARGUMENTS_PER_CALL", CHUNK):
            changed = history.changed_paths(hashes)
            calls.clear()
            touching = history.touching(["f%d.txt" % number for number in range(5)])
        self.assertEqual({commit_hash: {"f%d.txt" % number} for number, commit_hash in enumerate(hashes)}, changed)
        self.assertEqual(set(hashes), touching)
        self.assertEqual(3, len([argv for argv in calls if "log" in argv]))


if __name__ == "__main__":
    unittest.main()
