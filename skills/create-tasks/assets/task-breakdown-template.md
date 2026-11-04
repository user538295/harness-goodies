---
feature: {{Feature Name}}
plan: {{feature-name}}-team-plan.md
status: draft        # draft → in-progress → done
---

# {{SHORT-ID}} · {{Feature Name}} — Task Breakdown

**How to read this file**
- This is the **order view** for `{{feature-name}}-team-plan.md` — every task is a single-role checkbox in execution order, opening with a dependency graph.
- **Phases are vertical slices**: each delivers a working end-to-end increment, not a horizontal layer. No separate "integrate" phase. Sliced with {{the `vertical-slicer` skill | the built-in method}}.
- Each task carries the **role tag at the end of its title line**, then sub-bullets: **layer · estimate** (decimal hours), **needs · completes**, and a **Tests** block. **needs** = predecessor tasks; **completes** = the scenario `S#` (from the plan) it makes true, or the contract `C#` (from the plan) it realises.
- **Tests** are tagged by level. **Unit and integration tests belong to the implementing dev** (test-first); **e2e and manual tests are the tester's tasks**. The close-out task writes no tests.
- IDs (`BE-#`/`FE-#`/`T-#`/`K#`) are this file's traceability thread; `S#`/`C#`/`Q#` are defined in the plan.
- **Rule:** edit your own tasks freely.

---

## References

- **Plan:** [{{feature-name}}-team-plan.md](./{{feature-name}}-team-plan.md) — the full team plan (contracts, scenarios, architecture, allocation). **Always read the plan before you start planning the next task** — it holds the context this file only cites (`S#`/`C#`/`Q#`).
- **Brief:** [{{feature-name}}-brief.md](./{{feature-name}}-brief.md) — the source feature brief behind the plan.

---

## Task Breakdown

Single-role tasks in execution order, grouped into **vertical slices**.

### Dependency graph
<!-- Build from every task's `needs`. One node per task; one subgraph per phase (no separate tester subgraph); A --> B means B needs A. The close-out task depends on all leaf tasks. -->

```mermaid
flowchart LR
  K1([K1 · align])
  subgraph P1["Phase 1 · {{slice name}}"]
    {{nodes}}
  end
  T_END([{{close-out ID}} · close-out])
  {{edges}}
```

### Phase 0 · Kickoff *(prerequisite; the one cross-cutting step)*
- [ ] **K1** — Agree the Contracts and Scenarios with the team #team
    - — · {{1.0h}}
    - completes {{C#, C#}}
    - Tests

### Phase 1 · {{slice name}} *(the walking skeleton: thinnest end-to-end path; carries the data/model foundation)*
- [ ] **BE-1** — {{one-line description}} #backend-role
    - {{Layer}} · {{2.0h}}
    - needs K1 · completes {{C#/S#}}
    - Tests
        - #unit_test — `test_{{name}}` — {{what it covers}}
        - #integration_test — `test_{{name}}` — {{what it covers}}
- [ ] **FE-1** — {{one-line description}} #frontend-role
    - Presentation · {{2.0h}}
    - needs K1 · completes {{S#}}
    - Tests
        - #unit_test — `test_{{name}}` — {{...}}
- [ ] **T-1** — {{e2e or manual description}} #tester-role
    - — · {{2.0h}}
    - needs {{task IDs}} · completes {{S#}}
    - Tests
        - #e2e_test — `test_{{name}}` — {{...}}
<!-- Add Phase 2, Phase 3 … as further vertical slices. Backend/frontend integration tests live in the dev tasks above; the tester adds only e2e/manual tasks, each placed in the slice whose scenario it verifies — never a separate tester phase or bucket. -->

### Phase {{N}} · Close-out
- [ ] **{{close-out ID, e.g. T-N}}** — Project close-out & acceptance fact-check #tester-role
    - — · {{4.0h}}
    - needs all prior tasks · completes (acceptance gate)
    - Tests
    - Duties
        - Update all documentation per [{{feature-name}}-team-plan.md](./{{feature-name}}-team-plan.md)'s "Documentation update" section — project docs, user manuals, architecture docs, `CLAUDE.md`, `AGENTS.md`, etc.
        - Fix all build / compiler warnings, if any.
        - Run the full test suite; fix every failing test, including any unrelated to this feature.
        - Validate every Acceptance criterion one-by-one (from the plan) with a fact check — no assumptions; confirm each is genuinely done.

**Critical path:** {{ID → ID → ID → ...}}.
