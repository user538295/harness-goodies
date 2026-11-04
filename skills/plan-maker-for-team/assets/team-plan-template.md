---
id: {{SHORT-ID}}
feature: {{Feature Name}}
brief: {{feature-name}}-brief.md
purpose: {{one sentence: what users can do after this ships}}
audience: {{who uses it, in what context}}
status: draft        # draft (open questions remain) → planned → in-progress → done
roles: [frontend, backend, tester]
architecture: {{clean | the requested architecture skill, e.g. dotnet-vsa-webapi}}
---

# {{SHORT-ID}} · {{Feature Name}} — Team Plan

**How to read this file**
- **Architecture approach:** {{Clean Architecture (default fallback) | the requested skill}}. **Layers:** {{Presentation · Use Cases · Interface Adapters · Entities · Frameworks & Drivers | the active approach's structure units}}.
- The **Frontend, Backend, and Tester** sections are the **depth view** — each role's scope, grouped by layer.
- **Contracts** are logical: authored as linked `.tsp` files when TypeSpec is available (HTTP/API seams also emit a linked `openapi.yaml`), otherwise the built-in simplified form inline.
- **Role tags** (`#frontend-role`, `#backend-role`, `#tester-role`) mark each role-owned section.
- IDs (`S#`, `C#`, `Q#`) are the traceability thread.
- **Tasks** are not in this file — task breakdown is a separate downstream step that consumes this plan.
- **Rule:** change a contract only by team agreement.

---

## Background
{{1–2 sentences: how things work today, before this feature}}

---

## Goal
{{the end state after shipping, in user terms}}

---

## Scope

### In Scope
- {{...}}

### Out of Scope
- {{...}}

---

## Acceptance criteria
- {{verifiable statement}}
- ...

---

## What does NOT change
- {{existing behaviour/components explicitly preserved}}

---

## Known limitations / accepted trade-offs
- {{intentional v1 trade-off}}

---

## Approach & architecture

{{1–2 sentences on the overall approach, in the active architecture's terms.}}

### Architecture

<!-- If COMPONENTS or DEPENDENCIES was missing from the structured-output agent, write the line below and omit the diagram: -->
<!-- architecture diagram deferred — component set unconfirmed -->

```mermaid
flowchart TD
  classDef newNode fill:#a8d5a2;
  classDef modNode fill:#f9e4a1;
  classDef rmNode fill:#f4a4a4;

  {{ComponentA}}["{{ComponentA}} *(new)*"]
  {{ComponentB}}["{{ComponentB}} *(mod)*"]
  {{ComponentC}}["{{ComponentC}}"]
  {{ComponentA}} --> {{ComponentC}}
  {{ComponentB}} --> {{ComponentC}}

  class {{ComponentA}} newNode;
  class {{ComponentB}} modNode;
```
<!-- If > 15 nodes, apply bounded-context scoping (changed nodes + 1-hop neighbours) and add caption immediately after the block: -->
<!-- _Scope limited to change neighbourhood: [rationale]_ -->
<!-- If 1-hop expansion also > 15, include changed nodes only and add: -->
<!-- _Scope limited to changed nodes only: 1-hop expansion exceeded the node limit._ -->
<!-- If an override architecture skill is active, use its structure units instead of Clean Architecture layers. -->

<!-- Companion table: include when ≥2 components are new/modified/removed. -->
<!-- When exactly 1 component changed, omit table and add caption: -->
<!-- _New: [Name] — [one-line reason]_  /  _Modified: [Name] — [reason]_  /  _Removed: [Name] — [reason]_ -->

| Component | Change | Why |
|-----------|--------|-----|
| {{ComponentName}} | new / modified / removed | {{one-line reason}} |

**Layer map (and role mapping)**

| Layer | Role | Components |
|-------|------|-----------|
| Presentation | **Frontend** | {{...}} |
| Use Cases | Backend | {{...}} |
| Interface Adapters | Backend | {{...}} |
| Entities | Backend | {{...}} |
| Frameworks & Drivers | Backend | {{...}} |
<!-- For a client+server app, split layers across FE/BE along the client/server boundary and add the API as a contract. -->

**What changes**
- {{...}}

**Key decisions (from the brief)**
- {{...}}

### Actors & Use Cases

<!-- Include when ≥1 actor or use case is new, changed, or removed. Replace the skip line below with the Mermaid block when applicable. -->
_Skipped: no actor or use case is new, changed, or removed in this feature._

<!--
```mermaid
flowchart LR
  classDef newNode fill:#a8d5a2;
  classDef modNode fill:#f9e4a1;
  classDef rmNode fill:#f4a4a4;

  {{Actor}}["{{Actor}}"]
  {{UseCase}}(["{{Use Case}} *(new)*"])
  {{Actor}} --&gt; {{UseCase}}

  class {{UseCase}} newNode;
```
-->

### Flows

#### User Flow

<!-- Include when ≥1 user-facing step is added, removed, or reordered. -->
_Skipped: no user-facing step changes in this feature._

#### Data Flow

<!-- Include when ≥1 data edge (persistence, API call, migration) changes. -->
_Skipped: no data edge changes in this feature._

#### Sequence

<!-- Include when ≥2 components interact over ≥3 ordered steps. If > 8 participants, scope to primary user-facing flow and add caption after block. -->
_Skipped: no multi-step inter-component interaction in this feature._

### Prior decisions

<!-- Populated from ADR agent findings. Table columns: Decision, Rationale, Constraint. -->
<!-- Include only ADRs whose constraint: is non-empty and non-"none". -->
<!-- status: superseded appears as a parenthetical in the Decision cell. -->
<!-- Omit this subsection with an italicised note if no ADRs exist or all have constraint: none. -->
_No Architecture Decision Records found in this repository._

### Contradictions

<!-- Produced by synthesis: compare docs-agent findings against architecture/backend/frontend findings. -->
<!-- Two categories: code vs. docs (doc says X, code does Y) and brief vs. reality (brief assumes X, code has Y). -->
<!-- Each contradiction states an owner: "doc needs updating" / "code is the issue" / "open question". -->
<!-- Omit this subsection with an italicised note only when both categories are empty. -->
_No contradictions detected between code, documentation, and brief assumptions._

---

## Contracts / seams

Boundaries where roles must agree. **Logical, not code.** Changing one requires team agreement.
<!-- If TypeSpec is available: author internal logical seams as core-construct `.tsp` files beside this plan (validate with `tsp compile <file> --no-emit`); author HTTP/API seams as TypeSpec HTTP services under `api-contracts/` and emit `<contract>.openapi.yaml` (link both). Otherwise use the built-in simplified form. State which you used. -->

**C1 — {{name}}**  *({{Layer ↔ Layer}})*
{{what crosses the boundary; what each side promises}}  {{— see `{{contract}}.tsp` (+ `api-contracts/{{contract}}.openapi.yaml` for an HTTP/API seam) if TypeSpec used}}

<!-- Add C2, C3 … as needed. -->

---

## Data

<!-- Populated from data schema agent findings. -->
<!-- Include a Mermaid erDiagram when the project has a database. -->
<!-- Changed entities carry plain-text (new) / (mod) / (rm) label suffixes in their quoted name, e.g. users["users (mod)"]. -->
<!-- Do NOT add classDef or class statements — erDiagram does not support them and they cause a parse error. -->
<!-- Removed/added relationships use plain-text (rm) / (new) appended to the label, e.g. "has (new)" — relationship labels do not support markdown formatting. -->
<!-- Apply node-count rule: scope to changed entities + 1-hop neighbours if full schema > 15 nodes; add caption. -->
<!-- If the project HAS a database: remove the skip note below and uncomment the diagram block. -->

_This project has no database — Data section skipped._

<!--
```mermaid
erDiagram
  {{Entity}}["{{Entity}} (new)"] {
    {{type}} {{field}}
  }
  {{Entity}} ||--o{ {{OtherEntity}} : "{{relationship}}"
```

**Migration notes**
- {{migration change needed}}

**Entity model changes**
- {{entity change description}}
-->

---

## Scenarios #tester-role

Behavioural only — step-level detail is produced by the tasks below. Cover happy, unhappy, edge, and non-functional paths.

| id | Scenario (Given / When / Then) |
|----|--------------------------------|
| **S1** | **Given** ... · **When** ... · **Then** ... |
| **S2** | ... |

---

## Frontend — Presentation #frontend-role
<!-- If the feature has no frontend work, keep this heading and write: N/A — no frontend work for this feature. -->

**Scope:** {{...}}
**Owns layer:** Presentation.

**Done when**
- [ ] {{behavioural outcome}} — S#

---

## Backend — Entities · Use Cases · Adapters · Frameworks #backend-role
<!-- If the feature has no backend work, keep this heading and write: N/A. -->

**Scope:** {{...}} Writes both unit and integration tests for its tasks.
**Owns layers:** Entities, Use Cases, Interface Adapters, Frameworks & Drivers.

**Done when**
- [ ] {{behavioural outcome}} — S#

---

## Tester #tester-role

**Scope:** the tester owns **e2e and manual** tests plus the project close-out. **Unit and integration** tests belong to the implementing dev, in each implementation task's `Tests` block.

**Allocation** — each scenario at the cheapest level that proves it *(unit + integration are dev-written; e2e + manual are the tester's tasks)*

| Scenario | Cheapest level |
|----------|----------------|
| S# | unit / integration / e2e / manual |

---

## Documentation update

Docs the feature touches — the tasks file's close-out task works through this list. List only real files. Each file carries a reason: *new feature* / *contradiction with code* / *no change needed*.

- [ ] [{{feature-name}}-brief.md](./{{feature-name}}-brief.md) — *no change needed* (source brief)
- [ ] [{{feature-name}}-team-plan.md](./{{feature-name}}-team-plan.md) — *new feature* (this file)
- [ ] [{{filename}}]({{relative/path/to/doc}}) — *new feature* / *contradiction with code* / *no change needed* — (one-line note if needed)

**Consulted (read-only)**
- [{{filename}}]({{relative/path/to/doc}}) — {{why consulted}}

---

## Open questions

Resolve before committing (status moves `draft → planned`). Copy every open question from the brief; add any uncertainty hit while planning or surfaced by the investigation.

| id | Area | Question |
|----|------|----------|
| **Q1** | {{feature / architecture / tests}} | {{...}} |

<!-- After building, resolve any question the plan now answers and move it to a "Resolved in this revision: ..." note. -->

---

## References

- **Brief:** [{{feature-name}}-brief.md](./{{feature-name}}-brief.md)
<!-- The `create-tasks` skill appends a `- **Tasks:** [{{feature-name}}-tasks.md](./{{feature-name}}-tasks.md)` bullet here when the task breakdown is generated. -->


