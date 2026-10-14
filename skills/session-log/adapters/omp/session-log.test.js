import { describe, expect, test } from "bun:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
	stableKey,
	projectSlug,
	contentText,
	messageKey,
	pathInside,
	finiteNumber,
	timestampNumber,
	nonNegativeInteger,
	usageLine,
	formatHms,
	createRun,
	queuedEventHandlers,
	composeFinalizeRecord,
	usageCommandArguments,
	SECURE_APPEND_SCRIPT,
} from "./session-log.js";

const ADAPTER_PATH = path.join(import.meta.dir, "session-log.js");
const PRIVATE_FILE_MODE = 0o600;
const DEDUPE_RECORD_LIMIT = 64;

const temporaryDirectory = () => fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "omp-session-log-")));

const createLogFile = (directory) => {
	const file = path.join(directory, "session_test.md");
	fs.writeFileSync(file, "# Prompts\n", { mode: PRIVATE_FILE_MODE });
	return file;
};

const runAppendScript = (file, dedupeKey) => {
	const stat = fs.statSync(file);
	return Bun.spawnSync(["python3", "-c", SECURE_APPEND_SCRIPT], {
		env: {
			...process.env,
			SESSION_LOG_FILE: file,
			SESSION_LOG_EXPECTED_DEV: String(stat.dev),
			SESSION_LOG_EXPECTED_INO: String(stat.ino),
			SESSION_LOG_DEDUPE: dedupeKey ? "1" : "0",
			SESSION_LOG_DEDUPE_KEY: dedupeKey,
		},
		stdin: Buffer.from("entry\n"),
	});
};

describe("secure append script", () => {
	test("drops committed dedupe records once pending records fill the limit", () => {
		const directory = temporaryDirectory();
		const file = createLogFile(directory);
		const pending = Array.from({ length: DEDUPE_RECORD_LIMIT }, (_, index) => ({ key: `pending-${index}`, offset: 0, length: 1, sha256: "0", committed: false }));
		const committed = { key: "committed-0", offset: 0, length: 1, sha256: "0", committed: true };
		const dedupeFile = path.join(directory, ".session_test.md.dedupe");
		fs.writeFileSync(dedupeFile, [...pending, committed].map((record) => `${JSON.stringify(record)}\n`).join(""), { mode: PRIVATE_FILE_MODE });

		const result = runAppendScript(file, "new");

		expect(result.stderr.toString()).toBe("");
		const records = fs.readFileSync(dedupeFile, "utf8").split("\n").filter(Boolean).map((line) => JSON.parse(line));
		expect(records).toHaveLength(DEDUPE_RECORD_LIMIT);
		expect(records.every((record) => !record.committed)).toBe(true);
	});
});

// ensureLog reads the module-level log root from HOME, so it runs in a child process
// with a private HOME. The lstat hook tampers with the log right after the first
// successful lstat of it, simulating an attacker racing the check.
const ENSURE_LOG_SWAP_RUNNER = String.raw`
import fs from "node:fs";
import path from "node:path";
const realLstat = fs.lstatSync;
let swapped = false;
fs.lstatSync = (target, ...rest) => {
	const result = realLstat(target, ...rest);
	if (!swapped && path.basename(String(target)) === "session_victim.md") {
		swapped = true;
		if (process.env.TAMPER_MODE) {
			fs.chmodSync(target, Number.parseInt(process.env.TAMPER_MODE, 8));
		} else {
			fs.unlinkSync(target);
			fs.symlinkSync(process.env.ATTACKER_FILE, target);
		}
	}
	return result;
};
const { ensureLog } = await import(process.env.ADAPTER_PATH);
try {
	ensureLog({ id: "victim", header: { cwd: "/project" }, file: "" }, "/project");
	console.log("ACCEPTED");
} catch (error) {
	console.log("REJECTED", error.message);
}
`;

const runEnsureLogRace = (extraEnv = {}) => {
	const home = temporaryDirectory();
	const attackerFile = path.join(home, "attacker.md");
	fs.writeFileSync(attackerFile, "**Session ID:** victim\n", { mode: PRIVATE_FILE_MODE });
	const env = { ...process.env, HOME: home, ADAPTER_PATH, ATTACKER_FILE: attackerFile, ...extraEnv };
	delete env.PI_CODING_AGENT_DIR;
	delete env.OMP_PROMPT_LOG_DIR;
	return Bun.spawnSync(["bun", "--eval", ENSURE_LOG_SWAP_RUNNER], { env }).stdout.toString();
};

describe("ensureLog", () => {
	test("rejects a log swapped for a symlink after the lstat check", () => {
		expect(runEnsureLogRace()).toContain("REJECTED OMP path is not a safe file");
	});
	test("rejects a log readable by others even when owner permissions are narrower than 0600", () => {
		expect(runEnsureLogRace({ TAMPER_MODE: "407" })).toContain("REJECTED OMP log file permissions are too broad");
	});
});

// The extension as omp loads it from a marketplace install: <plugins>/cache/plugins/<marketplace>___<plugin>___<version>
// holds the repository root, and <plugins>/node_modules/<package name> links the version installed now.
const PACKAGE_DIR = path.resolve(import.meta.dir, "../..");
const PLUGIN_PACKAGE_NAME = "opencode-goodies";
const EXECUTABLE_MODE = 0o755;
const BACKGROUND_WAIT_MS = 5000;
// Long enough for a background repair the extension should not have started to show up.
const NO_CLEANUP_WAIT_MS = 1000;
const BACKGROUND_TEST_TIMEOUT_MS = 2 * BACKGROUND_WAIT_MS;
const POLL_INTERVAL_MS = 50;
const recordingCli = (label) => `#!/usr/bin/env bash\nprintf '%s %s\\n' "${label}" "$*" >> "$HOME/cli-calls"\n`;
const pluginsDirectory = (home) => path.join(home, ".omp", "plugins");
const installPlugin = (home, version) => {
	const root = path.join(pluginsDirectory(home), "cache", "plugins", `user538295___omp-goodies___${version}`);
	fs.mkdirSync(path.join(root, "skills"), { recursive: true });
	fs.writeFileSync(path.join(root, "package.json"), JSON.stringify({ name: PLUGIN_PACKAGE_NAME, version }));
	fs.cpSync(PACKAGE_DIR, path.join(root, "skills", "session-log"), { recursive: true, filter: (source) => !source.includes(`${path.sep}tests`) });
	fs.writeFileSync(path.join(root, "skills", "session-log", "bin", "session-log"), recordingCli(version), { mode: EXECUTABLE_MODE });
	return root;
};
const linkInstalledPlugin = (home, root) => {
	const link = path.join(pluginsDirectory(home), "node_modules", PLUGIN_PACKAGE_NAME);
	fs.mkdirSync(path.dirname(link), { recursive: true });
	fs.rmSync(link, { force: true });
	fs.symlinkSync(root, link);
};
const extensionOf = (root) => path.join(root, "skills", "session-log", "adapters", "omp", "session-log.js");
// Loads the extension, optionally deletes a directory the way an upgrade deletes the old cache, then runs a command.
const LOAD_AND_RUN = String.raw`
import fs from "node:fs";
const loaded = await import(process.env.EXTENSION);
let handler;
loaded.default({ registerCommand: (_name, options) => { handler = options.handler; }, on() {} });
if (process.env.DELETE_BEFORE_COMMAND) fs.rmSync(process.env.DELETE_BEFORE_COMMAND, { recursive: true, force: true });
if (process.env.COMMAND) await handler(process.env.COMMAND, { ui: { notify() {} } });
`;
const loadExtension = (home, extension, env = {}) => {
	const environment = { ...process.env, HOME: home, EXTENSION: extension, ...env };
	delete environment.PI_CODING_AGENT_DIR;
	delete environment.OMP_PROMPT_LOG_DIR;
	const result = Bun.spawnSync(["bun", "--eval", LOAD_AND_RUN], { env: environment });
	expect(result.stderr.toString()).toBe("");
	expect(result.exitCode).toBe(0);
};
const cliCalls = (home) => {
	try { return fs.readFileSync(path.join(home, "cli-calls"), "utf8"); } catch { return ""; }
};
const waitForCliCalls = async (home, waitMs = BACKGROUND_WAIT_MS) => {
	const deadline = Date.now() + waitMs;
	while (!cliCalls(home) && Date.now() < deadline) await Bun.sleep(POLL_INTERVAL_MS);
	return cliCalls(home);
};

describe("plugin command", () => {
	test("runs the CLI of the package the extension loaded from", () => {
		const home = temporaryDirectory();
		const current = installPlugin(home, "1.0.0");
		linkInstalledPlugin(home, current);
		loadExtension(home, extensionOf(current), { COMMAND: "status" });
		expect(cliCalls(home)).toBe("1.0.0 --entrypoint omp --harness omp --arguments status\n");
	});
	test("runs the installed version's CLI once an upgrade deleted the loaded package", () => {
		const home = temporaryDirectory();
		const loaded = installPlugin(home, "1.0.0");
		linkInstalledPlugin(home, installPlugin(home, "2.0.0"));
		loadExtension(home, extensionOf(loaded), { COMMAND: "off", DELETE_BEFORE_COMMAND: loaded });
		expect(cliCalls(home)).toBe("2.0.0 --entrypoint omp --harness omp --arguments off\n");
	});
});

describe("legacy cleanup at load", () => {
	const legacyLeftovers = {
		"extension link": (agent) => {
			fs.mkdirSync(path.join(agent, "extensions"), { recursive: true });
			fs.symlinkSync(path.join(agent, "skills", "session-log", "adapters", "omp", "session-log.js"), path.join(agent, "extensions", "session-log.js"));
		},
		"session-log-omp extension": (agent) => {
			fs.mkdirSync(path.join(agent, "extensions"), { recursive: true });
			fs.writeFileSync(path.join(agent, "extensions", "session-log-omp.js"), "// universal-session-log: managed\n");
		},
		"seeded package copy": (agent) => {
			fs.mkdirSync(path.join(agent, "skills", "session-log"), { recursive: true });
			fs.writeFileSync(path.join(agent, "skills", "session-log", ".universal-session-log.manifest"), "format=1\n");
		},
		"skill-root link": (agent) => {
			fs.mkdirSync(path.join(agent, "skills"), { recursive: true });
			fs.symlinkSync(path.join(agent, "elsewhere"), path.join(agent, "skills", "session-log"));
		},
	};
	for (const [name, leave] of Object.entries(legacyLeftovers)) {
		test(`runs repair in the background for a leftover ${name}`, async () => {
			const home = temporaryDirectory();
			const plugin = installPlugin(home, "1.0.0");
			leave(path.join(home, ".omp", "agent"));
			loadExtension(home, extensionOf(plugin));
			expect(await waitForCliCalls(home)).toBe("1.0.0 --entrypoint omp --harness omp repair\n");
		}, BACKGROUND_TEST_TIMEOUT_MS);
	}
	test("runs nothing without leftovers", async () => {
		const home = temporaryDirectory();
		const plugin = installPlugin(home, "1.0.0");
		loadExtension(home, extensionOf(plugin));
		expect(await waitForCliCalls(home, NO_CLEANUP_WAIT_MS)).toBe("");
	}, BACKGROUND_TEST_TIMEOUT_MS);
});

describe("stableKey", () => {
	test("is deterministic and 64-hex", () => {
		const key = stableKey("a", "b", 1);
		expect(key).toMatch(/^[0-9a-f]{64}$/);
		expect(stableKey("a", "b", 1)).toBe(key);
	});
	test("separates fields so joins can't collide", () => {
		expect(stableKey("a", "bc")).not.toBe(stableKey("ab", "c"));
	});
});

describe("projectSlug", () => {
	test("sanitizes paths and strips leading slash", () => {
		expect(projectSlug("/Users/foo/bar baz")).toBe("Users-foo-bar-baz");
	});
	test("falls back for empty or missing input", () => {
		expect(projectSlug("")).toBe("unknown-project");
		expect(projectSlug(null)).toBe("unknown-project");
		expect(projectSlug("///")).toBe("unknown-project");
	});
});

describe("contentText", () => {
	test("passes strings through", () => {
		expect(contentText("hello")).toBe("hello");
	});
	test("joins text parts and drops non-text", () => {
		expect(contentText([{ type: "text", text: "a" }, { type: "image" }, { type: "text", text: "b" }])).toBe("a\nb");
	});
	test("returns empty for non-array non-string", () => {
		expect(contentText(42)).toBe("");
	});
});

describe("messageKey", () => {
	test("ignores non-assistant messages", () => {
		expect(messageKey({ role: "user", id: "x" })).toBe("");
	});
	test("prefers a stable identity", () => {
		expect(messageKey({ role: "assistant", responseId: "r1" })).toBe("id:r1");
		expect(messageKey({ role: "assistant", id: 7 })).toBe("id:7");
	});
	test("falls back to timestamp then anonymous", () => {
		expect(messageKey({ role: "assistant", timestamp: 1000 })).toBe("timestamp:1000");
		expect(messageKey({ role: "assistant" })).toBe(`anonymous:${JSON.stringify({ role: "assistant" })}`);
	});
});

describe("pathInside", () => {
	test("accepts equal and nested paths", () => {
		expect(pathInside("/a/b", "/a/b")).toBe(true);
		expect(pathInside("/a/b", "/a/b/c")).toBe(true);
	});
	test("rejects siblings and escapes", () => {
		expect(pathInside("/a/b", "/a/bc")).toBe(false);
		expect(pathInside("/a/b", "/a")).toBe(false);
	});
});

describe("numeric coercers", () => {
	test("finiteNumber parses numbers and numeric strings", () => {
		expect(finiteNumber(3)).toBe(3);
		expect(finiteNumber("4.5")).toBe(4.5);
		expect(finiteNumber("nope")).toBeUndefined();
		expect(finiteNumber(Infinity)).toBeUndefined();
	});
	test("timestampNumber accepts ISO strings", () => {
		expect(timestampNumber("2021-01-01T00:00:00Z")).toBe(Date.parse("2021-01-01T00:00:00Z"));
		expect(timestampNumber(1234)).toBe(1234);
	});
	test("nonNegativeInteger floors and clamps", () => {
		expect(nonNegativeInteger("2.9")).toBe(2);
		expect(nonNegativeInteger(-5)).toBe(0);
	});
});

describe("formatting", () => {
	test("formatHms renders HH:MM:SS", () => {
		expect(formatHms(0)).toBe("00:00:00");
		expect(formatHms(3661_000)).toBe("01:01:01");
		expect(formatHms(-1000)).toBe("00:00:00");
	});
	test("usageLine includes token and cost fields", () => {
		const line = usageLine({ input: 10, output: 5, reasoning: 0, cacheWrite: 0, cacheRead: 0, total: 15, cost: 0.5, models: new Set(["m"]), efforts: new Set(["high"]) });
		expect(line).toContain("input: 10");
		expect(line).toContain("total_tokens: 15");
		expect(line).toContain("cost: $0.5000");
		expect(line).toContain("model: m");
		expect(line).toContain("effort: high");
	});
});

describe("usageCommandArguments", () => {
	const file = "/home/u/.omp/agent/sessions/p/a b's.jsonl";
	test("targets the current session file when usage has no target", () => {
		expect(usageCommandArguments("usage", file)).toBe("usage '/home/u/.omp/agent/sessions/p/a b'\\''s.jsonl'");
		expect(usageCommandArguments("  usage\t", file)).toBe("usage '/home/u/.omp/agent/sessions/p/a b'\\''s.jsonl'");
	});
	test("keeps explicit targets and other commands unchanged", () => {
		expect(usageCommandArguments("usage --latest", file)).toBe("usage --latest");
		expect(usageCommandArguments("usage omp-session", file)).toBe("usage omp-session");
		expect(usageCommandArguments("status", file)).toBe("status");
	});
	test("keeps bare usage when the current session file is unknown", () => {
		expect(usageCommandArguments("usage", "")).toBe("usage");
	});
});

describe("createRun", () => {
	test("provides defaults and applies overrides", () => {
		const run = createRun({ prompt: "hi", enableSignature: "sig" });
		expect(run.messages).toEqual([]);
		expect(run.endSeen).toBe(false);
		expect(run.appendAttempts).toBe(0);
		expect(run.responseDedupeKey).toBe("");
		expect(run.prompt).toBe("hi");
		expect(run.enableSignature).toBe("sig");
	});
});

describe("queuedEventHandlers dispatch", () => {
	test("end handler collects messages and marks terminal", () => {
		const run = createRun();
		queuedEventHandlers.end(run, { event: { messages: [{ role: "assistant" }] }, terminal: true });
		expect(run.messages).toHaveLength(1);
		expect(run.endSeen).toBe(true);
		expect(run.settled).toBe(true);
	});
	test("end handler with terminal false keeps run open", () => {
		const run = createRun();
		queuedEventHandlers.end(run, { event: { messages: [] }, terminal: false });
		expect(run.endSeen).toBe(false);
	});
	test("default handler settles once an assistant message exists", () => {
		const run = createRun();
		const ctx = { sessionManager: { getEntries: () => [{ message: { role: "assistant" } }] } };
		queuedEventHandlers.default(run, { ctx });
		expect(run.messages).toHaveLength(1);
		expect(run.settled).toBe(true);
	});
});

describe("composeFinalizeRecord", () => {
	const ctx = { model: { id: "test-model" } };
	test("root sessions get a response header and working time", () => {
		const state = { id: "s1" };
		const root = { id: "s1" };
		const out = composeFinalizeRecord({ state, root, messages: [], ctx, start: 0, end: 1000, previousModel: "" });
		expect(out.model).toBe("test-model");
		expect(out.record).toContain("response");
		expect(out.record).toContain("working time: 00:00:01");
	});
	test("sub-agent sessions use the sub-agent prefix", () => {
		const state = { id: "sub" };
		const root = { id: "root" };
		const out = composeFinalizeRecord({ state, root, messages: [], ctx, start: 0, end: 0, previousModel: "" });
		expect(out.record).toContain("sub-agent: sub");
	});
	test("emits a switched line when the model changes", () => {
		const state = { id: "s1" };
		const root = { id: "s1" };
		const out = composeFinalizeRecord({ state, root, messages: [], ctx, start: 0, end: 0, previousModel: "old-model" });
		expect(out.record).toContain("switched: old-model → test-model");
	});
});
