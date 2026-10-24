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

**Loop — repeat until no Critical, Major, Moderate issues remain or you finished the cycle 1:**

1. **Use the `/double-check` skill**.

- First run `/double-check status` and print it to the user. Do not prose it!
- Then run the `/double-check` and orovide what is need to be reviewed and do not stop or ask the user, because this is an automated review and this may be invoked by a sub-agent.

3. Get all of the findings and if there is no Critical, Major, Moderate issues — the review loop is complete. Go to the [Review summary](#Review-Summary) below.

4. **Spawn the most appropriate agent(s) to fix** all issues (Critical, Major, Moderate, Minor). Pass the full consolidated findings. Apply fixes to the actual files. Note which issue ID each fix resolves. **Fix agents must NOT create git commits** — all changes stay as uncommitted working tree modifications.

5. **Re-run the full automated test suite.** In Claude Code use `run_in_background: true` + `Monitor` for suites that take longer (Monitor streams output and keeps the session alive). All tests must pass before the next cycle. If tests fail, spawn the most appropriate agent to resolve them first. You can skip this if you didn't touch the code.

6. **Convergence check**: if the same issues (same root cause) reappear that were already fixed in a prior cycle, mark them as **unresolvable oscillations**, exit from this loop, and report them: Go to the [Review summary](#Review-Summary) below.

7. Go to step 1 with the updated target state.

**After the loop, output:**

## Review Summary

### Changes Made
All fixes applied across all cycles: issue ID → severity → what changed. Must be under 12 lines and every line must be under 250 chars.

### Remaining Open Issues
- Unresolvable oscillations with explanation (max 2 lines; max 250 chars / line)
- Unfixed Minor issues (1 line / issue; max 250 chars / line)

### Verdict
"No critical, major or moderate issues remain" OR "The following issues could not be resolved: [list]"

---
**⚠️ CALLER:** This Review Summary is the output of the `/iterative-review` sub-skill. It is NOT a task completion signal. If you invoked this from `/implement` or any other skill, you MUST continue to the next step immediately — do NOT stop here.
