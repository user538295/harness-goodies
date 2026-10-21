---
name: double-check
description: >-
  Config-driven, read-only review fan-out: runs every item listed in the project's `.double-check.json` — skills/commands, agents, MCP servers, CLI tools, Markdown rule files — on one target and merges all findings into one-line results. Targets: local changes (default), a commit, a commit range, files/folders/globs, or a described file set. `/double-check init` only creates the config (or prints its path when it exists). `/double-check status` only prints the configured items and their availability. Use when the user invokes /double-check, asks to double-check changes or files with all configured reviewers, or asks which configured reviewers are available.
---

# /double-check

`$ARGUMENTS` carries the target plus optional per-item parameters (`check src/ and run shellcheck with -S warning`). Read-only: never edit, stage, or commit reviewed files; the only project file this skill writes is `.double-check.json`, and the only other change it makes is a self-fix at the blocker gate (Step 2).

Severity rubric, shared by every item:

- **Critical**: blocks correctness, security, or safety
- **Major**: significant design flaw, missing requirement, or likely bug
- **Moderate**: suboptimal but workable
- **Minor**: style, naming, or nitpick

## `init`

When `$ARGUMENTS` is exactly `init`, resolve the project root (Step 0), then handle `.double-check.json` there and stop — no target, no validation, no review:

- **Missing** → create it with exactly the default config from Step 1 and print `Created <absolute path>`.
- **Exists** → leave it untouched and print `<absolute path>`.

## `status`

When `$ARGUMENTS` is exactly `status`, show the configured items and their availability, then stop — no target, no review:

1. Resolve the project root (Step 0); skip the target and the file list.
2. Run Step 1 unchanged.
3. Run Step 2 without a target: a `tool` is `✅` when `command -v <tool>` succeeds, and no item is checked against a target kind, so the reasons `installed, but there is no <file kind> in the target` and `not applicable: <why>` never appear.
4. Print the Step 2 block; an item with `args` adds a space and `(args: <args>)` after its name, before any `: <reason>` (`✅ 🛠 eslint (args: --max-warnings 0)`). Skip the blocker gate.

## Step 0 — Resolve the target

The **project root** is `git rev-parse --show-toplevel` inside a git repository, else the current working directory. A git repository is optional. Run every command from the project root.

Split `$ARGUMENTS` into the target and the per-item parameters. Map the target to exactly one kind, then to a **file list**: project-relative paths of files that exist on disk. In a git repository, run git as `git -c core.quotePath=false …` (keeps non-ASCII paths unescaped) and pipe its output through `while IFS= read -r f; do [ -f "$f" ] && printf '%s\n' "$f"; done` (drops paths deleted since).

Target kinds, with the file-list command in a git repository, then without git:

- **local** — empty or `local`. Git: `{ git diff --name-only --diff-filter=d HEAD; git ls-files --others --exclude-standard; } | sort -u`. Without git: there are no changes to diff; ask the user what to check.
- **commit** — one ref (`abc123`, `HEAD`). Git: `git diff-tree --root --no-commit-id --name-only -r --diff-filter=d <ref>`. Without git: `Nothing to check: not a git repository`.
- **range** — `A..B` or `A...B`. Git: `git diff --name-only --diff-filter=d <range>`. Without git: `Nothing to check: not a git repository`.
- **paths** — files, folders, globs (`*.h tests/`). Git: `git ls-files --cached --others --exclude-standard -- '<spec>'…` (quote globs; a git pathspec `*.h` matches at any depth). Without git: `find <dir> -type f -not -path '*/.*' | sed 's|^\./||'`; a glob → `find . -type f -name '<glob>' -not -path '*/.*' | sed 's|^\./||'`.
- **described** — prose (`all files except tests/`). Git: translate to pathspecs, e.g. `git ls-files --cached --others --exclude-standard -- . ':(exclude)tests/'`. Without git: translate to `find`, e.g. `find . -type f -not -path '*/.*' -not -path './tests/*' | sed 's|^\./||'`.

Ambiguous prose → ask the user which files are meant before continuing. Empty file list → print `Nothing to check: <reason>` and stop. Otherwise write the list to a temp file (`FILES=$(mktemp)`) and print one line: `Target: <kind> <spec> — <N> files`. Paths in every later step and in the summary are project-relative.

## Step 1 — Load `.double-check.json`

The config lives at the project root. It is a JSON array; each entry is an object with exactly one **type key** naming the item, plus an optional `args` string:

| Type key | Category | Value | Example |
| --- | --- | --- | --- |
| `skill` | skills, commands, plugins | invocation name | `{ "skill": "/brooks-review" }` |
| `agent` | agents | agent type | `{ "agent": "security-reviewer" }` |
| `mcp` | MCP servers | server name | `{ "mcp": "playwright" }` |
| `tool` | CLI tools and commands | executable name | `{ "tool": "eslint", "args": "--max-warnings 0" }` |
| `md` | Markdown files holding rules and guidelines | path from the project root | `{ "md": "conventions.md" }` |

**Item parameters** come from two sources and both apply: the entry's `args`, and the per-item parameters from `$ARGUMENTS`. For one run, the invocation parameters win where the two conflict; the file stays unchanged.

The default config:

```json
[
  { "skill": "/da-review" },
  { "skill": "/clean-code-review" },
  { "skill": "/brooks-review" },
  { "skill": "/open-code-review" },
  { "tool": "markdownlint" },
  { "tool": "shellcheck" },
  { "md": "conventions.md" }
]
```

- **Missing file** → create it with exactly the default above, tell the user in one line, continue.
- **Present, with problems** → list every problem in one line each with your suggested fix, then wait for the user's answer, apply it to the file, and continue with the updated config. Problems:
  - a default item missing from the config;
  - a **broken** file or entry: not JSON, not an array, an entry that is not an object, zero or several type keys, an `args` that is not a string;
  - an **unknown** key: anything other than the five type keys and `args`.

## Step 2 — Check availability

Check every config item in config order. For a `skill` item, read its definition (SKILL.md or command file) and find the agents, skills, and commands it needs; list each **dependency** on its own line directly above the item, even when it is absent from the config. MCP servers and CLI tools are never checked as dependencies.

An item is `✅` when it exists and can review this target:

- **agent / skill / dependency**: listed in your harness's agents, skills, or commands, under any plugin prefix (`claude-goodies:devils-advocate`, `brooks-lint:brooks-review`).
- **mcp**: the server is connected and its tools are available to you.
- **tool**: `command -v <tool>` succeeds, and the file list holds files the tool checks (decide from its name, `--help`, and project config; shellcheck also covers extensionless files with a `sh`/`bash` shebang).
- **md**: the file exists.
- Every item: its documentation allows this target kind (e.g. a reviewer of git diffs cannot take a described file set or a project without git).
- Every item: the runtime prerequisites its documentation names hold — credentials, a configured LLM provider, a login, a running service; a skill's prerequisites include those of the CLIs it wraps. Verify with the check the documentation gives, never with an interactive setup command (`/open-code-review` → `ocr llm test` exits 0). No documented check → treat as ready.

`❌` reasons: `not found` · `not installed` · `not connected` · `not ready: <what is missing>` · `installed, but there is no <file kind> in the target` · `not applicable: <why>` · `missing dependency <name>`. A missing dependency the item can work without (its documented fallback) leaves the item `✅` and adds `, <item> falls back to <fallback>` to the dependency's line.

Before any blocker fix or reviewer launch, send the availability block below as a standalone, user-visible, non-final assistant message (in omp, use `commentary`), with no tool call in the message. Do not substitute tool output or defer it to the final summary. After sending it, continue in the same run; wait for the user only when the blocker gate requires an answer. Preserve this format exactly — header, three-space indent, mark, category emoji, name; a `❌` line adds `: <reason>`:

```text
Check availability:
   ✅ 🧠 /devils-advocate
   ✅ 💡 /da-review
   ❌ 💡 /brooks-review: not found
   ✅ 🔌 playwright
   ❌ 🛠 shellcheck: installed, but there is no shell script in the target
   ❌ #️⃣ conventions.md: not found
```

Category emojis: agent 🧠 · skill 💡 · MCP 🔌 · tool 🛠 · md #️⃣. Agents and skills carry a leading `/`; MCP servers, tools, and md files do not. Every later step uses only the `✅` items (dependencies run inside their item, never on their own); no `✅` item after the blocker gate → skip.

**Blocker gate** — run before Step 3. A `❌` item or dependency with reason `not found`, `not installed`, `not connected`, `not ready`, or `missing dependency` is a **blocker**; the target-based reasons are not (those items have nothing to check). No blocker → Step 3. Otherwise take each blocker's fix from the item's documentation:

1. **Self-fixable** — a non-interactive command that needs no secret and no choice from the user (installing a documented CLI): run it without asking, recheck the item as in Step 2, and print one line `🔧 <emoji> <name>: <fix command>`. Fixed → the item is `✅`. Still `❌` → it becomes a user blocker with the new reason.
2. **User blockers** — interactive setup, credentials, logins, or a self-fix that failed. None → Step 3 without stopping. Otherwise print one line per user blocker with its fix, then wait for the user's answer; never ask for a secret in chat:

```text
Blocked — run the fixes, then answer `retry`, `continue` (review without them), or `stop`:
   ❌ 💡 /open-code-review: not ready: no LLM provider configured (ocr llm test failed) → run: ocr config provider
```

- `retry` → rerun Step 2 and this gate.
- `continue` → Step 3 with the `✅` items only; every user blocker goes to the `Not run` block with its Step 2 reason.
- `stop` or anything else → stop without reviewing.

## Step 3 — Review

**Every item runs in a subagent; you only orchestrate.** Two reasons: speed (subagents run in parallel) and your own context window — raw tool output, diffs, file contents, and reviewer reports stay inside the subagents, and you receive nothing but finding lines. You design the split: weigh per item how slow it is (LLM-backed CLIs, large file lists), whether it fans out on its own (an orchestrating skill such as `/clean-code-review` needs a subagent that can invoke skills and spawn subagents, e.g. Claude Code `general-purpose`), and how much output it produces. Give slow or heavy items their own subagent; batch quick, deterministic tools into one shared subagent; split one item across several subagents by file chunks when the target is large. Keep the running subagents, including the ones items spawn internally, under your harness's concurrent-subagent limit. Launch independent subagents in one parallel batch.

Every subagent gets: its items with their merged parameters, the target kind and spec, the `$FILES` path, the severity rubric, and the **finding line** format from Step 4. Every subagent is read-only (no edits, no commits, no auto-fix modes), regrades every finding onto the rubric, and returns only finding lines in the Step 4 format — no prose, no raw reports; zero findings → `NONE`; a failed item → one line in the final `Not run` form: `❌ <emoji> <name>: <reason>`.

How each type runs:

- **skill**: invoke it with the target in the form its documentation accepts (local changes, a ref range such as `<ref>^..<ref>` for a commit, the diff, or the file list) plus its parameters.
- **agent**: the item is itself the subagent — spawn that agent type directly on the target with its parameters and the return contract above.
- **mcp**: use the server's tools on the target the way the server is built for (e.g. playwright drives the UI the changed files render).
- **tool**: run it on its matching files with its parameters and the project's own tool config; map its native severities onto the rubric (e.g. shellcheck `error` → Major, `warning` → Moderate, `info`/`style` → Minor).
- **md**: check the target against every rule in the file; the finding name is the rule's heading or first words.

File-level items on a commit/range target read the worktree copy; when `git diff --quiet <end-ref> -- <file>` fails, add `(worktree copy)` to that file's explanations. No subagent tool in the harness → run every item yourself, one after another, keep only its finding lines, and add one line to the summary: `Review ran inline, not independent.` An item that fails at runtime (crash, missing credentials, timeout) is not retried blindly: its `❌` line goes to the `Not run` block unchanged.

## Step 4 — Summary

**Finding line** — one finding per line, at most 255 characters:

```text
emoji [item] · Severity · Short name/category | relative/path/to/file.ext:N | Short explanation of the finding and a short suggestion for the fix.
```

Example:

```text
💡 [clean-code-review] · Moderate · [smells-01] File Length | tests/test_config.py:1 | Split tests/test_config.py (1961 lines): move the section-specific suites, such as [namespaces] and [observability], into their own test_config_<section>.py files.
```

- emoji is the emoji of the item (🛠 for tools). A line merging several items carries every item's emoji, one per item in the same order as the names: `💡🛠 [da-review, shellcheck]`.
- `[item]` is the config name without `/` (`[da-review]`, `[shellcheck]`, `[playwright]`, `[conventions.md]`). The same root cause at the same location from several items → one line, items comma-joined: `[da-review, brooks-review]`.
- Severity is one of the four rubric words. No line number → `path`; no file at all → `-`.
- Sort by severity (Critical first), then path, then line.
- Before printing, write the lines to a temp file and run `awk 'length > 255' <file>`; shorten every line it prints (bytes overcount multibyte characters, so passing is safe).

Print the sorted lines (no findings → `No findings.`), then, when any item failed at runtime or was skipped at the blocker gate:

```text
Not run:
   ❌ <emoji> <name>: <reason>
```
