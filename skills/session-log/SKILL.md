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

In Claude Code the claude-goodies plugin's `hooks/hooks.json` always registers the session-log hooks, which stay idle until `on` creates the enable flag; `on` and `off` only flip that flag, and `status` only reads it together with the runtime marker the hooks of a running session write.

`repair` removes duplicate and stale session-log hook registrations and the leftovers of older releases for the current host, and reports what it changed: Codex and Cursor `hooks.json` entries (restoring the missing ones of a partial set), dangling OpenCode and OMP adapter links that point into the claude-goodies plugin cache, and in Claude Code every session-log hook in `settings.json` plus the package copy older releases seeded under `~/.claude/skills` (only when its ownership manifest proves every file unchanged; otherwise `repair` names it so you can move it aside). `on` runs the same cleanup first; Claude Code session start runs it silently, and only when `settings.json`, the session-log package (its location or `VERSION`), or the presence of that seeded copy changed since its last successful run. On native Windows only the plugin's hooks run (under Git Bash); `on`, `off`, `repair`, and the session-start cleanup need WSL, and while leftovers remain each native session start retries that cleanup silently because only a successful one is recorded.
