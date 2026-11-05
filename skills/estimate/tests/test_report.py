"""`report [--out <path>]` (C3) on a real repository: it analyses the delivered features with an after-delivery SP against
their commits in git and writes one self-contained HTML page (data, findings and the bundled Chart.js inline), by default
`.harness-goodies/estimation-report.html` at the project root; stdout names the page, the counts and the commits git lacks."""
import json
import os
import re
import unittest

from tests.support import (DATA_FOLDER, EXIT_BAD_ARGUMENTS, EXIT_OK, EXIT_RUNTIME_ERROR, ProjectTestCase, document,
                           new_feature)

DEFAULT_REPORT = DATA_FOLDER + "/estimation-report.html"
EMBEDDED_DATA = re.compile(r"const D = (\{.*?\});\n", re.DOTALL)
UNFILLED_MARKER = re.compile(r"<!--@\w+-->|/\*@\w+\*/")
CHART_JS_BANNER = "Chart.js v4.4.7"
ABSENT_COMMIT = "f" * 40


def lines(count):
    return "".join("line %d\n" % number for number in range(count))


class ReportTest(ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.export = self.commit("feat(EX-1): export", {"src/export.py": lines(30), "tests/test_export.py": lines(10)})
        self.importer = self.commit("feat(IM-2): import", {"src/importer.py": lines(80), "docs/import.md": lines(5)})
        self.search = self.commit("feat(SE-3): search", {"src/search.py": lines(200), "package-lock.json": lines(50)})

    def delivered(self, name, feature_id, size, commit, added, **fields):
        return new_feature(name, id=feature_id, status="delivered", updatedStoryPoints=size,
                           plan={"phases": 2, "tasks": size * 2, "estimatedHours": size * 3.0},
                           git={"commits": 1, "linesAdded": added, "linesRemoved": 0},
                           sources={"docs": [], "commits": [commit], "sessions": []}, **fields)

    def seed_three(self, **export_fields):
        self.seed(document(self.delivered("Export", "EX-1", 2, self.export, 40, **export_fields),
                           self.delivered("Import", "IM-2", 5, self.importer, 85),
                           self.delivered("Search", "SE-3", 8, self.search, 200),  # git-stats leaves package-lock.json out
                           new_feature("Planned", storyPoints=3)))

    def report(self, *arguments, cwd=None):
        result = self.run_estimate("report", *arguments, cwd=cwd)
        self.assertEqual(EXIT_OK, result.code, result.err)
        return result

    def page(self, relative=DEFAULT_REPORT):
        with open(self.path(relative), encoding="utf-8") as handle:
            return handle.read()

    def embedded(self, page):
        return json.loads(EMBEDDED_DATA.search(page).group(1))

    def test_it_writes_one_self_contained_page_of_the_references(self):
        self.seed_three()
        result = self.report()
        self.assertEqual({"report": DEFAULT_REPORT, "features": 3, "forecasts": 0, "missing": []}, result.json)
        page = self.page()
        self.assertEqual([], UNFILLED_MARKER.findall(page))
        self.assertIn(CHART_JS_BANNER, page)
        rows = {row["label"]: row for row in self.embedded(page)["rows"]}
        self.assertEqual({"EX-1 · Export", "IM-2 · Import", "SE-3 · Search"}, set(rows))
        self.assertEqual((30, 10), (rows["EX-1 · Export"]["src"], rows["EX-1 · Export"]["tests"]))
        self.assertEqual((200, 50), (rows["SE-3 · Search"]["src"], rows["SE-3 · Search"]["vendored"]))

    def test_stored_lines_without_dependency_files_match_git_and_the_dependency_lines_stay_apart(self):
        self.seed_three()
        self.report()
        data = self.embedded(self.page())
        search = next(row for row in data["rows"] if row["label"] == "SE-3 · Search")
        self.assertEqual((200, 200, 50), (search["churn"], search["git_churn"], search["vendored"]))
        self.assertEqual([], data["lines"]["mismatches"])
        self.assertEqual((325, 50), (data["lines"]["unique_total"], data["lines"]["cats"]["vendored"]))

    def test_an_estimate_made_before_work_counts_as_a_forecast(self):
        self.seed_three(storyPoints=5, estimatedAt="2026-09-01")
        result = self.report()
        self.assertEqual(1, result.json["forecasts"])
        forecast = self.embedded(self.page())["forecast"]
        self.assertEqual([("EX-1 · Export", 5, 2, -2)],
                         [(row["label"], row["est"], row["sp"], row["steps"]) for row in forecast["rows"]])

    def test_out_is_taken_relative_to_the_working_folder(self):
        self.seed_three()
        result = self.report("--out", "reports/estimates.html", cwd=self.path("docs"))
        self.assertEqual("docs/reports/estimates.html", result.json["report"])
        self.assertIn(CHART_JS_BANNER, self.page("docs/reports/estimates.html"))
        self.assertFalse(os.path.exists(self.path(DEFAULT_REPORT)))

    def test_commits_git_does_not_hold_are_named_as_missing(self):
        sized = self.delivered("Lost", "LO-4", 3, ABSENT_COMMIT, 12)
        self.seed(document(self.delivered("Export", "EX-1", 2, self.export, 40), sized))
        result = self.report()
        self.assertEqual(["1 commit not found in git: %s" % ABSENT_COMMIT[:8]], result.json["missing"])

    def test_duplicate_files_are_warned_about(self):
        self.seed(document(self.delivered("Export", "EX-1", 2, self.export, 40),
                           self.delivered("Export", "EX-9", 5, self.importer, 85, file="2026-10-02-export.json")))
        self.assertIn("duplicate name 'Export'", self.report().err)

    def test_without_a_delivered_and_sized_feature_it_is_a_runtime_error(self):
        self.seed(document(new_feature("Planned", storyPoints=3)))
        self.assert_error(self.run_estimate("report"), EXIT_RUNTIME_ERROR, "/estimate init")
        self.assertFalse(os.path.exists(self.path(DEFAULT_REPORT)))

    def test_a_scale_of_words_is_a_runtime_error(self):
        sized = new_feature("Sized", status="delivered", updatedStoryPoints="M")
        self.seed(document(sized, scale=["S", "M", "L"]))
        self.assert_error(self.run_estimate("report"), EXIT_RUNTIME_ERROR, "numeric scale")

    def test_report_takes_no_positional_argument(self):
        self.assert_error(self.run_estimate("report", "now"), EXIT_BAD_ARGUMENTS, "report takes no arguments")


if __name__ == "__main__":
    unittest.main()
