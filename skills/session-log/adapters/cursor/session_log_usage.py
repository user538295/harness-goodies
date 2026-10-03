#!/usr/bin/env python3
# universal-session-log: managed
import json
import os
import re
import sqlite3
import sys
import time
from contextlib import closing
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ADAPTERS_PATH = Path(__file__).resolve().parents[1]
NATIVE_PATH = str(ADAPTERS_PATH / "native")
if NATIVE_PATH not in sys.path:
    sys.path.insert(0, NATIVE_PATH)
from session_log_hook import SAFE_ID, TOKEN_FIELDS, TOKENS_SUFFIX, is_count, is_untrusted_descriptor, workspace_key

PRICES_PATH = ADAPTERS_PATH / "claude" / "scripts" / "prompt_log_prices.json"
READ_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_NONBLOCK
TOKEN_FILE_PREFIX = "session_"
STATE_DB_PATHS = {
    "darwin": Path("Library/Application Support/Cursor/User/globalStorage/state.vscdb"),
    "linux": Path(".config/Cursor/User/globalStorage/state.vscdb"),
}
RECORD_KEYS = {"prompt": "generation_id", "stop": "generation_id", "subagent": "child_conversation_id"}
# tokens * ($/MTok) = cents * 1e4, as in prompt_log_usage.jq.
TOKEN_CENTS_DIVISOR = 10000
CENTS_PER_DOLLAR = 100
MS_PER_SECOND = 1000
SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600
MISSING_CLOCK = "00:00:00"
MISSING_FIELD = "-"
UNKNOWN_EFFORT = "unknown"
UNKNOWN_SUBAGENT_TYPE = "unknown"
NO_CONTEXT = "no contextTokensUsed in Cursor state database"
# Cursor stores no subagent token usage; the final context size is the only per-subagent figure, so it never joins TOTAL.
UNTRACKED = "(not added to TOTAL; token usage not recorded client-side)"
SUBAGENT_QUERY = """
select substr(key, length('composerData:') + 1),
    case when json_type(value, '$.contextTokensUsed') = 'integer' then json_extract(value, '$.contextTokensUsed') end,
    json_extract(value, '$.subagentInfo.subagentTypeName')
from cursorDiskKV
where key like 'composerData:%' and json_extract(value, '$.subagentInfo.rootParentConversationId') = ?
"""


class SubagentsUnavailable(Exception):
    pass


@dataclass(frozen=True)
class SessionRecords:
    generations: list
    prompts: dict
    stops: dict
    subagents: dict


@dataclass(frozen=True)
class Bucket:
    model: str
    fast: bool = False
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0

    @property
    def key(self):
        return self.model + ("|fast" if self.fast else "")

    def plus(self, other):
        return replace(self, input=self.input + other.input, output=self.output + other.output,
                       cache_read=self.cache_read + other.cache_read,
                       cache_write=self.cache_write + other.cache_write)


@dataclass(frozen=True)
class StateSubagent:
    context_tokens: object
    type_name: object


def fail(message):
    print(message, file=sys.stderr)
    raise SystemExit(1)


def latest_token_file(logs_root, workspace="*", session="*"):
    files = [
        path for path in logs_root.glob(f"{workspace}/{TOKEN_FILE_PREFIX}{session}{TOKENS_SUFFIX}")
        if path.is_file() and not path.is_symlink()
    ]
    return max(files, key=lambda path: path.stat().st_mtime) if files else None


def latest_workspace_token_file(logs_root, cwd):
    # Cursor hooks key logs by the workspace root, so usage from a subdirectory must search upward.
    for directory in (cwd, *cwd.parents):
        path = latest_token_file(logs_root, workspace=workspace_key({"cwd": str(directory)}))
        if path is not None:
            return path
    return None


def resolve_token_file(logs_root, target):
    if target == "--latest":
        path = latest_workspace_token_file(logs_root, Path(os.getcwd()))
        if path is None:
            fail("no Cursor token usage records found for the current project")
        return path
    if not SAFE_ID.fullmatch(target):
        fail(f"invalid Cursor session id: {target}")
    path = latest_token_file(logs_root, session=target)
    if path is None:
        fail(f"Cursor session has no token usage records: {target}")
    return path


def open_token_file(path):
    try:
        descriptor = os.open(path, READ_FLAGS)
    except OSError as error:
        fail(f"cannot read Cursor token usage records: {error}")
    if is_untrusted_descriptor(os.fstat(descriptor)):
        os.close(descriptor)
        fail(f"unsafe Cursor token usage file: {path}")
    return os.fdopen(descriptor, encoding="utf-8", errors="replace")


def is_safe_id(value):
    return isinstance(value, str) and SAFE_ID.fullmatch(value) is not None


def parse_record(line):
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict) or record.get("event") not in RECORD_KEYS:
        return None
    return record if is_safe_id(record.get(RECORD_KEYS[record["event"]])) else None


def read_records(handle):
    # Hooks append without deduplication; a redelivered record counts once, last record wins.
    by_event = {event: {} for event in RECORD_KEYS}
    generations = {}
    for line in handle:
        record = parse_record(line)
        if record is None:
            continue
        key = record[RECORD_KEYS[record["event"]]]
        by_event[record["event"]][key] = record
        if record["event"] != "subagent":
            generations.setdefault(key, None)
    return SessionRecords(list(generations), by_event["prompt"], by_event["stop"], by_event["subagent"])


def count(record, key):
    value = record.get(key)
    return value if is_count(value) else 0


def epoch(record, key):
    value = record.get(key)
    return value if is_count(value) else None


def text(record, key):
    value = record.get(key)
    return value if isinstance(value, str) else None


def fmt_hms(seconds):
    return (f"{seconds // SECONDS_PER_HOUR:02d}:{seconds % SECONDS_PER_HOUR // SECONDS_PER_MINUTE:02d}"
            f":{seconds % SECONDS_PER_MINUTE:02d}")


def money(cents):
    rounded = int(Decimal(cents).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return f"${rounded // CENTS_PER_DOLLAR}.{rounded % CENTS_PER_DOLLAR:02d}"


# Longest contained price key wins; None marks a model missing from the table.
def rate(bucket, prices):
    if bucket.fast:
        return prices["fast"]
    model = re.sub(r".*claude-", "", bucket.model, count=1)
    matches = sorted((key for key in prices["per_mtok"] if key in model), key=len)
    return prices["per_mtok"][matches[-1]] if matches else None


def bucket_cents(bucket, prices):
    price = rate(bucket, prices)
    if price is None:
        return 0
    multipliers = prices["mult"]
    return ((bucket.input * price["in"])
            + (bucket.output * price["out"])
            + (bucket.cache_read * price["in"] * multipliers["cache_read"])
            + (bucket.cache_write * price["in"] * multipliers["cache_5m"])) / TOKEN_CENTS_DIVISOR


def est_line(buckets, efforts, prices):
    ordered = [buckets[key] for key in sorted(buckets)]
    input_tokens = sum(bucket.input for bucket in ordered)
    output_tokens = sum(bucket.output for bucket in ordered)
    cache_create = sum(bucket.cache_write for bucket in ordered)
    cache_read = sum(bucket.cache_read for bucket in ordered)
    cents = sum(bucket_cents(bucket, prices) for bucket in ordered)
    models = "+".join(bucket.model + (":fast" if bucket.fast else "") + ("?" if rate(bucket, prices) is None else "")
                      for bucket in ordered)
    return (f"est. used token: input: {input_tokens}, output: {output_tokens}, cache_create: {cache_create}, "
            f"cache_read: {cache_read}, total_tokens: {input_tokens + output_tokens + cache_create + cache_read}, "
            f"price: {money(cents)}, model: {models or MISSING_FIELD}, "
            f"effort: {'+'.join(sorted(efforts)) or MISSING_FIELD}")


def stop_bucket(stop):
    # Cursor's input_tokens includes cache reads and writes; Claude's input excludes them.
    if not any(is_count(stop.get(key)) for key in TOKEN_FIELDS):
        return None
    cache_read = count(stop, "cache_read_tokens")
    cache_write = count(stop, "cache_write_tokens")
    return Bucket(model=text(stop, "model") or "", fast=stop.get("fast") is True,
                  input=max(0, count(stop, "input_tokens") - cache_read - cache_write),
                  output=count(stop, "output_tokens"), cache_read=cache_read, cache_write=cache_write)


def ordered_generations(records):
    def started(generation):
        return epoch(records.prompts.get(generation, {}), "started_at")
    known = sorted((generation for generation in records.generations if started(generation) is not None), key=started)
    return known + [generation for generation in records.generations if started(generation) is None]


class Totals:
    def __init__(self):
        self.buckets = {}
        self.efforts = set()
        self.working_seconds = 0

    def add(self, bucket, effort, working_seconds):
        self.working_seconds += working_seconds
        if bucket is None:
            return
        previous = self.buckets.get(bucket.key)
        self.buckets[bucket.key] = previous.plus(bucket) if previous else bucket
        self.efforts.add(effort)


def request_report(records, prices):
    lines = []
    totals = Totals()
    for number, generation in enumerate(ordered_generations(records), start=1):
        prompt = records.prompts.get(generation, {})
        stop = records.stops.get(generation, {})
        started = epoch(prompt, "started_at")
        ended = epoch(stop, "ended_at")
        working = ended - started if started is not None and ended is not None and ended >= started else 0
        clock = time.strftime("%H:%M:%S", time.localtime(started)) if started is not None else MISSING_CLOCK
        bucket = stop_bucket(stop)
        effort = text(stop, "effort") or UNKNOWN_EFFORT
        totals.add(bucket, effort, working)
        buckets = {bucket.key: bucket} if bucket else {}
        lines.append(f'{number}. {clock} (working time {fmt_hms(working)}) "{text(prompt, "head") or ""}"')
        lines.append(est_line(buckets, {effort} if bucket else set(), prices))
    return lines, totals


def state_db_path(platform):
    relative = STATE_DB_PATHS.get(platform)
    if relative is None:
        raise SubagentsUnavailable(f"unsupported platform: {platform}")
    path = Path.home() / relative
    if not path.is_file():
        raise SubagentsUnavailable(f"Cursor state database not found: {path}")
    return path


def state_subagents(session_id, platform=sys.platform):
    db_path = state_db_path(platform)
    try:
        with closing(sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)) as connection:
            rows = connection.execute(SUBAGENT_QUERY, (session_id,)).fetchall()
    except sqlite3.Error as error:
        raise SubagentsUnavailable(f"cannot read Cursor state database: {error}") from error
    return {child: StateSubagent(context if is_count(context) else None,
                                 type_name if isinstance(type_name, str) else None)
            for child, context, type_name in rows if is_safe_id(child)}


def subagent_jsonl(hook, child, transcript):
    candidates = [text(hook, "agent_transcript_path")]
    if transcript is not None:
        candidates.append(str(Path(transcript).parent / "subagents" / f"{child}.jsonl"))
    return next((path for path in candidates if path and Path(path).is_file()), MISSING_FIELD)


def subagent_blocks(records, session_id, transcript):
    try:
        state, unavailable = state_subagents(session_id), None
    except SubagentsUnavailable as error:
        state, unavailable = {}, f"unavailable: {error}"
    blocks = []
    for child in sorted(records.subagents.keys() | state.keys()):
        hook = records.subagents.get(child, {})
        row = state.get(child, StateSubagent(None, None))
        kind = text(hook, "subagent_type") or row.type_name or UNKNOWN_SUBAGENT_TYPE
        context = unavailable or (f"unavailable: {NO_CONTEXT}" if row.context_tokens is None
                                  else f"{row.context_tokens} tokens")
        blocks.append(f"sub-agent: {kind} ({child}), "
                      f"working time: {fmt_hms(count(hook, 'duration_ms') // MS_PER_SECOND)}, "
                      f"jsonl: {subagent_jsonl(hook, child, transcript)}\n"
                      f"final context: {context} {UNTRACKED}")
    return blocks


def session_transcript(records):
    paths = [text(stop, "transcript_path") for stop in records.stops.values()]
    return next((path for path in reversed(paths) if path), None)


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    args = args or ["--latest"]
    if len(args) != 1:
        fail("usage accepts exactly one Cursor session target")
    token_file = resolve_token_file(Path.home() / ".cursor" / "prompt-logs", args[0])
    with open_token_file(token_file) as handle:
        records = read_records(handle)
    if not records.generations:
        fail(f"Cursor session has no token usage records: {token_file}")
    prices = json.loads(PRICES_PATH.read_text(encoding="utf-8"))
    transcript = session_transcript(records)
    requests, totals = request_report(records, prices)
    session_id = token_file.name[len(TOKEN_FILE_PREFIX):-len(TOKENS_SUFFIX)]
    subagents = subagent_blocks(records, session_id, transcript)
    print(f"session: {transcript or token_file}")
    print()
    print("\n".join(requests))
    if subagents:
        print()
        print("\n".join(subagents))
    print()
    print(f"TOTAL ({len(records.generations)} requests, {len(subagents)} sub-agents)")
    print(f"working time: {fmt_hms(totals.working_seconds)}")
    print(est_line(totals.buckets, totals.efforts, prices))


if __name__ == "__main__":
    main()
