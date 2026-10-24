// C4 — PriceBook port + price file. Logical only.
// The port and its Rates type belong to PricingRules (Use Cases). The price-file loader (Interface Adapters) implements the port;
// the file format below reuses Rates, so the file depends on the port's type, not the other way round.
// File: skills/session-log/prices.json, beside session_log.py (Q4). JSON keys are exactly the property names below (snake_case, as in the brief).
// Shape: { "models": [ { "prefixes": [...], "in", "out", "cache_read", "cache_write_5m", "cache_write_1h", "fast"?: {same five}, "source", "as_of" } ] }
namespace SessionLogSimple.C4PriceFile;

// ── Port (Use Cases)

/** USD per million tokens. */
model Rates {
  in: float64;
  out: float64;
  cache_read: float64;
  cache_write_5m: float64;
  cache_write_1h: float64;
}

interface PriceBook {
  /** No match -> no rate (the request is priced unknown, never $0). Recorded harness cost, when present, is used instead and this is not asked. */
  rateFor(modelName: string, fast: boolean): Rates | void;

  /** Missing or invalid prices.json: every model has no rate and this names the problem for status; commands still exit 0. */
  problem(): string | void;
}

// ── File format (read only by the loader)

model ModelPrice {
  /**
   * Model-name prefixes. The name is matched from its first `claude-` on, when it has one, so provider forms such as
   * `us.claude-opus-5-5-medium` and `us.anthropic.claude-opus-5-v1:0` match `claude-…` prefixes (as today's prompt_log_usage.jq:36-41).
   * The longest prefix matching across the whole file wins; a prefix listed twice makes the file invalid.
   */
  prefixes: string[];

  ...Rates;

  /** Rates for fast-mode requests, all five classes. Absent: fast requests of this model have no rate. */
  fast?: Rates;

  source: url;
  as_of: plainDate;
}

model PriceFile {
  models: ModelPrice[];
}
