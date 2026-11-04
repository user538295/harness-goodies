---
name: estimate
description: Story Point estimation of a brief, plan, task list, epic or single task by comparison with the project's own delivered features, kept with their real commits, lines changed, agent time, tokens and cost in `.estimation.json`. `/estimate <file.md> [--from <source.md>]` estimates a document; `/estimate init` collects the delivered features as references. Use when the user runs /estimate, asks how big a piece of planned work is, or when a planning skill or /implement calls it.
---

# /estimate

Sizes planned work in Story Points (SP) relative to the project's delivered features, and keeps every feature's estimate next to its real effort. All data lives in `.estimation.json` at the **project root** (`git rev-parse --show-toplevel`, else the current directory). Only the bundled script `estimate.py` reads and writes that file — never edit it by hand. Committing `.estimation.json` is the user's job.

`estimate.py` needs **Python 3.14 or newer**. macOS's stock `python3` is 3.9.6: if the script reports a too-old Python, tell the user to install Python 3.14+ from python.org or Homebrew and stop.

## Step 0 — Locate the scripts

1. **Harness.** Select the identity of the host that loaded this skill from runtime/system context: `claude`, `codex`, `cursor`, `opencode` or `omp`. Never infer it from paths, processes or environment variables. It replaces `<harness>` below.
2. **`$BASE`** is this skill's own directory. Claude Code and OpenCode print `Base directory for this skill: <path>`; omp prints `[Skill directory: <path>]`; in Cursor and Codex, use the directory of the `SKILL.md` file you were given. Take that path verbatim as `$BASE`. Only if no directory was reported, run this harness-neutral locator (an explicit `ESTIMATE_HOME=<skill dir>` wins; then project-level roots, then user-level roots of Claude Code, Cursor, OpenCode, omp and Codex, then the newest plugin caches) and capture the printed path as `$BASE`; on the error, stop and show it to the user:

```bash
BASE=""; for d in "${ESTIMATE_HOME:-}" .agents/skills/estimate .claude/skills/estimate .cursor/skills/estimate .opencode/skills/estimate .codex/skills/estimate ~/.agents/skills/estimate ~/.claude/skills/estimate ~/.cursor/skills/estimate ~/.config/opencode/skills/estimate ~/.omp/agent/skills/estimate ~/.codex/skills/estimate "$(ls -d ~/.claude/plugins/cache/*/claude-goodies/*/skills/estimate 2>/dev/null | sort -V | tail -1)" "$(ls -d ~/.omp/plugins/cache/plugins/*___omp-goodies___*/skills/estimate 2>/dev/null | sort -V | tail -1)" "$(ls -d ~/.codex/plugins/cache/*/codex-goodies/*/skills/estimate 2>/dev/null | sort -V | tail -1)" "$(ls -dt ~/.cursor/plugins/cache/*/cursor-goodies/*/skills/estimate 2>/dev/null | head -1)" "$(ls -dt ~/.cache/opencode/npm/*/*/node_modules/opencode-goodies/skills/estimate 2>/dev/null | head -1)"; do [ -n "$d" ] && [ -f "$d/estimate.py" ] && { BASE="$d"; break; }; done; [ -n "$BASE" ] && echo "$BASE" || { echo "ERROR: estimate not found in any known skills root — set ESTIMATE_HOME=<skill dir>" >&2; false; }
```

3. **`$SESSION_LOG_DIR`** is the session-log skill's directory, which holds `session_log.py`. Find it with its own locator (an explicit `SESSION_LOG_HOME=<skill dir>` wins). The locator assigns `BASE` too, so run it in a subshell and capture its output: `SESSION_LOG_DIR="$( <the block below> )"`. On the error, do not stop: run without session data and list "no session data: session-log not found" in the missing-data report.

```bash
BASE=""; for d in "${SESSION_LOG_HOME:-}" .agents/skills/session-log .claude/skills/session-log .cursor/skills/session-log .opencode/skills/session-log .codex/skills/session-log ~/.agents/skills/session-log ~/.claude/skills/session-log ~/.cursor/skills/session-log ~/.config/opencode/skills/session-log ~/.omp/agent/skills/session-log ~/.codex/skills/session-log "$(ls -d ~/.claude/plugins/cache/*/claude-goodies/*/skills/session-log 2>/dev/null | sort -V | tail -1)" "$(ls -d ~/.omp/plugins/cache/plugins/*___omp-goodies___*/skills/session-log 2>/dev/null | sort -V | tail -1)" "$(ls -d ~/.codex/plugins/cache/*/codex-goodies/*/skills/session-log 2>/dev/null | sort -V | tail -1)" "$(ls -dt ~/.cursor/plugins/cache/*/cursor-goodies/*/skills/session-log 2>/dev/null | head -1)" "$(ls -dt ~/.cache/opencode/npm/*/*/node_modules/opencode-goodies/skills/session-log 2>/dev/null | head -1)"; do [ -n "$d" ] && [ -f "$d/session_log.py" ] && { BASE="$d"; break; }; done; [ -n "$BASE" ] && echo "$BASE" || { echo "ERROR: session-log not found in any known skills root — set SESSION_LOG_HOME=<skill dir>" >&2; false; }
```

Run every command below **from the user's repository**, never from `$BASE`. JSON output goes to stdout. A failure is one `estimate: <message>` line on stderr with exit 1 (runtime) or 2 (bad arguments): report it as it is. A `save` also names on stderr every value it ignored (a frozen or user-set SP): report those lines too.

## Commands

```
python3 "$BASE/estimate.py" read [--references]
python3 "$BASE/estimate.py" save <entry-json | ->
python3 "$BASE/estimate.py" remove <name>
python3 "$BASE/estimate.py" doc-stats <file.md> [--plan-progress <path>]
python3 "$BASE/estimate.py" git-stats [--doc <path>]... [--task-id <id>]...
python3 "$BASE/estimate.py" match-sessions <features-json | -> --session-log "$SESSION_LOG_DIR/session_log.py"
python3 "$BASE/estimate.py" record-commit <plan-file> <commit> --harness <harness> [--session-log "$SESSION_LOG_DIR/session_log.py"]
```

- `read` prints the file (an empty one when it is absent); `--references` keeps only the delivered features with an after-delivery SP — the only features ever used for comparison.
- `save` upserts one feature from a partial entry (pass JSON with `-` on stdin). It targets the feature whose `sources.docs` shares a path with the entry's, else the one with the same `name`, else creates one (`name` and a `description` of at most 300 characters are then required). Given fields replace stored ones, except: `storyPoints` and `estimatedAt` freeze once the feature is `in-progress` or `delivered`, a non-null `updatedStoryPoints` never changes, and a null `plan` count never overwrites a known one. SPs must be values of the file's `scale`. `sources.docs` are merged; `git` replaces `git` and `sources.commits` together; sessions are upserted by harness and id; `usage` totals are always recomputed from the sessions.
- `doc-stats` counts phases, tasks and ticked tasks (through `/implement`'s `plan-progress.sh`) and sums the `· <n>h` task estimates. When it fails on a document, work the counts out by reading the document and say so.
- `git-stats` finds the commits that change any `--doc` or name any `--task-id` in their message.
- `match-sessions` assigns past sessions of all five harnesses to features: by prompts naming a feature's docs or task IDs, else by the feature's commit dates. A prompt match always beats dates.
- `record-commit` adds one task commit to its feature (see "Record a commit").

## `/estimate <file.md> [--from <source.md>]`

`--from` names the document a planning skill started from (the brief of a plan, the team plan of a task list). Both documents go into `sources.docs`, so the save updates that document's entry instead of creating a second one.

**The estimate sharpens with each document.** A feature's documents add detail in order: the brief, then the plan or team plan, then the tasks file. Each is estimated when it is written, and each save replaces the feature's SP. So the estimate saved last before the first task commit, normally the tasks file's, becomes the frozen original `storyPoints`: the one the accuracy check compares with the delivered size. Each document keeps the SP line it was written with, so a brief can show 8 while its tasks file shows 13.

1. Run `read --references`. When it lists no feature, still estimate, but open the report with: **"Warning: no delivered features to compare with — this estimate is not based on past work. Run `/estimate init`."**
2. Read `<file.md>` and run `doc-stats <file.md>`.
3. Compare the work with the references by their `updatedStoryPoints` and pick a value from the file's `scale`: name the 2–3 closest features and why (e.g. "bigger than X (5), smaller than Y (13)").
4. Expected effort: the agent time, tokens and cost of the references with the chosen SP (their range, naming them); none at that SP → list it as missing.
5. Split suggestion: only on a numeric scale and only at 40 SP or more, suggest how to cut the work into smaller deliverable pieces.
6. Run `read` and find the feature that lists `<file.md>` or `<source.md>`; reuse its `name`. When it already lists a more detailed document than `<file.md>` (a plan or tasks file for a brief, a tasks file for a plan), show the estimate but save it only after the user confirms; without confirmation save nothing and say the more detailed estimate stays. Save, on stdin to `save -`: `{"name", "description", "storyPoints", "estimatedAt": "<today YYYY-MM-DD>", "plan": {"phases", "tasks", "estimatedHours"}, "sources": {"docs": ["<file.md>", "<source.md>"]}}` (no `<source.md>` without `--from`). If `save` reports the SP as frozen, say the original SP stays and the new value is not saved.
7. Report: the SP; the closest features with reasons; phases, tasks and estimated hours when the file has them; the expected agent time, tokens and cost; the split suggestion; and every value that could not be found.

## `/estimate init`

Builds the references from the delivered features, one per planning-doc set (a brief, plan, team plan and tasks file of one feature, linked by their References sections, front matter or shared name). A set counts as delivered when its plan or tasks file has every task ticked; work without planning docs is skipped.

1. Run `read` and find the planning-doc sets in the repository.
2. **One sub-agent per feature** (inline, one by one, when the harness has no sub-agent tool). Each runs `doc-stats` on the set's docs and `git-stats` with every doc as `--doc` and every task ID as `--task-id`, and returns `name`, a description of at most 300 characters, `docs`, `taskIds`, `plan`, the git-stats result, and its missing values. Sub-agents never write `.estimation.json`.
3. **The main agent alone** runs `match-sessions` once over all features: a prompt match must beat dates across features, which needs one global view. Input: `{"features": [{"name", "docs", "taskIds", "firstDate", "lastDate"}]}`, the dates from git-stats.
4. Rank the features by real size (lines changed, commits, tasks, agent time) and propose an `updatedStoryPoints` from the scale for each, a mid-size feature being 5. Show one table: name, commits, lines +/−, tasks, hours, agent time, tokens, cost, `matchedBy`, proposed SP. A feature that already has an `updatedStoryPoints` keeps it: show it as kept. Let the user adjust the table.
5. `save` each feature: `{"name", "description", "status": "delivered", "updatedStoryPoints", "plan": {"phases", "tasks", "estimatedHours"}, "git": {"commits": <git-stats count>, "linesAdded", "linesRemoved"}, "usage": {"matchedBy"}, "sources": {"docs", "commits": <git-stats commits>, "sessions"}}`, with `matchedBy` and `sessions` from `match-sessions`. `git` and `sources.commits` always go together; send only these fields (an unknown field is rejected). A feature whose stored `usage.matchedBy` is `implement` gets no `usage` and no `sessions`: its recorded sessions are never replaced. `storyPoints` is never sent, so an estimate made before work stays.
6. Report every value that could not be found (from the sub-agents, `doc-stats`, `match-sessions`), e.g. "no session data for feature X".

Running `init` again adds newly delivered features and refreshes the actuals of the others, also of estimated features finished outside `/implement`; it never changes an SP the user set.

## Record a commit

`/implement` runs this as the last action of the step that commits a task, for the task-breakdown file and the new commit:

```
python3 "$BASE/estimate.py" record-commit <plan-file> <commit> --harness <harness> --session-log "$SESSION_LOG_DIR/session_log.py"
```

Leave out `--session-log` when session-log was not found. It adds the commit's lines, refreshes the plan counts and the current session's numbers, and sets the feature `in-progress`, or `delivered` once every task is ticked. It prints `{"feature", "needsResize", "missing"}`.

- **`needsResize: true`** — the feature was just delivered: re-size it from everything gathered (its actuals against `read --references`, as in `/estimate` step 3) and `save` `{"name": "<feature name>", "updatedStoryPoints": <SP>}`. The original `storyPoints` stays; the same value in both means the estimate was right.
- Report the feature's status and every `missing` value. A failure (e.g. "no feature lists <plan>; run /estimate <plan> first") is reported and never blocks the task. 
