# Investigation agents (Step 1)

A plan is only valid if it matches the real code. Before synthesising any section, gather ground truth from the **codebase (primary source of truth)** and the **documentation (intent + gaps)**.

Spawn **one read-only subagent per brief below — in parallel**. Each subagent **writes its findings to `<work-dir>/<agent-slug>-findings.md` and returns only a pointer** (see the **Findings to disk** rule below) — the findings body stays out of the orchestrator's context. Wait for every pointer before synthesising. If no subagent / Task tool is available, run each investigation **inline yourself, in order**, still writing each findings file. Agents `data schema`, `ADR`, and `docs` may produce empty findings if the project has no database, ADRs, or documentation — note the gap and continue.

Agent slugs (findings filenames): `contracts`, `scenarios`, `backend-architect`, `frontend-architect`, `data-schema`, `adr`, `docs`, `tester`, `structured-output`.

**`plan-maker-for-team`** runs all agents in this roster. Each agent's section heading below names the plan section it grounds.

---

## What each prompt carries

The orchestrator gives each subagent prompt just two things — it does **not** copy the brief into a second file or inline its text (the brief is already a file on disk from Step 0):

1. The path to the canonical brief `<feature-name>-brief.md` — the agent **reads it directly**.
2. A short **run header**:
   - **Repository root:** {{repo path}}
   - **Active architecture approach:** {{Clean Architecture (default), OR the user-requested skill (e.g. `dotnet-vsa-webapi`) + a note of its structure units}} — classify everything by THIS approach's layers, not Clean Architecture when an override is active.
   - **Contract tooling:** {{TypeSpec available → internal seams as core-construct `.tsp`, HTTP/API seams as TypeSpec HTTP services emitting `openapi.yaml`; OR not available → built-in simplified logical contracts}}.

The agent reads its own brief section (below) for what to investigate; all shared discipline is in the rules that follow.

- **Rules every investigation subagent follows:**
  - **Investigate only.** Gather facts to ground a team development plan; do NOT write the plan and do NOT write code.
  - **Test ownership (so you investigate the right things):** unit AND integration tests are written by the implementing dev; the tester owns e2e + manual only.
  - **Codebase first, docs second.** The code is the primary source of truth. Use docs (README, ADRs, `/docs`, design notes) to confirm intent and fill gaps.
  - **Cite reality.** For every claim, reference the exact file path and symbol name. No claim without a location.
  - **Read-only w.r.t. the project.** Do not modify, create, or delete any existing project file, and run no mutating commands against the repo. Your **sole permitted write** is your own findings file at `<work-dir>/<agent-slug>-findings.md`.
  - **No guessing.** If the brief assumes something you can't find, report it as an **OPEN QUESTION** — do not invent it.
  - **Use the active approach's vocabulary.** Classify by its structure units (Clean Architecture layers by default, or the override skill's units). Note the FE/BE deployment boundary (single-tier client vs client+server).
  - **Findings to disk, pointer back.** Write your structured bullets to `<work-dir>/<agent-slug>-findings.md` — each led by the real path/symbol, ending with an "Open questions" list; lean, one line per bullet, no narration. **Return to the orchestrator only** a one- to two-line pointer: a one-sentence summary, the findings-file path, and the open-question count. Never return the findings body.

---

## Contracts agent  → Contracts / seams
**Goal:** identify the real boundary seams two roles must agree on.
**Find:**
- Existing protocols / interfaces / service boundaries at layer edges.
- The current shape of data models that will change.
- Any client↔server API surface — **classify each seam** if TypeSpec is the active tooling: an **HTTP/API seam** (REST/client↔server — gets a TypeSpec HTTP service `.tsp` and an emitted `openapi.yaml`) or an **internal logical seam** (in-process interface — core-construct `.tsp`, no OpenAPI).
- Existing call sites that depend on each boundary (for blast-radius).
**Return:**
- Per seam: the real type/protocol/model name, its current shape, what must change, and who depends on it.
- Per seam: whether it is an **HTTP/API seam** (→ OpenAPI) or an **internal logical seam**.
- Unknowns as open questions.

## Scenarios agent  → Scenarios
**Goal:** ground the behaviour list in what the code and docs already do, so scenarios match reality and edges aren't missed.
**Find:**
- Current happy-path behaviour for this area.
- Existing edge / error / lifecycle handling (interruptions, route changes, persistence, migrations, etc.).
- Relevant spec / acceptance docs.
- Behaviours the brief assumes that already exist — or are missing.
**Return:**
- Confirmed current behaviours (with paths), edge cases already handled, risks/gaps, and brief-assumed behaviours not yet supported (→ open questions).

## Backend architect agent  → Backend role section
**Goal:** deep-dive the backend code to map service layer patterns, domain models, repository patterns, and component interactions for this feature area.
**Find:**
- Active service layer patterns and how the feature's backend components fit them — real service/use-case classes and their relationships.
- Domain model: entities, value objects, and aggregates in the affected area; their field-level shape and relationships.
- Repository and adapter patterns used for persistence and external integrations.
- How existing backend components interact (dependency graph among backend layers).
- Key architectural invariants and patterns the plan must preserve or extend.
**Return:**
- A map of service layer → domain model → repository/adapter interactions, with real file paths and symbol names.
- The existing patterns this feature should follow.
- Architectural risks or concerns the plan should address.
- Unknowns as open questions.

## Frontend architect agent  → Frontend role section
**Goal:** deep-dive the frontend code to map component hierarchy, state management, routing, and UI patterns for this feature area.
**Find:**
- Component hierarchy: the view tree in the affected area; parent/child relationships.
- State management: where state lives, how it flows, what state management pattern is in use.
- Routing: how navigation is wired for the affected screens/flows.
- UI patterns and conventions this feature must follow (layout, interaction, theming).
- How the UI connects to the service/use-case boundary (the handoff seam).
**Return:**
- Component hierarchy map with real file paths and symbol names.
- State management and routing patterns to follow.
- Architectural concerns or constraints for the plan.
- Unknowns as open questions.

## Data schema agent  → Data section; Contracts / seams
**Goal:** map the database schema, entity relationships, and identify any migrations this feature requires.
**Find:**
- All tables / collections / models in the affected area: their columns/fields, types, and constraints.
- Relationships between entities (one-to-many, many-to-many, foreign keys, etc.).
- Existing migrations and migration tooling in use.
- Which schema entities form explicit contract boundaries (e.g. shared DTOs, API response shapes) — these also inform the contracts agent.
- What schema changes this feature requires and in what order.
**Return:**
- A structured entity list (name, fields, relationships) using real table/model names from the codebase.
- Relationship edges between entities.
- Migration changes needed and the tooling conventions.
- Which schema entities form contract boundaries (flagged for Contracts / seams).
- Open questions if the schema cannot be confirmed.
- If the project has no database, report this clearly — no failure.

## ADR agent  → Prior decisions subsection
**Goal:** discover Architecture Decision Records (ADRs) in this repository and identify the ones that constrain or inform this feature.
**Find:**
- The ADR location in the repository (common paths: `docs/adr/`, `docs/decisions/`, `adr/`, `architecture/decisions/`, etc.).
- All ADRs relevant to the feature's affected area.
- For each relevant ADR: its title, decision, rationale, and status.

**Return** your findings as structured blocks — one block per ADR — immediately usable by the synthesis step:

```
decision: {{the decision made}}
rationale: {{why the decision was made}}
status: active|superseded
constraint: {{the constraint this imposes on the current feature, or "none" if none}}
```

Parsing rules the synthesis step applies:
- A value spans from its key to the next bare key at the start of a line, or end of block — multi-line values are valid.
- **First, check for excluded statuses:** `proposed` and `draft` ADRs are excluded entirely — not yet decided, so no constraint to impose. Discard these ADR blocks before applying any other rule.
- **Then map the remaining statuses:** `accepted|approved` → `active`; `deprecated|rejected|superseded|obsolete` → `superseded`; `active` and `superseded` pass through directly.
- **Unrecognised status fallback:** any status not in the mapping above maps to `active` with `(status: [original value])` appended to the `constraint:` field (not the `decision:` field).
- Pipe characters (`|`) in field values are escaped as `\|`.
- An ADR is included in `### Prior decisions` when `constraint:` is non-empty and non-`"none"` — regardless of whether status is `active` or `superseded`.

If no ADRs exist in the project, report this clearly — no failure.

## Docs agent  → Contradictions synthesis input; Background; Known limitations
**Goal:** discover what the project's documentation says about the area this feature touches — to ground the plan's Background and Known limitations, and to provide input to the contradiction-detection synthesis step.
**Find:**
- READMEs, design docs, wikis, and architecture notes covering the affected area.
- What each document says this area does or should do.
- Any discrepancy between what a document says and what the code does — report as a factual observation only; do NOT draw contradiction conclusions. The synthesis step produces `### Contradictions`.
**Return:**
- Per document: its real path, what it says about the affected area (concise bullet), and any factual discrepancy with the code.
- A list of documents consulted for background context only (no discrepancy found).
- Open questions if important documents cannot be found.
- If no meaningful documentation exists, report this clearly — no failure.

## Tester agent  → Tester allocation table
**Goal:** discover the real test setup so the allocation and the **tester's e2e + manual** tasks match what is actually possible. (Unit + integration are dev-owned; you focus on e2e and manual.)
**Find:**
- Existing e2e / UI test targets and manual-test conventions; the frameworks used; how tests run.
- Whether device-only behaviours (interruptions, route changes, permissions) are automatable in this harness or must be manual.
- Existing test helpers / fixtures to reuse for e2e.
- Whether any data migration this feature requires is testable in the e2e harness, or must be manual.
**Return:**
- The e2e and manual capabilities available and their conventions.
- For each anticipated scenario, the cheapest proving level **actually achievable here**, flagging which must be manual.
- Explicit answers to automatability questions (e.g. "can interruption be automated?", "is the data migration testable in e2e?").
- Gaps / unknowns as open questions.

## Structured-output agent  → Approach & architecture + Architecture diagram + Actors & Use Cases diagram

**Goal:** confirm the layer-presence map, FE/BE role mapping, and deployment tier; enumerate all actors and use cases the feature touches; inventory all components in the affected area with their layers; and map the dependency edges between components. This output grounds the Approach & architecture layer map and drives the coloured architecture diagram and actor–use-case map in the plan.

**Find:**
- Which of the **active approach's layers** are present in this repo and where each lives (root folder/module conventions) — the **layer-presence map**. If an override skill is active, follow **its** structure units.
- Whether the app is **single-tier (client only) or client+server**, and the **FE/BE role mapping** (which layers are frontend vs backend).
- All **actors** (user personas, system agents, external services) that interact with the feature area.
- All **use cases** (user-visible capabilities or system actions) the feature adds, changes, or removes, plus unchanged use cases that provide context.
- All **components** in the affected area: for each, its name, the layer it belongs to (active architecture approach), and whether it currently exists in the codebase (`exists`), is named in the feature brief as new but not yet present in the codebase (`planned`), or has been identified in the feature brief as removed (`removed`).
- The **dependency edges** between components (which component depends on which). Investigate the component dependency graph independently — you are the authoritative source for the DEPENDENCIES section used by the diagram synthesis step.

**Rules:**
- Report `status: "exists"` for every component confirmed present in the codebase.
- Report `status: "planned"` for components the feature brief explicitly names as new that are NOT yet present in the codebase (they don't exist yet; cannot be confirmed with `exists`).
- Report `status: "removed"` for components the feature brief explicitly names as removed, even if they no longer exist in the codebase and cannot be confirmed there.
- **Schema entities** (database tables, collections, or models confirmed by the data-schema agent) are also COMPONENTS. Use `layer: Frameworks & Drivers` unless the active architecture approach specifies otherwise.
- **Do not** infer change-set tags (new/modified/unchanged/removed) — that is the synthesis step, not your job. Report raw existence status only.
- Report every component in the affected area, including ones not mentioned in the feature brief (they will be tagged `unchanged` by the synthesis step).

**Return** your findings as the sections shown below. The three structured sections `ACTORS_AND_USE_CASES`, `COMPONENTS`, and `DEPENDENCIES` drive the diagrams and are parsed strictly — any missing or malformed one causes the corresponding diagram to be omitted with a parse-failure note, and a missing `DEPENDENCIES` section specifically causes the architecture diagram to be deferred. The `ARCHITECTURE_CONTEXT` block grounds the layer map, role mapping, and tier boundary (prose bullets, not strictly parsed).

```
ARCHITECTURE_CONTEXT
- layers present: Presentation (lib/ui/), Use Cases (lib/domain/), Interface Adapters (lib/data/), Entities (lib/domain/models/), Frameworks & Drivers (lib/infra/)
- tier: client+server
- role mapping: Presentation → frontend; Use Cases, Interface Adapters, Entities, Frameworks & Drivers → backend

ACTORS_AND_USE_CASES
- name: User, type: actor
- name: Select single sound, type: use_case
- name: Select multiple sounds, type: use_case
- name: Start timer with sounds, type: use_case

COMPONENTS
- name: TrackListView, layer: Presentation, status: exists
- name: AudioService, layer: Interface Adapters, status: exists
- name: MusicPreference, layer: Entities, status: exists
- name: AVAudioPlayerPool, layer: Frameworks & Drivers, status: planned
- name: LegacyAudioEngine, layer: Frameworks & Drivers, status: removed
- name: TracksTable, layer: Frameworks & Drivers, status: exists

DEPENDENCIES
- from: TrackListView, to: AudioService
- from: AudioService, to: MusicPreference
```

End with an "Open questions" list for anything you cannot confirm.

---

## Synthesis derivation fixture

The skill derives change-set tags (new/modified/unchanged/removed) from the structured-output agent's findings by comparing against the feature brief's stated scope. This fixture makes the derivation logic explicit and falsifiable.

**Part (a) — raw agent output:**

```
COMPONENTS
- name: AVAudioPlayerPool, layer: Frameworks & Drivers, status: planned
- name: AudioService, layer: Interface Adapters, status: exists
- name: MusicPreference, layer: Entities, status: exists
- name: LegacyAudioEngine, layer: Frameworks & Drivers, status: removed
```

**Part (b) — feature brief excerpt:**

```
In Scope:
- Add AVAudioPlayerPool to manage concurrent playback (new component)
- Update AudioService to fan out to the player pool (modified)
```

**Part (c) — expected synthesis output:**

| Component | status (from agent) | Derived tag | Derivation rule |
|-----------|---------------------|-------------|-----------------|
| AVAudioPlayerPool | planned | **new** | Direct pass-through: planned = not yet in codebase, named as new in brief |
| AudioService | exists | **modified** | Named as modified in the brief |
| MusicPreference | exists | **unchanged** | Exists but not mentioned as new or modified |
| LegacyAudioEngine | removed | **removed** | Passed through directly from agent status |

**Synthesis rules (applied in this order):**
1. `status: "removed"` → tag `removed` (direct pass-through; no brief comparison needed).
2. `status: "planned"` → tag `new` (direct pass-through: not yet in codebase, named as new).
3. `status: "exists"` + named in brief as new or added → tag `new` (a stub or placeholder exists, but the brief treats this as a new component).
4. `status: "exists"` + named in brief as changed/modified → tag `modified`.
5. `status: "exists"` + not mentioned in brief as new, added, or modified → tag `unchanged`.

If the agent output is missing `ACTORS_AND_USE_CASES`, `COMPONENTS`, or `DEPENDENCIES`, the synthesis step records a parse-failure note in the affected plan subsection: `_Diagram omitted: investigation output did not conform to the required structured format._` A missing `DEPENDENCIES` section specifically triggers: `architecture diagram deferred — component set unconfirmed`.
