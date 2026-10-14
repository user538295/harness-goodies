import { afterEach, beforeEach, expect, test } from "bun:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";

const pluginPath = path.join(import.meta.dir, "session-log.js");
const packageRoot = path.resolve(import.meta.dir, "../..");
const packageVersion = fs.readFileSync(path.join(packageRoot, "VERSION"), "utf8").trim();
const CLOCK_LINE = "[0-9]{2}:[0-9]{2}:[0-9]{2}";
const LEGACY_COMMAND_TEXT = "---\ndescription: legacy\n---\n<!-- universal-session-log: managed -->\n\nlegacy\n";
// Variables that relocate OpenCode's roots; the children must not inherit the developer's.
const RELOCATING_VARIABLES = ["XDG_CONFIG_HOME", "XDG_DATA_HOME", "OPENCODE_CONFIG_DIR"];
// A child that hangs (say, waiting on an event that never comes) fails its test instead of the suite.
const CHILD_TIMEOUT_MS = 30_000;

let home;
let configDirectory;
let logDirectory;
let stateDirectory;
let enabledFlag;

// Each test gets its own HOME with logging on.
beforeEach(() => {
  home = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "session-log-opencode-")));
  configDirectory = path.join(home, ".config", "opencode");
  logDirectory = path.join(configDirectory, "prompt-logs");
  stateDirectory = path.join(configDirectory, "session-log");
  enabledFlag = path.join(logDirectory, ".enabled");
  fs.mkdirSync(logDirectory, { recursive: true, mode: 0o700 });
  fs.writeFileSync(enabledFlag, "enabled", { mode: 0o600 });
});

afterEach(() => {
  fs.rmSync(home, { recursive: true, force: true });
});

const childEnvironment = (env) => {
  const environment = { ...process.env };
  for (const name of RELOCATING_VARIABLES) delete environment[name];
  return { ...environment, HOME: home, SESSION_LOG_PLUGIN: pluginPath, ...env };
};

// Runs `source` in a fresh Bun process (Bun reads HOME once at startup) whose HOME is the test's,
// with a V2 plugin context double. In `source`: `sessions` maps a session ID to its parent for
// ctx.session.get, `log` is a createSessionLog instance on the injected `clock`, `event(type, data)`
// delivers one V2 event, `step(...)` one assistant step, `prompt(...)` one prompt hook call, and
// `setup(overrides)` runs setupSessionLog with a full context double and returns what it registered.
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
    const event = (type, data, created) => log.onEvent({ id: "evt_fixture", type, data, ...(created === undefined ? {} : { created }) });
    const step = (sessionID, assistantMessageID, options = {}) => {
      const { agent = "build", model = "fixture-model", texts = [], seconds = 0, finish = "stop",
        tokens = { input: 2, output: 3, reasoning: 0, cache: { read: 0, write: 0 } }, cost = 0 } = options;
      event("session.step.started", { sessionID, assistantMessageID, agent, model: { id: model, providerID: "fixture" } });
      texts.forEach((text, ordinal) => event("session.text.ended", { sessionID, assistantMessageID, ordinal, text }));
      clock += seconds * 1000;
      event("session.step.ended", { sessionID, assistantMessageID, finish, cost, tokens });
    };
    const prompt = (sessionID, text, messageID = "msg_" + text.replace(/[^A-Za-z0-9]/g, "")) =>
      log.onPrompt({ sessionID, messageID, prompt: { text }, delivery: "steer" });
    const neverEndingEvents = () => ({ [Symbol.asyncIterator]: () => ({ next: () => new Promise(() => {}), return: async () => ({ done: true }) }) });
    const setup = async (overrides = {}) => {
      const registered = { hooks: [], commands: [], disposed: 0, notices: [] };
      const cleanup = await sessionLog.setupSessionLog({
        ...ctx,
        session: {
          ...ctx.session,
          hook: async (name, callback) => {
            registered.hooks.push(name);
            registered.prompt = callback;
            return { dispose: async () => { registered.disposed += 1; } };
          },
          synthetic: async (input) => { registered.notices.push(input); return {}; },
        },
        command: { transform: async (callback) => callback({ add: (definition) => registered.commands.push(definition) }) },
        event: { subscribe: neverEndingEvents },
        ...overrides,
      });
      return { cleanup, registered };
    };
    ${source}
    await log.waitUntilIdle();
  `], { env: childEnvironment(env), encoding: "utf8", timeout: CHILD_TIMEOUT_MS });
  if (result.status !== 0) throw new Error(`plugin run failed (${result.signal ?? result.status}): ${result.stderr}`);
  return result;
};
const readProjectLog = (project) => {
  const directory = path.join(logDirectory, project);
  return fs.readdirSync(directory)
    .filter((name) => name.endsWith(".md"))
    .map((name) => fs.readFileSync(path.join(directory, name), "utf8")).join("\n");
};
const projectLogFiles = (project) => fs.readdirSync(path.join(logDirectory, project)).filter((name) => name.endsWith(".md"));

test("logs the root prompt and each root step in the release log format", () => {
  runPlugin(`
    sessions.set("ses_root", undefined);
    await prompt("ses_root", "Question?");
    step("ses_root", "msg_step1", { texts: [], finish: "tool-calls", seconds: 1 });
    step("ses_root", "msg_step2", { texts: ["First part.", "Second part."], seconds: 2, cost: 0.0123,
      tokens: { input: 10, output: 20, reasoning: 3, cache: { read: 4, write: 5 } } });
  `, { SESSION_LOG_FIXTURE: "fixture/format" });
  const log = readProjectLog("fixture-format");
  expect(log).toMatch(new RegExp(`^# Prompts — .+\\n\\n\\*\\*Session ID:\\*\\* ses_root\\n\\*\\*Resume:\\*\\* \`opencode --session ses_root\`\\n\\n---\\n\\n`));
  expect(log).toMatch(new RegExp(`\\n## ${CLOCK_LINE}\\n\\nQuestion\\?\\n\\n---\\n\\n`));
  expect(log).toMatch(new RegExp(`### ${CLOCK_LINE} response\\n\\nworking time: 00:00:01\\n` +
    "est\\. used token: input: 2, output: 3, reasoning: 0, cache_write: 0, cache_read: 0, total: 5, cost: \\$0\\.0000, model: fixture-model\\n---\\n\\n"));
  expect(log).toMatch(new RegExp(`### ${CLOCK_LINE} response\\n\\nFirst part\\.\\nSecond part\\.\\n\\nworking time: 00:00:02\\n` +
    "est\\. used token: input: 10, output: 20, reasoning: 3, cache_write: 5, cache_read: 4, total: 42, cost: \\$0\\.0123, model: fixture-model\\n---\\n\\n$"));
});

test("times a step by its events' own created times", () => {
  runPlugin(`
    sessions.set("ses_timed", undefined);
    event("session.step.started", { sessionID: "ses_timed", assistantMessageID: "msg_timed", agent: "build", model: { id: "m", providerID: "p" } }, 1_000);
    clock = 999_999_000;
    event("session.step.ended", { sessionID: "ses_timed", assistantMessageID: "msg_timed", finish: "stop", cost: 0,
      tokens: { input: 1, output: 1, reasoning: 0, cache: { read: 0, write: 0 } } }, 3_725_000);
  `, { SESSION_LOG_FIXTURE: "fixture/timed" });
  expect(readProjectLog("fixture-timed")).toContain("working time: 01:02:04\n");
});

test("logs a child step as a sub-agent line in the root log and never the child prompt", () => {
  runPlugin(`
    sessions.set("ses_parent", undefined);
    await prompt("ses_parent", "Delegate.");
    event("session.created", { sessionID: "ses_child", parentID: "ses_parent", slug: "child", version: "2" });
    await prompt("ses_child", "You are a subagent spawned by another session.");
    step("ses_child", "msg_child", { agent: "explore", texts: ["kiwi"], seconds: 2 });
  `, { SESSION_LOG_FIXTURE: "fixture/child" });
  const log = readProjectLog("fixture-child");
  expect(log).toMatch(new RegExp(`\\n${CLOCK_LINE} sub-agent finished: explore \\(ses_child\\), working time: 00:00:02, ` +
    "est\\. used token: input: 2, output: 3, reasoning: 0, cache_write: 0, cache_read: 0, total: 5, cost: \\$0\\.0000, model: fixture-model\\n\\n$"));
  expect(log).not.toContain("You are a subagent");
  expect(log).not.toContain("kiwi");
});

test("a session resumed after a restart logs under its root, looking each session up once", () => {
  const result = runPlugin(`
    sessions.set("ses_resumed", undefined);
    sessions.set("ses_resumed_child", "ses_resumed");
    await prompt("ses_resumed", "After a restart.");
    step("ses_resumed_child", "msg_resumed_child", { agent: "general" });
    step("ses_resumed_child", "msg_resumed_child2", { agent: "general" });
    await log.waitUntilIdle();
    process.stdout.write(JSON.stringify(lookups));
  `, { SESSION_LOG_FIXTURE: "fixture/resumed" });
  const log = readProjectLog("fixture-resumed");
  expect(log).toContain("After a restart.");
  expect(log.match(/sub-agent finished: general \(ses_resumed_child\)/g)).toHaveLength(2);
  expect(JSON.parse(result.stdout)).toEqual(["ses_resumed", "ses_resumed_child"]);
});

test("appends to the log file an earlier instance wrote for the same root, also under the old short name", () => {
  const project = path.join(logDirectory, "fixture-reuse");
  fs.mkdirSync(project, { recursive: true, mode: 0o700 });
  const legacyFile = path.join(project, "session_2026-01-01_00-00-00_ses_legacy01.md");
  fs.writeFileSync(legacyFile, "# Prompts — earlier\n\n**Session ID:** ses_legacy0123456789\n\n---\n\n", { mode: 0o600 });
  for (const text of ["first run", "second run"]) {
    runPlugin(`
      sessions.set("ses_reused", undefined);
      sessions.set("ses_legacy0123456789", undefined);
      await prompt("ses_reused", "${text}");
      await prompt("ses_legacy0123456789", "${text} legacy");
    `, { SESSION_LOG_FIXTURE: "fixture/reuse" });
  }
  expect(projectLogFiles("fixture-reuse")).toHaveLength(2);
  expect(fs.readFileSync(legacyFile, "utf8")).toContain("first run legacy");
  expect(fs.readFileSync(legacyFile, "utf8")).toContain("second run legacy");
  expect(readProjectLog("fixture-reuse").match(/first run\n|second run\n/g)).toHaveLength(2);
});

test("notes a model or agent switch between root steps", () => {
  runPlugin(`
    sessions.set("ses_switch", undefined);
    await prompt("ses_switch", "Switch.");
    step("ses_switch", "msg_switch1", { texts: ["one"] });
    step("ses_switch", "msg_switch2", { texts: ["two"], model: "other-model", agent: "plan" });
  `, { SESSION_LOG_FIXTURE: "fixture/switch" });
  const log = readProjectLog("fixture-switch");
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
  const log = readProjectLog("fixture-failed");
  expect(log.match(/ response\n/g)).toHaveLength(1);
  expect(log).toContain("partial\n\nworking time: 00:00:00\nest. used token: input: 0, output: 0, reasoning: 0, cache_write: 0, cache_read: 0, total: 0, cost: $0.0000, model: fixture-model\n");
});

test("a repeated prompt or a replayed step is logged once", () => {
  runPlugin(`
    sessions.set("ses_repeat", undefined);
    await prompt("ses_repeat", "Once.", "msg_once");
    await prompt("ses_repeat", "Once.", "msg_once");
    step("ses_repeat", "msg_replayed", { texts: ["answer"] });
    step("ses_repeat", "msg_replayed", { texts: ["answer"] });
  `, { SESSION_LOG_FIXTURE: "fixture/repeat" });
  const log = readProjectLog("fixture-repeat");
  expect(log.match(/Once\./g)).toHaveLength(1);
  expect(log.match(/ response\n/g)).toHaveLength(1);
});

test("a session lookup that fails is reported and later prompts still log", () => {
  const result = runPlugin(`
    sessions.set("ses_known", undefined);
    await prompt("ses_unknown", "lost prompt");
    await prompt("ses_known", "kept prompt");
  `, { SESSION_LOG_FIXTURE: "fixture/lookup" });
  expect(result.stderr).toContain("session-log: Session not found: ses_unknown");
  const log = readProjectLog("fixture-lookup");
  expect(log).toContain("kept prompt");
  expect(log).not.toContain("lost prompt");
});

test("a parent cycle logs nothing and ends", () => {
  runPlugin(`
    event("session.created", { sessionID: "ses_cycle_a", parentID: "ses_cycle_b", slug: "a", version: "2" });
    event("session.created", { sessionID: "ses_cycle_b", parentID: "ses_cycle_a", slug: "b", version: "2" });
    await prompt("ses_cycle_a", "cyclic");
    step("ses_cycle_b", "msg_cycle", { texts: ["cyclic"] });
  `, { SESSION_LOG_FIXTURE: "fixture/cycle" });
  expect(fs.existsSync(path.join(logDirectory, "fixture-cycle"))).toBe(false);
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

test("deleting a root drops its writes that were still queued", () => {
  runPlugin(`
    sessions.set("ses_queued", undefined);
    await prompt("ses_queued", "logged before deletion");
    prompt("ses_queued", "queued at deletion");
    event("session.deleted", { sessionID: "ses_queued" });
  `, { SESSION_LOG_FIXTURE: "fixture/queued" });
  const log = readProjectLog("fixture-queued");
  expect(log).toContain("logged before deletion");
  expect(log).not.toContain("queued at deletion");
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

test("a step that started under an earlier `on` is dropped when `off` and `on` replaced the flag", () => {
  runPlugin(`
    sessions.set("ses_flag", undefined);
    await prompt("ses_flag", "Before the toggle.");
    event("session.step.started", { sessionID: "ses_flag", assistantMessageID: "msg_flag", agent: "build", model: { id: "m", providerID: "p" } });
    fs.writeFileSync(path.join(process.env.HOME, ".config/opencode/prompt-logs/.enabled"), "enabled again", { mode: 0o600 });
    event("session.step.ended", { sessionID: "ses_flag", assistantMessageID: "msg_flag", finish: "stop", cost: 0,
      tokens: { input: 1, output: 1, reasoning: 0, cache: { read: 0, write: 0 } } });
  `, { SESSION_LOG_FIXTURE: "fixture/flag" });
  const log = readProjectLog("fixture-flag");
  expect(log).toContain("Before the toggle.");
  expect(log).not.toContain(" response\n");
});

test("ignores invalid session IDs without writing a log", () => {
  runPlugin(`
    await prompt("../outside", "escape");
    step("../outside", "msg_escape", { texts: ["escape"] });
  `, { SESSION_LOG_FIXTURE: "fixture/invalid" });
  expect(fs.readdirSync(logDirectory)).toEqual([".enabled"]);
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

test("retries a write that failed once and logs it a single time", () => {
  const wrapperDirectory = fs.mkdtempSync(path.join(home, "python-wrapper-"));
  const failedOnce = path.join(wrapperDirectory, "failed-once");
  // The first append helper run fails; every other Python run passes through.
  fs.writeFileSync(path.join(wrapperDirectory, "python3"), [
    "#!/bin/sh",
    `if [ -n "$SESSION_LOG_EXPECTED_INO" ] && [ ! -e "${failedOnce}" ]; then : > "${failedOnce}"; echo "transient failure" >&2; exit 1; fi`,
    `exec "${Bun.which("python3")}" "$@"`,
  ].join("\n"), { mode: 0o700 });
  runPlugin(`
    sessions.set("ses_retry", undefined);
    await prompt("ses_retry", "Retried.");
  `, { SESSION_LOG_FIXTURE: "fixture/retry", PATH: `${wrapperDirectory}:${process.env.PATH}` });
  expect(fs.existsSync(failedOnce)).toBe(true);
  expect(readProjectLog("fixture-retry").match(/Retried\./g)).toHaveLength(1);
});

test("setup records the runtime marker while logging is on, and a prompt restores it after `off`", () => {
  const runtime = path.join(stateDirectory, "runtime.json");
  const result = runPlugin(`
    sessions.set("ses_marker", undefined);
    const { cleanup } = await setup();
    const runtime = path.join(process.env.HOME, ".config/opencode/session-log/runtime.json");
    const written = JSON.parse(fs.readFileSync(runtime, "utf8"));
    fs.unlinkSync(runtime);
    await prompt("ses_marker", "restore the marker");
    await log.waitUntilIdle();
    process.stdout.write(JSON.stringify({ written, restored: JSON.parse(fs.readFileSync(runtime, "utf8")) }));
    await cleanup();
  `, { SESSION_LOG_FIXTURE: "fixture/marker" });
  const { written, restored } = JSON.parse(result.stdout);
  expect(written).toMatchObject({ harness: "opencode", version: packageVersion });
  expect(written.pid).toBeGreaterThan(0);
  expect(written.process_start).not.toBe("");
  expect(written.nonce).not.toBe("");
  expect(restored).toMatchObject({ harness: "opencode", version: packageVersion });
  expect(fs.existsSync(runtime)).toBe(true);
});

test("setup writes no runtime marker while logging is off", () => {
  fs.unlinkSync(enabledFlag);
  runPlugin("const { cleanup } = await setup(); await cleanup();");
  expect(fs.existsSync(path.join(stateDirectory, "runtime.json"))).toBe(false);
});

test("a symlinked runtime marker is refused without throwing into OpenCode", () => {
  fs.mkdirSync(stateDirectory, { recursive: true, mode: 0o700 });
  const sentinel = path.join(home, "runtime-sentinel");
  fs.writeFileSync(sentinel, "runtime sentinel");
  fs.symlinkSync(sentinel, path.join(stateDirectory, "runtime.json"));
  const result = runPlugin(`
    sessions.set("ses_runtime_link", undefined);
    await prompt("ses_runtime_link", "still logged");
  `, { SESSION_LOG_FIXTURE: "fixture/runtime-link" });
  expect(fs.readFileSync(sentinel, "utf8")).toBe("runtime sentinel");
  expect(result.stderr).toContain("not a safe file");
  expect(readProjectLog("fixture-runtime-link")).toContain("still logged");
});

test("setup wires the prompt hook, the event stream, and /session-log; cleanup unhooks and keeps runtime.json", () => {
  const result = runPlugin(`
    sessions.set("ses_wired", undefined);
    let signal;
    let release;
    const drained = new Promise((resolve) => { release = resolve; });
    let registered;
    const { cleanup, registered: wired } = await setup({
      event: { subscribe: (options) => { signal = options.signal; return (async function* () {
        await registered.prompt({ sessionID: "ses_wired", messageID: "msg_wired_prompt", prompt: { text: "wired prompt" }, delivery: "steer" });
        yield { id: "evt_1", created: 1_000, type: "session.step.started", data: { sessionID: "ses_wired", assistantMessageID: "msg_wired", agent: "build", model: { id: "fixture-model", providerID: "fixture" } } };
        yield { id: "evt_2", created: 1_000, type: "session.text.ended", data: { sessionID: "ses_wired", assistantMessageID: "msg_wired", ordinal: 0, text: "wired answer" } };
        yield { id: "evt_3", created: 2_000, type: "session.step.ended", data: { sessionID: "ses_wired", assistantMessageID: "msg_wired", finish: "stop", cost: 0, tokens: { input: 1, output: 1, reasoning: 0, cache: { read: 0, write: 0 } } } };
        release();
        await new Promise((resolve) => signal.addEventListener("abort", resolve));
      })(); } },
    });
    registered = wired;
    await drained;
    await cleanup();
    const state = path.join(process.env.HOME, ".config/opencode/session-log");
    process.stdout.write(JSON.stringify({ hooks: wired.hooks, disposed: wired.disposed, aborted: signal.aborted,
      commands: wired.commands.map(({ name, description }) => ({ name, description })), state: fs.readdirSync(state).sort() }));
  `, { SESSION_LOG_FIXTURE: "fixture/wired" });
  const wired = JSON.parse(result.stdout);
  expect(wired.hooks).toEqual(["prompt"]);
  expect(wired.disposed).toBe(1);
  expect(wired.aborted).toBe(true);
  expect(wired.commands).toEqual([{ name: "session-log", description: "Manage session prompt logging and usage totals (on / off / status / usage / repair)" }]);
  expect(wired.state).toEqual(["legacy-cleanup.stamp", "runtime.json"]);
  const log = readProjectLog("fixture-wired");
  expect(log).toContain("wired prompt");
  expect(log).toContain("wired answer\n\nworking time: 00:00:01\n");
});

test("a failed event stream is reported and subscribed again", () => {
  const result = runPlugin(`
    let subscriptions = 0;
    let resubscribed;
    const again = new Promise((resolve) => { resubscribed = resolve; });
    const { cleanup } = await setup({
      event: { subscribe: () => {
        subscriptions += 1;
        if (subscriptions === 1) return (async function* () { throw new Error("stream overflow"); })();
        resubscribed();
        return neverEndingEvents();
      } },
    });
    await again;
    await cleanup();
    process.stdout.write(String(subscriptions));
  `);
  expect(result.stderr).toContain("session-log: stream overflow");
  expect(result.stdout).toBe("2");
});

for (const [variable, label] of [["XDG_CONFIG_HOME", "config"], ["OPENCODE_CONFIG_DIR", "config"], ["XDG_DATA_HOME", "data"]]) {
  test(`a relocated OpenCode ${label} root (${variable}) logs nothing but keeps /session-log`, () => {
    const relocated = fs.mkdtempSync(path.join(home, "relocated-"));
    const result = runPlugin(`
      sessions.set("ses_relocated", undefined);
      const { registered } = await setup();
      await registered.prompt?.({ sessionID: "ses_relocated", messageID: "msg_relocated", prompt: { text: "relocated" }, delivery: "steer" });
      process.stdout.write(JSON.stringify({ commands: registered.commands.map(({ name }) => name), hooks: registered.hooks }));
    `, { SESSION_LOG_FIXTURE: "fixture/relocated", [variable]: relocated });
    expect(JSON.parse(result.stdout)).toEqual({ commands: ["session-log"], hooks: [] });
    expect(result.stderr).toContain("OpenCode root is relocated");
    expect(fs.readdirSync(relocated)).toEqual([]);
    expect(fs.existsSync(stateDirectory)).toBe(false);
  });
}

test("/session-log runs the package CLI and posts its output as a notice; a failure throws its message", () => {
  fs.unlinkSync(enabledFlag);
  const result = runPlugin(`
    const { registered } = await setup();
    const [command] = registered.commands;
    await command.execute({ sessionID: "ses_command", prompt: { text: "  " }, delivery: "steer" });
    await command.execute({ sessionID: "ses_command", prompt: { text: "status" }, delivery: "steer" });
    let failure = "";
    try { await command.execute({ sessionID: "ses_command", prompt: { text: "bogus" }, delivery: "steer" }); }
    catch (error) { failure = error.message; }
    process.stdout.write(JSON.stringify({ notices: registered.notices, failure }));
  `);
  const { notices, failure } = JSON.parse(result.stdout);
  const notice = { sessionID: "ses_command", text: "OpenCode: off", description: "OpenCode: off", resume: false };
  expect(notices).toEqual([notice, notice]);
  expect(failure).toBe("session-log: unknown command: bogus");
});

test("/session-log usage without a target reports the session it ran in", () => {
  const wrapperDirectory = fs.mkdtempSync(path.join(home, "bash-wrapper-"));
  const record = path.join(wrapperDirectory, "arguments");
  // Records the CLI invocation instead of running the native usage report.
  fs.writeFileSync(path.join(wrapperDirectory, "bash"), `#!/bin/sh\nprintf '%s\\n' "$@" > "${record}"\necho recorded\n`, { mode: 0o700 });
  runPlugin(`
    const { registered } = await setup();
    await registered.commands[0].execute({ sessionID: "ses_usage", prompt: { text: "usage" }, delivery: "steer" });
  `, { PATH: `${wrapperDirectory}:${process.env.PATH}` });
  expect(fs.readFileSync(record, "utf8").split("\n").slice(-3)).toEqual(["--arguments", "usage ses_usage", ""]);
});

test("the legacy cleanup runs `repair` once per installed package, whether or not a leftover remains", () => {
  const legacyCommand = path.join(configDirectory, "commands", "session-log.md");
  const stamp = path.join(stateDirectory, "legacy-cleanup.stamp");
  fs.mkdirSync(path.dirname(legacyCommand), { recursive: true });
  fs.writeFileSync(legacyCommand, LEGACY_COMMAND_TEXT);
  runPlugin("await sessionLog.cleanupLegacyLeftovers();");
  expect(fs.existsSync(legacyCommand)).toBe(false);
  expect(fs.readFileSync(stamp, "utf8")).toBe(`${packageRoot}\n${packageVersion}\n`);
  fs.writeFileSync(legacyCommand, LEGACY_COMMAND_TEXT);
  runPlugin("await sessionLog.cleanupLegacyLeftovers();");
  expect(fs.existsSync(legacyCommand)).toBe(true);
  fs.writeFileSync(stamp, "another package\n");
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
    await log.waitUntilIdle();
    const project = path.join(process.env.HOME, ".config/opencode/prompt-logs/fixture-dedupe");
    const dedupe = path.join(project, fs.readdirSync(project).find((name) => name.endsWith(".dedupe")));
    const pending = Array.from({ length: ${maxDedupeRecords} }, (_, index) =>
      ({ key: "pending-" + index, offset: 0, length: 0, sha256: "", committed: false }));
    const committed = [{ key: "old-committed", legacy: true, committed: true }];
    fs.writeFileSync(dedupe, [...pending, ...committed].map((record) => JSON.stringify(record) + "\\n").join(""));
    await prompt("ses_dedupe", "Second");
    await log.waitUntilIdle();
    const records = fs.readFileSync(dedupe, "utf8").split("\\n").filter(Boolean).map((line) => JSON.parse(line));
    process.stdout.write(JSON.stringify({ total: records.length, committed: records.filter((record) => record.committed).length }));
  `, { SESSION_LOG_FIXTURE: "fixture/dedupe" });
  expect(JSON.parse(result.stdout)).toEqual({ total: maxDedupeRecords, committed: 0 });
});
