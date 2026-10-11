---
name: wrap-up
description: "Close out a work session: summarize what was done and why, run the tests and devil's advocate, then audit commit hygiene, acceptance criteria, and tracker status. Never changes anything (fix, commit, tracker) without an explicit yes, and blocks wrap-up on a failing test or devil's-advocate finding. Use when the user says 'wrap up', 'are we done', 'what's left', 'did we commit everything', or 'close out'."
---

# Wrap-Up

You are the person who, at the end of a work session, stops and asks "okay — what did we actually do, why did we do it, and did we finish it properly?" You produce a clear summary and an honest checklist, then hand control back to the user.

## Prime directive — verify freely, never mutate without a yes

Every action in this skill falls into one of two classes. The line between them is the whole point of the skill.

**Non-mutating — run these yourself, automatically, no asking.** Anything that only *observes* and leaves the code, git history, and tracker untouched:
- inspecting git/tracker/session state,
- **running the test suite**,
- **running devil's advocate / `/challenge`** on this session's work.

These are part of the audit. Run them as a matter of course — don't ask permission to verify. (Tests exercising a test DB/fixtures is fine; that is the harness doing its job, not a change to the user's code, commits, or tracker.)

**Mutating — never without an explicit yes to that specific action.** Anything that writes to disk, git, or the tracker:
- editing or fixing code,
- staging/committing,
- updating the tracker,
- any other write.

For a mutating step you **report and ask**, and act only after the user says yes to that exact step — then hand off to the proper skill/tool rather than improvising. "Wrap up" is never an instruction to fix or commit things on its own.

If you are unsure which class something is in, treat it as mutating — ask first.

## Step 1 — Gather the facts (read-only)

Collect, without changing anything:

- **What changed this session.** The files this session edited (session-edited-files tooling if available, else `git status` + `git diff`), and which commits were made *during* this session vs. which changes are still uncommitted.
- **What was pre-existing.** Compare against the working-tree state at session start. Files that were already modified/untracked before this session are **not** this session's work — call them out separately so they never get conflated with the session's diff.
- **The intent / the "why".** Which issue, ticket (e.g. NIM-XX), bug report, or request drove this session. Pull the tracker item and its acceptance criteria if there is one. If there is no ticket, derive intent from the first user prompt(s).
- **Where verification stands.** Note whether tests and a devil's-advocate pass were already run this session — but don't rely on it. You will run both yourself in Step 3 regardless, since they're non-mutating.

## Step 2 — Write the summary

Present a short, factual summary in two parts:

- **What was done** — the concrete changes, grouped logically (not a raw file list). Reference files as clickable links and commits by hash.
- **What it was for** — the issue/intent it served, and whether the change actually satisfies that intent.

Keep it tight. The user was there; this is a confirmation, not a retelling.

## Step 3 — Run verification, then audit the rest

This step has two halves. **Run** the non-mutating verification yourself (items 1–2); **assess** the rest read-only (items 3–5). Mark each ✅ done / ⚠️ partial / ❌ not done / ❓ can't tell, based on evidence, not optimism.

Run these now — no asking:

1. **Tests** — Run the relevant test suite and record the result. If running is genuinely blocked (e.g. Docker/DB not up), say so and mark ❓ — don't fabricate a pass.
2. **Devil's advocate** — Invoke the `da-review` skill (your skill list may show it with a plugin prefix, e.g. `claude-goodies:da-review` or `codex-goodies:da-review`) on this session's work and capture its findings.

Assess these read-only:

3. **Commit hygiene** — Is this session's work committed? Critically: does the commit (or a proposed commit) contain **only this session's changes** and not pre-existing uncommitted files? Is anything from this session still uncommitted?
4. **Done vs. acceptance criteria** — Walk the ticket's acceptance criteria one by one. Is each genuinely met, or just plausibly met? Flag any AC that's unaddressed or only partially covered.
5. **Tracker** — Is the issue's status current (e.g. flipped to in-review/done when its ACs are met)? A finished-but-still-to-do ticket is a gap.

Adapt the **assessment** items (3–5) to the session: a planning/triage session has no commit item; a pure-refactor session may have no ticket. Drop an assessment item only when it genuinely cannot apply, and say why.

The two verifications (1 tests, 2 devil's advocate) are **never** droppable on these grounds. "It was a small change", "I'm confident it's fine", or "there's no ticket" are not reasons to skip them — run both every time. The only acceptable non-run is a hard block (e.g. test harness won't start), which is marked ❓ with the reason, never silently omitted.

## Step 4 — Report

Show the summary (Step 2) and the checklist (Step 3) together, including what the tests and devil's advocate you just ran turned up. Lead with the headline: **done and clean**, **defects found — must fix first**, or **clean, but N close-out steps left**. Be honest — if something is ❓ because you couldn't verify it, say so rather than marking it ✅.

## Step 5 — Branch on what verification found

The question you ask depends entirely on whether verification came back clean.

### Case A — tests failed, or devil's advocate found something that needs to change

These are **blockers**, not close-out chores. The issue is not finished, so do **not** offer to commit, update the tracker, or otherwise "wrap up the issue" — that would be wrapping up around a known defect. Instead, present the failing tests / DA findings and ask the one question that fits: **do you want me to fix these now?**

> Devil's advocate / the tests surfaced these, and they need fixing before this can be wrapped up:
> 1. … 2. …
> Want me to fix them? [ ] fix all · [ ] fix #1 only · [ ] not now

Fixing is a mutating action, so it happens only after the user says yes — then hand off to the right flow (`/tdd`, the `iterative-review` skill's fix loop, a direct fix). After fixes land, verification re-runs and the wrap-up continues. Until then, the commit/tracker steps stay off the table.

### Case B — tests green and devil's advocate clean

Now the only open items are the read-only-assessed ones (commit hygiene, acceptance criteria, tracker). If they're all ✅, say so and stop — done.

Otherwise present the remaining **mutating** close-out steps and ask which, if any, to do — via an interactive prompt (in Nimbalyst, `PromptForUserInput` with a multi-select; otherwise list and ask). It's an offer, never a plan you've started:

> Verification's clean. These close-out steps are still open — want me to do any? [ ] commit this session's work only · [ ] update the tracker to in-review · …

Then:

- For each step the user picks, hand off to the proper flow — **after** the yes, never before. A commit stages **this session's changed lines only**; explicitly exclude files that were already dirty at session start and name the ones you excluded.
- For steps the user doesn't pick, leave them as they are. Don't nudge twice.
- If the user picks nothing / cancels, that's a valid answer. Stop cleanly.

## Notes

- "Are we done?" is the question this skill answers. The honest answers are "yes, here's the proof" or "no, here's exactly what's left" — never a hopeful "should be."
