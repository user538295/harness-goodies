---
name: session-log
description: Manage session prompt logging and usage totals (on / off / status / usage)
---

This skill runs the bundled script `session_log.py`, which turns prompt logging on or off, reports its status, and prints the token and price report of a session. It needs Python 3.14 or newer and recognizes exactly five host identities: `claude`, `codex`, `cursor`, `opencode` (OpenCode V2), and `omp`.

## Run it

1. Select the identity of the host that loaded this skill from runtime/system context. Never infer it from paths, processes, or environment variables. It replaces `<harness>` below. If the host is not one of the five, or you cannot tell, stop and tell the user.
2. Take the skill directory the host announced when it loaded this skill (Claude Code and OpenCode print `Base directory for this skill: <path>`, omp prints `[Skill directory: <path>]`; in Cursor and Codex it is the directory of this SKILL.md; a `file://` URL stands for the filesystem path it names). It replaces `<skill dir>` below.
3. If the host announced no directory: on macOS and Linux run the locator below in bash, which only prints the directory, and use that path; on native Windows the locator cannot run (it is bash), so ask the user to set `SESSION_LOG_HOME` to the skill directory and to tell you its value, and use that value.
4. Set `<arguments>` to the invocation arguments, or to `status` when none were supplied. Accept only `on`, `off`, `status`, `usage`, `usage --latest`, or `usage <session-id>` (a session id holds only letters, digits, `_` and `-`); for anything else stop and tell the user, because the line below is parsed by a shell.
5. Run this one line, keeping the double quotes around the directory:

```
python3 "<skill dir>/session_log.py" <harness> <arguments>
```

On Windows only: if `python3` cannot be started (it is missing, or only the Microsoft Store stand-in answers, and no `session-log:` line appears on stderr), run the same line with `py -3` in place of `python3`, and if that cannot be started either, with `python`; if none of the three can be started, tell the user that Python 3.14 or newer is required (python.org/downloads) and stop. Once the script has run, a `session-log:` message on stderr is its own answer: report it as it is and try no other interpreter.

## Locator

Run it in bash only when the host announced no skill directory (macOS and Linux). It tries `SESSION_LOG_HOME` first, then project-level roots, then the user-level roots of every supported host, then the plugin-cache copy (the newest version, or the most recently installed copy for Cursor and OpenCode), and takes the first directory that holds `session_log.py`. A project-level hit prints a path relative to the current directory:

```bash
BASE=""; for d in "${SESSION_LOG_HOME:-}" .agents/skills/session-log .claude/skills/session-log .cursor/skills/session-log .opencode/skills/session-log .codex/skills/session-log ~/.agents/skills/session-log ~/.claude/skills/session-log ~/.cursor/skills/session-log ~/.config/opencode/skills/session-log ~/.omp/agent/skills/session-log ~/.codex/skills/session-log "$(ls -d ~/.claude/plugins/cache/*/claude-goodies/*/skills/session-log 2>/dev/null | sort -V | tail -1)" "$(ls -d ~/.omp/plugins/cache/plugins/*___omp-goodies___*/skills/session-log 2>/dev/null | sort -V | tail -1)" "$(ls -d ~/.codex/plugins/cache/*/codex-goodies/*/skills/session-log 2>/dev/null | sort -V | tail -1)" "$(ls -dt ~/.cursor/plugins/cache/*/cursor-goodies/*/skills/session-log 2>/dev/null | head -1)" "$(ls -dt ~/.cache/opencode/npm/*/*/node_modules/opencode-goodies/skills/session-log 2>/dev/null | head -1)"; do [ -n "$d" ] && [ -f "$d/session_log.py" ] && { BASE="$d"; break; }; done; [ -n "$BASE" ] && echo "$BASE" || { echo "ERROR: session-log not found in any known skills root — set SESSION_LOG_HOME=<skill dir>" >&2; false; }
```

On success it prints the directory; on failure it prints the not-found error to stderr and exits non-zero. Report that error and its `SESSION_LOG_HOME` hint to the user.

## Results

Print the script's stdout to the user as it is. A nonzero exit status is a failure: report the `session-log:` message on stderr and do not say logging is on.
