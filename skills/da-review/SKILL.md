---
name: da-review
description: >-
  Single-pass devil's advocate review. Finds flaws without auto-fixing. Use when the user invokes /da-review or asks for a devil's advocate review, or when another skill needs a read-only adversarial review. Use the iterative-review skill to also apply fixes.
allowed-tools: Read, Grep, Glob
---

Review the following target: $ARGUMENTS (if empty — including when another skill invoked this one and named the target in its request instead — review that named target, else the current plan/code/work in context).
Use the `devils-advocate` agent for the review (agent type `devils-advocate`, or `claude-goodies:devils-advocate` if that is the name shown in your agent list; minimum 3).
**Fallback — decide by capability, not harness brand.** If no `devils-advocate` agent type is available (e.g. Codex, whose plugins ship no agents) but a subagent tool exists, spawn generic subagents and instruct each to first read and adopt the persona in `$BASE/../../agents/devils-advocate.md`, where `$BASE` is this skill's directory as announced by the harness (`Base directory for this skill: <path>` or `[Skill directory: <path>]`; in Cursor and Codex, the directory of this SKILL.md). If that file is missing, proceed with this rubric only and tell the user. If no subagent tool exists at all, run the review passes inline and tell the user the review is not independent.
Your goal is to find every flaw — do not soften findings. Don't assume, fact check everything including your findings that are correct.

Use the following severity rubrics:
- **Critical**: blocks correctness, security, or safety
- **Major**: significant design flaw, missing requirement, or likely bug
- **Moderate**: suboptimal but workable
- **Minor**: style, naming, or nitpick

For each issue found, provide:
- Severity label
- filename:line_number (omit when no file location applies — e.g. a plan-level, conceptual, or cross-cutting finding; never invent one)
- Short description of the problem and why it matters / what could go wrong. **Must be under 250 chars**

Group findings by severity (Critical first). At the end, give a one-sentence overall verdict.

Do not propose fixes — only identify problems.
