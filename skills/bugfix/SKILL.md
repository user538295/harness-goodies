---
name: bugfix
description: >-
  Fix a bug end-to-end via a four-agent pipeline: reproduce it with a failing test (code inspection is NOT acceptable proof — only a failing test counts), fix it with TDD, run /iterative-review to clear every issue, then /commit. Use when the user reports a bug and wants it fixed, or invokes /bugfix <description-or-path>. The argument is either a free-text bug description OR a path to a bug-report file — detect which and read the file if it is a path. Always assume the bug is real and exists.
---

# Bugfix

Orchestrates a fix for one bug through four sequential agents. Each step depends on the previous one, so run them in order — do not parallelize.

## Input

`$ARGUMENTS` is the bug. It is **either**:
- a **path** to an existing file (e.g. `Documentation/Backlog/*bug*.md`) — if it resolves to a readable file, read it and use its full contents as the bug spec, **or**
- **free-text** describing the bug — use it verbatim.

Decide with one check: does the argument resolve to an existing file? If yes, read it; otherwise treat it as prose. If `$ARGUMENTS` is empty, ask the user what the bug is before doing anything else.

**Core assumption passed to every agent: the bug is real and it exists.** No agent may conclude "there is no bug" or "cannot reproduce, closing."

## Pipeline

Every agent you spawn below gets this instruction, verbatim, at the top of its prompt: **"Be accurate. Follow these instructions precisely."**

### 1. Reproduce (Agent)

Spawn one agent (type `general-purpose`, `model: "opus"`) to reproduce the bug **with a test**:

- It MUST write a **new failing test** that fails *because of this bug*. **Verification by reading the code is NOT acceptable** — a code-level argument that "this looks wrong" does not count. The only accepted proof is a test that fails, and fails for the reason the bug describes.
- Assume the bug is real. If the first test does not fail, the test is wrong, not the bug — the agent iterates on the test until it fails for the right reason.
- The test is a **permanent regression test**: place it in the project's test suite following existing conventions, named and located so it stays after the fix.
- Return: the test's path + name, the exact command to run it, and the captured failing output proving the bug.

Do not continue until this agent returns a genuinely failing test. If it cannot produce one after honest effort, stop and report that to the user — do not proceed to fix a bug you could not reproduce.

### 2. Fix with TDD (Agent)

Spawn one agent (type `general-purpose`, `model: "opus"`) to fix the bug:

- Pass it the failing test from step 1 (path, command, output).
- TDD: the failing test is the spec. Make the smallest change that makes it pass. **Do not delete, skip, weaken, or edit the reproduction test to make it pass** — the production code must change.
- Run the reproduction test; it must now pass. Then run the surrounding test suite to confirm no regressions.
- Return: files changed, and confirmation the reproduction test + suite pass.

### 3. Update documentation (Agent)

Spawn one agent (type `general-purpose`, `model: "opus"`) to update any documentation the fix affects — behavior described in `Documentation/`, `CLAUDE.md`, `learnings.md`, changelogs, or docstrings that no longer match reality after the fix. Source code is the source of truth; when a doc and the changed code disagree, fix the doc. If nothing documented is affected, it returns "no doc changes needed" — do not invent documentation.

### 4. Iterative review (Agent)

Spawn one subagent (in Claude Code: type `general-purpose`, `model: "opus"`) that invokes the `iterative-review` skill (your skill list may show it with a plugin prefix, e.g. `claude-goodies:iterative-review`) via your harness's skill-loading mechanism on the uncommitted changes. It runs that skill's full loop — spawning reviewers and fix agents — until no Critical, Major, or Moderate issues remain, re-running tests after each fix pass. Relay its Review Summary.

### 5. Commit (Skill)

Invoke the `commit` skill (bare `/commit`, commit mode) to commit the related changes. If the commit is rejected (hook, nothing staged), report the error verbatim — do not work around it.

### 6. Report

Short, clear, well-structured — one line per field, no prose padding. Use exactly this shape:

```
## 🐛 Bugfix: <one-line bug title>

**Bug**      <what was wrong, one line>
**Repro**    <test path::name> — failed, now passes
**Fix**      <what changed, one line> (`file.py`)
**Docs**     <what doc updated, or "none affected">
**Review**   <N> issues fixed (<critical>/<major>/<moderate>), 0 remaining
**Commit**   <sha> — <n> files
```

If any step failed (no repro produced, commit rejected), replace that line with the failure reason and stop — do not fake later lines.
