---
name: session-log
description: Manage session prompt logging and usage totals (on / off / status / usage / repair)
---
<!-- universal-session-log: managed -->

This skill supports exactly five host identities: `claude`, `codex`, `cursor`, `opencode`, and `omp`.

Select the identity of the host that loaded this skill from runtime/system context. Never infer it from paths, processes, or environment variables. Set `harness` below to that literal identity. Set `BASE` first to the skill directory announced by the host. Set `arguments` to the exact invocation arguments, or to `status` when none were supplied.

```bash
harness='<claude|codex|cursor|opencode|omp: choose the current host>'
BASE='<the skill directory announced by the current host, or empty>'
arguments='<exact invocation arguments, or status>'
case "$harness" in claude|codex|cursor|opencode|omp) ;; *) printf 'session-log: unsupported host identity: %s\n' "$harness" >&2; false ;; esac
if [ ! -f "$BASE/install.sh" ]; then
  BASE=""; for d in "${SESSION_LOG_HOME:-}" .agents/skills/session-log .claude/skills/session-log .cursor/skills/session-log .opencode/skills/session-log .codex/skills/session-log ~/.agents/skills/session-log ~/.claude/skills/session-log ~/.cursor/skills/session-log ~/.config/opencode/skills/session-log ~/.omp/agent/skills/session-log ~/.codex/skills/session-log "$(ls -d ~/.claude/plugins/cache/*/claude-goodies/*/skills/session-log 2>/dev/null | sort -V | tail -1)" "$(ls -d ~/.omp/plugins/cache/plugins/*___omp-goodies___*/skills/session-log 2>/dev/null | sort -V | tail -1)" "$(ls -d ~/.codex/plugins/cache/*/codex-goodies/*/skills/session-log 2>/dev/null | sort -V | tail -1)" "$(ls -dt ~/.cursor/plugins/cache/*/cursor-goodies/*/skills/session-log 2>/dev/null | head -1)" "$(ls -dt ~/.cache/opencode/npm/*/*/node_modules/opencode-goodies/skills/session-log 2>/dev/null | head -1)"; do [ -n "$d" ] && [ -f "$d/install.sh" ] && { BASE="$d"; break; }; done
fi
if [ -n "$BASE" ]; then
  bash "$BASE/install.sh" --harness "$harness" --arguments "$arguments"
else
  printf 'session-log: complete package not found in any known skills root — set SESSION_LOG_HOME=<skill dir>\n' >&2
  false
fi
```

Keep arguments exact and shell-quote the assignment as one data value; never evaluate invocation text as shell syntax. For example, forward `usage --latest` as both words and preserve quoted values. Cursor usage prints Claude Code's per-request layout and price estimate from the parent agent's token counts recorded by Cursor `stop` hooks while logging is on, then lists each subagent with only its final context size from Cursor's local state (not added to TOTAL); it fails rather than inventing totals when none exist. Codex user hooks require trust review after installation.

In Claude Code the claude-goodies plugin's `hooks/hooks.json` always registers the session-log hooks, which stay idle until `on` creates the enable flag; `on` and `off` only flip that flag, and `status` only reads it together with the runtime marker the hooks of a running session write. In OpenCode the opencode-goodies plugin registers the session-log handlers and the `/session-log` command the same way; that command runs this package's CLI directly and shows its output as a session notice.

`repair` removes duplicate and stale session-log hook registrations and the leftovers of older releases for the current host, and reports what it changed: Codex and Cursor `hooks.json` entries (restoring the missing ones of a partial set), dangling OMP adapter links that point into the claude-goodies plugin cache, in OpenCode the adapter links and marked copies, the markdown `/session-log` command (which overrides the plugin's; a foreign one is named instead), and the package copy older releases installed under `~/.config/opencode`, and in Claude Code every session-log hook in `settings.json`, the legacy prompt_log scripts the pre-plugin install copied into `~/.claude/scripts` and their hooks, plus the package copy (or a skill-root link into the plugin cache) older releases left under `~/.claude/skills` (a copy only when its ownership manifest proves every file unchanged; otherwise `repair` names it so you can move it aside). An edited legacy script keeps the scripts and their hooks, and `repair` then fails naming it. OMP adapter copies and `session-log-omp` files of pre-universal releases go silently; outside Claude Code and OpenCode `status` and `usage` run the same cleanup when they repair an enabled adapter. `on` runs the cleanup first; in Claude Code and OpenCode it still turns logging on when the cleanup fails (naming the failure) or is skipped because another session-log command is running. Each Claude Code startup runs it silently, once per change of `settings.json`, the session-log package (its location or `VERSION`), or the presence of that seeded copy or those scripts, whether or not the attempt succeeds. The OpenCode plugin runs it silently each time it loads while such a leftover exists. On native Windows only the plugin's hooks run (under Git Bash); `on`, `off`, `repair`, and the startup cleanup need WSL; there the startup attempt fails silently once per such change.
