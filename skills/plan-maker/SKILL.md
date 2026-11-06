---
name: plan-maker
description: >
  Use this skill whenever the user asks to make a plan, create a plan, update a plan, or break down
  work into tasks. Triggers on phrases like "make a plan for", "create a plan", "plan this out",
  "write up a plan", "update the plan for", "add tasks to the plan", "how should we implement X",
  or any request to decompose a feature, fix, refactor, or initiative into actionable steps.
  Always use this skill even for short or casual planning requests — if there is structured work
  to decompose, this skill applies.
---

# Plan Maker

Creates and updates detailed, actionable implementation plans with the smallest possible tasks,
full test specifications, and explicit dependency chains.

---

## Step 1 — Gather Context

Collect the following from the conversation. Ask only for what is genuinely missing:

1. **What** is being built or changed? (feature name, goal, affected system)
2. **Why** does it exist? (business or user motivation)
3. **Who** is affected? (end users, internal team, API consumers)
4. **What already exists?** (relevant files, modules, current architecture)
5. **What is explicitly out of scope?**

---

## Step 2 — Resolve Architecture Decisions

Before writing the plan, surface any architectural choices that cannot be answered from the
codebase or prior conversation. Run rounds until all decisions are settled.

**Each round has two parts — in this order:**

### Part A: Challenge

Open every round with your honest architectural assessment. For each concern, present it as a
choice — never a bare problem. Format:

> **[Concern]**
> - **Option A: [path]** — Pro: ... Con: ...
> - **Option B: [path]** — Pro: ... Con: ...
> - **Recommendation**: [which and why, in one sentence]

Concerns to evaluate every round (only raise the ones that are real):
- Is the proposed approach compatible with the existing architecture?
- Does a simpler or more idiomatic alternative exist for this codebase?
- Will this decision create coupling, migration cost, or deployment risk?
- Does anything discovered in context reveal a conflict the user hasn't considered?
- After a user response: does their choice introduce a new concern?
- **AAA quality bar**: always seek the world-class, KISS solution — not the path of least resistance. If the codebase contains antipatterns, hacky workarounds, or smelling code adjacent to this area, do NOT replicate them. Name them explicitly and propose a clean approach instead.

If no real concern exists for a round, skip Part A entirely. Don't manufacture challenges.

### Part B: Questions

Ask only what cannot be answered from the code or docs. Maximum 3 questions per round.

Each question must:
- Be framed as a choice, not an open field
- Present 2–4 concrete options
- Each option: one-line pro, one-line con
- End with a clear recommendation and short reason

**Surface scope and edge cases — don't ask for them:**

Instead of _"what's out of scope?"_:
> "This could cover X and Y. I'd recommend scoping to X only — Y can follow later. Agree, or keep Y in?"

Instead of _"what are the edge cases?"_:
> "Edge case: what happens if [scenario]? Options: A / B / C. I'd go with B because..."

Fill any gap the user hasn't addressed with your own idea, presented as an option.

### Stop Condition

Move to Step 3 when ALL of the following are true:
- All key architectural choices are decided
- No open concern or conflict remains unresolved
- The task decomposition can proceed without ambiguity

If the user says _"looks good"_ or _"let's write the plan"_, move immediately.

---

## Step 3 — Find Where to Save the Plan

Do not create directories. Do not assume a location. Discover it.

**Search in this order:**

1. **Read `CLAUDE.md`** (or `claude.md`) at the project root if it exists — it may document where plans are stored.

2. **Search for existing plan files** — look for files that contain the plan header pattern `# [A-Z]+-\d+ —`. Whatever directory those files live in is the correct location.

3. **If nothing is found — ask the user once:**
   > "Where should I save this plan? I didn't find an existing plans directory in the project."

Save the new plan as `[ID]-[kebab-feature-name].md` in the identified location.

**Updating an existing plan:** if the user says "update the plan for X", search for the existing file first. Never create a duplicate.

---

## Step 4 — Write the Plan

Follow the mandatory format below exactly. Never omit a section — write `N/A` if a section genuinely does not apply, but keep the heading.
- Every task has an estimate in decimal hours (e.g. `1.0h`, `2.0h`, `4.0h`), written as `- **Estimate** · <n>h`, never days, T-shirt sizes or ranges.

**References:** Collect all files and resources discovered during context gathering and referenced in the plan (source files, docs, specs, ADRs, related plans). Copy them into the `## References` section as formatted entries. Every file path must be a markdown link `[path](path)`. If the Reference List is empty, write `N/A` under the heading — do not omit it.

```
# [ID] — [Feature name]
**Purpose**:
**Audience**:
**Status**: Draft | To Do | In Progress | Complete

---

## Background
[Why this work exists, what problem it solves, relevant history]

## Goal
[One paragraph: what success looks like when this is done]

---

## Scope

### In Scope
- [What is included]

### Out of Scope
- [What is excluded and why]

---

## Acceptance criteria

> Acceptance criteria are verified in the final task. See [Task N.N — Final verification & documentation update].

---

## What does NOT change
- [Existing behavior, APIs, or files that must remain untouched]

---

## Known limitations / accepted trade-offs
- [Conscious decisions not to solve certain problems, with brief rationale]

---

## Architecture
[Describe:]
- New modules / classes / functions being introduced
- How they connect to existing components
- Data flow and state changes
- New config keys or environment variables (with type and default value)
- API contracts: interfaces and method signatures with type hints

---

## Task breakdown

### Phase 1 — [Phase name]
> **Releasable**: [Precise condition — e.g. "after this phase", "after each tool task", "when Task 1.3 is complete"]

#### Task 1.1 — [Task name]
- [ ] **File**: [path/to/file.ext](path/to/file.ext)
- **Estimate** · 4.0h
- **Depends on**: [Task X.Y | nothing]
- **Description**:
  - Exact method/function signatures with type hints
  - Key behavior, edge cases, error handling
  - Config keys, env vars, or constants introduced
  - Integration points with other modules
- **Releasable**: [one sentence — what becomes callable or usable after this task completes]
- **Tests (TDD)** — [tests/path/test_file.ext](tests/path/test_file.ext):
  - Unit: `test_<scenario>` — what it verifies and how
  - Unit: `test_<edge_case>` — description
  - Integration: `test_<scenario>` — which components interact
  - E2E: `test_<user_flow>` — end-to-end behavior verified
  - Live E2E: `test_<smoke>` — what runs against real infrastructure (omit if N/A)
  - Checkpoint: `<command to run only this task's tests>`

---

### Final Phase — Verification & Documentation

#### Task N.N — Final verification & documentation update
- [ ] **File**: N/A (agent task)
- **Estimate** · 2.0h
- **Depends on**: [all prior tasks]
- **Description**:
  - Spawn an agent to discover all documentation in the project (READMEs, ADRs, API docs,
    architecture docs, user guides, CHANGELOG) and update every file whose content is affected
    by the changes delivered in this plan. The agent must not update docs that are unrelated.
  - Verify all acceptance criteria below are met before marking this task complete.
  - Verify every file reference in the plan is a valid markdown link — properly formatted as
    `[path](path)` and pointing to a file that exists in the project. List any broken references
    (path doesn't exist) as blockers before marking complete. New files created by this plan are
    exempt from the existence check.
- **Releasable**: after this task, the feature is fully verified and all documentation reflects
  the delivered implementation.
- **Acceptance criteria** (must all pass):
  - [Measurable, testable criterion]
- **Tests (TDD)**: N/A — this is a verification and documentation task.
- **Checkpoint**: manually confirm every acceptance criterion above is checked; scan the plan file for any bare file paths not wrapped in a markdown link and fix them.

---

## References
- [path/to/relevant-file.ext](path/to/relevant-file.ext) `[source]` — why it's relevant
```

---

## Step 5 — Estimate the Plan

After saving, load the `estimate` skill and run `/estimate <plan path>`, adding `--from <brief path>` when the plan started from a feature brief (so the brief's feature file under `.harness-goodies/estimations/` is updated, not duplicated). Write the SP it returns into the plan as the line immediately after the H1 title line, replacing any earlier one: `**Story Points:** <SP> — estimated <YYYY-MM-DD> with /estimate`. The plan keeps the SP of the moment it was written; its feature file under `.harness-goodies/estimations/` always holds the latest. A failed estimate is reported to the user and leaves the plan without the line.

---

## Task Rules

**Size**: One task = one callable unit of behavior — one function, one method, one endpoint, one tool, one schema. If a task implements more than one independently testable unit, split it. File count is irrelevant; a single method added to an existing file is a valid task, and two new files that together implement one cohesive unit is also valid.

**Detail**: Every task must be implementable without follow-up questions. Include exact signatures, error handling behavior, config keys, and edge cases.

**Tests**: Every task needs at minimum unit tests. Add integration, e2e, and live e2e wherever the task crosses component or system boundaries. Tests live only in the task — there is no separate top-level Tests section.

**Dependencies**: List prior task IDs that must be complete first, or write `nothing`. No circular dependencies.

**Checkpoint**: Each task must include a runnable command scoped to only its own tests.

**Separation of concerns**: data models, business logic, API layer, config, and documentation updates are each their own task — never combine them in one task.

**Final task**: Every plan must end with the "Final verification & documentation update" task as the last task of the last phase. It carries the full acceptance criteria list and the documentation update responsibility.

**File links**: Every file path in the plan must be written as a markdown link `[path](path)` — in **File** items, **Tests (TDD)** headers, and anywhere else a project file path appears.

---

## Phase Rules

- Phase 1 must produce the smallest thing that can be tested end-to-end
- Every phase header must state its releasability condition explicitly — when exactly something becomes usable, deployable, or demonstrable (e.g. "after this phase", "after each tool task", "when Task X.Y is complete")
- A phase typically contains 2–10 tasks; split if larger
- Later phases build on earlier ones — earlier work is never redone

---

## Updating an Existing Plan

1. Find and read the existing plan file first
2. Never remove completed tasks (`- [x]`) — they are history
3. Append new tasks to the appropriate phase, or add a new phase
4. Update `**Status**` if the overall state changed
5. Move any acceptance criteria that were in the top-level section into the final task if they aren't there already
6. Re-estimate the plan (Step 5) when its scope changed or earlier estimation is inaccurate

---

## ID Convention

Match the project's existing ID scheme if one exists.
If no scheme exists: `FEAT-001`, `FIX-001`, `REFACTOR-001`, `INFRA-001`, or `TASK-001`.

---

## Example: Good vs. Bad Task

**Bad** — too vague, no file, no signatures, no tests:
```
#### Task 2.1 — Add authentication
- [ ] Implement JWT auth
```

**Good** — implementable, testable, complete:
```
#### Task 2.1 — JWT token validation middleware
- [ ] **File**: [src/auth/jwt_middleware.py](src/auth/jwt_middleware.py)
- **Estimate** · 3.0h
- **Depends on**: Task 1.3 (User model), Task 1.4 (Config loader)
- **Description**:
  - `JWTMiddleware` class validates Bearer tokens on every protected route
  - `validate_token(token: str) -> TokenPayload | None` — decodes with PyJWT,
    raises `AuthError` on expiry or invalid signature
  - `TokenPayload(user_id: str, roles: list[str], exp: int)` — dataclass
  - Config: `JWT_SECRET` (str, required), `JWT_ALGORITHM` (str, default `"HS256"`)
  - Returns `401 {"error": "unauthorized"}` on failure; never leaks exception detail
  - Attaches decoded payload to `request.state.user` on success
  - **Releasable**: after this task, all protected routes enforce JWT validation
- **Tests (TDD)** — [tests/auth/test_jwt_middleware.py](tests/auth/test_jwt_middleware.py):
  - Unit: `test_valid_token_passes` — valid token returns decoded payload
  - Unit: `test_expired_token_returns_401` — expired token triggers AuthError
  - Unit: `test_missing_header_returns_401` — no Authorization header
  - Unit: `test_invalid_signature_returns_401` — tampered token
  - Integration: `test_protected_route_with_valid_token` — full request through middleware stack
  - E2E: `test_login_then_access_protected_endpoint` — login → token → call protected API
  - Checkpoint: `pytest tests/auth/test_jwt_middleware.py -v`
```
