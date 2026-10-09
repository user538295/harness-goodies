# demo-setup.zsh — scripted command handlers for the Harness Goodies hero GIF.
#
# Sourced by assets/demo.tape (VHS). It fakes three slash-commands so the
# recording shows realistic output without running anything real. Each command
# is a zsh function whose name contains a slash; zsh calls it directly (a
# slash-named function is looked up before path resolution when it is defined).
#
# Pacing lives here: the `sleep` calls between chunks drive the on-screen reveal
# (investigation → options → reply → saved). Tune those to change the timing.
# The final hold on each act is the `Sleep` after the command in demo.tape.

setopt interactivecomments 2>/dev/null
PROMPT='› '
RPROMPT=''

# Print a block passed as one argument verbatim (no glob/escape surprises).
_emit() { print -r -- "$1" }

# ---------------------------------------------------------------------------
# Act 1 — /feature-refinement
# ---------------------------------------------------------------------------
'/feature-refinement'() {
  _emit ""
  _emit "Investigating the codebase before we start..."
  sleep 1.0
  _emit ""
  _emit "Found:"
  _emit "  SettingsPage.tsx renders a flat list — no toggle components yet"
  _emit "  No existing theme or body-class mechanism in the codebase"
  _emit "  localStorage already used for other prefs (language, timezone)"
  sleep 1.2
  _emit ""
  _emit "> Scope boundary"
  _emit ">   Option A: Desktop only        Pro: ships faster, scoped CSS."
  _emit ">                                 Con: inconsistent on mobile."
  _emit ">   Option B: Desktop + mobile    Pro: consistent UX."
  _emit ">                                 Con: doubles CSS surface area."
  _emit ">   Recommendation: A — ship desktop now, mobile in a follow-up."
  sleep 1.5
  _emit ""
  _emit "> Persistence"
  _emit ">   Option A: localStorage        Pro: matches existing prefs pattern."
  _emit ">                                 Con: lost if user clears storage."
  _emit ">   Option B: Server profile      Pro: persists across devices."
  _emit ">                                 Con: overkill for a UI-only toggle."
  _emit ">   Option C: Session only        Pro: simplest to implement."
  _emit ">                                 Con: resets every visit."
  _emit ">   Recommendation: A — consistent with existing prefs."
  sleep 1.8
  _emit ""
  _emit "› Option A for both"
  sleep 0.8
  _emit ""
  _emit "Got it — desktop only, localStorage."
  sleep 0.6
  _emit ""
  _emit "Feature Brief saved → Documentation/Backlog/dark-mode-toggle-brief.md"
  _emit "Run /plan-maker to build the implementation plan."
  return 0
}

# ---------------------------------------------------------------------------
# Act 2 — /plan-maker
# ---------------------------------------------------------------------------
'/plan-maker'() {
  _emit ""
  _emit "# FEAT-042 — Dark Mode Toggle"
  _emit "**Purpose**: Persistent dark/light toggle on the Settings page"
  _emit "**Audience**: Desktop users"
  _emit "**Status**: To Do"
  sleep 0.8
  _emit ""
  _emit "## Background"
  _emit "Users have requested a dark theme; localStorage prefs established."
  _emit ""
  _emit "## Goal"
  _emit "Toggle persists across sessions; applies to all desktop views."
  sleep 0.8
  _emit ""
  _emit "## Scope"
  _emit "**In Scope**: Settings toggle, body class, localStorage persistence"
  _emit "**Out of Scope**: mobile breakpoints, per-component theming"
  _emit ""
  _emit "## Acceptance criteria"
  _emit "> Verified in the final task."
  _emit ""
  _emit "## What does NOT change"
  _emit "- Existing color tokens, route structure, settings layout"
  sleep 1.1
  _emit ""
  _emit "## Known limitations / accepted trade-offs"
  _emit "- No transition animation on first load (accepted)"
  _emit ""
  _emit "## Architecture"
  _emit "- SettingsStore.darkMode: boolean + toggleDarkMode()"
  _emit "- theme.ts applies dark class on <body>; reads on mount"
  _emit "- No new env vars"
  sleep 1.1
  _emit ""
  _emit "## Task breakdown"
  _emit ""
  _emit "### Phase 1 — Core toggle"
  _emit "> **Releasable**: after Task 1.2 — toggle visible and functional"
  _emit ""
  _emit "#### Task 1.1 — darkMode state in SettingsStore"
  _emit "- [ ] **File**: src/settings/SettingsStore.ts"
  _emit "- **Depends on**: nothing"
  _emit "- **Description**:"
  _emit "    - Add darkMode: boolean field"
  _emit "    - Expose toggleDarkMode(): void"
  _emit "    - Persist to localStorage key prefs.darkMode; read on init"
  _emit "- **Releasable**: state readable/toggleable by any component"
  _emit "- **Tests (TDD)** — tests/settings/SettingsStore.test.ts:"
  _emit "    - Unit: test_toggle_flips_state"
  _emit "    - Unit: test_persists_to_localstorage"
  _emit "- **Checkpoint**: vitest run tests/settings/SettingsStore.test.ts"
  sleep 1.4
  _emit ""
  _emit "### Final Phase — Verification & Documentation"
  _emit "#### Task 2.1 — verify acceptance criteria + update docs"
  sleep 0.6
  _emit ""
  _emit "Plan saved → plans/FEAT-042-dark-mode.md"
  return 0
}

# ---------------------------------------------------------------------------
# Act 3 — /implement next
# ---------------------------------------------------------------------------
'/implement'() {
  _emit ""
  _emit "Progress   : [░░░░░░░░░░░░] 0%  (0/4 tasks, 0/2 phases)"
  _emit "Next task  : 1.1  darkMode state in SettingsStore"
  sleep 1.0
  _emit ""
  _emit "Writing tests first..."
  _emit "→ tests/settings/SettingsStore.test.ts  ✓ created"
  sleep 0.8
  _emit ""
  _emit "Running tests (red)..."
  _emit "FAIL  2 tests failed — expected (TDD red phase)"
  sleep 1.0
  _emit ""
  _emit "Implementing..."
  _emit "→ src/settings/SettingsStore.ts  ✓ updated"
  sleep 0.8
  _emit ""
  _emit "Running tests (green)..."
  _emit "PASS  2 passed in 0.3s"
  sleep 0.8
  _emit ""
  _emit "Running full test suite..."
  _emit "PASS  47 passed in 1.2s"
  sleep 0.8
  _emit ""
  _emit "3 files changed, 32 insertions(+), 1 deletion(-)"
  _emit "[main a3f7c12] feat(dark-mode): add darkMode state to SettingsStore"
  sleep 0.7
  _emit ""
  _emit "Checked off: Task 1.1"
  _emit "Next: /implement next plans/FEAT-042-dark-mode.md"
  return 0
}
