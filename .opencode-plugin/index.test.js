import { beforeAll, expect, test } from "bun:test";
import fs from "node:fs";
import path from "node:path";
import plugin from "./index.js";

const repoRoot = path.resolve(import.meta.dir, "..");
const registered = { skills: new Map(), agents: new Map(), commandTransforms: 0 };

beforeAll(async () => {
  const context = {
    skill: {
      transform: async (callback) => callback({ add: (skill) => registered.skills.set(skill.id, skill) }),
    },
    command: { transform: async () => registered.commandTransforms++ },
    agent: {
      transform: async (callback) =>
        callback({
          update: (id, update) => {
            const agent = registered.agents.get(id) ?? { id, mode: "primary", permissions: [] };
            update(agent);
            registered.agents.set(id, agent);
          },
        }),
    },
  };
  await plugin.setup(context);
});

test("registers every skill directory under its directory name", () => {
  const directories = fs
    .readdirSync(path.join(repoRoot, "skills"))
    .filter((name) => fs.existsSync(path.join(repoRoot, "skills", name, "SKILL.md")));
  expect([...registered.skills.keys()].sort()).toEqual(directories.sort());
});

test("skill carries frontmatter metadata, its SKILL.md location, and the body without frontmatter", () => {
  const skill = registered.skills.get("session-log");
  expect(skill.name).toBe("session-log");
  expect(skill.description).toBe("Manage session prompt logging and usage totals (on / off / status / usage / repair)");
  expect(skill.location).toBe(path.join(repoRoot, "skills", "session-log", "SKILL.md"));
  expect(skill.content.startsWith("<!-- universal-session-log: managed -->")).toBe(true);
});

test("folded YAML descriptions and CRLF frontmatter parse to plain text", () => {
  expect(registered.skills.get("bugfix").description).toStartWith("Fix a bug end-to-end via a four-agent pipeline:");
  expect(registered.skills.get("bugfix").description).not.toContain("\n");
  expect(registered.skills.get("wrap-up").description).toStartWith("Close out a work session:");
  expect(registered.skills.get("wrap-up").content).not.toContain("\r");
});

test("review workflows register as skills, not commands", () => {
  expect(registered.skills.get("da-review").description).toStartWith("Single-pass devil's advocate review.");
  expect(registered.skills.get("iterative-review").location).toBe(
    path.join(repoRoot, "skills", "iterative-review", "SKILL.md"),
  );
  expect(registered.commandTransforms).toBe(0);
});

test("agent registers as a subagent with its description and body as system prompt", () => {
  const agent = registered.agents.get("devils-advocate");
  expect(agent.mode).toBe("subagent");
  expect(agent.description).toStartWith("Use this agent when you need to critically evaluate");
  expect(agent.system).toStartWith("You are a relentless critical analyst");
});
