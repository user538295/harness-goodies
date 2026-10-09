---
name: llm-wiki
description: >
  Set up or operate a generic LLM Wiki (Karpathy pattern) in any project, for any non-product
  domain — research, personal knowledge management, reading a book, course notes,
  competitive analysis, hobby deep-dives, learning. Use when the user asks to "set up an LLM
  wiki", "apply the Karpathy wiki pattern", "build a persistent knowledge base", "ingest into
  the wiki", "create a topic page", "draft a document from the wiki", "lint the wiki", or any
  task that involves the `llm-wiki/` directory with `raw/`, `wiki/`, `drafts/` layers.
---

# LLM Wiki

Applies Andrej Karpathy's LLM Wiki pattern faithfully, with no domain assumptions. The wiki is
a persistent, LLM-maintained markdown knowledge base that sits between raw sources (papers,
articles, notes, transcripts, exports, screenshots) and the people who need answers or want
to draft new material.

The pattern stays minimal. **No automation of wiki content. File change detection is permitted
as infrastructure — the watcher detects but never acts; all ingest decisions remain with the
human. No agents, no RAG, no vector DBs, no graph DBs, no eval systems, no governance.** Just
markdown files, an index, and a log.

The full original idea is in `reference/karpathy-llm-wiki.md` — read it if context is missing.

## Wikis from the retired `llm-wiki-product` skill

The product-team variant `llm-wiki-product` (bucketed `raw/` with `product-docs/`, `specs/`,
`code-notes/`, `competitor-docs/`; page categories `wiki/features/`, `wiki/decisions/`) is
retired. Its layout is incompatible with this skill, so Setup (Step 1) refuses to proceed if
such a wiki already exists at the target path.

## Scope vs. other skills

This skill governs everything **inside `llm-wiki/`**. Project documentation that lives outside
`llm-wiki/` (e.g. `docs/`, `notes/`, `README.md`) belongs to other skills such as
`documentation-standard`. The wiki references those external docs as sources via the existing
documentation section in `schema.md`, but does not own them.

**Single declared exception:** the skill maintains a short HTML-comment-anchored *pointer
block* in `CLAUDE.md` and/or `AGENTS.md` at the repo root so future agents discover the wiki.
Insertion and removal happen only with explicit user confirmation (see Step 1c). All structural
detail lives in `llm-wiki/schema.md`; the pointer block is a stable stub that does not change
when `schema.md` evolves. No other mutations to those files are permitted by this skill.

**Non-goals.** This skill assumes the wiki lives at `llm-wiki/` directly under the repo root.
Relocating the wiki (e.g. `docs/llm-wiki/`, per-package wikis in a monorepo) is explicitly out
of scope — if you need that, fork the skill and parameterise the paths. Half-supporting it
would produce subtle path mismatches across `CLAUDE.md`, `schema.md`, and the pointer block.

---

## When to set up vs. operate

- **No `llm-wiki/` directory exists** → run **Setup** (Step 1).
- **`llm-wiki/` exists and is complete** (contains `schema.md` and the full layer tree) → run
  the requested **Operation** (Step 2).
- **`llm-wiki/` exists but is incomplete** (partial setup) → run **Repair** (Step 1b).

Use `ls llm-wiki/` and check for `schema.md`, `index.md`, `log.md`, `raw/`, `wiki/`, `drafts/`
before deciding.

---

## Step 1 — Setup (first time in a project)

Create exactly this structure at the repo root:

```
llm-wiki/
  karpathy-llm-wiki.md   ← copy from skill's reference/
  schema.md              ← from templates/schema.md, adapted to project
  index.md               ← from templates/index.md
  log.md                 ← from templates/log.md  (bookkeeping; part of the Karpathy pattern)
  raw/                   ← sources; symlinks followed (files and directories)
  wiki/
    overview.md          ← from templates/overview.md
    glossary.md          ← from templates/glossary.md
    topics/
    decisions/
  drafts/                ← flat; no subdirectories
```

The skill itself lives at one of these paths (resolve at runtime by checking which exists, in this order):

1. an explicit `LLM_WIKI_HOME` override
2. project-level skills roots
3. user-level skills roots of Claude Code, Cursor, OpenCode, omp and Codex
4. the newest plugin caches of Claude Code, omp, Codex, Cursor and OpenCode

Resolve in one shot and capture the printed path as `<SKILL_ROOT>`:

```bash
BASE=""; for d in "${LLM_WIKI_HOME:-}" .agents/skills/llm-wiki .claude/skills/llm-wiki .cursor/skills/llm-wiki .opencode/skills/llm-wiki .codex/skills/llm-wiki ~/.agents/skills/llm-wiki ~/.claude/skills/llm-wiki ~/.cursor/skills/llm-wiki ~/.config/opencode/skills/llm-wiki ~/.omp/agent/skills/llm-wiki ~/.codex/skills/llm-wiki "$(ls -d ~/.claude/plugins/cache/*/claude-goodies/*/skills/llm-wiki 2>/dev/null | sort -V | tail -1)" "$(ls -d ~/.omp/plugins/cache/plugins/*___omp-goodies___*/skills/llm-wiki 2>/dev/null | sort -V | tail -1)" "$(ls -d ~/.codex/plugins/cache/*/codex-goodies/*/skills/llm-wiki 2>/dev/null | sort -V | tail -1)" "$(ls -dt ~/.cursor/plugins/cache/*/cursor-goodies/*/skills/llm-wiki 2>/dev/null | head -1)" "$(ls -dt ~/.cache/opencode/npm/*/*/node_modules/opencode-goodies/skills/llm-wiki 2>/dev/null | head -1)"; do [ -n "$d" ] && [ -f "$d/SKILL.md" ] && { BASE="$d"; break; }; done; [ -n "$BASE" ] && echo "$BASE" || { echo "ERROR: llm-wiki not found in any known skills root — set LLM_WIKI_HOME=<skill dir>" >&2; false; }
```

Steps:

1. **Coexistence guard — refuse to overwrite a product-skill wiki.** If `llm-wiki/schema.md`
   already exists, read it and check for any of these fingerprints of the retired
   `llm-wiki-product` skill:
   - the string `raw/product-docs/`
   - the string `raw/competitor-docs/`
   - the string `wiki/features/`
   - the marker `<!-- llm-wiki:pointer v` (the product skill's pointer namespace)

   If any fingerprint is present, **stop and refuse** with: *"This project's `llm-wiki/` was
   created by the retired `llm-wiki-product` skill (detected fingerprint: `<which one>`). Its
   layout is incompatible with `llm-wiki`; wipe the existing `llm-wiki/` first to start a
   generic wiki."* Do not attempt to convert or merge — the two layouts differ.

2. Verify `llm-wiki/` does not already exist. If it does (and the coexistence guard above
   did not refuse), switch to **Step 1b (Repair)**.

3. Create the directories above.

4. Copy `<SKILL_ROOT>/reference/karpathy-llm-wiki.md` into `llm-wiki/karpathy-llm-wiki.md`.

5. Copy each file from `<SKILL_ROOT>/templates/` into the matching location.

6. **Adapt `llm-wiki/schema.md` to the project — MANDATORY:**
   - Detect any existing documentation roots in the project (e.g. `docs/`, `notes/`,
     `papers/`, `literature/`, `references/`).
   - **Replace the HTML comment block** in the "Existing project documentation" section with
     a real table listing each detected root with a short note on how the LLM should treat
     it. Do not leave the comment in place — it must be substituted, not appended.
   - Table format:
     ```
     | Path                | Note                                                      |
     |---------------------|-----------------------------------------------------------|
     | `docs/papers/`      | Read-only literature corpus — cite as `docs/papers/<f>`.  |
     | `notes/`            | Free-form notes — read for context, never modify.         |
     ```
   - If no external docs roots are detected, replace the comment with: `_No external
     documentation roots detected. All sources will live under `raw/`._`

7. Append the init entry to `llm-wiki/log.md`:
   `## [YYYY-MM-DD] init | wiki created | schema.md, index.md, wiki/overview.md, wiki/glossary.md`

8. Tell the user what was created and the next recommended operation (usually: ingest the
   most authoritative source first).

9. **Offer Pointer Injection.** Run Step 1c — it will detect `CLAUDE.md`/`AGENTS.md`, show
   the exact block to be inserted, and only mutate after explicit user confirmation. If the
   user declines, log the skip and stop.

Do **not** ingest anything during setup unless the user explicitly asks.

### Step 1b — Repair (partial wiki exists)

1. List what exists vs. the canonical layout above.
2. Show the user the diff. Ask: "Fill in missing pieces, or wipe and restart?"
3. On "fill in": create only the missing directories/files, leaving existing files untouched.
   For `schema.md`, classify it:
   - **Missing** → create from template and adapt (run Setup step 6).
   - **Contains the unmodified template HTML comment** in the "Existing project documentation"
     section → adapt (run Setup step 6).
   - **Adapted (well-formed markdown table follows the heading)** → leave as-is.
   - **Half-adapted** (the HTML comment is gone but the section has no table, or has a table
     missing the `| Path | Note |` header row) → **do not auto-adapt**. Surface this as a
     finding to the user and ask for manual resolution. Silently rewriting a half-adapted
     schema risks discarding human work.
4. **Non-mutating pointer check.** Run Step 1c's *Detect-only phase D* (D1–D3): if any
   pointer-eligible file exists without a marker, surface this to the user as a finding
   ("`CLAUDE.md` has no llm-wiki pointer — run pointer injection separately if desired?").
   Do not mutate anything during repair without an explicit second confirmation.
5. On "wipe": confirm again, then `rm -rf llm-wiki/` and run Step 1.

### Step 1c — Pointer Injection (CLAUDE.md / AGENTS.md)

Goal: make sure future agents discover `llm-wiki/` and the skill. **Never executes a mutation
without explicit user confirmation.**

**Repo-root resolution.** Assign once at the start of the procedure:

```bash
ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || ROOT="$(pwd)"
```

If `git rev-parse` failed, tell the user the project is not a git repo and that
case-insensitive name resolution will fall back to `ls`.

**Detect-only phase D** (no writes — safe to run from Repair via Step 1b.4):

- **D1.** Resolve which of `CLAUDE.md` / `AGENTS.md` exist at `$ROOT` using their tracked
  casing. In a git repo:

  ```bash
  git -C "$ROOT" ls-files | grep -iE '^(CLAUDE|AGENTS)\.md$'
  ```

  Outside git, fall back to:

  ```bash
  (cd "$ROOT" && ls 2>/dev/null | grep -iE '^(CLAUDE|AGENTS)\.md$')
  ```

  Use the exact string the command returns — never write to a name with different casing on
  case-insensitive filesystems (macOS/Windows), or you may create a duplicate file under
  case-sensitive CI.
- **D2.** **Never create either file** if it does not exist (this is a Hard Rule — see
  below). If neither exists, tell the user and stop.
- **D3.** For each existing file, check for the anchor marker and capture the detected
  version. **Pre-strip CR characters so CRLF-terminated files match correctly** (otherwise
  the trailing `-->\r` defeats the regex and you will double-inject):

  ```bash
  tr -d '\r' < "$file" | grep -oE '<!-- llm-wiki-generic:pointer v[0-9]+ -->'
  ```

  Classify each file:
  - **No marker** → eligible for fresh injection.
  - **Marker matches the current target version** (the version in the block at M's
    template) → "already pointed (current)"; skip for any mutation. The marker is the
    single idempotency gate; the human-readable heading underneath is for readers and may
    be edited freely.
  - **Marker version is older than the current target** → "already pointed (outdated)";
    not silently skipped — surface to the user in M1 as an upgrade candidate.

  **Coexistence note (read-only):** a file may also contain a `<!-- llm-wiki:pointer v[0-9]+ -->`
  marker left by the retired `llm-wiki-product` skill. The generic skill never touches that
  marker — different namespace. If you observe one alongside (or absent) the generic
  marker, mention it to the user once for awareness and continue.

**Mutation phase M** (only proceeds after explicit user `yes`):

- **M1.** Present the user with three lists, plus the block and insertion strategy:
  - **Fresh injection targets** — files with no marker.
  - **Already current** — files with a marker matching the target version; will be skipped.
  - **Upgrade candidates** — files with an older marker. Offer per-file: "(a) leave the
    outdated marker, (b) upgrade by removing the old block and injecting the new one." If
    the user picks (b), run the Pointer removal operation first, then continue M3 for that
    file.

  Show: the list of files with casing, the exact block (below, verbatim), the insertion
  strategy ("appended at end-of-file, with one blank line of separation"), and a yes/no/per-
  file prompt as appropriate.
- **M2.** If the user declines, stop and log a skip (step M4).
- **M3.** On `yes`, for each target file:
  - If the file does not end in a newline, append a newline.
  - Append one blank line, then the block below verbatim, then a trailing newline.
  - Do not attempt to detect "obvious sections" — append-at-EOF is the only sanctioned
    placement.

Block to insert (verbatim — do not start with a blank line; M3 controls separation).
**The ```` ```markdown ```` fences below are presentation only — append only the lines between
them, never the fence lines themselves**:

```markdown
<!-- llm-wiki-generic:pointer v1 -->
### LLM Wiki — `/llm-wiki/`

An LLM-maintained markdown knowledge base lives at [`/llm-wiki/`](llm-wiki/). Read [`llm-wiki/schema.md`](llm-wiki/schema.md) for the canonical layer rules, source hierarchy, and operation procedures.

If you are an agent that supports skills, invoke the `llm-wiki` skill (e.g. Claude Code: `Skill(skill: "llm-wiki")`) before any operation on the folder. If the skill is unavailable, follow `llm-wiki/schema.md` directly.

**Keep the wiki in sync with substantive changes.** After adding, changing, or removing any significant concept, decision, or source material — update the affected page(s) under `llm-wiki/wiki/` and refresh `index.md` in the same session. Skip for trivial edits (typos, whitespace, comment-only tweaks). When in doubt, ask the user.
<!-- /llm-wiki-generic:pointer v1 -->
```

**Current target version: `v1`.** The generic skill is new — no older versions of this
pointer block exist yet. The namespace `llm-wiki-generic:pointer` is distinct from
`llm-wiki:pointer` (left by the retired `llm-wiki-product` skill), so the two markers can
coexist in the same file without interference. A file that carries both markers (e.g. a stale
product marker after the user wiped the product wiki) is not an error condition for the
generic skill — it just leaves the foreign marker alone.

- **M4.** Append to `llm-wiki/log.md` (note the distinct `infra |` prefix — pointer ops are
  not wiki content operations):
  - On mutate: `## [YYYY-MM-DD] infra | pointer injected | <file1>, <file2>`
  - On user-declined: `## [YYYY-MM-DD] infra | pointer skipped (user declined) | -`
  - On all-already-present: `## [YYYY-MM-DD] infra | pointer skipped (already present) | -`
  - On no-target-file: `## [YYYY-MM-DD] infra | pointer skipped (no CLAUDE.md or AGENTS.md) | -`

**Pointer removal operation:**

To remove an injected pointer from a file:

1. Match the versioned pair: opening `<!-- llm-wiki-generic:pointer v<N> -->` to the
   same-version closing `<!-- /llm-wiki-generic:pointer v<N> -->`. Both markers carry the
   same `<N>`, so future v1 and v2 blocks can coexist and be removed independently.
   Pre-strip CR characters when scanning to tolerate CRLF line endings (same as D3).
2. Show the user the exact lines to be removed (use `grep -n` for line ranges). If any line
   inside the block diverges from the canonical template, warn the user explicitly that
   custom content will be lost.
3. On confirmation, delete the lines from the opening marker through the matching closing
   marker inclusive. If the line immediately after the closing marker is literally blank
   (zero non-whitespace characters), also delete that one line. **Never delete a line that
   has any non-whitespace content** — protect against eating user-edited adjacent content.
4. Log: `## [YYYY-MM-DD] infra | pointer removed (v<N>) | <file>`

**Version bump.** If a future skill release changes the pointer text, the new injection
uses `v2` markers. Detection (D3) is already version-agnostic; the opening regex
`<!-- llm-wiki-generic:pointer v[0-9]+ -->` matches any version, so a `v1` block correctly
blocks duplicate `v2` injection. Upgrade procedure: remove the `v1` block via the removal
operation, then re-inject (which will write `v2`).

---

## Step 2 — Operations

**Content operation procedures live in `llm-wiki/schema.md`. Infrastructure and watcher
operations (`watcher`, `watcher on`, `watcher off`) are defined in this file below.**
Before running any content operation, re-read `llm-wiki/schema.md` — it evolves per project.

**Ingest state is owned by `catalog.py`, not the watcher.** `catalog.py` (resolved at
`<SKILL_ROOT>/catalog.py`) is a script-owned ledger of which `raw/` files have been ingested
and their sha256 at ingest time. It is the single authority for what needs ingesting: `status`
diffs `raw/` against the ledger into `new` / `changed` / `current` / `missing`. The watcher is
opt-in observability only — it journals filesystem activity and heartbeats; it holds no ingest
state and never decides ingest.

`catalog.py` commands (always pass the repo root as `<project-root>`):

- `python3 <SKILL_ROOT>/catalog.py status <project-root>` — print `{new, changed, current, missing}` as JSON (raw-relative POSIX paths). `new` = never ingested; `changed` = ingested but content differs now (needs re-ingest); `missing` = catalogued file no longer in `raw/`.
- `python3 <SKILL_ROOT>/catalog.py add <project-root> <path>...` — record an ingest (upsert: hashes the file now, stamps `ingested_at`). Call once per file **after** its wiki pages and `index.md`/`log.md` are updated.
- `python3 <SKILL_ROOT>/catalog.py get <project-root> <path>` — print one entry (or `null`).
- `python3 <SKILL_ROOT>/catalog.py remove <project-root> <path>...` — drop entries (idempotent; works even if the raw file is gone).

Paths are resolved and re-verified under `raw/` — a path outside `raw/` exits non-zero. `add`
requires the file to exist. The ledger lives at `llm-wiki/catalog.json` (committed, so a fresh
clone keeps ingest history); if absent, `status` reports every raw file as `new`.

This file (`SKILL.md`) only routes you to schema.md and enforces the hard rules below. If you
detect a conflict between this file and `schema.md`, `schema.md` wins (it was project-adapted)
— **with one carve-out**: the Source Hierarchy is canonical here in `SKILL.md`. `schema.md`
reproduces the list for local discoverability but defers to this file on it.

Operation names defined in `schema.md`:

- **Ingest** — read a source, update wiki pages, update `index.md`, append to `log.md`.
- **Create a topic page** — synthesize a `wiki/topics/<name>.md` from raw sources.
- **Draft a document** — write a `drafts/<name>.md` (not wiki truth until promoted by human).
- **Query** — read `index.md`, find pages, synthesize answer with citations.
- **Lint** — health-check the wiki (orphans, missing citations, contradictions).
- **Archive** — retire a stale page by moving it to `wiki/archive/<original-path>` with a
  superseded-by note (see schema.md for details).
- **Evolve schema** — when a new page category emerges (e.g. `wiki/methods/`,
  `wiki/people/`), update `schema.md` first, then create the directory.
- **watcher** — reconcile desired-vs-running (below), then report watcher liveness and the `catalog.py status` buckets (new/changed to ingest); offer to ingest them.
- **watcher on** — enable the watcher (persist desired state) and start it.
- **watcher off** — disable the watcher (persist desired state) and stop it.

All three resolve `<SKILL_ROOT>` per the priority order above (Setup) and use
`<SKILL_ROOT>/watcher.py`. The **desired state** is a one-line file `llm-wiki/.watcher/desired`
containing `on` or `off`; **absent means off** (never configured). It records what the user
wants, independent of whether the process is currently alive.

**Liveness check** (used throughout, referred to below as "running?"):

1. Read `llm-wiki/.watcher/watcher.pid` — absent → not running.
2. Parse `<pid>:<nonce>` (line 1) and the heartbeat timestamp (line 2).
3. Verify via `ps -p <pid> -o command=` that the output contains `watcher.py` — if not, the PID is stale → not running (delete the stale PID file).
4. Compute heartbeat age = `now - heartbeat`. Age < 90s → running. 90s–300s → hung (treat as running but warn "heartbeat is stale — it may be hung"). > 300s → not running.

### Watcher reconciliation (run at the START of every `/llm-wiki` invocation)

This makes the desired state stick across restarts. Before any operation:

1. **Skip** if `llm-wiki/.watcher/` does not exist (watcher was never set up).
2. Read `desired` (default `off`). Run the liveness check.
3. **desired `on` but not running** → run the **start procedure** below, then tell the user: *"The watcher was enabled earlier but wasn't running — I restarted it (PID X)."*
4. **desired `off` (or absent) but running** → tell the user: *"The watcher is running but it's turned off. Want me to stop it?"* Do **not** auto-stop — wait for the user. (Only `watcher off` stops it.)
5. Otherwise (on+running, or off+not-running) → say nothing; proceed.

### watcher on

1. Check `llm-wiki/` exists — if not, fail: "No llm-wiki/ directory found. Run setup first."
2. Create `llm-wiki/.watcher/` if absent.
3. Write `on` to `llm-wiki/.watcher/desired`.
4. Add `llm-wiki/.watcher/` to `.gitignore` at project root if not already present (mandatory — append the line if absent, never duplicate).
5. Run the **start procedure** below.

**Start procedure:**

- If already running (liveness check), report status and stop — do not spawn a second process.
- Run: `nohup python3 <SKILL_ROOT>/watcher.py start <project-root> > /dev/null &` (stderr is NOT redirected so startup errors are visible in the terminal before daemonizing).
- Wait 2 seconds, then read the PID file to confirm; report the PID. If the PID file is absent, check the terminal for startup errors.
- Log to `llm-wiki/log.md`: `## [YYYY-MM-DD] infra | watcher started | llm-wiki/.watcher/`

### watcher off

1. Write `off` to `llm-wiki/.watcher/desired` (create `.watcher/` first if absent).
2. Run the liveness check. If not running, report "watcher is not running" and stop (desired is now `off`).
3. Send SIGTERM: `kill -TERM <pid>`.
4. Wait up to 5s for the process to exit (poll `ps -p <pid>` every 1s). Report success or timeout.
5. Log to `llm-wiki/log.md`: `## [YYYY-MM-DD] infra | watcher stopped | llm-wiki/.watcher/`

### watcher (status)

1. Run the reconciliation above (it may restart or prompt).
2. Report desired state (`on`/`off`) and liveness (running + heartbeat age, or not running).
3. Run `python3 <SKILL_ROOT>/catalog.py status <project-root>`. Report the counts of `new` and `changed` (the files that need ingesting) and `missing` if any.
4. If `new` or `changed` is non-empty, ask: *"N file(s) need ingesting (X new, Y changed) — ingest them now?"* On yes, run the Ingest operation for each.

---

## Hard rules (never break — these override anything elsewhere)

- **`raw/` is append-only for the LLM.** The LLM may *create* new files inside `llm-wiki/raw/`
  during ingest of user-provided material, but **never modifies or deletes** files already
  there. Files in external paths listed in `schema.md`'s "Existing project documentation"
  section are read-only for the LLM entirely. Humans may edit anything freely — the
  immutability is a constraint on the LLM, not on humans.
- **Never create `CLAUDE.md` or `AGENTS.md`.** Pointer injection (Step 1c) only appends to
  these files when they already exist. Creating new top-level agent-instruction files is
  outside this skill's scope.
- **New ingested material lives inside `llm-wiki/raw/`.** When the user asks you to ingest a
  *new* source (article, paper, transcript, website export, screenshots, PDFs, notes, etc.),
  write the raw file under `llm-wiki/raw/` — never into `docs/`, `notes/`, or any sibling
  directory you invent. The "Existing project documentation" section in `schema.md` only
  governs *pre-existing* external docs; net-new material always goes into `raw/`. `raw/` has
  no required subdirectories; symlinked files and directories are followed.
  Synthesised analyses (comparisons, gap analyses, summaries) are wiki output, not raw — write
  them under `llm-wiki/wiki/`, not `raw/`.
- **Save format for ingested material.** Every raw file must (a) preserve all substantive
  claims from the source — structured reformatting into tables/sections is fine; lossy
  summarisation that drops claims is not — and (b) begin with the provenance header defined
  in `schema.md` (Ingest operation). Binary sources are placed by the human; the LLM creates
  the sibling markdown file with extracted/transcribed text.
- **Never treat a draft as truth.** Drafts in `drafts/` are work in progress; they are read
  for context but never cited as authoritative on wiki pages.
- **Never invent claims unsupported by a source.**
- **Never leave a conflict unresolved — flag it explicitly on the page.**
- **Source hierarchy** when sources conflict — this is the canonical list; `schema.md`
  reproduces it for project-local discoverability, but if the two disagree, this list wins:
  1. **Raw sources** (`raw/` and any external listed paths) — strongest evidence.
  2. **Drafts** (`drafts/`) — aspirational; never truth.

  Within raw sources, trust order is undefined. When two raw sources conflict, flag the
  contradiction explicitly on the affected wiki page and present both positions; never
  silently choose one. The human resolves raw-source conflicts (often by adding a decision
  page that records which source wins and why).

---

## Resources

- `reference/karpathy-llm-wiki.md` — original pattern (read for context, never copy into wiki output).
- `templates/schema.md` — generic schema template; adapt during setup.
- `templates/index.md`, `templates/log.md`, `templates/overview.md`, `templates/glossary.md` — empty starters.
