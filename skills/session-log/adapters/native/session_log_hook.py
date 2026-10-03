#!/usr/bin/env python3
# universal-session-log: managed
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

LIB_PATH = str(Path(__file__).resolve().parents[2] / "lib")
if LIB_PATH not in sys.path:
    sys.path.insert(0, LIB_PATH)
import pathsafe

MAX_SESSION_ID_LENGTH = 128
WORKSPACE_NAME_LIMIT = 64
WORKSPACE_DIGEST_LENGTH = 12
NONCE_BYTES = 16
SAFE_ID = re.compile(rf"^[A-Za-z0-9._-]{{1,{MAX_SESSION_ID_LENGTH}}}$")
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
TOKENS_SUFFIX = ".tokens.jsonl"


def fail(message):
    print(f"session-log: {message}", file=sys.stderr)
    raise SystemExit(1)


def new_nonce():
    return secrets.token_hex(NONCE_BYTES)


def ensure_directory(path, harness):
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            fail(f"unsafe {harness} path: {current}")
        if current.exists() and not current.is_dir():
            fail(f"non-directory {harness} path: {current}")
        if not current.exists():
            current.mkdir(mode=0o700, exist_ok=True)
    return path


def is_unsafe_path(path):
    return path.is_symlink() or (path.exists() and not path.is_file())


def safe_regular(path, harness):
    if is_unsafe_path(path):
        fail(f"unsafe {harness} file: {path}")


def is_untrusted_descriptor(info):
    return not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()


def _is_unsafe_existing_file(original):
    return original is not None and (
        stat.S_ISLNK(original.st_mode) or not stat.S_ISREG(original.st_mode)
    )


def atomic_private_json(path, value, harness):
    path = Path(path).absolute()
    ensure_directory(path.parent, harness)
    try:
        original = path.lstat()
    except FileNotFoundError:
        original = None
    if _is_unsafe_existing_file(original):
        fail(f"unsafe {harness} file: {path}")
    parent_fd = pathsafe.open_directory(str(path.parent), create=False)
    temporary = f".{path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
    descriptor = None
    temporary_stat = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary, flags, 0o600, dir_fd=parent_fd)
        temporary_stat = os.fstat(descriptor)
        handle = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = None
        with handle:
            json.dump(value, handle, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        pathsafe.replace_file_no_replace(
            pathsafe.AtomicReplacement(
                parent_fd,
                temporary,
                path.name,
                original,
                temporary_stat,
            )
        )
        temporary = None
        os.fsync(parent_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                pathsafe.unlink_if_same_file(parent_fd, temporary, temporary_stat)
            except OSError:
                pass
        os.close(parent_fd)

def append_private(path, text, harness):
    ensure_directory(path.parent, harness)
    safe_regular(path, harness)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        if is_untrusted_descriptor(os.fstat(descriptor)):
            fail(f"unsafe {harness} log file: {path}")
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, text.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def process_start(pid) -> Optional[str]:
    try:
        output = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "lstart="],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return output.rstrip("\n").lstrip(" ")
    except (OSError, subprocess.SubprocessError):
        return None


def session_id(payload) -> Optional[str]:
    for key in ("session_id", "conversation_id"):
        value = payload.get(key)
        if isinstance(value, str) and SAFE_ID.fullmatch(value):
            return value
    return None


def workspace_key(payload):
    value = payload.get("cwd")
    if not isinstance(value, str):
        roots = payload.get("workspace_roots")
        value = roots[0] if isinstance(roots, list) and roots and isinstance(roots[0], str) else "unknown"
    canonical = os.path.realpath(value)
    name = re.sub(r"[^A-Za-z0-9._-]", "-", Path(canonical).name or "root")
    digest = hashlib.sha256(canonical.encode()).hexdigest()[:WORKSPACE_DIGEST_LENGTH]
    return f"{name[:WORKSPACE_NAME_LIMIT] or 'unknown'}-{digest}"


def prompt_text(payload):
    value = payload.get("prompt")
    return ("prompt", value) if isinstance(value, str) and value else None


def response_text(payload):
    for key in ("text", "last_assistant_message", "response"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return ("response", value)
    return None


def subagent_text(payload):
    value = payload.get("summary") or payload.get("last_assistant_message")
    if isinstance(value, str) and value:
        kind = payload.get("subagent_type") or payload.get("agent_type") or "subagent"
        return ("sub-agent finished", f"{kind}: {value}")
    return None


def event_text(payload, event):
    if event == "user-prompt":
        return prompt_text(payload)
    if event in ("assistant-response", "stop"):
        return response_text(payload)
    if event == "subagent-stop":
        return subagent_text(payload)
    return None


def is_token_count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def token_record(payload) -> Optional[dict]:
    generation = payload.get("generation_id")
    if not isinstance(generation, str) or not SAFE_ID.fullmatch(generation):
        return None
    tokens = {key: payload[key] for key in TOKEN_FIELDS if is_token_count(payload.get(key))}
    return {"generation_id": generation, **tokens} if tokens else None


def respond(harness, event):
    value = {"continue": True} if harness == "cursor" and event == "user-prompt" else {}
    print(json.dumps(value, separators=(",", ":")))


@dataclass(frozen=True)
class HookRequest:
    harness: str
    event: str
    payload: object
    root: object


@dataclass(frozen=True)
class RuntimeHooks:
    now: object = time.time
    nonce_factory: object = new_nonce
    process_start_lookup: object = process_start


def process_payload(request, hooks=RuntimeHooks()):
    harness = request.harness
    event = request.event
    payload = request.payload
    root = request.root
    sid = session_id(payload)
    if sid is None:
        respond(harness, event)
        return
    package_root = Path(__file__).resolve().parents[2]
    version = (package_root / "VERSION").read_text(encoding="utf-8").strip()
    pid = os.getppid()
    loaded_at = hooks.now()
    atomic_private_json(root / "session-log" / "runtime.json", {
        "harness": harness,
        "version": version,
        "session_id": sid,
        "nonce": hooks.nonce_factory(),
        "loaded_at": int(loaded_at),
        "pid": pid,
        "process_start": hooks.process_start_lookup(pid),
    }, harness)
    log_dir = root / "prompt-logs" / workspace_key(payload)
    # Cursor reports identical per-turn totals on afterAgentResponse and stop; recording stop alone avoids double counting.
    record = token_record(payload) if harness == "cursor" and event == "stop" else None
    if record:
        append_private(log_dir / f"session_{sid}{TOKENS_SUFFIX}", json.dumps(record, separators=(",", ":")) + "\n", harness)
    content = event_text(payload, event)
    if content:
        label, text = content
        log = log_dir / f"session_{sid}.md"
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(loaded_at))
        append_private(log, f"\n### {timestamp} {label}\n\n{text}\n", harness)
    respond(harness, event)


def main(argv=None):
    supported = {"cursor": ".cursor", "codex": ".codex"}
    events = {
        "cursor": {"session-start", "user-prompt", "assistant-response", "subagent-stop", "stop"},
        "codex": {"session-start", "user-prompt", "subagent-stop", "stop"},
    }
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] not in supported:
        fail("native hook requires an explicit cursor or codex harness and lifecycle event")
    harness, event = args
    if event not in events[harness]:
        fail(f"unknown {harness} lifecycle event: {event}")
    home_value = os.environ.get("HOME", "")
    if not home_value:
        fail("HOME is not set; cannot resolve session-log directory")
    home = Path(os.path.realpath(home_value))
    root = home / supported[harness]
    state_dir = root / "session-log"
    flag = root / "prompt-logs" / ".enabled"
    runtime = state_dir / "runtime.json"
    ensure_directory(root, harness)
    safe_regular(flag, harness)
    safe_regular(runtime, harness)
    if not flag.is_file():
        if runtime.exists():
            runtime.unlink()
        respond(harness, event)
        return
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        fail(f"invalid {harness} hook payload")
    if not isinstance(payload, dict):
        fail(f"invalid {harness} hook payload")
    process_payload(HookRequest(harness, event, payload, root))


if __name__ == "__main__":
    main()
