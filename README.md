# Harness Goodies

**An opinionated, human-in-the-loop workflow for [Claude Code](https://claude.ai/code).** Turns a rough feature idea into shipped, reviewed, committed code — without you babysitting every step, and without letting Claude ship blind.

Skills and one adversarial review agent. Every piece explained with a worked example in the [interactive handout](https://user538295.github.io/harness-goodies/handout/) (English · [Magyar](https://user538295.github.io/harness-goodies/handout/index-hu.html)).

![Harness Goodies demo](assets/demo.gif)

> `/feature-refinement` → `/plan-maker` → `/implement` — idea to commit in one session. `/implement all <file>` runs the full plan unattended.

---

## The shape of it

```mermaid
flowchart LR
    A((Idea)) --> B[Brief] --> G1([You ✓]):::gate --> C[Plan] --> G2([You ✓]):::gate --> D[Code] --> G3([You ✓]):::gate --> E((Ship))
    classDef gate fill:#f59e0b,color:#000,stroke:#d97706
```

Three human gates, everything else automated (simplified default view; see the full handout for the 4-gate detailed pipeline). The full 9-step pipeline lives in the handout: [agentic-workflow-en.html](https://user538295.github.io/harness-goodies/handout/agentic-workflow-en.html) · [agentic-workflow-hu.html](https://user538295.github.io/harness-goodies/handout/agentic-workflow-hu.html).

---

## What it gives you

Each entry links to its handout page with a worked example.

### Ship a feature, start to finish

- [**`/feature-refinement`**](https://user538295.github.io/harness-goodies/handout/skill-feature-refinement.html) — Turn a rough idea into a brief you can hand off. A senior product thinker walks you through the questions you'd otherwise skip.
- [**`/plan-maker`**](https://user538295.github.io/harness-goodies/handout/skill-plan-maker.html) — Stop staring at a ticket wondering where to start. Breaks the brief into the smallest tasks with tests and dependencies.
- [**`/implement`**](https://user538295.github.io/harness-goodies/handout/cmd-implement.html) — One command for the whole plan, four ways to call it. `/implement next <file>` builds just the next task test-first, reviews itself, and commits — one task, one commit — then stops. `/implement all <file>` loops through every remaining task: it spawns a subagent per task whenever the harness exposes a subagent tool — Claude Code (including `claude -p`), OpenCode, omp, Cursor — and falls back to an inline loop only where no such tool exists. `/implement all inline <file>` forces that inline loop. Bare `/implement <file>` does the task if only one remains, otherwise asks next-or-all (headless defaults to next).
- [**`/quick-plan`**](https://user538295.github.io/harness-goodies/handout/skill-quick-plan.html) — No plan yet and too busy for `/plan-maker`. Defines the goal, success criteria, and 4–12 steps inline — one pass, no file written.
- [**`/commit`**](https://user538295.github.io/harness-goodies/handout/skill-commit.html) — Commit time. Reads the staged diff, writes a Conventional Commits message with a why-first body, and commits. Use `/commit message` to draft the text without touching the repo.
- [**`/wrap-up`**](https://user538295.github.io/harness-goodies/handout/skill-wrap-up.html) — Done for the day, not sure anything slipped. Audits commit hygiene, runs tests and devil's advocate, surfaces what's open — mutates nothing until you say yes.

See `skills/implement/SKILL.md` § NEXT mode, "Step 6: Commit" for the one-task-one-commit rule.

### Fix a bug

- [**`/bugfix`**](https://user538295.github.io/harness-goodies/handout/skill-bugfix.html) — You have a bug and need it gone — not just patched. Drives a four-agent pipeline: failing test first, TDD fix, doc update, full review loop, commit.

### Get a second opinion

- [**`/da-review`**](https://user538295.github.io/harness-goodies/handout/cmd-da-review.html) — A second opinion that actually pushes back. One-pass devil's-advocate review, no auto-fixes.
- [**`/iterative-review`**](https://user538295.github.io/harness-goodies/handout/cmd-iterative-review.html) — A review that doesn't stop at finding problems. Reviewers and fix agents loop until clean.
- [**`/aaa`**](https://user538295.github.io/harness-goodies/handout/skill-aaa.html) — When "looks good to me" isn't enough. Benchmarks an idea against world-class and hands you 3–4 concrete upgrade paths.
- [**`/clean-code-review`**](https://user538295.github.io/harness-goodies/handout/skill-clean-code-review.html) — Code done, want the deep read. Runs 132 checks across 7 groups (clarity, smells, SOLID, architecture, tests, safety, DDD) — on local changes, staged/unstaged/untracked, a git range, a pull/merge-request link, or specific files.
- **`/double-check`** — Want every reviewer on one target in one go. Runs every item listed in `.double-check.json` — skills/commands, agents, MCP servers, CLI tools (with optional `args`), Markdown rule files — on local changes, a commit, a range, files/globs, or a described file set, then merges the findings into one line each. Default config: `/da-review`, `/clean-code-review`, `/brooks-review`, `/open-code-review`, markdownlint, shellcheck, `conventions.md`. Before reviewing, it checks that every item is installed and ready (e.g. `/open-code-review` has an LLM provider configured) and fixes what it can by itself, such as installing a missing CLI; it stops only for fixes you must run (credentials, interactive setup) until you answer `retry`, `continue` without them, or `stop`. `/double-check init` creates the default config, or prints its path when it already exists. `/double-check status` prints every configured item with its `args` and whether it is available, without reviewing anything. Read-only.
- [**`/options`**](https://user538295.github.io/harness-goodies/handout/skill-options.html) — Stuck between approaches. Produces 2–4 genuinely different paths with honest pros/cons, grounded in your actual project files, and a firm recommendation.

Powered by the [`devils-advocate`](https://user538295.github.io/harness-goodies/handout/agentic-workflow-en.html#da) agent — the thing actually doing the attacking. Auto-invoked by both review skills and inside `/implement`.

### Make Claude remember

- [**`/llm-wiki`**](https://user538295.github.io/harness-goodies/handout/skill-llm-wiki.html) — You've done the research, but Claude keeps forgetting it. Captures notes, sources, decisions; future chats search it first → sharper answers, fewer tokens.

### Wrangle docs and skills

- [**`/documentation-standard`**](https://user538295.github.io/harness-goodies/handout/skill-documentation-standard.html) — Docs your team will actually find again. Enforces structure across architecture notes, ADRs, manuals, and dev guides.
- [**`/skill-packager`**](https://user538295.github.io/harness-goodies/handout/skill-skill-packager.html) — Built a Claude Code skill? Make it work in Claude Desktop too. Packages your folder into an upload-ready ZIP.
- [**`/doc-voice`**](https://user538295.github.io/harness-goodies/handout/skill-doc-voice.html) — Docs that read like marketing copy or dry internal prose. Applies a problem-first, proof-led voice to READMEs, handouts, and guides — without touching structure.
- [**`/md-reviewer`**](https://user538295.github.io/harness-goodies/handout/skill-md-reviewer.html) — Terms shift, cross-references rot, contradictions accumulate. Reviews Markdown for consistency, contradictions, and stale links — handles sets of 50+ files.
- [**`/plain-language`**](https://user538295.github.io/harness-goodies/handout/skill-plain-language.html) — Need to explain a technical decision to a non-technical stakeholder. Rewrites it consequence-first, technical detail in parentheses — precise but followable without knowing the codebase.

### Monitor background tasks

- [**`/status-report`**](https://user538295.github.io/harness-goodies/handout/skill-status-report.html) — Kicked off a long task and don't know when it'll finish. Reports status on demand or on a recurring schedule — cancel anytime with `off`.
- [**`/session-log`**](https://user538295.github.io/harness-goodies/handout/skill-session-log.html) — Archive each session's prompts and responses as a per-project Markdown log with a saved token and price report. `on`, `off`, `status` and `usage` run in Claude Code, Codex, Cursor and OMP on one bundled Python script (Python 3.14 or newer). OpenCode: see [its section](#opencode-plugin).

Each harness plugin ships the one package; nothing else is installed. Logging is opt-in: it stays off until you run `on`, and no Markdown log is written until a sync. Logs go to the harness's log root, `<harness root>/prompt-logs` (for example `~/.claude/prompt-logs`). While logging is on, `on`, `status` and `usage` first sync: they read the harness's session data and, for every session with activity since the on-time, write one Markdown log and a usage report beside it (`<log root>/<project folder>/session_<UTC time>_<id>.md` and `.usage.txt`; times inside carry a UTC offset), rewritten whole when the session changes. Sub-agents count in the report totals, except in Cursor, where a report covers the main session only and its price is marked incomplete. `off` syncs once and turns logging off; while it is off no command syncs. Hooks only start a sync or record events: Claude Code's plugin hook syncs at session start; Cursor's plugin hooks record each prompt and response (Cursor has no readable session store, so only prompts recorded while logging was on are archived) and sync at session start; Codex and OMP have no hooks, so their sessions are archived by the next `on`, `status` or `usage`. `usage` prints the saved report of an archived session (`usage`, `usage --latest` or `usage <session-id>`); for a session that was never archived (for example one with no activity since `on`) it computes the same report live from the harness's data and saves nothing. Each harness's invocation form is in [Install · Update](#install--update).

One script bundle handles the progress plumbing — [`scripts-plan`](https://user538295.github.io/harness-goodies/handout/scripts-plan.html) prints the next-task progress header — `/implement` reads it once in NEXT mode, and on every iteration in ALL mode. The [session-log handout](https://user538295.github.io/harness-goodies/handout/skill-session-log.html) has the details.

---

## Install · Update

### Claude Code plugin marketplace

```bash
claude plugin marketplace add user538295/harness-goodies
claude plugin install claude-goodies
```

The marketplace plugin bundles the skills, the `session_log.py` script and one hook, `hooks/hooks.json`: a SessionStart hook that runs `python3` on the script and, while logging is on, syncs the Claude Code store. The hook does nothing while logging is off and exits 0 when the script is missing. Invoke the skill as `/session-log <args>` or `/claude-goodies:session-log <args>` (both ran the script in `claude -p`; `<args>` is `on`, `off`, `status`, `usage`, `usage --latest` or `usage <session-id>`). Restart Claude Code (or start a new session) for plugin changes to load. To update later:

```bash
claude plugin update claude-goodies@user538295
```

### omp plugin marketplace

```bash
omp plugin marketplace add user538295/harness-goodies
omp plugin install omp-goodies@user538295
```

Inside omp the same steps are `/marketplace add user538295/harness-goodies` and `/marketplace install omp-goodies@user538295`. omp reads its own catalog (`.omp-plugin/marketplace.json`) and installs `omp-goodies`: every skill (including `/skill:da-review` and `/skill:iterative-review`) and the `devils-advocate` agent with omp tool names. Run `/reload-plugins` or start a new session to load them. To update later:

```bash
omp plugin marketplace update user538295
omp plugin upgrade omp-goodies@user538295
```

The plugin registers no extension, command or hook in omp: session-log is the skill alone, and omp sessions are archived by the next `on`, `status` or `usage`. Invoke it as `/skill:omp-goodies/session-log <args>`. The plain `/skill:session-log <args>` ran the older release-1 copy from the Claude plugin cache when Claude Code's plugin registry listed `claude-goodies`, and the new copy otherwise (observed on omp 18.12.6; both skills are named `session-log`), so use the namespaced form.

Each command passes its harness identity explicitly (`claude`, `codex`, `cursor`, `opencode`, `omp`); the script never infers it from directories or environment variables. The script exits 0 on success, 1 on a runtime error and 2 on bad arguments, and prints failures on stderr as `session-log:` messages.

### Cursor plugin

Cursor reads its own manifests (`.cursor-plugin/plugin.json` and `.cursor-plugin/marketplace.json`) and installs `cursor-goodies`: every skill, the `devils-advocate` agent, and the session-log hooks in `.cursor-plugin/hooks.json` (Cursor's own format): `sessionStart`, `beforeSubmitPrompt`, `afterAgentResponse`, `subagentStop` and `stop`, each running the plugin's `skills/session-log/hook` launcher, a POSIX shell script that exits 0 without starting Python while logging is off. While logging is on, the four non-start hooks write one record per event under `<log root>/.records/`, and the session-start hook syncs. `status` prints `Cursor has recorded no hook events since logging was turned on.` when no such record exists.

Cursor may also run the Claude Code `hooks/hooks.json` of an imported claude-goodies plugin; if it does, its session-start hook syncs the Claude Code store (`~/.claude`) when logging is on in Claude Code. Not yet verified: whether the Cursor app runs that imported hook, how long a chat start waits for it, whether the `hook` file stays executable after a marketplace install, and the install-cache layout of a marketplace install. The skill form `/session-log <args>` ran the script in `cursor-agent -p`, which fires no prompt hooks and so records nothing; the form in the Cursor app has not been observed. To test a local checkout, `cursor-agent -p --plugin-dir <checkout>` loads the plugin. On native Windows Cursor is **not yet tested**.

Install the plugin through one of two marketplace routes:

**Cursor Marketplace.** Once Cursor has reviewed and listed the plugin, open **Customize** in the sidebar, search for `cursor-goodies`, select **Install**, and choose a project or user scope. Cursor reviews every update before publishing it.

**Team marketplace** (Teams and Enterprise plans; on Enterprise only admins can add one):

1. Open **Dashboard → Plugins & MCPs**.
2. In **Team Marketplaces**, click **Add Marketplace**, choose **Import from Repo**, and paste `https://github.com/user538295/harness-goodies`.
3. Add `cursor-goodies` with **Add to Marketplace**.
4. Under **Marketplace Settings**, set **Marketplace Access** and optionally **Enable Auto Refresh** (needs the Cursor GitHub App on the repository), then save.
5. Each developer installs `cursor-goodies` from **Customize**, unless an admin set it to Default On or Required.

To update later, Auto Refresh re-indexes the marketplace after each push; otherwise click **Refresh** on the marketplace.

### Codex plugin

```bash
codex plugin marketplace add user538295/harness-goodies
codex plugin add codex-goodies@user538295
```

Codex reads its own manifests (`.agents/plugins/marketplace.json` and `.codex-plugin/plugin.json`) and installs `codex-goodies`: every skill, namespaced as `codex-goodies:<skill>`. Codex plugins carry no agents, so `devils-advocate` is not included; `da-review` and `iterative-review` fall back to generic subagents that read the persona from the plugin's `agents/devils-advocate.md`. The plugin ships no hooks (`.codex-plugin/hooks.json` is empty) and `on` writes no `~/.codex/hooks.json` entry; Codex sessions are archived by the next `on`, `status` or `usage`. Start a new session to load it.

Invoke the skill in Codex as `$session-log <args>`. A typed `/session-log` is plain text in `codex exec`, not a command, and the workspace-write sandbox refused writes to `~/.codex/prompt-logs` until approved, so approve that write for `on`.

To update later:

```bash
codex plugin marketplace upgrade user538295
```

Codex leftovers of older releases (`~/.codex/hooks.json` entries and a seeded `~/.codex/skills/session-log` copy) are reported by `on` and `status`, not removed: see [Upgrading from an older release](#upgrading-from-an-older-release).

Installed `claude-goodies@user538295` in Codex before this plugin existed? Switch with `codex plugin remove claude-goodies@user538295 && codex plugin add codex-goodies@user538295`.

### OpenCode plugin

```bash
opencode plugin add github:user538295/harness-goodies
```

Requires OpenCode V2. OpenCode installs the `opencode-goodies` package (`package.json` plus `.opencode-plugin/index.js`) into its package cache, adds it to `~/.config/opencode/opencode.json`, and loads it without a restart. The plugin registers every skill and the `devils-advocate` subagent. It registers no `/session-log` command and no logging handlers. To test a local checkout, `opencode plugin add git+file://<checkout>` works (a plain path is refused). The model reaches the skill through OpenCode's `skill` tool when asked in prose (observed with `opencode run`), but the shipped `skills/session-log/SKILL.md` still says session-log does not log in OpenCode until the next release and tells the model to stop; the model refused (observed), so do not run it there through the skill: it is **not usable through the skill in OpenCode**. The script run directly printed a correct report once: `python3 <skill dir>/session_log.py opencode usage`, where `<skill dir>` is the installed `skills/session-log` folder. To update later:

```bash
opencode plugin update
```

Skills and agents with the same names under `~/.config/opencode/skills` and `~/.config/opencode/agents` take precedence over the plugin's copies; remove those copies to use the plugin's versions. An old seeded `~/.config/opencode/skills/session-log` copy may be loaded in place of the plugin's skill: move it aside (see [Upgrading from an older release](#upgrading-from-an-older-release)).

### Upgrading from an older release

Each harness plugin above ships the script, so there is nothing to install separately. Releases before this one copied the package into the harness's skill root (for example `~/.claude/skills/session-log`) and registered hooks in the harness's own files, and the harness plugin of a newer release does not remove them. `on` and `status` list what they find for the harness they run for under `Old-release leftovers (not deleted):`, each with its fix, and delete, move and rewrite nothing: move an old seeded `skills/session-log` copy aside, and remove its old session-log hook entries from `~/.claude/settings.json`, `~/.codex/hooks.json` or `~/.cursor/hooks.json`. Also listed: old `~/.claude/scripts/prompt_log_*` files, old OpenCode plugin, script and command files, old OMP extension files and `skills/session-log-omp`, the old `<harness root>/session-log/` state folder and `~/.claude/session-maps/`. Until a seeded copy is moved aside it may be loaded in place of the plugin's skill. Old-format logs stay beside the new ones; a few sessions that span the upgrade can appear twice (old-format log plus new log).

### Running the script directly

From a checkout of this repository, run the script directly (the identity is `claude`, `codex`, `cursor`, `opencode` or `omp`):

```bash
python3 skills/session-log/session_log.py claude status
```

The script recognizes exactly those five identities. The `SKILL.md` requires the host to choose one explicitly. The shipped `SKILL.md` still refuses to run in OpenCode (see the OpenCode section).

Only default roots are supported: Claude Code `~/.claude`, Codex `~/.codex`, Cursor `~/.cursor`, OpenCode `~/.config/opencode`, and OMP `~/.omp/agent`; a relocated config or data root (such as `CLAUDE_CONFIG_DIR` or `CODEX_HOME`) fails the command. Logging stays off until enabled separately in each harness, and logs go to `<root>/prompt-logs`.

Prices come from `skills/session-log/prices.json`; a problem with that file is shown by `status` as `Price file problem:`.

Run the unit tests with `python3 -m unittest discover -s skills/session-log/tests -t skills/session-log`.

---

## Why this repo has opinions

Unconstrained AI coding produces verbose, coupled code that accumulates fast and is hard to reverse. Constraints aren't slow — they're the thing that makes the output trustworthy enough to ship.

The repo is developed under the guidelines in its own [`AGENTS.md`](AGENTS.md), which Claude Code, Codex, OpenCode, and omp load at session start. The plugins don't install it — copy it into your own `AGENTS.md` or `~/.claude/CLAUDE.md` if you want the same rules. It encodes three principles:

1. **Think before coding.** Verify and cite every claim, state assumptions, surface tradeoffs, push back when warranted.
2. **Simplicity first.** Minimum code that solves the problem; nothing speculative.
3. **Surgical changes.** Touch only what the task requires, and fix any concrete defect you find along the way.

All three are adapted from [Andrej Karpathy's guidelines](https://github.com/multica-ai/andrej-karpathy-skills/blob/main/skills/karpathy-guidelines/SKILL.md).

And enforces: tests before code (85%+ coverage), warning-free codebase at all times. `skills/implement/SKILL.md` adds the execution-level rule: one commit per plan task, no batching multiple tasks into a single commit.

If that's not your speed, this repo isn't for you. If it is — install in 30 seconds.

## Requirements

- Universal session-log: Claude Code, Codex, Cursor, or OMP. OpenCode: see its section.
- macOS or Linux, and Python 3.14 or newer as `python3`; with an older `python3` commands fail with a message and hooks do nothing. The script uses only the Python standard library: no bash, jq, sqlite3 command, Bun, Node or `zstd` is needed to run it (bash is needed only for the skill's optional locator block, and Cursor's `hook` launcher is a POSIX shell script).
- Native Windows is **not yet tested** for Claude Code, Codex, Cursor, OMP and OpenCode alike; the maintainer runs `skills/session-log/tests/windows-checklist.md` on a real Windows PC after the release, and this line changes with its result.

No MCP servers are required.

---

Full reference, with worked examples for every skill, agent, and script — open the handout:
**[English](https://user538295.github.io/harness-goodies/handout/) · [Magyar](https://user538295.github.io/harness-goodies/handout/index-hu.html)**.
