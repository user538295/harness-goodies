import { afterAll, beforeAll, expect, test } from "bun:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";

const repoRoot = path.resolve(import.meta.dir, "..");
const pluginPath = path.join(import.meta.dir, "index.js");
const scratchHome = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "opencode-goodies-plugin-")));
let registered;
// Variables that relocate OpenCode's roots; the child must not inherit the developer's.
const RELOCATING_VARIABLES = ["XDG_CONFIG_HOME", "XDG_DATA_HOME", "OPENCODE_CONFIG_DIR"];
// A child that hangs fails its test instead of the suite.
const CHILD_TIMEOUT_MS = 30_000;
// A detached process the plugin might start gets this long to run before the child exits.
const DETACHED_SETTLE_MS = 300;
const EXECUTABLE_MODE = 0o700;
// The plugin starts no background process. A `bash` that records its arguments in $BASH_CALL_LOG
// proves it: the test asserts the log was never written, and a control call proves the stub records.
const recordingBashDirectory = path.join(scratchHome, "recording-bash");
const bashCallLog = path.join(scratchHome, "bash-calls.log");
const controlBashCallLog = path.join(scratchHome, "control-bash-calls.log");
fs.mkdirSync(recordingBashDirectory);
fs.writeFileSync(path.join(recordingBashDirectory, "bash"), '#!/bin/sh\necho "$@" >> "$BASH_CALL_LOG"\n', { mode: EXECUTABLE_MODE });
const childEnvironment = (callLog) => {
  const environment = { ...process.env };
  for (const name of RELOCATING_VARIABLES) delete environment[name];
  return { ...environment, HOME: scratchHome, PATH: `${recordingBashDirectory}:${process.env.PATH}`, BASH_CALL_LOG: callLog, OPENCODE_GOODIES_PLUGIN: pluginPath };
};

// The plugin runs in a child Bun process so the recording `bash` is found on its PATH. The context
// records every skill and agent registration, and every touch of any other part of the context.
// The child prints what the plugin registered as JSON.
const runSetup = () => {
  const result = spawnSync(process.execPath, ["--eval", `
    const { default: plugin } = await import(process.env.OPENCODE_GOODIES_PLUGIN);
    const registered = { skills: {}, agents: {}, touched: [] };
    const unexpected = (name) =>
      new Proxy({}, { get: (_, property) => { registered.touched.push(name + "." + String(property)); return () => {}; } });
    const context = {
      skill: { transform: async (callback) => callback({ add: (skill) => { registered.skills[skill.id] = skill; } }) },
      agent: {
        transform: async (callback) =>
          callback({
            update: (id, update) => {
              const agent = registered.agents[id] ?? { id, mode: "primary", permissions: [] };
              update(agent);
              registered.agents[id] = agent;
            },
          }),
      },
      command: unexpected("command"),
      session: unexpected("session"),
      event: unexpected("event"),
      location: unexpected("location"),
    };
    registered.returned = typeof (await plugin.setup(context));
    await new Promise((resolve) => setTimeout(resolve, ${DETACHED_SETTLE_MS}));
    process.stdout.write(JSON.stringify(registered));
  `], { env: childEnvironment(bashCallLog), encoding: "utf8", timeout: CHILD_TIMEOUT_MS });
  if (result.status !== 0) throw new Error(`plugin setup failed: ${result.error ?? result.signal ?? ""} ${result.stderr}`);
  return JSON.parse(result.stdout);
};

beforeAll(() => {
  registered = runSetup();
});

afterAll(() => {
  fs.rmSync(scratchHome, { recursive: true, force: true });
});

test("registers every skill directory under its directory name", () => {
  const directories = fs
    .readdirSync(path.join(repoRoot, "skills"))
    .filter((name) => fs.existsSync(path.join(repoRoot, "skills", name, "SKILL.md")));
  expect(Object.keys(registered.skills).sort()).toEqual(directories.sort());
});

test("plugin registers skills and agent with path, not location", () => {
  for (const [id, skill] of Object.entries(registered.skills)) {
    // OpenCode rejects a skill whose fields fail its schema, which disables the whole plugin.
    for (const field of ["name", "description", "content"]) expect(skill[field]).toBeTruthy();
    expect(skill.path).toBe(path.join(repoRoot, "skills", id, "SKILL.md"));
    expect(skill.location).toBeUndefined();
  }
  const skill = registered.skills["session-log"];
  expect(skill.name).toBe("session-log");
  expect(skill.description).toBe("Manage session prompt logging and usage totals (on / off / status / usage)");
  expect(skill.content.trimStart().startsWith("This skill runs the bundled script `session_log.py`")).toBe(true);
  const agents = fs.readdirSync(path.join(repoRoot, "agents")).filter((name) => name.endsWith(".md"));
  expect(Object.keys(registered.agents)).toHaveLength(agents.length);
  for (const agent of Object.values(registered.agents)) expect(agent.system).toBeTruthy();
});

test("folded YAML descriptions and CRLF frontmatter parse to plain text", () => {
  expect(registered.skills.bugfix.description).toStartWith("Fix a bug end-to-end via a four-agent pipeline:");
  expect(registered.skills.bugfix.description).not.toContain("\n");
  expect(registered.skills["wrap-up"].description).toStartWith("Close out a work session:");
  expect(registered.skills["wrap-up"].content).not.toContain("\r");
});

test("review workflows register as skills", () => {
  expect(registered.skills["da-review"].description).toStartWith("Single-pass devil's advocate review.");
  expect(registered.skills["iterative-review"].path).toBe(
    path.join(repoRoot, "skills", "iterative-review", "SKILL.md"),
  );
});

test("no plugin command, hook or event subscription is registered", () => {
  expect(registered.touched).toEqual([]);
  expect(registered.returned).toBe("undefined");
});

test("plugin starts no bash process, which the recording stub would log (PATH-resolved bash only)", () => {
  // Control: the stub does record a `bash` call made through the child's environment.
  spawnSync("bash", ["control"], { env: childEnvironment(controlBashCallLog) });
  expect(fs.readFileSync(controlBashCallLog, "utf8")).toBe("control\n");
  expect(fs.existsSync(bashCallLog)).toBe(false);
});

test("agent registers as a subagent with its description and body as system prompt", () => {
  const agent = registered.agents["devils-advocate"];
  expect(agent.mode).toBe("subagent");
  expect(agent.description).toStartWith("Use this agent when you need to critically evaluate");
  expect(agent.system).toStartWith("You are a relentless critical analyst");
});
