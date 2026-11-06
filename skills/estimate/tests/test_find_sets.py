"""`find-sets`: every planning-doc set of the project (briefs, team plans, plans and task files grouped by folder and stem, by
front-matter links and by a shared first heading, never by links in the text) and whether its task list is all ticked. Inside git
the docs are the tracked and untracked-not-ignored files; outside git a walk that skips hidden and dependency folders. Counts come
from the real sibling `implement/scripts/plan-progress.sh --counts`."""
import os
import unittest

from tests.support import EXIT_BAD_ARGUMENTS, EXIT_OK, ProjectTestCase

BRIEF = """# Feature Brief: Export

## Goal
Export invoices.
"""

PLAN = """# Export — Plan

## Goal
Export invoices.
"""


def tasks_file(title, *ticks):
    """A task file titled `title` with one phase holding one `· 1h` task per tick (True = ticked)."""
    lines = ["# %s — Task Breakdown" % title, "", "## Tasks", "", "### Phase 1 · Core"]
    lines += ["- [%s] Task %d · 1h" % ("x" if ticked else " ", number) for number, ticked in enumerate(ticks, 1)]
    return "\n".join(lines) + "\n"


def with_front_matter(text, **links):
    """`text` behind a front matter block holding `links` (a `team_plan` key is written `team-plan`)."""
    block = ["---"] + ["%s: %s" % (key.replace("_", "-"), value) for key, value in links.items()] + ["status: done  # a note", "---"]
    return "\n".join(block) + "\n\n" + text


ALL_TICKED = (True, True, True)
PHASES = 1


def delivered_plan(task_count):
    return {"phases": PHASES, "tasks": task_count, "completed": task_count, "estimatedHours": float(task_count)}


class FindSetsTest(ProjectTestCase):
    def find_sets(self, cwd=None):
        result = self.run_estimate("find-sets", cwd=cwd)
        self.assertEqual(EXIT_OK, result.code, result.err)
        self.assertEqual("", result.err)
        return result.json

    def set_docs(self, payload):
        return [found["docs"] for found in payload["sets"]]

    def test_a_brief_plan_and_tasks_file_of_one_stem_are_one_delivered_set(self):
        self.commit("docs", {"docs/export-brief.md": BRIEF, "docs/export-plan.md": PLAN,
                             "docs/export-tasks.md": tasks_file("Export", *ALL_TICKED)})
        payload = self.find_sets()
        self.assertEqual({"sets": [{"docs": ["docs/export-brief.md", "docs/export-plan.md", "docs/export-tasks.md"],
                                    "planDoc": "docs/export-tasks.md", "plan": delivered_plan(len(ALL_TICKED)),
                                    "delivered": True, "missing": []}], "missing": []}, payload)

    def test_a_brief_alone_is_not_delivered_and_says_why(self):
        self.commit("brief", {"docs/export-brief.md": BRIEF})
        self.assertEqual([{"docs": ["docs/export-brief.md"], "planDoc": None, "plan": None, "delivered": False,
                           "missing": ["no plan or tasks file with tasks"]}], self.find_sets()["sets"])

    def test_a_plan_without_tasks_is_no_plan_doc(self):
        self.commit("plan", {"docs/export-plan.md": PLAN})
        found = self.find_sets()["sets"][0]
        self.assertEqual((None, None, False, ["no plan or tasks file with tasks"]),
                         (found["planDoc"], found["plan"], found["delivered"], found["missing"]))

    def test_a_partly_ticked_flat_task_list_is_not_delivered_and_its_gaps_name_the_file(self):
        self.commit("tasks", {"export_tasks.md": "## Tasks\n- [x] Parse\n- [ ] Print\n"})
        self.assertEqual([{"docs": ["export_tasks.md"], "planDoc": "export_tasks.md",
                           "plan": {"phases": None, "tasks": 2, "completed": 1, "estimatedHours": None}, "delivered": False,
                           "missing": ["export_tasks.md: phases: a flat task list (no ### phase headings)",
                                       "export_tasks.md: estimatedHours: no task line ends with '· <hours>h'"]}],
                         self.find_sets()["sets"])

    def test_the_stem_compares_case_insensitively_and_other_stems_stay_apart(self):
        self.commit("docs", {"docs/Export-Brief.md": BRIEF, "docs/export-tasks.md": tasks_file("Export", *ALL_TICKED),
                             "docs/import-brief.md": "# Feature Brief: Import\n", "other/export-brief.md": BRIEF})
        self.assertEqual([["docs/Export-Brief.md", "docs/export-tasks.md"], ["docs/import-brief.md"],
                          ["other/export-brief.md"]], self.set_docs(self.find_sets()))

    def test_a_front_matter_link_joins_docs_of_different_stems(self):
        self.commit("docs", {"docs/alpha-brief.md": "# Feature Brief: Alpha\n",
                             "docs/beta-tasks.md": with_front_matter(tasks_file("Beta", *ALL_TICKED), brief="alpha-brief.md")})
        found = self.find_sets()["sets"]
        self.assertEqual([["docs/alpha-brief.md", "docs/beta-tasks.md"]], [item["docs"] for item in found])
        self.assertTrue(found[0]["delivered"])

    def test_a_front_matter_path_from_the_project_root_resolves(self):
        self.commit("docs", {"docs/alpha-brief.md": "# Feature Brief: Alpha\n",
                             "docs/beta-tasks.md": with_front_matter(tasks_file("Beta", *ALL_TICKED),
                                                                     brief="docs/alpha-brief.md")})
        self.assertEqual([["docs/alpha-brief.md", "docs/beta-tasks.md"]], self.set_docs(self.find_sets()))

    def test_a_stale_front_matter_path_resolves_by_basename_and_an_unresolvable_one_is_reported(self):
        stale = "Documentation/Backlog/gate-v2-brief.md"
        self.commit("docs", {
            "Documentation/Completed/gate-v2-brief.md": "# Feature Brief: Gate v2\n",
            "Documentation/Completed/eval-gate-tasks.md": with_front_matter(tasks_file("Eval gate", *ALL_TICKED), brief=stale,
                                                                            plan="gone-plan.md")})
        self.assertEqual([{"docs": ["Documentation/Completed/eval-gate-tasks.md", "Documentation/Completed/gate-v2-brief.md"],
                           "planDoc": "Documentation/Completed/eval-gate-tasks.md", "plan": delivered_plan(len(ALL_TICKED)),
                           "delivered": True,
                           "missing": ["Documentation/Completed/eval-gate-tasks.md: front matter plan: gone-plan.md not found"]}],
                         self.find_sets()["sets"])

    def test_a_shared_first_heading_joins_differently_named_docs_of_one_folder(self):
        self.commit("docs", {
            "docs/a6-brief.md": "# Feature Brief: A6 — State-Store + Router Cache Hardening\n",
            "docs/state-store-team-plan.md": "# A6 —  state-store + router cache hardening - Team Plan\n",
            "docs/cache-tasks.md": tasks_file("A6 — State-Store + Router Cache Hardening", *ALL_TICKED),
            "other/cache-brief.md": "# Feature Brief: A6 — State-Store + Router Cache Hardening\n"})
        self.assertEqual([["docs/a6-brief.md", "docs/cache-tasks.md", "docs/state-store-team-plan.md"],
                          ["other/cache-brief.md"]], self.set_docs(self.find_sets()))

    def test_links_in_a_references_section_never_join_two_features(self):
        self.commit("docs", {
            "docs/00_foundation_brief.md": "# Feature Brief: Foundation\n\n## References\n- [Search](01_search_brief.md)\n"
                                           "- [Search plan](./01_search-team-plan.md)\n",
            "docs/01_search_brief.md": "# Feature Brief: Search\n",
            "docs/01_search-team-plan.md": "# Search — Team Plan\n"})
        self.assertEqual([["docs/00_foundation_brief.md"], ["docs/01_search-team-plan.md", "docs/01_search_brief.md"]],
                         self.set_docs(self.find_sets()))

    def test_the_tasks_file_is_the_plan_doc_over_a_team_plan_with_tasks(self):
        self.commit("docs", {"docs/export-team-plan.md": tasks_file("Export", True, False),
                             "docs/export-tasks.md": tasks_file("Export", *ALL_TICKED)})
        found = self.find_sets()["sets"][0]
        self.assertEqual(("docs/export-tasks.md", delivered_plan(len(ALL_TICKED)), True),
                         (found["planDoc"], found["plan"], found["delivered"]))

    def test_a_team_plan_with_tasks_is_the_plan_doc_when_the_tasks_file_has_none(self):
        self.commit("docs", {"docs/export-team-plan.md": tasks_file("Export", True, False),
                             "docs/export-tasks.md": "# Export — Tasks\n\nTo do.\n"})
        found = self.find_sets()["sets"][0]
        self.assertEqual(("docs/export-team-plan.md", False), (found["planDoc"], found["delivered"]))

    def test_two_task_files_of_one_set_take_the_first_and_report_both(self):
        self.commit("docs", {"docs/export-tasks.md": tasks_file("Export", *ALL_TICKED),
                             "docs/more-tasks.md": with_front_matter(tasks_file("More", False), tasks="export-tasks.md")})
        found = self.find_sets()["sets"][0]
        self.assertEqual(("docs/export-tasks.md", ["several task lists: docs/export-tasks.md, docs/more-tasks.md"]),
                         (found["planDoc"], found["missing"]))

    def test_an_untracked_doc_is_found_and_a_gitignored_one_skipped(self):
        self.commit("ignore", {".gitignore": "scratch/\n"})
        self.write("docs/export-brief.md", BRIEF)
        self.write("scratch/draft-brief.md", BRIEF)
        self.assertEqual([["docs/export-brief.md"]], self.set_docs(self.find_sets()))

    def test_only_planning_doc_names_count(self):
        self.commit("docs", {"docs/export-design.md": BRIEF, "docs/myplan.md": PLAN, "docs/brief.txt": BRIEF,
                             "docs/Plan.md": PLAN})
        self.assertEqual([["docs/Plan.md"]], self.set_docs(self.find_sets()))

    def test_a_project_without_planning_docs_has_no_sets(self):
        self.assertEqual({"sets": [], "missing": []}, self.find_sets())

    def test_outside_git_a_walk_skips_hidden_and_dependency_folders(self):
        folder = os.path.join(self.base, "plain")
        for relative in ("docs/export-brief.md", ".cache/old-brief.md", "node_modules/lib/lib-brief.md"):
            path = os.path.join(folder, relative)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(BRIEF)
        self.assertEqual([["docs/export-brief.md"]], self.set_docs(self.find_sets(cwd=folder)))

    def test_find_sets_takes_no_arguments(self):
        self.assert_error(self.run_estimate("find-sets", "docs"), EXIT_BAD_ARGUMENTS, "find-sets takes no arguments")


if __name__ == "__main__":
    unittest.main()
