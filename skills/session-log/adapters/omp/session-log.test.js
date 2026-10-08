import { describe, expect, test } from "bun:test";
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
} from "./session-log.js";

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
