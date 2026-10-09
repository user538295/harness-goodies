# Release Guide

Four installation paths exist. A release must satisfy all of them.

| Path | Entry point | Version source |
|------|-------------|----------------|
| Claude Code plugin marketplace | `.claude-plugin/plugin.json` | `"version"` field in that file |
| omp plugin marketplace | `.omp-plugin/marketplace.json` | `"version"` field of the `omp-goodies` entry |
| Cursor marketplace (Cursor Marketplace or a team marketplace) | `.cursor-plugin/marketplace.json` → `.cursor-plugin/plugin.json` | Indexed commit; `"version"` in `plugin.json` is kept in sync |
| OpenCode package plugin | `package.json` → `.opencode-plugin/index.js` | Installed Git commit; `"version"` in `package.json` is kept in sync |

---

## How the install paths work

### Claude Code plugin marketplace
```bash
claude plugin marketplace add user538295/claude_goodies
claude plugin install claude-goodies
# update later:
claude plugin update claude-goodies@user538295
```
Claude Code reads `.claude-plugin/plugin.json` to resolve the plugin. The `"version"` field in that file is what `claude plugin` reports and uses to decide whether an update is available.

`.claude-plugin/marketplace.json` defines the marketplace listing — it does not contain a version and does not need to change on every release.

### omp plugin marketplace
```bash
omp plugin marketplace add user538295/claude_goodies
omp plugin install omp-goodies@user538295
# update later:
omp plugin marketplace update user538295
omp plugin upgrade omp-goodies@user538295
```
omp reads `.omp-plugin/marketplace.json` and ignores the Claude catalog when it exists. `omp plugin upgrade` compares the catalog entry's `"version"`, so it must change on every release. `.omp-plugin/plugin.json` marks the plugin as omp-native and carries no version.

### Cursor marketplace
First time only: submit the repository at [cursor.com/marketplace/publish](https://cursor.com/marketplace/publish) for the Cursor Marketplace, or import it into a team marketplace (**Dashboard → Plugins & MCPs → Add Marketplace → Import from Repo**). Cursor reads `.cursor-plugin/marketplace.json`, which lists `cursor-goodies` at `source: "./"`, and merges it with `.cursor-plugin/plugin.json`. Cursor caches installs per commit (`~/.cursor/plugins/cache/<marketplace>/cursor-goodies/<commit>/`), so a pushed commit is what reaches users: the Cursor Marketplace after Cursor reviews the update, a team marketplace on its next Auto Refresh or manual **Refresh**. `plugin.json` points `hooks` at the empty `.cursor-plugin/hooks.json`, so Cursor never loads the Claude Code hooks in `hooks/hooks.json`.

### OpenCode package plugin
```bash
opencode plugin add github:user538295/claude_goodies
# update later:
opencode plugin update
```
OpenCode installs the repository as the npm-style package `opencode-goodies` from `package.json`; `"files"` limits the install to `.opencode-plugin/index.js`, `skills/`, `commands/`, and `agents/`, and `"dependencies"` brings in `yaml` for frontmatter parsing. `opencode plugin check` compares the installed commit with the branch head, so every pushed commit is an update.

---

## Pre-release checklist

### 1. New files or directories?
Update `sync-manifest.txt` before anything else. It is the curated list of paths that live in both `~/.claude` and this repo — `release.sh` prompts you to confirm it is current.

- New skill directory → `skills/my-skill/` (trailing slash = recursive copy)
- New command file → `commands/my-command.md`
- New script → `scripts/my-script.sh`
- New single-file agent → `agents/my-agent.md`

Files not in the manifest are not tracked as curated runtime paths.

### 2. Bump the version
`release.sh` does this for you: it sets the same version in every file of `VERSION_FILES` — `.claude-plugin/plugin.json`, `.omp-plugin/marketplace.json`, `.cursor-plugin/plugin.json`, and `package.json`. By hand, update the `"version"` field in all four:

```json
{
  "name": "claude-goodies",
  "version": "X.Y.Z",
  ...
}
```

Follow semver (`MAJOR.MINOR.PATCH`):
- PATCH — bug fixes, doc updates, small additions
- MINOR — new skill, command, or agent
- MAJOR — breaking change to the workflow or install contract

### 3. Commit
```bash
git add sync-manifest.txt .claude-plugin/plugin.json .omp-plugin/marketplace.json .cursor-plugin/plugin.json package.json
git commit -m "chore(release): bump version to X.Y.Z"
```

---

## Tag naming convention

Tags must follow `vMAJOR.MINOR.PATCH` — no prefixes, no suffixes.

```
v1.0.2   ✓
v1.0.3   ✓
v1.0.4   ✓
claude-goodies--v1.0.4   ✗  (wrong — do not repeat)
```

---

## Cutting the release

```bash
# Confirm you are on main and it is clean
git checkout main
git status          # must be clean

# Create an annotated tag
git tag -a vX.Y.Z -m "Release vX.Y.Z"

# Push
git push origin main
git push origin vX.Y.Z
```

---

## Post-release verification

### Claude Code plugin marketplace
```bash
claude plugin update claude-goodies@user538295
```
Should report the new version number from `.claude-plugin/plugin.json`.

### omp plugin marketplace
```bash
omp plugin marketplace update user538295 && omp plugin upgrade omp-goodies@user538295
```
Should report `Upgraded omp-goodies@user538295 (user) to X.Y.Z`. `Marketplace "user538295" not found` means omp on this machine never added it; run the first-time setup once:
```bash
omp plugin marketplace add user538295/claude_goodies && omp plugin install omp-goodies@user538295
```

### Cursor marketplace
On a team marketplace without Auto Refresh, click **Refresh** in **Dashboard → Plugins & MCPs**. The Cursor Marketplace publishes the update after Cursor reviews it.

### OpenCode package plugin
```bash
opencode plugin update
```
Should report `Updated Server plugin "opencode-goodies"`; `opencode plugin check` then lists the new commit as `(current)`. On a machine that never added it, run `opencode plugin add github:user538295/claude_goodies` once.

---

## Summary: what changes every release

| File | What to do |
|------|------------|
| `sync-manifest.txt` | Add any new paths |
| `.claude-plugin/plugin.json` | Bump `"version"` |
| `.omp-plugin/marketplace.json` | Bump `"version"` of the `omp-goodies` entry |
| `.cursor-plugin/plugin.json` | Bump `"version"` |
| `package.json` | Bump `"version"` |
| git tag | Create `vX.Y.Z` on the release commit |
| `.claude-plugin/marketplace.json` | No change needed (listing metadata only) |
