import { afterAll, beforeAll, expect, test } from "bun:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { Database } from "bun:sqlite";

const home = fs.mkdtempSync(path.join(os.tmpdir(), "session-log-opencode-"));
const logDirectory = path.join(home, ".config", "opencode", "prompt-logs");
const databaseDirectory = path.join(home, ".local", "share", "opencode");
const databasePath = path.join(databaseDirectory, "opencode.db");
const pluginPath = path.join(import.meta.dir, "session-log.js");
let database;

beforeAll(() => {
  fs.mkdirSync(logDirectory, { recursive: true, mode: 0o700 });
  fs.mkdirSync(databaseDirectory, { recursive: true, mode: 0o700 });
  fs.writeFileSync(path.join(logDirectory, ".enabled"), "enabled", { mode: 0o600 });
  database = new Database(databasePath);
  database.exec("CREATE TABLE part (message_id TEXT, time_created INTEGER, data TEXT)");
  database.exec("CREATE TABLE session (id TEXT, parent_id TEXT)");
});

afterAll(() => {
  database?.close();
  fs.rmSync(home, { recursive: true, force: true });
});

const runPlugin = (source, env = {}) => execFileSync(process.execPath, ["--eval", `
  import fs from "node:fs";
  import path from "node:path";
  const { SessionLogPlugin, commitPairedRuntime } = await import(process.env.SESSION_LOG_PLUGIN);
  const event = (hooks, type, info) => hooks.event({ event: { type, properties: { info } } });
  ${source}
`], { env: { ...process.env, HOME: home, SESSION_LOG_PLUGIN: pluginPath, ...env }, encoding: "utf8" });
const loggedText = (project) => fs.readdirSync(path.join(logDirectory, project))
  .filter((name) => name.endsWith(".md"))
  .map((name) => fs.readFileSync(path.join(logDirectory, project, name), "utf8")).join("\n");

test("records one prompt and the final assistant text, not synthetic content or duplicates", () => {
  database.query("INSERT INTO part VALUES (?, ?, ?)").run("answer-1", 1, JSON.stringify({ type: "text", text: "Answer." }));
  database.query("INSERT INTO part VALUES (?, ?, ?)").run("answer-1", 2, JSON.stringify({ type: "text", text: "hidden", synthetic: true }));
  runPlugin(`
    const hooks = await SessionLogPlugin({ directory: "fixture/response" });
    const sessionID = "response-root";
    await hooks["chat.message"]({ sessionID, messageID: "prompt-1" }, { parts: [{ type: "text", text: "Question?" }] });
    const message = { id: "answer-1", sessionID, role: "assistant", finish: "stop", time: { created: 1000, completed: 2500 }, tokens: { input: 2, output: 3 } };
    await event(hooks, "message.updated", message);
    await event(hooks, "message.updated", message);
    await hooks["chat.message"]({ sessionID, messageID: "prompt-1" }, { parts: [{ type: "text", text: "Question?" }] });
  `);
  const log = loggedText("fixture-response");
  expect(log.match(/Question\?/g)).toHaveLength(1);
  expect(log.match(/Answer\./g)).toHaveLength(1);
  expect(log).not.toContain("hidden");
});

test("links child completions to the root log and records them only once", () => {
  runPlugin(`
    const hooks = await SessionLogPlugin({ directory: "fixture/child" });
    await event(hooks, "session.created", { id: "child-root", parentID: null });
    await event(hooks, "session.created", { id: "child-worker", parentID: "child-root" });
    const message = {
      id: "child-finish", sessionID: "child-worker", role: "assistant", mode: "Explore",
      time: { created: 1000, completed: 3000 }, tokens: { input: 1, output: 2 },
    };
    await event(hooks, "message.updated", message);
    await event(hooks, "message.updated", message);
  `);
  const log = loggedText("fixture-child");
  expect(log).toContain("sub-agent finished: Explore (child-worker)");
  expect(log.match(/sub-agent finished:/g)).toHaveLength(1);
  expect(log).toContain("working time: 00:00:02");
});

test("deleted parent suppresses late child events and does not recreate a log", () => {
  runPlugin(`
    const hooks = await SessionLogPlugin({ directory: "fixture/deleted" });
    await event(hooks, "session.created", { id: "deleted-root", parentID: null });
    await event(hooks, "session.created", { id: "deleted-child", parentID: "deleted-root" });
    await event(hooks, "session.deleted", { id: "deleted-root" });
    await hooks["chat.message"]({ sessionID: "deleted-root" }, { parts: [{ text: "late prompt" }] });
    await event(hooks, "message.updated", { id: "late-answer", sessionID: "deleted-child", role: "assistant", time: { completed: 3000 } });
  `);
  expect(fs.existsSync(path.join(logDirectory, "fixture-deleted"))).toBe(false);
});

test("database snapshots are removed when opening them fails", () => {
  const temporaryDirectory = fs.mkdtempSync(path.join(home, "snapshot-tmp-"));
  const wrapperDirectory = fs.mkdtempSync(path.join(home, "python-wrapper-"));
  // Make every snapshot unreadable so `new Database(snapshot)` throws after the copy exists.
  fs.writeFileSync(path.join(wrapperDirectory, "python3"), [
    "#!/bin/sh",
    `output=$("${Bun.which("python3")}" "$@") || exit $?`,
    'if [ -n "$SESSION_LOG_SOURCE" ]; then chmod 000 "$output"; fi',
    'printf "%s\\n" "$output"',
  ].join("\n"), { mode: 0o700 });
  runPlugin(`
    const hooks = await SessionLogPlugin({ directory: "fixture/snapshot-leak" });
    await hooks["chat.message"]({ sessionID: "leak-root", messageID: "leak-prompt" }, { parts: [{ type: "text", text: "Leak?" }] });
    const message = { role: "assistant", finish: "stop", time: { created: 1000, completed: 2000 }, tokens: { input: 1, output: 1 } };
    await event(hooks, "message.updated", { ...message, id: "leak-answer", sessionID: "leak-root" });
    await event(hooks, "message.updated", { ...message, id: "leak-orphan", sessionID: "leak-unknown" });
  `, { TMPDIR: temporaryDirectory, PATH: `${wrapperDirectory}:${process.env.PATH}` });
  expect(fs.readdirSync(temporaryDirectory).filter((name) => name.startsWith("session-log-opencode-db-"))).toEqual([]);
});

test("failed second runtime rename restores the previous shared runtime record", () => {
  const result = JSON.parse(runPlugin(`
    await SessionLogPlugin({ directory: "fixture/runtime" });
    const state = path.join(process.env.HOME, ".config/opencode/session-log");
    const runtime = path.join(state, "runtime.json");
    const processRuntime = path.join(state, "occupied-process-runtime");
    const temporary = path.join(state, "next-runtime");
    const processTemporary = path.join(state, "next-process-runtime");
    const backup = path.join(state, "runtime-backup");
    const previous = fs.readFileSync(runtime, "utf8");
    fs.mkdirSync(processRuntime);
    fs.writeFileSync(temporary, "new runtime");
    fs.writeFileSync(processTemporary, "new process runtime");
    let failure = "";
    try { commitPairedRuntime({ temporary, processTemporary, runtime, processRuntime, backup }); }
    catch (error) { failure = error.code; }
    console.log(JSON.stringify({
      failure,
      runtimeRestored: fs.readFileSync(runtime, "utf8") === previous,
      blockerPreserved: fs.statSync(processRuntime).isDirectory(),
      backupConsumed: !fs.existsSync(backup),
    }));
  `));
  expect(result.failure).toMatch(/EISDIR|ENOTEMPTY/);
  expect(result.runtimeRestored).toBe(true);
  expect(result.blockerPreserved).toBe(true);
  expect(result.backupConsumed).toBe(true);
});

test("runtime rollback still restores the backup when the new runtime vanishes first", () => {
  const result = JSON.parse(runPlugin(`
    await SessionLogPlugin({ directory: "fixture/runtime-vanish" });
    const state = path.join(process.env.HOME, ".config/opencode/session-log");
    const runtime = path.join(state, "runtime.json");
    const processRuntime = path.join(state, "vanish-process-runtime");
    const temporary = path.join(state, "vanish-next-runtime");
    const processTemporary = path.join(state, "vanish-next-process-runtime");
    const backup = path.join(state, "vanish-runtime-backup");
    const previous = fs.readFileSync(runtime, "utf8");
    fs.mkdirSync(processRuntime);
    fs.writeFileSync(temporary, "new runtime");
    fs.writeFileSync(processTemporary, "new process runtime");
    const unlinkSync = fs.unlinkSync;
    // Another process removes runtime.json between the rollback's check and its unlink.
    fs.unlinkSync = (file) => {
      if (file === runtime) unlinkSync(file);
      return unlinkSync(file);
    };
    let failure = "";
    try { commitPairedRuntime({ temporary, processTemporary, runtime, processRuntime, backup }); }
    catch (error) { failure = error.code || error.name; }
    fs.unlinkSync = unlinkSync;
    console.log(JSON.stringify({ failure, runtimeRestored: fs.readFileSync(runtime, "utf8") === previous }));
  `));
  expect(result.failure).toMatch(/EISDIR|ENOTEMPTY/);
  expect(result.runtimeRestored).toBe(true);
});

test("dedupe state drops committed records once pending records fill the retention limit", () => {
  const maxDedupeRecords = 64;
  const result = JSON.parse(runPlugin(`
    const hooks = await SessionLogPlugin({ directory: "fixture/dedupe-limit" });
    const sessionID = "dedupe-root";
    await hooks["chat.message"]({ sessionID, messageID: "dedupe-first" }, { parts: [{ type: "text", text: "First" }] });
    const project = path.join(process.env.HOME, ".config/opencode/prompt-logs/fixture-dedupe-limit");
    const dedupe = path.join(project, fs.readdirSync(project).find((name) => name.endsWith(".dedupe")));
    const pending = Array.from({ length: ${maxDedupeRecords} }, (_, index) =>
      ({ key: "pending-" + index, offset: 0, length: 0, sha256: "", committed: false }));
    const committed = [{ key: "old-committed", legacy: true, committed: true }];
    fs.writeFileSync(dedupe, [...pending, ...committed].map((record) => JSON.stringify(record) + "\\n").join(""));
    await hooks["chat.message"]({ sessionID, messageID: "dedupe-second" }, { parts: [{ type: "text", text: "Second" }] });
    const records = fs.readFileSync(dedupe, "utf8").split("\\n").filter(Boolean).map((line) => JSON.parse(line));
    console.log(JSON.stringify({ total: records.length, committed: records.filter((record) => record.committed).length }));
  `));
  expect(result).toEqual({ total: maxDedupeRecords, committed: 0 });
});
