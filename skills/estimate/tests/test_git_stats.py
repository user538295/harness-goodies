"""`git-stats <features-json | ->` (C3) on a real repository: per feature, the commits that change one of its docs or name one of its
task IDs (the boundary rule), oldest first, with their numstat line counts (binary files count 0). A task ID that more than one
feature lists finds no commit and is named on stderr."""
import json
import os
import shutil
import tempfile
import unittest

from tests.support import EXIT_BAD_ARGUMENTS, EXIT_OK, EXIT_RUNTIME_ERROR, ProjectTestCase

BINARY_BYTES = b"\x00\x01\x02logo\x00"


def feature(name="X", docs=(), task_ids=()):
    return {"name": name, "docs": list(docs), "taskIds": list(task_ids)}


class GitStatsTest(ProjectTestCase):
    def run_stats(self, *features, cwd=None):
        result = self.run_estimate("git-stats", json.dumps({"features": list(features)}), cwd=cwd)
        self.assertEqual(EXIT_OK, result.code, result.err)
        return result

    def stats(self, *features, cwd=None):
        return {entry["name"]: entry for entry in self.run_stats(*features, cwd=cwd).json["features"]}

    def test_doc_and_task_id_commits_with_their_lines(self):
        c1 = self.commit("add brief", {"docs/x-brief.md": "a\nb\nc\n", "src/ten.py": "ten\n"})
        self.commit("BE-10 wider id", {"src/wide.py": "1\n"})
        os.makedirs(self.path("assets"))
        with open(self.path("assets/logo.bin"), "wb") as handle:
            handle.write(BINARY_BYTES)
        c3 = self.commit("(BE-1) store the set", {"src/store.py": "x\ny\n"})
        c4 = self.commit("docs", {"docs/x-brief.md": "a\nB\nc\n"})
        os.remove(self.path("src/ten.py"))
        c5 = self.commit("Finish BE-1.")
        stats = self.stats(feature(docs=["docs/x-brief.md"], task_ids=["BE-1"]))
        # c1 +3 brief +1 ten.py (whole commit counts); c3 +2, logo.bin binary 0; c4 +1 -1; c5 -1.
        self.assertEqual({"X": {"name": "X", "commits": [c1, c3, c4, c5], "count": 4, "linesAdded": 7, "linesRemoved": 2}}, stats)

    def test_a_task_id_is_matched_only_on_its_boundaries(self):
        expected = {}
        for message, matches in (("BE-1 start", True), ("end BE-1", True), ("BE-1.", True), ("(BE-1)", True),
                                 ("BE-1, BE-2", True), ("BE-1: done", True), ("BE-10", False), ("BE-1.2", False),
                                 ("xBE-1", False), ("v.BE-1", False), ("BE-1-fix", False), ("pre-BE-1", False),
                                 ("BE-1_x", False)):
            expected[self.commit(message)] = matches
        matched = set(self.stats(feature(task_ids=["BE-1"]))["X"]["commits"])
        self.assertEqual({commit for commit, matches in expected.items() if matches}, matched)

    def test_a_task_id_two_features_list_finds_no_commit_and_is_named(self):
        export_doc = self.commit("BE-1 export", {"docs/export-tasks.md": "- [x] BE-1\n"})
        import_doc = self.commit("BE-1 import", {"docs/import-tasks.md": "- [x] BE-1\n"})
        own = self.commit("EX-7 export only", {"src/export.py": "e\n"})
        result = self.run_stats(feature("Export", ["docs/export-tasks.md"], ["BE-1", "EX-7"]),
                                feature("Import", ["docs/import-tasks.md"], ["BE-1", "BE-2"]))
        self.assertEqual({"Export": [export_doc, own], "Import": [import_doc]},
                         {entry["name"]: entry["commits"] for entry in result.json["features"]})
        self.assertEqual("estimate: task IDs more than one feature lists find no commits: BE-1\n", result.err)

    def test_a_doc_is_found_from_a_subfolder(self):
        brief = self.commit("add brief", {"docs/x-brief.md": "a\n"})
        self.commit("other", {"src/a.py": "a\n"})
        self.assertEqual([brief], self.stats(feature(docs=["x-brief.md"]), cwd=self.path("docs"))["X"]["commits"])

    def test_nothing_matched_gives_zeros(self):
        self.commit("other", {"src/a.py": "a\n"})
        self.assertEqual({"name": "X", "commits": [], "count": 0, "linesAdded": 0, "linesRemoved": 0},
                         self.stats(feature(docs=["docs/none.md"], task_ids=["BE-1"]))["X"])

    def test_a_repository_without_commits_matches_nothing(self):
        self.assertEqual(0, self.stats(feature(task_ids=["BE-1"]))["X"]["count"])

    def test_the_features_can_come_from_stdin(self):
        brief = self.commit("add brief", {"docs/x-brief.md": "a\n"})
        result = self.run_estimate("git-stats", "-", stdin=json.dumps({"features": [feature(docs=["docs/x-brief.md"])]}))
        self.assertEqual(EXIT_OK, result.code, result.err)
        self.assertEqual([brief], result.json["features"][0]["commits"])

    def test_features_without_names_are_a_bad_argument(self):
        self.assert_error(self.run_estimate("git-stats", json.dumps({"features": [{"docs": ["a.md"]}]})), EXIT_BAD_ARGUMENTS,
                          "each feature needs a name")

    def test_outside_a_git_repository_it_is_a_runtime_error(self):
        folder = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        result = self.run_estimate("git-stats", json.dumps({"features": [feature(task_ids=["BE-1"])]}), cwd=folder)
        self.assert_error(result, EXIT_RUNTIME_ERROR, "git")


if __name__ == "__main__":
    unittest.main()
