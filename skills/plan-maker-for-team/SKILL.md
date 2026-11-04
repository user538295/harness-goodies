---
name: plan-maker-for-team
description: Generate a structured, role-split team development plan in Markdown from a feature brief. Use this whenever the user wants to turn a feature brief or a feature request into an actionable engineering plan that splits work across frontend, backend, and tester roles, with a layered architecture, Given/When/Then scenarios, logical contracts between roles, a Mermaid layer diagram, a test-level allocation table, a documentation checklist, and Obsidian-friendly role tags. Trigger this skill when the user provides a brief file (often named like feature-name-brief.md), or asks to plan a feature for the team, make a team plan, or organize multi-role work — even if they do not say the word plan. If the input is only a short feature request or is missing the core brief sections (problem, goal, core flow, in scope, out of scope), refine it into a brief first. This skill produces the **plan only** — task-by-task breakdown is a separate downstream step.
license: MIT
---

# Plan Maker for Team

Turn a feature **brief** into one reviewable Markdown **plan** that splits the work across **frontend, backend, and tester**, with a layered architecture, scenarios, logical contracts, a test-level allocation table, and a documentation checklist.

A plan is only trustworthy if it matches the real code. So before writing anything, this skill **investigates the codebase and docs with read-only subagents** (Step 1), then synthesises the plan from what they find.

The output is a single file, `<feature-name>-team-plan.md`, written **in the same folder as the brief** (plus optional `.tsp` contract files beside it, and a generated `openapi.yaml` per HTTP/API contract — see the Contracts rule).

**This skill does not produce tasks.** The plan is the input to a separate downstream task-breakdown step.

## Read these first

Work runs in three context tiers so no single context holds everything (see **Context strategy**). Read only what your tier needs.

**Orchestrator (this context):** only `references/example-brief.md` (a real input brief, for the Step 0 gate). It spawns the agents using the verbatim prompts hardcoded in **Generation process Step 1**.

**Each investigation agent** (its own context): the verbatim prompt from Step 1 (which already contains its goal, instructions, and run header), plus the canonical brief `<feature-name>-brief.md`.

**The synthesis agent** (its own context): the findings files in the work directory, the brief, plus —
- `references/example-team-plan.md` — the exact plan generated from the example brief. Source of truth for the output format.
- `references/example-team-plan-fixtures.md` — nine golden-output fixtures for diagram verification.
- `references/typespec-tooling.md` — how to detect, author, and validate TypeSpec `.tsp` contracts, and emit an `openapi.yaml` for HTTP/API seams (only when TypeSpec is the active tooling; rule 5).
- `assets/team-plan-template.md` — the fill-in skeleton.

## Context strategy

One agent that reads the codebase, holds every finding, and loads the format examples at once fills a 200k context and forces compaction. So the work runs in three tiers that **never share a context window** — the steps live in **Generation process**; the invariant each tier holds is:

1. **Orchestrator** (this context) — holds pointers, never findings or format examples.
2. **Investigation agents** (Step 1, one context each) — their reading and findings stay in their own windows and the findings files; only a pointer comes back.
3. **Synthesis agent** (Step 2, one context) — reads the findings files + format examples in a fresh window.

**Work directory.** The orchestrator creates `<feature-name>-plan-work/` beside the brief and passes its absolute path to every agent; it holds one `<agent-slug>-findings.md` per investigation agent. These persist, so the synthesis phase can re-run standalone against an existing work directory — regenerating the plan **without re-investigating**, no separate command. Tell the user it can be deleted once the plan is accepted.

## Glossary

Spell these out; do not abbreviate them in the generated plan.

- **Architecture approach** — how the code is organised. **Default: Clean Architecture** (layers: Presentation → Use Cases → Interface Adapters → Entities → Frameworks & Drivers, dependencies inward). It is **overridable**: if the user asks to use a specific architecture skill, that skill's approach and its **structure units** (its "layers") are used instead. The active approach and its valid layers are stated in the plan's "How to read this file" block.
- **Layer** — the active approach's structure unit a piece of work touches. Under Clean Architecture the layers are Presentation, Use Cases, Interface Adapters, Entities, Frameworks & Drivers. An override approach defines its own (e.g. vertical feature slices).
- **AAA world-class quality** — the coverage bar: every meaningful path is covered — happy, unhappy, edge, and non-functional — with nothing important missing. It governs *completeness*; KISS governs *form*. Lean writing, complete coverage.
- **KISS** — keep the artefact simple: lean tables and one-liners, no padding.
- **Contract / seam** — a logical agreement at a layer (or client↔server) boundary that two roles must honour; changed only by team agreement.

## Step 0 — Gate the input (refine first if thin)

The input should be a brief named `<feature-name>-brief.md`. Before planning, confirm the brief adequately answers all five core questions:

| Section | Must answer |
|---|---|
| Problem | the user problem this solves |
| Goal | the end state after it ships |
| Core Flow | the main step-by-step user path |
| In Scope | what the feature includes |
| Out of Scope | what it explicitly excludes |

A section passes only if it is **present and substantive** — concrete sentences, not a placeholder or one vague word.

**If the input is only a short feature request, or a file that is missing or weak on any of these five**, do not plan yet. Tell the user the brief is insufficient and invoke the **`feature-refinement`** skill/command to produce `<feature-name>-brief.md`. Once it exists, continue from Step 1. Never invent the missing product intent — refine first.

## Output naming
- Derive `<feature-name>` from the brief filename (strip the `-brief` suffix) or its title.
- Write the plan to `<feature-name>-team-plan.md` in the **same folder as the brief**.

## Locked rules (the output must satisfy all)

1. **Fixed roles.** Always three role sections: Frontend, Backend, Tester. If a role has no work for this feature, keep its section and mark it **N/A** — never delete it.
2. **Architecture approach — Clean Architecture by default, overridable.** Default to **Clean Architecture**. **If the user explicitly asks to use a specific architecture skill** (e.g. `dotnet-vsa-webapi`), locate and read that skill, follow its approach, and use **its** structure units ("layers") throughout in place of Clean Architecture's. **If the named skill cannot be found, STOP and ask the user** — do not silently fall back. Whichever approach is active: **(a) state it explicitly to the user while you work** (they must know transparently whether you use the requested skill or the Clean Architecture fallback); **(b)** record it in the front-matter `architecture:` field and **name its valid layers in the "How to read this file" block**.
3. **Role mapping by the active approach.** Map the approach's structure units to roles. For Clean Architecture: Presentation → frontend; Use Cases, Interface Adapters, Entities, Frameworks & Drivers → backend. For an override approach, follow its mapping; if it does not specify one, keep UI/Presentation → frontend and the rest → backend. For a client+server app, split along the client/server boundary and add the client↔server API as a contract. Tester is cross-cutting. If the boundary is unclear, add an open question.
4. **Contracts — TypeSpec when available, else built-in; HTTP seams also emit OpenAPI; always logical, never implementation.** Detect and use TypeSpec per `references/typespec-tooling.md`. If **TypeSpec** is available on the machine:
   - **Internal logical seams** (in-process interfaces, non-HTTP) — author each as a `.tsp` file **beside the plan**, **core constructs only** (model/op/interface/namespace), each **validated with `tsp compile <file> --no-emit`**.
   - **HTTP/API seams** (client↔server, REST) — author each as a TypeSpec **HTTP service** `.tsp` in an `api-contracts/` subfolder beside the plan and **emit a `<contract>.openapi.yaml`** from it with the `@typespec/openapi3` emitter (per `references/typespec-tooling.md`). Link **both** the `.tsp` and the generated `.openapi.yaml` in the Contracts section. If the OpenAPI libraries cannot be installed, fall back to a core-construct `.tsp` (no YAML) and say so.

   In the Contracts section give a short **logical summary plus link(s)** to the artefact(s). If TypeSpec is not available at all, write the **built-in simplified logical contract** inline (the fallback). Either way, **tell the user which you are using** (TypeSpec — and whether OpenAPI was emitted — or the built-in fallback). No signatures/SQL/bodies in the plan prose.
5. **Scenarios are behavioural, Given/When/Then.** Cover happy, unhappy, edge, and non-functional paths (**AAA world-class quality** — nothing important missing). They live in the shared Scenarios section, tagged `#tester-role`. Keep them behavioural; step-level implementation detail is out of scope for the plan.
6. **Test ownership policy.** **Unit AND integration tests belong to the implementing dev; the tester owns e2e and manual tests only.** This governs the Tester section's Allocation table.
7. **Cheapest level that proves it.** Allocate each scenario, in the Tester section's Allocation table, to the lowest test level that can prove it; promote to e2e only for a critical end-to-end path. No inverted pyramid.
8. **No code; name real components, don't implement.** Naming a type/view/service is fine; method bodies, signatures, and SQL are not. **Every component, type, file, and layer name in the plan comes from the Step 1 codebase investigation under the active approach — not from guessing.** Never invent product behaviour the brief did not state; every such unknown becomes an open question.
9. **Stable IDs.** `S#` scenarios, `C#` contracts, `Q#` questions. Number them contiguously when first generated. On any later edit (insert, delete) **do not renumber** existing IDs — they are stable traceability anchors for anything downstream.
10. **Role tags.** Put the **role tag at the end of** each role-owned section heading: `#frontend-role`, `#backend-role`, `#tester-role`.
11. **KISS form, AAA world-class coverage.** Lean tables and one-liners, no padding — and at the same time no missing scenario, edge, or role. Cut words, never cut coverage.
12. **Status lifecycle.** `draft` while any open question remains → `planned` once all are resolved → `in-progress` → `done`.
13. **Documentation checklist.** Add a **Documentation update** section immediately before Open Questions: a checklist (`- [ ]`) of the real documentation files the feature touches (from the investigation). Each file carries a **reason**: *new feature* (doc must be created or updated for this feature), *contradiction with code* (doc conflicts with the real code), or *no change needed* (explicitly evaluated and accurate — no update required). Precedence when a file qualifies for multiple reasons: *contradiction with code* > *new feature* > *no change needed*. Each file appears at most once. Separately list files the docs agent read for background context only as **Consulted (read-only)**. List only real files; do not invent.
14. **File references are links.** Every mention of a real project file (source file, documentation file, ADR, migration, contract artefact, configuration file) **anywhere in the plan's prose** must use Markdown link syntax: `[filename](relative/path/to/file)`. A token is a file-path candidate only if it contains a path separator (`/`) or a file extension (e.g. `.md`, `.ts`, `.yaml`); a bare identifier with neither is a code symbol — do not link it.

   "Real project file" means a file in the **target project** (the project being planned for), confirmed to exist by the Step 1 investigation agents. Skill-internal files (references/…, assets/…), planned-but-not-yet-created files, and code symbols are excluded.

   **Explicitly excluded** from this rule: fenced code blocks (` ``` `), inline code spans, all front-matter YAML fields, and Mermaid diagram source. Component names, type names, layer names, and service names are code symbols, not file references — do not treat them as bare paths. Links must be relative to the plan file's location.

## Generation process (orchestrator)

The orchestrator runs these steps; the heavy reading and synthesis are delegated to subagents (see **Context strategy**).

0. **Gate** the input (above). Refine first if needed.
1. **Set up, then investigate (parallel subagents).**
   - **Architecture approach:** default Clean Architecture; if the user named an architecture skill, locate and read it (stop and ask if not found). **Announce the active approach to the user.**
   - **Contract tooling:** check whether TypeSpec is available — run `tsp --version` (exit `0` = available); see `references/typespec-tooling.md`. HTTP/API seams additionally emit an `openapi.yaml` via the `@typespec/openapi3` emitter (installed into an `api-contracts/` project). **Announce to the user** whether you are using TypeSpec (and whether OpenAPI will be emitted) or the built-in fallback.
   - **Create the work directory** `<feature-name>-plan-work/` beside the brief (it holds the findings files).
   - **Spawn all read-only investigation subagents** — `contracts`, `scenarios`, `backend-architect`, `frontend-architect`, `data-schema`, `adr`, `docs`, `tester`, `structured-output` — in parallel. The full prompt for each agent is written out below, so the orchestrator never needs to read `references/investigation-agents.md` at runtime. **Copy these prompts verbatim — do not modify them.** Each agent's prompt is the **common preamble** followed by that agent's **agent-specific block**; fill in only the `{{…}}` placeholders (agent slug, brief path, work-dir path, repo root, active approach + layers, contract tooling). Each agent investigates (**codebase first, docs second**, citing real paths/symbols, modifying nothing), **writes findings to `<work-dir>/<agent-slug>-findings.md`**, and **returns only a pointer** (one-line summary + open-question count; the path is the deterministic slug). Do not copy the brief into a file or inline its text.

     **Common preamble (the start of every agent's prompt):**

     ```
     You are the {{agent-slug}} read-only investigation agent gathering ground truth for a team development plan.

     Read the feature brief directly at: {{path to <feature-name>-brief.md}}

     Run header:
     - Repository root: {{repo path}}
     - Active architecture approach: {{Clean Architecture (default), OR the user-requested skill (e.g. dotnet-vsa-webapi) + a note of its structure units}} — classify everything by THIS approach's layers, not Clean Architecture when an override is active.
     - Contract tooling: {{TypeSpec available → internal seams as core-construct .tsp, HTTP/API seams as TypeSpec HTTP services emitting openapi.yaml; OR not available → built-in simplified logical contracts}}.

     Rules:
     - Investigate only. Gather facts to ground a team development plan; do NOT write the plan and do NOT write code.
     - Test ownership (so you investigate the right things): unit AND integration tests are written by the implementing dev; the tester owns e2e + manual only.
     - Codebase first, docs second. The code is the primary source of truth. Use docs (README, ADRs, /docs, design notes) to confirm intent and fill gaps.
     - Cite reality. For every claim, reference the exact file path and symbol name. No claim without a location.
     - Read-only w.r.t. the project. Do not modify, create, or delete any existing project file, and run no mutating commands against the repo. Your sole permitted write is your own findings file at {{work-dir}}/{{agent-slug}}-findings.md.
     - No guessing. If the brief assumes something you can't find, report it as an OPEN QUESTION — do not invent it.
     - Use the active approach's vocabulary. Classify by its structure units (Clean Architecture layers by default, or the override skill's units). Note the FE/BE deployment boundary (single-tier client vs client+server).

     Output: write your findings to {{work-dir}}/{{agent-slug}}-findings.md — structured bullets, each led by the real path/symbol, ending with an "Open questions" list; lean, one line per bullet, no narration. Return to the orchestrator ONLY a pointer: a one-sentence summary, the findings-file path, and the open-question count. Never return the findings body.
     ```

     **Agent-specific blocks (append the matching block after the preamble):**

     <details>
     <summary><code>contracts</code> agent prompt</summary>

     ```
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
     ```

     </details>

     <details>
     <summary><code>scenarios</code> agent prompt</summary>

     ```
     **Goal:** ground the behaviour list in what the code and docs already do, so scenarios match reality and edges aren't missed.
     **Find:**
     - Current happy-path behaviour for this area.
     - Existing edge / error / lifecycle handling (interruptions, route changes, persistence, migrations, etc.).
     - Relevant spec / acceptance docs.
     - Behaviours the brief assumes that already exist — or are missing.
     **Return:**
     - Confirmed current behaviours (with paths), edge cases already handled, risks/gaps, and brief-assumed behaviours not yet supported (→ open questions).
     ```

     </details>

     <details>
     <summary><code>backend-architect</code> agent prompt</summary>

     ```
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
     ```

     </details>

     <details>
     <summary><code>frontend-architect</code> agent prompt</summary>

     ```
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
     ```

     </details>

     <details>
     <summary><code>data-schema</code> agent prompt</summary>

     ```
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
     ```

     </details>

     <details>
     <summary><code>adr</code> agent prompt</summary>

     ````
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
     ````

     </details>

     <details>
     <summary><code>docs</code> agent prompt</summary>

     ```
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
     ```

     </details>

     <details>
     <summary><code>tester</code> agent prompt</summary>

     ```
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
     ```

     </details>

     <details>
     <summary><code>structured-output</code> agent prompt</summary>

     ````
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
     ````

     </details>
   - **Wait** for every pointer before synthesising.
   - **Fallback (no subagent/Task tool):** perform the same nine investigations yourself, inline and in order, writing each findings file, then run the synthesis instructions inline too (accepting the higher context cost). `data-schema`, `adr`, and `docs` may be empty if the project has no database, ADRs, or docs — note the gap and continue.
2. **Synthesize (one subagent).** Spawn a single synthesis agent. Give it: the work-directory path, the brief path, the output path `<feature-name>-team-plan.md`, the active approach + its layers, and the contract tooling decision. Instruct it to read the findings files plus the format references (see **Read these first**) and execute the **Synthesis agent instructions** below — producing the plan, back-linking the brief, running the link audit and self-check. It returns a short summary: status, open-question count, which diagrams were included or deferred, any link warnings, and the plan path.
3. **Report** the synthesis agent's summary to the user.
4. **Clean up.** After reporting to the user, delete the work directory `<feature-name>-plan-work/` — it is ephemeral and safe to delete permanently. Tell the user the work directory has been removed.

## Synthesis agent instructions

The synthesis agent executes these steps in its own context, reading each `<agent-slug>-findings.md` from the work directory. In the no-Task-tool fallback the orchestrator performs them inline. Ordering: the `scenarios` findings precede the Scenarios section; `tester` precedes the Allocation table; `structured-output` precedes the diagram synthesis step (1b); `data-schema` precedes the Data section; `adr` precedes `### Prior decisions`; `docs` is required before the contradiction-detection step that produces `### Contradictions`.

   - **1b. Synthesis — parse structured output and decide diagram inclusion (after reading all findings files).**
   Parse the `structured-output` agent's response for three sections: `ACTORS_AND_USE_CASES`, `COMPONENTS`, and `DEPENDENCIES`. See `references/investigation-agents.md` for the exact format and the four-component derivation fixture.

     - **If any section is missing or malformed**, record a parse-failure note in the affected plan subsection: `_Diagram omitted: investigation output did not conform to the required structured format._` A missing `DEPENDENCIES` section specifically triggers: `architecture diagram deferred — component set unconfirmed` (write this string in `### Architecture` and omit the diagram; same note used for both the no-components and missing-DEPENDENCIES cases).
     - **Derive change-set tags for COMPONENTS** by comparing against the feature brief's stated scope: `removed` entries → tag `removed` (direct pass-through); `planned` entries → tag `new` (direct pass-through); `exists` + named in brief as new or added → tag `new`; `exists` + named in brief as changed/modified → tag `modified`; all other `exists` → tag `unchanged`. See the derivation fixture in `references/investigation-agents.md`.
     - **Derive change-set tags for ACTORS_AND_USE_CASES** by comparing each entry against the feature brief's stated scope: actors or use cases the brief explicitly names as new or added → tag `new`; those the brief names as changed or modified → tag `modified`; those the brief names as removed → tag `removed`; all others → tag `unchanged`. These tags drive the colour and label suffixes on the actor–use-case diagram.
     - **Evaluate diagram inclusion triggers** (in this order): (1) Architecture — included by default, deferred only if COMPONENTS/DEPENDENCIES missing. (2) Actor–use-case map — include when ≥1 actor or use case is new, changed, or removed. (3) User flow — include when ≥1 user-facing step is added, removed, or reordered. (4) Data flow — include when ≥1 data edge (persistence, API call, migration) changes. (5) Sequence — include when ≥2 components interact over ≥3 ordered steps; skip if the complete inter-component interaction for this feature is already captured by the data flow diagram. When a conditional diagram is skipped, its subsection heading still appears in the plan body with an italicised skip reason.
     - **Apply node-count rule** to included flowchart diagrams (not sequence): the rendered diagram must contain ≤ 15 nodes. If the full node set exceeds 15 (i.e., ≥ 16), scope to changed nodes + 1-hop neighbours; write a caption line in the plan body immediately after the Mermaid block: `_Scope limited to change neighbourhood: [rationale]_`. If the 1-hop expansion also exceeds 15 nodes (i.e., ≥ 16), include changed nodes only and append to caption. For sequence diagrams > 8 participants, scope to the primary user-facing flow and add a caption.
     - **Authoritative colour and label convention** (all references point here): `classDef newNode fill:#a8d5a2;` / `classDef modNode fill:#f9e4a1;` / `classDef rmNode fill:#f4a4a4;`. New nodes: `*(new)*` suffix; modified: `*(mod)*`; removed: `*(rm)*`. Fallback for stadium shapes: single-asterisk `*new*`. Unchanged: no colour, no suffix. In architecture diagrams, use a dotted edge `-.->`  (no label) to represent a removed component's last known connection — this distinguishes severed from active edges without needing a label.
     - **Companion table** below each architecture diagram for ≥2 changed components. Omit for fewer than 2 and use a caption line: `_New: [Name] — [reason]_` / `_Modified:_` / `_Removed:_`.
2. **Front-matter + carried sections.** Set `id`, `feature`, `brief`, `purpose`, `audience`, `status: draft`, `roles: [frontend, backend, tester]`, `architecture:` (the active approach). Carry the brief's product content into Background, Goal, Scope (In/Out), Acceptance criteria, What does NOT change, Known limitations / trade-offs — rewording lightly, never inventing.
3. **Approach & architecture** — from the **structured-output**, **backend architect**, and **frontend architect** findings, plus **ADR** and **docs** findings. (The structured-output agent contributes the layer-presence map, FE/BE role mapping, and tier boundary from its `ARCHITECTURE_CONTEXT` block, plus the component inventory that drives the Mermaid diagram via COMPONENTS + DEPENDENCIES; backend/frontend architect contribute the "What changes" component interactions.) Produce the `## Approach & architecture` section with these subsections:
   - **`### Architecture`** — a Mermaid `flowchart` showing all components in the affected area with dependency arrows, colour and label suffixes on changed nodes (from synthesis step 1b), followed by the companion table (when ≥2 components changed). Includes the **Layer map** table and the **What changes** and **Key decisions** bullet blocks. Deferred with note "architecture diagram deferred — component set unconfirmed" if COMPONENTS is empty/malformed or DEPENDENCIES is missing.
   - **`### Actors & Use Cases`** — a Mermaid `flowchart LR` (or `TD`) with `[Actor]` rectangles and `([Use Case])` stadium shapes, colour and label suffixes on changed nodes. Skipped with italicised skip reason if no actor or use case is new, changed, or removed.
   - **`### Flows`** — three fixed child headings: `#### User Flow`, `#### Data Flow`, `#### Sequence`. Each is either a Mermaid diagram or an italicised skip reason, per the triggers evaluated in step 1b. See `assets/team-plan-template.md` for placeholders.
   - **`### Prior decisions`** — from **ADR agent** findings. A table (columns: **Decision**, **Rationale**, **Constraint**) containing only ADRs whose `constraint:` is non-empty and non-`"none"`. `status: superseded` is shown as a parenthetical in the Decision cell. Omitted with an italicised note if no ADRs exist or all have `constraint: none`.
   - **`### Contradictions`** — produced by synthesis (not by any single agent). Compare **docs agent** findings against **architecture / backend architect / frontend architect** findings to detect two categories: *code vs. docs* (what the code does differs from what a doc says) and *brief vs. reality* (what the brief assumes differs from what actually exists). Each contradiction states an owner: `"doc needs updating"` (*code vs. docs*, doc is wrong), `"code is the issue"` (*code vs. docs*, code is wrong), or `"open question"` (either category, cannot be resolved without more information). **Heuristic for choosing:** assign `"doc needs updating"` when evidence shows the code is correct (e.g., passing tests prove the code behaviour, or recent commits post-date the doc indicating a deliberate change); assign `"code is the issue"` only when evidence shows the code contradicts an accepted requirement — not merely a stale doc; otherwise default to `"open question"`. *Brief vs. reality* contradictions use `"open question"` exclusively. Owner `"doc needs updating"` maps to `## Documentation update` with reason *contradiction with code*. Omit this subsection with an italicised note only when both categories are empty.
4. **Contracts / seams** — from the **contracts** findings (and schema-contract boundaries flagged by the **data schema** agent). If TypeSpec is available: author internal logical seams as core-construct `.tsp` files beside the plan (**validate with `tsp compile <file> --no-emit`**); author HTTP/API seams as TypeSpec HTTP services in `api-contracts/` and **emit `<contract>.openapi.yaml`** (per `references/typespec-tooling.md`). Give each a logical summary + link(s) to the artefact(s). Otherwise write the built-in logical contract inline.
5. **Data** — from **data schema agent** findings. Produce the `## Data` section with: a Mermaid `erDiagram`. Changed entities carry plain-text `(new)`, `(mod)`, or `(rm)` label suffixes in their quoted entity name (e.g. `users["users (mod)"]`) — entity name labels do not support markdown formatting. Removed or added relationships are indicated by plain-text `(rm)`/`(new)` appended to the relationship label (e.g. `"has (new)"`) — relationship labels do not support markdown formatting. Do NOT include `classDef` or `class` statements — `erDiagram` does not support them and they cause a parse error. Apply the same node-count rule as architecture diagrams: scope to changed entities + 1-hop neighbours if the full schema exceeds 15 nodes, with a caption. Migration notes and entity model changes follow the diagram. Skip this section with an italicised reason if the project has no database.
6. **Scenarios** — from the **scenarios** findings. Cover the real current behaviour plus the edge/error paths already handled, and add the unhappy and non-functional paths. Assign `S#`. Tag the heading `#tester-role`.
7. **Allocation table (Tester section)** — from the **tester** findings. Allocate each scenario to the cheapest level the **real harness** supports. Remember ownership: unit + integration are dev-written; the tester section owns e2e + manual.
8. **Role sections (Frontend / Backend / Tester).** For each role: scope, the layers it owns, and a behavioural "Done when" checklist (Frontend/Backend) or the Allocation table (Tester). Populate the Backend and Frontend role sections' scope and "Done when" checklist from the **backend architect** and **frontend architect** agent findings respectively — the service layer patterns, domain model, component interactions, and architectural concerns they surfaced. Mark a role N/A if it has no scope.
9. **Documentation update** (before Open Questions). List the real docs the feature touches as a checklist with reasons (rule 13). Owner `"doc needs updating"` from `### Contradictions` maps here as reason *contradiction with code*. Additionally: for contradictions where the owner is `"code is the issue"`, check whether any documentation describes the code area being corrected — if so, that doc goes in Documentation update with reason *contradiction with code* (the doc described behaviour that was wrong; fixing the code also makes the doc stale). Files from the docs agent consulted for background only appear in a separate **Consulted (read-only)** list. Use Markdown link syntax for every real project file listed: `[filename](relative/path/to/file)`. Relative paths come from the docs agent's findings.
10. **Open questions.** Copy every open question from the brief verbatim; add every unknown the subagents surfaced and any you hit, as new `Q#`. Do not invent answers.
11. **Resolve pass.** For each open question, check whether the finished plan answers it. If yes, apply the answer and move it to a short "Resolved in this revision" note. If no, keep it open. Set `status` accordingly.
12. **Self-check** (below), then write `<feature-name>-team-plan.md` (and any `.tsp` files) and present it.
13. **Back-link the brief.** After writing the plan file, update `<feature-name>-brief.md`. Search the **entire file** for a `## References` heading. Four cases: **(a) No `## References` section exists** — if the last non-empty line of the file is already `---`, append a blank line then the block; otherwise append a `---` separator line, a blank line, then the block. The block is: a `## References` heading, a blank line, and a `- **Team plan:** [<feature-name>-team-plan.md](./<feature-name>-team-plan.md)` bullet. **(b) A `## References` section exists and already contains a `**Team plan:**` bullet pointing to the current plan filename** — skip; output: `<feature-name>-brief.md already links to this plan — no update needed.` **(c) A `## References` section exists and contains a `**Team plan:**` bullet with a different (stale) filename** — replace that bullet line (and only that line; remove any duplicate `**Team plan:**` bullets) with the updated link. **(d) A `## References` section exists but has no `**Team plan:**` bullet at all** — add `- **Team plan:** [<feature-name>-team-plan.md](./<feature-name>-team-plan.md)` as the first bullet under the heading. For cases (a), (c), and (d), output before writing: `Updating <feature-name>-brief.md — adding back-link to this plan.`
14. **Link audit and fix (post-write).** Re-read `<feature-name>-team-plan.md`. Scan all prose in the plan for bare file paths that refer to real **target-project** files. A token is a file-path candidate only if it contains a path separator (`/`) or a file extension — a bare identifier with neither is a code symbol, skip it. For each bare path found: resolve it using the file paths already cited by the Step 1 investigation agents (available in the findings files) and rewrite it as `[filename](relative/path)`. Do **not** scan inside fenced code blocks (` ``` `), inline code spans, front-matter YAML fields, or Mermaid diagram source. Do **not** treat component names, type names, layer names, or service names as file references — they are code symbols. Skill-internal files (references/…, assets/…) and planned-but-not-yet-created files are excluded.

   Also verify that existing Markdown links in those sections point to files that actually exist.

   If a bare path cannot be resolved from the findings files, report it as a warning in the summary returned to the orchestrator (do not guess the path). Do not add anything as a formal Q# open question — the resolve pass has already run.

15. **Estimate the plan (post-write).** Load the `estimate` skill and run `/estimate <plan path> --from <brief path>`: the plan path is `<feature-name>-team-plan.md`, the brief path is the plan's `brief` front-matter path resolved against the plan's folder (so the brief's feature file under `.harness-goodies/estimations/` is updated, not duplicated), both given relative to the current directory. Write the SP it returns into the plan as the line immediately after the H1 title line, replacing any earlier one: `**Story Points:** <SP> — estimated <YYYY-MM-DD> with /estimate`. The plan keeps the SP of the moment it was written; its feature file under `.harness-goodies/estimations/` always holds the latest. A failed estimate is reported to the user and leaves the plan without the line.

## Output structure (sections, in order)

See `assets/team-plan-template.md` for the fill-in skeleton and `references/example-team-plan.md` for a complete worked example. Headings carry **no numbers**.

1. Front-matter (`id, feature, brief, purpose, audience, status, roles, architecture`) — `brief` is a plain relative path to the source brief file, e.g. `brief: feature-name-brief.md`
2. Title + "How to read this file" (states the active architecture approach and its valid layers; notes that task breakdown is a separate downstream step)
3. Brief-derived: Background · Goal · Scope (In/Out) · Acceptance criteria · What does NOT change · Known limitations / trade-offs
4. **Approach & architecture** — `### Architecture` (component diagram + companion table + layer map + what changes + key decisions) · `### Actors & Use Cases` · `### Flows` (`#### User Flow`, `#### Data Flow`, `#### Sequence`) · `### Prior decisions` (ADR table or italicised skip note) · `### Contradictions` (contradiction block or italicised skip note)
5. **Contracts / seams** — logical summary (+ `.tsp` links if TypeSpec, plus `.openapi.yaml` link for HTTP/API seams)
6. **Data** — Mermaid `erDiagram` with `(new)`/`(mod)`/`(rm)` plain-text label suffixes on changed entities + migration notes; skipped with italicised reason if no database
7. **Scenarios** — Given/When/Then table — heading tagged `#tester-role`
8. **Frontend** — heading tagged `#frontend-role`
9. **Backend** — heading tagged `#backend-role`
10. **Tester** — heading tagged `#tester-role` — ownership note + allocation table
11. **Documentation update** — checklist of real docs touched with reason per file (*new feature* / *contradiction with code* / *no change needed*); separate **Consulted (read-only)** list
12. **Open questions**
13. **References** — a `## References` section as the final section: a bullet `- **Brief:** [<feature-name>-brief.md](./<feature-name>-brief.md)`. The downstream `create-tasks` skill appends a `- **Tasks:** [...]` bullet here when the task breakdown is generated (bidirectional plan↔tasks link) — do not add it during planning, since the tasks file does not exist yet.

## Final self-check (run before writing the file)

- [ ] The active architecture approach was **announced to the user**, recorded in front-matter, and its valid layers named in "How to read this file"; the contract tooling (TypeSpec vs built-in) was **announced**.
- [ ] Component/layer names, the role mapping, and contract types match the Step 1 investigation (real paths/symbols) — not guesses; everything unconfirmed is an open question.
- [ ] Every scenario `S#` has an entry in the Tester Allocation table (no coverage gap).
- [ ] Every contract `C#` has a logical summary (and links a `.tsp` file if TypeSpec was used — internal seams **compiled clean with `tsp compile <file> --no-emit`**; HTTP/API seams **emitted clean to a linked `<contract>.openapi.yaml`** via the `@typespec/openapi3` emitter). No task-ID back-references.
- [ ] A **Documentation update** section precedes Open Questions and lists only real files.
- [ ] Headings carry no numbers. Role tags end each role-owned section heading. No code in prose. All three role sections present (N/A if empty). Open questions handled; `status` set.
- [ ] The plan does not contain a Task Breakdown, dependency graph, task IDs, or estimates — except the single `**Story Points:**` line under the H1 title, written by step 15.
- [ ] The `brief` front-matter field is a plain relative path to the source brief (e.g. `feature-name-brief.md`), and a `## References` section is the final section of the plan with a `**Brief:**` link.
- [ ] *(post-write, after step 13 — Back-link the brief)* The source brief contains a `**Team plan:**` back-link to this plan — inserted, updated, or confirmed current; or skipped as already current. The prescribed announcement was output before any write.
- [ ] *(post-write, after step 14 — Link audit and fix)* All prose in the plan has been scanned for bare target-project file paths (tokens with a path separator or file extension); bare paths were rewritten as Markdown links using investigation-context paths; any broken existing links were reported as warnings; any path that could not be resolved from investigation context was flagged to the user.
- [ ] **Diagram gates:** (a) every included Mermaid diagram is syntactically valid; (b) every skipped diagram has an italicised skip reason under its subsection heading in the plan body; (c) change-set tags in the architecture diagram and actor–use-case map match the feature brief — every item named as new/modified/removed carries the right suffix, and no unchanged item is tagged; (d) if a flowchart diagram's full node set ≥ 16, a caption line appears in the plan body immediately after its Mermaid block; (e) if `COMPONENTS` or `DEPENDENCIES` was missing, `### Architecture` contains "architecture diagram deferred — component set unconfirmed"; (f) every colour-carrying diagram (architecture and actor–use-case only) includes classDef entries for all three: `newNode`, `modNode`, and `rmNode`, even if no nodes of a particular type are present in this feature. (g) if the plan contains a `## Data` section with a Mermaid `erDiagram`: do NOT include `classDef` or `class` statements — `erDiagram` does not support them and they cause a parse error; changed entities carry plain-text `(new)`/`(mod)`/`(rm)` in their quoted entity name; changed relationships carry plain-text `(new)`/`(rm)` appended to the relationship label — these are the sole visual indicators for ER diagrams; and a node-count caption appears when the 15-node scope cap applies. After generating the plan, **communicate this to the user**: all Mermaid diagrams with `classDef` colour fills and label suffixes (`*(new)*`/`*(mod)*`/`*(rm)*`) should be manually verified on the intended render targets (GitHub, Obsidian, claude.ai Artifacts) — in particular, that fill colours render and that `*(new)*` inside stadium-shaped nodes (`([...])`) does not break the Mermaid parser (if it does, the single-asterisk fallback `*new*` is the alternative).
