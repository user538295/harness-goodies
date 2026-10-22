---
name: app-store-review
description: Validate an app against every item of Apple's App Review Guidelines before App Store submission. Keeps a checklist file `AppReviewGuidelines.md` in sync with https://developer.apple.com/app-store/review/guidelines/, verifies each item against the app with parallel agents, and lists the items that are not satisfied or cannot be checked. Use when the user invokes /app-store-review or asks to check an app against the App Store review guidelines before submitting it.
---

# /app-store-review

Guidelines page: https://developer.apple.com/app-store/review/guidelines/

`$ARGUMENTS` is an optional path to the app project; default is the current directory. The checklist is `<project>/AppReviewGuidelines.md`. If the project holds no app (no Xcode project/workspace, Swift package, `Info.plist`, Flutter/React Native/Expo `ios`/`macos` folder, or similar), stop and say so.

## Step 1 — Sync the checklist with the page

Fetch the raw HTML: `curl -sL -A "Mozilla/5.0" <url>` (server-rendered; every guideline carries an `id` such as `1.1.1`). Do not use a reader/markdown web-fetch: it cuts long lines and drops text. Never rely on memory of the guidelines.

Format (one blank line between items; a note line directly under an item has no blank line):

```md
# App Review Guidelines

Source: <url> (page last updated <date from the page>)

## Before You Submit

- [ ] Before-1 <text>

## 1. Safety

### 1.1 Objectionable Content

- [ ] 1.1-1 <text>

- [ ] 1.1.1 <text>

#### 3.1.1 In-App Purchase

- [ ] 3.1.1-1 <text>
```

Rules:

- Cover sections 1–5 and the bullet list of `Before You Submit` (ids `Before-1`, `Before-2`, …, one per bullet; the documentation link groups are one item per group, links kept; skip that section's intro paragraphs, the "Make sure you:" lead-in and the notarization note). Skip Introduction and After You Submit.
- `##` = section, `###` = subsection (`N.N`; when the page gives only text after the id, the heading is just the id, e.g. `### 4.6`), `####` = a deeper guideline with a title but no text of its own, only sub-items (for example `3.1.1`, `5.1.1`, `2.3.1`). A guideline with any text of its own is an item, never a heading.
- Every text block is an item: every numbered guideline, every `(a)`/`(i)` sub-item (id `2.3.1(a)`, `5.1.1(i)`), every bullet, every paragraph — including section intros and the "Intentionally omitted" entries. Unnumbered blocks get `<nearest id>-<n>` in page order (`1.2-1`, `1.2-2`, …).
- A numbered item's title stays in its text (`3.1.2 Subscriptions: Apps may offer …`, `5.6.2 Developer Identity: Providing …`). Text is verbatim, and nothing may appear twice; links become `[text](absolute url)`; drop the notarization key icon.

New file: write all of it. Existing file: compare every item with the page.

- Unchanged text → keep the checkbox and its note.
- Changed or new text → `- [ ]`, drop the note.
- Item gone from the page → delete it.

Then prove the sync: list the guideline ids on the page (`grep -o 'id="[0-9][0-9.]*[a-z]*"'` on the HTML) and confirm each exists in the file (the page writes `3.1.1a` where the file has `3.1.1(a)`); compare the item count per section. Fix every gap before going on. Tell the user in one line what was created, added, changed (unchecked) and removed.

## Step 2 — Verify every item with agents

Do not edit the checklist from the agents: parallel writes race.

1. A unit is a `##` or `###` heading with the items below it, up to the next `##`/`###` heading (`####` stays inside). Number of agents = `ceil(items / 10)`, clamped to 5–32. One agent per unit; with more units than agents, merge the smallest neighbouring units until the count fits; with fewer units than agents, use one agent per unit. Over the harness's concurrency limit → queue the rest. No subagent tool → check the units yourself one after another and tell the user the check is not independent.
2. Models, if the harness lets you choose: the most capable model for any group containing section 1, 3 or 5 or `Before You Submit` (content, money, privacy and policy judgment); a balanced mid-tier model for groups of only section 2 or 4 (technical and UI evidence).
3. Give each agent: the project path, the checklist path, its subsections, and the rules below. All agents get the same rules.

Agent rules:

- Read the real artifacts: source, `Info.plist`, entitlements, `PrivacyInfo.xcprivacy`, Xcode project/build settings, dependency manifests and SDKs, assets, store metadata files (for example `fastlane/metadata`), privacy policy and support URLs. Never modify the project. Never assume; every verdict needs evidence from an artifact or a command run.
- Judge each item on its own and return exactly one result per item id. Statuses:
  - `pass` — positively verified. Give evidence (`path:line`, command and output).
  - `na` — the item concerns something the app does not have (Kids Category, Mac App Store, in-app purchases, a framework it does not import). Prove the absence (search, manifest) and give the reason.
  - `fail` — evidence shows a violation or a required piece is missing. Give a short name, the reason, and a concrete fix.
  - `manual` — the answer lives outside the project (App Store Connect metadata, age-rating answers, screenshots as uploaded, review notes, demo account, pricing, regulatory approval, legal rights to assets, live backend). Never guess `pass`. Give a short name and exactly what the user must confirm. If part of the item is checkable and fails, report `fail`.
- Return only the result lines, one line per item, no preamble and no extra lines: `<id> | pass|na|fail|manual | <evidence, or reason, or short name - reason and fix>`.

## Step 3 — Apply the results

Read each agent's full output (a harness may truncate a long result; fetch the rest), and take every line that matches `<id> | pass|na|fail|manual | …`; ignore any other text. Every item id needs a result. Re-spawn an agent for any id that is missing or malformed (at most twice); anything still missing stays unchecked with the note `UNCHECKED: Not verified - no result returned`.

Rewrite the checklist once with all results (an item is re-verified every run, so replace old notes):

- `pass` → `- [x]`, no note.
- `na` → `- [x]` and a note line `  > N/A: <reason>`.
- `fail` → `- [ ]` and `  > UNCHECKED: <short name> - <reason and fix>`.
- `manual` → `- [ ]` and `  > MANUAL: <short name> - <what the user must confirm>`.

## Step 4 — Report

Print this, nothing else beyond one summary line (`Checked X of Y items, Z marked N/A`):

```
Unchecked items:
- 1.1.6 - False information and features - Your app contains false information: ..... Update this information to ...
```

One line per unchecked item: id, short name, reason with the suggested fix. For `MANUAL` items write: `Cannot be verified from the project, please confirm: <what>`. State plainly that the manual items need the user's own check; once the user has confirmed one, they tick it in the file.
