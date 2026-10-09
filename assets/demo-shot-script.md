# Demo Shot Script — Harness Goodies

**Purpose**: Source of truth for the `demo.tape` author.  
**GIF target duration**: ~48s total (Act 1: ~16s, Act 2: ~14s, Act 3: ~15s; title card: ~3s)

---

## Act 1 — "1 / Refine the idea" (~16s)

**Title card**: `  1 / Refine the idea`

**User prompt:**
```
/feature-refinement Add dark mode toggle to the settings page
```

**Claude response — Phase 1 (investigation):**
```
Investigating the codebase before we start...

Found:
• SettingsPage.tsx renders a flat list — no toggle components yet
• No existing theme or body-class mechanism in the codebase
• localStorage already used for other prefs (language, timezone)
```

**Claude response — Phase 2 (challenge + question):**
```
> **Scope boundary**
> - **Option A: Desktop only** — Pro: ships faster, scoped CSS. Con: inconsistent on mobile.
> - **Option B: Desktop + mobile** — Pro: consistent UX. Con: doubles CSS surface area.
> - **Recommendation**: Option A — ship desktop now, mobile in a follow-up.

Should dark mode persist across sessions?
- **Option A: localStorage** — Pro: matches existing prefs pattern, zero extra infra. Con: lost if user clears storage.
- **Option B: Server-side user profile** — Pro: persists across devices. Con: overkill for a UI-only toggle.
- **Option C: Session only** — Pro: simplest to implement. Con: resets every visit, poor UX.
- **Recommendation**: Option A — localStorage already used for prefs; consistent with existing patterns.
```

**User reply:**
```
Option A for both
```

**Claude response — Phase 3 (brief saved):**
```
Feature Brief saved → Documentation/Backlog/dark-mode-toggle-brief.md

Run /plan-maker Documentation/Backlog/dark-mode-toggle-brief.md to turn this into an implementation plan.
```

---

## Act 2 — "2 / Build the plan" (~14s)

**Title card**: `  2 / Build the plan`

**User prompt:**
```
/plan-maker Documentation/Backlog/dark-mode-toggle-brief.md
```

**Claude response (abbreviated plan scrolling in):**
```
# FEAT-042 — Dark Mode Toggle
**Purpose**: Persistent dark/light toggle on the desktop Settings page
**Audience**: Desktop users
**Status**: To Do

## Background
...

---

## Goal
...

## Scope

### In Scope
...

### Out of Scope
...

---

## Acceptance criteria
...

## What does NOT change
...

## Known limitations / accepted trade-offs
...

## Architecture
...

---

## Task breakdown

### Phase 1 — Core toggle
> **Releasable**: after Task 1.2 — toggle visible and functional on Settings page

#### Task 1.1 — darkMode state in SettingsStore
- [ ] **File**: `src/settings/SettingsStore.ts`
- **Depends on**: nothing
- **Description**: add `darkMode: boolean` field; expose `toggleDarkMode(): void`;
  persist to localStorage key `prefs.darkMode`; read on init
- **Releasable**: darkMode state readable and toggleable by any component
- **Tests (TDD)** — `tests/settings/SettingsStore.test.ts`:
  - Unit: `test_toggle_flips_state` — toggleDarkMode() flips darkMode true→false→true
  - Unit: `test_persists_to_localstorage` — value written to prefs.darkMode key after toggle
  - Checkpoint: `vitest run tests/settings/SettingsStore.test.ts`

#### Task 1.2 — Toggle component on SettingsPage
- [ ] **File**: `src/settings/SettingsPage.tsx`
- **Depends on**: Task 1.1
  ...

#### Task 1.3 — Apply body CSS class
- [ ] **File**: `src/settings/theme.ts`
- **Depends on**: Task 1.1
  ...

### Final Phase — Verification & Documentation
#### Task 2.1 — Final verification & documentation update
- [ ] **File**: N/A (agent task)
  ...

Plan saved → plans/FEAT-042-dark-mode.md
```

---

## Act 3 — "3 / Ship the task" (~15s)

**Title card**: `  3 / Ship the task`

**User prompt:**
```
/implement next plans/FEAT-042-dark-mode.md
```

**Claude response (implementation sequence):**
```
Progress   : [░░░░░░░░░░░░] 0%  (0/4 tasks, 0/2 phases)
Next task  : 1.1  darkMode state in SettingsStore

Writing tests first...
→ tests/settings/SettingsStore.test.ts  ✓ created

Running tests (red)...
FAIL  2 tests failed — expected (TDD red phase)

Implementing...
→ src/settings/SettingsStore.ts  ✓ updated

Running tests (green)...
PASS  2 passed in 0.3s

Running full test suite...
PASS  47 passed in 1.2s

3 files changed, 32 insertions(+), 1 deletion(-)
[main a3f7c12] feat(dark-mode): add darkMode state to SettingsStore

Checked off: Task 1.1
Next: /implement next plans/FEAT-042-dark-mode.md
```
