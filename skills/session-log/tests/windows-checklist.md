# Windows checklist for session-log (scenario S31)

Run by the maintainer on a real Windows PC (Intel/AMD, x64) after release 2 (Q22). Nothing here runs in the automated suite, because the suite runs on POSIX. Every step is a `- [ ]` box; tick it only when the expected result happened exactly as written, and write any deviation (with the exact output) next to the step. When the checklist is done, update README so native Windows no longer reads "not yet tested" for the harnesses that passed (Q1), and file each failed step against the scenario it names (section 11).

## Conventions

- `<home>` is `%USERPROFILE%` (PowerShell: `$HOME`). A harness root is `<home>\.claude`, `<home>\.codex`, `<home>\.cursor`, `<home>\.omp\agent` or `<home>\.config\opencode`; its log folder, the "log root", is `<root>\prompt-logs`. `on` prints it as `Logs are saved under <log root>.`; `status` prints it as `Log root: <log root>`.
- `<skill dir>` is the installed skill directory, the one that holds `session_log.py`, `hook` and `hook.ps1`. The skill runs exactly one line: `python3 "<skill dir>/session_log.py" <harness> <arguments>`.
- The skill is invoked as `/session-log <arguments>`; in OMP as `/skill:session-log <arguments>`; for OpenCode and Cursor use the invocation form recorded by the S30 smoke run on the Mac (kept with the release's close-out evidence). Cursor's plugin is `cursor-goodies`; the other harnesses use `claude-goodies`, `codex-goodies` and `omp-goodies`, installed by the route S30 recorded for each.
- "As on macOS" means the wording of the Mac run of S30. There is no `sync` command: `on`, `status` and `usage` sync first while logging is on.
- Prompt text for every "one prompt" step: `Say hello. Győző ≥ 3` (it holds `ő` and `≥`). Send it in a new session of that harness, in the demo project, and wait for the reply before running `status` or `usage`.
- Changing `PATH`, app execution aliases or environment variables only affects processes started afterwards: quit the harness completely (check Task Manager), change the setting, then start the harness again from a new terminal (CLI harnesses) or the Start menu (Cursor).
- Cursor's hooks write a record for every chat event, so run Cursor's `on`, `off`, `status` and `usage` from a terminal with the skill line (`python3 "<skill dir>\session_log.py" cursor <arguments>`), never inside a Cursor chat; a chat would leave records after the on-time and spoil the hook checks.
- `Archived sessions:` counts every session with activity since the on-time, so the session that ran `on` counts too: run `on`, the prompt and `status` in the same session on a fresh log root and expect 1.
- Every state a section changes (`PATH`, aliases, `py` default, `.records`, on-time) is restored or reset by the section's last box; do not carry it into the next section.
- A step that needs a harness to have archived a session needs a prompt sent AFTER `on` (sync only archives sessions with activity since the on-time); a Claude Code session start syncs the sessions of earlier runs, so create the session first, quit, then start a new one.

## 0. Preparation and backup

- [ ] Windows version and architecture noted (Settings > System > About); the architecture is x64 (Intel/AMD).
- [ ] Backed up before any change: each harness's `prompt-logs` folder (copy it away), the current user `PATH` (`[Environment]::GetEnvironmentVariable('Path','User')` saved to a file), the state of the `python3` and `python` app execution aliases, and any harness config you will touch. Restore them after the run.
- [ ] Windows PowerShell 5.1 (`powershell`) starts; PowerShell 7 (`pwsh`) is hidden from `PATH` for the 5.1 steps (`Get-Command pwsh` finds nothing).
- [ ] Python 3.14 or newer is installed with the Python install manager from python.org/downloads/windows; `py -3 --version` and `python3 --version` print 3.14 or newer, and an older Python (for example 3.12) is installed or installable for the `Python 3.14+ required` step of section 6, and the `py` launcher default (`PY_PYTHON3` or the install manager's default) is noted so it can be restored.
- [ ] Claude Code, Codex, Cursor, OMP and OpenCode are installed with the session-log skill of release 2.
- [ ] Each harness starts with logging off: `<root>\prompt-logs\.log_enabled` and `.enabled` do not exist (move the backed-up `prompt-logs` away; nothing logs after install).
- [ ] A demo project exists at `C:\work\demo`, and one harness can be started with the drive root `C:\` as its working folder (if a harness refuses `C:\`, note which and use another for section 8).

## 1. Each harness: `on`, one prompt, `status`, `usage`

Repeat the whole section once per harness (Claude Code, Codex, Cursor, OMP, OpenCode) and tick each box per harness: copy the box as `- [ ] <harness>: ...` in your notes so the result of each harness is recorded separately. Cursor under Windows PowerShell 5.1 is section 2.

- [ ] `on` prints `Session logging is on since <time>.` and `Logs are saved under <log root>.`, prints no line starting `python3 does not run Python 3.14 or newer`, and `<log root>\.log_enabled` exists.
- [ ] `on` run a second time prints `Session logging is already on, since <the same time>.`
- [ ] One prompt is sent and answered.
- [ ] `status` prints `Session logging is on since <time>.`, `Log root: <log root>`, a `Last sync:` line and `Archived sessions: 1`, and no `Unreadable or skipped items:` block.
- [ ] `usage` prints the report of that session; `usage --latest` and `usage <session-id>` print the same text. `<log root>\<slug>\session_<UTC time>_<id>.md` and `session_<UTC time>_<id>.usage.txt` exist side by side; times inside carry a UTC offset; both files are UTF-8 with LF line ends (check in an editor that shows encoding and line ends).
- [ ] `ő` and `≥` read correctly in the `.md` log and in the report (both hold the prompt text; the report's prompt row shows its first characters); Cursor under 5.1 is exempt (section 2).
- [ ] `off` prints `Session logging is off.` and `Logs under <log root> are kept.`; `.log_enabled` is gone and the log files stay. Run `on` again before the next section.
- [ ] OpenCode only: `status` finds the database in `<home>\.local\share\opencode` (`opencode.db`, else the newest `opencode-*.db`) and lists no "no OpenCode database" item. Do not set `XDG_DATA_HOME` (a moved data folder makes every command exit 1 as a relocated root); `OPENCODE_DB` must name a database inside that folder.
- [ ] OMP only: the skill runs as `/skill:session-log`, and OMP loads no session-log extension at start (S28).

## 2. Cursor under Windows PowerShell 5.1 without pwsh

- [ ] Start from a fresh Cursor log root (move `<home>\.cursor\prompt-logs` away). With `pwsh` hidden from `PATH`, quit and restart Cursor in the demo project, run `on` from a terminal, and start a new chat: the chat starts normally with no hook error and nothing printed by the `sessionStart` hook; no file is added under `.records\` by it (S34).
- [ ] Send the prompt and wait for the reply, then run `status` from a terminal: `Archived sessions: 1`, and the line `Cursor has recorded no hook events since logging was turned on.` is NOT printed.
- [ ] `<home>\.cursor\prompt-logs\.records\<conversation_id>\` holds `<arrival_ns>-<event>-<pid>.json` files for `beforeSubmitPrompt`, `afterAgentResponse` and `stop`, and no leftover `*.tmp` older than an hour.
- [ ] Under 5.1 the non-ASCII prompt text may arrive with replacement characters in the record and log; this is accepted. The session is still recorded, and `status` lists no `.records\.invalid` item.
- [ ] `usage` from a terminal prints the Cursor report as on macOS: the price marked `(API-equivalent)`, shown as `≥ $<amount> (incomplete)`, no sub-agent tokens; `≥` prints correctly in the console and in the saved `.usage.txt`.
- [ ] Reset: `off`, and move `<home>\.cursor\prompt-logs` away again.

## 3. Cursor with no Python at all (5.1; no `py`, `python3` or `python`)

- [ ] Reset the hook state from a terminal (before hiding anything): `off`, delete `<home>\.cursor\prompt-logs\.records`, then `on`.
- [ ] Hide every interpreter from Cursor's `PATH` (remove them from the user `PATH`, disable the `python3` and `python` app execution aliases; `py.exe` lives in `C:\Windows`, so launch Cursor from a terminal whose `PATH` has no `C:\Windows` copy of `py` visible, or rename `py.exe` as administrator and restore it afterwards), then restart Cursor.
- [ ] Start a new chat and send one prompt: the chat starts and works, with no hook error dialog and no hook output (`hook.ps1` exits 0 when it finds no interpreter).
- [ ] Restore `PATH`, then run `status` from a terminal: it prints `Cursor has recorded no hook events since logging was turned on.`, and `.records\` was not created. Restore every interpreter, alias and `PATH` change of this section.

## 4. Cursor hook interpreter order (Q27)

- [ ] Reset (`off`, delete `.records`, `on`, from a terminal) before each bullet below. With `py -3` (3.14+) and the Store `python3` stand-in both present (section 5 setup B), a Cursor chat and prompt record files under `.records\` (`hook.ps1` takes `py -3` first and never reaches the stand-in); `status` does not print the no-hook-events line.
- [ ] With `py` hidden, `python3` hidden and a 3.14+ `python` on `PATH`, a Cursor chat records files too (`hook.ps1` falls back to `python`).
- [ ] Make `py -3` resolve to an older Python (set `PY_PYTHON3=3.12` for the legacy launcher, or the install manager's default setting; `py -3 --version` must print 3.12) while `python3` is a working 3.14+. With that and the reset above: nothing is recorded (once `py -3` is found, no other interpreter is tried; the hook exits 0) and `status` (from a terminal) prints the no-hook-events line. Restore the `py` default afterwards.

## 5. No `python3`, and the Store stand-in first on `PATH` (S24, Q2, Q3)

Run setup A, then setup B; in each, run the `on`/`status` box, then the Claude session-start box. Restore a working `python3` before the Q3 box.

- [ ] Setup A, no `python3`: remove `python3` from `PATH`, disable its app execution alias (Settings > Apps > Advanced app settings > App execution aliases), keep `py -3` available (restored after section 6).
- [ ] Setup B, Store stand-in first: re-enable the `python3` alias and put `%LOCALAPPDATA%\Microsoft\WindowsApps` before the real Python on `PATH`; `python3 -c "print(1)"` opens the Store or fails instead of printing `1`.
- [ ] In each setup run `on` and then `status` (both, S24 names both): after the skill falls back to `py -3`, each prints its normal output followed by `python3 does not run Python 3.14 or newer; fix: install Python 3.14 or newer with the Python install manager from python.org/downloads/windows, then make sure it is the default python3 (py list) and the Microsoft Store python3 app execution alias does not shadow it (Settings > Apps > Advanced app settings > App execution aliases)` (the text of `PROBE_FIX` in `sessionlog/python_probe.py`); the fix text is actionable as written and no traceback appears.
- [ ] In each setup, restart Claude Code and start a new session: it starts at once, with at most a non-blocking hook error shown (no blocking dialog, no hang); no log is written by that start.
- [ ] Q3: restore a working `python3` (the Python install manager alias pointing at Python 3.14+), turn logging on in Claude Code, send a prompt in session A, quit, run no `on`, `status` or `usage` (they would sync A and void the proof), then start session B in the same project and wait up to 30 s (the hook is asynchronous): session A's log appears under `<home>\.claude\prompt-logs\<slug>\` with no error shown, which proves Claude's no-shell hook (`python3 -I -c ... session_log.py hook claude SessionStart`) started the `python3` app alias. If it did not start, record the outcome: the hook must change to shell form, and which shell runs it (Git Bash, or PowerShell when Git Bash is missing) goes back to the user.

## 6. Fallback chain `py -3`, then `python` (S25, S55)

- [ ] `python3` missing (setup A) and `py -3` present: `status` runs, via the skill line with `py -3` in place of `python3`.
- [ ] No `py` either (hide `py.exe` from `PATH`) and `python` on `PATH` pointing at Python 3.14+: `status` runs via `python`.
- [ ] None of `python3`, `py -3` and `python` starts: the skill tells the user that Python 3.14 or newer is required (python.org/downloads) and stops, with no traceback.
- [ ] With an older Python (for example 3.12) first on `PATH` as `python3`: the command prints `session-log: Python 3.14+ required (found 3.12)` and `session-log: fix: install the Python install manager from python.org/downloads/windows`, exits 1, and `on` creates no `.log_enabled`; the skill reports that message as it is and tries no other interpreter. Restore `PATH` and the `py` default.
- [ ] No announced skill directory: use a harness chat where the skill directory is not announced (for example paste the SKILL.md text into a chat of a harness that has not loaded the skill, or tell the model to ignore the announced directory): the skill does not run the bash locator; it asks for `SESSION_LOG_HOME`. After setting `SESSION_LOG_HOME` to `<skill dir>` and telling it the value, the command runs. If no harness can produce this case, note that and tick it from the SKILL.md wording alone (Run it, step 3).

## 7. Cursor installed under a path with spaces and an apostrophe (`hook.ps1`)

- [ ] Uninstall any earlier `cursor-goodies` install, `off`, move `<home>\.cursor\prompt-logs` away, then `on`. Put a copy of the `cursor-goodies` plugin in `C:\Users\<you>\My 'Tools'\cursor-goodies` (spaces and an apostrophe; none of `$`, `` ` ``, braces, parens, `|`, `;`, `,`, `<`, `>`) and install it in Cursor by the local route S30 recorded; run `on` (from a terminal), start a chat and send one prompt: the hooks run (`.records\` fills, `status` does not print the no-hook-events line) and no hook error is shown.
- [ ] With `session_log.py` moved out of that install, a new chat starts normally, prints nothing and records nothing (`hook.ps1` exits 0 when the script is missing). Put the file back.
- [ ] The skill line, with `<skill dir>` replaced by that path in double quotes, runs under PowerShell: `python3 "C:\Users\<you>\My 'Tools'\cursor-goodies\skills\session-log\session_log.py" cursor status` prints the status.

## 8. A session recorded in `C:\` (S38)

- [ ] Start a session in a harness with `C:\` as its working folder, send the prompt, run `status`: the log lands in `<log root>\root-<12 hex>\session_<UTC time>_<id>.md` and its `- project:` header line shows `C:\`.
- [ ] The demo project lands in `demo-<12 hex>`. The slug hash is taken from the working folder as the harness recorded it, with letter case folded on Windows: start a second session in `C:\WORK\DEMO` (same folder, other case, `cd` into it by that spelling) and its log's `- project:` line shows `C:\WORK\DEMO`; the folder name keeps the typed case (`DEMO-<12 hex>`), but its 12-hex suffix equals the one of `demo-<12 hex>` (the hash folds case on Windows).

## 9. A log or report open in a program that locks it (S33)

- [ ] Lock the log: in PowerShell run `$h = [System.IO.File]::Open('<log path>', 'Open', 'Read', 'None')` on a session's `.md` file; send one more prompt in that session and run `status`: the log is named under `Unreadable or skipped items:` with its path and the reason Windows gave (note the text, for example a sharing violation), and no `*.tmp` is left in the folder.
- [ ] While still locked, run `usage`: it prints the report text for the session (the same text the next save will write).
- [ ] Release it (`$h.Dispose()`) and run `status` again: the next sync writes the log and report and the skipped item is gone.
- [ ] Repeat with the `.usage.txt` report locked instead of the log: `status` names the report path with its reason, no `*.tmp` is left, and `usage` still prints the report text; releasing the lock and running `status` saves it.

## 10. Junction in place of a slug folder, and the skill command in PowerShell

The junction steps are the Windows part of the old `test_portability.py` test `test_open_directory_refuses_a_linked_component` (suite runs on POSIX, S62).

- [ ] Junctioned slug folder: with one archived session and the harness restarted for the next prompt, close the harness, then in `cmd.exe` run `ren "<log root>\<slug>" "<slug>-real"` and `mklink /J "<log root>\<slug>" "<log root>\<slug>-real"` (no administrator rights needed). Reopen the harness, add a prompt to that session and run `status`: under `Unreadable or skipped items:` the log file inside the junctioned folder is named as `<log path>: its folder is a symbolic link or junction, so it is left alone and nothing is written through it`, and nothing new appears inside `<slug>-real`.
- [ ] Remove the junction with `cmd /c rmdir "<log root>\<slug>"` (removes only the link; never `rmdir /s` and never PowerShell `Remove-Item -Recurse`), rename `<slug>-real` back to `<slug>`, and run `status`: the next sync writes the log and the skipped item is gone.
- [ ] Junctioned log root: in `cmd.exe` replace a harness's whole `prompt-logs` folder with a junction the same way (rename it, `mklink /J`); any command exits 1 with `session-log: <log root> is a symbolic link or junction, so it is left alone and nothing is written through it`, and `on` does not say logging is on. Remove the junction and restore the folder afterwards.
- [ ] In Windows PowerShell 5.1 run `python3 "<skill dir>\session_log.py" claude status` (then with `/` separators, as the skill writes the line): the same status as through the harness, no quoting error, no traceback. Run it also in `cmd.exe`, and in PowerShell 7 if installed.
- [ ] In `cmd.exe` run `cd /d C:\work\demo` and then `python3 "<skill dir>\session_log.py" claude usage --latest > usage.txt` (cmd keeps the bytes as UTF-8; PowerShell 5.1's `>` would re-encode to UTF-16): the file is UTF-8 with LF line ends and `≥` and `ő` from the prompt row are intact; the console shows `≥` and `ő` correctly in Windows PowerShell 5.1 and Windows Terminal.
- [ ] A bad argument, `python3 "<skill dir>\session_log.py" claude bogus`, prints one `session-log: ...` line, no argparse `usage:` line, and exits 2 (`echo %ERRORLEVEL%` in `cmd.exe`).

## 11. Close-out

- [ ] Every box ticked, or each failure written next to its step with the exact output, and the backups of section 0 restored.
- [ ] README's Windows lines updated by the result: each harness that passed loses "not yet tested"; failures stay marked.
- [ ] Failed steps filed against their scenarios: S24 and S25 (Python probe and fallback chain), S31 (Claude no-shell hook and Q3, Cursor 5.1 behaviour), S33 (locked file), S38 (`C:\` slug and case folding), S62 (junction), S21 and S56 (Cursor `hook.ps1`), S34 (`sessionStart` hook), S28 (OMP extension), S55 (old-Python message), Q27 (hook interpreter order).
