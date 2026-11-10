---
name: estimate
description: Story Point estimation of a brief, plan, task list, epic or single task, calculated from planned hours at the scale's anchor or else placed by blind comparison with the project's delivered features, kept with their real effort in `.harness-goodies/estimations/`. Commands `/estimate <file.md> [--from <source.md>]` (estimate a document), `/estimate init` (collect delivered features as references), `/estimate usage <file.md>` (attach the sessions that worked on a feature to its stored estimate), `/estimate report` (offline HTML accuracy report). Use when the user runs /estimate, asks how big planned work is, asks how good the estimates are, or a planning skill or /implement calls it.
---

# /estimate

Sizes planned work in Story Points (SP) relative to the project's delivered features, and keeps every feature's estimate next to its real effort. All data lives under `.harness-goodies/` at the **project root** (`git rev-parse --show-toplevel`, else the current directory): `estimation.json` holds the scale and its anchor (written with defaults by the first save, the user's to edit after), and `estimations/` holds one file per feature, so parallel branches never touch the same file. Only `estimate.py` writes feature files: never edit them by hand. Committing `.harness-goodies/` is the user's job.

`estimate.py` needs **Python 3.14+**. If the script reports a too-old Python, show its message, tell the user to install Python 3.14+, and stop.

## Step 0 — Locate the scripts

1. **Harness.** Take the identity of the host that loaded this skill from runtime/system context: `claude`, `codex`, `cursor`, `opencode` or `omp`. Never infer it from paths, processes or environment variables. The skill directory may sit in another harness's plugin folder (omp also loads Claude Code's plugins): that is expected, do not remark on it. It replaces `<harness>` below.
2. **`$BASE`** is this skill's own directory, taken verbatim from the host: Claude Code and OpenCode print `Base directory for this skill: <path>`; omp prints `[Skill directory: <path>]`; in Cursor and Codex use the directory of the `SKILL.md` you were given. Only if none was reported, run this locator and capture the printed path as `$BASE`; on error, stop and show it. An explicit `ESTIMATE_HOME=<skill dir>` wins, then project roots, then user roots, then the newest plugin caches.

```bash
BASE=""; for d in "${ESTIMATE_HOME:-}" .agents/skills/estimate .claude/skills/estimate .cursor/skills/estimate .opencode/skills/estimate .codex/skills/estimate ~/.agents/skills/estimate ~/.claude/skills/estimate ~/.cursor/skills/estimate ~/.config/opencode/skills/estimate ~/.omp/agent/skills/estimate ~/.codex/skills/estimate "$(ls -d ~/.claude/plugins/cache/*/claude-goodies/*/skills/estimate 2>/dev/null | sort -V | tail -1)" "$(ls -d ~/.omp/plugins/cache/plugins/*___omp-goodies___*/skills/estimate 2>/dev/null | sort -V | tail -1)" "$(ls -d ~/.codex/plugins/cache/*/codex-goodies/*/skills/estimate 2>/dev/null | sort -V | tail -1)" "$(ls -dt ~/.cursor/plugins/cache/*/cursor-goodies/*/skills/estimate 2>/dev/null | head -1)" "$(ls -dt ~/.cache/opencode/npm/*/*/node_modules/opencode-goodies/skills/estimate 2>/dev/null | head -1)"; do [ -n "$d" ] && [ -f "$d/estimate.py" ] && { BASE="$d"; break; }; done; [ -n "$BASE" ] && echo "$BASE" || { echo "ERROR: estimate not found in any known skills root — set ESTIMATE_HOME=<skill dir>" >&2; false; }
```

3. **`$SESSION_LOG_DIR`** is the session-log skill's directory (holds `session_log.py`). Its locator below works the same way (`SESSION_LOG_HOME=<skill dir>` wins) but assigns `BASE` too, so run it in a subshell: `SESSION_LOG_DIR="$( <the block below> )"`. On error do not stop: run without session data and list "no session data: session-log not found" in the missing report.

```bash
BASE=""; for d in "${SESSION_LOG_HOME:-}" .agents/skills/session-log .claude/skills/session-log .cursor/skills/session-log .opencode/skills/session-log .codex/skills/session-log ~/.agents/skills/session-log ~/.claude/skills/session-log ~/.cursor/skills/session-log ~/.config/opencode/skills/session-log ~/.omp/agent/skills/session-log ~/.codex/skills/session-log "$(ls -d ~/.claude/plugins/cache/*/claude-goodies/*/skills/session-log 2>/dev/null | sort -V | tail -1)" "$(ls -d ~/.omp/plugins/cache/plugins/*___omp-goodies___*/skills/session-log 2>/dev/null | sort -V | tail -1)" "$(ls -d ~/.codex/plugins/cache/*/codex-goodies/*/skills/session-log 2>/dev/null | sort -V | tail -1)" "$(ls -dt ~/.cursor/plugins/cache/*/cursor-goodies/*/skills/session-log 2>/dev/null | head -1)" "$(ls -dt ~/.cache/opencode/npm/*/*/node_modules/opencode-goodies/skills/session-log 2>/dev/null | head -1)"; do [ -n "$d" ] && [ -f "$d/session_log.py" ] && { BASE="$d"; break; }; done; [ -n "$BASE" ] && echo "$BASE" || { echo "ERROR: session-log not found in any known skills root — set SESSION_LOG_HOME=<skill dir>" >&2; false; }
```

4. **`$SESSION_DIR`** is the working directory your host reports for this session in its runtime/system context (the folder the session was started in), verbatim. `estimator` and `record-commit` read the newest session of that folder, which is the one running now; the project's own folder can hold a newer session of another run.

## Commands

```
python3 "$BASE/estimate.py" read [--references]
python3 "$BASE/estimate.py" save <entry-json | ->
python3 "$BASE/estimate.py" remove <file>
python3 "$BASE/estimate.py" find-sets
python3 "$BASE/estimate.py" doc-stats <file.md> [--plan-progress <path>]
python3 "$BASE/estimate.py" check-text <texts-json | ->
python3 "$BASE/estimate.py" git-stats <features-json | ->
python3 "$BASE/estimate.py" match-sessions <features-json | -> --session-log "$SESSION_LOG_DIR/session_log.py"
python3 "$BASE/estimate.py" usage <file.md> --session-log "$SESSION_LOG_DIR/session_log.py"
python3 "$BASE/estimate.py" record-commit <plan-file> <commit> --harness <harness> [--session-log "$SESSION_LOG_DIR/session_log.py" --session-dir "$SESSION_DIR"]
python3 "$BASE/estimate.py" estimator --harness <harness> [--session-log "$SESSION_LOG_DIR/session_log.py" --session-dir "$SESSION_DIR"]
python3 "$BASE/estimate.py" calculate <features-json | ->
python3 "$BASE/estimate.py" neighbours <request-json | ->
python3 "$BASE/estimate.py" place <request-json | ->
python3 "$BASE/estimate.py" anchor <hours>
python3 "$BASE/estimate.py" report [--out <path>]
```

Run from the user's repository, never from `$BASE`. Output is JSON on stdout; `-` reads the JSON argument from stdin. A failure is one `estimate: <message>` line on stderr, exit 1 or 2: report it as printed. `cannot read <file>` on a feature file usually means an unresolved merge conflict: tell the user to resolve it in git. Warnings are also `estimate:` lines on stderr (ignored frozen or set SP, replaced id, shared commits, duplicates): report each.

- **Duplicates.** A command that reads the feature files warns `duplicate name '<x>'`, `duplicate id '<x>'` or `duplicate doc <path>`, followed by `: <file>, <file>`, when two files share one. A shared name or id is a leftover copy: report both files and ask the user which to keep; `remove <file>` deletes the other. A doc listed by two features on purpose may stay. Under `/implement` only report, never ask or block.
- **Anchor.** `.harness-goodies/estimation.json` holds the scale and `anchor` (`{"sp": 13, "hours": 75}` by default; 13 SP is about one developer's two-week sprint). While on, a feature with planned hours is sized by them, with no judge (see "Find the size"). It is off when absent, null, `{}`, or with `hours` null or 0: then such a feature is compared like any other, its hours on its card. On a non-numeric scale the anchor is off and `neighbours`, `place` and `report` fail (`needs a numeric scale`): report that as printed. An invalid anchor makes every command that reads the feature files fail with `cannot read .harness-goodies/estimation.json`: tell the user to fix the file.
- **Estimator.** Run `estimator` once before the run's first save of an SP; send its `estimator` value unchanged with every SP the run saves, as `estimatedBy` with `storyPoints` and `updatedBy` with `updatedStoryPoints`. It prints `{"estimator": {"harness", "models"}, "missing"}`: the models of the last prompt of the harness's latest session in `--session-dir` (the project root without it), i.e. the prompt running now; `models` is null, reason in `missing`, without `--session-log` or a named model.

| Command | Input → output |
|---|---|
| `read` | → `{"version","scale","anchor","features"}` (defaults when nothing is saved), features in file order (first day, id, name), each with its `file` and `usage`. `--references`: only delivered features with an `updatedStoryPoints`, the only ones ever compared with. |
| `save` | Partial entry, fields `id, name, description, effort, status, storyPoints, updatedStoryPoints, estimatedAt, estimatedBy, updatedBy, plan {phases,tasks,estimatedHours}, git {commits,linesAdded,linesRemoved}, usage {matchedBy}, sources {docs,commits,sessions}` → the saved feature with its `file`. Rules below. |
| `remove <file>` | Deletes the feature file `read` shows as `file`. |
| `find-sets` | → `{"sets": [{"docs","planDoc","plan","delivered","missing"}], "missing"}`: the planning docs (`…brief.md`, `…team-plan.md`, `…plan.md`, `…tasks.md`) grouped per feature; `planDoc` holds the tasks; `plan` is `{"phases","tasks","completed","estimatedHours"}` or null; `delivered` is true when every task is ticked. |
| `doc-stats <file.md>` | → `{"phases","tasks","completed","estimatedHours","missing"}` (ticks via `/implement`'s `plan-progress.sh`; hours sum the `· <n>h` task estimates). Null counts with the reason in `missing`, or a failure: work the counts out by reading the document, say so, and report the `missing` lines. |
| `check-text` | `{"description","effort"}` (one or both) → `{"problems": [...]}`, `[]` when they pass. Rewrite until empty; `save` refuses the same problems. |
| `git-stats` | `{"features": [{"name","id","docs","taskIds","commits"}]}`, all features at once → per feature `{"name","commits","count","linesAdded","linesRemoved","candidates"}`. `commits` (optional): full hashes a check accepted, passed back as given (a short hash fails). `candidates` (`[{"hash","subject"}]`) are unclaimed commits naming the `id`; they count only once accepted and passed back as `commits`. |
| `match-sessions` | Same input as `git-stats`, once over all features; needs `--session-log` (without session-log, skip it and save no `usage` or `sessions`) → `{"features": [{"name","matchedBy","sessions","usage"}], "missing"}`. Save `sessions` as `sources.sessions` and `matchedBy` as `usage.matchedBy`; report `usage` (agent time, tokens, cost) and `matchedBy`. `missing`: failed calls, sessions without prompt rows, commits made outside any matched session. |
| `usage <file.md>` | The feature listing `<file.md>`; needs `--session-log` → `{"feature","matchedBy","sessions","usage","missing"}`, the values saved in that feature (`none` when nothing is stored): runs `match-sessions`' rule over every stored feature and merges that feature's `sources.sessions` and `usage.matchedBy` into the stored ones, never dropping a stored session (see "/estimate usage <file.md>"). |
| `record-commit` | Adds one task commit to the feature listing `<plan-file>` → `{"feature","needsResize","missing"}` (see "Record a commit"). |
| `calculate` | `{"features": [{"name","id","description","effort","tasks","estimatedHours"}]}` (cards) → `{"features": [{"name","hours","sp","missing"}], "pool": [...]}`: `sp` as `anchor` gives it, null with the reason in `missing`; `pool` is the features with an `sp`, to pass as `pool` below. |
| `neighbours` | `{"name", "pool"?}`; `pool` (items `{name,id,description,effort,sp,tasks,estimatedHours}`) replaces the references (the first `init` has none saved) → `{"picks","groups","missing"}`. `picks`, each `{name,id,description,effort,tasks,estimatedHours}`, are the features to compare with; give judges `picks` as printed, never SP, `groups` or `missing`. |
| `place` | `{"name","pool"?,"hours","verdicts","pickVerdicts"}`: `name`, `pool` as given to `neighbours`; `hours` planned hours or null; the two judge calls (see "Find the size"), each `{"<pick name>": "bigger"\|"same"\|"smaller"}` naming every pick once → `{"sp","tie","report","verdicts","missing"}`: `sp` the size (null with no pick); `tie` null or `{"between":[a,b],"by":"hours"\|"round up"}`; `report` e.g. `bigger than S (5), same as / smaller than M-1 (8)` ("X / Y": the calls disagree); `missing` is `neighbours`'. |
| `anchor <hours>` | → `{"hours","sp","missing"}`: the scale value nearest by ratio to those hours at the anchor (13 SP = 75 h: 30 h → 5, 60 h → 13); `sp` null with the reason in `missing` when the anchor is off. |
| `report` | Writes an offline HTML report from the references and their git commits to `--out` (default `.harness-goodies/estimation-report.html`) → `{"report","features","forecasts","missing"}`: path, references analysed, how many had an SP before work, listed commits git lacks. |

**`save` rules.**
- Target: the feature whose `sources.docs` shares a path with the entry's, else the one with the same `name`, else a new feature (`name`, `description`, `effort` required).
- `storyPoints`, `updatedStoryPoints` and pool `sp` must be values of `read`'s `scale`, your own estimate included. `save` refuses any field not listed above: never send `file`. `storyPoints` and `estimatedAt` freeze once the feature is `in-progress` or `delivered`; a non-null `updatedStoryPoints` never changes. Both warn when ignored. A null `plan` count never overwrites a known one; a missing or null `id` never erases the stored one (a different `id` replaces it with a warning).
- `estimatedBy` only with `storyPoints`, `updatedBy` only with `updatedStoryPoints`; each follows its size, a new size saved without one clears it.
- `effort` is required with a new feature and with any entry carrying `storyPoints` or `updatedStoryPoints`, and replaces the stored one, so it explains the SP saved with it; when the save ignores its SP the stored effort stays (the given one is stored only if the feature has none).
- `git` and `sources.commits` are saved together; `sources.docs` merge.
- `description` and `effort`: at most 300 characters, `effort` without digits (what they say: "Find the size"); `save` refuses otherwise; stored texts are never re-checked.

## Find the size

Every SP this skill saves (by `/estimate <file.md>`, `init` and the re-size after delivery) is found here. A feature with planned hours is **calculated** from them while the anchor is on (see "Commands"); every other feature is **compared** with delivered features of every size, so sizes stay consistent across runs and people. The script picks the features and turns verdicts into the size; two judges who see no SP give the verdicts. Size means total build effort: lines changed and commits are never evidence, and a comparison never changes a reference's SP. The calling step gives the feature's planned hours (none when it has none) and, in the first `init` only, a `pool`.

1. **Calculated.** With planned hours and the anchor on, run `anchor <estimatedHours>`: its `sp` is the size, no judge. Report "calculated from N h".
2. **Compared.** Otherwise run `neighbours` with `{"name": "<feature name>"}` (plus the step's `"pool"`). Start two fresh sub-agents as judges (inline, by you, only when the harness has no sub-agent tool); in `init` run at most 8 judges at once.
   - Each judge gets the feature as a card (`name`, `id`, `description`, `effort`, `tasks`, and `estimatedHours` only when the feature has hours and the anchor is off) and `neighbours`' `picks` exactly as printed: never an SP, `groups` or `missing`.
   - **Call 1** (first judge): the feature against each pick, picks in printed order. Returns `{"<pick name>": "bigger" | "same" | "smaller"}`, the feature against that pick.
   - **Call 2** (second judge, a different fresh sub-agent): each pick against the feature, picks in reverse order. Returns the same shape, that pick against the feature.

   Then run `place` with `{"name": "<feature name>", "hours": <planned hours or null>, "verdicts": <call 1>, "pickVerdicts": <call 2>}` (plus the same `"pool"`): its `sp` is the size.
3. **No pick and nothing calculated** (`neighbours` prints no `picks`): give your own estimate from the feature's documents and open the report with the warning of `/estimate <file.md>` step 1.
4. Report `place`'s `report` (e.g. "bigger than A6 (3), same as C4 (5)"), its `tie` when not null (e.g. "between 5 and 8, by hours"), and its `missing` lines as missing data. Verdicts are reported, never saved.

**`description` and `effort`** are written from the feature's documents, at most 300 characters each. `description`: what was built. `effort`: what makes the work big or small (parts changed, new storage or migrations, outside services, unknowns, risky areas), with no digits (number words are fine), because `neighbours` returns tasks and hours itself. Run `check-text` and rewrite until its `problems` is empty; texts already stored are never re-checked. Every document that can be sized gives an effort: a brief's problem, goal and scope name the drivers coarsely, and the plan and tasks file sharpen it as they sharpen the SP. The effort always explains the SP saved with it. Example from archon-search's A6 brief: "Two concurrency fixes: a lock around the indexing-state store's read-modify-write on every write path, including sync's reset, and a cache-invalidation call on the router. No new storage. Risk: proving no lost writes under concurrent syncs."

## `/estimate <file.md> [--from <source.md>]`

`--from` names the document a planning skill started from (the brief of a plan, the team plan of a task list). Both go into `sources.docs`, so the save updates that feature's entry instead of creating a second one.

**The estimate sharpens with each document.** A feature's documents add detail in order: brief, plan or team plan, tasks file. Each is estimated when written and each save replaces the feature's SP. The estimate saved last before the first task commit, normally the tasks file's, becomes the frozen original `storyPoints`, the one the accuracy check compares with the delivered size. Each document keeps the SP line it was written with (a brief can show 8 while its tasks file shows 13).

1. Run `read --references`. When it lists no feature, still estimate, but open the report with: **"Warning: no delivered features to compare with — this estimate is not based on past work. Run `/estimate init`."**
2. Read `<file.md>` and run `doc-stats <file.md>`. Note the feature's own ID when the document shows one: in its title, its front matter `id:` or its file name (e.g. `PM-12` of `PM-12-csv-import.md`), even when it looks like `TASK-001` or `FEAT-001`. The ID of an item inside the document (a task, scenario, contract or question: `BE-1`, `FE-2`, `T-3`, `K1`, `S1`, `C1`, `Q1`) is never the feature's ID; when unsure, note none.
3. Run `read` and find the feature that lists `<file.md>` or `<source.md>`; a new feature goes under the name step 6 will save. Run "Find the size" for it under that `name`, with the planned hours from step 2's `doc-stats` (none when it has none).
4. Expected agent time and cost: the `usage` (agent time, tokens, cost) of the references with the chosen SP (their range, naming them); none at that SP: list as missing.
5. Split suggestion: only on a numeric scale and only at 40 SP or more, suggest how to cut the work into smaller deliverable pieces.
6. Save to the feature step 3 found, reusing its `name`. When it already lists a more detailed document than `<file.md>` (a plan or tasks file for a brief, a tasks file for a plan), show the estimate but save only after the user confirms; otherwise save nothing and say the more detailed estimate stays. Run `estimator`, then save on stdin to `save -`: `{"id", "name", "description", "effort", "storyPoints", "estimatedAt": "<today YYYY-MM-DD>", "estimatedBy": <estimator>, "plan": {"phases", "tasks", "estimatedHours"}, "sources": {"docs": ["<file.md>", "<source.md>"]}}` (no `id` when step 2 noted none; no `<source.md>` without `--from`). Write `description` and `effort` from `<file.md>` (see "Find the size"). If `save` reports the SP as frozen, say the original SP stays and the new value is not saved.
7. Report: the SP; the verdicts and tie, or "calculated from N h" (see "Find the size"); phases, tasks and estimated hours when the file has them; the expected agent time, tokens and cost; the split suggestion; the feature's `file`; and every value that could not be found, `neighbours`', `place`'s and `anchor`'s `missing` lines included.

## `/estimate init`

Builds the references from the delivered features, one per planning-doc set as `find-sets` groups them. A set is delivered when `find-sets` gives it `delivered: true`; work without planning docs is skipped. "Sub-agent" steps run inline, one by one, when the harness has no sub-agent tool. Sub-agents never write estimate files.

1. Run `read`, then `find-sets`. Every `delivered: true` set is one feature: its `docs` and `plan` go to step 2; its `missing` and the top-level `missing` lines go to step 8.
2. **One sub-agent per feature**, given the set's `docs`. It reads them all together and returns `name`, `description` and `effort` (from the whole set, see "Find the size"), the feature's `id` (found as in `/estimate <file.md>` step 2, else null), `docs` (the set's; a brief, plan, team plan or tasks doc of the same feature that `find-sets` missed is added and named in its missing values), `taskIds` (every task ID of its plan or tasks file) and its missing values. It rewrites `description` and `effort` until `check-text` gives no `problems`. The feature's `plan` comes from `find-sets`: no `doc-stats`.
3. **The main agent alone** runs `git-stats` once over all features: each feature's doc commits and `candidates`. Shared names and commits are only told apart with every feature in view.
4. **One sub-agent per feature that has candidates.** It reads each candidate (`git show --stat --format=%B <hash>`) and keeps only commits that do this feature's work (code, tests or docs) judged against the feature's name and description. A commit that merely names the ID is rejected (e.g. `test(e2e): …` for a feature whose ID is `e2e`). It returns the accepted hashes.
5. **The main agent alone** runs `git-stats` again and `match-sessions` once, both with every feature's accepted hashes as `commits`. Take the results as they are: never drop, add or re-assign commits or sessions by hand, never fall back to dates.
6. **The main agent alone** sizes the features by "Find the size" (judges see no SP; each gets the feature's card from step 2).
   - **First init** (`read --references` lists none): run `calculate` once with every feature's card from steps 1–2. A feature with an `sp` is calculated (report "calculated from N h"); place each other feature with `calculate`'s `pool` as `pool`. When `calculate` gives no `sp` at all (anchor off, or no feature has planned hours), ask the user to name example features for a few sizes, at least one at 13 SP and one small; each keeps the size named, and the examples are the `pool`. This is the only place `init` waits for the user.
   - **Later inits**: every feature with an `updatedStoryPoints` keeps it; run `calculate` with the cards of the others: those with an `sp` are calculated, the rest placed against the stored references (no `pool`).

   Show one table: name, commits, lines +/−, tasks, hours, sessions, agent time, tokens, cost (from `match-sessions`' `usage`), `matchedBy`, SP (kept, calculated or placed), and `place`'s report or "calculated from N h". Then go straight on to step 7.
7. Run `estimator` once, then `save` each feature with: `id`, `name`, `description`, `effort` (step 2), `status: "delivered"`, `updatedStoryPoints`, `updatedBy` (the estimator), `plan` (from `find-sets`), `git` (`commits` = step 5's `git-stats` `count`, `linesAdded`, `linesRemoved`) and `sources.commits` (= its `commits` hashes; `git` and `sources.commits` always go together), `usage.matchedBy` and `sources.sessions` (from `match-sessions`), `sources.docs`. A feature whose stored `usage.matchedBy` is `implement` gets no `usage` and no `sessions`: its recorded sessions are never replaced. Never send `storyPoints`: an estimate made before work stays. A kept `updatedStoryPoints` keeps its stored effort; a feature saved before `effort` existed takes the one sent.
8. Report every value that could not be found (from `find-sets`, the sub-agents, `git-stats`, `match-sessions`, `calculate`, `neighbours`, `place`, `anchor`), e.g. "P2-03: no session found for 14 of 14 commits".

Running `init` again adds newly delivered features and refreshes the actuals of the others, also of estimated features finished outside `/implement`; it never changes a saved `updatedStoryPoints`.

## `/estimate usage <file.md>`

Attaches the sessions that worked on a feature to its stored estimate before any commit exists (a feature estimated from a brief or plan otherwise has `usage: null` until delivery). Use it when the user asks to update the estimate's usage or sessions. Needs `$SESSION_LOG_DIR`: without it say so and stop. No harness is needed: the matcher reads the sessions of all five.

1. Run `usage <file.md> --session-log "$SESSION_LOG_DIR/session_log.py"`. `<file.md>` must be listed in one feature's `sources.docs`; on `no feature lists <file.md>; run /estimate <file.md> first`, tell the user to estimate it first.
2. It matches the sessions against every stored feature (docs and recorded commits, so names and commits shared with another feature stay unattributed) and merges the result into that feature's `sources.sessions` and `usage.matchedBy`: a matched session is added, or replaces the stored one of the same id only when it counts every prompt the stored one counts. It never drops a stored session, so a failed `session-log` call, a session the harness rotated out or the sessions `/implement` recorded are never lost; when the stored `usage.matchedBy` is `implement` it saves nothing. It also saves nothing, and says so in `missing`, when no session matches. Running it again is safe. The script exits 1 without `session-log` (unlike `match-sessions`, which names the failed call in `missing`): `usage` has no other work, so nothing is saved and you stop as above.
3. Report the saved values it prints (`matchedBy`, `sessions` with their `usage`: agent time, tokens, cost): they describe the stored file, which may hold sessions this run did not match, and `matchedBy` is `none` when nothing is stored. Report every `missing` line too (the lines naming other features' commits come from the matcher's run over all features).

Limits: sessions are chosen by date: every session of the project active since the feature's first day (the day in its file name, less 14 hours for time zones) is read, so a doc written long before the feature was first estimated is missed. Task IDs are not stored with the feature, so only its docs and recorded commits count as evidence, never a task ID in a prompt.

**Never assign sessions by hand**: not by editing the feature file, not by `save` with sessions you picked. If `usage` finds none, report that.

## `/estimate report`

`report` here is the command word, never a file name. It needs only `$BASE` (no harness, no session-log).

1. Run `report`, with `--out <path>` when the user names one.
2. Report the page's path (it opens offline in any browser; offer to open it), how many features it analyses, how many forecasts it grades, and every `missing` value. With no forecasts, say the forecast-accuracy section fills once features estimated with `/estimate <file>` before work are delivered. On a "run /estimate init" failure, tell the user to run `/estimate init` first.

Features that are only estimated (no after-delivery SP) are shown apart, in their own color: in Pipeline, in All features and, at their estimated SP, on the Tasks, Estimated hours and Phases tabs of the Metric explorer. They feed no statistic. Never edit the page by hand: run `report` again after more features are delivered. Whether to commit it is the user's call.

## Record a commit

`/implement` runs this as the last action of the step that commits a task, for the task-breakdown file and the new commit:

```
python3 "$BASE/estimate.py" record-commit <plan-file> <commit> --harness <harness> --session-log "$SESSION_LOG_DIR/session_log.py" --session-dir "$SESSION_DIR"
```

Leave out `--session-log` and `--session-dir` when session-log was not found. It adds the commit's lines, refreshes the plan counts and the feature's slice of the current session, and sets the feature `in-progress`, or `delivered` once every task is ticked. It prints `{"feature", "needsResize", "missing"}`.

- **`needsResize: true`** (every task is ticked and no `updatedStoryPoints` exists yet, i.e. the feature was just delivered): re-size it by "Find the size" with its planned hours as delivered (`read`'s `plan.estimatedHours`): calculated from them when it has them and the anchor is on, else placed against the references (no `pool`). Then run `estimator` and `save` `{"name": "<feature name>", "updatedStoryPoints": <SP>, "updatedBy": <estimator>, "effort": "<effort>"}`, the effort written from the feature's documents as delivered. The original `storyPoints` stays; equal values mean the estimate was right. Report the verdicts, or "calculated from N h".
- Report the feature's status and every `missing` value. A failure (e.g. "no feature lists <plan>; run /estimate <plan> first") is reported and never blocks the task.
