// OpenCode V2 package plugin: registers this repository's skills and agents, and session-log's
// `/session-log` command and logging handlers. OpenCode loads one server entrypoint per package.
// Installed with `opencode plugin add github:user538295/harness-goodies`.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parse as parseYaml } from "yaml";

const packageRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const FRONTMATTER = /^---\n([\s\S]*?)\n---(?:\n|$)/;

const readMarkdown = (file) => {
  const text = fs.readFileSync(file, "utf8").replace(/\r\n/g, "\n");
  const match = FRONTMATTER.exec(text);
  if (!match) return { data: {}, body: text };
  return { data: parseYaml(match[1]) ?? {}, body: text.slice(match[0].length) };
};

const markdownFiles = (directory) =>
  fs
    .readdirSync(path.join(packageRoot, directory))
    .filter((name) => name.endsWith(".md"))
    .sort()
    .map((name) => ({ id: path.basename(name, ".md"), file: path.join(packageRoot, directory, name) }));

const loadSkills = () =>
  fs
    .readdirSync(path.join(packageRoot, "skills"))
    .sort()
    .map((id) => ({ id, file: path.join(packageRoot, "skills", id, "SKILL.md") }))
    .filter(({ file }) => fs.existsSync(file))
    .map(({ id, file }) => {
      const { data, body } = readMarkdown(file);
      return { id, name: data.name ?? id, description: data.description, location: file, content: body };
    });

const loadAgents = () =>
  markdownFiles("agents").map(({ id, file }) => {
    const { data, body } = readMarkdown(file);
    return { id: data.name ?? id, description: data.description, system: body.trim() };
  });

export default {
  id: "opencode-goodies",
  setup: async (ctx) => {
    const skills = loadSkills();
    const agents = loadAgents();

    await ctx.skill.transform((editor) => {
      for (const skill of skills) editor.add(skill);
    });

    await ctx.agent.transform((editor) => {
      for (const agent of agents) {
        editor.update(agent.id, (draft) => {
          draft.description = agent.description;
          draft.system = agent.system;
          draft.mode = "subagent";
        });
      }
    });

    // A session-log failure, even while loading its module, must not cost the skills and agents
    // registered above.
    try {
      const { setupSessionLog } = await import("../skills/session-log/adapters/opencode/session-log.js");
      return await setupSessionLog(ctx);
    } catch (error) {
      process.stderr.write(`session-log: ${error?.message || error}\n`);
    }
  },
};
