---
name: clean-code-review
description: Structured clean code review — 132 checks across 7 groups (clarity, smells, solid, arch, tests, safety, ddd). Flexible targets — local changes (default), staged/unstaged/untracked, a git ref/range, a pull/merge-request link (GitHub/GitLab/Bitbucket), or explicit files (works without git). Runs scripted detections, spawns one agent per group, synthesizes findings.
---

# /clean-code-review

## Usage
`/clean-code-review [TARGET ...] [GROUP ...]`

**Targets** (default: `local` — all local changes):
- *(none)* or `local` — staged + unstaged + untracked changes
- `staged` · `unstaged` · `untracked` — only those areas; combinable (e.g. `staged untracked`)
- a git ref or range — `main..HEAD`, `HEAD~3`, `abc123..def456`. A single ref diffs against the worktree. Not combinable with the keywords above.
- a pull/merge-request link — GitHub `.../pull/<n>` (Gitea `.../pulls/<n>`), GitLab `.../merge_requests/<n>` (or `.../merge-requests/<n>`), Bitbucket `.../pull-requests/<n>`. Resolved to a `base...head` range in an isolated worktree (Step 1.5), then reviewed as a ref target — your own branch and working tree are untouched. Not combinable with any other target.
- one or more file paths — review the whole files; the only mode that works outside a git repository

**Groups** (case-insensitive): `clarity` · `smells` · `solid` · `arch` · `tests` · `safety` · `ddd`. Omit to run all 7.

Expected check counts: clarity=18, smells=27, solid=15, arch=18, tests=13, safety=32, ddd=9 (132 total).

## Configuration (optional)

A project may silence specific rules with a `.clean-code-review-config.json` file at its repo root (or, outside git, the working directory):

```json
{ "deny": ["ddd", "clarity-08", "safety-25"] }
```

- `deny` is the only key. Each entry is a **group name** (`ddd` → all its checks) or a **check id** (`clarity-08`). Only string entries are honored; a non-string entry (number, boolean, `null`) is ignored — a number surfaces as an unknown-entry `WARN-CONFIG:`, while `true`/`null` are dropped silently.
- Denied checks are never run, reported, or counted — the review proceeds on the remaining rules with no "partial evaluation" warning.
- `collect.sh` resolves entries (matched literally) against the real check universe, writes the concrete silenced ids to `denied.txt`, and slices those checks out of the per-group MD copies each agent reads (`$OUTDIR/groups/{group}.md`) — so a denied check's definition never reaches the model, not merely an instruction to skip it. A group with every check denied is dropped entirely.
- A broken config never aborts the run: an unknown entry, invalid JSON, or a `deny` value that isn't an array each produces a `WARN-CONFIG:` line (surfaced in the report) and is otherwise ignored — the review continues on the valid entries (possibly none).

## Severity
- **Critical**: blocks correctness, security, or safety
- **Major**: significant design flaw, missing requirement, or likely bug
- **Moderate**: suboptimal but workable
- **Minor**: style, naming, or nitpick

---

## Step 0 — Locate this skill (`$BASE`)

`$BASE` is this skill's own directory (the one holding this `SKILL.md`). Every file this skill names is written **relative to the skill root** (`scripts/collect.sh`, `scripts/resolve-pr.sh`, `synthesizer.md`, per the Agent Skills spec) and must always be resolved as `$BASE/<relative path>` — never run bare: no harness sets the shell's working directory to the skill root, and the shell must stay in the reviewed repository for `collect.sh` to work.

**Primary — use the directory your harness reported when this skill loaded.** Claude Code and OpenCode print `Base directory for this skill: <path>`; omp prints `[Skill directory: <path>]`; in Cursor, use the directory of the `SKILL.md` file you were given. Take that path verbatim as `$BASE`.

**Fallback — only if no directory was reported**, run this harness-neutral locator (an explicit `CCR_HOME=<skill dir>` wins; then project-level roots, then user-level roots of Claude Code, Cursor, OpenCode, omp and Codex, then the newest Claude Code plugin cache; symlinks are followed, stale ones skipped):

```bash
BASE=""; for d in "${CCR_HOME:-}" .agents/skills/clean-code-review .claude/skills/clean-code-review .cursor/skills/clean-code-review .opencode/skills/clean-code-review .codex/skills/clean-code-review ~/.agents/skills/clean-code-review ~/.claude/skills/clean-code-review ~/.cursor/skills/clean-code-review ~/.config/opencode/skills/clean-code-review ~/.omp/agent/skills/clean-code-review ~/.codex/skills/clean-code-review "$(ls -d ~/.claude/plugins/cache/*/claude-goodies/*/skills/clean-code-review 2>/dev/null | sort -V | tail -1)" "$(ls -d ~/.omp/plugins/cache/plugins/*___omp-goodies___*/skills/clean-code-review 2>/dev/null | sort -V | tail -1)" "$(ls -dt ~/.cursor/plugins/cache/*/cursor-goodies/*/skills/clean-code-review 2>/dev/null | head -1)" "$(ls -dt ~/.cache/opencode/npm/*/*/node_modules/opencode-goodies/skills/clean-code-review 2>/dev/null | head -1)"; do [ -n "$d" ] && [ -f "$d/scripts/collect.sh" ] && { BASE="$d"; break; }; done; [ -n "$BASE" ] && echo "$BASE" || { echo "ERROR: clean-code-review not found in any known skills root — set CCR_HOME=<skill dir>" >&2; false; }
```

Capture the printed path as `$BASE`. On the error, stop and show it to the user.

## Step 1 — Parse arguments

Split `$ARGUMENTS` into tokens:
- Tokens that match a group name (case-insensitive) select active groups. No group tokens → all 7 active. A group-name token is always treated as a group, never as a file or ref.
- A token whose path segment before the number is `pull`, `pulls`, `merge_requests`, `merge-requests`, or `pull-requests` (e.g. `.../pull/<n>`, `.../merge_requests/<n>`) is a **PR/MR target** — resolve it in Step 1.5 and pass the resolved ref to `collect.sh` instead of the URL. It cannot be combined with any other target token.
- **Every other token is passed through to `collect.sh` unchanged** — the script validates targets and rejects unknown tokens itself. Do not pre-validate refs or files yourself.

## Step 1.5 — Resolve a PR/MR link (only when a PR/MR target was given)

With `$BASE` from Step 0, resolve the request into an isolated worktree. Capture stdout with `$(…)` so the script's **exit status is preserved** (its stderr streams to the user):

```bash
PR_OUT=$(bash "$BASE/scripts/resolve-pr.sh" "$REQUEST_URL"); PR_RC=$?   # add "$BASE_BRANCH" as a 2nd arg only to override the base
PR_WORKTREE=$(printf '%s\n' "$PR_OUT" | sed -n 1p)
PR_REF=$(printf '%s\n' "$PR_OUT" | sed -n 2p)
```

Replace `$REQUEST_URL` with the actual PR/MR link (no literal placeholder or `[…]` reaches the command).

- `resolve-pr.sh` works with any provider that publishes request refs over git, one ref per URL kind: `refs/pull/<n>/head` (GitHub/Gitea), `refs/merge-requests/<n>/head` (GitLab), `refs/pull-requests/<n>/from` (Bitbucket). It fetches the head with plain `git fetch` and checks it out into a **temporary isolated worktree** — the user's branch and working tree are never touched, so nothing needs restoring. Its status (which remote and base branch it used, and the exact cleanup command) goes to stderr; show it to the user.
- **Base branch**: an explicit 2nd argument wins; else, for github.com, the GitHub CLI supplies it when it is installed and working (`gh pr view`, used only to read the base name); else the remote's default branch. If the request targets a non-default branch and the base is wrong or cannot be found, re-run with the base branch as the 2nd argument.
- **If `$PR_RC` is non-zero**: `resolve-pr.sh` failed and already printed the reason to stderr — report that to the user and **STOP the review here**. It creates the worktree last and cleans up on failure, so a non-zero exit leaves nothing to remove; do not run Step 2 (never pass an empty `$PR_REF` to `collect.sh`, and never drop the token so it falls back to reviewing the caller's own tree).
- **Success** (`$PR_RC` = 0): `$PR_WORKTREE` is the worktree path, `$PR_REF` a `base...head` range. In Step 2, run `collect.sh` **from inside `$PR_WORKTREE`**, passing `$PR_REF` as the (only) non-group token.
- **Config comes from the PR's checkout** — a review-integrity caveat: `collect.sh` reads `.clean-code-review-config.json` from the reviewed tree, which for a PR/MR target is the request head's committed content, **not** the reviewer's. An untrusted author can therefore commit a `deny` list that silences checks on their own PR. Any silenced checks are disclosed in Step 6 once the review reaches it — no action is needed here. (An early abort in Step 2/3/4 skips that disclosure, but such an abort also produces no review to trust, so nothing an author silenced is ever acted on.)
- **Cleanup (authoritative)**: once the worktree exists it must be removed on **every** exit path after its creation — after Step 6 on success, and before stopping at any earlier abort (a `collect.sh` error in Step 2, the Step 3 language-gate abort, or the Step 4 all-groups-denied abort). Always run, and tell the user: `git worktree remove --force "$PR_WORKTREE"`. (If a run is killed before this, the leftover is harmless and clearable: `git worktree remove --force <path>` when the dir is still there — its path is in the resolver's stderr — or `git worktree prune` if the temp dir was already OS-cleaned.)

## Step 2 — Collect (scripted)

With `$BASE` from Step 0, run:

```bash
bash "$BASE/scripts/collect.sh" <non-group tokens...>
```

For a **PR/MR target** (Step 1.5), run this from inside the resolved worktree and pass the resolved range instead of the URL — e.g. `(cd "$PR_WORKTREE" && bash "$BASE/scripts/collect.sh" "$PR_REF")` — so the detections scan the request's checked-out files.

- **Non-zero exit**: report the script's stderr message to the user verbatim and stop. For a PR/MR target, first run Step 1.5's worktree cleanup.
- **Success**: stdout is an output directory (`$OUTDIR`) containing:

| File | Content |
|---|---|
| `mode.txt` | resolved target (`staged`/`unstaged`/`untracked` lines, `ref: X`, or `files`) |
| `files.txt` | files under review (vendor/generated already excluded) |
| `files_prod.txt` | `files.txt` minus test files — the `SKIP_TESTS` checks (`ddd-01`, `safety-06`, `solid-06`, `solid-08`, `solid-09`, `arch-14`) run against this list instead |
| `skipped.txt` | excluded files |
| `languages.txt` | detected language tokens |
| `unanalysed.txt` | code extensions with no language mapping |
| `addedlines.txt` | `file:line` index of added/changed lines |
| `diff.patch` | the raw unified diff (untracked/file targets appear as whole-file additions) |
| `numbered.patch` | the same diff with each added/context line prefixed `N\|` (its true file line number) — this is what agents receive |
| `hits.txt` | detection hits, already filtered to added lines and capped |
| `denied.txt` | resolved check ids silenced by project config (one per line; empty when no config) — see Configuration |
| `groups/{group}.md` | one allow-listed copy per group (always written) — verbatim when the group has no denied checks, otherwise with denied checks sliced out; agents always read these |
| `warnings.txt` | `WARN-CAP:` / `WARN-DETECT:` / `WARN-CONFIG:` / `NOTICE-LARGE-DIFF:` lines |

The script needs no GNU grep or other extras — detection patterns run via perl (preinstalled on macOS/Linux). Paths with spaces are handled.

## Step 3 — Language gate

If `languages.txt` is empty, abort the review: `No supported language files detected. Supported: TypeScript, JavaScript, C#, Python, Swift, Kotlin, Java, C++. Unanalysed: {contents of unanalysed.txt}.` Do not spawn agents. (For a PR/MR target, run Step 1.5's worktree cleanup before stopping.)

## Step 4 — Spawn review agents

Spawn **one agent per active group in parallel** (Agent tool). Group prompt files:

| Group | File | Checks |
|---|---|---|
| clarity | `$OUTDIR/groups/clarity.md` | 18 |
| smells | `$OUTDIR/groups/smells.md` | 27 |
| solid | `$OUTDIR/groups/solid.md` | 15 |
| arch | `$OUTDIR/groups/arch.md` | 18 |
| tests | `$OUTDIR/groups/tests.md` | 13 |
| safety | `$OUTDIR/groups/safety.md` | 32 |
| ddd | `$OUTDIR/groups/ddd.md` | 9 |

The `Checks` column is each group's full catalog size. `collect.sh` writes one copy of every group MD to `$OUTDIR/groups/{group}.md` — always the file to pass an agent — with any denied checks already sliced out, so the actual count may be lower (see below).

Before spawning, get the diff size: `DIFF_LINES=$(wc -l < "$OUTDIR/numbered.patch")`.

**Drop fully-denied groups.** A group's **effective check count** is the number of `### {group}-` headers in its `$OUTDIR/groups/{group}.md`. If that count is 0 (every check in the group denied), **drop the group** — do not spawn its agent, and remove it from the active set (and from Step 5's counts). If every active group is dropped, abort: `All active groups are silenced by .clean-code-review-config.json — nothing to review.` For a PR/MR target this means the **reviewed PR/MR's own committed config silenced every check** — warn the user this may be author-introduced to evade review (Step 6's disclosure is not reached on this abort), then run Step 1.5's worktree cleanup before stopping.

Pass each agent:
- The path to `numbered.patch` as `$DIFF` and its exact length: "The diff file is {DIFF_LINES} lines. You MUST read all {DIFF_LINES} lines — keep issuing Read calls with increasing `offset` until you have seen the final line. Reviewing a partially-read diff is a failure." Each added/context line is prefixed `N|` with its true file line number.
- Its group MD file path `$OUTDIR/groups/{group}.md` (agent reads it; it already contains only the checks to evaluate)
- `$PRECOMPUTED`: its group's lines from `hits.txt` (those starting `{group}-`). Line formats, tab-separated after the check id:
  - `id<TAB>file:line:text` → `{ check_id, file, line, matched_text }` — split on the **first two** colons only (paths and text may contain colons)
  - `tests-13` `<TAB>file:count` → `{ check_id, file, count }`
  - `smells-01` `<TAB>count file` → `{ check_id, file, line_count }`
- `$LANGUAGES`: contents of `languages.txt`
- The `skipped.txt` list with instruction: "Files in this list are excluded — report NO findings for them."
- This instruction:

> **Read-only**: do not edit any file. Report findings only.
> Scriptable detections were pre-executed — work from `$PRECOMPUTED` and the diff; do not re-run detections. Where a check explicitly requires reading repository files, you may do so.
> Read your group MD file. For each precomputed hit: confirm it is a real violation (keep) or a false positive (dismiss silently).
> **Systematic sweep — this is the required work protocol**: process your group's checks one at a time, in ID order. For each check, scan the ENTIRE diff for violations of that check before moving to the next check. Report EVERY violation you find, not a representative sample — two findings of the same check in different files are two finding lines. Do not skip a check because early checks already produced findings. Output findings only — one line per finding, no prose.
> **Anchoring**: added/context lines in `$DIFF` are prefixed `N|` with their true file line number. Anchor every finding at the line your action text refers to, taking the number from that prefix — never compute line numbers from `@@` hunk offsets yourself. If your action names a specific call, symbol, or statement, the anchor MUST be the `N|` of the exact line containing it — not the line before it, not the enclosing block's first line. Strip the `N|` prefix when quoting code.
> End with a status line: `STATUS: GROUP={group} findings=N checks=M ok` (N = finding lines emitted, M = number of `### {group}-NN` headers in your MD file) or `STATUS: GROUP={group} failed=<reason>`.

**Finding format:**
```
[{group}-NN] · Severity · Check Name | file:line | One-line action
```
A literal ` | ` inside the action field must be escaped as ` \| `. Example:
```
[clarity-08] · Moderate · Magic Numbers/Strings | src/payment/calculator.ts:42 | Extract 365 into named constant DAYS_IN_YEAR
```

## Step 5 — Synthesize

After all group agents complete, spawn a synthesizer agent following `$BASE/synthesizer.md`. Pass it:
- All finding lines and STATUS lines
- Active groups (fully-denied groups already dropped), `languages.txt`, `skipped.txt`, `unanalysed.txt`, `mode.txt`
- The **effective** expected check count per active group = the number of `### {group}-` headers in the MD passed to that group's agent (collect.sh already sliced out denied checks) — this is the count table the synthesizer must use for reconciliation and `{checks_run}`
- All lines from `warnings.txt` (including any `WARN-CONFIG:` lines)

## Step 6 — Output

For a **PR/MR target only**, before presenting the report: if `$OUTDIR/denied.txt` is non-empty, show the user exactly one disclosure line (the sole allowed exception to the no-commentary rule below) — `⚠ The reviewed PR/MR's committed .clean-code-review-config.json silenced these checks: {ids in denied.txt}. This may be the project's standing policy or author-introduced; for an untrusted contribution, confirm none were disabled to hide problems.` This is the authoritative, reliably-executed home of the config-integrity disclosure noted in Step 1.5.

Present the synthesizer output directly, **exactly once** — do not repeat, re-summarize, or echo any section of it, and add no commentary before or after it (no dedup narration, no framing text) beyond the single PR/MR disclosure line above. Then delete `$OUTDIR` (a mktemp directory this skill created), falling back to the trash if `rm` fails:

```bash
rm -rf "$OUTDIR" || trash "$OUTDIR"
```

For a **PR/MR target**, also run Step 1.5's authoritative worktree cleanup now: `git worktree remove --force "$PR_WORKTREE"` (tell the user).

---

## Edge cases

- **Ref-target line drift**: detection patterns scan worktree file contents, while `addedlines.txt` comes from the requested diff. If the worktree has drifted far from the ref being reviewed, some scripted hits may be filtered out; judgment checks still see the true diff.
- **Partially visible hierarchies** (solid/ddd checks): agents must state the limitation rather than guess — their MD files define the severity-downgrade rules.
- **Testing this skill**: run all five suites after any change to `scripts/` or to Step 0:
  - `tests/test_locator.sh` — the Step 0 fallback locator, extracted verbatim from this file and run against fixture layouts of every supported harness (Claude Code, Cursor, OpenCode, omp, Codex)
  - `tests/test_collect.sh` — target resolution, filtering, caps, numbered diff
  - `tests/test_checks.sh` — every detection command executes cleanly
  - `tests/test_corpus.sh` — pattern semantics: per check+language, `tests/corpus.tsv` defines code that MUST match and near-misses that must NOT. When adding or changing a detection pattern, add its MATCH/NOMATCH rows to `corpus.tsv`.
  - `tests/test_resolve_pr.sh` — PR-link parsing, guards, remote-tier selection, base-branch resolution (including a stubbed-`gh` path), and the offline `git fetch` fallback (forced with `CCR_NO_GH=1`). Only real-network fetches to a hosting provider are out of scope.
- **Recall benchmark** (manual eval, not CI): `benchmark/` contains deliberately flawed Python, TypeScript, C#, Swift, and C++ files with 122 catalogued violations (`benchmark/planted.tsv`) plus precision traps. The catalogue exercises 91 of the 132 checks; the C++ fixture added safety-16/17/19, `typescript/presentation/RefundUseCase.ts` added arch-16/18, and `python/legacy/` added arch-17, but most of the checks added later (the rest of safety-08 through safety-32, smells-20 through smells-27, arch-11 through arch-15, tests-13, ddd-06 through ddd-09, clarity-18) still have no planted violations, so a benchmark run cannot measure their recall. The fourteen judgment checks clarity-18, arch-15, ddd-08, ddd-09, safety-25/26/27/28/29/30/31/32, and smells-24/25 are non-scriptable, so `tests/corpus.tsv` does not cover them either — they ship unmeasured until the fixtures catch up. Run the skill on those files and score against the catalog after changing agent prompts or models — see `benchmark/README.md`. Never "fix" the benchmark files.
