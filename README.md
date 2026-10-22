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
- [**`/session-log`**](https://user538295.github.io/harness-goodies/handout/skill-session-log.html) — One stable package for Claude Code, Codex, Cursor, and OMP; it does not log in OpenCode until the next release. The host selects its identity explicitly; the package never guesses from paths, processes, or environment variables. Logging and usage stay native to each harness.

Each harness plugin ships the package; nothing else is installed. In Claude Code and Cursor the plugin's hooks stay idle until `/session-log on`, which takes effect with the next prompt (`on — logging starts with the next prompt`; Cursor says `on — logging starts with the next prompt once the cursor-goodies hooks run; run status after it to confirm`, because nothing proves its plugin hooks are loaded until one runs); when logging was already on but no hook of the running session has seen it, `on` reports `on — restart required` in Claude Code and `on — no hook of this cursor-goodies version has run`, naming the plugin install or update, the Cursor setting that loads plugin hooks, and a restart, in Cursor. In OMP the plugin's extension logs nothing until `/session-log on` and reads that flag at every prompt; `on` reports `on — restart required` until a restarted omp has loaded it. In the other harnesses, restart after `/session-log on` when it reports `on — restart required`; Codex instead reports `on — approve the session-log hooks in /hooks, or restart Codex and choose 'Trust all and continue' if it shows the 'Hooks need review' screen`, because Codex runs new `hooks.json` commands only once you trust them; `status` repeats it until a Codex session has run them. OpenCode is not covered: session-log does not log there until the next release.

One script bundle handles the progress plumbing — [`scripts-plan`](https://user538295.github.io/harness-goodies/handout/scripts-plan.html) prints the next-task progress header — `/implement` reads it once in NEXT mode, and on every iteration in ALL mode. The [`session-log`](https://user538295.github.io/harness-goodies/handout/skill-session-log.html) skill archives the prompt and response text exposed by each harness as per-project Markdown; the universal package owns activation and migration for Claude Code, Codex, Cursor, and OMP; OpenCode logs nothing until the next release. See the [session-log handout](https://user538295.github.io/harness-goodies/handout/skill-session-log.html) for details.

---

## Install · Update

### Claude Code plugin marketplace

```bash
claude plugin marketplace add user538295/harness-goodies
claude plugin install claude-goodies
```

The marketplace plugin bundles the Claude skill, runtime, and native hooks. Its `hooks/hooks.json` always registers the session-log hooks, which do nothing until `/session-log on` creates the enable flag: `on` and `off` register nothing (`on` creates that flag after the legacy cleanup, `off` removes it and the runtime markers), and `status` only reads it together with the runtime marker the hooks of a running session write. This release registers hooks only through the plugin; a copy the cleanup had to keep (see below) loses its unchanged SKILL.md, so Claude Code no longer loads it as a skill; move it aside. Older releases registered the hooks in `~/.claude/settings.json` and copied the package to `~/.claude/skills/session-log`, which went stale after plugin updates; `/session-log on`, `repair`, and each Claude Code startup remove those leftovers (and the legacy prompt_log scripts the pre-plugin install copied into `~/.claude/scripts`), and removing them never makes `status` report a restart; `on` still turns logging on when that cleanup fails, which it reports as `legacy cleanup failed: <reason>; fix it, then run repair` (or `legacy cleanup failed; run repair to see why` when the cleanup names no reason), or is skipped for a transient reason (exit status 75, below), which it reports as `legacy cleanup skipped: <reason>`. The copy goes only when its ownership manifest proves every file in it unchanged; otherwise `repair` names it so you can move it aside. A startup repeats that cleanup only when `settings.json`, the session-log package (its location or `VERSION`), or the presence of that copy or those scripts changed since its last attempt; only a transient attempt (exit status 75, see below) or one a signal ended before it finished leaves no stamp and runs again at the next startup, while a failed one (for example on an edited legacy script) is stamped and runs again only when one of those changes; `/session-log repair` runs it on demand and reports what it changed. Cursor imports enabled Claude Code plugins and runs their hooks too; there only the session-start hook runs the legacy cleanup, and none of them logs, nor writes or removes Claude runtime markers (a kept legacy copy still registered in `settings.json` runs older code that does, until you move it aside). Restart Claude Code (or start a new session) for plugin changes to load. To update later:

```bash
claude plugin update claude-goodies@user538295
```

### omp plugin marketplace

```bash
omp plugin marketplace add user538295/harness-goodies
omp plugin install omp-goodies@user538295
```

Inside omp the same steps are `/marketplace add user538295/harness-goodies` and `/marketplace install omp-goodies@user538295`. omp reads its own catalog (`.omp-plugin/marketplace.json`) and installs `omp-goodies`: every skill (including `/skill:da-review` and `/skill:iterative-review`) and the `devils-advocate` agent with omp tool names. Run `/reload-plugins` or start a new session to load them; the session-log extension needs an omp restart instead (see below). To update later:

```bash
omp plugin marketplace update user538295
omp plugin upgrade omp-goodies@user538295
```

The plugin's `package.json` registers the session-log extension (its `omp.extensions` entry), which logs nothing until `/session-log on` creates the enable flag; it still writes its runtime marker and starts the legacy cleanup when it loads. On a relocated or symlinked omp root (for example a named profile) the extension only registers `/session-log`, which reports the error. `on` and `off` register nothing (`on` creates that flag after the legacy cleanup, `off` removes it and leaves the loaded extension's runtime marker), the loaded extension reads it at every prompt, and `status` only reads it together with that runtime marker. `/reload-plugins` does not reload extensions, so restart omp after installing or upgrading the plugin; until then `on` and `status` report `on — restart required`. A session started before an upgrade keeps working, and its `/session-log` command runs the upgraded package; it needs an omp restart once the package it loaded is gone and no user-scope install is linked for it (as with a project-scope-only install), or once the cleanup removed it. Older releases linked `~/.omp/agent/extensions/session-log.js` into a package copy seeded at `~/.omp/agent/skills/session-log`, and omp would load that extension beside the plugin's; `on`, `repair`, and each omp start (in the background) remove them, the copy only when its ownership manifest proves every file in it unchanged; otherwise `repair` names it so you can move it aside. A foreign (unmarked) file or link at the extension path stays, and the rest of the cleanup still runs. An omp start runs that cleanup once per change of the package (its location or `VERSION`) or of the leftovers present, and stamps it in `~/.omp/agent/session-log/legacy-cleanup.stamp` after every attempt except a transient one (exit status 75) or one that never finished (timed out after 120 seconds, killed, or could not start), which runs again at the next start; a failed attempt is stamped too and runs again only when one of those changes. The first omp start after upgrading still loads the legacy extension once; both extensions write the same deduplicated entries. The background cleanup reports a failure (not a transient one), or a package copy it kept, once on omp's stderr, and an unfinished attempt at each start that retries it; `/session-log repair` names the cause. While a seeded copy remains, `/skill:session-log` may resolve to that old copy, so use `/session-log`.

Each entrypoint passes its native harness identity explicitly; it never infers a harness from directories or environment variables. `off` removes the enable flag and the runtime markers (not OMP's, which its loaded extension keeps) and never registers or removes hooks. Exit status 75 means a transient condition: running the same command again is safe and can succeed, and the reports earlier steps printed (such as a `removed …` line) stand. It happens when another session-log command holds the install lock (`another session-log command is already in progress for <harness>`), or the Claude Code cleanup met another writer of `settings.json` and left no file of its own behind (`Claude settings changed during the legacy cleanup; run repair again`): `settings.json` changed or went away before the cleanup moved it to its backup, which leaves it as the other writer left it, or, while the cleanup was putting its new version in place, other programs recreated `settings.json` and then removed both it and that backup, which leaves nothing to clean. Any other clash with another writer of `settings.json` is a failure (exit status 1), not exit 75, including every other one that recreates `settings.json` while the cleanup's backup stands in for it: `Claude settings changed while the legacy cleanup was replacing it; another version is kept in <file>` names the conflict or backup file that holds the version not left in place, and `Claude settings changed while the legacy cleanup was replacing it; another program removed settings.json, so the cleanup restored it from its backup; review it` means the cleanup put the version it started from back as `settings.json` after another program removed it. A Codex or Cursor write of `hooks.json` (Codex `on`, `repair`, and the adapter repair of `status` and `usage`; the Cursor cleanup) that clashes with another writer fails the same way (exit status 1) with `<path> changed while session-log was replacing it; another version is kept in <file>`, and any other error writing it with `cannot update hooks file <path>: <error>`, or, when the write could not put its backup back as `hooks.json` either, `cannot update hooks file <path>: <error>; a version is kept in <file>`. A kept file stays until you delete it: beside `settings.json`, `.session-log-backup.<pid>.<attempt>` or that name plus `.conflict.<suffix>`; beside `hooks.json`, `.session-log-backup.<pid>.<8 hex digits>.<attempt>` (or that name plus `.conflict.<suffix>`) or `.hooks.json.<pid>.<8 hex digits>.conflict.<suffix>`. Claude Code, Codex, and Cursor `on`, `repair`, and `status` name each one as `<label>: another version of <name> is kept in <file>; review it, then delete it`, or, while `<name>` (`settings.json` or `hooks.json`) itself is missing and the kept file may be its only copy, as `<label>: <name> is missing; another version of it is kept in <file>; review it and copy back what you still need`, so a clash the silent session-start cleanup hit is reported too. `on`, `repair`, and `status` print these lines before anything else, so a failing `repair`, or a Codex `status` whose adapter repair fails or rebuilds a missing `hooks.json`, still names them. Codex and Cursor name no kept `hooks.json` version while `~/.codex` or `~/.cursor` is a directory session-log does not trust (no sticky bit, and writable by group or others or owned by another user), because session-log leaves its backup there after every `hooks.json` write; Claude Code names kept `settings.json` versions in any directory, since its cleanup deletes its own backup after every write that succeeds. `on` runs the legacy cleanup after naming those kept files and prints the cleanup's report lines (such as a `removed …` line), as `repair` does. In Claude Code, Cursor, and OMP `on` still turns logging on when the legacy cleanup fails, which it reports as `legacy cleanup failed: <reason>; fix it, then run repair` (or `legacy cleanup failed; run repair to see why` when the cleanup names no reason), or when it hits exit status 75, which it reports as `legacy cleanup skipped: <reason>`; either line comes before the `on` line. `off` takes that lock only in Codex.

### Cursor plugin

Cursor reads its own manifests (`.cursor-plugin/plugin.json` and `.cursor-plugin/marketplace.json`) and installs `cursor-goodies`: every skill, the `devils-advocate` agent, and the session-log hooks in `.cursor-plugin/hooks.json` (Cursor's own format; cursor-goodies does not use the Claude Code `hooks/hooks.json`, which Cursor runs only for an imported claude-goodies plugin, where only their session-start hook runs, for the legacy cleanup, and none of them logs). Those hooks fire on each session start, prompt, response, stop, and sub-agent stop, and run with no prompt. Cursor runs the hook commands with your `$SHELL -c`; they are tested with sh, bash, zsh, and fish, and shells with another syntax (for example nushell, xonsh, or PowerShell) fail on every event, which Cursor records only in **Cursor Settings → Hooks** (no notification). Each command checks the enable flag first, so while logging is off the plugin's hooks exit immediately without starting Python (legacy `~/.cursor/hooks.json` entries still run the older code until the cleanup removes them), and they also exit silently when the plugin's hook script or `python3` is missing. Native Windows is not supported for these hooks, since Cursor runs them through PowerShell there: use Cursor under WSL. `/session-log on` creates the flag after the legacy cleanup, `off` removes it and the runtime markers, neither registers anything, and `status` reads it together with the runtime marker the hooks write. Cursor loads plugin hooks only while **Cursor Settings → Include Third-Party Plugins, Skills, and Other Configs** is on (the default) and its server-side plugin import gate (`enable_cc_plugin_import`, on by default in the client) allows it; when logging is on but no hook of the installed cursor-goodies version has run, `status` reports `on — no hook of this cursor-goodies version has run` and names the plugin install or update, that setting, and a restart. `status` counts only hooks of the same package `VERSION` as the session-log skill it runs from, and failing hook runs are listed in **Cursor Settings → Hooks**. Older releases registered the hooks in `~/.cursor/hooks.json` and copied the package to `~/.cursor/skills/session-log`; Cursor runs both hook sources, so `/session-log on` and `repair` remove those session-log entries (every other entry stays), the copy when its ownership manifest proves every file unchanged (otherwise `repair` names it so you can move it aside: a kept copy whose SKILL.md itself changed still runs older code that re-registers its `~/.cursor/hooks.json` entries whenever it is used), and the adapter state files older releases wrote. Each Cursor session start while logging is on runs `repair` when `~/.cursor/hooks.json` still holds such entries, for at most 20 seconds; a timeout, or a cleanup that cannot start, is written to the hook's stderr, which Cursor logs. Install it through one of two marketplace routes.

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

Codex reads its own manifests (`.agents/plugins/marketplace.json` and `.codex-plugin/plugin.json`) and installs `codex-goodies`: every skill, namespaced as `codex-goodies:<skill>`. Codex plugins carry no agents, so `devils-advocate` is not included; `da-review` and `iterative-review` fall back to generic subagents that read the persona from the plugin's `agents/devils-advocate.md`. The plugin ships no hooks; session logging stays opt-in through the `session-log` skill's `on` command. Start a new session to load it.

`on` requires this plugin installed from the `user538295` marketplace and adds four commands to `~/.codex/hooks.json`. Each one runs the newest codex-goodies package installed from that marketplace (never another marketplace's) and does nothing once the plugin is removed. Codex runs them only once you trust them: until a Codex session has run them, `on` and `status` report `on — approve the session-log hooks in /hooks, or restart Codex and choose 'Trust all and continue' if it shows the 'Hooks need review' screen`. Approving them in `/hooks` takes effect in the running session. `codex exec` and other non-interactive runs show no review screen and run only hooks already trusted, so trust them once in the interactive TUI. Their text names no package path or version, so plugin updates never change it, and they stay trusted as long as the entries keep their place in `hooks.json` (Codex ties a hook's trust to its position there).

To update later:

```bash
codex plugin marketplace upgrade user538295
```

`on` and `repair` also replace the entries older releases registered by package path (only while the plugin is installed; without it they leave those entries alone and `status` reports `on — codex-goodies plugin not installed`) and remove the package copy those releases seeded into `~/.codex/skills/session-log`; with logging on, `status` and `usage` run the same cleanup when they repair the `hooks.json` entries. A copy with a changed or foreign file is named, not removed. A copy `~/.codex/hooks.json` still names (or that cannot be read) stays, because Codex sessions started earlier keep running its hook, and a hook whose script is gone blocks their prompts. Restart Codex before the next `on` or `repair`: that run removes the copy, and neither command can tell whether Codex restarted. Without the plugin, `repair` keeps such a copy and tells you to install the plugin, then run `on`.

Installed `claude-goodies@user538295` in Codex before this plugin existed? Switch with `codex plugin remove claude-goodies@user538295 && codex plugin add codex-goodies@user538295`; that install also registered the Claude Code session-log hooks, which the switch removes.

### OpenCode plugin

```bash
opencode plugin add github:user538295/harness-goodies
```

Requires OpenCode V2. OpenCode installs the `opencode-goodies` package (`package.json` plus `.opencode-plugin/index.js`) into its package cache, adds it to `~/.config/opencode/opencode.json`, and loads it without a restart. The plugin registers every skill and the `devils-advocate` subagent. It registers no `/session-log` command and no logging handlers: session-log does not log in OpenCode until the next release, so do not run it there. To update later:

```bash
opencode plugin update
```

Skills and agents with the same names under `~/.config/opencode/skills` and `~/.config/opencode/agents` take precedence over the plugin's copies; remove those copies to use the plugin's versions. Until the next release the plugin does not log or clean up session-log files, and the shipped `session-log` skill tells the model to stop instead of running the script. An old `~/.config/opencode/skills/session-log` copy outranks the plugin's skill and lacks that stop rule; delete it, together with `~/.config/opencode/commands/session-log.md` and `~/.config/opencode/plugins/session-log.js` if present, which OpenCode V2 tries to load at every start. If the model still offers to run `session-log` in OpenCode, tell it not to: nothing would be logged.

### Universal session-log package

Each harness plugin above ships the package, so there is nothing to install separately. The skill's `install.sh` runs one command through the package it belongs to, for the identity the host names (Codex is the exception: its hooks run only the codex-goodies plugin-cache package, so Codex `on` fails from a checkout without that plugin installed, and Codex `status` compares the runtime marker its hooks write with the running package's `VERSION`, so run `status` from the plugin's skill: from a checkout with another `VERSION` it keeps reporting the trust instruction); from a checkout of this repository:

```bash
bash skills/session-log/install.sh --harness claude --arguments "on"
```

Every package copy the cleanup keeps loses the SKILL.md files its ownership manifest proves unchanged, so no harness loads it as a skill and its older code can no longer be invoked (a listed SKILL.md that cannot be removed is reported as `kept the SKILL.md of legacy package copy <path>: <reason>; it may still be loaded as a skill` for a copy kept for running sessions' hooks, and as `kept legacy package copy <path>: <reason>; cannot remove <file>: <error>, so it may still be loaded as a skill; move it aside` for a copy its manifest cannot prove unchanged); Codex keeps a proven copy that way while running sessions' hooks may still run it. Earlier releases copied the package into every harness's skill root; in Claude Code and OMP, `/session-log on`, `repair`, and each start remove that copy, and so do Cursor's `/session-log on`, `repair`, and session start while logging is on and legacy `~/.cursor/hooks.json` entries remain, and Codex's `on` and `repair` (see each harness's section above).

The script recognizes exactly the identities `claude`, `codex`, `cursor`, `opencode`, and `omp`; `opencode` is unavailable (`on`, `off`, `status`, and `usage`) until the next release. The universal `SKILL.md` requires the current host/model to choose one explicitly; identity is never inferred from directories, processes, or environment variables.

Only default roots are supported: Claude Code `~/.claude`, Codex `~/.codex`, Cursor `~/.cursor`, OpenCode `~/.config/opencode`, and OMP `~/.omp/agent`; relocated config or data roots fail explicitly. Logging remains off until enabled separately in each harness. In Codex, `status` and `usage` repair a missing or outdated adapter while logging is enabled; the Claude Code, Cursor, and OMP plugins ship theirs, so there is no adapter to repair.

OMP keeps per-process runtime records alongside the shared runtime pointer. The adapter bounds its in-memory session history; after a process restart, the on-disk logs remain the source of recorded prompts and responses.

The Python package's `sessionlog.pricing.PricingRules` builds report data from normalized sessions and an injected `PriceBook`; it is not yet wired into the existing CLI. Recorded costs take precedence over model rates. Rows round half-up to cents, unknown prices remain unknown, mixed rows retain their priced subtotal, and TOTAL sums those rounded row amounts. Codex/Cursor totals carry the API-equivalent flag. Run its unit tests with `python3 -m unittest discover -s skills/session-log/tests -t skills/session-log -p test_pricing.py`.

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

- Universal session-log: Claude Code, Codex, Cursor, or OMP (OpenCode does not log until the next release).
- macOS or Linux — or Windows, natively for Claude Code or via WSL.
- Native Windows runs the Claude Code plugin under Git Bash (install Git for Windows), with a Windows Python 3 as `python3` and jq on its PATH: the session-log hooks, `/session-log on`, `off`, `status`, `usage`, `repair`, and the session-start legacy cleanup all work. The hooks exit 0 silently when their script is missing, and need no jq while logging is off. Windows has no `dir_fd`, so session-log checks that no folder on a path is a symlink or junction just before it uses the path, not through an open folder: a program running as the same user could swap one in between, which the macOS and Linux design also trusts such a program not to do. The other harnesses need WSL on Windows.
- Bash 4+, Python 3, jq, standard POSIX utilities, and `shasum` or `sha256sum`. Stock macOS Bash 3.2 is insufficient for the session-log CLI; put an installed Bash 4+ on `PATH`.
- OMP native usage reports run on Bun when it is installed, otherwise on the Bun built into the `omp` binary.

No MCP servers are required.

---

Full reference, with worked examples for every skill, agent, and script — open the handout:
**[English](https://user538295.github.io/harness-goodies/handout/) · [Magyar](https://user538295.github.io/harness-goodies/handout/index-hu.html)**.
