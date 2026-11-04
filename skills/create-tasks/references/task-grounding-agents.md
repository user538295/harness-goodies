# Task-grounding agents (Step 2)

Task breakdown is only trustworthy if it matches the real code. Before writing anything, investigate the codebase with read-only subagents so that every task names real files, follows real patterns, and has a grounded dependency order.

Spawn **three read-only subagents in parallel** — `backend-grounding`, `frontend-grounding`, `tester-grounding` — and wait for all to return before synthesising. If no subagent / Task tool is available, run each investigation **inline yourself, in order**.

These briefs are task-grounding focused: they find the real files/patterns each work item will touch and the integration tests the implementing dev should own. They are distinct from the architecture-investigation briefs used by `plan-maker-for-team` — the goal here is file-level "what to touch and in what order", not a deep architectural map.

Map of agent → the task breakdown section it grounds:
- backend-grounding → backend task list (real files, dependency order, integration tests)
- frontend-grounding → frontend task list (real files, UI patterns, integration tests)
- tester-grounding → tester allocation table (e2e harness capabilities, manual vs automatable)

---

## Shared context — paste into EVERY subagent prompt

- **Team plan:** {{paste the full plan text, or the relevant Contracts/Scenarios/Scope sections at minimum}}
- **Repository root:** {{repo path}}
- **Active architecture approach:** {{Clean Architecture (default), OR the user-requested skill and its structure units}}.
- **Test ownership:** unit AND integration tests are written by the implementing dev; the tester owns e2e + manual only.
- **Your job:** investigate only. Gather facts that will ground the task-by-task breakdown. You will NOT write tasks and you will NOT write code.
- **Rules every investigation subagent follows:**
  - **Codebase first, docs second.** The code is the primary source of truth.
  - **Cite reality.** For every claim, reference the exact file path and symbol name.
  - **Read-only.** Do not modify, create, or delete any file; do not run mutating commands.
  - **No guessing.** If the plan assumes something you can't find, report it as an **OPEN QUESTION**.
  - **Use the active approach's vocabulary.** Classify by its structure units.
  - **Output discipline.** Return concise, structured bullets the task-writer can use directly.

---

## Backend-grounding agent  → Backend task list

**Goal:** find the real files/patterns each backend work item will touch, a code-grounded dependency order, **and the integration tests the backend dev should own**.
**Find:**
- The types/files for each backend layer in the plan's scope; existing patterns; current implementations that change; how layers currently call each other.
- Existing backend integration tests/harness and what is realistically integration-testable here (since the backend dev now writes integration tests, not the tester).
- The dependency order tasks must follow — which changes must land before others can compile or run.
**Return:**
- Per anticipated backend item: the real files/symbols + pattern; the unit and integration tests it should carry; a code-grounded dependency order; risks/unknowns as open questions.

---

## Frontend-grounding agent  → Frontend task list

**Goal:** find the real views/components each frontend work item will touch, existing UI patterns, **and the integration tests the frontend dev should own**.
**Find:**
- The view files/components for the plan's scope; current selection/interaction patterns; how the UI talks to the service layer (the handoff seam); existing UI states.
- Existing frontend test setup and what is integration-testable on the frontend (the frontend dev now writes frontend integration tests).
- The dependency order for frontend tasks — which components or contracts must exist before others can be wired up.
**Return:**
- Per anticipated frontend item: the real files/components + pattern; the unit and integration tests it should carry; how it integrates with the service boundary; risks/unknowns as open questions.

---

## Tester-grounding agent  → Tester allocation table

**Goal:** discover the real test setup so the allocation and the **tester's e2e + manual** tasks match what is actually possible. (Unit + integration are dev-owned; you focus on e2e and manual.)
**Find:**
- Existing e2e / UI test targets and manual-test conventions; the frameworks used; how tests run.
- Whether device-only behaviours (interruptions, route changes, permissions) are automatable in this harness or must be manual.
- Existing test helpers / fixtures to reuse for e2e.
- Whether any data migration this feature requires is testable in the e2e harness, or must be manual.
**Return:**
- The e2e and manual capabilities available and their conventions.
- For each scenario in the plan, the cheapest proving level **actually achievable here**, flagging which must be manual.
- Explicit answers to automatability questions (e.g. "can interruption be automated?", "is the data migration testable in e2e?").
- Gaps / unknowns as open questions.
