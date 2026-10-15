import { afterEach, describe, expect, test } from "bun:test";
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

// Every directory a test creates, removed after it.
const createdDirectories = [];
const temporaryDirectory = (prefix = "omp-session-log-") => {
	const directory = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), prefix)));
	createdDirectories.push(directory);
	return directory;
};
afterEach(() => {
	for (const directory of createdDirectories.splice(0)) fs.rmSync(directory, { recursive: true, force: true });
});

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

// Python puts the working directory first on sys.path for "python3 -c", and omp runs the extension in the
// user's project, so a project file named like a standard-library module the inline Python imports would run.
const HIJACKABLE_MODULES = ["json", "subprocess", "hashlib", "base64"];
// Logs one prompt through the extension's before_agent_start handler: creating the log, then appending to it.
const LOG_PROMPT_RUNNER = String.raw`
const { default: sessionLogOmp } = await import(process.env.ADAPTER_PATH);
const handlers = {};
sessionLogOmp({ on: (event, handler) => { handlers[event] = handler; }, registerCommand: () => {} });
const sessionManager = { getSessionId: () => "isolated", getSessionFile: () => "isolated.jsonl", getHeader: () => ({ cwd: process.cwd() }), getEntries: () => [] };
await handlers.before_agent_start({ prompt: "isolated prompt" }, { sessionManager, cwd: process.cwd() });
`;

describe("inline Python", () => {
	test("never imports a module from the project directory", () => {
		const home = temporaryDirectory();
		const project = temporaryDirectory("session-log-omp-project-");
		for (const module of HIJACKABLE_MODULES) {
			fs.writeFileSync(path.join(project, `${module}.py`),
				`open(__file__ + '.imported', 'w').close()\nraise SystemExit('project ${module}.py ran')\n`);
		}
		const logDirectory = path.join(home, ".omp/agent/prompt-logs");
		fs.mkdirSync(logDirectory, { recursive: true });
		fs.writeFileSync(path.join(logDirectory, ".enabled"), "");
		const env = { ...process.env, HOME: home, ADAPTER_PATH };
		delete env.PI_CODING_AGENT_DIR;
		delete env.OMP_PROMPT_LOG_DIR;
		Bun.spawnSync(["bun", "--eval", LOG_PROMPT_RUNNER], { env, cwd: project });
		expect(fs.readdirSync(project).filter((name) => name.endsWith(".imported"))).toEqual([]);
		const logs = fs.readdirSync(logDirectory, { recursive: true }).filter((name) => name.endsWith("session_isolated.md"));
		expect(logs).toHaveLength(1);
		expect(fs.readFileSync(path.join(logDirectory, logs[0]), "utf8")).toContain("isolated prompt");
	});
});

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
const REPOSITORY_ROOT = path.resolve(import.meta.dir, "../../../..");
const PLUGIN_PACKAGE_NAME = JSON.parse(fs.readFileSync(path.join(REPOSITORY_ROOT, "package.json"), "utf8")).name;
const PACKAGE_TESTS = "tests";
const EXECUTABLE_MODE = 0o755;
const BACKGROUND_WAIT_MS = 5000;
const BACKGROUND_TEST_TIMEOUT_MS = 2 * BACKGROUND_WAIT_MS;
const POLL_INTERVAL_MS = 50;
const FAILED_EXIT_CODE = 1;
// EX_TEMPFAIL: the CLI's exit status for a transient failure, such as another session-log command holding the install lock.
const TRANSIENT_FAILURE_EXIT_CODE = 75;
const NON_EXECUTABLE_MODE = 0o644;
// Long enough for the CLI to start its long child, which writes its pid, before the timeout fires even under load, so
// the timeout tests can require that pid instead of passing when the timeout struck first.
const LONG_CHILD_CLI_TIMEOUT_MS = 3000;
// A loader that hangs (say, on a long child the timeout left holding the CLI's output open) fails its test instead of
// blocking the suite until that child ends.
const LOADER_TIMEOUT_MS = 30_000;
const PACKAGE_REMOVED_MESSAGE = "session-log: the session-log package this omp session loaded is gone (upgraded or uninstalled); restart omp";
const LOCK_BUSY_ERROR = "session-log: another session-log command is already in progress for omp";
const BACKGROUND_REPAIR = "background:--entrypoint omp --harness omp repair";
const REPAIR_FAILURE_PREFIX = "session-log: legacy cleanup failed: ";
const REPAIR_FAILURE_SUFFIX = "; /session-log repair shows the details\n";
const LEGACY_CLEANUP_STAMP = "legacy-cleanup.stamp";
const KEPT_COPY_REPORT = "OMP: kept legacy package copy /home/u/.omp/agent/skills/session-log: SKILL.md was changed; move it aside";
const PRIVATE_DIRECTORY_MODE = 0o700;
const SEEDED_MANIFEST = ".universal-session-log.manifest";
const SEEDED_MANIFEST_HEAD = "format=1\nowner=universal-session-log\nversion=2.0.0\n";
const SHA256_HEX_LENGTH = 64;
const shellQuote = (value) => `'${value.replaceAll("'", "'\\''")}'`;
// Records each call, then prints each `stdout` and `stderr` line and exits with `exitCode`, the way a repair reports.
const recordingCli = (label, { stdout = [], stderr = [], exitCode = 0 } = {}) => [
	"#!/usr/bin/env bash",
	`printf '%s %s\\n' "${label}" "$*" >> "$HOME/cli-calls"`,
	...stdout.map((line) => `printf '%s\\n' ${shellQuote(line)}`),
	...stderr.map((line) => `printf '%s\\n' ${shellQuote(line)} >&2`),
	`exit ${exitCode}`,
	"",
].join("\n");
const isPackageTests = (source) => {
	const relative = path.relative(PACKAGE_DIR, source);
	return relative === PACKAGE_TESTS || relative.startsWith(`${PACKAGE_TESTS}${path.sep}`);
};
const pluginsDirectory = (home) => path.join(home, ".omp", "plugins");
const cliOf = (root) => path.join(root, "skills", "session-log", "bin", "session-log");
// With `behaviour` null the plugin keeps the package's real CLI; otherwise a recording stub replaces it.
const installPlugin = (home, version, behaviour = {}) => {
	const root = path.join(pluginsDirectory(home), "cache", "plugins", `user538295___omp-goodies___${version}`);
	fs.mkdirSync(path.join(root, "skills"), { recursive: true });
	fs.writeFileSync(path.join(root, "package.json"), JSON.stringify({ name: PLUGIN_PACKAGE_NAME, version }));
	fs.cpSync(PACKAGE_DIR, path.join(root, "skills", "session-log"), { recursive: true, filter: (source) => !isPackageTests(source) });
	if (behaviour === null) return root;
	fs.writeFileSync(cliOf(root), recordingCli(version, behaviour));
	// writeFileSync applies its mode only to a file it creates, and the copied CLI already exists.
	fs.chmodSync(cliOf(root), EXECUTABLE_MODE);
	return root;
};
const linkInstalledPlugin = (home, root) => {
	const link = path.join(pluginsDirectory(home), "node_modules", PLUGIN_PACKAGE_NAME);
	fs.mkdirSync(path.dirname(link), { recursive: true });
	fs.rmSync(link, { force: true });
	fs.symlinkSync(root, link);
};
const extensionOf = (root) => path.join(root, "skills", "session-log", "adapters", "omp", "session-log.js");
// Loads the extension LOADS times (default once), optionally deletes a directory the way an upgrade deletes the old
// cache, then runs a command; prints a failed command's message and then what the extension registered, including
// each background command its loads started ("background:<CLI arguments>"). With WAIT_FOR_STDERR_MS it stays alive until
// the extension writes to stderr (or that wait ends), so the outcome of a background repair reaches it. With NOTICES_FILE
// it appends each notice the command shows the user to that file, one JSON string per line.
const LOAD_AND_RUN = String.raw`
import fs from "node:fs";
if (process.env.WAIT_FOR_STDERR_MS) {
	const keepAlive = setTimeout(() => {}, Number(process.env.WAIT_FOR_STDERR_MS));
	const write = process.stderr.write.bind(process.stderr);
	process.stderr.write = (...chunks) => {
		clearTimeout(keepAlive);
		return write(...chunks);
	};
}
const registrations = [];
let loading = true;
const spawn = Bun.spawn;
Bun.spawn = (command, ...rest) => {
	if (loading) registrations.push("background:" + command.slice(1).join(" "));
	return spawn(command, ...rest);
};
const loaded = await import(process.env.EXTENSION);
let handler;
const pi = {
	registerCommand: (name, options) => { registrations.push("command:" + name); handler = options.handler; },
	on: (event) => { registrations.push("on:" + event); },
};
const options = process.env.CLI_TIMEOUT_MS ? { cliTimeoutMs: Number(process.env.CLI_TIMEOUT_MS) } : undefined;
for (let load = 0; load < Number(process.env.LOADS || 1); load += 1) loaded.default(pi, options);
loading = false;
if (process.env.DELETE_BEFORE_COMMAND) fs.rmSync(process.env.DELETE_BEFORE_COMMAND, { recursive: true, force: true });
if (process.env.COMMAND) {
	const notify = (message) => { if (process.env.NOTICES_FILE) fs.appendFileSync(process.env.NOTICES_FILE, JSON.stringify(message) + "\n"); };
	try { await handler(process.env.COMMAND, { ui: { notify } }); }
	catch (error) { console.log("command failed: " + error.message); }
}
console.log(JSON.stringify(registrations));
`;
// Loads the extension, swaps its state directory for a symlink to ELSEWHERE, then starts a session.
const SESSION_START_RUNNER = String.raw`
import fs from "node:fs";
const { default: sessionLogOmp } = await import(process.env.EXTENSION);
const handlers = {};
sessionLogOmp({ on: (event, handler) => { handlers[event] = handler; }, registerCommand: () => {} });
fs.rmSync(process.env.BREAK_STATE_DIR, { recursive: true });
fs.mkdirSync(process.env.ELSEWHERE);
fs.symlinkSync(process.env.ELSEWHERE, process.env.BREAK_STATE_DIR);
try {
	await handlers.session_start();
	console.log("session_start handled");
} catch (error) {
	console.log("session_start threw: " + error.message);
}
`;
// This process's environment without the variables that relocate the OMP roots.
const loaderEnvironment = () => {
	const environment = { ...process.env };
	delete environment.PI_CODING_AGENT_DIR;
	delete environment.OMP_PROMPT_LOG_DIR;
	return environment;
};
const loadExtension = (home, extension, env = {}) => {
	const result = Bun.spawnSync(["bun", "--eval", LOAD_AND_RUN], { env: { ...loaderEnvironment(), HOME: home, EXTENSION: extension, ...env }, timeout: LOADER_TIMEOUT_MS });
	expect(result.exitCode).toBe(0);
	return { stdout: result.stdout.toString(), stderr: result.stderr.toString() };
};
// The background commands one LOAD_AND_RUN started, from the registrations it prints last.
const backgroundCommands = (stdout) =>
	JSON.parse(stdout.trimEnd().split("\n").pop()).filter((registration) => registration.startsWith("background:"));
const cliCalls = (home) => {
	try { return fs.readFileSync(path.join(home, "cli-calls"), "utf8"); } catch { return ""; }
};
const waitForCliCalls = async (home, waitMs = BACKGROUND_WAIT_MS) => {
	const deadline = Date.now() + waitMs;
	while (!cliCalls(home) && Date.now() < deadline) await Bun.sleep(POLL_INTERVAL_MS);
	return cliCalls(home);
};
// Restores the plugin's recording CLI, loads the extension again, and tells whether that start ran the repair.
const repairRunsAtNextStart = async (home, plugin) => {
	fs.writeFileSync(cliOf(plugin), recordingCli("1.0.0"));
	fs.chmodSync(cliOf(plugin), EXECUTABLE_MODE);
	loadExtension(home, extensionOf(plugin));
	return await waitForCliCalls(home) === "1.0.0 --entrypoint omp --harness omp repair\n";
};
const leaveSessionLogOmpExtension = (agent) => {
	fs.mkdirSync(path.join(agent, "extensions"), { recursive: true });
	fs.writeFileSync(path.join(agent, "extensions", "session-log-omp.js"), "// universal-session-log: managed\n");
};

// Whether no process has `pid`.
const isGone = (pid) => {
	try {
		process.kill(pid, 0);
		return false;
	} catch (error) {
		return error.code === "ESRCH";
	}
};
const processEnds = async (pid, waitMs = BACKGROUND_WAIT_MS) => {
	const deadline = Date.now() + waitMs;
	while (!isGone(pid) && Date.now() < deadline) await Bun.sleep(POLL_INTERVAL_MS);
	return isGone(pid);
};
// Makes the CLI start a long child in its process group and wait for it; the child writes its own pid to `pidFile` first.
const startLongChild = (cli, pidFile) => fs.writeFileSync(cli, fs.readFileSync(cli, "utf8")
	.replace(/^#!.*\n/, (shebang) => `${shebang}sh -c 'echo $$ > "$1"; exec sleep 600' sh ${shellQuote(pidFile)} & wait\n`));
// The long child's pid, or 0 when it never wrote one.
const longChildPid = (pidFile) => {
	try {
		return Number(fs.readFileSync(pidFile, "utf8")) || 0;
	} catch (error) {
		if (error.code === "ENOENT") return 0;
		throw error;
	}
};

describe("plugin command", () => {
	test("runs the CLI of the package the extension loaded from", () => {
		const home = temporaryDirectory();
		const current = installPlugin(home, "1.0.0");
		linkInstalledPlugin(home, current);
		expect(loadExtension(home, extensionOf(current), { COMMAND: "status" }).stderr).toBe("");
		expect(cliCalls(home)).toBe("1.0.0 --entrypoint omp --harness omp --arguments status\n");
	});
	test("runs the installed version's CLI once an upgrade deleted the loaded package", () => {
		const home = temporaryDirectory();
		const loaded = installPlugin(home, "1.0.0");
		linkInstalledPlugin(home, installPlugin(home, "2.0.0"));
		expect(loadExtension(home, extensionOf(loaded), { COMMAND: "off", DELETE_BEFORE_COMMAND: loaded }).stderr).toBe("");
		expect(cliCalls(home)).toBe("2.0.0 --entrypoint omp --harness omp --arguments off\n");
	});
	test("asks for a restart once an upgrade deleted the loaded package and nothing links another", () => {
		const home = temporaryDirectory();
		const loaded = installPlugin(home, "1.0.0");
		const { stdout, stderr } = loadExtension(home, extensionOf(loaded), { COMMAND: "status", DELETE_BEFORE_COMMAND: loaded });
		expect(stderr).toBe("");
		expect(stdout.split("\n")[0]).toBe(`command failed: ${PACKAGE_REMOVED_MESSAGE}`);
		expect(cliCalls(home)).toBe("");
	});
	test("asks for a restart once an upgrade deleted a loaded package whose package.json names no package", () => {
		const home = temporaryDirectory();
		const loaded = installPlugin(home, "1.0.0");
		fs.writeFileSync(path.join(loaded, "package.json"), JSON.stringify({ version: "1.0.0" }));
		linkInstalledPlugin(home, installPlugin(home, "2.0.0"));
		const { stdout, stderr } = loadExtension(home, extensionOf(loaded), { COMMAND: "status", DELETE_BEFORE_COMMAND: loaded });
		expect(stderr).toBe("");
		expect(stdout.split("\n")[0]).toBe(`command failed: ${PACKAGE_REMOVED_MESSAGE}`);
		expect(cliCalls(home)).toBe("");
	});
	// Each leaves the loaded package's CLI in place but unable to start; the linked package must not stand in for it.
	const unstartableClis = {
		"whose interpreter is missing": {
			breakCli: (cli) => fs.writeFileSync(cli, "#!/nonexistent/interpreter\n"),
			error: "ENOENT: no such file or directory",
		},
		"that is not executable": {
			breakCli: (cli) => fs.chmodSync(cli, NON_EXECUTABLE_MODE),
			error: "EACCES: permission denied",
		},
	};
	for (const [name, { breakCli, error }] of Object.entries(unstartableClis)) {
		test(`reports the spawn error of a CLI ${name}`, () => {
			const home = temporaryDirectory();
			const loaded = installPlugin(home, "1.0.0");
			linkInstalledPlugin(home, installPlugin(home, "2.0.0"));
			breakCli(cliOf(loaded));
			const { stdout, stderr } = loadExtension(home, extensionOf(loaded), { COMMAND: "status" });
			expect(stderr).toBe("");
			// Bun words the spawn error itself; only its code and the CLI it names are this extension's.
			expect(stdout.split("\n")[0]).toStartWith(`command failed: ${error}`);
			expect(stdout.split("\n")[0]).toContain(cliOf(loaded));
			expect(cliCalls(home)).toBe("");
		});
	}
	const commandOutcomes = {
		"shows the CLI's output as a notice": { behaviour: { stdout: ["OMP: on", ""] }, failure: "", notice: "OMP: on" },
		"shows a failed CLI's error as a notice and fails the command with it": {
			behaviour: { stdout: ["partial"], stderr: ["session-log: boom"], exitCode: FAILED_EXIT_CODE },
			failure: "command failed: session-log: boom\n",
			notice: "session-log: boom",
		},
	};
	for (const [name, { behaviour, failure, notice }] of Object.entries(commandOutcomes)) {
		test(name, () => {
			const home = temporaryDirectory();
			const notices = path.join(home, "notices");
			const plugin = installPlugin(home, "1.0.0", behaviour);
			const { stdout } = loadExtension(home, extensionOf(plugin), { COMMAND: "on", NOTICES_FILE: notices });
			expect(stdout.slice(0, failure.length)).toBe(failure);
			expect(fs.readFileSync(notices, "utf8")).toBe(`${JSON.stringify(notice)}\n`);
		});
	}
	test("ends a command whose CLI outlives the timeout, with the processes it started, and names the timeout", async () => {
		const home = temporaryDirectory();
		const plugin = installPlugin(home, "1.0.0");
		const childPidFile = path.join(home, "cli-child");
		startLongChild(cliOf(plugin), childPidFile);
		try {
			const { stdout } = loadExtension(home, extensionOf(plugin), { COMMAND: "status", CLI_TIMEOUT_MS: String(LONG_CHILD_CLI_TIMEOUT_MS) });
			expect(stdout.split("\n")[0]).toBe(`command failed: session-log: status timed out after ${LONG_CHILD_CLI_TIMEOUT_MS} ms`);
			const pid = longChildPid(childPidFile);
			expect(pid).toBeGreaterThan(0);
			expect(await processEnds(pid)).toBe(true);
		} finally {
			// The test's own child, should the extension have left it running.
			const pid = longChildPid(childPidFile);
			if (pid && !isGone(pid)) process.kill(pid, "SIGKILL");
		}
	}, BACKGROUND_TEST_TIMEOUT_MS);
});

describe("unsupported OMP root", () => {
	const unsupportedRoots = {
		"relocated by a named profile": (home) => ({ PI_CODING_AGENT_DIR: path.join(home, "profiles", "work") }),
		"reached through a symlink": (home) => {
			fs.mkdirSync(path.join(home, "dotfiles", "omp-agent"), { recursive: true });
			fs.symlinkSync(path.join(home, "dotfiles", "omp-agent"), path.join(home, ".omp", "agent"));
			return {};
		},
		"with only the prompt-log root relocated": (home) => ({ OMP_PROMPT_LOG_DIR: path.join(home, "prompt-logs") }),
	};
	for (const [name, prepare] of Object.entries(unsupportedRoots)) {
		test(`${name} registers only the command, which leaves the error to the CLI`, () => {
			const home = temporaryDirectory();
			const plugin = installPlugin(home, "1.0.0");
			const env = prepare(home);
			const agent = env.PI_CODING_AGENT_DIR || path.join(home, ".omp", "agent");
			leaveSessionLogOmpExtension(agent);
			const { stdout, stderr } = loadExtension(home, extensionOf(plugin), { ...env, COMMAND: "status" });
			expect(stderr).toBe("");
			// No background repair either: it would be listed among the registrations.
			expect(stdout).toBe(`${JSON.stringify(["command:session-log"])}\n`);
			expect(cliCalls(home)).toBe("1.0.0 --entrypoint omp --harness omp --arguments status\n");
			for (const stateDir of [path.join(agent, "session-log"), path.join(home, ".omp", "agent", "session-log")]) {
				expect(fs.existsSync(path.join(stateDir, "runtime.json"))).toBe(false);
			}
		});
	}
});

describe("unsafe state directory", () => {
	test("registers only the command and names the error on stderr", () => {
		const home = temporaryDirectory();
		const plugin = installPlugin(home, "1.0.0");
		const agent = path.join(home, ".omp", "agent");
		const stateDir = path.join(agent, "session-log");
		fs.mkdirSync(path.join(home, "elsewhere"), { recursive: true });
		fs.mkdirSync(agent, { recursive: true });
		fs.symlinkSync(path.join(home, "elsewhere"), stateDir);
		leaveSessionLogOmpExtension(agent);
		const { stdout, stderr } = loadExtension(home, extensionOf(plugin));
		expect(stderr).toBe(`session-log: OMP path is not a safe directory: ${stateDir}\n`);
		// No background repair either: it would be listed among the registrations.
		expect(stdout).toBe(`${JSON.stringify(["command:session-log"])}\n`);
	});
	test("a session start names the error on stderr instead of throwing into omp", () => {
		const home = temporaryDirectory();
		const plugin = installPlugin(home, "1.0.0");
		const stateDir = path.join(home, ".omp", "agent", "session-log");
		const result = Bun.spawnSync(["bun", "--eval", SESSION_START_RUNNER], {
			env: { ...loaderEnvironment(), HOME: home, EXTENSION: extensionOf(plugin), BREAK_STATE_DIR: stateDir, ELSEWHERE: path.join(home, "elsewhere") },
		});
		expect(result.stdout.toString()).toBe("session_start handled\n");
		expect(result.stderr.toString()).toBe(`session-log: OMP path is not a safe directory: ${stateDir}\n`);
	});
});

// A pid no process has: probe upward from one that just exited until signalling it fails with ESRCH.
const unusedPid = () => {
	for (let pid = Bun.spawnSync(["true"]).pid; ; pid += 1) {
		try { process.kill(pid, 0); } catch (error) { if (error.code === "ESRCH") return pid; }
	}
};
// launchd or init: alive, and signalling it fails with EPERM rather than ESRCH for anyone but root.
const INIT_PID = 1;
const ROOT_UID = 0;
// A directory whose entries can be listed but not removed.
const READ_ONLY_DIRECTORY_MODE = 0o500;

describe("runtime markers at load", () => {
	const loadWithMarkers = (makeMarkers) => {
		const home = temporaryDirectory();
		const plugin = installPlugin(home, "1.0.0");
		const stateDir = path.join(home, ".omp", "agent", "session-log");
		fs.mkdirSync(stateDir, { recursive: true, mode: PRIVATE_DIRECTORY_MODE });
		const markers = makeMarkers(stateDir);
		const { stderr } = loadExtension(home, extensionOf(plugin));
		return { stateDir, markers, stderr };
	};
	test("prunes the per-process markers of dead processes and keeps live ones", () => {
		const { stateDir, markers: [dead, live], stderr } = loadWithMarkers((stateDir) => {
			const files = [unusedPid(), process.pid].map((pid) => path.join(stateDir, `runtime.${pid}.json`));
			for (const marker of files) fs.writeFileSync(marker, "{}\n", { mode: PRIVATE_FILE_MODE });
			return files;
		});
		expect(stderr).toBe("");
		expect(fs.existsSync(dead)).toBe(false);
		expect(fs.existsSync(live)).toBe(true);
		expect(fs.existsSync(path.join(stateDir, "runtime.json"))).toBe(true);
	});
	test.skipIf(process.getuid?.() === ROOT_UID)("keeps the marker of a process it may not signal", () => {
		const { markers: [marker], stderr } = loadWithMarkers((stateDir) => {
			const file = path.join(stateDir, `runtime.${INIT_PID}.json`);
			fs.writeFileSync(file, "{}\n", { mode: PRIVATE_FILE_MODE });
			return [file];
		});
		expect(stderr).toBe("");
		expect(fs.existsSync(marker)).toBe(true);
	});
	test("keeps a dead process's marker path that is not a regular file", () => {
		const { markers: [directory], stderr } = loadWithMarkers((stateDir) => {
			const entry = path.join(stateDir, `runtime.${unusedPid()}.json`);
			fs.mkdirSync(entry, { mode: PRIVATE_DIRECTORY_MODE });
			return [entry];
		});
		expect(stderr).toBe("");
		expect(fs.statSync(directory).isDirectory()).toBe(true);
	});
	test.skipIf(process.getuid?.() === ROOT_UID)("names a dead process's marker it cannot remove on stderr", () => {
		const { markers: [marker], stderr } = loadWithMarkers((stateDir) => {
			const file = path.join(stateDir, `runtime.${unusedPid()}.json`);
			fs.writeFileSync(file, "{}\n", { mode: PRIVATE_FILE_MODE });
			fs.chmodSync(stateDir, READ_ONLY_DIRECTORY_MODE);
			return [file];
		});
		fs.chmodSync(path.dirname(marker), PRIVATE_DIRECTORY_MODE);
		expect(stderr).toContain(`session-log: EACCES: permission denied, unlink '${marker}'\n`);
		expect(fs.existsSync(marker)).toBe(true);
	});
});

describe("legacy cleanup at load", () => {
	const legacyLeftovers = {
		"extension link": (agent) => {
			fs.mkdirSync(path.join(agent, "extensions"), { recursive: true });
			fs.symlinkSync(path.join(agent, "skills", "session-log", "adapters", "omp", "session-log.js"), path.join(agent, "extensions", "session-log.js"));
		},
		"session-log-omp extension": leaveSessionLogOmpExtension,
		"seeded package copy": (agent) => {
			fs.mkdirSync(path.join(agent, "skills", "session-log"), { recursive: true });
			fs.writeFileSync(path.join(agent, "skills", "session-log", ".universal-session-log.manifest"), "format=1\n");
		},
	};
	for (const [name, leave] of Object.entries(legacyLeftovers)) {
		test(`runs repair in the background for a leftover ${name}`, async () => {
			const home = temporaryDirectory();
			const plugin = installPlugin(home, "1.0.0");
			leave(path.join(home, ".omp", "agent"));
			expect(loadExtension(home, extensionOf(plugin)).stderr).toBe("");
			expect(await waitForCliCalls(home)).toBe("1.0.0 --entrypoint omp --harness omp repair\n");
		}, BACKGROUND_TEST_TIMEOUT_MS);
	}
	const noLeftovers = {
		"nothing left": () => {},
		// A skill-root link is the user's own (e.g. dotfiles); repair handles a session-log one when the user runs it.
		"only a skill-root link": (agent) => {
			fs.mkdirSync(path.join(agent, "skills"), { recursive: true });
			fs.symlinkSync(path.join(agent, "elsewhere"), path.join(agent, "skills", "session-log"));
		},
	};
	for (const [name, leave] of Object.entries(noLeftovers)) {
		test(`runs nothing with ${name}`, () => {
			const home = temporaryDirectory();
			const plugin = installPlugin(home, "1.0.0");
			leave(path.join(home, ".omp", "agent"));
			const { stdout, stderr } = loadExtension(home, extensionOf(plugin));
			expect(stderr).toBe("");
			expect(backgroundCommands(stdout)).toEqual([]);
		});
	}
	test("starts one repair however often omp calls the factory", () => {
		const home = temporaryDirectory();
		const plugin = installPlugin(home, "1.0.0");
		leaveSessionLogOmpExtension(path.join(home, ".omp", "agent"));
		const { stdout, stderr } = loadExtension(home, extensionOf(plugin), { LOADS: "2" });
		expect(stderr).toBe("");
		expect(backgroundCommands(stdout)).toEqual([BACKGROUND_REPAIR]);
	});
	const repairOutcomes = {
		"reports a failed repair's last error line on stderr": {
			behaviour: { stderr: ["session-log: first problem", "session-log: boom", ""], exitCode: FAILED_EXIT_CODE },
			expected: "session-log: legacy cleanup failed: boom; /session-log repair shows the details\n",
			retried: false,
		},
		"reports a failure by its exit status, not by its message": {
			behaviour: { stderr: [LOCK_BUSY_ERROR], exitCode: FAILED_EXIT_CODE },
			expected: `session-log: legacy cleanup failed: ${LOCK_BUSY_ERROR.slice("session-log: ".length)}; /session-log repair shows the details\n`,
			retried: false,
		},
		"reports a package copy the repair kept on stderr": {
			behaviour: { stdout: ["OMP: removed legacy adapter link /x", KEPT_COPY_REPORT] },
			expected: `session-log: ${KEPT_COPY_REPORT.slice("OMP: ".length)}\n`,
			retried: false,
		},
		"stays silent when another session-log command holds the install lock": {
			behaviour: { stderr: [LOCK_BUSY_ERROR], exitCode: TRANSIENT_FAILURE_EXIT_CODE },
			expected: "",
			retried: true,
		},
	};
	for (const [name, { behaviour, expected, retried }] of Object.entries(repairOutcomes)) {
		test(`${name}, and ${retried ? "retries it" : "does not run it again"} at the next start`, () => {
			const home = temporaryDirectory();
			const plugin = installPlugin(home, "1.0.0", behaviour);
			leaveSessionLogOmpExtension(path.join(home, ".omp", "agent"));
			const { stderr } = loadExtension(home, extensionOf(plugin), { WAIT_FOR_STDERR_MS: String(BACKGROUND_WAIT_MS) });
			expect(stderr).toBe(expected);
			expect(cliCalls(home)).toBe("1.0.0 --entrypoint omp --harness omp repair\n");
			expect(backgroundCommands(loadExtension(home, extensionOf(plugin)).stdout)).toEqual(retried ? [BACKGROUND_REPAIR] : []);
		}, BACKGROUND_TEST_TIMEOUT_MS);
	}
	test("runs a stamped repair again once a leftover appears", () => {
		const home = temporaryDirectory();
		const agent = path.join(home, ".omp", "agent");
		const plugin = installPlugin(home, "1.0.0", { stdout: [KEPT_COPY_REPORT] });
		leaveSessionLogOmpExtension(agent);
		loadExtension(home, extensionOf(plugin), { WAIT_FOR_STDERR_MS: String(BACKGROUND_WAIT_MS) });
		expect(fs.existsSync(path.join(agent, "session-log", LEGACY_CLEANUP_STAMP))).toBe(true);
		fs.symlinkSync(path.join(agent, "elsewhere"), path.join(agent, "extensions", "session-log.js"));
		expect(backgroundCommands(loadExtension(home, extensionOf(plugin)).stdout)).toEqual([BACKGROUND_REPAIR]);
	}, BACKGROUND_TEST_TIMEOUT_MS);
	test("runs a stamped repair again once an upgrade replaced the package", () => {
		const home = temporaryDirectory();
		const agent = path.join(home, ".omp", "agent");
		const loaded = installPlugin(home, "1.0.0", { stdout: [KEPT_COPY_REPORT] });
		leaveSessionLogOmpExtension(agent);
		loadExtension(home, extensionOf(loaded), { WAIT_FOR_STDERR_MS: String(BACKGROUND_WAIT_MS) });
		expect(fs.existsSync(path.join(agent, "session-log", LEGACY_CLEANUP_STAMP))).toBe(true);
		expect(backgroundCommands(loadExtension(home, extensionOf(loaded)).stdout)).toEqual([]);
		const upgraded = installPlugin(home, "2.0.0");
		expect(backgroundCommands(loadExtension(home, extensionOf(upgraded)).stdout)).toEqual([BACKGROUND_REPAIR]);
	}, BACKGROUND_TEST_TIMEOUT_MS);
	// A repair that never finished decided nothing: each is reported but not stamped, so the next start retries it.
	const unfinishedRepairs = {
		"cannot start": {
			breakCli: (cli) => fs.chmodSync(cli, NON_EXECUTABLE_MODE),
			// Bun words the spawn error itself; only its code and the CLI it names are this extension's.
			reasonParts: (cli) => ["EACCES", cli],
		},
		"is killed": {
			breakCli: (cli) => fs.writeFileSync(cli, "#!/usr/bin/env bash\nkill -KILL $$\n"),
			reasonParts: () => ["repair was killed by SIGKILL"],
		},
	};
	for (const [name, { breakCli, reasonParts }] of Object.entries(unfinishedRepairs)) {
		test(`reports a repair that ${name} and retries it at the next start`, async () => {
			const home = temporaryDirectory();
			const plugin = installPlugin(home, "1.0.0");
			const agent = path.join(home, ".omp", "agent");
			leaveSessionLogOmpExtension(agent);
			breakCli(cliOf(plugin));
			const { stderr } = loadExtension(home, extensionOf(plugin), { WAIT_FOR_STDERR_MS: String(BACKGROUND_WAIT_MS) });
			expect(stderr).toStartWith(REPAIR_FAILURE_PREFIX);
			expect(stderr).toEndWith(REPAIR_FAILURE_SUFFIX);
			for (const part of reasonParts(cliOf(plugin))) expect(stderr).toContain(part);
			expect(fs.existsSync(path.join(agent, "session-log", LEGACY_CLEANUP_STAMP))).toBe(false);
			expect(await repairRunsAtNextStart(home, plugin)).toBe(true);
		}, BACKGROUND_TEST_TIMEOUT_MS);
	}
	test("ends a repair that outlives the timeout, with the processes it started, reports it, and retries it at the next start", async () => {
		const home = temporaryDirectory();
		const plugin = installPlugin(home, "1.0.0");
		const agent = path.join(home, ".omp", "agent");
		leaveSessionLogOmpExtension(agent);
		const childPidFile = path.join(home, "cli-child");
		startLongChild(cliOf(plugin), childPidFile);
		try {
			const { stderr } = loadExtension(home, extensionOf(plugin), { CLI_TIMEOUT_MS: String(LONG_CHILD_CLI_TIMEOUT_MS), WAIT_FOR_STDERR_MS: String(BACKGROUND_WAIT_MS) });
			expect(stderr).toBe(`${REPAIR_FAILURE_PREFIX}repair timed out after ${LONG_CHILD_CLI_TIMEOUT_MS} ms${REPAIR_FAILURE_SUFFIX}`);
			const pid = longChildPid(childPidFile);
			expect(pid).toBeGreaterThan(0);
			expect(await processEnds(pid)).toBe(true);
			expect(fs.existsSync(path.join(agent, "session-log", LEGACY_CLEANUP_STAMP))).toBe(false);
			expect(await repairRunsAtNextStart(home, plugin)).toBe(true);
		} finally {
			// The test's own child, should the extension have left it running.
			const pid = longChildPid(childPidFile);
			if (pid && !isGone(pid)) process.kill(pid, "SIGKILL");
		}
	}, BACKGROUND_TEST_TIMEOUT_MS);
	// The same outcomes from the package's real CLI, so a reworded repair report or a changed lock exit status fails here.
	test("stays silent when the real CLI finds the install lock held by a live command", () => {
		const home = temporaryDirectory();
		const plugin = installPlugin(home, "1.0.0", null);
		const agent = path.join(home, ".omp", "agent");
		leaveSessionLogOmpExtension(agent);
		const lock = path.join(agent, "session-log", ".install.lock");
		fs.mkdirSync(lock, { recursive: true, mode: PRIVATE_DIRECTORY_MODE });
		const start = Bun.spawnSync(["ps", "-p", String(process.pid), "-o", "lstart="]).stdout.toString().trim();
		fs.writeFileSync(path.join(lock, "owner"), `${process.pid}\n${start}\n`);
		// Runs the real CLI and records its exit status, which proves the repair ran into the held lock.
		fs.renameSync(cliOf(plugin), `${cliOf(plugin)}.real`);
		fs.writeFileSync(cliOf(plugin), [
			"#!/usr/bin/env bash",
			'"$0.real" "$@"',
			"status=$?",
			'printf \'%s\\n\' "$status" >> "$HOME/cli-exit-status"',
			'exit "$status"',
			"",
		].join("\n"), { mode: EXECUTABLE_MODE });
		const { stderr } = loadExtension(home, extensionOf(plugin), { WAIT_FOR_STDERR_MS: String(BACKGROUND_WAIT_MS) });
		expect(stderr).toBe("");
		expect(fs.readFileSync(path.join(home, "cli-exit-status"), "utf8")).toBe(`${TRANSIENT_FAILURE_EXIT_CODE}\n`);
	}, BACKGROUND_TEST_TIMEOUT_MS);
	test("reports the seeded copy the real CLI keeps", () => {
		const home = temporaryDirectory();
		const plugin = installPlugin(home, "1.0.0", null);
		const seeded = path.join(home, ".omp", "agent", "skills", "session-log");
		fs.mkdirSync(seeded, { recursive: true });
		fs.writeFileSync(path.join(seeded, "SKILL.md"), "edited\n");
		fs.writeFileSync(path.join(seeded, SEEDED_MANIFEST), `${SEEDED_MANIFEST_HEAD}asset=SKILL.md\nsha256=${"0".repeat(SHA256_HEX_LENGTH)}\n`);
		const { stderr } = loadExtension(home, extensionOf(plugin), { WAIT_FOR_STDERR_MS: String(BACKGROUND_WAIT_MS) });
		expect(stderr).toBe(`session-log: kept legacy package copy ${seeded}: SKILL.md was changed; move it aside\n`);
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
