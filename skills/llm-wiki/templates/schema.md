# Wiki Schema

This document tells the LLM how the wiki works. Follow it exactly. Update it when conventions
evolve (see **Evolve schema** below).

Based on Karpathy's LLM Wiki pattern (see `karpathy-llm-wiki.md` in this folder). The pattern is
domain-agnostic — research, personal knowledge, course notes, reading a book, deep-dives on a
topic, business intelligence. The concrete examples in this file are written against a research
wiki for illustration; the structure works for any non-product domain. Adapt vocabulary in
context (e.g. "topic" can mean a concept, a paper, a character, a habit, an entity — whatever
the unit of synthesis is for your domain).

---

## Directory Layout

```
llm-wiki/
  schema.md          ← you are here
  index.md           ← catalog of all wiki pages (LLM maintains)
  log.md             ← append-only activity log (LLM maintains)
  raw/               ← immutable sources (LLM never modifies); symlinks followed
  wiki/              ← LLM-maintained knowledge (write here)
    overview.md      ← high-level summary of what this wiki covers
    glossary.md      ← shared terminology
    topics/          ← main content pages (concepts, entities, summaries)
    decisions/       ← key choices and their rationale
    archive/         ← retired pages (created lazily on first archive)
  drafts/            ← work in progress (not truth); flat — no subdirectories
```

`raw/` has no required subdirectories — categorization is the wiki's job, not raw/'s. You may
organize your own sub-folders inside `raw/` if you find them useful (the skill won't stop you),
and you may **symlink** external directories or files into `raw/` to include them in the corpus
without copying. Symlinked files and directories are followed by the catalog.

Additional `wiki/` subdirectories (e.g. `wiki/methods/`, `wiki/people/`,
`wiki/timelines/`) may emerge as the wiki grows. **Add them via the Evolve schema operation —
register the new category here before creating the directory.**

## Existing project documentation

<!--
  SETUP STEP — REPLACE THIS COMMENT BLOCK ENTIRELY.

  If external documentation already exists in the project (e.g. a `docs/` folder, a
  literature directory, exported notebooks), list each path here with a one-line note on how
  the LLM should treat it. If all sources will be dropped into `raw/` directly, replace this
  comment with the no-external-docs line below.

  Example:

  | Path                    | Note                                                          |
  |-------------------------|---------------------------------------------------------------|
  | `docs/papers/`          | Read-only literature corpus — cite as `docs/papers/<file>`.   |
  | `docs/notebooks/`       | Personal notebooks — cite when relevant; do not modify.       |
  | `notes/`                | Free-form notes — read for context, never treat as truth.     |

  If no external docs exist, replace this comment with:
  _No external documentation roots detected. All sources will live under `raw/`._
-->

When ingesting from external docs, note the source as the full path
(e.g. `source: docs/papers/2024-attention-is-all-you-need.pdf`).

**Immutability scope:** the LLM never modifies files in `raw/` or in any external path listed
above. Humans may edit external paths freely (they are the source of truth that the LLM reads
from).

---

## Source Hierarchy (trust order)

**This list is reproduced from `SKILL.md` for project-local discoverability. SKILL.md holds the
canonical version — if the two ever diverge, SKILL.md wins for this list only. (For everything
else in this document, schema.md is authoritative over SKILL.md.)**

Two tiers:

1. **Raw sources** (`raw/` and any external paths listed above) — the strongest evidence.
2. **Drafts** (`drafts/`) — aspirational; work in progress; never cited as truth.

Within raw sources, trust order is **undefined**. When two raw sources conflict, the LLM must
flag the contradiction explicitly on the wiki page and present both positions; it must never
silently choose one. The human is responsible for resolving raw-source conflicts (typically by
adding a decision page that records which source wins and why).

Always note the source of a claim. When sources conflict, state the conflict explicitly on the
wiki page — do not silently choose one.

---

## Layer Rules

### raw/ — Sources (append-only for the LLM)

- **Create-yes, modify-no.** The LLM may *create* new files in `raw/` during ingest of
  user-provided material (pasted text, fetched URL content, transcribed screenshots). The LLM
  **never modifies or deletes** files already in `raw/`. When a source changes (e.g. a paper
  has a revised preprint), the LLM creates a *new* raw file with a new dated filename — the
  old file stays as the audit trail.
- Most sources are markdown or text. **Non-text sources are allowed** (PDFs, screenshots,
  exported HTML, CSV):
  - For binary sources: **the human places the binary file at the target path themselves**
    (the LLM does not write binary content via text-edit tools). The LLM then creates a
    sibling markdown file capturing the extracted/transcribed text plus the provenance header
    below; the wiki page cites the markdown sibling (not the binary).
  - For PDFs specifically, the LLM may *read* the PDF directly (multimodal) for extraction,
    even though it cannot *write* one.
- Filename convention: `YYYY-MM-DD_short-description.<ext>`
- `raw/` has no required subdirectories. Sub-folders and **symlinks** (files or directories)
  are fully supported — the catalog follows them. Citations use the full relative
  path (e.g. `raw/linked-corpus/paper.md`).

### wiki/ — Compiled knowledge (LLM writes)

- LLM creates and updates these files.
- Every page must have a one-line `> summary:` at the top.
- Use `[[page-name]]` for internal links (Obsidian-compatible).
- Cross-reference generously. Orphan pages are a smell.
- When a claim comes from a source, cite it inline: `(source: raw/<file>)` or
  `(source: <external-path>)`.
- **Filename collisions:** if two topics would produce the same kebab-case name (e.g.
  `attention.md` for the cognitive concept vs. the ML mechanism), disambiguate by domain
  prefix: `attention-cognitive.md`, `attention-ml.md`. Never overwrite an existing page
  silently.

**Topic pages** (`wiki/topics/<topic-name>.md`):
- What the topic is (the synthesis — the LLM's compiled view across sources)
- Key claims with citations
- Related topics (links)
- Open questions
- Conflicts or gaps between sources

**Decision pages** (`wiki/decisions/<decision-name>.md`):
- What was decided
- Why
- Alternatives considered
- Source reference

Decision pages capture the choices *the wiki itself* makes about how to interpret conflicting
sources, what conventions to adopt, which threads to prioritise — anything where a deliberate
call was made. In a research wiki this might be "which definition of `embodied cognition` we
adopt across pages"; in a book wiki it might be "which character is the actual narrator of
chapter 3 given two conflicting interpretations."

### drafts/ — Work in progress (not truth)

- Drafts live here until reviewed and promoted to `wiki/topics/` (or `wiki/decisions/`).
- Top of every draft: `> STATUS: draft | author: X | date: YYYY-MM-DD`
- **Promotion is a human action**, not a skill operation: the human explicitly moves a draft
  out of `drafts/` into the appropriate `wiki/` location to mark it as truth.
- Never cite a draft as evidence on a wiki page.
- `drafts/` is **flat** — no subdirectories.

---

## Operations

## Pre-flight check

**Run this check before every operation.** It surfaces raw files that need ingesting. Crash
safety is automatic: a file is recorded in the catalog
(`catalog.py add`) only *after* its wiki pages are written, so an interrupted batch simply
leaves the remaining files showing as `new`/`changed` next time — there is no separate
processing-queue to recover.

`catalog.py` and its resolved path are defined in `SKILL.md` (Step 2). It is the single
authority for ingest state.

Run `catalog.py status <project-root>`.

- If `new` and `changed` are both empty, no action needed — proceed to the requested operation.
- Otherwise say: *"N file(s) in raw/ need ingesting (X new, Y changed) — ingest them first?"*
  - **Yes**: ingest each via the Ingest operation — one Ingest call per file, its own
    `log.md` entry, and its own `catalog.py add` on success. `changed` files follow
    **Re-ingest** (a tracked source whose content changed).
  - **No**: proceed with the requested operation. The files stay `new`/`changed` and will
    surface again next time — accurate content-hash detection means declining is safe (no
    snooze needed).
- If `missing` is non-empty (catalogued files no longer in `raw/`), mention it once as
  informational — usually a superseded source; no action required.

### Log line format

All operations append a single line to `log.md` in this shape:

```
## [YYYY-MM-DD] <verb> | <subject> | <objects-touched>
```

- `<verb>`: the operation name, lowercase (e.g. `init`, `ingest`, `re-ingest`, `topic`,
  `draft`, `archive`, `schema`, `lint`, `infra`).
- `<subject>`: the primary input of the operation — usually a filename, but for `init` it is
  a short phrase like `wiki created`, for `infra` it is `pointer injected` or similar.
- `<objects-touched>`: comma-separated list of pages or files modified by the operation. Use
  `-` if no objects were touched.

Pipes `|` are field separators — never use `|` inside any field. Use "or" or restructure the
text if needed.

### Ingest a source

1. The human provides material in one of three ways:
   - **(a)** drops a file into `raw/` (or an external listed path) and says
     "Ingest `<path>`";
   - **(b)** pastes text or gives a URL — the LLM creates the raw file under `raw/`
     using the filename convention and the provenance header below;
   - **(c)** provides a binary file (PDF, image) — the human places the binary at the target
     path; the LLM creates a sibling markdown file with extracted text and the provenance
     header, and treats *that* sibling as the citable source.
2. **Provenance header** — every LLM-created raw markdown file begins with:

   ```markdown
   # <Short title>

   **Source:** <URL | path | "user-paste" | "user-binary: relative/path/to/file.pdf">
   **Ingested:** YYYY-MM-DD
   **Original format:** <html | pdf-text | plain-text | screenshots | url | ...>
   **Supersedes:** <prior-raw-filename>  ← only present on Re-ingest

   ---
   ```

   Use the most specific source identifier available — `user-paste` is the fallback when no
   URL or path applies. `Supersedes:` is included only when this file replaces an earlier raw
   file (see Re-ingest below) — it provides the forward-discoverable audit chain that the
   append-only rule on `raw/` would otherwise lose. Existing raw files without all fields are
   grandfathered (Lint may flag missing fields as informational, never as an error).

   **Filename convention.** New raw files should use the date-prefixed form
   `YYYY-MM-DD_short-description.<ext>` (e.g. `2026-05-30_attention-paper.md`). Legacy raw
   files ingested before this convention may lack the prefix — leave them as-is; Re-ingest of
   a legacy file produces a properly-named new file with `Supersedes: <legacy-name>`.
3. **Save-format rule.** Preserve all substantive claims from the source. Structured
   reformatting into tables/sections is fine; lossy summarisation that drops claims is not.
4. LLM reads the source and creates or updates the relevant wiki pages, citing the raw file
   inline as `(source: raw/<file>)`.
5. LLM updates `index.md`.
6. LLM appends to `log.md`: `## [YYYY-MM-DD] ingest | <filename> | <pages touched>`
7. LLM records the ingest in the catalog — **only after steps 4–6 succeed**:
   `catalog.py add <project-root> raw/<filename>` (see `SKILL.md` Step 2 for the resolved
   path). This stamps the file's sha256 so it reads as `current`; if the source later changes,
   `catalog.py status` will flag it as `changed`.

**Re-ingest (source updated).** When a tracked source changes — a paper has a revised
preprint, a website page is rewritten, a chapter is re-released — create a *new* raw file with
the new ingestion date in the filename (e.g. `2026-08-01_attention-paper.md`) and include
`Supersedes: <prior-name>` in its provenance header. Update wiki citations to point at the new
file: **trace the full supersession chain back via `Supersedes:` links and grep for citations
of every predecessor filename**, not just the immediate prior one — at chain depth > 1, a wiki
page that was never updated during an earlier re-ingest may still cite the original. The old
files stay untouched as the audit trail; the Supersedes field makes the chain
forward-discoverable without modifying them. Log:
`## [YYYY-MM-DD] re-ingest | <new-filename> supersedes <old-filename> | <pages touched>`

Then record the new raw file in the catalog: `catalog.py add <project-root> raw/<new-filename>`.
The superseded file keeps its own catalog entry (its content is unchanged, so it stays
`current`) — leave it; the append-only audit trail is intact.

**In-place change (same filename).** If a source file in `raw/` is edited in place rather than
superseded by a new dated file, `catalog.py status` reports it as `changed`. Re-run the Ingest
save-format and page updates against the new content, then `catalog.py add` the same path to
re-stamp its hash back to `current`.

**Sync trigger.** Beyond explicit user-initiated ingests, the LLM should proactively refresh
the wiki in the same session after adding, changing, or removing any significant concept,
decision, or source material in the underlying domain. Skip for trivial edits (typos,
whitespace, comment-only tweaks). When in doubt, ask the user before re-ingesting — re-ingest
is cheaper than spurious churn but still costs a session.

### Create a topic page

1. Human: "Create a topic page for <topic name>"
2. LLM checks `index.md` — does the page already exist?
3. LLM searches raw sources for relevant material.
4. LLM creates `wiki/topics/<topic-name>.md` using the topic page structure above.
5. LLM links it from related pages and `wiki/overview.md` if appropriate.
6. LLM updates `index.md`.
7. LLM appends to `log.md`.

### Draft a document

1. Human: "Draft a document for <subject>" (e.g. a literature review section, a synthesis
   memo, an essay outline, a proposal — anything that isn't ready to be cited as truth yet).
2. LLM reads any relevant topic pages from `wiki/topics/`.
3. LLM reads relevant raw sources for context.
4. LLM creates `drafts/<short-name>.md` with the STATUS header.
5. LLM does NOT update `index.md` or treat draft as wiki truth.
6. Human reviews and edits the draft.
7. When promoted: human moves the draft to the appropriate `wiki/` location, then LLM
   updates `index.md` and `log.md` for the promoted page.

### Query

1. Human asks a question.
2. LLM reads `index.md` to find relevant pages.
3. LLM reads those pages and synthesizes an answer with citations.
4. If the answer is reusable (a comparison, an analysis, a connection discovered), LLM asks:
   "Should I file this as a wiki page?"

### Lint (periodic)

Ask the LLM to health-check the wiki:
- Pages with no inbound links
- Claims without source citations
- Contradictions between pages
- Topics mentioned in overview but lacking a topic page
- Drafts that have been sitting without review (flag drafts not modified in 30+ days as
  stale)

### Archive (retire a stale page)

1. Human: "Archive `wiki/<path>`" (with optional `superseded by <new-page>`).
2. LLM moves the file to `wiki/archive/<original-path>` (preserving the original subpath).
3. LLM adds a top-of-file note: `> ARCHIVED YYYY-MM-DD — superseded by [[new-page]]` (or
   `> ARCHIVED YYYY-MM-DD — no longer relevant`).
4. LLM updates inbound links: either redirect them or remove them.
5. LLM updates `index.md` (move row to an "Archive" section, do not delete).
6. LLM appends to `log.md`: `## [YYYY-MM-DD] archive | <page> | superseded by <new-page>`

### Evolve schema (add a new wiki page category)

When the wiki organically needs a new category (e.g. `wiki/methods/`, `wiki/people/`,
`wiki/timelines/`):

1. Human: "Evolve schema — add `<category>` pages."
2. **Validate the proposed category name:**
   - Reserved peer directories (must not be used as wiki/ subdirectory names — they exist at
     the top level of `llm-wiki/`): `raw`, `drafts`.
   - Reserved wiki/ subdirectories (already defined): `topics`, `decisions`, `archive`.
   - Reserved wiki/ filenames (do not name a category the same as an existing top-level wiki
     file): `overview`, `glossary`.
   - Must not duplicate an existing category in the Directory Layout.
   - Must be lowercase kebab-case.
   - If validation fails, stop and ask the human for a different name.
3. **Show the human the exact diff to `schema.md` first** and get explicit confirmation
   before writing. The Evolve schema operation is the only path by which the LLM modifies
   `schema.md` — treat it with the same care as a `raw/` write would deserve if it were
   allowed.
4. LLM updates `schema.md`: add the new directory to the **Directory Layout** section above,
   with a one-line description.
5. LLM creates the new directory.
6. LLM appends to `log.md`: `## [YYYY-MM-DD] schema | added wiki/<category>/ | schema.md`

**Rollback:** if the new category turns out wrong, run Evolve schema in reverse — show the
removal diff, get confirmation, remove the line from Directory Layout, and either delete the
(empty) directory or archive any pages in it via the Archive operation first.

---

## Page Frontmatter (optional but useful)

```yaml
---
type: topic | decision | overview | glossary
sources: [raw/2026-05-30_attention-paper.md, docs/papers/2024-attention.pdf]
updated: YYYY-MM-DD
status: current | stale | disputed | archived
---
```

Add new `type:` values as you Evolve the schema.

---

## What the LLM must never do

- Modify or delete existing files in `raw/` or in any external listed path. (The LLM may
  *create* new files in `raw/` during ingest — see the Ingest operation.)
- Treat a draft as truth.
- Invent claims not supported by a source.
- Leave a conflict unresolved — always flag it explicitly.
- Create a new `wiki/` subdirectory without first registering it via **Evolve schema** —
  with one exception: `wiki/archive/` is pre-registered in the Directory Layout and is
  created lazily on the first Archive operation, no Evolve ceremony required.
- Overwrite an existing wiki page on filename collision — disambiguate with a domain prefix.
