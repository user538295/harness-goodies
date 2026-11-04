"""`doc-stats <file.md>` (C3) on the two real task formats: plan-maker's `## Task breakdown` (phases as `### Phase`, one checkbox per
`#### Task`) and create-tasks' `## Task Breakdown` (a `### Dependency graph`, checkbox tasks with `- Layer · 2.0h` sub-bullets).
Phases and tasks come from the real sibling `implement/scripts/plan-progress.sh --counts`."""
import os
import unittest

from tests.support import EXIT_OK, EXIT_RUNTIME_ERROR, ProjectTestCase

PLAN_MAKER_PLAN = """# Export — Plan

## Task breakdown

### Phase 1 — Walking skeleton
> **Releasable**: after this phase

#### Task 1.1 — Invoice model
- [x] **File**: [src/invoice.py](src/invoice.py)
- **Depends on**: nothing
- **Description**:
  - `Invoice` holds the lines

#### Task 1.2 — PDF renderer
- [ ] **File**: [src/pdf.py](src/pdf.py)
- **Depends on**: Task 1.1

### Final Phase — Verification & Documentation

#### Task 2.1 — Final verification & documentation update
- [ ] **File**: N/A (agent task)
- **Depends on**: all prior tasks
"""

CREATE_TASKS_FILE = """# EXP · Export — Task Breakdown

## References

- **Plan:** [export-team-plan.md](./export-team-plan.md)

## Task Breakdown

### Dependency graph

```mermaid
flowchart LR
  K1([K1 · align])
```

### Phase 0 · Kickoff *(prerequisite)*
- [x] **K1** — Agree the Contracts with the team #team
    - — · 1.0h
    - completes C1
    - Tests

### Phase 1 · Core export *(the walking skeleton)*
- [x] **BE-1** — Store the invoice set #backend-role
    - Entity · 8.0h
    - needs K1 · completes C1
    - Tests
        - #unit_test — `test_store` — keeps the set
- [ ] **T-1** — e2e: export → open #tester-role
    - — · 2.5h
    - needs BE-1 · completes S1
"""

FLAT_DONE = """## Tasks
- [x] Parse the input · 1h
- [x] Print the report · 0.5h
"""

NO_SECTION = """# Brief
## Goal
- [ ] not a task
"""


class DocStatsTest(ProjectTestCase):
    def stats(self, relative, text, *options, cwd=None):
        self.write(relative, text)
        result = self.run_estimate("doc-stats", relative if cwd is None else os.path.relpath(self.path(relative), cwd),
                                   *options, cwd=cwd)
        self.assertEqual(EXIT_OK, result.code, result.err)
        return result.json

    def test_a_plan_maker_plan_has_phases_and_tasks_but_no_hours(self):
        stats = self.stats("docs/export-plan.md", PLAN_MAKER_PLAN)
        # 2 phases; checkboxes 1 + 1 + 1 = 3, one ticked; no `· <n>h` line.
        self.assertEqual({"phases": 2, "tasks": 3, "completed": 1, "estimatedHours": None,
                          "missing": ["estimatedHours: no task line ends with '· <hours>h'"]}, stats)

    def test_a_create_tasks_file_sums_its_hours_and_skips_the_graph_heading(self):
        stats = self.stats("docs/export-tasks.md", CREATE_TASKS_FILE)
        # Phases 0 and 1 (the graph heading holds no task); 3 tasks, 2 ticked; hours 1.0 + 8.0 + 2.5 = 11.5.
        self.assertEqual({"phases": 2, "tasks": 3, "completed": 2, "estimatedHours": 11.5, "missing": []}, stats)

    def test_a_fully_ticked_flat_list_counts_with_null_phases(self):
        stats = self.stats("tasks.md", FLAT_DONE)
        # No ### heading; 2 tasks, both ticked; 1 + 0.5 = 1.5 hours.
        self.assertEqual({"phases": None, "tasks": 2, "completed": 2, "estimatedHours": 1.5,
                          "missing": ["phases: a flat task list (no ### phase headings)"]}, stats)

    def test_a_doc_without_a_task_section_reports_why_the_counts_are_missing(self):
        stats = self.stats("brief.md", NO_SECTION)
        self.assertEqual((None, None, None), (stats["phases"], stats["tasks"], stats["completed"]))
        self.assertIn("phases, tasks, completed: plan-progress.sh exit 3: WARNING: no recognized task section heading found.",
                      stats["missing"])

    def test_a_missing_plan_progress_script_leaves_the_counts_null_but_still_sums_hours(self):
        absent = os.path.join(self.base, "nowhere", "plan-progress.sh")
        stats = self.stats("tasks.md", FLAT_DONE, "--plan-progress", absent)
        self.assertEqual((None, None, None, 1.5), (stats["phases"], stats["tasks"], stats["completed"], stats["estimatedHours"]))
        self.assertEqual(["phases, tasks, completed: plan-progress.sh not found at " + absent], stats["missing"])

    def test_the_path_is_relative_to_the_working_folder(self):
        os.makedirs(self.path("src"))
        stats = self.stats("docs/export-tasks.md", CREATE_TASKS_FILE, cwd=self.path("src"))
        self.assertEqual(3, stats["tasks"])

    def test_a_missing_doc_is_a_runtime_error(self):
        self.assert_error(self.run_estimate("doc-stats", "nope.md"), EXIT_RUNTIME_ERROR, "nope.md")


if __name__ == "__main__":
    unittest.main()
