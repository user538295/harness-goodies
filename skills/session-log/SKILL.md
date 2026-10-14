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

Keep arguments exact and shell-quote the assignment as one data value; never evaluate invocation text as shell syntax. For example, forward `usage --latest` as both words and preserve quoted values. Cursor usage prints Claude Code's per-request layout and price estimate from the parent agent's token counts recorded by Cursor `stop` hooks while logging is on, then lists each subagent with only its final context size from Cursor's local state (not added to TOTAL); it fails rather than inventing totals when none exist. Codex `on` requires the codex-goodies plugin installed from the `user538295` marketplace and adds `~/.codex/hooks.json` commands that run its newest package; until a Codex session has run them, `on` and `status` report `on — approve the session-log hooks in /hooks, or restart Codex and choose 'Trust all and continue' if it shows the 'Hooks need review' screen` (approving in `/hooks` takes effect in the running session; `codex exec` and other non-interactive runs run only hooks already trusted, so trust them once in the interactive TUI). Their text names no package path or version, so plugin updates never change it, and they stay trusted as long as the entries keep their place in `hooks.json`. `status` compares the runtime marker with the running package's `VERSION`, so run it through the plugin's skill; a checkout with another `VERSION` keeps reporting the instruction. Without the plugin, Codex `status` reports `on — codex-goodies plugin not installed`.

In Claude Code the claude-goodies plugin's `hooks/hooks.json` always registers the session-log hooks, which stay idle until `on` creates the enable flag; `on` and `off` only flip that flag, and `status` only reads it together with the runtime marker the hooks of a running session write.

`repair` removes duplicate and stale session-log hook registrations and the leftovers of older releases for the current host, and reports what it changed: Codex and Cursor `hooks.json` entries (restoring the missing ones of a partial set, and in Codex, while the codex-goodies plugin is installed, replacing the entries older releases registered by package path), the package copy older releases seeded into `~/.codex/skills` (removed only when proven unchanged, as in Claude Code below, and kept while `~/.codex/hooks.json` names its hook or cannot be read, because Codex sessions started earlier keep running that hook; restart Codex before the next `on` or `repair`, which then removes it, since neither can tell whether Codex restarted; without the plugin `repair` keeps it and says to install the plugin, then run `on`), dangling OpenCode and OMP adapter links that point into the claude-goodies plugin cache, and in Claude Code every session-log hook in `settings.json`, the legacy prompt_log scripts the pre-plugin install copied into `~/.claude/scripts` and their hooks, plus the package copy (or a skill-root link into the plugin cache) older releases left under `~/.claude/skills` (a copy only when its ownership manifest proves every file unchanged; otherwise `repair` names it so you can move it aside). An edited legacy script keeps the scripts and their hooks, and `repair` then fails naming it. OpenCode and OMP adapter copies and `session-log-omp` files of pre-universal releases go silently; outside Claude Code `status` and `usage` run the same cleanup when they repair an enabled adapter. `on` runs the cleanup first; in Claude Code it still turns logging on when the cleanup fails (naming the failure) or is skipped because another session-log command is running. Each Claude Code startup runs it silently, once per change of `settings.json`, the session-log package (its location or `VERSION`), or the presence of that seeded copy or those scripts, whether or not the attempt succeeds. On native Windows only the plugin's hooks run (under Git Bash); `on`, `off`, `repair`, and the startup cleanup need WSL; there the startup attempt fails silently once per such change.
