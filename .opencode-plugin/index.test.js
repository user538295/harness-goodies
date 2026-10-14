import { afterAll, beforeAll, expect, test } from "bun:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";

const repoRoot = path.resolve(import.meta.dir, "..");
const scratchHome = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "opencode-goodies-plugin-")));
let registered;
// Variables that relocate OpenCode's roots; the child must not inherit the developer's.
const RELOCATING_VARIABLES = ["XDG_CONFIG_HOME", "XDG_DATA_HOME", "OPENCODE_CONFIG_DIR"];
// A child that hangs fails its test instead of the suite.
const CHILD_TIMEOUT_MS = 30_000;
const childEnvironment = (pluginPath) => {
  const environment = { ...process.env };
  for (const name of RELOCATING_VARIABLES) delete environment[name];
  return { ...environment, HOME: scratchHome, OPENCODE_GOODIES_PLUGIN: pluginPath };
};

// The plugin's session-log setup writes below HOME, and Bun reads HOME once at startup, so the
// plugin runs in a child Bun process with a scratch HOME. `commandTransform` is the body of the
// context's command.transform; it prints what the plugin registered as JSON.
const runSetup = (commandTransform = "callback({ add: (definition) => registered.commands.push(definition.name) })", pluginPath = path.join(import.meta.dir, "index.js")) => {
  const result = spawnSync(process.execPath, ["--eval", `
    const { default: plugin } = await import(process.env.OPENCODE_GOODIES_PLUGIN);
    const registered = { skills: {}, agents: {}, commands: [], hooks: [] };
    const context = {
      location: { directory: process.env.HOME },
      skill: { transform: async (callback) => callback({ add: (skill) => { registered.skills[skill.id] = skill; } }) },
      command: { transform: async (callback) => { ${commandTransform}; } },
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
      session: { hook: async (name) => { registered.hooks.push(name); return { dispose: async () => {} }; }, get: async ({ sessionID }) => ({ id: sessionID }) },
      event: { subscribe: () => (async function* () {})() },
    };
    const cleanup = await plugin.setup(context);
    registered.cleanup = typeof cleanup;
    await cleanup?.();
    process.stdout.write(JSON.stringify(registered));
  `], { env: childEnvironment(pluginPath), encoding: "utf8", timeout: CHILD_TIMEOUT_MS });
  if (result.status !== 0) throw new Error(`plugin setup failed: ${result.stderr}`);
  return { registered: JSON.parse(result.stdout), stderr: result.stderr };
};

beforeAll(() => {
  registered = runSetup().registered;
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

test("skill carries frontmatter metadata, its SKILL.md location, and the body without frontmatter", () => {
  const skill = registered.skills["session-log"];
  expect(skill.name).toBe("session-log");
  expect(skill.description).toBe("Manage session prompt logging and usage totals (on / off / status / usage / repair)");
  expect(skill.location).toBe(path.join(repoRoot, "skills", "session-log", "SKILL.md"));
  expect(skill.content.startsWith("<!-- universal-session-log: managed -->")).toBe(true);
});

test("folded YAML descriptions and CRLF frontmatter parse to plain text", () => {
  expect(registered.skills.bugfix.description).toStartWith("Fix a bug end-to-end via a four-agent pipeline:");
  expect(registered.skills.bugfix.description).not.toContain("\n");
  expect(registered.skills["wrap-up"].description).toStartWith("Close out a work session:");
  expect(registered.skills["wrap-up"].content).not.toContain("\r");
});

test("review workflows register as skills; /session-log is the only command", () => {
  expect(registered.skills["da-review"].description).toStartWith("Single-pass devil's advocate review.");
  expect(registered.skills["iterative-review"].location).toBe(
    path.join(repoRoot, "skills", "iterative-review", "SKILL.md"),
  );
  expect(registered.commands).toEqual(["session-log"]);
});

test("session-log registers its prompt hook and setup returns its cleanup", () => {
  expect(registered.hooks).toEqual(["prompt"]);
  expect(registered.cleanup).toBe("function");
});

test("agent registers as a subagent with its description and body as system prompt", () => {
  const agent = registered.agents["devils-advocate"];
  expect(agent.mode).toBe("subagent");
  expect(agent.description).toStartWith("Use this agent when you need to critically evaluate");
  expect(agent.system).toStartWith("You are a relentless critical analyst");
});

test("a failing session-log setup leaves the skills and agents registered", () => {
  const failed = runSetup('throw new Error("command registry unavailable")');
  expect(Object.keys(failed.registered.skills)).toContain("session-log");
  expect(failed.registered.agents["devils-advocate"].mode).toBe("subagent");
  expect(failed.stderr).toContain("session-log: command registry unavailable");
});

test("a session-log module that fails to load leaves the skills and agents registered", () => {
  // A copy of the package whose session-log VERSION is missing, so the adapter throws on import.
  const copy = fs.mkdtempSync(path.join(scratchHome, "package-"));
  for (const entry of [".opencode-plugin", "skills", "agents", "package.json"]) {
    fs.cpSync(path.join(repoRoot, entry), path.join(copy, entry), { recursive: true });
  }
  fs.symlinkSync(path.resolve(Bun.resolveSync("yaml/package.json", import.meta.dir), "../.."), path.join(copy, "node_modules"));
  fs.unlinkSync(path.join(copy, "skills", "session-log", "VERSION"));
  const broken = runSetup(undefined, path.join(copy, ".opencode-plugin", "index.js"));
  expect(Object.keys(broken.registered.skills)).toContain("session-log");
  expect(broken.registered.agents["devils-advocate"].mode).toBe("subagent");
  expect(broken.registered.commands).toEqual([]);
  expect(broken.stderr).toContain("session-log: ENOENT");
});
