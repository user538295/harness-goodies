"""`git-stats [--doc <path>]... [--task-id <id>]...` (C3) on a real repository: commits that change a doc or name a task ID (the
boundary rule), oldest first, with their numstat line counts (binary files count 0) and author dates."""
import os
import shutil
import tempfile
import unittest

from tests.support import EXIT_BAD_ARGUMENTS, EXIT_OK, EXIT_RUNTIME_ERROR, ProjectTestCase

BINARY_BYTES = b"\x00\x01\x02logo\x00"


class GitStatsTest(ProjectTestCase):
    def stats(self, *argv, cwd=None):
        result = self.run_estimate("git-stats", *argv, cwd=cwd)
        self.assertEqual(EXIT_OK, result.code, result.err)
        return result.json

    def test_doc_and_task_id_commits_with_their_lines_and_dates(self):
        c1 = self.commit("add brief", {"docs/x-brief.md": "a\nb\nc\n", "src/ten.py": "ten\n"}, when="2026-09-01T10:00:00+00:00")
        self.commit("BE-10 wider id", {"src/wide.py": "1\n"}, when="2026-09-02T10:00:00+00:00")
        os.makedirs(self.path("assets"))
        with open(self.path("assets/logo.bin"), "wb") as handle:
            handle.write(BINARY_BYTES)
        c3 = self.commit("(BE-1) store the set", {"src/store.py": "x\ny\n"}, when="2026-09-05T10:00:00+00:00")
        c4 = self.commit("docs", {"docs/x-brief.md": "a\nB\nc\n"}, when="2026-09-07T10:00:00+00:00")
        os.remove(self.path("src/ten.py"))
        c5 = self.commit("Finish BE-1.", when="2026-09-08T10:00:00+00:00")
        stats = self.stats("--doc", "docs/x-brief.md", "--task-id", "BE-1")
        # c1 +3 brief +1 ten.py (whole commit counts); c3 +2, logo.bin binary 0; c4 +1 -1; c5 -1.
        self.assertEqual({"commits": [c1, c3, c4, c5], "count": 4, "linesAdded": 7, "linesRemoved": 2,
                          "firstDate": "2026-09-01", "lastDate": "2026-09-08"}, stats)

    def test_a_task_id_is_matched_only_on_its_boundaries(self):
        expected = {}
        for message, matches in (("BE-1 start", True), ("end BE-1", True), ("BE-1.", True), ("(BE-1)", True),
                                 ("BE-1, BE-2", True), ("BE-1: done", True), ("BE-10", False), ("BE-1.2", False),
                                 ("xBE-1", False), ("v.BE-1", False), ("BE-1-fix", False), ("pre-BE-1", False),
                                 ("BE-1_x", False)):
            expected[self.commit(message)] = matches
        matched = set(self.stats("--task-id", "BE-1")["commits"])
        self.assertEqual({commit for commit, matches in expected.items() if matches}, matched)

    def test_a_doc_is_found_from_a_subfolder(self):
        brief = self.commit("add brief", {"docs/x-brief.md": "a\n"})
        self.commit("other", {"src/a.py": "a\n"})
        self.assertEqual([brief], self.stats("--doc", "x-brief.md", cwd=self.path("docs"))["commits"])

    def test_nothing_matched_gives_zeros_and_null_dates(self):
        self.commit("other", {"src/a.py": "a\n"})
        self.assertEqual({"commits": [], "count": 0, "linesAdded": 0, "linesRemoved": 0, "firstDate": None, "lastDate": None},
                         self.stats("--task-id", "BE-1", "--doc", "docs/none.md"))

    def test_a_repository_without_commits_matches_nothing(self):
        self.assertEqual(0, self.stats("--task-id", "BE-1")["count"])

    def test_without_a_doc_or_task_id_it_is_a_bad_argument(self):
        self.assert_error(self.run_estimate("git-stats"), EXIT_BAD_ARGUMENTS, "git-stats needs --doc or --task-id")

    def test_outside_a_git_repository_it_is_a_runtime_error(self):
        folder = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        self.assert_error(self.run_estimate("git-stats", "--task-id", "BE-1", cwd=folder), EXIT_RUNTIME_ERROR, "git")


if __name__ == "__main__":
    unittest.main()
