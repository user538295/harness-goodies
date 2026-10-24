// C3 — Archive output: Markdown log + saved usage report. Logical only.
// The Archive port and its input type UsageReport belong to the use cases (the sync use case and PricingRules, which builds
// UsageReport from a C2 Session and the C4 PriceBook); the writer (Interface Adapters) implements the port and only renders and writes.
// All times in the log and the report are local time with the UTC offset (user decision Q29): header and log entries `YYYY-MM-DD HH:MM:SS UTC+2`
// (`UTC-5`, `UTC+5:30`, plain `UTC` at offset 0; the offset valid at that instant), report rows `HH:MM:SS` under the offset in the report header,
// a row whose offset differs (daylight-saving change inside the session) adding its own. The zone is an injected value (the clock); the file
// name time stays UTC (Q6). A timezone change alone rewrites nothing, so a file keeps the zone of its last write.
// Log: header (harness, session id, project, start; orphans `sub-agent of <id> (parent not found)`), then per prompt `## <time>` and
// the prompt text, then `### <time> response` and the reply text. Rendered straight from the C2 Session; no second model.
// Report: session_<ts>_<id>.usage.txt beside the log, today's plain-text layout (Q7) without file paths and without the "internal helpers" line:
//   `session: <id>` with harness and project; per prompt `N. HH:MM:SS (working time HH:MM:SS) "<head>"` then
//   `est. used token: input: N, output: N, cache_create: N, cache_read: N, total_tokens: N, price: <price>, model: <models>, effort: <efforts>`
//   (`-` when no model or effort); per sub-agent `sub-agent: <type> (<id>), working time: HH:MM:SS` then its est. line, or
//   `tokens: not recorded by Cursor`; then `TOTAL (N requests, M sub-agents)`, `working time: HH:MM:SS` and its est. line, with models
//   and efforts of every row, sorted, unique, joined with "+"; Codex and Cursor prices labelled `API-equivalent`.
import "./session-log-simple-c2-session-model.tsp";

using SessionLogSimple.C2SessionModel;

namespace SessionLogSimple.C3ArchiveOutput;

union PriceStatus { priced: "priced", partial: "partial", unpriced: "unknown" }

/**
 * Rendered `$X.XX` (priced), `>= $X.XX (incomplete)` (partial: some requests have no rate), or `unknown` (no request has a rate).
 * No rate: unknown model, or a fast request of a model without fast rates; never $0.
 */
model Price {
  /** Priced part, rounded half-up to whole cents; 0 when unknown. */
  cents: int64;
  status: PriceStatus;
}

model PromptRow {
  at: utcDateTime;
  /** 00:00:00 when the prompt got no reply. */
  workingTime: duration;
  /** Newlines and tabs collapsed to one space, then the first 60 characters (code points). */
  promptHead: string;
  inputTokens: int64;
  outputTokens: int64;
  cacheWriteTokens: int64;
  cacheReadTokens: int64;
  totalTokens: int64;
  price: Price;
  /** Every model of the row, sorted, joined with "+"; fast requests marked ":fast". */
  modelNames: string;
  effort?: string;
}

model SubAgentRow {
  /** C2 SubAgent.agentId, shown as recorded; never a path. */
  agentId: string;
  type: string;
  depth: int32;
  workingTime?: duration;
  /** Absent for Cursor: shown as `not recorded by Cursor`. */
  totalTokens?: int64;
  price: Price;
  modelNames?: string;
  effort?: string;
}

model Total {
  /** Main session + every sub-agent (Cursor: main session only, labelled). */
  totalTokens: int64;
  /** Sum of the rows' cents shown above it, so TOTAL equals the sum of the rows. */
  priceCents: int64;
  /** Rendered `>= $X (incomplete)` when any row is partial or unknown, or Cursor sub-agent tokens are missing. */
  priceIncomplete: boolean;
  /** Codex and Cursor. */
  apiEquivalent: boolean;
  /** Sum of main-session per-prompt working times only. */
  workingTime: duration;
  /** Number of prompt rows (today's `TOTAL (N requests, M sub-agents)` wording). */
  requestCount: int32;
  subAgentCount: int32;
  /** Cursor: `tokens: main session only — Cursor does not record sub-agent tokens`. */
  label?: string;
}

model UsageReport {
  harness: Harness;
  sessionId: SafeId;
  projectDir?: string;
  prompts: PromptRow[];
  /** Depth-first, each level ordered as C2 SubAgent. */
  subAgents: SubAgentRow[];
  total: Total;
}

model WriteOutcome {
  path: string;
  written: boolean;
  /** Why the write failed; recorded in C6 for status (Q17). */
  reason?: string;
}

/**
 * Archive owns every path rule; the sync use case never builds a path. It asks newLogPath once for a session without a C6 entry,
 * keeps that path in the entry, and passes it back to write and readReport, which derive the report path beside it.
 * Log path: <log root>/<slug>/session_<YYYY-MM-DD_HH-MM-SS UTC>_<session-id>.md (Q6), report session_<ts>_<id>.usage.txt beside it.
 * slug = <basename, each char outside [A-Za-z0-9_-] replaced by "-", cut to 64; empty basename -> "root"; no recorded cwd -> "unknown">
 *        -<sha256(UTF-8 of normcase(cwd as the harness recorded it))[:12]>, with the injected path module (`os.path`; tests pass `ntpath`).
 * A session keeps the path in its C6 session entry, even if its start time or cwd is later read differently. Paths are never found
 * by searching the log root, so old-format logs (`session_<id>.md`, `session_<ts>_<id>.md`) are never touched (Q13).
 */
interface Archive {
  newLogPath(session: Session): string;

  renderLog(session: Session): string;

  /** The same text is saved by sync and printed by `usage`, also when saving failed (S33) and for a session never archived, whose report is computed live and never written (S68). */
  renderReport(report: UsageReport): string;

  /**
   * Writes the log at logPath and the report beside it. Whole-file rewrite of each: a unique temp `.<final name>.<pid>.<random hex>.tmp`
   * in the same folder, created exclusively, then os.replace.
   * UTF-8, LF line ends. On POSIX new folders are 0700, existing folders keep their mode, and every written file is 0600 (also a rewritten one).
   * Never writes through a symlink or a Windows junction (reparse tag SYMLINK or MOUNT_POINT, as today's lib/pathsafe.py): such a slug
   * folder or file gives written=false with the reason; a symlinked or junctioned harness root or log root fails the command before any
   * write (C1 exit 1; hooks exit 0 silently).
   * If os.replace fails (Windows, target open): remove the temp, keep the old file, return written=false with the failed path and the reason.
   * Each sync first removes temps of that pattern older than STALE_TEMP_SECONDS (1 hour) in the folders it writes (left by a killed sync).
   */
  write(logPath: string, logText: string, reportText: string): WriteOutcome;

  /** The saved report beside the log at logPath, for `usage`; absent when it does not exist. */
  readReport(logPath: string): string | void;
}
