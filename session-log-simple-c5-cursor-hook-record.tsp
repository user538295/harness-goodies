// C5 — Cursor hook record: file format between the hook entry (Frameworks & Drivers, writes) and CursorReader (Interface Adapters, reads).
// No port: no use case touches records; CursorReader gets the records folder by constructor (C2) and treats it as Cursor's harness store.
// Path: prompt-logs/.records/<conversation_id>/<arrival_ns>-<event>-<pid>.json (Q8).
// Written to a unique `.<name>.tmp` created exclusively (O_EXCL), then renamed to its final name, so a reader never sees a half-written record;
// readers ignore `*.tmp`. The hook-records module that writes them also removes them: the Cursor `sessionStart` hook, before its sync,
// removes record temps older than 1 hour (STALE_TEMP_SECONDS, C3) under `.records/` (left by a killed hook). No reader deletes anything.
// The hook parses stdin only to read `conversation_id` for the folder name. Invalid JSON, a missing id, or an id failing the C2 SafeId rule
// -> folder `.records/.invalid/`; nothing is written outside `.records`, and nothing through a symlinked or junctioned `.records` or conversation folder.
// CursorReader lists `.records/<SafeId>/` folders only; its scan lists `.records/.invalid/` as one unreadable item naming how many calls it holds.
// Every other field is parsed later, in sync.
// Kept, no rotation: records are Cursor's only source, so a log can always be rebuilt whole; sync reads a conversation only when its fingerprint changed (C2, C6).
// Joins: prompt and stop by generation_id; usage comes from `stop` only (afterAgentResponse repeats the same totals).
// Sub-agents: one row per `child_conversation_id` in the conversation, start = subagentStop arrival - duration_ms, ordered as C2 SubAgent.
// Duplicates (same event and generation_id, or same child_conversation_id) count once, the last one wins.
// A conversation that another conversation's subagentStop names as its child_conversation_id is no session (not archived, never picked by `usage --latest`; its parent keeps the row): CursorReader.read gives none for it, once per scan. Until that record arrives nothing marks it, so it is read like any conversation (and stays archived if it was); a conversation never counts as its own child, and a subagentStop record that cannot be opened names nobody.
// A prompt without a stop shows no reply and zero tokens; a stop without a prompt is counted in TOTAL under `(prompt not recorded)`.
// sessionStart runs sync and writes no record (Q9).
import "./session-log-simple-c2-session-model.tsp";

using SessionLogSimple.C2SessionModel;

namespace SessionLogSimple.C5CursorHookRecord;

enum CursorRecordEvent { beforeSubmitPrompt, afterAgentResponse, stop, subagentStop }

/** Content of one record file. */
model HookRecord {
  event: CursorRecordEvent;
  conversationId: SafeId;
  /** Nanoseconds; also part of the file name. */
  receivedAtNs: int64;
  pid: int32;
  /** Hook stdin decoded as UTF-8 with errors replaced, BOM stripped (pwsh adds one; Windows PowerShell 5.1 may send ANSI bytes). */
  rawInput: string;
}
