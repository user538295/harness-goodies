---
name: create-tasks
description: Generate the task-by-task breakdown for an existing team plan — a Mermaid dependency graph, vertical-slice phases, single-role checkbox tasks with per-task tests, and a mandatory close-out task — written to its own Markdown file. Use this whenever the user has a `<feature-name>-team-plan.md` (produced by the `plan-maker-for-team` skill) and asks to break it into tasks, generate the task breakdown, create tasks for the plan, or invokes `/create-tasks`. Requires an existing team plan as input — if none exists yet, tell the user to run `plan-maker-for-team` first.
license: MIT
---

# Create Tasks

Turn an existing **team plan** (`<feature-name>-team-plan.md`, from `plan-maker-for-team`) into a single-role, checkbox **task breakdown**: a dependency graph, vertical-slice phases (walking skeleton first), per-task tests, and a mandatory close-out task.

A task breakdown is only trustworthy if it matches the real code, so before writing anything this skill **investigates the codebase with read-only subagents** (Step 2) — re-run fresh even if `plan-maker-for-team` already investigated, so this skill works standalone in a later session.

The output is a single file, `<feature-name>-tasks.md`, written **in the same folder as the plan** by default.

## Read these first

- `references/task-grounding-agents.md` — this skill uses the backend-grounding, frontend-grounding, and tester-grounding briefs from this file, re-run independently of `plan-maker-for-team`'s own run.
- The `vertical-slicer` skill's `references/vertical-slicing.md` — the slicing authority for rule 1; read it once Step 1 has loaded the `vertical-slicer` skill (else use the built-in fallback below).
- `references/example-tasks.md` — the exact task breakdown generated for the plan in `plan-maker-for-team`'s `references/example-team-plan.md`. Source of truth for the output format.
- `assets/task-breakdown-template.md` — the fill-in skeleton.

## Glossary

- **Vertical slice (phasing)** — a delivery phase that ships a working end-to-end increment; distinct from the architecture's structure units (which the plan already fixed). The first slice is the walking skeleton.
- **Walking skeleton** — the first vertical slice: the thinnest end-to-end path that exercises the architecture, carrying the data/model foundation.
- **needs / completes** — task fields: `needs` = predecessor task IDs (task → task only, within this file); `completes` = the scenario (`S#`) or contract (`C#`) — both defined in the plan — that the task makes true or realises.

## Step 0 — Gate the input

Requires an existing `<feature-name>-team-plan.md`, produced by `plan-maker-for-team`.

- If given the plan path directly, use it.
- If given a brief path (`<feature-name>-brief.md`) or just a feature name, look for the sibling `<feature-name>-team-plan.md` in the same folder.
- **If no team plan exists, do not invent one.** Tell the user to run `plan-maker-for-team` first, then stop.

## Output naming

- Derive `<feature-name>` from the plan's frontmatter `feature`/filename.
- Write the task breakdown to `<feature-name>-tasks.md`, in the **same folder as the plan**.

## Locked rules (the output must satisfy all)

1. **Phases are vertical slices, never horizontal — slice with the `vertical-slicer` skill when available, else built-in.** Set the slice boundaries, their order, and the walking skeleton with the **`vertical-slicer`** skill when it is installed: read and apply that skill's `references/vertical-slicing.md` (its verticality test, smells, order, and cut-thinner toolkit; its `references/exemplars.md` on borderline slices) to form and self-check the phases. If `vertical-slicer` is **not** installed, fall back to the **built-in simplified** slicing below. Either way, **tell the user which you are using**. Each phase delivers a working end-to-end increment (data/model → logic → adapter → UI → its tests), not a layer and not an activity. **The first slice is the walking skeleton**, carrying the data/model foundation (and its migration). There is **no separate "integrate" phase** — each slice integrates by construction. A short **Kickoff** precedes the slices; a mandatory **Close-out** task ends them (see rule 6).
2. **Test ownership.** **Unit AND integration tests belong to the implementing dev** — written test-first in each implementation task's `Tests` block (backend dev writes backend integration tests; frontend dev writes frontend integration tests). **The tester owns e2e and manual tests only.** Coordination/close-out tasks have an **empty** `Tests` block. **Every `#manual_test` scenario must also have a companion `#e2e_test` in the same task covering the same path** — manual tests document exploratory or hard-to-automate checks, but they do not replace automation; if a path is worth testing manually, it is worth automating too. **Exception:** if the tester-grounding agent (Step 2) confirms a scenario is genuinely non-automatable (e.g. OS-level interruption, device permission dialog, hardware-only path), annotate the `#manual_test` line with `(non-automatable: <reason>)` and omit the companion `#e2e_test` for that scenario only.
3. **No code; name real components, don't implement.** Naming a type/view/service is fine; method bodies, signatures, and SQL are not. **Every component, type, and file name comes from the Step 2 codebase investigation — not from guessing.** Never invent product behaviour the plan did not state; every such unknown becomes an open question (surface it to the user; the plan owns `Q#` tracking).
4. **Stable IDs.** `K#`/`BE-#`/`FE-#`/`T-#` tasks, numbered contiguously when first generated. On any later edit (reslice, insert, delete) **do not renumber** existing IDs. `completes` cites the plan's `S#`/`C#` IDs verbatim — this file never redefines them.
5. **Tags.** Put the **role tag at the end of** each task title line: `#frontend-role`, `#backend-role`, `#tester-role` (`#team` for cross-team tasks). Put a **test-type tag at the start of** each test line: `#unit_test`, `#integration_test`, `#e2e_test`, `#manual_test`. (Under rule 2, `#unit_test`/`#integration_test` appear under dev tasks; `#e2e_test`/`#manual_test` under tester tasks.)
6. **Estimates + mandatory close-out task.** Estimate each task in **decimal hours** — e.g. `1.0h`, `2.0h`, `4.0h`. Never days or T-shirt sizes. **Always end with a single mandatory close-out task** (role `#tester-role`) whose duties are: **update all documentation** per the plan's Documentation update section (link to the plan file as a markdown link — e.g. [plan-name.md](./plan-name.md) — do not wrap the link in backticks); **fix all warnings**, if any; **run the full test suite and fix every failing test**, even tests unrelated to this feature; and **validate each of the plan's Acceptance criteria one-by-one with a fact check** — no assumptions, verify everything is genuinely done.
7. **Every task sits inside a phase — nothing floats.** Every task (dev *and* tester) lives under a phase. A test delivers no value on its own, so place each tester e2e/manual task in the **slice whose scenario(s) it verifies**; if its scenarios span several slices, the slice that completes the **last** of them (whole-feature regression → Close-out). A dev/foundation task with no `S#` belongs to the slice whose behaviour it enables. **Never collect tester tasks into a separate non-phase bucket**, and make the Mermaid dependency-graph subgraphs match the phases one-to-one (no extra tester subgraph). Kickoff and Close-out are the only non-slice phases.
8. **KISS form, AAA world-class coverage.** Lean one-liners, no padding — and at the same time no missing task, scenario coverage, or test. Cut words, never cut coverage.

## Generation process

0. **Gate** the input (above): locate the team plan; stop and ask if none exists.
1. **Read the plan, decide slicing tooling.** Parse the plan's frontmatter (`architecture:`, `roles`), its Contracts (`C#` + logical summaries), Scenarios (`S#`), Frontend/Backend/Tester scope + Allocation table, Documentation update checklist. Check whether the **`vertical-slicer`** skill is installed (it appears in your available skills); if it is, load it — its `references/` paths are relative to the skill directory the harness reports when it loads. **Announce to the user** which slicing approach you're using.
2. **Investigate (parallel subagents).** Read `references/task-grounding-agents.md` and spawn **three read-only investigation subagents** — `backend-grounding`, `frontend-grounding`, `tester-grounding` — injecting: the plan's architecture approach + layers, its Contracts/Scenarios (so dependency order and tests can cite real `C#`/`S#`), and the shared investigation discipline (codebase-first, cite reality, read-only, no guessing, open questions). Re-run fresh — do not trust the plan's prose alone. Wait for all three before synthesising.
   - **Fallback (no subagent/Task tool):** perform the same three investigations yourself, inline, with the same discipline.
3. **Tasks** — from the **backend** and **frontend** findings (and the plan's Allocation table for tester tasks). Each task names the real files/components it touches and follows existing patterns; order tasks to match how the code is structured. For each: role, layer (or `—` for tester), decimal-hour estimate, `needs`, `completes` (citing the plan's `S#`/`C#`), and a `Tests` block — **unit and integration tests for dev tasks (test-first); e2e/manual for tester tasks; empty for Kickoff/Close-out.** If a grounding agent surfaces an unknown that the plan has not addressed, note it as a labelled open question at the end of the task file (`**Open questions**` section, `Q#` numbering continuing from the plan) and tell the user to reconcile it with the plan before marking the affected task in-progress.
4. **Slices + order.** Group tasks into vertical-slice phases using the active slicing tooling (rule 1): with `vertical-slicer`, apply its behaviour-first order and verticality self-check to set the phase boundaries, their order, and the walking skeleton; otherwise slice with the built-in method. Walking-skeleton-first; order within each by dependency. **Place every task — including each tester e2e/manual task — inside the slice whose scenario it verifies; never bucket tester tasks outside the phases (rule 7).** Add Kickoff (first, completing the plan's `C#`s) and the mandatory Close-out task (last, rule 6). Build the Mermaid dependency graph from every task's `needs`, one subgraph per phase. State the critical path.
5. **Self-check** (below), then write `<feature-name>-tasks.md` and present it.
6. **Back-link the plan.** After writing the tasks file, update the plan's `## References` section to point back at this file (bidirectional link). Add/refresh a single bullet `- **Tasks:** [<feature-name>-tasks.md](./<feature-name>-tasks.md)` (markdown link, to match the plan's existing `**Brief:**` bullet style). Idempotent: if that bullet already points to this filename, skip and say so; if it points to a stale name, replace it; if the References section has no `**Tasks:**` bullet, add it under the heading. Announce `Updating <feature-name>-team-plan.md — adding back-link to the tasks file.` before writing.
7. **Link audit.** Re-read `<feature-name>-tasks.md`. Scan **all prose in the tasks file** for bare file paths that refer to real **target-project** files. A token is a file-path candidate only if it contains a path separator (`/`) or a file extension — a bare identifier with neither is a code symbol, skip it. For each bare path found: rewrite it as `[filename](./relative/path)`. The plan and brief exist in the same folder as the tasks file (written there by Step 0/Step 5) — verify their links resolve with a filesystem check; the Step 2 grounding agents investigated the target codebase, not these sibling planning files, so do not rely on their findings for plan/brief paths. **Exclusions (do not scan inside):** fenced code blocks (` ``` `), inline code spans, front-matter YAML fields, Mermaid diagram source. **Do not treat as file paths:** component names, type names, layer names, or service names (code symbols). Skill-internal files (references/…, assets/…) and planned-but-not-yet-created files are excluded. Also verify that existing Markdown links in the tasks file point to files that actually exist on disk. If a link cannot be resolved, report it as a warning to the user — do not add a Q# open question.
8. **Estimate the task list (post-write).** Load the `estimate` skill and run `/estimate <tasks path> --from <plan path>` (both relative to the current directory; `--from` names the team plan, so its feature file under `.harness-goodies/estimations/` is updated, not duplicated). Write the SP it returns into the tasks file as the line immediately after the H1 title line, replacing any earlier one: `**Story Points:** <SP> — estimated <YYYY-MM-DD> with /estimate`. The tasks file keeps the SP of the moment it was written; its feature file under `.harness-goodies/estimations/` always holds the latest. A failed estimate is reported to the user and leaves the file without the line.

## Task-block grammar

Every task is a checkbox with sub-bullets, in this exact shape:

```markdown
- [ ] **ID** — one-line description of what to accomplish #role-tag
    - Layer · 2.0h
    - needs ID, ID · completes S#/C#
    - Tests
        - #unit_test — `test_functionName` — one line on what it covers
        - #integration_test — `test_anotherCase` — ...
```

- `Layer` is the plan's active architecture approach's structure unit (a Clean Architecture layer by default). **Tester tasks** have no layer → write `— · 2.0h`; their tests use `#e2e_test` (with a `` `test_fn` `` name) or `#manual_test — Case name — description` (manual has no code function, so no backticks).
- **Kickoff** and the **Close-out** task: include `needs`/`completes` as applicable and an **empty** `Tests` block. The Close-out task lists its four duties under a `Duties` sub-bullet (plain items, not checkboxes).
- **`needs` when empty:** omit the `needs` segment entirely — do not write `needs —`. Write only `completes S#/C#` on its own line.
- Estimates are decimal hours only (`1.0h`, `2.0h`, `4.0h`, …).

## Task granularity

A task is a single coherent piece of work for one role that a reviewer can check in one sitting.

- **Split** when a task would span more than one layer, cover unrelated behaviours, or exceed roughly **1–2 days** — or when two behaviours in the same (role × layer) cell have **different dependencies**.
- **Do not over-split.** A change and its 2–4 tests are **one** task, not one task per test. Never create a task for a single trivial test or a one-line change.
- Aim for **~2–4 tests per implementation task**.

## Output structure

See `assets/task-breakdown-template.md` for the fill-in skeleton and `references/example-tasks.md` for a complete worked example. Headings carry **no numbers**.

1. Front-matter (`feature`, `plan`, `status`)
2. Title + "How to read this file" (states the active slicing tooling — `vertical-slicer` or built-in — and the companion plan file)
3. **References** — **relative** markdown links only (`[text](./path)`, matching the plan's style — never absolute or `~/` paths) to the plan and brief, placed **before** Task Breakdown so the reader sees them first. Brief filename comes from the plan's frontmatter `brief:`. Instruct the reader to **always read the plan before planning the next task** for full context.
4. **Task Breakdown** — dependency graph (Mermaid, one subgraph per phase) → vertical-slice phases holding **all** dev and tester tasks (every task under a phase; no separate tester bucket) → mandatory Close-out task → critical path

## Final self-check (run before writing the file)

- [ ] The slicing tooling (`vertical-slicer` vs built-in) was **announced** to the user.
- [ ] Component/file names and dependency order match the Step 2 investigation (real paths/symbols) — not guesses.
- [ ] Every scenario `S#` from the plan appears in at least one task's `completes` (no coverage gap); every contract `C#` appears in at least one task's `completes`.
- [ ] Every task is single-role, has a decimal-hour estimate, and `needs` references only task IDs that exist in this file.
- [ ] **Unit and integration tests appear only under dev (BE/FE) tasks; e2e and manual only under tester tasks.** Every `#manual_test` either has a companion `#e2e_test` in the same task, or carries a `(non-automatable: <reason>)` annotation confirmed by the tester-grounding agent (rule 2).
- [ ] Phases are vertical slices set with the active slicing tooling; the first is a walking skeleton carrying the data foundation; there is no horizontal "integrate" phase.
- [ ] **Every task sits under a phase; no tester (e2e/manual) task floats in a separate bucket** — each is in the slice whose `S#` it verifies, and the dependency-graph subgraphs match the phases one-to-one (rule 7).
- [ ] A **References** section (markdown links to plan + brief, "read the plan first") sits **before** Task Breakdown.
- [ ] The plan file's `## References` was back-linked to this tasks file (bidirectional; idempotent).
- [ ] *(post-write, after step 7 — Link audit)* All prose in the tasks file has been scanned for bare target-project file paths (tokens with a path separator or file extension); bare paths were rewritten as Markdown links; existing links verified to exist on disk; any unresolvable link reported as a warning.
- [ ] The breakdown ends with the mandatory **Close-out** task covering documentation (citing the plan file), warnings, full test suite, and one-by-one acceptance fact-check.
- [ ] Headings carry no numbers. Role tags end each task title; test-type tags start each test line. No code in prose.
