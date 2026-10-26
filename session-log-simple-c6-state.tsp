// C6 — On flag + sync state (commands, hooks, sync, status, usage). Logical only.
// Flag: prompt-logs/.log_enabled holds the on-time (`on` writes it when absent, `off` deletes it). Sync state: prompt-logs/.sync.json, written only by sync (Q5).
// StateStore is a port owned by the use cases; its file implementation is an Interface Adapter.
namespace SessionLogSimple.C6State;

/** Content of prompt-logs/.log_enabled; the file's absence means off. */
model OnFlag {
  /** Sync cutoff: sessions with activity since this time are archived whole (Q12). */
  onAt: utcDateTime;
  /** The content could not be parsed, so onAt is the file's modification time; status says so. */
  fromFileTime?: boolean;
}

enum UnitState {
  /** Its session was read (see the session entry). */
  read,
  /** Changed before the on-time, or read and its session had no activity since the on-time; not read again until its fingerprint changes. */
  inactive,
  /** Could not be read (reason given); retried on every sync. */
  unreadable,
}

/** One entry per C2 SourceUnit key: what sync knows about one source. */
model UnitEntry {
  /** Absent = read again on the next sync (see saveSync). */
  fingerprint?: string;
  /** C2 session key, learned once (C2 sessionOf); absent while not learnable. */
  sessionKey?: string;
  state: UnitState;
  reason?: string;
}

enum WriteState {
  written,
  /** The last rewrite of its log or report failed (reason given); the files of an earlier write remain; retried on every sync. */
  skipped,
  /**
   * The source now holds less than the files do (reason given: fewer prompts, or fewer sub-agent rows / units): nothing is lost, the files stay
   * (a log that only gained prompts is rewritten and the saved report kept). Read again only when a source unit changed or is unreadable,
   * not for a render version; `status` lists it.
   */
  withheld,
}

/**
 * One entry per archived session (key: C2 session key): where its files are and what `usage` ranks by.
 * Created only by the session's first successful write; when that first write fails, its units are left unreadable with the reason,
 * so the next sync retries them.
 */
model SessionEntry {
  /** The first log path this session was written to, from C3 newLogPath; the only source of log-path reuse (C3). Never replaced once set. */
  logPath: string;
  state: WriteState;
  reason?: string;
  /** Recorded cwd, for matching the current project in `usage`. */
  projectDir?: string;
  lastPromptAt?: utcDateTime;
  lastActivityAt: utcDateTime;
  /** An orphan sub-agent archived alone; never a `usage` latest candidate. */
  orphan: boolean;
  /**
   * RENDER_VERSION of the program that last wrote the files. RENDER_VERSION is an integer constant in the package, raised by every change
   * that alters log or report text for the same source (readers, model, PricingRules, writer, prices.json); a unit test pins the
   * sha256 of prices.json to it, so a price change without a raise fails (S65). Sync rewrites, once, every written session whose
   * renderVersion is lower than its own, within the hook budget, saving each entry as it goes, so the next sync continues with the rest.
   * A higher renderVersion (written by a newer installed copy on the same log root) is never rewritten for its version, so two installed
   * versions do not undo each other's rewrites. A withheld session is not owed a rewrite for its version.
   */
  renderVersion: int32;
  /**
   * Number of source units the last write was built from: M in "source partly gone (N of M units)". A session read again for another reason is
   * not rewritten when its files hold the same or a newer render version, built from as many units, with no unit changed.
   */
  unitCount: int32;
  /**
   * Prompts the files hold (the log and the report both). A rewrite from a source with fewer prompts is not published (state withheld):
   * a reverted OpenCode session, a rewritten Codex rollout or a cut transcript never replaces the archive. Tokens and prices are not compared.
   * Absent in an entry written before the counts existed: nothing is protected then. After a failed write it is the larger of the old and
   * the new count, since either file may hold the new rows.
   */
  promptCount?: int32;
  /**
   * Sub-agent rows (nested ones too) the saved report holds. A read with as many or more prompts and fewer rows (a deleted Codex child, a cleaned
   * Claude sub-agent file, an unreadable unit) rewrites the log, so new prompts are archived, and keeps the saved report (which `usage` then prints with a warning): state withheld,
   * "source partly gone (N of M units)" (N units read, M counted at the last write) or, when no unit is missing,
   * "source holds fewer sub-agents than archived (N of M)". A rewrite owed only to a render version, a price file or deleted files, from a source no
   * unit of which changed and that has no fewer units, is published as read: a reader fix may drop rows.
   */
  subAgentCount?: int32;
  /**
   * The files were written while `prices.json` was unusable, so every price in the report is unknown: the next sync with a usable
   * price file rewrites the session once. Absent when the file was usable.
   */
  priceFileProblem?: boolean;
}

/** Content of prompt-logs/.sync.json. The archived count shown by status is the number of session entries. */
model SyncState {
  lastSyncAt?: utcDateTime;
  units: Record<UnitEntry>;
  sessions: Record<SessionEntry>;
}

interface StateStore {
  /**
   * Run by every command and every hook after the Python version, relocated-root and symlinked-root checks (C1), before the .log_enabled check.
   * If prompt-logs/.enabled exists: create .log_enabled exclusively (O_EXCL, on-time = now; an existing .log_enabled is kept), then remove .enabled,
   * ignoring that it is already gone. Old-release hooks, which check .enabled, then see logging off.
   * Removal: together with the C1 leftover list, in a later release.
   */
  migrateEnabled(): void;

  /** Reads .log_enabled; null when off. Writes nothing. */
  readOnFlag(): OnFlag | null;

  /** Creates .log_enabled exclusively; `on` while on (or a concurrent `on`) keeps the existing on-time. */
  writeOnFlag(flag: OnFlag): void;

  deleteOnFlag(): void;

  /** A missing file is an empty state; an empty or corrupt file is an empty state and status reports the reset. */
  loadSync(): SyncState;

  /**
   * Called after each session sync writes while the state is small (up to 500 entries), after batches of sessions beyond that (once the
   * sessions done since the last save reach 1/50 of the entries), and at the end, so a killed sync loses at most the session in
   * progress plus one batch.
   * Re-reads .sync.json and merges by key: this run's entries win, other runs' entries are kept, except
   * - a unit entry another run changed since this run loaded the state, to a different fingerprint: its fingerprint is dropped,
   *   so the next sync reads that unit again (two syncs at once can never leave an older log behind a newer fingerprint);
   * - a session entry's logPath, which is never replaced.
   * Then writes a unique temp, flushes it to disk and os.replace. If the replace fails, the old file stays and the temp is removed; the next sync
   * re-reads what was lost (fingerprints only make sync cheaper).
   */
  saveSync(state: SyncState): void;
}
