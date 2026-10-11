---
name: iterative-review
description: >-
  Spawn multiple devil's advocate agents in parallel — plus a Brooks-Lint reviewer when /brooks-review is available, and a one-off clean-code catalog pass in cycle 1 when /clean-code-review is available — to review, then spawn fix agents to address all moderate+ issues, re-run tests after each fix pass. Repeat until no critical, major or moderate issues remain. Use when the user invokes /iterative-review or when another skill (e.g. implement, bugfix) needs its review-and-fix loop.
---

Target: $ARGUMENTS (if empty — including when another skill invoked this one and named the target in its request instead — use that named target, else the current uncommitted changes — run `git diff HEAD` to get them).

**Reviewer fallback — decide by capability, not harness brand.** If no `devils-advocate` agent type is available (e.g. Codex, whose plugins ship no agents) but a subagent tool exists, spawn generic subagents in step 1 instead and instruct each to first read and adopt the persona in `$BASE/../../agents/devils-advocate.md`, where `$BASE` is this skill's directory as announced by the harness (`Base directory for this skill: <path>` or `[Skill directory: <path>]`; in Cursor and Codex, the directory of this SKILL.md). If that file is missing, proceed with the rubric only and tell the user. If no subagent tool exists at all, run the review and fix passes inline and tell the user the review is not independent — this overrides the "MUST use the Agent tool" rule in step 1.

Severity rubric — use this consistently across all agents:
- **Critical**: blocks correctness, security, or safety
- **Major**: significant design flaw, missing requirement, or likely bug
- **Moderate**: suboptimal but workable
- **Minor**: style, naming, or nitpick

**Extra reviewers — detect both once, before the loop, with a single Bash call:**

```bash
find ./.claude/commands ./.claude/skills ./.cursor/commands ./.cursor/skills ./.codex/commands ./.codex/skills ./.opencode/command ./.opencode/skills ./.agents/commands ./.agents/skills ~/.claude/commands ~/.claude/skills ~/.claude/plugins ~/.cursor/commands ~/.cursor/skills ~/.codex/commands ~/.codex/skills ~/.config/opencode/command ~/.config/opencode/skills ~/.omp/agent/skills ~/.agents/commands ~/.agents/skills -iname 'brooks-review*' -print -quit 2>/dev/null | sed 's|^|BROOKS |'
find ./.claude/commands ./.claude/skills ./.cursor/commands ./.cursor/skills ./.codex/commands ./.codex/skills ./.opencode/command ./.opencode/skills ./.agents/commands ./.agents/skills ~/.claude/commands ~/.claude/skills ~/.claude/plugins ~/.cursor/commands ~/.cursor/skills ~/.codex/commands ~/.codex/skills ~/.config/opencode/command ~/.config/opencode/skills ~/.omp/agent/skills ~/.agents/commands ~/.agents/skills -iname 'clean-code-review*' -print -quit 2>/dev/null | sed 's|^|CCR |'
```

**Brooks-Lint (`/brooks-review`)** — a `BROOKS ` line printed:
- **Present → available.** Tell the user in one line: `✓ /brooks-review found — adding a Brooks-Lint reviewer alongside the devil's advocate agents.` Include the Brooks-Lint reviewer in step 1 of every cycle.
- **Absent → not available.** Tell the user in one line: `✗ /brooks-review not found — proceeding with devil's advocate agents only.` Skip every Brooks-Lint instruction below.

**Clean code catalog (`/clean-code-review`)** — a `CCR ` line printed. It runs **in cycle 1 only** (step 1b), and only when the target is code: uncommitted changes, a git ref/range, or source file paths. If the target is a plan file or a free-text description, skip it — it has no code to catalogue.
- **Present and target is code → available.** Tell the user in one line: `✓ /clean-code-review found — running one clean-code catalog pass in cycle 1.`
- **Otherwise → skip.** Tell the user in one line: `✗ /clean-code-review skipped — <not installed | target is not code>.` Skip step 1b.

**Loop — repeat until no Critical, Major, Moderate issues remain or you finished the cycle 3:**

1. **Use the Agent tool to spawn multiple `devils-advocate` agents in parallel** (agent type `devils-advocate`, or `claude-goodies:devils-advocate` if that is the name shown in your agent list; minimum 3), **each with `model: "claude-opus-4-8", effort: "medium"`**, each reviewing independently from a different angle: one focuses on correctness and edge cases, one on architecture and design, one on test coverage gaps. **You MUST use the Agent tool — never simulate reviews with Bash commands, heredocs, inline text, or any other method. Only actual Agent tool invocations count.** Pass each agent:
   - The current state of the target (diff / changed files / plan)
   - The full findings and fix history from all prior cycles (if any)
   - Instruction to label every issue with severity and a short ID prefixed by the current cycle number (`C1-I-1`, `C1-I-2`, …). Do not soften findings.
   - Every findings must be in this format: [short ID] - [filename:line_number] - [one-line short description which must be under 250 chars]

   **If `/brooks-review` is available** (per the check above), in the SAME parallel batch also spawn one agent **with `model: "claude-opus-4-8", effort: "medium"`** that invokes the `brooks-review` skill (fully qualified `brooks-lint:brooks-review`) via the Skill tool on the same target. Instruct it to: review read-only — make NO file edits and NO commits; map each Brooks-Lint finding onto the severity rubric above; and label each with a cycle-prefixed ID using a `B` marker (`C1-B-1`, `C1-B-2`, …). Its findings are peers of the devil's advocate findings in every step below. The Brooks review findings also must be in this format: [short ID] - [filename:line_number] - [one-line short description which must be under 250 chars]

1b. **Cycle 1 only — clean code catalog pass.** If `/clean-code-review` is available (per the check above), spawn **one `general-purpose` agent in the SAME parallel batch as step 1** whose only job is to invoke the `clean-code-review` skill (fully qualified `claude-goodies:clean-code-review`) via the Skill tool and return that skill's output verbatim and unabridged — the full synthesizer report with every finding line intact, not a summary of it. Instruct it: review read-only — make NO file edits and NO commits. The agent type must be `general-purpose`, not `devils-advocate`: `/clean-code-review` is itself a fan-out orchestrator that spawns its own 7 group agents plus a synthesizer, and only `general-purpose` has the Agent and Skill tools needed for that. Pass it the same target (no argument = its `local` default, which is a superset of this command's default of uncommitted changes — `local` also covers untracked files; a git ref/range or file paths pass through verbatim). Its severity rubric is identical to the one above — take its finding lines as-is and re-label each with a cycle-prefixed `CC` ID (`C1-CC-1`, `C1-CC-2`, …). Its findings are peers of the devil's advocate findings in every step below. Do not repeat this pass in later cycles — from cycle 2 on, the loop is driven by the DA agents and the Brooks-Lint reviewer. The findings also must be in this format: [short ID] - [filename:line_number] - [one-line short description which must be under 250 chars] 

2. **Consolidate** findings across all DA agents, the Brooks-Lint reviewer (if it ran), and the clean code catalog (cycle 1), deduplicating by root cause. Every findings must be in this format: [short ID] - [filename:line_number] - [one-line short description which must be under 250 chars]

3. If the consolidated list contains no Critical, Major, Moderate issues — the review loop is complete. Go to the summary below. (This ends the review loop only — not the calling skill's turn.)

4. **Spawn the most appropriate agent(s) to fix** all issues (Critical, Major, Moderate, Minor). Pass the full consolidated findings. Apply fixes to the actual files. Note which issue ID each fix resolves. **Fix agents must NOT create git commits** — all changes stay as uncommitted working tree modifications.

5. **Re-run the full automated test suite.** In Claude Code the foreground ceiling is ~120 s — use `run_in_background: true` + `Monitor` for suites that take longer (Monitor streams output and keeps the session alive). All tests must pass before the next cycle. If tests fail, spawn the most appropriate agent to resolve them first. You can skip this if you didn't touch the code.

6. **Convergence check**: if the same issues (same root cause) reappear that were already fixed in a prior cycle, mark them as **unresolvable oscillations**, stop the loop, and report them.

7. Go to step 1 with the updated target state.

**After the loop, output:**

## Review Summary

### Changes Made
All fixes applied across all cycles: issue ID → severity → what changed. Must be under 12 lines and every line must be under 250 chars.

### Remaining Open Issues
- Unresolvable oscillations with explanation (max 2 lines and max 250 chars / line)
- Unfixed Minor issues (1 line / issue, max 250 chars / line)

### Verdict
"No critical, major or moderate issues remain" OR "The following issues could not be resolved: [list]"

---
**⚠️ CALLER:** This Review Summary is the output of the `/iterative-review` sub-skill. It is NOT a task completion signal. If you invoked this from `/implement` or any other skill, you MUST continue to the next step immediately — do NOT stop here.
