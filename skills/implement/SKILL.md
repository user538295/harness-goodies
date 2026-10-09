---
name: implement
description: >
  Execute an existing plan or task-breakdown file — implement the next task, or loop through every
  remaining task — each task built test-first (TDD), self-reviewed, and committed as its own commit.
  Use when the user runs /implement or explicitly asks to implement/execute the tasks in a specific
  plan file, e.g. "implement next <file>", "implement all <file>", "implement all inline <file>".
  Requires a plan or task-breakdown file as argument. Do NOT use for creating or updating plans
  (that is plan-maker) or for ad-hoc code edits that are not driven by a plan file.
---

# Implement

Execute an existing plan or task-breakdown file. **This is not a guideline. You MUST follow the instructions step-by-step, precisely. You MUST NOT make shortcuts, or override the instructions!**

`$ARGUMENTS` holds an optional leading mode keyword plus the plan-file path.

## Step 0 — Locate this skill (`$BASE`)

`$BASE` is this skill's own directory (the one holding this `SKILL.md`). Every bundled file this skill names is written **relative to the skill root** (`scripts/plan-progress.sh`, and the templates in `scripts/`, per the Agent Skills spec) and must always be resolved as `$BASE/<relative path>` — never run bare: no harness sets the shell's working directory to the skill root, and the shell must stay in the user's repository.

**Primary — use the directory your harness reported when this skill loaded.** Claude Code and OpenCode print `Base directory for this skill: <path>`; omp prints `[Skill directory: <path>]`; in Cursor, use the directory of the `SKILL.md` file you were given. Take that path verbatim as `$BASE`.

**Fallback — only if no directory was reported**, run this harness-neutral locator (an explicit `IMPLEMENT_HOME=<skill dir>` wins; then project-level roots, then user-level roots of Claude Code, Cursor, OpenCode, omp and Codex, then the newest Claude Code plugin cache; symlinks are followed, stale ones skipped):

```bash
BASE=""; for d in "${IMPLEMENT_HOME:-}" .agents/skills/implement .claude/skills/implement .cursor/skills/implement .opencode/skills/implement .codex/skills/implement ~/.agents/skills/implement ~/.claude/skills/implement ~/.cursor/skills/implement ~/.config/opencode/skills/implement ~/.omp/agent/skills/implement ~/.codex/skills/implement "$(ls -d ~/.claude/plugins/cache/*/claude-goodies/*/skills/implement 2>/dev/null | sort -V | tail -1)" "$(ls -d ~/.omp/plugins/cache/plugins/*___omp-goodies___*/skills/implement 2>/dev/null | sort -V | tail -1)" "$(ls -dt ~/.cursor/plugins/cache/*/cursor-goodies/*/skills/implement 2>/dev/null | head -1)" "$(ls -dt ~/.cache/opencode/npm/*/*/node_modules/opencode-goodies/skills/implement 2>/dev/null | head -1)"; do [ -n "$d" ] && [ -f "$d/scripts/plan-progress.sh" ] && { BASE="$d"; break; }; done; [ -n "$BASE" ] && echo "$BASE" || { echo "ERROR: implement not found in any known skills root — set IMPLEMENT_HOME=<skill dir>" >&2; false; }
```

Capture the printed path as `$BASE`. On the error, stop and show it to the user. All later script calls use `"$BASE/scripts/plan-progress.sh"` (and the templates in `"$BASE/scripts/"` as the documented fallback when the script errors — see NEXT mode, Step 1).

## Step 1 — Parse arguments and select the mode

Parse `$ARGUMENTS` deterministically. The **first whitespace-separated token** is a mode keyword **only if it is exactly** `next`, `all`, or `inline` (case-insensitive); anything else is part of the path. When the first token is `all`, the **second** token is consumed as the `inline` keyword **only if it is exactly** `inline` (case-insensitive); otherwise the second token and everything after it is the path (so a file literally named `inline …` is treated as a path, not a keyword). Symmetrically, when the first token is `inline`, the **second** token is consumed as the `all` keyword **only if it is exactly** `all` (case-insensitive); otherwise the second token and everything after it is the path. The two-token prefixes `all inline` and `inline all` both mean **forced-inline ALL**; there is **no `next inline` form** (`next` never takes a second keyword — anything after `next` is the path). Everything after the recognized keyword(s) is the plan-file path — **handle a path that contains spaces** (take the entire remainder verbatim, do not split it).

- **`next <file>`** → **NEXT mode**: implement only the next uncompleted task (the full NEXT-mode Steps 1–7 below).
- **`all <file>`** → **ALL mode**: loop over every remaining task. Spawn ONE subagent per task via your harness's subagent tool when one exists (Claude Code's `Agent`, OpenCode's / omp's / Cursor's equivalent); **AUTOMATICALLY fall back to the INLINE loop** (no subagents) only when your harness exposes no subagent tool at all. See "ALL mode (subagents)" — it performs the capability check and switch.
- **`all inline <file>` / `inline all <file>` / `inline <file>`** → **ALL mode forced INLINE** (never spawn subagents). Go straight to "ALL mode (inline)".
- **`<file>` only (no keyword)** → **FIRST resolve the plan path** via the shared "Resolve the plan file & companion plan" section below (`test -f`, else fuzzy search / ask the user). Only once the path resolves to a real file, run `"$BASE/scripts/plan-progress.sh"` on it to count remaining tasks:
  - **Exit 0** (header printed, tasks remain) → compute the remaining count from the header's `(COMPLETED/TOTAL tasks)` figure as **TOTAL − COMPLETED** (the flat header also prints a `Remaining` line, but the phased header does not — so derive it from `COMPLETED/TOTAL`, which both templates print): exactly 1 remaining → proceed in NEXT mode; more than 1 remaining → **ASK** the user whether to do just the next task or all of them. In a **NON-INTERACTIVE / headless run** (e.g. `claude -p`, no user to answer) do NOT hang: default to NEXT mode and print a one-line note that it defaulted (mention "add 'all' to run the whole plan"). (Exit 0 never means zero remaining — the script emits exit 1 for that; see below.)
  - **Exit 1** (all complete) → report the plan is already complete and stop.
  - **Exit 3** (no task section / empty section — the script printed a clear "no tasks" message) → report that message to the user and **STOP**: the plan has nothing to implement. Do NOT fall through to NEXT mode.
  - **Non-zero (exit 2 usage/not-found — shouldn't occur after the path is resolved — or any other unexpected code):** the task count cannot be determined. Do NOT leave the branch undefined — in an interactive run, ask the user whether to proceed in NEXT mode or fix the plan file; in a **headless run**, default to NEXT mode and print a one-line note that the count was undeterminable (script exit N) and it defaulted (NEXT mode's Step 1 fallback recomputes progress from the file directly).
- **no argument at all** → stop and ask: "Please provide a plan file path (optionally prefixed with 'next' or 'all')."

---

## Resolve the plan file & companion plan (shared)

This section is used by NEXT mode and by both ALL loops (once per task iteration in the loops).

**Resolve the plan-file path.** If the parsed path names an existing file (`test -f`), use it. If it does not exist: search for `**/*.md` files (your runtime's glob primitive), filter by keyword match on name or path, then read each keyword-matched candidate and check it contains at least one unchecked task line (`- [ ]`); discard any that does not.
- Exactly one match → use that path, note it to the user.
- Multiple matches → stop and ask the user to choose: "Found multiple matching plan files: [list them]. Please provide the full path to the one you want."
- No match → stop and ask the user: "Could not find a plan file matching '<path>'. Please provide the full path."

**Companion plan detection.** The resolved file may be a self-contained plan, or a task-breakdown file whose scenario/contract definitions (`S#`/`C#`), open questions (`Q#`), architecture, contracts, and acceptance criteria live in a separate **companion plan**. Resolve the companion plan in this order:

1. **References link.** If the task-breakdown file has a `## References` section, resolve its `**Plan:**` bullet's Markdown link (target: the `*-team-plan.md` file) relative to the task-breakdown file's directory. The producer (`create-tasks`) co-emits this link and the `plan:` key below, both naming the same sibling, so they normally agree; the link is canonical because, as a relative path, it still resolves correctly if a plan is ever hand-moved. If both are present but resolve to two *different existing* files, STOP and report the inconsistency — that is a corrupt task file, not a precedence choice.
2. **Frontmatter `plan:` key (sibling fallback).** Else, if the frontmatter has a `plan:` key naming a *different* file, resolve it relative to the task-breakdown file's directory (this assumes the plan is a sibling — the key is a bare filename).
3. **Self-contained.** Else — no References link and no `plan:` key, or the key points at the task-breakdown file itself — there is no companion plan; the file holds everything.

If step 1 or 2 identifies a companion plan path but that file **does not exist** (moved/renamed), STOP and report it — do NOT silently fall through to self-contained, because a split plan implemented without its context will resolve `S#`/`C#` wrong.

**Terminology used throughout:** "**the task-breakdown file**" = the resolved plan path (holds the checkboxes; this is what progress, check-off, and the commit target). "**the companion plan**" = the resolved plan file (context only — never edited, never committed by this run). When a companion plan exists, note its path — you will pass it to every agent below. This loop/run never checks off, stages, or commits the companion plan.

---

## NEXT mode

Implement the next uncompleted task. **You MUST follow the instructions step-by-step, precisely. You MUST NOT make shortcuts!**

### Step 1: Show progress and identify the next task

Resolve `$BASE` (Step 0) and the plan file + companion plan (shared section above), then run:

```
bash "$BASE/scripts/plan-progress.sh" "<plan-path>"
```

**Exit 0 (normal):** print the human-readable block verbatim. Read `NEXT_TASK_NAME` from the machine-readable lines, then read the task-breakdown file to extract the full task details: description, sub-items, and — for a self-contained plan — acceptance criteria. (For a task-breakdown file with a companion plan, per-task acceptance criteria are the `S#`/`C#` the task `completes`; those are **defined in the companion plan**, not in the task-breakdown file — see the shared section above.)

**Exit 1:** all tasks are complete — stop here, report completion.

**Any other exit code or script not found (fallback):** read the appropriate template from `$BASE/scripts/` (`progress-header-phased.template` if the plan has `###` headings within the task section (`## Task` / `## Tasks` / `## Task breakdown`), `progress-header-flat.template` otherwise), compute the placeholder values by reading the task-breakdown file directly, substitute them, and print the result. Then continue as normal — including the companion-plan detection above — a failed script must not block progress.

### Step 2: Implement (TDD)

**Determine the most appropriate agent type for this task, then spawn it to perform the implementation.** Pass it the full task description, sub-items, the working directory, the task-breakdown file path, and — when Step 1 detected one — the **companion plan path**. Instruct it to follow these instructions exactly:

**You MUST follow these instructions**:

**CONTEXT — if a companion plan was detected in Step 1:** read the companion plan file FIRST, before any other work. This task's `completes` field cites the scenarios/contracts (`S#`/`C#`) it must satisfy — those, plus the architecture, contracts, and acceptance criteria, are **defined only in the companion plan**; the task-breakdown file gives you the IDs, the plan gives you their meaning. (`needs` cites predecessor task IDs, not `S#`/`C#`; `Q#` are the plan's open questions — read them for context but they are not yours to resolve.) Read only the companion plan itself — do NOT chase its own `brief:`/References links; one hop is enough. This applies to every task, including documentation and close-out tasks that reference the plan's sections by name.

**SCOPE — non-negotiable:**
- Implement EXACTLY ONE task: the first uncompleted task in the plan. Do not preview, prepare, or implement any subsequent task.
- Touch only what THIS task requires: files the task description names, PLUS any minimal side-effect edits the change forces (broken sibling tests, import updates, manifests). No unrelated refactors, cleanups, or "while I'm here" edits on files this task does not require.

**FORBIDDEN:**
- Do NOT modify the task-breakdown file, or the companion plan, at all. Checkboxes are toggled by the orchestrator in Step 5, not by you.
- Do NOT create any git commits — leave all changes as uncommitted working tree modifications.

If the task produces testable code output, follow strict TDD:

1. **Write tests first** — unit, integration, and live/end-to-end tests covering the new behaviour and the task's acceptance criteria. Tests must fail at this point (red).
2. **Run the tests** — confirm they fail for the right reasons. One test run at a time: before starting any run, verify no earlier test run is still alive with `ps -Ao comm=,args= | awk '$1 ~ /[Pp]ython/ && /\/pytest/'` (must be empty — `pgrep -fl pytest` self-matches the shell and must not be used). Overlapping runs multiply parallel workers and can OOM the machine.
3. **Implement the functionality** — write only enough code to make the tests pass (green).
4. **Run the tests again** — all new and existing tests must pass before continuing.

If the task has no testable code output (documentation, configuration, CI changes, diagrams), skip the TDD cycle and implement directly.

No assumptions — read all relevant code, documentation, and context first.

Return a summary of what was implemented and which files were changed.

Instruct the agent with all of the above. Wait for the agent to return before continuing.

### Step 3: Critical review

**You MUST run `/iterative-review`** (or `claude-goodies:iterative-review` if that is the name shown in your skill list)**.** When a companion plan was detected in Step 1, pass its path in the review target and instruct the reviewers to read it first — the acceptance criteria and `S#`/`C#` this task must satisfy are defined there, not in the task-breakdown file.

After `/iterative-review` returns — regardless of what its Verdict says — you MUST immediately continue to Step 4. The Review Summary is a sub-task result, not your completion signal. **Do NOT stop here.**

### Step 4: Run tests

You MUST run the full test suite. In Claude Code the `Bash` foreground ceiling is **~120 seconds** — commands that run longer are auto-backgrounded, ending your turn before any commit lands. To run a suite that takes longer than 120 s:

1. Launch pytest with `run_in_background: true` — capture the process ID from the result.
2. Immediately call the `Monitor` tool on that process — it streams stdout line-by-line and **keeps your session alive** for the full duration.
3. Read the Monitor result to determine pass/fail and continue to Step 5.

In Cursor or other harnesses without `Monitor`, fall back to the *task-relevant subset* (tests added in Step 2 plus their immediate neighbourhood) as a blocking call; report the partial scope in Step 7.

**Before any test run, verify no earlier run is still alive:** `ps -Ao comm=,args= | awk '$1 ~ /[Pp]ython/ && /\/pytest/'` must be empty (`pgrep -fl pytest` self-matches the shell — do not use it). Stacked suite runs multiply parallel workers and have OOM-crashed a 48 GB machine.

If the test command reports failures:

1. Spawn the same agent type as Step 2. Pass it: the full test failure output, the task description, the companion plan path with the "read it first" instruction if one was detected in Step 1 (the acceptance criteria live there), the SCOPE and FORBIDDEN constraints from Step 2, the working directory, and — on retries — the output and changes from all prior fix attempts so the agent knows what was already tried and why it failed.
2. Re-run the same command.
3. Repeat until green, or three consecutive fix attempts all fail — in which case stop and report the failures for human review.

Only continue to next Step once your chosen test scope is fully green.

### Step 5: Check off completed items

Update the task-breakdown file: mark the implemented task and every completed sub-item as done (`[ ]` → `[x]`). Be precise — only check what was actually implemented and verified. Do not check items that were skipped or only partially completed.

### Step 6: Commit

**NON-NEGOTIABLE: one task = one commit.** Never batch multiple tasks into a single commit.

Commit all changes for this task — implementation files AND the updated task-breakdown file (with its newly checked boxes) — in a single commit with a message derived from the actual task content. The companion plan, if any, is not modified and is not part of the commit.

### Step 7: Report

Output a concise report for this task in the following exact form. Do NOT prose, you MUST report it and in the exact form. This is non-negotiable.
> **Task [N.M] Implementation report:**
> - Implemented: [What was implemented]
> - Tests: [Test results summary, max 250 chars]
> - Feature loss or deviation: [Any feature loss or deviation from the task spec (be extremely precise); max 250 chars]
> - Unresolvable oscillations: [Any unresolvable oscillations from the review loop; max 250 chars]
> - Task [N.M] — checked at line [NNN] and committed ([full-hash])

---

## ALL mode (subagents)

Loop over every remaining task, spawning ONE subagent per task. **You MUST follow the instructions step-by-step, precisely. You MUST NOT make shortcuts, or override the instructions!**

### Step -1: Pick the execution mode — subagents vs inline

Decide by **capability, not harness brand**. **Check the tools actually available to you** in this run: if any tool spawns a subagent — Claude Code's `Agent` (including headless `claude -p`), or OpenCode's / omp's / Cursor's equivalent subagent/`Task` tool — proceed in **subagent mode** (Loop body below). Switch to **ALL mode (inline)** only when no such tool is present at all.

Inform the user which mode was selected and why before continuing. In inline mode the ALL loop does NOT wrap each task in its own per-task subagent — tasks run in the current context via NEXT mode (whose own implementation and `/iterative-review` steps still spawn agents where a subagent tool exists, and act directly where none does).

### Step 0: Resolve the plan file

Resolve `$BASE` (Step 0 above) and the plan file + companion plan via the shared "Resolve the plan file & companion plan" section. If `$ARGUMENTS` had no path at all, stop and ask the user: "Please provide a plan file path or keyword to search for." The resolved file is "the plan file" for the loop below.

### Loop body

**Termination condition:** All tasks in the plan file are marked complete (`plan-progress.sh` returns exit 1). Repeat all steps until this condition is met.

**Terminology:** "the plan file" throughout is the resolved task-breakdown file — the one NEXT mode checks off and commits. It may reference a separate **companion plan** that NEXT mode resolves and reads as **read-only context** (its Step 1); this loop never checks off, stages, or commits the companion plan.

Each iteration:

#### 1. **Progress** Run, replacing `<plan-path>` with the resolved file path:
   ```
   bash "$BASE/scripts/plan-progress.sh" "<plan-path>"
   ```
   - Exit 1 → all tasks complete — stop.
   - Exit 2 or 3 → stop and report the error.
   - Any other exit code → stop and report the unexpected exit code.
   - Exit 0 → tasks remain, note the reported NEXT_TASK_NAME and continue.

   Always run `plan-progress.sh` in every new iteration and **copy + show the first two lines of the output of the script to the user. Exactly in the same format, do NOT reformat it. Do NOT prose it!**

#### 2. **Spawn a subagent to implement this task.** First, run:
   ```
   date '+%H:%M:%S' 2>/dev/null || powershell -Command "Get-Date -Format 'HH:mm:ss'" 2>/dev/null || echo "(time unavailable)"
   ```
   Print the result to the user in this exact format (brackets are literal, e.g. `Launching task 6.1 at [12:50:31]`) and do NOT prose it: `Launching task <NEXT_TASK_NAME> at [HH:MM:SS]`

   Spawn the subagent with your harness's subagent tool — in **Claude Code**, the `Agent` tool (`subagent_type: general-purpose`, `run_in_background: true`); in **OpenCode / omp / Cursor**, their equivalent subagent tool (run it in the background if that tool supports it). Step -1 routed only harnesses that lack any subagent tool to ALL mode (inline), so this loop always has a subagent tool to use.

   You MUST give this prompt to the subagent (a fresh general-purpose subagent does NOT already have this skill's NEXT-mode text — it must actually invoke the skill). **Before spawning, substitute the resolved task-breakdown file path for every `<plan-path>` below — the subagent must receive the real resolved path, exactly as step 1 substitutes it into the `plan-progress.sh` call, not a literal `<plan-path>`:**
   > Invoke the `implement` skill in NEXT mode: run `/implement next <plan-path>` — implement the next uncompleted task (NEXT-mode Steps 1–7). If your skill list shows it as `claude-goodies:implement`, invoke that skill with `next <plan-path>`.
   >
   > **SCOPE — non-negotiable:**
   > - Implement EXACTLY ONE task: the first uncompleted task in the plan. Do not preview, prepare, or implement any subsequent task.
   > - Touch only what THIS task requires: files the task description names, PLUS any minimal side-effect edits the change forces (broken sibling tests, import updates, manifests). No unrelated refactors, cleanups, or "while I'm here" edits on files this task does not require.
   >
   > **ACCEPTANCE CRITERIA:**
   > **YOUR TURN ENDS ONLY when ALL of these are true.**
   > A. Implementation files modified per the task spec.
   > B. NEXT-mode Step 3 completed, you did run `/iterative-review`.
   > C. NEXT-mode Step 4 tests pass — OR, for doc-only tasks where NEXT-mode Step 2 explicitly permits skipping the TDD cycle (documentation, configuration, CI changes, diagrams), the inline verification specified by the task spec succeeded.
   > D. Plan file's `- [ ]` for this task flipped to `- [x]`.
   > E. A single git commit exists containing the implementation + plan checkoff.
   > F. NEXT-mode Step 7 report emitted.
   >
   > **Repeat until ALL items in Acceptance Criteria are completed. It is a MUST!**
   >
   > **FORBIDDEN:**
   > - Do NOT use `--no-verify`, `--amend`, or any pre-commit hook bypass.
   > - Do NOT skip NEXT-mode Steps 4, 5, 6, 7 even if `/iterative-review` returned "no issues remain". Review convergence is a green light to proceed to NEXT-mode Step 4 — it is NOT a signal to terminate your turn.
   > - Do NOT bundle this task with adjacent ones into a single commit.
   > - Do NOT spawn a nested ALL-mode loop from inside your task work.
   > - Do NOT modify the plan file beyond toggling THIS task's checkbox, and never modify a companion plan the task may reference (it is read-only context, never committed).
   > - MUST NOT make shortcuts! MUST follow the instructions step-by-step precisely.

   Then wait for the subagent to return before continuing.

#### 3. **Recovery check and report — verify the task landed.**

   - Check that the task is checked in the plan file, and check that the related files are committed.
   - **Always report to the user in the following exact form. Follow the format literally. You and the subagents must follow the instructions strictly. Don't miss that!**
      - If there was no violation (task was already checked and committed), tell the user exactly in this format:
         - Task [N.M] ✅ — checked (line [NNN]) and committed ([short-hash]). Checkpoint verified (checkbox + commit).
      - If there was any other violation, tell the user exactly in this format:
         - Task [N.M] failed ❌:
            - **What:** [was the violation; max 250 chars]
            - **Why:** [was that (no assumptions, fact check!); max 250 chars]
            - **Fix:** [did you fix it?; max 250 chars]
            - **Prevention:** [how you will prevent it in the future.; max 250 chars]
            save the learnings to prevent this next time;
   - If the task is **not checked** (regardless of commit state) → **you MUST go to step 2 ("Spawn a subagent to implement this task") and redo the full process. This is non-negotiable. You MUST NOT decide differently!** Track attempt count — after 3 failed attempts, stop and report: "Task [N.M] failed after 3 attempts. Manual intervention required."
   - If the task **is checked but the files are not committed** → commit only: run `git status --porcelain` to identify all modified and untracked files (covers both tracked modifications and newly created files). Cross-reference each file against the task description to determine membership. Stage by explicit file path only those that belong to this task's implementation. Do NOT use `git add -A` or `git add .` — that risks including unrelated working-tree changes. The companion plan (if the task-breakdown file references one) is read-only context — never stage it; if it shows as modified, report that as a FORBIDDEN violation rather than committing it. If uncertain whether any *other* file belongs to this task, include it and note the uncertainty in the commit message. Never leave modified task files unstaged without reporting them. Then commit. Do NOT respawn the subagent.
     Report this as a violation and do NOT prose it:
        - Task [N.M] partial ⚠️ — checked but not committed; committed now ([short-hash]).
           - **What:** [task was checked but commit was missing (criterion E violated); max 250 chars]
           - **Why:** [determine from context — no assumptions; max 250 chars]
           - **Fix:** [committed the missing changes above; max 250 chars]
           - **Prevention:** [how to prevent this in the future; max 250 chars]
           save the learnings to prevent this next time;

---

## ALL mode (inline)

> **Inline execution mode.** The ALL loop does not wrap each task in its own per-task subagent — each task runs in the current context via NEXT mode, whose own implementation (Step 2) and `/iterative-review` (Step 3) steps still apply and still spawn agents where a subagent tool exists (degrading to acting directly in harnesses without one). Also used automatically by ALL mode (subagents) when the harness exposes no subagent tool at all.

**This is not a guideline. You MUST follow the instructions step-by-step, precisely. You MUST NOT make shortcuts, or override the instructions!**

### Step 0: Resolve the plan file

Resolve `$BASE` (Step 0 above) and the plan file + companion plan via the shared "Resolve the plan file & companion plan" section. If `$ARGUMENTS` had no path at all, stop and ask the user: "Please provide a plan file path or keyword to search for." The resolved file is "the plan file" for the loop below.

### Loop body

**Termination condition:** All tasks in the plan file are marked complete (`plan-progress.sh` returns exit 1). Repeat all steps until this condition is met.

**Terminology:** "the plan file" throughout is the resolved task-breakdown file — the one NEXT mode checks off and commits. It may reference a separate **companion plan** that NEXT mode resolves and reads as **read-only context** (its Step 1); this loop never checks off, stages, or commits the companion plan.

Each iteration:

#### 1. **Progress** — identical to ALL mode (subagents) Loop body step 1: apply it here. Run `bash "$BASE/scripts/plan-progress.sh" "<plan-path>"`, handle the exit codes the same way (exit 1 → all complete, stop; exit 2 or 3 → stop and report the error; any other exit code → stop and report it; exit 0 → tasks remain, note `NEXT_TASK_NAME` and continue), and show the first two lines of the script output to the user verbatim (do NOT reformat, do NOT prose).

#### 2. **Implement next task.** First, run:
   ```
   date '+%H:%M:%S' 2>/dev/null || powershell -Command "Get-Date -Format 'HH:mm:ss'" 2>/dev/null || echo "(time unavailable)"
   ```
   Print the result to the user in this exact format (brackets are literal, e.g. `Starting task 6.1 at [12:50:31]`): `Starting task <NEXT_TASK_NAME> at [HH:MM:SS]`

   You MUST follow these rules:

   Run NEXT mode (the NEXT-mode Steps 1–7 above) inline on plan file `<plan-path>` — implement the next uncompleted task in the current context.

   **SCOPE — non-negotiable:**
   - Implement EXACTLY ONE task: the first uncompleted task in the plan. Do not preview, prepare, or implement any subsequent task.
   - Touch only what THIS task requires: files the task description names, PLUS any minimal side-effect edits the change forces (broken sibling tests, import updates, manifests). No unrelated refactors, cleanups, or "while I'm here" edits on files this task does not require.

   **YOUR TURN ENDS ONLY when ALL of these are true:**
   A. Implementation files modified per the task spec.
   B. NEXT-mode Step 3 completed, you did run `/iterative-review`.
   C. NEXT-mode Step 4 tests pass — OR, for doc-only tasks where NEXT-mode Step 2 explicitly permits skipping the TDD cycle (documentation, configuration, CI changes, diagrams), the inline verification specified by the task spec succeeded.
   D. Plan file's `- [ ]` for this task flipped to `- [x]`.
   E. A single git commit exists containing the implementation + plan checkoff.
   F. NEXT-mode Step 7 report emitted.

   **Repeat until ALL items in Acceptance Criteria are completed. It is a MUST!**

   **FORBIDDEN:**
   - Do NOT use `--no-verify`, `--amend`, or any pre-commit hook bypass.
   - Do NOT skip NEXT-mode Steps 4, 5, 6, 7 even if `/iterative-review` returned "no issues remain". Review convergence is a green light to proceed to NEXT-mode Step 4 — it is NOT a signal to terminate your turn.
   - Do NOT bundle this task with adjacent ones into a single commit.
   - Do NOT modify the plan file beyond toggling THIS task's checkbox, and never modify a companion plan the task may reference (it is read-only context, never committed).
   - MUST NOT make shortcuts! MUST follow the instructions step-by-step precisely.

#### 3. **Recovery check and report** — the recovery check (checkbox + commit verification), the **checked-but-not-committed commit-only path** (including the `git status --porcelain` / explicit-path staging / never-stage-the-companion-plan rules), the **3-attempt cap**, and the exact ✅ / ⚠️ / ❌ report formats are **identical to ALL mode (subagents) Loop body step 3 — apply them here verbatim**, with these two mode-specific substitutions:

   - On the **not-checked** path, "go to step 2" means this section's step 2 ("Implement next task", re-run NEXT mode inline in the current context), not "Spawn a subagent".
   - On the **checked-but-not-committed** path, after committing, do NOT re-run NEXT mode (the subagents section's "Do NOT respawn the subagent").
