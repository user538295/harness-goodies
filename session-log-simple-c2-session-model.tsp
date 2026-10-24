// C2 — Normalized session model and reader port (readers -> sync use case -> writer). Logical only.
// SessionReader is a port owned by the sync use case; the five readers (Interface Adapters) implement it. A run builds only the
// reader of the invoked harness: one reader per run, so a sync never reads another harness's store.
// Each reader gets only its own dependencies by constructor, built in the composition root (tests pass sandbox values):
//   ClaudeReader(root), CodexReader(root), OmpReader(root), OpenCodeReader(databasePath), CursorReader(recordsDir).
// OpenCode databasePath, chosen as today (adapters/opencode/session_log_usage.sh:67-108) in ~/.local/share/opencode: OPENCODE_DB (relative = inside
// that folder; `:memory:` is a runtime error); else OPENCODE_CHANNEL: `latest`, `beta`, `prod` or OPENCODE_DISABLE_CHANNEL_DB=1/true give
// opencode.db, any other channel opencode-<channel, chars outside [A-Za-z0-9._ -] replaced by "-">.db; else opencode.db if it exists;
// else the newest regular opencode-*.db; else none ("no OpenCode database").
// Readers depend on this model only; they know nothing of the writer or of the sync state.
// Record rule for every reader: record types the reader does not use (e.g. Claude `custom-title`, `atis-latch`, `queue-operation`) and
// lines that do not parse are skipped without a report. A unit is unreadable only when it cannot be opened or decoded, is not a regular
// file, no line of it parses, or (Codex) its first line is not `session_meta`.
// Parent cycles: sessions whose OpenCode `parent_id` or OMP `parentSession` chain loops are listed unreadable ("parent cycle") and not archived.
namespace SessionLogSimple.C2SessionModel;

/** The five supported harnesses; passed explicitly, never inferred. Defined only here; C1, C3 and C5 import it. */
enum Harness { claude, codex, cursor, omp, opencode }

/**
 * Id rule for every id that becomes part of a path: a session id (also an orphan sub-agent archived alone) and a Cursor
 * conversation id. 1-128 of [A-Za-z0-9_-] (no dots, no separators) and not a Windows device name (CON, PRN, AUX, NUL,
 * COM1-9, LPT1-9, any case). A session whose id fails the rule is not archived; status lists it as unreadable, reason "unsafe id".
 * Sub-agent ids never become paths, so they are not checked: a sub-agent always keeps its row and its tokens.
 */
@pattern("^[A-Za-z0-9_-]{1,128}$")
scalar SafeId extends string;

/**
 * One request's tokens. Codex: `input_tokens` includes `cached_input_tokens` (codex protocol.rs `non_cached_input`),
 * so inputTokens = max(0, input_tokens - cached_input_tokens) and cacheReadTokens = cached_input_tokens; no token is counted twice.
 * Codex requests: when the rollout has `token_usage_record` lines, each one is one request (its `usage`; they also cover compaction
 * calls) and `token_count` events are ignored. Otherwise each `token_count` event whose `info.total_token_usage` grew is one request
 * whose tokens are the growth (per field); events with null info or an unchanged total are skipped, and a `token_count` before the
 * rollout's first `task_started` (a forked thread's inherited total) only sets the starting total. `last_token_usage` is never summed:
 * events repeat it. (Checked on the 709 local rollouts: totals never shrink; repeated events made summing `last_token_usage` overcount.)
 */
model RequestUsage {
  modelName: string;
  effort?: string;
  fast?: boolean;
  /** Input tokens not read from cache. */
  inputTokens: int64;
  outputTokens: int64;
  cacheReadTokens: int64;
  /** Harnesses without a 5m/1h split (Codex `cache_write_input_tokens`, Cursor `cache_write_tokens`) put every cache-write token here. */
  cacheWrite5mTokens: int64;
  cacheWrite1hTokens: int64;
  /** Cost recorded by the harness (OMP, OpenCode); 0 means free. Absent = compute from the price file. */
  recordedCostUsd?: float64;
}

model Prompt {
  submittedAt: utcDateTime;
  text: string;
  replyAt?: utcDateTime;
  /** Final reply text for this prompt; tool calls are not included. */
  replyText?: string;
  /** Prompt submitted -> last reply of that turn; 0 when there is no reply. */
  workingTime?: duration;
  requests: RequestUsage[];
}

/** Sub-agents belong to the session, not to a prompt: the report lists them after the prompts, as today. */
model SubAgent {
  /** Shown in the report only, never used in a path. */
  agentId: string;
  type: string;
  /**
   * Rows at each level are ordered by startedAt, then agentId, for every harness.
   * Cursor: arrival time of its subagentStop record minus its duration_ms.
   */
  startedAt: utcDateTime;
  workingTime?: duration;
  /** Empty with tokensNotRecorded=true for Cursor. */
  requests: RequestUsage[];
  tokensNotRecorded: boolean;
  /**
   * Nested sub-agents. Codex: a thread nests under the thread its `parent_thread_id` names; when that thread is not in the session,
   * it sits at the top level.
   */
  subAgents: SubAgent[];
}

model Session {
  harness: Harness;
  sessionId: SafeId;
  /** cwd of the session's first record (Cursor: workspace_roots[0] of its first record); absent when none was recorded. */
  projectDir?: string;
  startedAt: utcDateTime;
  /**
   * Newest prompt or reply time of the session or any of its sub-agents (Cursor: newest record arrival).
   * Sync archives the session, whole, when this is at or after the on-time.
   */
  lastActivityAt: utcDateTime;
  /** Set for a sub-agent session whose root is gone; it is archived alone and labelled. */
  orphanOfParentId?: string;
  prompts: Prompt[];
  subAgents: SubAgent[];
}

/** One cheaply listed source: a file (Claude main or sub-agent file, Codex rollout, OMP file), one OpenCode session row, or one Cursor conversation folder. */
model SourceUnit {
  /** Path, except Codex: the path without `.zst`, so compressing a rollout keeps its key. */
  key: string;
  /**
   * Files: size + mtime_ns. Codex: mtime_ns only (compression keeps mtime, codex rollout/src/compression.rs:902).
   * OpenCode: row count + max seq + max time_updated of the session's messages (assistant rows are updated in place).
   * Cursor: record count + newest record name.
   */
  fingerprint: string;
  /** Newest change time; the sync's pre-filter: a unit changed before the on-time had no activity since then. */
  modifiedAt: utcDateTime;
}

model UnreadableSource {
  harness: Harness;
  path: string;
  /** e.g. unknown format, not a regular file, a corrupt `.zst`, unsafe id, parent cycle, database busy, no OpenCode database. */
  reason: string;
}

/**
 * What scan found. Sources that are not regular files (FIFO, device) are listed unreadable and never opened; so are
 * `.records/.invalid/` (with its call count) and a missing, busy or v1-only OpenCode database.
 */
model ScanResult {
  units: SourceUnit[];
  unreadable: UnreadableSource[];
}

model ReadResult {
  session?: Session;
  unreadable: UnreadableSource[];
}

/** One implementation per harness. */
interface SessionReader {
  /** Lists every unit with its fingerprint; no session content is parsed (files: stat; OpenCode: one aggregate query; Cursor: folder listing). */
  scan(): ScanResult;

  /**
   * Session key (`<harness>:<root id>`, naming the root session) of a unit the sync state does not know yet; asked once per unit,
   * the answer is kept (C6). From the path (Claude `<session>/subagents/`, OMP child folders `X/` and `X/Name/` beside root `X`,
   * Cursor folder) or the database (OpenCode `parent_id`). OMP root id: the root file stem `<ts>_<uuid>` after its first `_`, which is
   * the `session` header id. Codex reads the rollout's first line (a `.zst` rollout through `compression.zstd`): the root id is
   * `session_meta.session_id` (equal to `id` for a root; nested sub-agents name a child in `parent_thread_id`, but `session_id` still
   * names the root); without `session_id`, `parent_thread_id`, else `id`. Absent: not learnable now; asked again on the next sync.
   */
  sessionOf(unit: SourceUnit): string | void;

  /** Reads one session whole from all of its units (root, children, sub-agent files). */
  read(sessionKey: string, units: SourceUnit[]): ReadResult;
}
