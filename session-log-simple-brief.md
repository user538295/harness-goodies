# Feature Brief: Simple Session-log

Supersedes the earlier `session-log-python-core-*` brief, plan, tasks and 14 `.tsp` contracts; those files were removed.

## Problem
Today's session-log records every prompt live through per-prompt hooks in five harnesses (12,238 product lines and 10,511 test lines in `skills/session-log`). Almost every bug and every planned fix comes from those hooks: shell differences, CRLF on Windows, POSIX-only calls (`ps`, `fcntl`, `dir_fd`), enable flags checked under locks, runtime markers, process identity checks, Codex hook trust, and the cleanup of what older releases registered. Native Windows does not work, and the plan to fix it grew to about 600 KB of documents.

## Goal
`/session-log` gives the user two things in Claude Code, Codex, Cursor, OMP and OpenCode, on macOS, Linux and native Windows:
1. **Logs**: a readable Markdown file per session with every user prompt and the agent's final reply for that prompt, with times.
2. **Usage**: a per-session report of token counts, price, model, effort and working time per prompt and per sub-agent, and a **whole-session total that includes every sub-agent's tokens**. The user sees how long the session took and how many tokens it used in all, and the model and tokens give the price.

The user needs only Python 3.14 or newer. No bash, jq, sqlite3 CLI, Git Bash or Node is needed.

## Big picture: what, how, why

**The key fact:** four of the five harnesses already save the whole conversation, with times and token counts, on the user's disk (checked on the maintainer's Mac, 2026-10-18). So session-log does not need to record anything live in those four. It reads what the harness saved and turns it into Markdown and a usage report. Cursor is the exception: it saves the text but not the times or the tokens, so Cursor still needs hooks.

| Harness | What the harness saves | Prompts + replies | Times | Tokens | Deletes old sessions by itself? | How session-log gets the data |
|---|---|---|---|---|---|---|
| Claude Code | `~/.claude/projects/<key>/<session>.jsonl`, sub-agents in `<session>/subagents/` | yes | yes | yes (`message.usage`, `message.model`) | **yes, after 30 days by default** (`cleanupPeriodDays`) | reads the file |
| Codex | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`, and `*.jsonl.zst` once compressed | yes | yes | yes (`token_count` events) | no; files older than 7 days may be compressed | reads the file |
| OMP | `~/.omp/agent/sessions/<project>/*.jsonl`, and `~/.omp/agent/archive/sessions/**/*.jsonl.gz` after `omp gc --apply` | yes | yes | yes, with cost per message (`message.usage.cost`) | no; only the manual `omp gc --apply` moves files | reads the file |
| OpenCode V2 | `~/.local/share/opencode/opencode.db`: `session_v2` (sessions, `parent_id` for sub-agents) and `session_message` (messages as JSON, read in `seq` order). `session`, `message` and `part` are leftover v1 tables (upstream v2.0.30 `packages/core/src/session/sql.ts:22-23,79-80`). | yes | yes | yes, with cost (`data.tokens`, `data.cost` on assistant rows) | not checked; not needed while it keeps sessions in its database | reads the database (Python `sqlite3`) |
| Cursor | `~/.cursor/projects/<project>/agent-transcripts/<id>/<id>.jsonl` | text only | no | no (state DB `tokenCount` is 0 in 22,453 of 22,456 records; `usageData` is `{}`) | — | its own hooks record times and tokens |

**Why session-log must still copy, not only read:** Claude Code deletes transcripts after 30 days by default (`code.claude.com/docs/en/data-usage.md`, "Local caching"). So session-log copies each session into its own archive: the Markdown log plus a saved usage report, which both outlive the harness's cleanup.

**How it works:**
- One Python script, standard library only. It has one small reader per harness and one shared writer. Each reader turns the harness's data into the same simple list: session, prompts, replies, times, model, effort and tokens per request, and sub-agents. The writer produces the Markdown log and the usage report from that list.
- **Sync** means: for every session with activity since logging was turned on, re-read it from the harness and rewrite its log and usage report. A rewrite replaces the whole file at once (write a temp file, then rename it over the old one), so there is no appending, no locking and no half-written file. Running sync twice gives the same logs and reports. Sync remembers a cheap fingerprint of each source, so it re-reads only sessions that changed since the last sync, and it remembers where each session's log is, so it never searches the log folder and never touches an old-format log. After an update that changes log or report text, or a price change, it rewrites each archived session once; it never rewrites a session from less source than the archive already holds (team plan C2, C3, C6).
- **When sync runs:** while logging is on, at the start of every `/session-log` command. It also runs at session start in Claude Code, so sessions are copied before Claude's 30-day cleanup, and in Cursor, whose hooks run anyway.
- **Claude session-start hook**: exec form (`"command": "python3"`, `"args"`: a one-line `-c` guard, then `"${CLAUDE_PLUGIN_ROOT}/…"` and the hook arguments). The guard runs the script only when it exists, because a bare `python3 <missing script>` exits 2, which blocks when Cursor runs the imported hook (F1). Claude Code starts it with no shell on every OS (`code.claude.com/docs/en/hooks.md`, "Exec form and shell form"). Claude hooks have no per-OS command field (hooks docs, "Command hook fields"; the installed 2.1.292 binary lists none), so one command must serve all three OSes. `python3` is the only name that exists on stock macOS and Linux. The hook sets `"async": true`, so Claude Code runs it in the background and the session does not wait for the sync (hooks.md, "Run hooks in the background"); team plan S23 checks this in a real Claude Code.
- **Cursor hooks** (`sessionStart`, `beforeSubmitPrompt`, `afterAgentResponse`, `stop`, `subagentStop`): each runs `"${CURSOR_PLUGIN_ROOT}/skills/session-log/hook" cursor <event>`. `sessionStart` runs sync; the other four only write one small file per hook call with the hook input plus the time it arrived, reading only `conversation_id` for the folder name. All other parsing happens later, in sync. The `stop` input carries `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens` and `model`, for the parent conversation only. The `subagentStop` input carries the sub-agent's `duration_ms` and type (`cursor.com/docs/hooks`, "subagentStop").
- **Codex, OMP, OpenCode**: no hooks and no plugin code for session-log. The skill runs the script.

**Why this is simpler and more stable:**
- No live recording in four harnesses, so the shell, CRLF, `ps`/`fcntl`, lock, runtime-marker and process-identity problems disappear, because that code no longer exists.
- Codex needs no hook, so there is no hook trust step.
- The OMP extension and the OpenCode logging handlers go away, so there is no embedded Python and no per-OS JavaScript.
- A whole-file rewrite from the harness's own data repairs itself: a missed run is fixed by the next sync.
- Each harness is one small reader, tested alone with one fixture transcript. A harness format change breaks only its reader.

## Verified findings (2026-10-18)
Five agents checked the open questions of the first draft against upstream source, official docs, the installed apps and local data. Each finding below is reflected in the sections that follow.

**F1. Cursor hooks on native Windows: solvable with two small launchers.**
- *How Cursor runs hooks.* On Windows, Cursor runs every hook through PowerShell: `pwsh` if it is on PATH, otherwise Windows PowerShell 5.1, with `-NoProfile -NonInteractive -ExecutionPolicy Bypass -c`. It wraps the command so that the hook input comes in through `$input`. On macOS and Linux it runs `$SHELL -c` (Cursor 3.23.12 `extensionHostProcess.js:479,728,730`).
- *Plugin root and working folder.* Cursor itself replaces the braced `${CURSOR_PLUGIN_ROOT}` with the install path before any shell sees the command. Plugin hooks run in the plugin folder, except `stop` and `subagentStop`, which run in the first workspace folder (`workbench.desktop.main.js:18761`). So relative paths break, and the braced placeholder works on every OS.
- *Two dangers to avoid.* Today's `[ ! -f … ] || …` command fails in PowerShell with a parse error. A bare `python3 "<script>"` exits with code 2 when the script is missing, and in Cursor exit code 2 blocks the prompt (`cursor.com/docs/hooks`, "Exit code behavior").
- *The fix, tested.* Ship an extensionless `hook` (POSIX sh, file mode 755) and a `hook.ps1` side by side. On Windows, Cursor adds `.ps1` to an extensionless command when that file exists (`extensionHostProcess.js:728`). Both launchers exit 0 silently when Python or the script is missing. In a harness built from Cursor's own runner code, both worked in sh, zsh, fish and pwsh 7, including install paths with spaces and an apostrophe, and with Python or the script missing.
- *Not tested.* A real Windows machine, and Windows PowerShell 5.1. In 5.1, Cursor's wrapper reads the hook input with the ANSI code page, so non-ASCII prompt text arriving through the hook would be garbled (Microsoft `about_Character_Encoding`, 5.1). An install path containing `$`, `` ` ``, `{`, `}`, `(`, `)`, `|`, `;`, `,`, `<` or `>` also defeats the `.ps1` lookup.

**F2. Python command name on Windows: no single name works everywhere.**
- *Where `python3` exists.* Only the Python install manager (pymanager), the old Microsoft Store package and Scoop put a real `python3.exe` on PATH.
- *Where it doesn't.* The python.org full installer, winget, Chocolatey, conda and venv provide `python` and/or `py`, but no `python3`. On those machines `python3` is the Microsoft Store stand-in, which fails or opens the Store.
- *Sources:* cpython v3.13.0 `Tools/msi/exe/exe_files.wxs:19,27`; cpython v3.14.0 `Doc/using/windows.rst:132-135`, which itself calls `python3` "not meant to be widely used or recommended"; pymanager `msi.wxs:38-41`; winget and Scoop manifests.
- *Consequence.* The Claude hook keeps `python3`, and Windows users need an install that provides it. The Cursor launcher `hook.ps1` tries `py -3`, then `python3`, then `python`. The skill command tries `python3`, then `py -3`, then `python` (team plan Q27).

**F3. Codex: nothing is deleted, but files get compressed.**
- *No deletion.* Codex 0.162.1 deletes a rollout only when the user deletes the thread (`openai/codex` `rust-v0.162.1` `thread-store/src/local/delete_thread.rs:259-301`).
- *Compression.* Rollouts older than 7 days can be compressed in place to `*.jsonl.zst` (`rollout/src/compression.rs:364,960`). On this Mac, 601 of 681 rollouts are already `.zst`. Python reads zstd with `compression.zstd` (3.14 and newer, docs.python.org).
- *Typed prompt.* The prompt the user typed is the `event_msg` → `item_completed` → `item.type == "UserMessage"` text parts in new-format files, or `event_msg` `user_message.message` in old-format files (`rollout/src/policy.rs:141-155`, `protocol/src/items.rs:81-87`). `response_item` role=user parts also carry injected context (`<environment_context>`, `# AGENTS.md` and more), and 38 of 104 typed prompts appear in no `response_item`, so never read prompts from there.
- *Sub-agents.* Sub-agent threads carry `session_meta.parent_thread_id`.

**F4. OMP deletes nothing by itself.** The oldest local session (2026-09-16) is the install date. Only the manual `omp gc --apply` moves sessions, gzipped, into `~/.omp/agent/archive/sessions/**/*.jsonl.gz`; its default `gc.archive` is `true` (`can1357/oh-my-pi` v18.11.1 `cli/gc-settings.ts`, `gc-cli.ts:335-337`). Python's `gzip` reads those.

**F5. OpenCode: the whole plugin is disabled today.**
- *Why.* OpenCode ≥ 2.0.4 accepts plugin skills only with `path`; `location` was removed (`anomalyco/opencode` v2.0.30 `packages/schema/src/skill.ts:27-34`). Our `.opencode-plugin/index.js:34` sends `location`. Decoding it fails, and OpenCode then disables the whole plugin: every skill, the `devils-advocate` agent and session-log (`packages/core/src/plugin/host.ts:472`, `state.ts:178-184`, `plugin.ts:50-53`).
- *Proof.* In an isolated `opencode serve` (v2.0.30, temp HOME/XDG), the unchanged plugin shows `"status":"failed"`. With `path`, it is `"active"` and loads all 20 repo skills plus the agent.

**F6. The price table is wrong, and Codex has no price.**
- *Where prices live.* The only table is `adapters/claude/scripts/prompt_log_prices.json:2-14`, shared by the Claude and Cursor reports, with cache multipliers that are the same for every model.
- *What's wrong.* Compared with `platform.claude.com/docs/en/about-claude/pricing`, it overstates Opus 5.5 by 73%, Sonnet 5.5 by 71%, Sonnet 5 by 50% and Fable 5.1 by 45% on local data. Those models have $4/$20 or $2/$10 rates and model-specific cache-read rates.
- *Unknown models.* They are priced $0 with no warning (`prompt_log_usage.jq:32,136`). For example, Cursor's `composer-2.5` is $0 today, but its listed price is $0.5/$2.5 (`cursor.com/docs/models-and-pricing`).
- *Codex.* It prints no price and ignores `cache_write_input_tokens` (`codex/session_log_usage.py:159`).
- *Recorded costs.* OMP records cost per message, and its rates match the official pages. OpenCode records cost; it is 0 locally only because every local session used a local model. Claude writes an undocumented session-level `cost-state` record; Codex and Cursor record no cost.

**F7. Cursor has no sub-agent tokens.** The `subagentStop` input has no token fields, and `stop` does not fire for child conversations. The input definitions are `SubagentStopRequestQuery` at `workbench.desktop.main.js:7266` and `StopRequestQuery` at `:8750`. Whether the parent's `stop` totals include sub-agent usage is unverified.

**F8. Sub-agent usage: four harnesses save it, Cursor saves only time.** Today's reports, and where the new readers take the same data from:

| Harness | Sub-agent tokens | Sub-agent time | Today's report | Source for the new reader |
|---|---|---|---|---|
| Claude Code | yes | yes | rows + in TOTAL (`prompt_log_usage.sh:102-120`) | `<session>/subagents/**/agent-*.jsonl` (recursive, for workflow sub-agents), type from `agent-*.meta.json` `agentType` |
| Codex | yes | yes | **dropped**: sub-agent rollouts are filtered out, and only totals are printed (`codex/session_log_usage.py:60,198`) | rollouts whose `session_meta` has `parent_thread_id`, attached to the parent (F3) |
| OMP | yes, with cost | yes | rows + in TOTAL (`omp/session_log_usage.ts:619-627`) | child session files of the root session |
| OpenCode | yes, with cost | yes | rows + in TOTAL (`opencode/session_log_usage.sh:246,418-428`) | `session_v2` rows with `parent_id`, walked recursively |
| Cursor | **no** (F7) | yes (`subagentStop` `duration_ms`) | final context size only, not in TOTAL (`cursor/session_log_usage.py:41-48`) | `subagentStop` hook records |

So the new design keeps sub-agent tokens and time wherever they exist today and adds them for Codex. Cursor stays as limited as today.

**F9. Cursor's parent token counts do not include sub-agent tokens (tested 2026-10-18).** Setup: Cursor `agent` CLI 2026.10.14, model `composer-2.5`, a 100 KB `data.txt`, and the same whole-file task three ways. The usage figures are from `--output-format json` `usage`, in tokens (input + cache read + output).

| Run | Who read the file | Parent usage |
|---|---|---|
| B (trivial lookup, through 1 sub-agent) | sub-agent, 3 reads | 12,565 + 19,758 + 534 ≈ **32.9k** |
| B2 (whole-file task, through 1 sub-agent; it ran 136 s) | sub-agent, 4 reads | 12,717 + 19,820 + 609 ≈ **33.1k** |
| C2 (same whole-file task, no sub-agent) | parent, 3 reads + 1 shell | 49,371 + 68,542 + 4,836 ≈ **122.7k** |

- *Result:* reading the file costs about 90k tokens when the parent does it (C2), but adds nothing to the parent's count when a sub-agent does it (B2 ≈ B). So the sub-agent's tokens are not in the parent's usage.
- *Where the sub-agent's work lives:* Cursor saves it as its own transcript (with tool calls, but no tokens). Its `store.db` has no readable token fields.
- *Limit of the test:* it measured the CLI's result `usage`, not the IDE's `stop` hook. In this test the CLI ran no hooks at all, neither a project `.cursor/hooks.json` nor the plugin's (with `--trust` and logging on). Both figures come from the server's per-turn usage [INFERENCE: the agent's reading of `triggerStopHook`, F7].
- *Consequence:* a Cursor TOTAL can't include sub-agent tokens. It must be labelled as excluding them.

## Users & Context
- Developers using any of the five harnesses on macOS, Linux or native Windows who want a readable history of their prompts and replies and to know what a session cost.
- The maintainer, who must be able to test the whole feature on a Mac, plus Linux in Docker, plus one manual Windows checklist.

## Core Flow
1. The user installs or updates the harness plugin. Nothing logs yet.
2. The user runs `/session-log on`. Session-log records the time, syncs, on Windows checks that Python can be started the way the hooks start it, and prints where the logs go.
3. The user works normally. Claude Code and Cursor sync at each session start. While logging is on, every `/session-log` command syncs too.
4. `/session-log status` prints on or off, the log folder, the last sync time, and how many sessions were archived. It also lists any sessions it could not read and why, for example corrupt files. In Cursor it also says whether the hooks have recorded anything since `on`.
5. `/session-log usage [--latest | <session-id>]` syncs while logging is on, then prints the saved usage report for that session. With no argument it uses the latest session of the current project. While logging is off it prints the report saved at the last sync. A session that was never archived (from before `on`, or any session while logging is off) is read from the harness store and its report is computed on the spot and not saved; `--latest` reads the store only when the project has no archived session.
6. `/session-log off` syncs once more, then stops further syncing and recording. Existing logs stay.

## In Scope
- Commands: `on`, `off`, `status`, `usage`. While logging is on, every command syncs first; while it is off, no command syncs: `status` only reads what is archived, `usage` reads the archive and, for a session that is not archived, the harness store, and `off` does nothing.
- **Logs**: one Markdown file per session in the harness's existing folder (`~/.claude/prompt-logs`, `~/.codex/prompt-logs`, `~/.cursor/prompt-logs`, `~/.omp/agent/prompt-logs`, `~/.config/opencode/prompt-logs`). Path: `<basename, only [A-Za-z0-9._-], ≤64>-<sha256(os.path.normcase(recorded cwd))[:12]>/session_<YYYY-MM-DD_HH-MM-SS, UTC>_<session-id>.md` (the dot stays, as in the old native hook's key, so `my.app` and `.claude` keep one folder with their old logs; the 12-hex hash always follows, so a basename of `.` or `..` is still one safe folder name); a session keeps the path it was first written to. The header has the harness, session ID, project folder and start time. Then each prompt follows as `## <time>` with the prompt text, and `### <time> response` with the final reply text. When a prompt's model or effort differs from the previous prompt's (both known), its response ends with the old Claude log's `switched: model a → b, effort c → d` line, only the part that changed. All times in the log and the usage report are local time with the UTC offset (for example `2026-07-01 12:56:00 UTC+2`); the file name time stays UTC. Tool calls are not logged, as today.
- **State**: `prompt-logs/.log_enabled` holds the on-time (`on` writes it, `off` deletes it); `prompt-logs/.sync.json` is written only by sync. Every command and hook first checks that the harness root is neither relocated nor symlinked, then converts an existing `.enabled` to `.log_enabled` (on-time = now) and removes `.enabled`, then checks `.log_enabled`.
- **Usage**, with the same content as today's Claude report:
  - One row per prompt: time, working time, the first 60 characters of the prompt, input, output, cache-write, cache-read and total tokens, price, model, and effort where the harness records it.
  - One row per sub-agent, nested sub-agents included: type, working time, tokens, price, model, effort.
  - OMP and OpenCode rows (and the TOTAL) also show `reasoning: N` right after `output`: information only, a part of the `output` shown (OpenCode records reasoning apart from `output` and the reader adds it into `output`; OMP's `output` already holds it, its `totalTokens` leaves it out), so it is never added to the total or priced again. Cursor shows the transcript paths its hooks name: `session: <transcript path>` and, per sub-agent, `jsonl: <path>`; the files are never read.
  - **TOTAL**, calculated as today:
    - *Tokens and price:* the main session plus every sub-agent. In Cursor, sub-agent tokens are unavailable (F9), so its TOTAL line reads `tokens: main session only — Cursor does not record sub-agent tokens` and its price is marked incomplete.
    - *Working time:* the sum of the main session's per-prompt working times (prompt submitted → last reply of that turn). Sub-agents run inside those turns, so their own times are shown on their rows but not added again. Check on today's Claude report: 8 prompt times sum exactly to its 05:06:06 TOTAL.
  - **Price** = tokens × the model's per-token rate (or the recorded cost). Effort is shown, because it changes how many tokens a turn uses, but it doesn't change the rate. Fast mode does change the rate (F6).
  - The report is saved beside the log, so it survives the harness deleting its own data.
- **Price rules (F6):**
  - Prefer the cost the harness recorded per message (OMP, OpenCode; a recorded 0 means free).
  - Otherwise use one JSON price file in the skill, read only by the Python script. Each model gets its own `in`, `out`, `cache_read`, `cache_write_5m`, `cache_write_1h` rate, an optional `fast` set of the same five rates, an explicit list of model-name prefixes, a `source` URL and an `as_of` date (team plan C4).
  - An unknown model shows its tokens with `price: unknown`, never $0. A row that mixes it with known models, and a total that includes one, read `≥ $X (incomplete)` (team plan C3).
  - Codex and Cursor prices are labelled "API-equivalent", because subscription billing differs (`developers.openai.com/codex/pricing`).
- **Readers:**
  - *Claude:* the main file, plus its `subagents/` folder, recursively.
  - *Codex (F3):*
    - It reads `sessions/` and `archived_sessions/`, both `*.jsonl` and `*.jsonl.zst`.
    - Prompts come only from `UserMessage` items, or `user_message` events in old-format files. The IDE's context in front of the typed text is cut at the first `## My request for Codex:` line (the Codex desktop app writes `## My request:`), when the message opens with a `#` heading, as Codex's `strip_user_message_prefix` does.
    - Threads with `parent_thread_id` are shown as sub-agents, nested under their parent thread, in the root session that `session_meta.session_id` names (sub-agents of sub-agents exist locally), not as separate logs. A guardian review (a `source.subagent` that is no `thread_spawn`) is never a log of its own: it is only a sub-agent of the thread it reviews, and a review whose parent rollout is missing is not archived.
    - It reads `cache_write_input_tokens`, and counts `cached_input_tokens` and `cache_write_input_tokens` once each, as cache read and cache write and out of the input, because Codex's `input_tokens` already includes both (codex `protocol.rs` `non_cached_input`; `codex-api` `ResponseCompletedUsage`: input 100 = cached 40 + cache write 60, `total_tokens` = input + output).
    - Requests are the `token_usage_record` lines when the rollout has them, else the growth of the cumulative `token_count` total; per-event `last_token_usage` is never summed, because events repeat it (checked on the 709 local rollouts; team plan C2).
  - *OMP (F4):* `sessions/**/*.jsonl` plus `archive/sessions/**/*.jsonl.gz`.
    - A session file is a tree (`id`/`parentId`; the leaf is the last entry, as OMP reloads it). Prompts and replies follow the active branch, the chain of parents from the leaf: the user message of an aborted attempt that was retried from before it, or of any branch left with `/tree`, is no prompt. What those branches spent still counts in the TOTAL: a request beside the active reply stays in its turn, and the others are charged to the next prompt of the active branch, the one that replaced them. Entries without links read in file order.
  - *OpenCode:* the database, found as today (`OPENCODE_DB`, `OPENCODE_CHANNEL`, newest `opencode-*.db`) and opened read-only.
  - *Cursor:* the hook records (prompt, reply, `stop` tokens, `subagentStop` time).
- **Compressed Codex files** (`*.jsonl.zst`) are read with Python's `compression.zstd`, like plain rollouts.
- **Claude hook on Windows (F2):** the README requires a Python install that provides `python3`: pymanager (the python.org default from 3.14), the Store package, or Scoop. On Windows, `on` and `status` run `python3 -c` with a one-line check that the version is at least 3.14 (always with arguments, never bare) and print the exact fix if it fails. The script itself also refuses Python older than 3.14 (team plan S55): a command prints the requirement and one install-fix line for the platform (macOS: Homebrew `python@3.14` or python.org; Windows: the Python install manager; elsewhere: package manager, pyenv or uv) and `on` then creates no flag, so the hooks stay silent.
- **Cursor hooks (F1):**
  - The hook command is `"${CURSOR_PLUGIN_ROOT}/skills/session-log/hook" cursor <event>`, braced and double-quoted, with nothing after it.
  - Two launchers ship next to the script: `hook` (sh, mode 755) and `hook.ps1`. While logging is off they exit 0 without starting Python, as today's hook commands do; both exit 0 when the script or Python is missing; `hook.ps1` tries `py -3`, `python3`, `python`.
  - The Python hook never exits with code 2 and never prints to stdout. It reads stdin as bytes and decodes UTF-8 with an optional BOM, because pwsh adds one; bytes that are not UTF-8 are replaced, so a record is never lost.
- **Skill command (F2):** SKILL.md tells the model to run `python3 "<skill dir>/session_log.py" <harness> <arguments>`, with the directory in double quotes, which work the same in sh-family shells and PowerShell. On Windows, if that fails, it tries `py -3`, then `python`, with the same arguments.
- **The harness identity is passed explicitly**, as today (`SKILL.md`: never inferred from paths or environment).
- **Old-release leftovers:** `on` and `status` find them: their hook entries in `~/.claude/settings.json`, `~/.codex/hooks.json` and `~/.cursor/hooks.json`, package copies seeded into skill folders (the harness's own and the shared `~/.agents/skills/session-log`, recognized by old package files, the ownership marker or an old link), the old release store `~/.local/share/universal-session-log`, and old state folders (team plan C1). The shared copy and the store are reported by every harness; everything else only by the invoked harness. They print each path with a one-line fix: a move command in POSIX or PowerShell syntax (by platform), or, where a hook entry runs a listed file, one `First delete …, then move …` item, because moving the file first makes the hook fail at every prompt (Cursor blocks it). Nothing is deleted automatically. A seeded copy that a harness loads instead of the plugin's skill runs old code, so the new report never reaches that user; the README's upgrade note tells them to move it aside.
- **OpenCode fix (F5):** `.opencode-plugin/index.js:34` changes `location: file` to `path: file`. The tests at `.opencode-plugin/index.test.js:79,92` change to `.path`, and the test name at `:75` changes from "location" to "path". The same release removes the session-log import at `index.js:63-70` and its tests (team plan Q28).
- **Clean cutover:**
  - Remove `adapters/claude`, `adapters/native`, `adapters/codex`, `adapters/cursor`, `bin/`, `lib/`, `install.sh`, the OMP and OpenCode usage scripts and their tests.
  - Remove the OMP and OpenCode session-log adapters (`adapters/omp`, `adapters/opencode`) and the `package.json` `omp.extensions` entry. The `.opencode-plugin/index.js` import of the handlers and `/session-log` command already goes in the OpenCode fix release (F5).
  - Delete `skills/session-log/VERSION` with its release guard in `release.sh`.
  - Replace `adapters/claude/scripts/prompt_log_prices.json` with the new price file.
  - Update README.md, SKILL.md and the session-log handouts.
- **Tests:**
  - One small, anonymized fixture per harness, cut from a real file, with a `.zst` Codex case and a `.gz` OMP case.
  - Unit tests for each reader, the writer, the usage maths and the price rules, run with `python3 -m unittest` on macOS and in a Linux Docker container.
  - A launcher test: the `hook` script runs under sh, zsh and fish, and `hook.ps1` under pwsh; it exits 0 with the script or Python missing.
  - One real smoke run per harness on the Mac: through its one-prompt mode for Claude, Codex, OMP and OpenCode, and through the Cursor app for Cursor, because `agent -p` ran no hooks in the F9 test.
  - One written manual checklist for Windows covering `on`, one prompt, `status` and `usage` in each harness, including Cursor under Windows PowerShell 5.1 without pwsh.

## Out of Scope
- Live, per-prompt writing in Claude Code, Codex, OMP and OpenCode. The log can lag until the next session start or `/session-log` command, and nothing is lost because the harness still has the data.
- `repair`: nothing is registered that could need repair.
- Automatic deletion of old-release leftovers: they are reported, and the user moves them aside.
- Cursor sub-agent tokens: Cursor gives them neither to hooks nor to the parent's usage, and doesn't store them in readable form (F7, F9). The report lists each Cursor sub-agent with its type and time and marks its tokens `not recorded by Cursor`. This is the same gap as today, now stated in the TOTAL line.
- Renaming the `claude-goodies` plugin, release-script fixes and other findings from the superseded brief that are not about session-log working.
- Any new tool, CI or shipped binary. The launchers are small text scripts, not binaries.

## Key Decisions
- **Read what the harness saved instead of recording it live**: the four harnesses already save complete data. Recording it again is where the complexity and the Windows breakage come from.
- **Copy into an own archive**: Claude Code deletes transcripts after 30 days by default.
- **Hooks only where unavoidable**: Claude's session-start hook protects against the 30-day cleanup. Cursor's hooks are the only source of its times and tokens. Codex needs no hook, because it deletes nothing (F3).
- **Python standard library**: `json`, `sqlite3`, `gzip`, `pathlib`, `compression.zstd` and `os.replace` cover every store on all three OSes. Python is already required, but the Command Line Tools' `/usr/bin/python3` is only 3.9.6 (`/usr/bin/python3 --version`), so the minimum is 3.14 and macOS users install Python from python.org or Homebrew.
- **`python3` for the Claude hook**: there is no per-OS field, and `python`/`py` don't exist on stock macOS (F2). Writing an absolute interpreter path into `~/.claude/settings.json` was rejected, because it brings back the hook registration and cleanup this design removes.
- **Launchers for Cursor**: they are the only tested way to get one hook command that is safe in sh-family shells and PowerShell and never blocks a prompt (F1).
- **Whole-file rewrite, no append**: this removes locks, partial writes and duplicate-entry handling.
- **One reader per harness, one shared writer**: the separation the user asked for (each harness on its own) without copying the rendering code five times.

## Edge Cases & Constraints
- **Logging off**: the Claude and Cursor hooks check the flag file first (after converting an old `.enabled`) and exit 0 at once. A hook never blocks a prompt and prints nothing while it runs.
- **Script or Python missing mid-update**: the Claude session-start hook can't block. A missing script exits 0 through the hook's guard; a missing `python3` is a non-blocking error, and exit code 2 on `SessionStart` only shows stderr to the user (`hooks.md`, "Other exit codes" and the exit-code-2 table). The Cursor launchers exit 0.
- **Session still running during sync**: the harness file may lag the last turn (`hooks.md`, `transcript_path`). The next sync rewrites the file complete.
- **Harness changes its format**: the reader skips records it doesn't understand without reporting them, and `status` names the harness and file only when nothing in the file can be read. Only that harness is affected.
- **Cursor on Windows PowerShell 5.1 without pwsh**: non-ASCII prompt text in the hook input may be garbled (F1). The README recommends PowerShell 7 for Cursor on Windows. Times and tokens are unaffected.
- **Windows**: paths only through `pathlib`, files written with LF, and no output parsed through a shell.

## Open Questions
None open on the product. The team plan's review decisions wait for the user's confirmation (team plan Q29). The Windows questions were decided on 2026-10-19 (team plan Q1–Q3, Q22):
- **Release without waiting for Windows.** The README marks every harness on native Windows "not yet tested" until the maintainer's manual run passes (extended from Cursor alone in team-plan review C1).
- **That run happens on a real Windows PC after the release.** It also checks that Claude's no-shell hook can start the `python3` app alias. If it can't, that hook switches to shell form; which shell it then uses goes back to the user (team plan Q3).
- **What the Store `python3` stand-in returns no longer matters.** The Python check treats any failure as "Python not usable" and prints the fix.

## Future Iterations
- More harnesses: each one is one more reader.
- Export several sessions or a date range into one file.
- A `--check` that compares estimated prices with OMP's recorded costs and Claude's `cost-state` totals, and flags a stale price table.

## Recommendation
Build this instead of the Python-core plan. It gives the same two results, logs and usage, on all five harnesses and three OSes, with a small fraction of the code. My estimate for the product code is under 1,000 lines, and that is an estimate, not a measurement. Ship the OpenCode `path` fix (F5) first and on its own: today it disables the whole OpenCode plugin. That release also removes the plugin's session-log import, so the old handlers do not come back (team plan Q28). Do not compromise on two rules: a hook never blocks a prompt, and every log is rebuilt whole from the harness's own data.

---

## References

- **Team plan:** [session-log-simple-team-plan.md](./session-log-simple-team-plan.md)
