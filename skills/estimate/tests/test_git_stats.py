"""`git-stats <features-json | ->` (C3) on a real repository: per feature, the commits that change one of its docs (followed
back through moves and copies) plus the full hashes its input `commits` gives (a checker accepted them), oldest first, with
their numstat line counts (binary files count 0), and its `candidates`: the commits whose message (subject or body) names the
feature's id (the boundary rule, in any case) that no feature claims. An id never ties a commit by itself, and task IDs find
nothing. A commit that more than one feature claims counts for none of them and is named on stderr; a given hash that is not a
commit reachable from HEAD is a bad argument."""
import json
import os
import shutil
import tempfile
import unittest

from tests.support import EXIT_BAD_ARGUMENTS, EXIT_OK, EXIT_RUNTIME_ERROR, ProjectTestCase, git

BINARY_BYTES = b"\x00\x01\x02logo\x00"
SHORT_HASH = 8


UNKNOWN_HASH = "0123456789abcdef0123456789abcdef01234567"


def feature(name="X", docs=(), task_ids=(), feature_id=None, commits=None):
    given = {"name": name, "id": feature_id, "docs": list(docs), "taskIds": list(task_ids)}
    return given if commits is None else {**given, "commits": list(commits)}


def candidate(commit_hash, subject):
    return {"hash": commit_hash, "subject": subject}


class GitStatsTest(ProjectTestCase):
    def run_stats(self, *features, cwd=None):
        result = self.run_estimate("git-stats", json.dumps({"features": list(features)}), cwd=cwd)
        self.assertEqual(EXIT_OK, result.code, result.err)
        return result

    def stats(self, *features, cwd=None):
        return {entry["name"]: entry for entry in self.run_stats(*features, cwd=cwd).json["features"]}

    def move(self, source, target, message="move"):
        os.makedirs(os.path.dirname(self.path(target)), exist_ok=True)
        os.rename(self.path(source), self.path(target))
        return self.commit(message)

    def test_doc_commits_and_given_commits_with_their_lines(self):
        c1 = self.commit("add brief", {"docs/x-brief.md": "a\nb\nc\n", "src/ten.py": "ten\n"})
        self.commit("X-10 wider id", {"src/wide.py": "1\n"})
        os.makedirs(self.path("assets"))
        with open(self.path("assets/logo.bin"), "wb") as handle:
            handle.write(BINARY_BYTES)
        c3 = self.commit("store the set", {"src/store.py": "x\ny\n"})
        c4 = self.commit("docs", {"docs/x-brief.md": "a\nB\nc\n"})
        os.remove(self.path("src/ten.py"))
        c5 = self.commit("finish")
        stats = self.stats(feature(docs=["docs/x-brief.md"], feature_id="X-1", commits=[c5, c3]))
        # c1 +3 brief +1 ten.py (whole commit counts); c3 +2, logo.bin binary 0; c4 +1 -1; c5 -1.
        self.assertEqual({"X": {"name": "X", "commits": [c1, c3, c4, c5], "count": 4, "linesAdded": 7, "linesRemoved": 2,
                                "candidates": []}}, stats)

    def test_a_doc_is_followed_back_through_a_move(self):
        created = self.commit("add plan", {"docs/backlog/x-plan.md": "- [ ] T1\n- [ ] T2\n"})
        self.commit("other", {"src/a.py": "a\n"})
        ticked = self.commit("work", {"docs/backlog/x-plan.md": "- [x] T1\n- [ ] T2\n", "src/x.py": "x\n"})
        moved = self.move("docs/backlog/x-plan.md", "docs/completed/x-plan.md")
        self.assertEqual([created, ticked, moved], self.stats(feature(docs=["docs/completed/x-plan.md"]))["X"]["commits"])

    def test_a_doc_copied_to_its_new_folder_and_deleted_later_is_followed(self):
        created = self.commit("add plan", {"docs/backlog/x-plan.md": "- [ ] T1\n- [ ] T2\n- [ ] T3\n"})
        ticked = self.commit("work", {"docs/backlog/x-plan.md": "- [x] T1\n- [x] T2\n- [x] T3\n", "src/x.py": "x\n"})
        copied = self.commit("archive", {"docs/completed/x-plan.md": "- [x] T1\n- [x] T2\n- [x] T3\n"})
        os.remove(self.path("docs/backlog/x-plan.md"))
        self.commit("drop the backlog copy")
        self.assertEqual([created, ticked, copied], self.stats(feature(docs=["docs/completed/x-plan.md"]))["X"]["commits"])

    def test_an_id_anywhere_in_a_message_on_its_boundaries_in_any_case_makes_a_candidate_never_a_commit(self):
        expected = []
        for message, matches in (("feat(A7): x", True), ("fix(a7): y", True), ("merge(E2b A7)!: z", True),
                                 ("docs(A1–A7): moved", True), ("A7 start", True), ("docs: move (A7) to Completed", True),
                                 ("test: a7 e2e", True), ("feat(graph): x\n\nfix(A7): named in the body", True),
                                 ("feat(A70): x", False), ("feat(A7.2): x", False), ("feat(xA7): x", False),
                                 ("feat(v.A7): x", False), ("feat(A7-fix): x", False), ("feat(pre-A7): x", False),
                                 ("feat(A7_x): x", False), ("BA7 start", False)):
            commit_hash = self.commit(message)
            if matches:
                expected.append(candidate(commit_hash, message.split("\n")[0]))
        stats = self.stats(feature(feature_id="A7"))["X"]
        self.assertEqual([], stats["commits"])
        self.assertEqual(expected, stats["candidates"])

    def test_an_id_that_is_also_a_scope_word_proposes_its_commits_only_as_candidates(self):
        brief = self.commit("add brief", {"docs/e2e-brief.md": "e2e\n"})
        scoped = self.commit("test(e2e): cover the search page", {"tests/search.spec.ts": "t\n"})
        task = self.commit("feat(pipeline): E2a BE-3 store the index", {"src/index.py": "i\n"})
        self.commit("chore: pre-E2a cleanup and BE-10")
        stats = self.stats(feature("E2E", ["docs/e2e-brief.md"], feature_id="e2e"), feature("Pipeline", feature_id="E2a"))
        self.assertEqual({"E2E": ([brief], [candidate(scoped, "test(e2e): cover the search page")]),
                          "Pipeline": ([], [candidate(task, "feat(pipeline): E2a BE-3 store the index")])},
                         {name: (entry["commits"], entry["candidates"]) for name, entry in stats.items()})

    def test_task_ids_find_no_commit_and_no_candidate(self):
        self.commit("(BE-1) store the set", {"src/store.py": "x\n"})
        self.assertEqual(([], []), tuple(self.stats(feature(task_ids=["BE-1"]))["X"][key] for key in ("commits", "candidates")))

    def test_candidates_leave_out_the_commits_any_feature_claims(self):
        by_doc = self.commit("feat(X-1): brief", {"docs/x-brief.md": "x\n"})
        given = self.commit("feat(X-1): store", {"src/store.py": "s\n"})
        self.commit("docs(X-1): the y plan names x", {"docs/y-plan.md": "y\n"})
        y_given = self.commit("fix(Y-1): touch X-1 too", {"src/both.py": "b\n"})
        loose = self.commit("test(X-1): cover it", {"tests/x.py": "t\n"})
        result = self.run_stats(feature("X", ["docs/x-brief.md"], feature_id="X-1", commits=[given]),
                                feature("Y", ["docs/y-plan.md"], feature_id="Y-1", commits=[y_given]))
        stats = {entry["name"]: entry for entry in result.json["features"]}
        self.assertEqual([by_doc, given], stats["X"]["commits"])
        self.assertEqual([candidate(loose, "test(X-1): cover it")], stats["X"]["candidates"])
        self.assertEqual([], stats["Y"]["candidates"])

    def test_a_commit_several_features_claim_counts_for_none_is_named_and_is_no_candidate(self):
        export_plan = self.commit("export plan", {"docs/export-plan.md": "export\n"})
        import_plan = self.commit("import plan", {"docs/import-plan.md": "import\n"})
        archive = self.commit("archive both", {"docs/export-plan.md": "export done\n", "docs/import-plan.md": "import done\n"})
        crossing = self.commit("feat(EX): reuse the import parser", {"docs/import-plan.md": "import done, reused\n"})
        both_given = self.commit("feat(EX, IM): one format", {"src/format.py": "f\n"})
        own = self.commit("feat(EX): write the file", {"src/export.py": "e\n"})
        result = self.run_stats(feature("Export", ["docs/export-plan.md"], feature_id="EX", commits=[crossing, both_given, own]),
                                feature("Import", ["docs/import-plan.md"], feature_id="IM", commits=[both_given]))
        self.assertEqual({"Export": ([export_plan, own], []), "Import": ([import_plan], [])},
                         {entry["name"]: (entry["commits"], entry["candidates"]) for entry in result.json["features"]})
        self.assertEqual("estimate: commits that several features claim count for none: %s, %s, %s\n"
                         % (archive[:SHORT_HASH], crossing[:SHORT_HASH], both_given[:SHORT_HASH]), result.err)

    def test_a_doc_is_found_from_a_subfolder(self):
        brief = self.commit("add brief", {"docs/x-brief.md": "a\n"})
        self.commit("other", {"src/a.py": "a\n"})
        self.assertEqual([brief], self.stats(feature(docs=["x-brief.md"]), cwd=self.path("docs"))["X"]["commits"])

    def test_nothing_matched_gives_zeros(self):
        self.commit("other", {"src/a.py": "a\n"})
        self.assertEqual({"name": "X", "commits": [], "count": 0, "linesAdded": 0, "linesRemoved": 0, "candidates": []},
                         self.stats(feature(docs=["docs/none.md"], feature_id="X-1"))["X"])

    def test_a_repository_without_commits_matches_nothing(self):
        stats = self.stats(feature(docs=["docs/x-brief.md"], feature_id="X-1"))["X"]
        self.assertEqual((0, []), (stats["count"], stats["candidates"]))

    def test_a_given_hash_that_is_no_commit_reachable_from_head_is_a_bad_argument_naming_it_and_the_feature(self):
        main = self.commit("start")
        git(self.root, "checkout", "-q", "-b", "side")
        side = self.commit("X-1 on a side branch")
        git(self.root, "checkout", "-q", "main")
        for given in (UNKNOWN_HASH, side, main[:SHORT_HASH]):
            result = self.run_estimate("git-stats", json.dumps({"features": [feature(feature_id="X-1", commits=[main, given])]}))
            self.assert_error(result, EXIT_BAD_ARGUMENTS, "'%s' of 'X'" % given)

    def test_given_commits_that_are_not_a_list_of_strings_are_a_bad_argument(self):
        for given in ("abc", [7], [""]):
            result = self.run_estimate("git-stats", json.dumps({"features": [{"name": "X", "commits": given}]}))
            self.assert_error(result, EXIT_BAD_ARGUMENTS, "commits of 'X' must be a list of strings")

    def test_the_features_can_come_from_stdin(self):
        brief = self.commit("add brief", {"docs/x-brief.md": "a\n"})
        result = self.run_estimate("git-stats", "-", stdin=json.dumps({"features": [feature(docs=["docs/x-brief.md"])]}))
        self.assertEqual(EXIT_OK, result.code, result.err)
        self.assertEqual([brief], result.json["features"][0]["commits"])

    def test_features_without_names_are_a_bad_argument(self):
        self.assert_error(self.run_estimate("git-stats", json.dumps({"features": [{"docs": ["a.md"]}]})), EXIT_BAD_ARGUMENTS,
                          "each feature needs a name")

    def test_an_id_that_is_not_text_is_a_bad_argument(self):
        self.assert_error(self.run_estimate("git-stats", json.dumps({"features": [{"name": "X", "id": 7}]})), EXIT_BAD_ARGUMENTS,
                          "id of 'X' must be text or null")

    def test_outside_a_git_repository_it_is_a_runtime_error(self):
        folder = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        result = self.run_estimate("git-stats", json.dumps({"features": [feature(docs=["docs/x-brief.md"])]}), cwd=folder)
        self.assert_error(result, EXIT_RUNTIME_ERROR, "git")


if __name__ == "__main__":
    unittest.main()
