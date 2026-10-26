// C1 — Command surface, hook entry and Cursor launchers. Logical only.
// Entry: skills/session-log/session_log.py (Q4), owned by Backend. It is the composition root and the controller: it checks Python,
// parses argv, builds every adapter, calls one use case per command and hands the result to the presentation module (Frontend), which renders it.
// Commands: `session_log.py <harness> on|off|status|usage [--latest|<id>]`. Hooks: `session_log.py hook <harness> <event>`, checked before argument parsing.
// The entry file uses only syntax Python 3.4 parses (no f-strings, variable annotations, async, walrus or `match`; `py -3` may pick an old
// Python; S55 runs it on 3.4) and checks Python >= 3.14 before importing the package: commands then print
// `session-log: Python 3.14+ required (found X.Y)`, then `session-log: fix: <hint>` with one install hint for the running platform
// (macOS: `brew install python@3.14` or python.org/downloads, then make `python3` resolve to it; Windows: the Python install manager from
// python.org/downloads/windows; other: the package manager, pyenv or uv, then make `python3` resolve to it), and exit 1; hooks exit 0 silently.
// The entry reconfigures stdout and stderr to UTF-8 before anything prints (S54).
// Order for every command and hook: Python version check, relocated-root check, symlinked harness root or `prompt-logs` check,
// the .enabled -> .log_enabled migration (C6), then the .log_enabled check. So a refused root is never written, not even by the migration.
// Any unexpected exception: commands print `session-log: internal error: <type>: <message>` and exit 1; hooks exit 0 silently. Never a traceback.
// Every stderr text (Python version, internal error, argument errors, runtime errors) belongs to the entry, not to the presentation module:
// a use case raises a runtime error with its facts, and the entry prints it as `session-log: <msg>`, exit 1.
import "./session-log-simple-c2-session-model.tsp";

using SessionLogSimple.C2SessionModel;

namespace SessionLogSimple.C1Cli;

enum Command { on, off, status, usage }

/**
 * `usage` target. Absent and `latest` mean the same: the root session of the current project with the newest prompt, ranked from the
 * C6 session entries (no harness file is read for ranking). Only when the project has no archived session is the store read:
 * every never-archived root session is ranked by its newest prompt, the newest source unit breaking a tie (S68).
 * Current project: the sessions whose recorded cwd equals the command's cwd; if none, those of the nearest parent folder of the cwd
 * that has any (so `usage` works from a subfolder, as today's Cursor `usage`). Paths are compared under os.path.normcase, or both after
 * os.path.realpath.
 * Sessions without a prompt rank below every session with one; if none has a prompt, the newest last activity wins; sub-agents and
 * orphan sub-agent logs are never candidates.
 * sessionId: an exact id wins; otherwise a prefix shared by exactly one session, archived or listed by the reader's scan together;
 * a prefix shared by several is ambiguous (exit 1).
 */
union UsageTarget { latest: "latest", sessionId: string }

model Invocation {
  /** Passed explicitly by the caller; never inferred from paths or environment. */
  harness: Harness;
  command: Command;
  usageTarget?: UsageTarget;
}

/** Plain text for the user; errors go to stderr as `session-log: <msg>` (own text, never argparse's `usage:`/`error:` lines). */
model CommandResult {
  stdout: string;
  stderr: string;
  /**
   * 0 ok, 1 runtime error (unknown or ambiguous session id, no session, archived or in the store, for this project, an unreadable store source,
   * unwritable log root, symlinked or junctioned harness root or `prompt-logs`, relocated root), 2 bad arguments.
   */
  exitCode: 0 | 1 | 2;
}

interface SessionLogCli {
  /**
   * While logging is on, every command syncs first (`off` syncs, then turns off). While off, no command syncs.
   * A command's sync has no time budget; if the harness's tool timeout kills it, the progress saved after each session (C6)
   * lets the next command continue.
   */
  run(invocation: Invocation): CommandResult;
}

// ── Presentation <-> Use Cases seam: use cases return data; the presentation module (Frontend) renders the stdout text.
// Use cases get by constructor the ports SessionReader of the invoked harness (one reader per run, C2), Archive (C3), PriceBook (C4),
// StateStore (C6), plus plain injected values: the clock (now and the local time zone), the platform name, the path module (`os.path`; tests pass `ntpath`; it also
// gives normcase and realpath), the Python probe runner and the leftover scan (Interface Adapters; tests pass fakes). No other I/O.

/**
 * An old-release leftover of the current harness: reported, never deleted. The leftover scan (Interface Adapters) holds this list,
 * the C1 leftover list; the use cases call it. A leftover is any existing:
 * hook entry in ~/.claude/settings.json, ~/.codex/hooks.json or ~/.cursor/hooks.json whose command runs
 * adapters/claude/claude_hook.sh, adapters/native/session_log_hook.py or ~/.claude/scripts/prompt_log_*;
 * ~/.claude/scripts/prompt_log_* file; seeded copy <harness root>/skills/session-log holding install.sh or the
 * `universal-session-log: managed` marker; OpenCode plugins/session-log.js, plugins/session-log-omp.js,
 * scripts/session_log_usage.sh, commands/session-log.md, commands/session-log-omp.md; OMP
 * extensions/session-log.js, extensions/session-log-omp.js, skills/session-log-omp; the old state folder
 * <harness root>/session-log/ (version stamps, plugin-root, runtime markers, locks); ~/.claude/session-maps/.
 * Not reported: inert files inside the log root beside old-format logs (`.enabled.lock`, OMP `.*.dedupe`), which stay untouched (Q13).
 */
model Leftover {
  path: string;
  /** One line, e.g. "move it aside". */
  fix: string;
}

/** Windows only: `python3 -c` exits 0 only on Python >= 3.14 (always with arguments, never bare). */
model ProbeResult {
  ok: boolean;
  fix?: string;
}

model UnreadableItem {
  path: string;
  reason: string;
}

model OnResult {
  /** `on` while on keeps the existing on-time. */
  alreadyOn: boolean;
  onAt: utcDateTime;
  logRoot: string;
  leftovers: Leftover[];
  probe?: ProbeResult;
}

model OffResult {
  /** `off` while off does nothing else: no sync, exit 0. */
  alreadyOff: boolean;
  logRoot: string;
}

/** Built from .log_enabled and .sync.json (C6) after the sync, if any. */
model StatusResult {
  on: boolean;
  onAt?: utcDateTime;
  /** Set when .log_enabled could not be parsed and its file time is used instead. */
  onAtFromFileTime?: boolean;
  logRoot: string;
  lastSyncAt?: utcDateTime;
  /** The number of C6 session entries: every session this tool has archived under the log root, also one whose last rewrite failed. */
  archivedSessionCount: int32;
  /**
   * Unreadable sources (Cursor: also `.records/.invalid/` with its call count), files skipped because the write failed, sessions
   * withheld because their source holds less than the archive (C6 withheld: fewer prompts, partly gone), and sessions whose log or report
   * is missing from disk with no source left to rebuild it, with reasons. While the log root cannot be written, `status` and `usage` go on
   * with what is archived and print one warning line on stderr; the exit code stays 0.
   */
  unreadable: UnreadableItem[];
  /** Cursor only. */
  cursorRecordedSinceOn?: boolean;
  priceFileProblem?: string;
  /** .sync.json existed but was empty or corrupt, so it was rebuilt. */
  syncStateReset: boolean;
  leftovers: Leftover[];
  probe?: ProbeResult;
}

model UsageResult {
  /**
   * Exactly the C3 report text: the saved file; when this run's write failed, the same rendering (S33); or, for a session never archived,
   * the same rendering of a report computed live from the store (reader, PricingRules, Archive.renderReport), never saved (S68).
   * While off an archived session's report is the one saved at the last sync.
   */
  reportText: string;
}

interface CommandUseCases {
  on(harness: Harness): OnResult;
  off(harness: Harness): OffResult;
  status(harness: Harness): StatusResult;
  usage(harness: Harness, cwd: string, target?: UsageTarget): UsageResult;
}

/** What one sync hands back to its caller. */
model SyncOutcome {
  /** Report text per C2 session key whose report could not be saved in this run (S33); `usage` prints it. */
  unsavedReports: Record<string>;
}

/**
 * Sync of the invoked harness's store, called by the command use cases while on and by the hook entry.
 * budgetSeconds absent (commands): no time budget. Hook entry: HOOK_SYNC_BUDGET_SECONDS (20 s, below the 30 s hook timeouts);
 * the sync stops starting new sessions after it, and the progress saved after each session (C6) lets the next sync continue.
 */
interface SyncUseCase {
  sync(harness: Harness, budgetSeconds?: float64): SyncOutcome;
}

// ── Hook entry

/** Only Claude and Cursor have hooks. */
enum HookHarness { claude, cursor }

enum ClaudeHookEvent { SessionStart }

enum CursorHookEvent { sessionStart, beforeSubmitPrompt, afterAgentResponse, stop, subagentStop }

/**
 * Never blocks a prompt, never prints, never exits 2: always exit 0, also for malformed argv, unknown harness or event (nothing written).
 * Order: as for commands (header); exit 0 when a root is refused or logging is off.
 * Registration: Claude SessionStart exec form, no matcher, `async: true` (runs in the background, code.claude.com hooks.md
 * "Run hooks in the background") and `timeout` 30 (not enforced while async; it bounds the start if a Claude Code version ignores
 * `async`; S23 checks a real start); Cursor five events, each `timeout` 30.
 * Claude command: `python3` with args `-c <one-line guard> <script path> hook claude SessionStart`; the guard runs session_log.py only when
 * it exists and otherwise exits 0, because `python3 <missing script>` exits 2, which blocks under Cursor's import of the hook (F1, S59).
 * The hook entry calls SyncUseCase with HOOK_SYNC_BUDGET_SECONDS. Cursor `sessionStart` first removes record temps older than 1 hour (C5).
 */
model HookInvocation {
  harness: HookHarness;
  event: ClaudeHookEvent | CursorHookEvent;
  /** Raw stdin bytes; decoded as UTF-8 with errors replaced and an optional BOM stripped. */
  input: bytes;
}

interface SessionLogHook {
  handle(invocation: HookInvocation): void;
}

// ── Cursor launchers (Frameworks & Drivers -> hook entry)

/**
 * `hook` (POSIX sh, git mode 100755) and `hook.ps1`, beside session_log.py.
 * Cursor command: "${CURSOR_PLUGIN_ROOT}/skills/session-log/hook" cursor <event> — braced, double-quoted, nothing after it.
 * First, without starting Python: when neither `<home>/.cursor/prompt-logs/.log_enabled` nor `.enabled` exists, exit 0 (logging off, as
 * today's hook commands do). Otherwise both pass argv and stdin bytes unchanged to `<python> <their dir>/session_log.py hook cursor <event>`.
 * Interpreter: `hook` uses python3; `hook.ps1` uses the first found of `py -3`, `python3`, `python` (no second try when the one found is
 * older than 3.14: the hook then records nothing and status shows no hook records since on).
 * Both exit 0, silently, also when the script or every interpreter is missing.
 */
model LauncherCall {
  argv: string[];
  stdin: bytes;
}

interface CursorLauncher {
  run(call: LauncherCall): 0;
}
