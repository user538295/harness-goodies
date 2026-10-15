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

Keep arguments exact and shell-quote the assignment as one data value; never evaluate invocation text as shell syntax. For example, forward `usage --latest` as both words and preserve quoted values.

## What each host reports

In Claude Code, Cursor, OMP, and OpenCode the harness plugin registers the session-log hooks from its own package. They log nothing until `on` creates the enable flag; `on` and `off` only flip that flag, and `status` only reads it together with the runtime marker the hooks of a running session write.

Claude Code:
- The claude-goodies plugin's `hooks/hooks.json` registers the hooks.
- When Cursor imports the enabled plugin and runs them, only the session-start hook runs the legacy cleanup; none of them logs, nor writes or removes Claude runtime markers.

Cursor:
- The cursor-goodies plugin's `.cursor-plugin/hooks.json` registers the hooks. Cursor loads them only while its "Include Third-Party Plugins, Skills, and Other Configs" setting is on (and its server-side plugin import gate allows it).
- The hooks run with no prompt, through the user's `$SHELL -c`. Each command checks the enable flag first, so while logging is off they exit immediately without starting Python, and they exit silently when the plugin's hook script or `python3` is missing. Legacy `~/.cursor/hooks.json` entries still run the older code until the cleanup removes them.
- The commands are tested with sh, bash, zsh, and fish. Shells with another syntax (for example nushell, xonsh, or PowerShell) fail on every event, which Cursor records only in Cursor Settings → Hooks (no notification). Native Windows is not supported for these hooks (Cursor runs them through PowerShell there), so use Cursor under WSL.
- The first `on` reports `on — logging starts with the next prompt once the cursor-goodies hooks run; run status after it to confirm`.
- When logging is on but no hook of this cursor-goodies version has run, `status` reports `on — no hook of this cursor-goodies version has run` and names the plugin install or update, that setting, and a restart. It counts only hooks of the same package `VERSION` as this skill; failing hook runs are listed in Cursor Settings → Hooks.
- `usage` prints Claude Code's per-request layout and price estimate from the parent agent's token counts recorded by Cursor `stop` hooks while logging is on, then lists each subagent with only its final context size from Cursor's local state (not added to TOTAL). It fails rather than inventing totals when none exist.

OMP:
- The omp-goodies plugin registers the session-log extension, which reads the enable flag at every prompt. It still writes its runtime marker and starts the legacy cleanup when it loads, so `on` reports `on — restart required` until a restarted omp has loaded it.
- A failed background cleanup is reported on omp's stderr (one skipped because another session-log command holds the install lock is not); `repair` names the cause.

OpenCode:
- The opencode-goodies plugin registers the session-log handlers and the `/session-log` command. That command runs this package's CLI directly and shows its output as a session notice.

Codex:
- `on` requires the codex-goodies plugin installed from the `user538295` marketplace. Without it, `status` reports `on — codex-goodies plugin not installed`.
- `on` adds `~/.codex/hooks.json` commands that run the plugin's newest package. Until a Codex session has run them, `on` and `status` report `on — approve the session-log hooks in /hooks, or restart Codex and choose 'Trust all and continue' if it shows the 'Hooks need review' screen`.
- Approving in `/hooks` takes effect in the running session. `codex exec` and other non-interactive runs run only hooks already trusted, so trust them once in the interactive TUI.
- The commands name no package path or version, so plugin updates never change them, and they stay trusted as long as the entries keep their place in `hooks.json`.
- `status` compares the runtime marker with the running package's `VERSION`, so run it through the plugin's skill; a checkout with another `VERSION` keeps reporting the trust instruction.

## `repair` and the legacy cleanup

`repair` removes duplicate and stale session-log hook registrations and the leftovers of older releases for the current host, and reports what it changed:
- Codex: the `hooks.json` entries, restoring the missing ones of a partial set and, while the codex-goodies plugin is installed, replacing the entries older releases registered by package path. The package copy older releases seeded into `~/.codex/skills` goes only when proven unchanged (as in Claude Code). It is kept, without its SKILL.md, while `~/.codex/hooks.json` names its hook or cannot be read, because Codex sessions started earlier keep running that hook: restart Codex before the next `on` or `repair`, which then removes it, since neither can tell whether Codex restarted. Without the plugin `repair` keeps it and says to install the plugin, then run `on`. `status` and `usage` run the same cleanup when they repair an enabled adapter.
- Cursor: every session-log entry older releases put in `~/.cursor/hooks.json`, the package copy they seeded into `~/.cursor/skills` (only when its ownership manifest proves every file unchanged; otherwise `repair` names it; move it aside), and their adapter state files. Each Cursor session start while logging is on runs `repair` when `~/.cursor/hooks.json` still holds such entries, for at most 20 seconds; a timeout, or a cleanup that cannot start, is named on the hook's stderr, which Cursor logs.
- OMP: the `~/.omp/agent/extensions/session-log.js` link, the package copy older releases seeded into `~/.omp/agent/skills/session-log`, and its usage link. The OMP standalone extension copy and the `session-log-omp` files of pre-universal releases go silently; a foreign (unmarked) file or link at that extension path stays, and the rest of the cleanup still runs. Each omp start runs the cleanup in the background once per change of this package (its location or `VERSION`) or of the leftovers present, and stamps it in `~/.omp/agent/session-log/legacy-cleanup.stamp` only after a success: a failed attempt, or one skipped because another session-log command holds the install lock, runs again at the next start, and a kept copy is reported once.
- OpenCode: the adapter links and marked copies, the markdown `/session-log` command (which overrides the plugin's; a foreign one is named instead), and the package copy older releases installed under `~/.config/opencode`. The plugin runs the cleanup in the background once per installed package (its location or `VERSION`) and again when the markdown command, the seeded copy, or the adapter link appears or goes, and stamps it only after a success: a failed attempt, including one skipped because another session-log command holds the install lock, runs again at the next start. It writes its report or failure to OpenCode's log.
- Claude Code: every session-log hook in `settings.json`, the legacy prompt_log scripts the pre-plugin install copied into `~/.claude/scripts` and their hooks, and the package copy (or a skill-root link into the plugin cache) older releases left under `~/.claude/skills`; a copy goes only when its ownership manifest proves every file unchanged, otherwise `repair` names it so you can move it aside. An edited legacy script keeps the scripts and their hooks, and `repair` then fails naming it. Each Claude Code startup runs the cleanup silently, once per change of `settings.json`, the session-log package (its location or `VERSION`), or the presence of that seeded copy or those scripts, and stamps it only after a success: a failed attempt, for example one skipped because another session-log command holds the install lock, runs again at the next startup.

Every package copy the cleanup keeps loses the SKILL.md files its manifest proves unchanged, so no harness loads it as a skill and its older code can no longer be invoked to register its hooks again; only a copy whose SKILL.md itself changed stays loadable. A listed SKILL.md that cannot be removed is reported as `kept the SKILL.md of legacy package copy <path>: <reason>; it may still be loaded as a skill`.

`on` runs the cleanup first; in Claude Code, Cursor, OMP, and OpenCode it still turns logging on when the cleanup fails (naming the failure) or is skipped because another session-log command is running. Any command that needs the install lock while another session-log command holds it fails with `another session-log command is already in progress for <harness>` and exit status 75; in those four hosts `off` never waits for that lock.

On native Windows only the Claude Code plugin's hooks run (under Git Bash); `on`, `off`, `repair`, and the startup cleanup need WSL, and the startup attempt there fails silently, so it runs again at every startup while those leftovers remain.
