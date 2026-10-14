import { afterAll, beforeEach, expect, test } from "bun:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";

const home = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "session-log-opencode-")));
const configDirectory = path.join(home, ".config", "opencode");
const logDirectory = path.join(configDirectory, "prompt-logs");
const stateDirectory = path.join(configDirectory, "session-log");
const enabledFlag = path.join(logDirectory, ".enabled");
const pluginPath = path.join(import.meta.dir, "session-log.js");
const packageVersion = fs.readFileSync(path.join(import.meta.dir, "../../VERSION"), "utf8").trim();
const CLOCK_LINE = "[0-9]{2}:[0-9]{2}:[0-9]{2}";

beforeEach(() => {
  fs.mkdirSync(logDirectory, { recursive: true, mode: 0o700 });
  fs.writeFileSync(enabledFlag, "enabled", { mode: 0o600 });
});

afterAll(() => {
  fs.rmSync(home, { recursive: true, force: true });
});

// Runs `source` in a fresh Bun process whose HOME is the fixture home, with a v2 plugin context
// double: `sessions` maps a session ID to its parent for ctx.session.get, `log` is the session-log
// instance, `event(type, data)` delivers one v2 event, and `step(...)` delivers one assistant step.
const runPlugin = (source, env = {}) => {
  const result = spawnSync(process.execPath, ["--eval", `
    import fs from "node:fs";
    import path from "node:path";
    const sessionLog = await import(process.env.SESSION_LOG_PLUGIN);
    const sessions = new Map();
    const lookups = [];
    let clock = 0;
    const ctx = {
      location: { directory: process.env.SESSION_LOG_FIXTURE || "fixture/default" },
      session: {
        get: async ({ sessionID }) => {
          lookups.push(sessionID);
          if (!sessions.has(sessionID)) throw new Error("Session not found: " + sessionID);
          const parentID = sessions.get(sessionID);
          return parentID ? { id: sessionID, parentID } : { id: sessionID };
        },
      },
    };
    const log = sessionLog.createSessionLog(ctx, { now: () => clock });
    const event = (type, data) => log.onEvent({ id: "evt_fixture", type, data });
    const step = (sessionID, assistantMessageID, options = {}) => {
      const { agent = "build", model = "fixture-model", texts = [], seconds = 0, finish = "stop",
        tokens = { input: 2, output: 3, reasoning: 0, cache: { read: 0, write: 0 } }, cost = 0, type = "session.step.ended" } = options;
      event("session.step.started", { sessionID, assistantMessageID, agent, model: { id: model, providerID: "fixture" } });
      texts.forEach((text, ordinal) => event("session.text.ended", { sessionID, assistantMessageID, ordinal, text }));
      clock += seconds * 1000;
      event(type, { sessionID, assistantMessageID, finish, cost, tokens });
    };
    const prompt = (sessionID, text, messageID = "msg_" + text.replace(/[^A-Za-z0-9]/g, "")) =>
      log.onPrompt({ sessionID, messageID, prompt: { text }, delivery: "steer" });
    ${source}
    await log.idle();
  `], { env: { ...process.env, HOME: home, SESSION_LOG_PLUGIN: pluginPath, ...env }, encoding: "utf8" });
  if (result.status !== 0) throw new Error(`plugin run failed: ${result.stderr}`);
  return result;
};
const projectLog = (project) => {
  const directory = path.join(logDirectory, project);
  return fs.readdirSync(directory)
    .filter((name) => name.endsWith(".md"))
    .map((name) => fs.readFileSync(path.join(directory, name), "utf8")).join("\n");
};

test("logs the root prompt and each root step in the release log format", () => {
  runPlugin(`
    sessions.set("ses_root", undefined);
    await prompt("ses_root", "Question?");
    step("ses_root", "msg_step1", { texts: [], finish: "tool-calls", seconds: 1 });
    step("ses_root", "msg_step2", { texts: ["First part.", "Second part."], seconds: 2, cost: 0.0123,
      tokens: { input: 10, output: 20, reasoning: 3, cache: { read: 4, write: 5 } } });
  `, { SESSION_LOG_FIXTURE: "fixture/format" });
  const log = projectLog("fixture-format");
  expect(log).toMatch(new RegExp(`^# Prompts — .+\\n\\n\\*\\*Session ID:\\*\\* ses_root\\n\\*\\*Resume:\\*\\* \`opencode --session ses_root\`\\n\\n---\\n\\n`));
  expect(log).toMatch(new RegExp(`\\n## ${CLOCK_LINE}\\n\\nQuestion\\?\\n\\n---\\n\\n`));
  expect(log).toMatch(new RegExp(`### ${CLOCK_LINE} response\\n\\nworking time: 00:00:01\\n` +
    "est\\. used token: input: 2, output: 3, reasoning: 0, cache_write: 0, cache_read: 0, total: 5, cost: \\$0\\.0000, model: fixture-model\\n---\\n\\n"));
  expect(log).toMatch(new RegExp(`### ${CLOCK_LINE} response\\n\\nFirst part\\.\\nSecond part\\.\\n\\nworking time: 00:00:02\\n` +
    "est\\. used token: input: 10, output: 20, reasoning: 3, cache_write: 5, cache_read: 4, total: 42, cost: \\$0\\.0123, model: fixture-model\\n---\\n\\n$"));
});

test("logs a child step as a sub-agent line in the root log and never the child prompt", () => {
  runPlugin(`
    sessions.set("ses_parent", undefined);
    await prompt("ses_parent", "Delegate.");
    event("session.created", { sessionID: "ses_child", parentID: "ses_parent", slug: "child", version: "2" });
    await prompt("ses_child", "You are a subagent spawned by another session.");
    step("ses_child", "msg_child", { agent: "explore", texts: ["kiwi"], seconds: 2 });
  `, { SESSION_LOG_FIXTURE: "fixture/child" });
  const log = projectLog("fixture-child");
  expect(log).toMatch(new RegExp(`\\n${CLOCK_LINE} sub-agent finished: explore \\(ses_child\\), working time: 00:00:02, ` +
    "est\\. used token: input: 2, output: 3, reasoning: 0, cache_write: 0, cache_read: 0, total: 5, cost: \\$0\\.0000, model: fixture-model\\n\\n$"));
  expect(log).not.toContain("You are a subagent");
  expect(log).not.toContain("kiwi");
});

test("resolves sessions it never saw created through ctx.session.get, once each", () => {
  const result = runPlugin(`
    sessions.set("ses_resumed", undefined);
    sessions.set("ses_resumed_child", "ses_resumed");
    await prompt("ses_resumed", "After a restart.");
    step("ses_resumed_child", "msg_resumed_child", { agent: "general" });
    step("ses_resumed_child", "msg_resumed_child2", { agent: "general" });
    await log.idle();
    process.stdout.write(JSON.stringify(lookups));
  `, { SESSION_LOG_FIXTURE: "fixture/resumed" });
  const log = projectLog("fixture-resumed");
  expect(log).toContain("After a restart.");
  expect(log.match(/sub-agent finished: general \(ses_resumed_child\)/g)).toHaveLength(2);
  expect(JSON.parse(result.stdout)).toEqual(["ses_resumed", "ses_resumed_child"]);
});

test("notes a model or agent switch between root steps", () => {
  runPlugin(`
    sessions.set("ses_switch", undefined);
    await prompt("ses_switch", "Switch.");
    step("ses_switch", "msg_switch1", { texts: ["one"] });
    step("ses_switch", "msg_switch2", { texts: ["two"], model: "other-model", agent: "plan" });
  `, { SESSION_LOG_FIXTURE: "fixture/switch" });
  const log = projectLog("fixture-switch");
  expect(log.match(/switched:/g)).toHaveLength(1);
  expect(log).toContain("model: other-model\nswitched: model fixture-model → other-model, agent build → plan\n---\n\n");
});

test("logs a failed step and an ended step without a started event only once", () => {
  runPlugin(`
    sessions.set("ses_failed", undefined);
    await prompt("ses_failed", "Fail.");
    event("session.step.started", { sessionID: "ses_failed", assistantMessageID: "msg_failed", agent: "build", model: { id: "fixture-model", providerID: "fixture" } });
    event("session.text.ended", { sessionID: "ses_failed", assistantMessageID: "msg_failed", ordinal: 0, text: "partial" });
    event("session.step.failed", { sessionID: "ses_failed", assistantMessageID: "msg_failed", error: { type: "provider.transport", message: "offline" } });
    event("session.step.ended", { sessionID: "ses_failed", assistantMessageID: "msg_failed", finish: "stop", cost: 0,
      tokens: { input: 1, output: 1, reasoning: 0, cache: { read: 0, write: 0 } } });
  `, { SESSION_LOG_FIXTURE: "fixture/failed" });
  const log = projectLog("fixture-failed");
  expect(log.match(/ response\n/g)).toHaveLength(1);
  expect(log).toContain("partial\n\nworking time: 00:00:00\nest. used token: input: 0, output: 0, reasoning: 0, cache_write: 0, cache_read: 0, total: 0, cost: $0.0000, model: fixture-model\n");
});

test("a deleted session and its children log nothing more", () => {
  runPlugin(`
    event("session.created", { sessionID: "ses_gone", slug: "gone", version: "2" });
    event("session.created", { sessionID: "ses_gone_child", parentID: "ses_gone", slug: "child", version: "2" });
    event("session.deleted", { sessionID: "ses_gone" });
    await prompt("ses_gone", "late prompt");
    step("ses_gone_child", "msg_late", { texts: ["late"] });
  `, { SESSION_LOG_FIXTURE: "fixture/deleted" });
  expect(fs.existsSync(path.join(logDirectory, "fixture-deleted"))).toBe(false);
});

test("logs nothing while the enable flag is missing, including steps that end after it appears", () => {
  fs.unlinkSync(enabledFlag);
  runPlugin(`
    sessions.set("ses_off", undefined);
    await prompt("ses_off", "not logged");
    event("session.step.started", { sessionID: "ses_off", assistantMessageID: "msg_off", agent: "build", model: { id: "m", providerID: "p" } });
    fs.writeFileSync(path.join(process.env.HOME, ".config/opencode/prompt-logs/.enabled"), "enabled", { mode: 0o600 });
    event("session.step.ended", { sessionID: "ses_off", assistantMessageID: "msg_off", finish: "stop", cost: 0,
      tokens: { input: 1, output: 1, reasoning: 0, cache: { read: 0, write: 0 } } });
  `, { SESSION_LOG_FIXTURE: "fixture/off" });
  expect(fs.existsSync(path.join(logDirectory, "fixture-off"))).toBe(false);
});

test("ignores invalid session IDs without writing outside the log root", () => {
  const result = runPlugin(`
    await prompt("../outside", "escape");
    step("../outside", "msg_escape", { texts: ["escape"] });
  `, { SESSION_LOG_FIXTURE: "fixture/invalid" });
  expect(fs.existsSync(path.join(logDirectory, "fixture-invalid"))).toBe(false);
  expect(fs.existsSync(path.join(configDirectory, "outside"))).toBe(false);
  expect(result.status).toBe(0);
});

test("refuses a symlinked log directory without throwing into OpenCode", () => {
  const outside = fs.mkdtempSync(path.join(home, "outside-"));
  fs.writeFileSync(path.join(outside, "sentinel"), "sentinel");
  fs.symlinkSync(outside, path.join(logDirectory, "fixture-symlinked"));
  const result = runPlugin(`
    sessions.set("ses_symlinked", undefined);
    await prompt("ses_symlinked", "must not escape");
  `, { SESSION_LOG_FIXTURE: "fixture/symlinked" });
  expect(fs.readdirSync(outside)).toEqual(["sentinel"]);
  expect(result.stderr).toContain("not a safe directory");
});

test("setup records the runtime marker and a prompt restores it after `off` removed it", () => {
  const runtime = path.join(stateDirectory, "runtime.json");
  const result = runPlugin(`
    sessions.set("ses_marker", undefined);
    const command = { add: () => {} };
    const cleanup = await sessionLog.setupSessionLog({
      ...ctx,
      session: { ...ctx.session, hook: async () => ({ dispose: async () => {} }) },
      command: { transform: async (callback) => callback(command) },
      event: { subscribe: () => ({ [Symbol.asyncIterator]: () => ({ next: () => new Promise(() => {}), return: async () => ({ done: true }) }) }) },
    });
    const runtime = path.join(process.env.HOME, ".config/opencode/session-log/runtime.json");
    const written = JSON.parse(fs.readFileSync(runtime, "utf8"));
    fs.unlinkSync(runtime);
    await prompt("ses_marker", "restore the marker");
    await cleanup();
    process.stdout.write(JSON.stringify(written));
  `, { SESSION_LOG_FIXTURE: "fixture/marker" });
  const written = JSON.parse(result.stdout);
  expect(written).toMatchObject({ harness: "opencode", version: packageVersion });
  expect(written.pid).toBeGreaterThan(0);
  expect(written.process_start).not.toBe("");
  expect(written.nonce).not.toBe("");
  expect(JSON.parse(fs.readFileSync(runtime, "utf8"))).toMatchObject({ harness: "opencode", version: packageVersion });
});

test("a symlinked runtime marker is refused without throwing into OpenCode", () => {
  fs.mkdirSync(stateDirectory, { recursive: true, mode: 0o700 });
  const runtime = path.join(stateDirectory, "runtime.json");
  fs.rmSync(runtime, { force: true });
  const sentinel = path.join(home, "runtime-sentinel");
  fs.writeFileSync(sentinel, "runtime sentinel");
  fs.symlinkSync(sentinel, runtime);
  const result = runPlugin(`
    sessions.set("ses_runtime_link", undefined);
    await prompt("ses_runtime_link", "still logged");
  `, { SESSION_LOG_FIXTURE: "fixture/runtime-link" });
  expect(fs.readFileSync(sentinel, "utf8")).toBe("runtime sentinel");
  expect(result.stderr).toContain("not a safe file");
  expect(projectLog("fixture-runtime-link")).toContain("still logged");
  fs.unlinkSync(runtime);
});

test("setup wires the prompt hook, the event stream, and the /session-log command; cleanup stops the stream", () => {
  const result = runPlugin(`
    sessions.set("ses_wired", undefined);
    const registered = { hooks: [], commands: [] };
    let release;
    const queued = [
      { id: "evt_1", type: "session.step.started", data: { sessionID: "ses_wired", assistantMessageID: "msg_wired", agent: "build", model: { id: "fixture-model", providerID: "fixture" } } },
      { id: "evt_2", type: "session.text.ended", data: { sessionID: "ses_wired", assistantMessageID: "msg_wired", ordinal: 0, text: "wired answer" } },
      { id: "evt_3", type: "session.step.ended", data: { sessionID: "ses_wired", assistantMessageID: "msg_wired", finish: "stop", cost: 0, tokens: { input: 1, output: 1, reasoning: 0, cache: { read: 0, write: 0 } } } },
    ];
    let signal;
    const drained = new Promise((resolve) => { release = resolve; });
    const cleanup = await sessionLog.setupSessionLog({
      ...ctx,
      session: { ...ctx.session, hook: async (name, callback) => { registered.hooks.push(name); registered.prompt = callback; return { dispose: async () => {} }; } },
      command: { transform: async (callback) => callback({ add: (definition) => registered.commands.push(definition) }) },
      event: { subscribe: (options) => { signal = options.signal; return (async function* () {
        await registered.prompt({ sessionID: "ses_wired", messageID: "msg_wired_prompt", prompt: { text: "wired prompt" }, delivery: "steer" });
        yield* queued;
        release();
        await new Promise((resolve) => signal.addEventListener("abort", resolve));
      })(); } },
    });
    await drained;
    await cleanup();
    const files = fs.readdirSync(path.join(process.env.HOME, ".config/opencode/prompt-logs/fixture-wired"));
    process.stdout.write(JSON.stringify({ hooks: registered.hooks, commands: registered.commands.map(({ name, description }) => ({ name, description })), aborted: signal.aborted,
      log: fs.readFileSync(path.join(process.env.HOME, ".config/opencode/prompt-logs/fixture-wired", files.find((name) => name.endsWith(".md"))), "utf8") }));
  `, { SESSION_LOG_FIXTURE: "fixture/wired" });
  const wired = JSON.parse(result.stdout);
  expect(wired.hooks).toEqual(["prompt"]);
  expect(wired.commands).toEqual([{ name: "session-log", description: "Manage session prompt logging and usage totals (on / off / status / usage / repair)" }]);
  expect(wired.aborted).toBe(true);
  expect(wired.log).toContain("wired prompt");
  expect(wired.log).toContain("wired answer");
});

test("a relocated OpenCode config root logs nothing but keeps the /session-log command", () => {
  const relocated = fs.mkdtempSync(path.join(home, "relocated-"));
  const result = runPlugin(`
    sessions.set("ses_relocated", undefined);
    const commands = [];
    await sessionLog.setupSessionLog({
      ...ctx,
      session: { ...ctx.session, hook: async () => ({ dispose: async () => {} }) },
      command: { transform: async (callback) => callback({ add: (definition) => commands.push(definition.name) }) },
      event: { subscribe: () => (async function* () {})() },
    });
    process.stdout.write(JSON.stringify(commands));
  `, { SESSION_LOG_FIXTURE: "fixture/relocated", XDG_CONFIG_HOME: relocated });
  expect(JSON.parse(result.stdout)).toEqual(["session-log"]);
  expect(result.stderr).toContain("OpenCode root is relocated");
  expect(fs.readdirSync(relocated)).toEqual([]);
});

test("/session-log runs the package CLI for the session and posts its output as a notice", () => {
  fs.unlinkSync(enabledFlag);
  const result = runPlugin(`
    const notices = [];
    let command;
    await sessionLog.setupSessionLog({
      ...ctx,
      session: { ...ctx.session, hook: async () => ({ dispose: async () => {} }), synthetic: async (input) => { notices.push(input); return {}; } },
      command: { transform: async (callback) => callback({ add: (definition) => { command = definition; } }) },
      event: { subscribe: () => (async function* () {})() },
    });
    await command.execute({ sessionID: "ses_command", prompt: { text: "  " }, delivery: "steer" });
    await command.execute({ sessionID: "ses_command", prompt: { text: "status" }, delivery: "steer" });
    let failure = "";
    try { await command.execute({ sessionID: "ses_command", prompt: { text: "bogus" }, delivery: "steer" }); }
    catch (error) { failure = error.message; }
    process.stdout.write(JSON.stringify({ notices, failure }));
  `, { SESSION_LOG_FIXTURE: "fixture/command" });
  const { notices, failure } = JSON.parse(result.stdout);
  const notice = { sessionID: "ses_command", text: "OpenCode: off", description: "OpenCode: off", resume: false };
  expect(notices).toEqual([notice, notice]);
  expect(failure).toBe("session-log: unknown command: bogus");
});

test("the legacy cleanup runs `repair` only while an older release's leftover exists", () => {
  const legacyCommand = path.join(configDirectory, "commands", "session-log.md");
  fs.rmSync(stateDirectory, { recursive: true, force: true });
  runPlugin("await sessionLog.cleanupLegacyLeftovers();");
  expect(fs.existsSync(stateDirectory)).toBe(false);
  fs.mkdirSync(path.dirname(legacyCommand), { recursive: true });
  fs.writeFileSync(legacyCommand, "---\ndescription: legacy\n---\n<!-- universal-session-log: managed -->\n\nlegacy\n");
  runPlugin("await sessionLog.cleanupLegacyLeftovers();");
  expect(fs.existsSync(legacyCommand)).toBe(false);
});

test("failed second runtime rename restores the previous shared runtime record", () => {
  const result = runPlugin(`
    const state = path.join(process.env.HOME, ".config/opencode/session-log");
    fs.mkdirSync(state, { recursive: true });
    const runtime = path.join(state, "runtime.json");
    fs.writeFileSync(runtime, "previous runtime");
    const processRuntime = path.join(state, "occupied-process-runtime");
    const temporary = path.join(state, "next-runtime");
    const processTemporary = path.join(state, "next-process-runtime");
    const backup = path.join(state, "runtime-backup");
    fs.mkdirSync(processRuntime);
    fs.writeFileSync(temporary, "new runtime");
    fs.writeFileSync(processTemporary, "new process runtime");
    let failure = "";
    try { sessionLog.commitPairedRuntime({ temporary, processTemporary, runtime, processRuntime, backup }); }
    catch (error) { failure = error.code; }
    process.stdout.write(JSON.stringify({
      failure,
      runtimeRestored: fs.readFileSync(runtime, "utf8") === "previous runtime",
      blockerPreserved: fs.statSync(processRuntime).isDirectory(),
      backupConsumed: !fs.existsSync(backup),
    }));
    fs.rmdirSync(processRuntime);
  `);
  const outcome = JSON.parse(result.stdout);
  expect(outcome.failure).toMatch(/EISDIR|ENOTEMPTY/);
  expect(outcome.runtimeRestored).toBe(true);
  expect(outcome.blockerPreserved).toBe(true);
  expect(outcome.backupConsumed).toBe(true);
});

test("runtime rollback still restores the backup when the new runtime vanishes first", () => {
  const result = runPlugin(`
    const state = path.join(process.env.HOME, ".config/opencode/session-log");
    fs.mkdirSync(state, { recursive: true });
    const runtime = path.join(state, "runtime.json");
    fs.writeFileSync(runtime, "previous runtime");
    const processRuntime = path.join(state, "vanish-process-runtime");
    const temporary = path.join(state, "vanish-next-runtime");
    const processTemporary = path.join(state, "vanish-next-process-runtime");
    const backup = path.join(state, "vanish-runtime-backup");
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
    try { sessionLog.commitPairedRuntime({ temporary, processTemporary, runtime, processRuntime, backup }); }
    catch (error) { failure = error.code || error.name; }
    fs.unlinkSync = unlinkSync;
    process.stdout.write(JSON.stringify({ failure, runtimeRestored: fs.readFileSync(runtime, "utf8") === "previous runtime" }));
    fs.rmdirSync(processRuntime);
  `);
  const outcome = JSON.parse(result.stdout);
  expect(outcome.failure).toMatch(/EISDIR|ENOTEMPTY/);
  expect(outcome.runtimeRestored).toBe(true);
});

test("dedupe state drops committed records once pending records fill the retention limit", () => {
  const maxDedupeRecords = 64;
  const result = runPlugin(`
    sessions.set("ses_dedupe", undefined);
    await prompt("ses_dedupe", "First");
    await log.idle();
    const project = path.join(process.env.HOME, ".config/opencode/prompt-logs/fixture-dedupe");
    const dedupe = path.join(project, fs.readdirSync(project).find((name) => name.endsWith(".dedupe")));
    const pending = Array.from({ length: ${maxDedupeRecords} }, (_, index) =>
      ({ key: "pending-" + index, offset: 0, length: 0, sha256: "", committed: false }));
    const committed = [{ key: "old-committed", legacy: true, committed: true }];
    fs.writeFileSync(dedupe, [...pending, ...committed].map((record) => JSON.stringify(record) + "\\n").join(""));
    await prompt("ses_dedupe", "Second");
    await log.idle();
    const records = fs.readFileSync(dedupe, "utf8").split("\\n").filter(Boolean).map((line) => JSON.parse(line));
    process.stdout.write(JSON.stringify({ total: records.length, committed: records.filter((record) => record.committed).length }));
  `, { SESSION_LOG_FIXTURE: "fixture/dedupe" });
  expect(JSON.parse(result.stdout)).toEqual({ total: maxDedupeRecords, committed: 0 });
});
