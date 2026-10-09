---
name: md-reviewer
description: Semantic and logic consistency reviewer for Markdown documentation. Use when reviewing documentation for terminology consistency, contradictions, cross-reference validity, formatting standards, voice/tone analysis, technical accuracy, and duplicate detection. Supports master-follower validation where follower docs are checked against authoritative master documents. Handles 50+ file documentation sets with resumable progress tracking. Triggers on requests like "review my documentation", "check docs for inconsistencies", "validate markdown files", "find contradictions in docs", or "audit documentation quality".
---

# MD-Reviewer: Markdown Documentation Reviewer

## ⛔ STOP - READ THIS FIRST

**This skill requires STRICT sequential processing.**

```
┌─────────────────────────────────────────────────────────────┐
│  ❌ FORBIDDEN: Reading multiple documents at once           │
│  ❌ FORBIDDEN: Skipping workspace creation                  │
│  ❌ FORBIDDEN: Processing followers before masters done     │
│  ❌ FORBIDDEN: Moving to next file without updating progress│
└─────────────────────────────────────────────────────────────┘
```

**You MUST follow the phases in order. Each phase has explicit STOP points.**

---

## Phase 1: SETUP (Do This First)

### Step 1.1: Ask Questions

Ask the user (wait for answers before proceeding):

```
1. Which documents are your master/authoritative sources?
2. Which documents are your follower?
3. Where should I create the _review/ workspace?
4. Output format: console, inline, report, or auto-fix?
5. How to handle conflicts between masters: ask, warn, or first-wins?
6. Any priority files to check first? (optional)
```

### Step 1.2: Create Workspace

**Locate this skill (`$BASE`):** Bundled scripts must run as `"$BASE/scripts/..."`, never bare — no harness sets the shell cwd to the skill directory. For `$BASE`, use the path the harness reported when this skill loaded (`Base directory for this skill: <path>` in Claude Code/OpenCode; `[Skill directory: <path>]` in omp; the SKILL.md's own directory in Cursor and Codex) verbatim. Only if none was reported, run this one-liner and capture the printed path as `$BASE`:

```bash
BASE=""; for d in "${MD_REVIEWER_HOME:-}" .agents/skills/md-reviewer .claude/skills/md-reviewer .cursor/skills/md-reviewer .opencode/skills/md-reviewer .codex/skills/md-reviewer ~/.agents/skills/md-reviewer ~/.claude/skills/md-reviewer ~/.cursor/skills/md-reviewer ~/.config/opencode/skills/md-reviewer ~/.omp/agent/skills/md-reviewer ~/.codex/skills/md-reviewer "$(ls -d ~/.claude/plugins/cache/*/claude-goodies/*/skills/md-reviewer 2>/dev/null | sort -V | tail -1)" "$(ls -d ~/.omp/plugins/cache/plugins/*___omp-goodies___*/skills/md-reviewer 2>/dev/null | sort -V | tail -1)" "$(ls -d ~/.codex/plugins/cache/*/codex-goodies/*/skills/md-reviewer 2>/dev/null | sort -V | tail -1)" "$(ls -dt ~/.cursor/plugins/cache/*/cursor-goodies/*/skills/md-reviewer 2>/dev/null | head -1)" "$(ls -dt ~/.cache/opencode/npm/*/*/node_modules/opencode-goodies/skills/md-reviewer 2>/dev/null | head -1)"; do [ -n "$d" ] && [ -f "$d/scripts/init_review.py" ] && { BASE="$d"; break; }; done; [ -n "$BASE" ] && echo "$BASE" || { echo "ERROR: md-reviewer not found in any known skills root — set MD_REVIEWER_HOME=<skill dir>" >&2; false; }
```

**⛔ MANDATORY: Run this command BEFORE reading any documents:**

```bash
python3 "$BASE/scripts/init_review.py" \
    --workspace <path> \
    --masters <file1,file2,...> \
    --output <format> \
    --on-master-conflict <mode>
```

### Step 1.3: Verify Workspace

Confirm `_review/` folder exists with:
- `config.json`
- `progress.md`
- `glossary.md`
- `master_facts.md`
- `findings/`

```
⛔ STOP: Do NOT proceed until workspace is created and verified.
```

---

## Phase 2a: MASTER EXTRACTION

### ⚠️ CRITICAL: ONE MASTER AT A TIME

```
❌ WRONG:
   Read(master1.md)
   Read(master2.md)
   Read(master3.md)
   [process all together]

✅ CORRECT:
   Read(master1.md)
   [extract claims, write to master_facts.md, update progress.md]
   "✓ Extracted master1.md: 23 claims, 12 terms"
   
   Read(master2.md)
   [extract claims, write to master_facts.md, update progress.md]
   "✓ Extracted master2.md: 18 claims, 8 terms"
```

### For EACH Master (One at a Time):

**Step 2a.1:** Read ONE master document

**Step 2a.2:** Extract from this document:
- Claims (tool, version, config, behavior, architecture, process, recommendation, limitation)
- Terminology (key terms with definitions)
- Anchors (heading → anchor ID mapping)

**Step 2a.3:** Append to `_review/master_facts.md`

**Step 2a.4:** Update `_review/progress.md`:
```markdown
- [x] master1.md | claims: 23 | terms: 12 | anchors: 8
```

**Step 2a.5:** Output confirmation:
```
✓ Extracted master1.md: 23 claims, 12 terms, 8 anchors
```

**Step 2a.6:** Only NOW read the next master

```
⛔ STOP: After EACH master, you must:
   1. Write to master_facts.md
   2. Update progress.md
   3. Output confirmation message
   BEFORE reading the next master.
```

---

## Phase 2b: MASTER CONSOLIDATION

**⛔ Only begin when ALL masters show [x] in progress.md**

### Step 2b.1: Build Glossary

Merge all terminology into `_review/glossary.md`

### Step 2b.2: Check for Master Conflicts

Do any masters contradict each other? Write to `_review/findings/master-conflicts.md`

Handle based on config:
- `ask`: Stop and ask user
- `warn`: Log and continue
- `first-wins`: First master wins

### Step 2b.3: Update Progress

```markdown
- [x] Phase 2a: Master Extraction (3/3)
- [x] Phase 2b: Master Consolidation
```

### Step 2b.4: Display Gate Summary

```
═══════════════════════════════════════════════════════════════
PHASE 2 COMPLETE: Master Analysis
═══════════════════════════════════════════════════════════════

Masters analyzed: 3
Total claims extracted: 47
Glossary terms: 23
Internal conflicts: 1

Proceeding to validate 26 follower documents...
═══════════════════════════════════════════════════════════════
```

```
⛔ STOP: Display the gate summary BEFORE proceeding to Phase 3.
```

---

## Phase 3: FOLLOWER VALIDATION

### ⚠️ CRITICAL: ONE FOLLOWER AT A TIME

```
❌ WRONG:
   Read(chapter1.md)
   Read(chapter2.md)
   Read(chapter3.md)
   [check all together]

✅ CORRECT:
   Read(chapter1.md)
   [extract claims, compare to masters, write findings, update progress]
   "✓ [1/26] chapter1.md: 2 critical, 1 warning, 0 info"
   
   Read(chapter2.md)
   [extract claims, compare to masters, write findings, update progress]
   "✓ [2/26] chapter2.md: 0 critical, 3 warning, 1 info"
```

### For EACH Follower (One at a Time):

**Step 3.1:** Update progress.md to show in-progress:
```markdown
- [~] chapter1.md | IN PROGRESS
```

**Step 3.2:** Read ONE follower document

**Step 3.3:** Extract claims from this document

**Step 3.4:** Compare against master claims:
- Content consistency (contradictions, value mismatches)
- Terminology variants
- Formatting issues
- Voice & tone

**Step 3.5: Semantic Scope Check**

After extracting claims, perform this semantic comparison:

Ask yourself these questions about the current follower document:

┌─────────────────────────────────────────────────────────────────┐
│ SCOPE EXPANSION CHECK                                           │
│                                                                 │
│ Compare this follower against ALL master documents:             │
│                                                                 │
│ 1. What TOPICS does this follower discuss?                      │
│    (deployment, databases, authentication, platforms, etc.)     │
│                                                                 │
│ 2. For each topic, do the masters mention it at all?            │
│    - If YES: Does follower introduce NEW OPTIONS not in masters?│
│    - If NO: Follower introduces entirely new topic scope        │
│                                                                 │
│ 3. Are there any SOLUTIONS, METHODS, or APPROACHES in the       │
│    follower that masters never mention or hint at?              │
│                                                                 │
│ Examples that should trigger WARNING:                           │
│ • Master says "cloud deployment" → Follower adds "on-premise"   │
│ • Master says "PostgreSQL" → Follower mentions "MongoDB"        │
│ • Master says "REST API" → Follower documents "GraphQL"         │
│ • Master is silent on mobile → Follower has iOS instructions    │
│                                                                 │
│ For each scope expansion found, record:                         │
│ - What topic/domain                                             │
│ - What masters say (or "not mentioned")                         │
│ - What follower introduces                                      │
│ - Severity: WARNING                                             │
│ - Category: scope_expansion                                     │
└─────────────────────────────────────────────────────────────────┘

**Step 3.6:** Write findings based on output format:
- Always write: `_review/findings/<filename>.json` (source of truth)
- If format is `console`, `report`, or `auto-fix`: also write `_review/findings/<filename>.md` (human readable)
- If format is `inline`: add `<!-- MD-REVIEW [SEVERITY]: <message> -->` comments directly into the source file (do NOT create a separate findings MD file)

**Step 3.7:** Update progress.md:
```markdown
- [x] chapter1.md | critical: 2 | warning: 1 | info: 0
```

**Step 3.8:** Output confirmation:
```
✓ [1/26] chapter1.md: 2 critical, 1 warning, 0 info
```

**Step 3.9:** Only NOW read the next follower

```
⛔ STOP: After EACH follower, you must:
   1. Write findings JSON file
   2. Write findings MD file
   3. Update progress.md
   4. Output confirmation with count
   BEFORE reading the next follower.
```

---

## Phase 4: CROSS-DOCUMENT ANALYSIS

**⛔ Only begin when ALL followers show [x] in progress.md**

1. Build global link map from all documents
2. Validate cross-references (links, anchors, images)
3. Detect duplicate content
4. Write to `_review/cross_refs.md`

---

## Phase 5: OUTPUT GENERATION

1. Consolidate per-file findings into:
   - `_review/findings/critical.md`
   - `_review/findings/warnings.md`
   - `_review/findings/info.md`

2. Generate output based on format:
   - `console`: Print summary to terminal
   - `inline`: Add `<!-- MD-REVIEW [SEVERITY]: <message> -->` comments directly into the original source files being reviewed (NOT new files)
   - `report`: Generate `_review/report.md`
   - `auto-fix`: Apply fixes (confirm critical changes)

3. Mark complete in progress.md

---

## Filename Convention for Findings

Replace `/` with `__` in paths:
- `docs/install.md` → `docs__install.md.json`
- `chapters/01_intro.md` → `chapters__01_intro.md.json`

---

## JSON Findings Format

```json
{
  "file": "docs/install.md",
  "checked_at": "2025-01-15T10:35:00Z",
  "summary": {"critical": 2, "warning": 3, "info": 1},
  "findings": [
    {
      "id": "CC-001",
      "severity": "critical",
      "category": "content_consistency",
      "type": "value_mismatch",
      "line": 23,
      "message": "Version mismatch",
      "follower_claim": {"subject": "version", "object": "3.2.1"},
      "master_claim": {"subject": "version", "object": "3.4.0"},
      "master_source": {"file": "README.md", "line": 15},
      "confidence": 0.95
    }
  ]
}
```

---

## Check Types

| Category | Severity | Description |
|----------|----------|-------------|
| Content Consistency | Critical/Warning | Claims contradicting masters |
| Terminology | Warning/Critical | Term variants, conflicts |
| Formatting | Warning | Heading hierarchy, code blocks |
| Cross-references | Critical | Broken links, dead anchors |
| Voice & Tone | Warning/Info | Style inconsistencies |
| Duplicates | Info | Copy-paste that may drift |

---

## Reference Documents

- `references/check-types.md` - Detection patterns
- `references/content-consistency.md` - Claim extraction
- `references/output-formats.md` - Output templates
- `references/semantic-analysis.md` - Analysis techniques

---

## Resuming Interrupted Reviews

If `_review/progress.md` exists:
1. Read progress.md
2. Check "Phase Status" section
3. Find first `[ ]` or `[~]` file
4. Resume from that exact point

---

## Summary: The Golden Rules

```
┌─────────────────────────────────────────────────────────────┐
│  1. ALWAYS create workspace first (init_review.py)          │
│  2. Process ONE file at a time (never batch)                │
│  3. Write findings BEFORE moving to next file               │
│  4. Update progress.md AFTER each file                      │
│  5. Complete ALL masters BEFORE any followers               │
│  6. Display gate summary between Phase 2 and Phase 3        │
└─────────────────────────────────────────────────────────────┘
```
