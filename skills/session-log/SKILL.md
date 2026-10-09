---
name: session-log
description: Manage session prompt logging and usage totals (on / off / status / usage)
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
  BASE=""; for d in "${SESSION_LOG_HOME:-}" .agents/skills/session-log .claude/skills/session-log .cursor/skills/session-log .opencode/skills/session-log .codex/skills/session-log ~/.agents/skills/session-log ~/.claude/skills/session-log ~/.cursor/skills/session-log ~/.config/opencode/skills/session-log ~/.omp/agent/skills/session-log ~/.codex/skills/session-log "$(ls -d ~/.claude/plugins/cache/*/claude-goodies/*/skills/session-log 2>/dev/null | sort -V | tail -1)" "$(ls -d ~/.omp/plugins/cache/plugins/*___omp-goodies___*/skills/session-log 2>/dev/null | sort -V | tail -1)"; do [ -n "$d" ] && [ -f "$d/install.sh" ] && { BASE="$d"; break; }; done
fi
if [ -n "$BASE" ]; then
  bash "$BASE/install.sh" --harness "$harness" --arguments "$arguments"
else
  printf 'session-log: complete package not found in any known skills root — set SESSION_LOG_HOME=<skill dir>\n' >&2
  false
fi
```

Keep arguments exact and shell-quote the assignment as one data value; never evaluate invocation text as shell syntax. For example, forward `usage --latest` as both words and preserve quoted values. Cursor usage prints Claude Code's per-request layout and price estimate from the parent agent's token counts recorded by Cursor `stop` hooks while logging is on, then lists each subagent with only its final context size from Cursor's local state (not added to TOTAL); it fails rather than inventing totals when none exist. Codex user hooks require trust review after installation.
