#!/usr/bin/env bun
// universal-session-log: managed

import fs from "node:fs";
import os from "node:os";
import path from "node:path";

type JsonObject = Record<string, any>;
type SessionRecord = { file: string; header: JsonObject; entries: JsonObject[] };
// Money is an exact integer count of ten-thousandths of a dollar, so per-request
// amounts can be summed without binary-float drift; format only at the edge.
type Money = number;
type Usage = {
	input: number;
	output: number;
	reasoning: number;
	cacheRead: number;
	cacheWrite: number;
	total: number;
	spend: Money;
	models: Set<string>;
	efforts: Set<string>;
};
type Segment = { prompt: string; start: number; end: number; usage: Usage };
type Aggregate = { requests: Segment[]; usage: Usage; workMs: number; helpers: number };
type SessionIndex = { byFile: Map<string, SessionRecord>; bySessionId: Map<string, SessionRecord> };
type RecordedUsageOptions = { recorded: JsonObject; model: unknown; provider: unknown; effort: unknown };

const MONEY_SCALE = 10000;

function isErrnoException(error: unknown): error is { code?: string } {
	return typeof error === "object" && error !== null && "code" in error;
}

function isEnoent(error: unknown): boolean {
	return isErrnoException(error) && error.code === "ENOENT";
}

const canonicalPath = (value: string): string => {
	const resolved = path.resolve(value);
	try { return fs.realpathSync.native(resolved); } catch {}
	const suffix: string[] = [];
	let current = resolved;
	while (true) {
		try {
			const canonical = fs.realpathSync.native(current);
			return path.join(canonical, ...suffix.reverse());
		} catch (error) {
			if (!isEnoent(error)) return resolved;
			const parent = path.dirname(current);
			if (parent === current) return resolved;
			suffix.push(path.basename(current));
			current = parent;
		}
	}
};
const home = canonicalPath(os.homedir());
const containsSymlinkComponent = (value: string): boolean => {
	let current = path.resolve(value);
	while (true) {
		try {
			if (fs.lstatSync(current).isSymbolicLink()) return true;
		} catch (error) {
			if (!isEnoent(error)) throw error;
		}
		const parent = path.dirname(current);
		if (parent === current) return false;
		current = parent;
	}
};
const rawDefaultAgentDir = path.join(home, ".omp", "agent");
const rawDefaultLogDir = path.join(rawDefaultAgentDir, "prompt-logs");
const rawAgentDir = process.env.PI_CODING_AGENT_DIR || rawDefaultAgentDir;
const rawLogDir = process.env.OMP_PROMPT_LOG_DIR || rawDefaultLogDir;
if (containsSymlinkComponent(rawAgentDir) || containsSymlinkComponent(rawLogDir)) throw new Error("OMP root contains an unsafe symlink");
const defaultAgentDir = path.join(home, ".omp", "agent");
const agentDir = canonicalPath(process.env.PI_CODING_AGENT_DIR || defaultAgentDir);
const defaultLogDir = path.join(defaultAgentDir, "prompt-logs");
const configuredLogDir = canonicalPath(process.env.OMP_PROMPT_LOG_DIR || defaultLogDir);
if (agentDir !== canonicalPath(defaultAgentDir) || configuredLogDir !== canonicalPath(defaultLogDir)) throw new Error("OMP root is relocated; universal session-log does not support custom roots");
const sessionsDir = path.join(agentDir, "sessions");
const validId = (value: unknown): value is string => typeof value === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(value);

// Canonicalize the directory but keep the literal basename, so a not-yet-created
// leaf is still checked against a real, symlink-resolved parent.
function canonicalDirWithBasename(target: string): string {
	const absolute = path.resolve(target);
	return path.join(canonicalPath(path.dirname(absolute)), path.basename(absolute));
}

// Returns true when the sessions root itself is absent and the target *is* the
// root, so the caller may accept the path without walking any components.
function rootIsAbsent(root: string, resolved: string): boolean {
	try {
		if (fs.lstatSync(root).isSymbolicLink()) throw new Error(`OMP sessions root is a symlink: ${root}`);
	} catch (error) {
		if (isEnoent(error) && resolved === root) return true;
		if (isEnoent(error)) throw new Error(`OMP session path has a missing parent: ${root}`);
		throw error;
	}
	return false;
}

function assertSafeComponents(root: string, resolved: string): void {
	const components = resolved.slice(root.length).split(path.sep).filter(Boolean);
	let current = root;
	for (const [index, component] of components.entries()) {
		current = path.join(current, component);
		const isLeaf = index === components.length - 1;
		try {
			const stat = fs.lstatSync(current);
			if (stat.isSymbolicLink()) throw new Error(`OMP session path contains a symlink: ${current}`);
			if (!isLeaf && !stat.isDirectory()) throw new Error(`OMP session path parent is not a directory: ${current}`);
		} catch (error) {
			if (isEnoent(error) && isLeaf) return;
			if (isEnoent(error)) throw new Error(`OMP session path has a missing parent: ${current}`);
			throw error;
		}
	}
}

function safeSessionPath(value: string): string {
	const resolved = canonicalDirWithBasename(value);
	const root = canonicalDirWithBasename(sessionsDir);
	if (resolved !== root && !resolved.startsWith(`${root}${path.sep}`)) throw new Error(`OMP session path escapes the configured root: ${resolved}`);
	if (rootIsAbsent(root, resolved)) return resolved;
	assertSafeComponents(root, resolved);
	return resolved;
}

function sessionReference(value: string, base = sessionsDir): string {
	const candidate = path.isAbsolute(value) ? value : path.join(base, value);
	return safeSessionPath(candidate);
}

function printUsage(): never {
	console.error(`usage: session_log_usage.ts <session-id | transcript.jsonl | --latest> [--check]`);
	process.exit(2);
}

function finiteNumber(value: unknown): number | undefined {
	if (typeof value === "number" && Number.isFinite(value)) return value;
	if (typeof value === "string" && value.trim() !== "") {
		const parsed = Number(value);
		if (Number.isFinite(parsed)) return parsed;
	}
	return undefined;
}

function nonnegativeInteger(value: unknown): number | undefined {
	const parsed = finiteNumber(value);
	return parsed === undefined ? undefined : Math.max(0, Math.floor(parsed));
}

function parseMoney(value: unknown): Money {
	const parsed = finiteNumber(value);
	if (parsed === undefined || parsed <= 0) return 0;
	return Math.round(parsed * MONEY_SCALE);
}

function formatMoney(value: Money): string {
	return `$${(value / MONEY_SCALE).toFixed(4)}`;
}

function timestampMs(value: unknown): number {
	if (typeof value === "number" && Number.isFinite(value)) return value;
	if (typeof value === "string" && value.trim() !== "") {
		const numeric = Number(value);
		if (Number.isFinite(numeric)) return numeric;
		const parsed = Date.parse(value);
		if (Number.isFinite(parsed)) return parsed;
	}
	return 0;
}

function entryTime(entry: JsonObject, message = entry.message): number {
	return timestampMs(message?.timestamp) || timestampMs(entry.timestamp);
}

function completedTime(entry: JsonObject): number {
	return timestampMs(entry.message?.completedAt) || timestampMs(entry.completedAt) || entryTime(entry);
}

function textOf(content: unknown): string {
	if (typeof content === "string") return content;
	if (!Array.isArray(content)) return "";
	return content
		.map(item => {
			if (typeof item === "string") return item;
			return item?.type === "text" && typeof item.text === "string" ? item.text : "";
		})
		.filter(Boolean)
		.join("\n");
}

function isUserMessage(entry: JsonObject): boolean {
	return entry.message?.role === "user" && entry.message.attribution !== "agent";
}

function isUserCustomMessage(entry: JsonObject): boolean {
	return entry.attribution === "user" || entry.customType === "skill-prompt";
}

const promptExtractors: Record<string, (entry: JsonObject) => string | null> = {
	message: entry => (isUserMessage(entry) ? textOf(entry.message.content) : null),
	custom_message: entry => (isUserCustomMessage(entry) ? textOf(entry.content) : null),
};

function promptOf(entry: JsonObject): string | null {
	const extract = promptExtractors[entry.type];
	return extract ? extract(entry) : null;
}

function newUsage(): Usage {
	return { input: 0, output: 0, reasoning: 0, cacheRead: 0, cacheWrite: 0, total: 0, spend: 0, models: new Set(), efforts: new Set() };
}

function recordedNumber(recorded: JsonObject, names: string[]): number {
	for (const name of names) {
		const value = nonnegativeInteger(recorded[name]);
		if (value !== undefined) return value;
	}
	return 0;
}

function nativeRecordedNumber(recorded: JsonObject, names: string[]): number | undefined {
	for (const name of names) {
		const value = finiteNumber(recorded[name]);
		if (value !== undefined && value >= 0) return Math.floor(value);
	}
	return undefined;
}

function modelLabelOf(model: unknown, provider: unknown): string {
	const modelName = typeof model === "string" ? model : "";
	const providerName = typeof provider === "string" ? provider : "";
	if (!modelName) return "";
	return providerName && !modelName.includes("/") ? `${providerName}/${modelName}` : modelName;
}

function recordedSpend(recorded: JsonObject): Money {
	const rawCost = recorded.cost;
	return parseMoney(typeof rawCost === "object" && rawCost !== null ? rawCost.total : rawCost);
}

function addRecordedUsage(usageValue: Usage, options: RecordedUsageOptions): void {
	const { recorded, model, provider, effort } = options;
	if (!recorded || typeof recorded !== "object") return;
	const input = recordedNumber(recorded, ["input", "inputTokens", "input_tokens"]);
	const output = recordedNumber(recorded, ["output", "outputTokens", "output_tokens"]);
	const reasoning = recordedNumber(recorded, ["reasoning", "reasoningTokens", "reasoning_tokens"]);
	const cacheRead = recordedNumber(recorded, ["cacheRead", "cacheReadTokens", "cache_read"]);
	const cacheWrite = recordedNumber(recorded, ["cacheWrite", "cacheWriteTokens", "cache_write"]);
	usageValue.input += input;
	usageValue.output += output;
	usageValue.reasoning += reasoning;
	usageValue.cacheRead += cacheRead;
	usageValue.cacheWrite += cacheWrite;
	const total = nativeRecordedNumber(recorded, ["totalTokens", "total_tokens", "total"]);
	usageValue.total += total ?? input + output + reasoning + cacheRead + cacheWrite;
	usageValue.spend += recordedSpend(recorded);
	const modelLabel = modelLabelOf(model, provider);
	if (modelLabel) usageValue.models.add(modelLabel);
	if (typeof effort === "string" && effort) usageValue.efforts.add(effort);
}

function addAssistant(usageValue: Usage, message: JsonObject, currentEffort: string): void {
	if (message.role !== "assistant") return;
	const messageEffort = typeof message.thinkingLevel === "string" ? message.thinkingLevel : typeof message.effort === "string" ? message.effort : currentEffort;
	addRecordedUsage(usageValue, { recorded: message.usage, model: message.model, provider: message.provider, effort: messageEffort });
}

function addModelUsage(usageValue: Usage, entry: JsonObject, currentEffort: string): void {
	if (entry.type !== "model_usage") return;
	addRecordedUsage(usageValue, { recorded: entry.usage, model: entry.model, provider: entry.provider, effort: currentEffort });
}

function mergeUsage(target: Usage, source: Usage): void {
	target.input += source.input;
	target.output += source.output;
	target.reasoning += source.reasoning;
	target.cacheRead += source.cacheRead;
	target.cacheWrite += source.cacheWrite;
	target.total += source.total;
	target.spend += source.spend;
	for (const model of source.models) target.models.add(model);
	for (const effort of source.efforts) target.efforts.add(effort);
}

function isPlainRecord(value: unknown): value is JsonObject {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readEntries(file: string): JsonObject[] {
	let contents: string;
	try {
		contents = fs.readFileSync(file, "utf8");
	} catch (error) {
		if (isEnoent(error)) return [];
		throw error;
	}
	const entries: JsonObject[] = [];
	for (const line of contents.split("\n")) {
		if (!line.trim()) continue;
		try {
			const value = JSON.parse(line);
			if (!isPlainRecord(value)) throw new Error("record is not an object");
			entries.push(value);
		} catch {
			// OMP transcripts can be interrupted mid-write; ignore only the malformed line.
		}
	}
	return entries;
}

function sessionHeader(entries: JsonObject[]): JsonObject | null {
	return entries.find(entry => entry.type === "session") ?? null;
}

function walkJsonl(dir: string): string[] {
	const files: string[] = [];
	const safeDir = safeSessionPath(dir);
	let entries: fs.Dirent[];
	try {
		entries = fs.readdirSync(safeDir, { withFileTypes: true });
	} catch (error) {
		if (isEnoent(error)) return files;
		throw error;
	}
	for (const entry of entries) {
		const full = path.join(safeDir, entry.name);
		try {
			if (entry.isSymbolicLink()) throw new Error(`OMP session path contains a symlink: ${full}`);
			if (entry.isDirectory()) files.push(...walkJsonl(full));
			else if (entry.isFile() && entry.name.endsWith(".jsonl")) files.push(safeSessionPath(full));
		} catch (error) {
			if (!isEnoent(error)) throw error;
		}
	}
	return files;
}

function recordFromFile(file: string): SessionRecord | null {
	const resolved = safeSessionPath(file);
	const entries = readEntries(resolved);
	const header = sessionHeader(entries);
	if (!header) return null;
	const id = typeof header.id === "string" ? header.id : path.basename(resolved, ".jsonl");
	return validId(id) ? { file: resolved, header, entries } : null;
}

function loadSessionsFrom(dir: string): SessionRecord[] {
	return walkJsonl(dir).map(recordFromFile).filter((value): value is SessionRecord => value !== null);
}

function loadSessions(): SessionRecord[] {
	return loadSessionsFrom(sessionsDir);
}

function sessionId(record: SessionRecord): string {
	const value = typeof record.header.id === "string" ? record.header.id : path.basename(record.file, ".jsonl");
	return validId(value) ? value : "";
}

function indexSessions(records: SessionRecord[]): SessionIndex {
	const byFile = new Map<string, SessionRecord>();
	const bySessionId = new Map<string, SessionRecord>();
	for (const record of records) {
		byFile.set(record.file, record);
		const id = sessionId(record);
		if (id && !bySessionId.has(id)) bySessionId.set(id, record);
	}
	return { byFile, bySessionId };
}

function parentFile(record: SessionRecord, index: SessionIndex): string | null {
	const parent = record.header.parentSession;
	if (typeof parent !== "string" || !parent) return null;
	const bySessionId = index.bySessionId.get(parent);
	if (bySessionId) return bySessionId.file;
	let resolved: string;
	try { resolved = sessionReference(parent); } catch { return null; }
	return index.byFile.has(resolved) ? resolved : null;
}

function rootFile(record: SessionRecord, index: SessionIndex): string {
	let current = record;
	const seen = new Set<string>();
	while (true) {
		if (seen.has(current.file)) throw new Error(`Malformed OMP session ancestry cycle involving ${current.file}`);
		seen.add(current.file);
		const hasParent = typeof current.header.parentSession === "string" && current.header.parentSession.length > 0;
		const parent = parentFile(current, index);
		if (hasParent && !parent) throw new Error(`Malformed OMP session ancestry involving ${current.file}`);
		const next = parent ? index.byFile.get(parent) : undefined;
		if (!next) break;
		current = next;
	}
	return current.file;
}

function hasUnresolvedParent(record: SessionRecord, index: SessionIndex): boolean {
	const hasParent = typeof record.header.parentSession === "string" && record.header.parentSession.length > 0;
	return hasParent && !parentFile(record, index);
}

function recordFromPathTarget(target: string): SessionRecord | null {
	if (target === "--latest" || validId(target)) return null;
	let resolved: string;
	try {
		resolved = safeSessionPath(target);
		if (!fs.statSync(resolved).isFile()) return null;
	} catch (error) {
		if (!isEnoent(error)) throw error;
		return null;
	}
	return recordFromFile(resolved);
}

function rootsOf(records: SessionRecord[], index: SessionIndex): { roots: SessionRecord[]; malformed: Set<string> } {
	const malformed = new Set<string>();
	for (const record of records) {
		try { rootFile(record, index); } catch { malformed.add(record.file); }
	}
	const roots = records.filter(record =>
		!malformed.has(record.file) &&
		!parentFile(record, index) &&
		!hasUnresolvedParent(record, index),
	);
	return { roots, malformed };
}

function isRecordInCwd(record: SessionRecord, cwd: string): boolean {
	const recordCwd = record.header.cwd;
	return typeof recordCwd === "string" && recordCwd.length > 0 && path.resolve(recordCwd) === cwd;
}

function compareRecency(a: SessionRecord, b: SessionRecord): number {
	return modifiedTime(b) - modifiedTime(a) ||
		timestampMs(b.header.timestamp) - timestampMs(a.header.timestamp) ||
		a.file.localeCompare(b.file);
}

// The session the user is typing in is the one with the newest user prompt; a
// sibling session that is still streaming assistant output has a newer mtime.
function latestPromptTime(record: SessionRecord): number {
	let latest = 0;
	for (const entry of record.entries) {
		if (promptOf(entry) !== null) latest = Math.max(latest, entryTime(entry));
	}
	return latest;
}

function newestRoot(roots: SessionRecord[]): SessionRecord {
	const cwd = path.resolve(process.cwd());
	const inCwd = roots.filter(record => isRecordInCwd(record, cwd));
	const candidates = inCwd.length ? inCwd : roots;
	const promptTimes = new Map(candidates.map(record => [record, latestPromptTime(record)]));
	const newest = [...candidates].sort((a, b) => promptTimes.get(b)! - promptTimes.get(a)! || compareRecency(a, b))[0];
	if (!newest) throw new Error(`No OMP session found under ${sessionsDir}`);
	return newest;
}

function matchById(target: string, records: SessionRecord[], malformed: Set<string>): SessionRecord {
	const matches = records.filter(record => sessionId(record) === target || sessionId(record).startsWith(target));
	if (matches.length === 1) {
		if (malformed.has(matches[0].file)) throw new Error(`Malformed OMP session ancestry involving ${matches[0].file}`);
		return matches[0];
	}
	if (matches.length > 1) throw new Error(`Session id is ambiguous: ${matches.map(sessionId).join(", ")}`);
	throw new Error(`No OMP session found for: ${target}`);
}

function resolveTarget(target: string, records: SessionRecord[]): SessionRecord {
	const direct = recordFromPathTarget(target);
	if (direct) return direct;
	const index = indexSessions(records);
	const { roots, malformed } = rootsOf(records, index);
	if (target === "--latest") return newestRoot(roots);
	if (!validId(target)) throw new Error(`Invalid OMP session target: ${target}`);
	return matchById(target, records, malformed);
}

function modifiedTime(record: SessionRecord): number {
	try { return fs.statSync(record.file).mtimeMs; } catch { return timestampMs(record.header.timestamp); }
}

function cleanHead(value: string): string {
	return value.replace(/\s+/g, " ").slice(0, 60);
}

function formatHms(milliseconds: number): string {
	const seconds = Math.max(0, Math.floor(milliseconds / 1000));
	return [Math.floor(seconds / 3600), Math.floor((seconds % 3600) / 60), seconds % 60].map(value => String(value).padStart(2, "0")).join(":");
}

function formatUsage(value: Usage): string {
	const models = [...value.models].sort().join("+") || "-";
	const efforts = [...value.efforts].sort().join("+") || "-";
	return `est. used token: input: ${value.input}, output: ${value.output}, reasoning: ${value.reasoning}, cache_write: ${value.cacheWrite}, cache_read: ${value.cacheRead}, total_tokens: ${value.total}, cost: ${formatMoney(value.spend)}, model: ${models}, effort: ${efforts}`;
}

function responseKeyOf(message: JsonObject): string {
	const identity = message.responseId || message.messageId || message.id;
	if ((typeof identity === "string" && identity) || typeof identity === "number") return `id:${String(identity)}`;
	if (typeof message.timestamp === "string" || typeof message.timestamp === "number") return `timestamp:${String(message.timestamp)}`;
	return "";
}

function isAssistantEntry(entry: JsonObject): boolean {
	return entry.type === "message" && entry.message?.role === "assistant";
}

function latestResponseIndices(entries: JsonObject[]): Map<string, number> {
	const latest = new Map<string, number>();
	for (const [index, entry] of entries.entries()) {
		if (!isAssistantEntry(entry)) continue;
		const key = responseKeyOf(entry.message);
		if (key) latest.set(key, index);
	}
	return latest;
}

function summarize(requests: Segment[], helperUsage: Usage, helpers: number): Aggregate {
	const total = newUsage();
	let workMs = 0;
	for (const request of requests) {
		request.end = Math.max(request.end, request.start);
		workMs += request.end - request.start;
		mergeUsage(total, request.usage);
	}
	mergeUsage(total, helperUsage);
	return { requests, usage: total, workMs, helpers };
}

function aggregate(record: SessionRecord): Aggregate {
	const requests: Segment[] = [];
	const helperUsage = newUsage();
	let helpers = 0;
	let current: Segment | null = null;
	let currentEffort = "";
	const latestResponses = latestResponseIndices(record.entries);
	for (const [index, entry] of record.entries.entries()) {
		if (entry.type === "thinking_level_change" && typeof entry.thinkingLevel === "string") currentEffort = entry.thinkingLevel;
		const prompt = promptOf(entry);
		if (prompt !== null) {
			if (current) requests.push(current);
			current = { prompt, start: entryTime(entry), end: 0, usage: newUsage() };
		}
		if (entry.type === "model_usage") { helpers += 1; addModelUsage(current?.usage || helperUsage, entry, currentEffort); continue; }
		if (!isAssistantEntry(entry)) continue;
		const message = entry.message;
		const responseKey = responseKeyOf(message);
		if (responseKey && latestResponses.get(responseKey) !== index) continue;
		if (!current) current = { prompt: "", start: entryTime(entry), end: 0, usage: newUsage() };
		current.end = Math.max(current.end, completedTime(entry));
		addAssistant(current.usage, message, currentEffort);
	}
	if (current) requests.push(current);
	return summarize(requests, helperUsage, helpers);
}

function parseArgs(args: string[]): { target: string; check: boolean } {
	let target = "";
	let check = false;
	for (const arg of args) {
		if (arg === "--check") check = true;
		else if (arg === "--latest") target = "--latest";
		else if (arg === "--help" || arg === "-h") printUsage();
		else if (arg.startsWith("-")) printUsage();
		else if (target) throw new Error("Only one OMP session target is allowed");
		else target = arg;
	}
	return { target: target || "--latest", check };
}

function discoverFamily(records: SessionRecord[], selected: SessionRecord): SessionRecord[] {
	if (records.some(record => record.file === selected.file)) return records;
	const discovered = new Map(records.map(record => [record.file, record]));
	for (const record of loadSessionsFrom(path.dirname(selected.file))) discovered.set(record.file, record);
	let parent = selected.header.parentSession;
	while (typeof parent === "string" && parent) {
		let parentPath: string;
		try { parentPath = sessionReference(parent); } catch { break; }
		const parentRecord = recordFromFile(parentPath);
		if (!parentRecord) break;
		discovered.set(parentRecord.file, parentRecord);
		parent = parentRecord.header.parentSession;
	}
	return [...discovered.values()];
}

function compareFamily(a: SessionRecord, b: SessionRecord, rootFilePath: string): number {
	if (a.file === rootFilePath) return -1;
	if (b.file === rootFilePath) return 1;
	return modifiedTime(b) - modifiedTime(a);
}

function familyOf(records: SessionRecord[], root: SessionRecord, index: SessionIndex): SessionRecord[] {
	return records
		.filter(record => {
			try { return rootFile(record, index) === root.file; } catch { return false; }
		})
		.sort((a, b) => compareFamily(a, b, root.file));
}

function printReport(root: SessionRecord, rootAggregate: Aggregate, childAggregates: { child: SessionRecord; aggregate: Aggregate }[], check: boolean): void {
	console.log(`session: ${root.file}${root.header.title ? `  — ${root.header.title}` : ""}`);
	for (const [index, request] of rootAggregate.requests.entries()) {
		const startClock = request.start ? new Date(request.start).toTimeString().slice(0, 8) : "00:00:00";
		console.log(`${index + 1}. ${startClock} (working time ${formatHms(request.end - request.start)}) "${cleanHead(request.prompt)}"`);
		console.log(formatUsage(request.usage));
	}
	if (rootAggregate.helpers) console.log(`internal helpers: ${rootAggregate.helpers}`);
	for (const { child, aggregate: childAggregate } of childAggregates) {
		const helperLabel = childAggregate.helpers ? `, internal helpers: ${childAggregate.helpers}` : "";
		console.log(`sub-agent: ${sessionId(child)}, working time: ${formatHms(childAggregate.workMs)}${helperLabel}, jsonl: ${child.file}`);
		console.log(formatUsage(childAggregate.usage));
	}
	const total = newUsage();
	mergeUsage(total, rootAggregate.usage);
	for (const { aggregate: childAggregate } of childAggregates) mergeUsage(total, childAggregate.usage);
	console.log("---");
	console.log(`TOTAL: requests: ${rootAggregate.requests.length}, sub-agents: ${childAggregates.length}, working time: ${formatHms(rootAggregate.workMs)}`);
	console.log(formatUsage(total));
	if (check) console.log("check: OMP transcript usage is authoritative; native cost fields are preserved");
}

function main(): void {
	const { target, check } = parseArgs(process.argv.slice(2));
	const selected = resolveTarget(target, loadSessions());
	const records = discoverFamily(loadSessions(), selected);
	const index = indexSessions(records);
	const root = index.byFile.get(rootFile(selected, index)) ?? selected;
	const children = familyOf(records, root, index).filter(record => record.file !== root.file);
	const rootAggregate = aggregate(root);
	const childAggregates = children.map(child => ({ child, aggregate: aggregate(child) }));
	printReport(root, rootAggregate, childAggregates, check);
}

try { main(); } catch (error) { console.error(error instanceof Error ? error.message : String(error)); process.exit(1); }
