import { describe, expect, test } from "bun:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const USAGE_PATH = path.join(import.meta.dir, "session_log_usage.ts");
const CHILD_TIMEOUT_MS = 10_000;

// The parser resolves its sessions root from HOME at module load and runs main() on
// import, so every case runs it as a child process against a private HOME.
const createHome = () => {
	const home = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "omp-session-usage-")));
	const sessionsDir = path.join(home, ".omp", "agent", "sessions", "project");
	fs.mkdirSync(sessionsDir, { recursive: true });
	return { home, sessionsDir };
};

const writeSession = (file: string, header: Record<string, unknown>) => {
	fs.writeFileSync(file, `${JSON.stringify({ type: "session", ...header })}\n`);
};

const runUsage = (home: string, args: string[], extraEnv: Record<string, string> = {}, preload?: string) => {
	const env: Record<string, string | undefined> = { ...process.env, HOME: home, ...extraEnv };
	delete env.PI_CODING_AGENT_DIR;
	delete env.OMP_PROMPT_LOG_DIR;
	const command = preload ? ["bun", "--preload", preload, USAGE_PATH, ...args] : ["bun", USAGE_PATH, ...args];
	const result = Bun.spawnSync(command, { env, cwd: home, timeout: CHILD_TIMEOUT_MS });
	return { exitCode: result.exitCode, stdout: result.stdout.toString(), stderr: result.stderr.toString() };
};

describe("session id targets", () => {
	test("an exact session id wins over a longer id sharing its prefix", () => {
		const { home, sessionsDir } = createHome();
		writeSession(path.join(sessionsDir, "abc.jsonl"), { id: "abc" });
		writeSession(path.join(sessionsDir, "abcd.jsonl"), { id: "abcd" });

		const result = runUsage(home, ["abc"]);

		expect(result.stderr).toBe("");
		expect(result.stdout).toContain(`session: ${path.join(sessionsDir, "abc.jsonl")}\n`);
	});

	test("a prefix shared by several sessions stays ambiguous", () => {
		const { home, sessionsDir } = createHome();
		writeSession(path.join(sessionsDir, "abcd.jsonl"), { id: "abcd" });
		writeSession(path.join(sessionsDir, "abce.jsonl"), { id: "abce" });

		const result = runUsage(home, ["abc"]);

		expect(result.exitCode).not.toBe(0);
		expect(result.stderr).toContain("Session id is ambiguous");
	});
});

// Simulates an attacker racing every check: the session file is a regular file whenever
// it is listed or lstat-ed and is a symlink to a file outside the root whenever it is used.
const SYMLINK_SWAP_PRELOAD = String.raw`
import fs from "node:fs";
const { RACE_LEAF: leaf, RACE_ORIGINAL: original, RACE_ATTACKER: attacker } = process.env;
const realLstat = fs.lstatSync;
const realReaddir = fs.readdirSync;
const restore = () => {
	if (!realLstat(leaf).isSymbolicLink()) return;
	fs.unlinkSync(leaf);
	fs.copyFileSync(original, leaf);
};
fs.lstatSync = (target, ...rest) => {
	if (String(target) !== leaf) return realLstat(target, ...rest);
	restore();
	const result = realLstat(target, ...rest);
	fs.unlinkSync(leaf);
	fs.symlinkSync(attacker, leaf);
	return result;
};
fs.readdirSync = (...args) => {
	restore();
	return realReaddir(...args);
};
`;

describe("session path targets", () => {
	test("never reads through a session file swapped for a symlink after its checks", () => {
		const { home, sessionsDir } = createHome();
		const leaf = path.join(sessionsDir, "victim.jsonl");
		const original = path.join(home, "original.jsonl");
		const attacker = path.join(home, "attacker.jsonl");
		const preload = path.join(home, "swap-preload.js");
		writeSession(original, { id: "victim" });
		writeSession(attacker, { id: "victim", title: "ATTACKER_SECRET" });
		fs.copyFileSync(original, leaf);
		fs.writeFileSync(preload, SYMLINK_SWAP_PRELOAD);

		const result = runUsage(home, [leaf], { RACE_LEAF: leaf, RACE_ORIGINAL: original, RACE_ATTACKER: attacker }, preload);

		expect(result.stdout).not.toContain("ATTACKER_SECRET");
		expect(result.exitCode).not.toBe(0);
	});

	test("a FIFO session path is ignored without blocking", () => {
		const { home, sessionsDir } = createHome();
		const fifo = path.join(sessionsDir, "pipe.jsonl");
		expect(Bun.spawnSync(["mkfifo", fifo]).exitCode).toBe(0);

		const result = runUsage(home, [fifo]);

		expect(result.stderr).toContain("Invalid OMP session target");
	});
});
