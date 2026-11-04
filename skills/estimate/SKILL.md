---
name: estimate
description: Story Point estimation of a brief, plan, task list, epic or single task by comparison with the project's own delivered features, kept with their real commits, lines changed, agent time, tokens and cost in one file per feature under `.harness-goodies/estimations/`. `/estimate <file.md> [--from <source.md>]` estimates a document; `/estimate init` collects the delivered features as references. Use when the user runs /estimate, asks how big a piece of planned work is, or when a planning skill or /implement calls it.
---

# /estimate

Sizes planned work in Story Points (SP) relative to the project's delivered features, and keeps every feature's estimate next to its real effort. All data lives under `.harness-goodies/` at the **project root** (`git rev-parse --show-toplevel`, else the current directory): the scale in `estimation.json`, written with the default scale by the first save and the user's to edit from then on, and one file per feature in `estimations/`, so features estimated on parallel branches never touch the same file. Only the bundled script `estimate.py` writes the feature files — never edit them by hand. Committing `.harness-goodies/` is the user's job.

`estimate.py` needs **Python 3.14 or newer**. macOS's stock `python3` is 3.9.6: if the script reports a too-old Python, tell the user to install Python 3.14+ from python.org or Homebrew and stop.

## Step 0 — Locate the scripts

1. **Harness.** Select the identity of the host that loaded this skill from runtime/system context: `claude`, `codex`, `cursor`, `opencode` or `omp`. Never infer it from paths, processes or environment variables. The skill directory may sit in another harness's plugin folder (omp also loads Claude Code's plugins): that is expected and changes nothing, so do not remark on it. It replaces `<harness>` below.
2. **`$BASE`** is this skill's own directory. Claude Code and OpenCode print `Base directory for this skill: <path>`; omp prints `[Skill directory: <path>]`; in Cursor and Codex, use the directory of the `SKILL.md` file you were given. Take that path verbatim as `$BASE`. Only if no directory was reported, run this harness-neutral locator (an explicit `ESTIMATE_HOME=<skill dir>` wins; then project-level roots, then user-level roots of Claude Code, Cursor, OpenCode, omp and Codex, then the newest plugin caches) and capture the printed path as `$BASE`; on the error, stop and show it to the user:

```bash
BASE=""; for d in "${ESTIMATE_HOME:-}" .agents/skills/estimate .claude/skills/estimate .cursor/skills/estimate .opencode/skills/estimate .codex/skills/estimate ~/.agents/skills/estimate ~/.claude/skills/estimate ~/.cursor/skills/estimate ~/.config/opencode/skills/estimate ~/.omp/agent/skills/estimate ~/.codex/skills/estimate "$(ls -d ~/.claude/plugins/cache/*/claude-goodies/*/skills/estimate 2>/dev/null | sort -V | tail -1)" "$(ls -d ~/.omp/plugins/cache/plugins/*___omp-goodies___*/skills/estimate 2>/dev/null | sort -V | tail -1)" "$(ls -d ~/.codex/plugins/cache/*/codex-goodies/*/skills/estimate 2>/dev/null | sort -V | tail -1)" "$(ls -dt ~/.cursor/plugins/cache/*/cursor-goodies/*/skills/estimate 2>/dev/null | head -1)" "$(ls -dt ~/.cache/opencode/npm/*/*/node_modules/opencode-goodies/skills/estimate 2>/dev/null | head -1)"; do [ -n "$d" ] && [ -f "$d/estimate.py" ] && { BASE="$d"; break; }; done; [ -n "$BASE" ] && echo "$BASE" || { echo "ERROR: estimate not found in any known skills root — set ESTIMATE_HOME=<skill dir>" >&2; false; }
```

3. **`$SESSION_LOG_DIR`** is the session-log skill's directory, which holds `session_log.py`. Find it with its own locator (an explicit `SESSION_LOG_HOME=<skill dir>` wins). The locator assigns `BASE` too, so run it in a subshell and capture its output: `SESSION_LOG_DIR="$( <the block below> )"`. On the error, do not stop: run without session data and list "no session data: session-log not found" in the missing-data report.

```bash
BASE=""; for d in "${SESSION_LOG_HOME:-}" .agents/skills/session-log .claude/skills/session-log .cursor/skills/session-log .opencode/skills/session-log .codex/skills/session-log ~/.agents/skills/session-log ~/.claude/skills/session-log ~/.cursor/skills/session-log ~/.config/opencode/skills/session-log ~/.omp/agent/skills/session-log ~/.codex/skills/session-log "$(ls -d ~/.claude/plugins/cache/*/claude-goodies/*/skills/session-log 2>/dev/null | sort -V | tail -1)" "$(ls -d ~/.omp/plugins/cache/plugins/*___omp-goodies___*/skills/session-log 2>/dev/null | sort -V | tail -1)" "$(ls -d ~/.codex/plugins/cache/*/codex-goodies/*/skills/session-log 2>/dev/null | sort -V | tail -1)" "$(ls -dt ~/.cursor/plugins/cache/*/cursor-goodies/*/skills/session-log 2>/dev/null | head -1)" "$(ls -dt ~/.cache/opencode/npm/*/*/node_modules/opencode-goodies/skills/session-log 2>/dev/null | head -1)"; do [ -n "$d" ] && [ -f "$d/session_log.py" ] && { BASE="$d"; break; }; done; [ -n "$BASE" ] && echo "$BASE" || { echo "ERROR: session-log not found in any known skills root — set SESSION_LOG_HOME=<skill dir>" >&2; false; }
```

Run every command below **from the user's repository**, never from `$BASE`. JSON output goes to stdout. A failure is one `estimate: <message>` line on stderr with exit 1 (runtime) or 2 (bad arguments): report it as it is. A `cannot read <file>` failure usually means an unresolved merge conflict in that feature file: tell the user to resolve it in git, since every command needs every feature file readable. Any command may also print warnings as `estimate:` lines on stderr (an ignored frozen or user-set SP, a replaced id, duplicate files): report each one.

## Commands

```
python3 "$BASE/estimate.py" read [--references]
python3 "$BASE/estimate.py" save <entry-json | ->
python3 "$BASE/estimate.py" remove <file>
python3 "$BASE/estimate.py" doc-stats <file.md> [--plan-progress <path>]
python3 "$BASE/estimate.py" git-stats <features-json | ->
python3 "$BASE/estimate.py" match-sessions <features-json | -> --session-log "$SESSION_LOG_DIR/session_log.py"
python3 "$BASE/estimate.py" record-commit <plan-file> <commit> --harness <harness> [--session-log "$SESSION_LOG_DIR/session_log.py"]
```

- `read` prints `{"version", "scale", "features"}`: the scale and every feature file in name order, each feature with its `file` name (the default scale and no features when there are none); `--references` keeps only the delivered features with an after-delivery SP — the only features ever used for comparison.
- A feature's file is `<first day>[-<id>]-<name>.json` in lowercase ASCII (e.g. `2026-11-04-pm-12-csv-import.json`); the id and name parts are cut when long. The first day is set when the file is created: the earliest of the day git first added each of its docs, the creation day of each doc not committed yet, the day of its first commit, and today. A changed name or id renames the file; its first day never changes.
- Every command that reads the files names on stderr each name, id or doc that two feature files share, e.g. `duplicate name 'X': 2026-10-01-x.json, 2026-10-02-x.json` (usually after two branches were merged); ids are compared by their file-name form, so `PM-12` and `pm-12` are the same id. A shared name or id means one file is a leftover copy: report it with both files and ask the user which one to keep; `remove <file>` deletes the other. A doc listed by two features on purpose (e.g. one plan for two features) may stay. Under `/implement` (`record-commit`, possibly headless) only report the duplicates; never ask or block. A command whose own feature is ambiguous fails, naming both files.
- `save` upserts one feature from a partial entry (pass JSON with `-` on stdin). It targets the feature whose `sources.docs` shares a path with the entry's, else the one with the same `name`, else creates one (`name` and a `description` of at most 300 characters are then required). Given fields replace stored ones, except: `storyPoints` and `estimatedAt` freeze once the feature is `in-progress` or `delivered`, a non-null `updatedStoryPoints` never changes, a null `plan` count never overwrites a known one, and a missing or null `id` never erases a stored one (a different `id` replaces it with a warning). Names and ids are unique: a save is refused when it would give its feature a name or id another feature already holds (a name or id already shared is only warned about), and an id without a letter or digit is refused. SPs must be values of the `scale`. `sources.docs` are merged; `git` replaces `git` and `sources.commits` together; given `sources.sessions` replace the stored ones; `usage` totals are always recomputed from the sessions.
- `remove <file>` deletes one feature's file, named as `read` shows its `file`.
- `doc-stats` counts phases, tasks and ticked tasks (through `/implement`'s `plan-progress.sh`) and sums the `· <n>h` task estimates. When it fails on a document, work the counts out by reading the document and say so.
- `git-stats` and `match-sessions` take the same input, all features at once: `{"features": [{"name", "docs", "taskIds"}]}`. A doc path, doc basename, task ID or commit that more than one feature lists is evidence for none of them (every plan reuses `BE-1`), which only one global view can tell.
- `git-stats` gives each feature the commits that change one of its docs or name one of its unshared task IDs, with their lines; stderr names the shared task IDs.
- `match-sessions` reads the past sessions of all five harnesses (session-log `list`, then `usage` with its per-prompt rows) and gives each feature its slice of a session, prompt by prompt: a prompt belongs to the features its text names, else to the feature(s) with the most commits made while it ran; a prompt with neither follows the prompt before it (earlier prompts the first match); a sub-agent follows the prompt it started under. Each slice is a session entry whose `prompts` lists the counted prompt numbers (the rows of `session-log usage`) and whose numbers are those rows' sums, so a session that worked on three features is split three ways, never counted three times. Nothing is matched by dates. `missing` names failed calls, sessions without prompt rows (counted whole, `prompts: null`) and, per feature, commits made while no prompt of a listed session ran (made by hand, or in sessions whose transcripts are gone).
- `record-commit` adds one task commit to its feature (see "Record a commit").

## `/estimate <file.md> [--from <source.md>]`

`--from` names the document a planning skill started from (the brief of a plan, the team plan of a task list). Both documents go into `sources.docs`, so the save updates that document's entry instead of creating a second one.

**The estimate sharpens with each document.** A feature's documents add detail in order: the brief, then the plan or team plan, then the tasks file. Each is estimated when it is written, and each save replaces the feature's SP. So the estimate saved last before the first task commit, normally the tasks file's, becomes the frozen original `storyPoints`: the one the accuracy check compares with the delivered size. Each document keeps the SP line it was written with, so a brief can show 8 while its tasks file shows 13.

1. Run `read --references`. When it lists no feature, still estimate, but open the report with: **"Warning: no delivered features to compare with — this estimate is not based on past work. Run `/estimate init`."**
2. Read `<file.md>` and run `doc-stats <file.md>`. Note the feature's own ID when the document shows one: in its title, its front matter `id:` or its file name (e.g. `PM-12` of `PM-12-csv-import.md`), even when it looks like `TASK-001` or `FEAT-001`. The ID of an item inside the document (a task, scenario, contract or question: `BE-1`, `FE-2`, `T-3`, `K1`, `S1`, `C1`, `Q1`) is never the feature's ID; when unsure, note none.
3. Compare the work with the references by their `updatedStoryPoints` and pick a value from the `scale`: name the 2–3 closest features and why (e.g. "bigger than X (5), smaller than Y (13)").
4. Expected effort: the agent time, tokens and cost of the references with the chosen SP (their range, naming them); none at that SP → list it as missing.
5. Split suggestion: only on a numeric scale and only at 40 SP or more, suggest how to cut the work into smaller deliverable pieces.
6. Run `read` and find the feature that lists `<file.md>` or `<source.md>`; reuse its `name`. When it already lists a more detailed document than `<file.md>` (a plan or tasks file for a brief, a tasks file for a plan), show the estimate but save it only after the user confirms; without confirmation save nothing and say the more detailed estimate stays. Save, on stdin to `save -`: `{"id", "name", "description", "storyPoints", "estimatedAt": "<today YYYY-MM-DD>", "plan": {"phases", "tasks", "estimatedHours"}, "sources": {"docs": ["<file.md>", "<source.md>"]}}` (no `id` when step 2 noted none; no `<source.md>` without `--from`). If `save` reports the SP as frozen, say the original SP stays and the new value is not saved.
7. Report: the SP; the closest features with reasons; phases, tasks and estimated hours when the file has them; the expected agent time, tokens and cost; the split suggestion; the feature's `file`; and every value that could not be found.

## `/estimate init`

Builds the references from the delivered features, one per planning-doc set (a brief, plan, team plan and tasks file of one feature, linked by their References sections, front matter or shared name). A set counts as delivered when its plan or tasks file has every task ticked; work without planning docs is skipped.

1. Run `read` and find the planning-doc sets in the repository.
2. **One sub-agent per feature** (inline, one by one, when the harness has no sub-agent tool). Each runs `doc-stats` on the set's docs and returns `name`, a description of at most 300 characters, the feature's `id` (found as in `/estimate` step 2, else null), `docs` (every doc of the set), `taskIds` (every task ID of its plan or tasks file), `plan`, and its missing values. Sub-agents never write the estimate files.
3. **The main agent alone** runs `git-stats` and then `match-sessions` once each over all features, both with `{"features": [{"name", "docs", "taskIds"}]}`: shared names and commits can only be told apart with every feature in view. Take their results as they are: never drop, add or re-assign sessions by hand, and never fall back to dates.
4. Rank the features by real size (lines changed, commits, tasks, agent time) and propose an `updatedStoryPoints` from the scale for each, a mid-size feature being 5. Show one table: name, commits, lines +/−, tasks, hours, sessions, agent time, tokens, cost, `matchedBy`, proposed SP. A feature that already has an `updatedStoryPoints` keeps it: show it as kept. Never wait for the user: go straight on to step 5 with these values.
5. `save` each feature: `{"id", "name", "description", "status": "delivered", "updatedStoryPoints", "plan": {"phases", "tasks", "estimatedHours"}, "git": {"commits": <git-stats count>, "linesAdded", "linesRemoved"}, "usage": {"matchedBy"}, "sources": {"docs", "commits": <git-stats commits>, "sessions"}}`, with `matchedBy` and `sessions` from `match-sessions`. `git` and `sources.commits` always go together; send only these fields (an unknown field is rejected). A feature whose stored `usage.matchedBy` is `implement` gets no `usage` and no `sessions`: its recorded sessions are never replaced. `storyPoints` is never sent, so an estimate made before work stays.
6. Report every value that could not be found (from the sub-agents, `doc-stats`, `git-stats`, `match-sessions`), e.g. "P2-03: no session found for 14 of 14 commits".

Running `init` again adds newly delivered features and refreshes the actuals of the others, also of estimated features finished outside `/implement`; it never changes a saved `updatedStoryPoints`.

## Record a commit

`/implement` runs this as the last action of the step that commits a task, for the task-breakdown file and the new commit:

```
python3 "$BASE/estimate.py" record-commit <plan-file> <commit> --harness <harness> --session-log "$SESSION_LOG_DIR/session_log.py"
```

Leave out `--session-log` when session-log was not found. It adds the commit's lines, refreshes the plan counts and the feature's slice of the current session (its prompts, by the `match-sessions` rule over every stored feature's docs and commits, so earlier work of the same session on another feature is not counted), and sets the feature `in-progress`, or `delivered` once every task is ticked. It prints `{"feature", "needsResize", "missing"}`.

- **`needsResize: true`** — the feature was just delivered: re-size it from everything gathered (its actuals against `read --references`, as in `/estimate` step 3) and `save` `{"name": "<feature name>", "updatedStoryPoints": <SP>}`. The original `storyPoints` stays; the same value in both means the estimate was right.
- Report the feature's status and every `missing` value. A failure (e.g. "no feature lists <plan>; run /estimate <plan> first") is reported and never blocks the task. 
