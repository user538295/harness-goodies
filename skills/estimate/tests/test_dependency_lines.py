"""Committed dependency files (line_kinds' vendored kind: node_modules and similar folders, lockfiles, minified files) never
count as lines changed: `git-stats` and `record-commit` leave their numstat lines out, judging a renamed path by its new path
(`dir/{old => new}/f` and `old => new`), and keep every other file's lines."""
import json
import os
import unittest

from tests.support import EXIT_OK, ProjectTestCase, document, new_feature

BRIEF = "docs/x-brief.md"
PLAN = "docs/x-tasks.md"
TASKS_OPEN = "## Task Breakdown\n\n### Phase 1 · Core\n- [ ] **BE-1** — Store the set\n    - Entity · 2.0h\n"
TASKS_DONE = TASKS_OPEN.replace("- [ ]", "- [x]")
TEN_LINES = 10


def numbered(word, count=TEN_LINES):
    """`count` distinct lines, so git pairs each moved file with its own source."""
    return "".join("%s %d\n" % (word, number) for number in range(1, count + 1))


class DependencyLinesTest(ProjectTestCase):
    def add_sources_and_dependencies(self, extra=None):
        """src/app.js, src/old.js (10 lines each), node_modules/left-pad/index.js (10) and web/package-lock.json (4)."""
        return self.commit("add", {"src/app.js": numbered("app"), "src/old.js": numbered("old"),
                                   "node_modules/left-pad/index.js": numbered("pad"), "web/package-lock.json": "1\n2\n3\n4\n",
                                   **(extra or {})})

    def move(self, source, target, word):
        """Moves `source` to `target` and changes its fifth line (+1 −1), so git still sees a rename."""
        os.makedirs(os.path.dirname(self.path(target)), exist_ok=True)
        os.rename(self.path(source), self.path(target))
        self.write(target, numbered(word).replace("%s 5\n" % word, "%s five\n" % word))

    def move_into_and_out_of_node_modules(self, extra=None):
        """numstat shows `{src => node_modules/app}/app.js`, `src/old.js => node_modules/new.js` and
        `node_modules/left-pad/index.js => src/left-pad.js`, each +1 −1."""
        self.move("src/app.js", "node_modules/app/app.js", "app")
        self.move("src/old.js", "node_modules/new.js", "old")
        self.move("node_modules/left-pad/index.js", "src/left-pad.js", "pad")
        return self.commit("move", extra)

    def test_git_stats_leaves_out_dependency_lines_and_judges_renames_by_the_new_path(self):
        c1 = self.add_sources_and_dependencies({BRIEF: "a\nb\nc\n"})
        c2 = self.move_into_and_out_of_node_modules({BRIEF: "a\nB\nc\n"})
        result = self.run_estimate("git-stats", json.dumps({"features": [{"name": "X", "id": None, "docs": [BRIEF],
                                                                          "taskIds": []}]}))
        self.assertEqual(EXIT_OK, result.code, result.err)
        stats = result.json["features"][0]
        # c1: brief +3, app.js +10, old.js +10 (left-pad and the lockfile left out); c2: brief +1 −1, left-pad → src +1 −1.
        self.assertEqual(([c1, c2], 25, 2), (stats["commits"], stats["linesAdded"], stats["linesRemoved"]))

    def test_record_commit_leaves_out_dependency_lines_and_judges_renames_by_the_new_path(self):
        self.commit("plan", {PLAN: TASKS_OPEN})
        self.seed(document(new_feature("X", storyPoints=5, estimatedAt="2026-10-01",
                                       sources={"docs": [PLAN], "commits": [], "sessions": []})))
        for commit in (self.add_sources_and_dependencies({PLAN: TASKS_DONE}), self.move_into_and_out_of_node_modules()):
            result = self.run_estimate("record-commit", PLAN, commit, "--harness", "claude")
            self.assertEqual(EXIT_OK, result.code, result.err)
        # add: tick +1 −1, app.js +10, old.js +10; move: left-pad → src +1 −1.
        self.assertEqual({"commits": 2, "linesAdded": 22, "linesRemoved": 2}, self.feature("X")["git"])


if __name__ == "__main__":
    unittest.main()
