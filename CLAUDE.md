# Testing unreleased plugin code in the real harnesses

End-to-end tests run the real harnesses (Claude Code, Codex, Cursor, OMP, OpenCode) in the maintainer's real home directory, with no API keys (logins are the user's own). Unreleased code is tested from the local checkout, never from a pushed branch, unless a harness has no workable local install route.

- **Install from the local checkout under the same marketplace name.** Every marketplace manifest in this repo is named `user538295` (`.agents/plugins/marketplace.json`, `.claude-plugin/marketplace.json`, `.cursor-plugin/marketplace.json`, `.omp-plugin/marketplace.json`). Code depends on the resulting cache paths (the skill locators search the plugin caches), so the local install must land where a GitHub install lands.
- **Swap, then restore.** The GitHub-sourced `user538295` marketplace is already installed; replace it with the local checkout for the run (use each harness's own marketplace command, e.g. `claude plugin marketplace …`, `codex plugin marketplace add <local root>`), then restore the GitHub source afterwards.
- **Back up before every run, restore after.** At minimum: `~/.claude/settings.json`, `~/.codex/hooks.json`, the `~/.cursor`, `~/.omp` and OpenCode config directories, and every harness's `prompt-logs` directory. A failed run must not leave the daily setup changed.
- **Verify the layout once per harness, and record it.** Compare the cache path a local install produces with the GitHub install's path. Where they differ, cover the path-dependent code with fixture tests (as `skills/clean-code-review/tests/test_locator.sh` does) instead of trusting the end-to-end run.
- **Fallback: a pushed test branch**, only for a harness with no workable local route (OpenCode documents no local-folder `plugin add`; check a config-file path or a packed npm archive first).

# Session-log: key decisions

Short form of the Key Decisions in `session-log-simple-brief.md`; there are no ADRs.

- **Read what the harness saved instead of recording it live.** Claude Code, Codex, OMP and OpenCode already save complete conversations; re-recording them is where the complexity and the Windows breakage came from.
- **Copy into an own archive.** Claude Code deletes transcripts after 30 days by default, so each session is copied as a Markdown log plus a saved usage report.
- **Hooks only where unavoidable.** Claude's session-start hook is there to sync before that cleanup runs; Cursor's hooks are the only source of its times and tokens; Codex, OMP and OpenCode need none (Codex deletes nothing).
- **Python standard library, 3.14 or newer** (`json`, `sqlite3`, `gzip`, `pathlib`, `compression.zstd`, `os.replace`). macOS's Command Line Tools `python3` is 3.9.6, so macOS users install Python from python.org or Homebrew.
- **`python3` for the Claude hook.** Claude hooks have no per-OS command field and `python`/`py` do not exist on stock macOS; an absolute interpreter path in `~/.claude/settings.json` was rejected because it brings back hook registration and its clean-up.
- **Launchers for Cursor** (`hook`, `hook.ps1`): the only tested way to get one hook command that is safe in sh-family shells and PowerShell and never blocks a prompt.
- **Whole-file rewrite, no append.** No half-written logs and no duplicate-entry handling; one `.sync.lock` only serializes the `.sync.json` merge.
- **One reader per harness, one shared writer.** Each harness is tested alone; a format change breaks only its reader.
