// universal-session-log: managed
// Universal session-log OpenCode adapter.
// The command entrypoint supplies explicit harness identity; this module only
// uses OpenCode's native plugin hooks and SQLite storage APIs.
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";

const stableKey = (...parts) => createHash("sha256").update(parts.map((part) => String(part)).join("\0")).digest("hex");
const rawHome = os.homedir();
const canonicalPath = (value) => {
  const resolved = path.resolve(value);
  let current = resolved;
  const missing = [];
  while (true) {
    try {
      const real = fs.realpathSync.native(current);
      return path.join(real, ...missing);
    } catch (error) {
      if (error?.code !== "ENOENT") return resolved;
      const parent = path.dirname(current);
      if (parent === current) return resolved;
      missing.unshift(path.basename(current));
      current = parent;
    }
  }
};
const home = canonicalPath(rawHome);
const containsSymlinkComponent = (value) => {
  let current = path.resolve(value);
  while (true) {
    try {
      if (fs.lstatSync(current).isSymbolicLink()) return true;
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
    }
    const parent = path.dirname(current);
    if (parent === current) return false;
    current = parent;
  }
};
const rawDefaultConfigHome = path.join(home, ".config");
const rawDefaultDataHome = path.join(home, ".local", "share");
const rawConfigHome = process.env.XDG_CONFIG_HOME || rawDefaultConfigHome;
const rawDataHome = process.env.XDG_DATA_HOME || rawDefaultDataHome;
if (
  containsSymlinkComponent(rawConfigHome) ||
  containsSymlinkComponent(path.join(rawConfigHome, "opencode")) ||
  containsSymlinkComponent(rawDataHome) ||
  containsSymlinkComponent(path.join(rawDataHome, "opencode"))
) {
  throw new Error("OpenCode root contains an unsafe symlink");
}
const defaultConfigHome = canonicalPath(path.join(home, ".config"));
const defaultDataHome = canonicalPath(path.join(home, ".local", "share"));
const configHome = canonicalPath(process.env.XDG_CONFIG_HOME || defaultConfigHome);
const dataHome = canonicalPath(process.env.XDG_DATA_HOME || defaultDataHome);
if (configHome !== defaultConfigHome || dataHome !== defaultDataHome) {
  throw new Error("OpenCode root is relocated; universal session-log does not support custom roots");
}
const CONFIG_DIR = path.join(configHome, "opencode");
const assertNoFollowContained = (root, candidate) => {
  const base = path.resolve(root);
  const target = path.resolve(candidate);
  if (!pathInside(base, target)) throw new Error("OpenCode database escapes the configured data root");
  const parsed = path.parse(target);
  let current = parsed.root;
  const components = target.slice(parsed.root.length).split(path.sep).filter(Boolean);
  for (let index = 0; index < components.length; index += 1) {
    current = path.join(current, components[index]);
    const stat = fs.lstatSync(current);
    if (isUnsafeDatabaseComponent(stat, index === components.length - 1)) {
      throw new Error(`OpenCode database path is not safe: ${current}`);
    }
  }
  const stat = fs.lstatSync(target);
  if (!stat.isFile()) throw new Error(`OpenCode database path is not a file: ${target}`);
};
const DATA_DIR = path.join(dataHome, "opencode");
const discoverDatabasePath = () => {
  try {
    const candidates = fs.readdirSync(DATA_DIR, { withFileTypes: true })
      .filter((entry) => entry.isFile() && /^opencode-.+\.db$/.test(entry.name))
      .map((entry) => {
        const candidate = path.join(DATA_DIR, entry.name);
        return { candidate, mtime: fs.statSync(candidate).mtimeMs };
      })
      .sort((left, right) => right.mtime - left.mtime);
    return candidates[0]?.candidate;
  } catch {
    return undefined;
  }
};
const resolveDatabasePath = () => {
  const configured = process.env.OPENCODE_DB;
  if (configured) {
    if (configured === ":memory:") throw new Error("OpenCode in-memory databases are unsupported");
    return path.isAbsolute(configured) ? configured : path.join(DATA_DIR, configured);
  }
  const channel = process.env.OPENCODE_CHANNEL;
  if (channel) {
    const channelDatabase = ["latest", "beta", "prod"].includes(channel) ||
      process.env.OPENCODE_DISABLE_CHANNEL_DB === "1" ||
      process.env.OPENCODE_DISABLE_CHANNEL_DB === "true"
      ? "opencode.db"
      : `opencode-${channel.replace(/[^a-z A-Z 0-9._-]/g, "-")}.db`;
    return path.join(DATA_DIR, channelDatabase);
  }
  const defaultDatabase = path.join(DATA_DIR, "opencode.db");
  return fs.existsSync(defaultDatabase)
    ? defaultDatabase
    : discoverDatabasePath() || path.join(DATA_DIR, "opencode-local.db");
};

const LOGS_DIR = path.join(CONFIG_DIR, "prompt-logs");
const ENABLED_FLAG = path.join(LOGS_DIR, ".enabled");
const STATE_DIR = path.join(CONFIG_DIR, "session-log");
const PACKAGE_ROOT = path.resolve(import.meta.dir, "../..");
const VERSION = fs.readFileSync(path.join(PACKAGE_ROOT, "VERSION"), "utf8").trim();

const enabled = () => {
  try { return fs.lstatSync(ENABLED_FLAG).isFile(); } catch { return false; }
};
const enabledSignature = () => {
  try {
    const stat = fs.lstatSync(ENABLED_FLAG);
    if (!stat.isFile()) return "";
    const token = stat.size < 4096 ? fs.readFileSync(ENABLED_FLAG, "utf8") : "";
    return token || `${stat.dev}:${stat.ino}`;
  } catch {
    return "";
  }
};
const validId = (value) => typeof value === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(value);
const pathInside = (root, candidate) => {
  const base = path.resolve(root);
  const resolved = path.resolve(candidate);
  return resolved === base || resolved.startsWith(`${base}${path.sep}`);
};
const unsafePathError = (message) => Object.assign(new Error(message), { code: "SESSION_LOG_UNSAFE_PATH" });
const ensureDirectory = (directory) => {
  const resolved = path.resolve(directory);
  const parsed = path.parse(resolved);
  let current = parsed.root;
  for (const component of resolved.slice(parsed.root.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, component);
    let stat;
    try { stat = fs.lstatSync(current); } catch (error) {
      if (error?.code !== "ENOENT") throw error;
      fs.mkdirSync(current, { mode: 0o700 });
      stat = fs.lstatSync(current);
    }
    if (isUnsafeDirectory(stat)) throw unsafePathError(`OpenCode path is not a safe directory: ${current}`);
    const privateDirectory = pathInside(LOGS_DIR, current) || pathInside(STATE_DIR, current);
    const uid = typeof process.getuid === "function" ? process.getuid() : null;
    if (privateDirectory && uid !== null && stat.uid !== uid) {
      throw unsafePathError(`OpenCode private directory is not owned by this process: ${current}`);
    }
    if (privateDirectory && (stat.mode & 0o077) !== 0) fs.chmodSync(current, 0o700);
  }
};
const ensureFile = (file, allowMissing = true) => {
  ensureDirectory(path.dirname(file));
  try {
    const stat = fs.lstatSync(file);
    if (stat.isSymbolicLink() || !stat.isFile()) throw unsafePathError(`OpenCode path is not a safe file: ${file}`);
  } catch (error) {
    if (error?.code !== "ENOENT" || !allowMissing) throw error;
  }
};
const assertLogFile = (file, root) => {
  ensureFile(file, false);
  const stat = fs.lstatSync(file);
  const uid = typeof process.getuid === "function" ? process.getuid() : null;
  if (isNotPrivateOrOwned(stat, uid)) {
    throw unsafePathError(`OpenCode log file is not private or owned by this process: ${file}`);
  }
  let descriptor;
  try {
    descriptor = fs.openSync(file, "r");
    const buffer = Buffer.alloc(4096);
    const bytes = fs.readSync(descriptor, buffer, 0, buffer.length, 0);
    const header = buffer.toString("utf8", 0, bytes);
    if (!header.split("\n").slice(0, 8).includes(`**Session ID:** ${root}`)) {
      throw unsafePathError(`OpenCode log file has an unexpected session header: ${file}`);
    }
  } finally {
    if (descriptor !== undefined) fs.closeSync(descriptor);
  }
};
const MAX_DELETED_SESSIONS = 4096;
const MAX_SNAPSHOTS_PER_SESSION = 512;
const MAX_PROMPT_IDS_PER_SESSION = 512;
// Shared directory walk (create-on-missing, symlink-refusing) reused by the
// append and ensure-file Python helpers below. A cross-file module cannot be
// shared because each adapter installs as one standalone plugin file, so the
// duplication is factored here, within this file, as an interpolated snippet.
const OPEN_DIRECTORY_PY = String.raw`def open_directory(path):
    fd = os.open(os.sep, flags)
    try:
        for part in path.split(os.sep)[1:]:
            if not part or part == ".":
                continue
            if part == "..":
                raise RuntimeError("parent traversal")
            try:
                next_fd = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise`;
const isUnsafeDatabaseComponent = (stat, isFinalComponent) =>
  stat.isSymbolicLink() || (!isFinalComponent && !stat.isDirectory());
const isUnsafeDirectory = (stat) => stat.isSymbolicLink() || !stat.isDirectory();
const isNotPrivateOrOwned = (stat, uid) =>
  (stat.mode & 0o077) !== 0 || (uid !== null && stat.uid !== uid);
const isVisibleTextPart = (part) =>
  part?.type === "text" && !part.synthetic && !part.ignored && Boolean(part.text);
const hasExplicitPromptId = (promptID) =>
  (typeof promptID === "string" && promptID.length > 0) || typeof promptID === "number";
const differs = (previous, next) => Boolean(previous && next && previous !== next);
const isLoggableAssistantMessage = (message) =>
  Boolean(message) && message.role === "assistant" &&
  Number.isFinite(Number(message.time?.completed)) && validId(message.id);
const elapsedSeconds = (start, end) => {
  const started = Number(start);
  const finished = Number(end);
  if (!Number.isFinite(started) || !Number.isFinite(finished)) return 0;
  return Math.max(0, (finished - started) / 1000);
};
const snapshotFingerprint = (message) => {
  try { return JSON.stringify(message); } catch { return ""; }
};
const trimOldest = (collection, max, onEvict) => {
  while (collection.size > max) {
    const key = collection.keys().next().value;
    const value = collection instanceof Map ? collection.get(key) : key;
    collection.delete(key);
    onEvict?.(value);
  }
};
const MAX_RETRY_ATTEMPTS = 5;
const RETRY_DELAY_MS = 250;
const clearRetryTimer = (record) => {
  if (!record?.retryTimer) return;
  clearTimeout(record.retryTimer);
  record.retryTimer = undefined;
};
const clearRetryTimers = (records) => {
  if (records) for (const record of records) clearRetryTimer(record);
};
const scheduleRetry = (record, operation) => {
  if (!record || record.retryTimer) return;
  if ((record.retryAttempts || 0) >= MAX_RETRY_ATTEMPTS) {
    record.retryable = false;
    record.pending = false;
    record.exhausted = true;
    return;
  }
  record.retryAttempts = (record.retryAttempts || 0) + 1;
  record.retryable = true;
  const delay = RETRY_DELAY_MS * (2 ** (record.retryAttempts - 1));
  const timer = setTimeout(async () => {
    if (record.retryTimer !== timer) return;
    record.retryTimer = undefined;
    try {
      await operation();
    } catch (error) {
      record.retryError = error;
      scheduleRetry(record, operation);
    }
  }, delay);
  timer.unref?.();
  record.retryTimer = timer;
};
const descendantsOf = (sessionID, childToParent) => {
  const childrenByParent = new Map();
  for (const [child, parent] of childToParent) {
    const children = childrenByParent.get(parent) || [];
    children.push(child);
    childrenByParent.set(parent, children);
  }
  const descendants = new Set([sessionID]);
  const pending = [sessionID];
  for (let index = 0; index < pending.length; index += 1) {
    for (const child of childrenByParent.get(pending[index]) || []) {
      if (descendants.has(child)) continue;
      descendants.add(child);
      pending.push(child);
    }
  }
  return descendants;
};
const isUnsafePathError = (error) => error?.code === "SESSION_LOG_UNSAFE_PATH";
const hasPendingPrompts = (pendingPromptsBySession, sessionID) =>
  Boolean(pendingPromptsBySession.get(sessionID)?.length);
const isDifferentSignature = (record, currentSignature) => record.enableSignature !== currentSignature;
const finishSnapshot = (snapshot) => {
  clearRetryTimer(snapshot);
  snapshot.logged = true;
  snapshot.pending = false;
  snapshot.retryable = false;
};
const prepareAssistantSnapshot = (snapshots, message, sessionID, root, signature) => {
  const fingerprint = snapshotFingerprint(message);
  const dedupeKey = stableKey("assistant", sessionID, message.id, fingerprint);
  const previous = snapshots.get(message.id);
  let snapshot = previous?.terminal ? previous : null;
  const unchanged = snapshot?.fingerprint === fingerprint;
  if (snapshot && isDifferentSignature(snapshot, signature)) {
    clearRetryTimer(snapshot);
    if (unchanged) return null;
    snapshots.delete(message.id);
    snapshot = null;
  }
  if (snapshot?.logged || (snapshot?.exhausted && unchanged)) return null;
  if (snapshot && !unchanged) {
    snapshot.retryAttempts = 0;
    snapshot.retryable = false;
    snapshot.pending = true;
    snapshot.exhausted = false;
    snapshot.retryError = undefined;
  }
  if (snapshot) {
    snapshot.fingerprint = fingerprint;
    snapshot.dedupeKey = dedupeKey;
    clearRetryTimer(snapshot);
    if ((snapshot.pending && !snapshot.retryable && unchanged) || (sessionID !== root && !snapshot.pending)) return null;
    return snapshot;
  }
  clearRetryTimer(previous);
  snapshot = {
    terminal: message.finish !== "tool-calls",
    fingerprint,
    enableSignature: signature,
    pending: true,
    retryable: false,
    retryAttempts: 0,
    logged: false,
    dedupeKey,
  };
  snapshots.set(message.id, snapshot);
  trimOldest(snapshots, MAX_SNAPSHOTS_PER_SESSION, clearRetryTimer);
  return snapshot;
};
const promptIdentifier = (input, output) =>
  input.messageID ?? input.messageId ?? input.id ?? input.requestId ??
  output?.messageID ?? output?.messageId ?? output?.id ?? output?.requestId;
// The second rename can fail after runtime.json changes; restore its previous
// bytes before exposing the failure. Per-process state is replaced last.
export function commitPairedRuntime({ temporary, processTemporary, runtime, processRuntime, backup }) {
  let restorable = false;
  try {
    fs.renameSync(runtime, backup);
    restorable = true;
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
  try {
    fs.renameSync(temporary, runtime);
    fs.renameSync(processTemporary, processRuntime);
  } catch (error) {
    try {
      try {
        if (!fs.lstatSync(runtime).isSymbolicLink()) fs.unlinkSync(runtime);
      } catch (unlinkError) {
        if (unlinkError?.code !== "ENOENT") throw unlinkError;
      }
      if (restorable) fs.renameSync(backup, runtime);
    } catch (rollbackError) {
      throw new AggregateError([error, rollbackError], "OpenCode runtime rollback failed");
    }
    throw error;
  }
  if (restorable) fs.unlinkSync(backup);
}
const promptBlock = (text) => `## ${clock()}\n\n${text}\n\n---\n\n`;
const subagentLine = (sessionID, message, elapsed) =>
  `${clock()} sub-agent finished: ${message.mode || "subagent"} (${sessionID}), ` +
  `working time: ${fmtHms(elapsed)}, ${usageLine(message)}\n\n`;
const responseBlock = (body, elapsed, message, switched) =>
  `### ${clock()} response\n\n${body ? `${body}\n\n` : ""}` +
  `working time: ${fmtHms(elapsed)}\n${usageLine(message)}\n${switched}---\n\n`;
const SECURE_APPEND_SCRIPT = String.raw`
import fcntl
import hashlib
import json
import os
import stat
import subprocess
import time
file = os.environ["SESSION_LOG_FILE"]
payload = __import__("sys").stdin.buffer.read()
dedupe = os.environ["SESSION_LOG_DEDUPE"] == "1"
dedupe_key = os.environ.get("SESSION_LOG_DEDUPE_KEY", "")
enable_lock_path = os.environ.get("SESSION_LOG_ENABLE_LOCK", "")
enable_flag_path = os.environ.get("SESSION_LOG_ENABLE_FLAG", "")
expected_enable_signature = os.environ.get("SESSION_LOG_ENABLE_SIGNATURE", "")
flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
parent, name = os.path.split(file)
dedupe_name = "." + name + ".dedupe"
expected_dev = int(os.environ["SESSION_LOG_EXPECTED_DEV"])
expected_ino = int(os.environ["SESSION_LOG_EXPECTED_INO"])
lock_fd = None
descriptor = None
enable_flag_fd = None
enable_lock_fd = None
token = "%s-%s" % (os.getpid(), time.monotonic_ns())
deadline = time.monotonic() + 5

${OPEN_DIRECTORY_PY}
def read_dedupe_records(parent_fd):
    try:
        descriptor = os.open(dedupe_name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd)
    except FileNotFoundError:
        return []
    try:
        current = os.fstat(descriptor)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
            or current.st_uid != os.getuid()
            or (current.st_mode & 0o077)
            or current.st_size > 65536
        ):
            raise RuntimeError("unsafe OpenCode dedupe state")
        raw = os.read(descriptor, 65536)
    finally:
        os.close(descriptor)
    try:
        records = []
        for line in raw.decode("utf-8").splitlines():
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                record = {"key": line, "legacy": True, "committed": True}
            if not isinstance(record, dict) or not isinstance(record.get("key"), str) or not record["key"]:
                raise RuntimeError("invalid OpenCode dedupe state")
            records.append(record)
        return records
    except UnicodeDecodeError:
        raise RuntimeError("invalid OpenCode dedupe state")
MAX_DEDUPE_RECORDS = 64
def retain_records(records):
    pending = [record for record in records if not record.get("committed")]
    committed = [record for record in records if record.get("committed")]
    committed_limit = max(0, MAX_DEDUPE_RECORDS - len(pending))
    return pending + (committed[-committed_limit:] if committed_limit else [])
def write_dedupe_records(parent_fd, records):
    if not dedupe_key:
        return
    data = b"".join(
        (json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8")
        for record in retain_records(records)
    )
    temporary_name = f"{dedupe_name}.tmp.{os.getpid()}.{time.monotonic_ns()}"
    temporary_fd = None
    try:
        temporary_fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_fd,
        )
        written = 0
        while written < len(data):
            written += os.write(temporary_fd, data[written:])
        os.fsync(temporary_fd)
        os.close(temporary_fd)
        temporary_fd = None
        os.replace(temporary_name, dedupe_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        if temporary_fd is not None:
            os.close(temporary_fd)
        try:
            os.unlink(temporary_name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass

def read_owner(fd):
    owner = os.open("owner", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=fd)
    try:
        return json.loads(os.read(owner, 512).decode("utf-8"))
    finally:
        os.close(owner)

def process_start(pid):
    try:
        value = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "lstart="],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return value or None
    except (OSError, subprocess.SubprocessError):
        return None

def owner_alive(fd):
    try:
        owner = read_owner(fd)
        pid = int(owner.get("pid", 0))
        if pid <= 0:
            return None
        expected_start = owner.get("start")
    except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
        return None
    if isinstance(expected_start, str) and expected_start:
        actual_start = process_start(pid)
        if actual_start is None:
            return None
        return actual_start == expected_start
    try:
        os.kill(pid, 0)
    except OSError as error:
        return getattr(error, "errno", None) != 3
    return True
if enable_lock_path:
    enable_lock_fd = os.open(
        enable_lock_path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    enable_lock_stat = os.fstat(enable_lock_fd)
    if (
        not stat.S_ISREG(enable_lock_stat.st_mode)
        or enable_lock_stat.st_nlink != 1
        or enable_lock_stat.st_uid != os.getuid()
    ):
        raise RuntimeError("unsafe OpenCode enable lock")
    os.fchmod(enable_lock_fd, 0o600)
    fcntl.flock(enable_lock_fd, fcntl.LOCK_EX)


parent_fd = open_directory(parent)
lock_name = name + ".lock"
try:
    if enable_flag_path:
        try:
            enable_flag_stat = os.lstat(enable_flag_path)
        except FileNotFoundError:
            raise SystemExit(0)
        if (
            stat.S_ISLNK(enable_flag_stat.st_mode)
            or not stat.S_ISREG(enable_flag_stat.st_mode)
            or enable_flag_stat.st_uid != os.getuid()
        ):
            raise RuntimeError("unsafe OpenCode enable flag")
        try:
            enable_flag_fd = os.open(
                enable_flag_path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
        except FileNotFoundError:
            raise SystemExit(0)
        current_flag_stat = os.fstat(enable_flag_fd)
        if (
            current_flag_stat.st_dev != enable_flag_stat.st_dev
            or current_flag_stat.st_ino != enable_flag_stat.st_ino
            or current_flag_stat.st_nlink != 1
            or current_flag_stat.st_uid != os.getuid()
        ):
            raise SystemExit(0)
        flag_bytes = os.read(enable_flag_fd, 4096)
        if len(flag_bytes) >= 4096:
            current_enable_signature = f"{current_flag_stat.st_dev}:{current_flag_stat.st_ino}"
        else:
            try:
                flag_token = flag_bytes.decode("utf-8")
            except UnicodeDecodeError:
                raise SystemExit(0)
            current_enable_signature = flag_token or f"{current_flag_stat.st_dev}:{current_flag_stat.st_ino}"
        if expected_enable_signature and current_enable_signature != expected_enable_signature:
            raise SystemExit(0)
    while True:
        try:
            os.mkdir(lock_name, 0o700, dir_fd=parent_fd)
            lock_fd = os.open(lock_name, flags, dir_fd=parent_fd)
            owner = os.open("owner", os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=lock_fd)
            try:
                owner_data = {"pid": os.getpid(), "token": token}
                owner_start = process_start(os.getpid())
                if owner_start:
                    owner_data["start"] = owner_start
                os.write(owner, json.dumps(owner_data).encode("utf-8"))
                os.fsync(owner)
            finally:
                os.close(owner)
            break
        except FileExistsError:
            if lock_fd is not None:
                os.close(lock_fd)
                lock_fd = None
            lock_fd = os.open(lock_name, flags, dir_fd=parent_fd)
            state = owner_alive(lock_fd)
            stale_invalid = state is not True and time.time() - os.fstat(lock_fd).st_mtime >= 5
            if state is True or not stale_invalid:
                os.close(lock_fd)
                lock_fd = None
                if time.monotonic() >= deadline:
                    raise TimeoutError("OpenCode append lock timed out")
                time.sleep(0.01)
                continue
            try:
                os.unlink("owner", dir_fd=lock_fd)
            except FileNotFoundError:
                pass
            os.close(lock_fd)
            lock_fd = None
            os.rmdir(lock_name, dir_fd=parent_fd)
            if time.monotonic() >= deadline:
                raise TimeoutError("OpenCode append lock timed out")
    dedupe_records = read_dedupe_records(parent_fd)
    path_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if (
        stat.S_ISLNK(path_stat.st_mode)
        or not stat.S_ISREG(path_stat.st_mode)
        or path_stat.st_dev != expected_dev
        or path_stat.st_ino != expected_ino
    ):
        raise RuntimeError("OpenCode log file changed during append")
    descriptor = os.open(name, os.O_RDWR | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd)
    current = os.fstat(descriptor)
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_nlink != 1
        or current.st_uid != os.getuid()
        or (current.st_mode & 0o077)
        or current.st_dev != expected_dev
        or current.st_ino != expected_ino
    ):
        raise RuntimeError("unsafe OpenCode log file")
    before = current.st_size
    payload_digest = hashlib.sha256(payload).hexdigest()
    existing_record = next((record for record in dedupe_records if record.get("key") == dedupe_key), None)
    remaining_records = [record for record in dedupe_records if record.get("key") != dedupe_key]
    if existing_record and not existing_record.get("legacy"):
        try:
            offset = int(existing_record["offset"])
            length = int(existing_record["length"])
            recorded_digest = str(existing_record["sha256"])
        except (KeyError, TypeError, ValueError):
            raise RuntimeError("invalid OpenCode dedupe record")
        if offset < 0 or length < 0:
            raise RuntimeError("invalid OpenCode dedupe record")
        if offset < before < offset + length:
            for record in dedupe_records:
                if record is existing_record or record.get("legacy"):
                    continue
                try:
                    record_offset = int(record["offset"])
                except (KeyError, TypeError, ValueError):
                    raise RuntimeError("invalid OpenCode dedupe record")
                if record_offset >= offset:
                    raise RuntimeError("OpenCode dedupe record has later data")
            os.ftruncate(descriptor, offset)
            os.fsync(descriptor)
            before = offset
        if before >= offset + length:
            os.lseek(descriptor, offset, os.SEEK_SET)
            existing_payload = os.read(descriptor, length)
            os.lseek(descriptor, 0, os.SEEK_END)
            if len(existing_payload) == length and hashlib.sha256(existing_payload).hexdigest() == recorded_digest:
                existing_record["committed"] = True
                write_dedupe_records(parent_fd, remaining_records + [existing_record])
                print("1")
                raise SystemExit(0)
        if before != offset:
            raise RuntimeError("OpenCode dedupe record does not match log")
    elif existing_record and existing_record.get("legacy"):
        print("1")
        raise SystemExit(0)
    if dedupe_key:
        pending_record = {
            "key": dedupe_key,
            "offset": before,
            "length": len(payload),
            "sha256": payload_digest,
            "committed": False,
        }
        write_dedupe_records(parent_fd, remaining_records + [pending_record])
    try:
        written = 0
        while written < len(payload):
            written += os.write(descriptor, payload[written:])
        os.fsync(descriptor)
    except BaseException:
        try:
            os.ftruncate(descriptor, before)
        except OSError:
            pass
        raise
    if dedupe_key:
        pending_record["committed"] = True
        write_dedupe_records(parent_fd, remaining_records + [pending_record])
finally:
    if descriptor is not None:
        os.close(descriptor)
    if lock_fd is not None:
        try:
            owner = read_owner(lock_fd)
            if owner.get("token") == token:
                os.unlink("owner", dir_fd=lock_fd)
                os.close(lock_fd)
                lock_fd = None
                os.rmdir(lock_name, dir_fd=parent_fd)
        except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
            pass
        if lock_fd is not None:
            os.close(lock_fd)
    if enable_flag_fd is not None:
        os.close(enable_flag_fd)
    os.close(parent_fd)
    if enable_lock_fd is not None:
        fcntl.flock(enable_lock_fd, fcntl.LOCK_UN)
        os.close(enable_lock_fd)
print("1")
`;
const secureAppend = (file, payload, options = {}) => {
  const { dedupe = false, enableSignature = "", dedupeKey = "" } = options;
  const expected = fs.lstatSync(file);
  const result = execFileSync("python3", ["-c", SECURE_APPEND_SCRIPT], {
    env: {
      ...process.env,
      SESSION_LOG_FILE: file,
      SESSION_LOG_EXPECTED_DEV: String(expected.dev),
      SESSION_LOG_EXPECTED_INO: String(expected.ino),
      SESSION_LOG_ENABLE_LOCK: path.join(LOGS_DIR, ".enabled.lock"),
      SESSION_LOG_ENABLE_FLAG: ENABLED_FLAG,
      SESSION_LOG_ENABLE_SIGNATURE: enableSignature,
      SESSION_LOG_DEDUPE: dedupe ? "1" : "0",
      SESSION_LOG_DEDUPE_KEY: dedupeKey,
    },
    input: Buffer.from(payload),
    encoding: "utf8",
    stdio: ["pipe", "pipe", "pipe"],
  });
  return result.trim() === "1";
};
const SECURE_SNAPSHOT_SCRIPT = String.raw`
import os
import sqlite3
import stat
import tempfile

source = os.environ["SESSION_LOG_SOURCE"]
directory, name = os.path.split(source)
flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
parent_fd = os.open(os.sep, flags)
descriptor = None
source_db = None
destination_db = None
temporary = None
temporary_fd = None
source_link = None
wal_link = None
shm_link = None
try:
    for part in directory.split(os.sep)[1:]:
        if not part or part == ".":
            continue
        if part == "..":
            raise RuntimeError("parent traversal")
        next_fd = os.open(part, flags, dir_fd=parent_fd)
        os.close(parent_fd)
        parent_fd = next_fd
    descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd)
    current = os.fstat(descriptor)
    if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1 or current.st_uid != os.getuid():
        raise RuntimeError("unsafe OpenCode database")
    path_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if (path_stat.st_dev, path_stat.st_ino) != (current.st_dev, current.st_ino):
        raise RuntimeError("OpenCode database changed during snapshot")
    for attempt in range(100):
        candidate = f".session-log-source.{os.getpid()}.{attempt}"
        try:
            os.link(name, candidate, src_dir_fd=parent_fd, dst_dir_fd=parent_fd, follow_symlinks=False)
            source_link = candidate
            break
        except FileExistsError:
            continue
    if source_link is None:
        raise RuntimeError("cannot stage OpenCode database")
    for suffix, attribute in (("-wal", "wal_link"), ("-shm", "shm_link")):
        try:
            sidecar_stat = os.stat(name + suffix, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(sidecar_stat.st_mode) or sidecar_stat.st_uid != os.getuid():
            raise RuntimeError("unsafe OpenCode database sidecar")
        candidate = f"{source_link}{suffix}"
        try:
            os.link(name + suffix, candidate, src_dir_fd=parent_fd, dst_dir_fd=parent_fd, follow_symlinks=False)
        except FileExistsError:
            raise RuntimeError("cannot stage OpenCode database sidecar")
        if attribute == "wal_link":
            wal_link = candidate
        else:
            shm_link = candidate
    temporary_fd, temporary = tempfile.mkstemp(prefix="session-log-opencode-db-")
    os.close(temporary_fd)
    temporary_fd = None
    source_db = sqlite3.connect(os.path.join(directory, source_link), uri=False)
    destination_db = sqlite3.connect(temporary, uri=False)
    source_db.backup(destination_db)
    destination_db.commit()
    destination_db.close()
    destination_db = None
    source_db.close()
    source_db = None
    path_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    after = os.fstat(descriptor)
    if (
        (path_stat.st_dev, path_stat.st_ino, path_stat.st_size, path_stat.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise RuntimeError("OpenCode database changed during snapshot")
    os.chmod(temporary, 0o600)
    with open(temporary, "rb") as handle:
        os.fsync(handle.fileno())
    print(temporary)
except BaseException:
    if source_db is not None:
        source_db.close()
    if destination_db is not None:
        destination_db.close()
    if temporary is not None:
        try:
            os.unlink(temporary)
        except OSError:
            pass
    raise
finally:
    if temporary_fd is not None:
        os.close(temporary_fd)
    if descriptor is not None:
        os.close(descriptor)
    for staged in (shm_link, wal_link, source_link):
        if staged is not None:
            try:
                os.unlink(staged, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
    os.close(parent_fd)
`;
const secureDatabaseSnapshot = (file) => execFileSync("python3", ["-c", SECURE_SNAPSHOT_SCRIPT], {
  env: { ...process.env, SESSION_LOG_SOURCE: file },
  encoding: "utf8",
  stdio: ["ignore", "pipe", "pipe"],
}).trim();
const SECURE_FILE_SCRIPT = String.raw`
import base64
import os
import stat

source = os.environ["SESSION_LOG_FILE"]
content = base64.b64decode(os.environ["SESSION_LOG_CONTENT"])
directory, name = os.path.split(source)
flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
${OPEN_DIRECTORY_PY}
descriptor = None
created = False
parent_fd = open_directory(directory)
try:
    try:
        descriptor = os.open(name, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd)
    except FileNotFoundError:
        descriptor = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=parent_fd)
        created = True
    current = os.fstat(descriptor)
    if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1 or current.st_uid != os.getuid() or (current.st_mode & 0o077):
        raise RuntimeError("unsafe OpenCode log file")
    if created:
        offset = 0
        while offset < len(content):
            offset += os.write(descriptor, content[offset:])
        os.fsync(descriptor)
finally:
    if descriptor is not None:
        os.close(descriptor)
    os.close(parent_fd)
`;
const secureEnsureFile = (file, content) => {
  execFileSync("python3", ["-c", SECURE_FILE_SCRIPT], {
    env: {
      ...process.env,
      SESSION_LOG_FILE: file,
      SESSION_LOG_CONTENT: Buffer.from(content).toString("base64"),
    },
    stdio: "pipe",
  });
};
const pad = (value) => String(value).padStart(2, "0");
const clock = () => {
  const now = new Date();
  return `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
};
const fmtHms = (seconds) => {
  const value = Math.max(0, Math.floor(seconds));
  return `${pad(Math.floor(value / 3600))}:${pad(Math.floor((value % 3600) / 60))}:${pad(value % 60)}`;
};
const fileStamp = () => {
  const now = new Date();
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}_${pad(now.getHours())}-${pad(now.getMinutes())}-${pad(now.getSeconds())}`;
};

function processStart(pid) {
  try {
    return execFileSync("ps", ["-p", String(pid), "-o", "lstart="], { encoding: "utf8" }).trim();
  } catch {
    return "";
  }
}
const appendFileLocked = (file, root, text, options = {}) => {
  const { dedupeSuffix = false, enableSignature = "", dedupeKey = "" } = options;
  assertLogFile(file, root);
  const payload = text.endsWith("\n") ? text : `${text}\n`;
  return secureAppend(file, payload, { dedupe: Boolean(dedupeKey) || dedupeSuffix, enableSignature, dedupeKey });
};
function markLoaded() {
  ensureDirectory(STATE_DIR);
  const nonce = `${process.pid}.${Date.now()}`;
  const temporary = path.join(STATE_DIR, `.runtime.${nonce}.tmp`);
  const processTemporary = path.join(STATE_DIR, `.runtime.${nonce}.process.tmp`);
  const runtime = path.join(STATE_DIR, "runtime.json");
  const processRuntime = path.join(STATE_DIR, `runtime.${process.pid}.json`);
  const backup = path.join(STATE_DIR, `.runtime.${nonce}.backup`);
  ensureFile(temporary);
  ensureFile(processTemporary);
  ensureFile(runtime);
  ensureFile(processRuntime);
  const payload = {
    harness: "opencode",
    version: VERSION,
    pid: process.pid,
    process_start: processStart(process.pid),
    nonce,
  };
  try {
    const encoded = `${JSON.stringify(payload)}\n`;
    fs.writeFileSync(temporary, encoded, { flag: "wx", mode: 0o600 });
    fs.writeFileSync(processTemporary, encoded, { flag: "wx", mode: 0o600 });
    ensureFile(temporary, false);
    ensureFile(processTemporary, false);
    ensureFile(runtime);
    ensureFile(processRuntime);
    commitPairedRuntime({ temporary, processTemporary, runtime, processRuntime, backup });
  } catch (error) {
    // Preserve a failed rollback's backup so the previous shared state is recoverable.
    for (const file of [temporary, processTemporary]) {
      try {
        if (!fs.lstatSync(file).isSymbolicLink()) fs.unlinkSync(file);
      } catch {}
    }
    throw error;
  }
}
const parseJsonOrNull = (value) => {
  try { return JSON.parse(value); } catch { return null; }
};
const visibleText = (parts) => parts.filter(isVisibleTextPart).map((part) => part.text).join("\n");
const tableExists = (database, name) => Boolean(database
  .query("SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?").get(name));
const responseFromDatabase = (database, messageID) => {
  if (tableExists(database, "part")) {
    const rows = database.query("SELECT data FROM part WHERE message_id = ? ORDER BY time_created").all(messageID);
    if (rows.length) return { ok: true, text: visibleText(rows.map((row) => parseJsonOrNull(row.data))) };
  }
  if (tableExists(database, "session_message")) {
    const row = database.query("SELECT data FROM session_message WHERE id = ? LIMIT 1").get(messageID);
    if (row) {
      const data = parseJsonOrNull(row.data);
      const parts = Array.isArray(data?.content) ? data.content : Array.isArray(data?.parts) ? data.parts : [];
      return { ok: true, text: visibleText(parts) };
    }
  }
  if (tableExists(database, "message") &&
      database.query("SELECT id FROM message WHERE id = ? LIMIT 1").get(messageID)) {
    return { ok: true, text: "" };
  }
  return { ok: false, text: "" };
};
async function responseText(messageID) {
  let snapshot = "";
  try {
    const databasePath = resolveDatabasePath();
    assertNoFollowContained(DATA_DIR, databasePath);
    snapshot = secureDatabaseSnapshot(databasePath);
    const { Database } = await import("bun:sqlite");
    const database = new Database(snapshot, { readonly: true });
    try {
      return responseFromDatabase(database, messageID);
    } finally {
      database.close();
    }
  } catch (error) {
    if (enabled()) process.stderr.write(`session-log: OpenCode response text unavailable: ${error?.message || error}\n`);
    return { ok: false, text: "" };
  } finally {
    if (snapshot) {
      try { fs.unlinkSync(snapshot); } catch {}
    }
  }
}

const numeric = (value, fallback = 0) => {
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? number : fallback;
};
const nonnegative = (value, fallback = 0) => Math.max(0, numeric(value, fallback));
const usageLine = (message) => {
  const tokens = message.tokens || {};
  const cache = tokens.cache || {};
  const input = nonnegative(tokens.input);
  const output = nonnegative(tokens.output);
  const reasoning = nonnegative(tokens.reasoning);
  const cacheWrite = nonnegative(cache.write);
  const cacheRead = nonnegative(cache.read);
  const computedTotal = input + output + reasoning + cacheRead + cacheWrite;
  const total = tokens.total === undefined || tokens.total === null
    ? computedTotal
    : nonnegative(tokens.total, computedTotal);
  const cost = nonnegative(message.cost);
  return `est. used token: input: ${input}, output: ${output}, reasoning: ${reasoning}, ` +
    `cache_write: ${cacheWrite}, cache_read: ${cacheRead}, total: ${total}, ` +
    `cost: $${cost.toFixed(4)}, model: ${message.modelID || "-"}`;
};
const textParts = (parts) => (Array.isArray(parts) ? parts : [])
  .map((part) => (typeof part?.text === "string" ? part.text : ""))
  .filter(Boolean)
  .join("\n");

export const SessionLogPlugin = async ({ directory }) => {
  markLoaded();
  const logFileByRoot = new Map();
  const appendLocks = new Map();
  const lastByRoot = new Map();
  const childToParent = new Map();
  const knownSessions = new Set();
  // A deleted session is a tombstone for this plugin lifetime: late native events
  // must not recreate its root or log file.
  const deletedSessions = new Set();
  // Per-session snapshots retain bounded terminal-message and retry state.
  const seenAssistantBySession = new Map();
  const pendingMessagesBySession = new Map();
  const pendingPromptsBySession = new Map();
  const rootRetryMessagesBySession = new Map();
  const seenPromptIdsBySession = new Map();
  const seenPromptOutputs = new WeakSet();
  const promptDrainsBySession = new Map();
  let sessionEventQueue = Promise.resolve();
  const enqueueSession = (_sessionID, operation) => {
    const current = sessionEventQueue.then(operation, operation);
    const settled = current.finally(() => {
      if (sessionEventQueue === settled) sessionEventQueue = Promise.resolve();
    });
    sessionEventQueue = settled;
    return settled;
  };
  const projectSlug = String(directory || process.cwd()).split(path.sep).filter(Boolean).slice(-2).join("-") || "root";

  const rootOf = (sessionID) => {
    if (!validId(sessionID)) return "";
    let id = sessionID;
    const guard = new Set();
    while (childToParent.has(id) && !guard.has(id)) {
      guard.add(id);
      const parent = childToParent.get(id);
      if (!validId(parent)) return "";
      id = parent;
    }
    return id;
  };
  const rememberSession = (info) => {
    const id = info?.id;
    if (!validId(id) || deletedSessions.has(id)) return;
    if (validId(info.parentID) && info.parentID !== id) {
      childToParent.set(id, info.parentID);
      knownSessions.add(id);
    } else if (info.parentID === null) {
      // An explicit null means that this session is a root. An omitted
      // parentID is intentionally left untouched so updates cannot lose it
      // or prevent native resolution of an unseen child.
      childToParent.delete(id);
      knownSessions.add(id);
    } else if (childToParent.has(id)) {
      knownSessions.add(id);
    }
  };
  const isLoggingSuspended = (id, expectedSignature) =>
    !enabled() || expectedSignature !== enabledSignature() || deletedSessions.has(id);
  const rootReadyForSession = (root, sessionID) =>
    validId(root) && root === sessionID && !deletedSessions.has(root);
  const rootActive = (root) => validId(root) && !deletedSessions.has(root);
  const shouldLinkParent = (sessionID, parentID) =>
    !childToParent.has(sessionID) && validId(parentID) && parentID !== sessionID;
  const clearSessionState = (child) => {
    deletedSessions.add(child);
    knownSessions.delete(child);
    clearRetryTimers(seenAssistantBySession.get(child)?.values());
    clearRetryTimers(pendingPromptsBySession.get(child));
    clearRetryTimers(rootRetryMessagesBySession.get(child)?.values());
    for (const collection of [
      rootRetryMessagesBySession, pendingMessagesBySession, seenAssistantBySession,
      seenPromptIdsBySession, pendingPromptsBySession, promptDrainsBySession, childToParent,
    ]) collection.delete(child);
  };
  const forgetSession = (sessionID, parentID) => {
    if (!validId(sessionID)) return;
    if (shouldLinkParent(sessionID, parentID)) childToParent.set(sessionID, parentID);
    const root = rootOf(sessionID);
    for (const child of descendantsOf(sessionID, childToParent)) clearSessionState(child);
    trimOldest(deletedSessions, MAX_DELETED_SESSIONS);
    if (root !== sessionID || !validId(root)) return;
    for (const collection of [logFileByRoot, appendLocks, lastByRoot]) collection.delete(root);
  };
  const ensureLogFile = (root) => {
    if (!validId(root)) throw new Error("OpenCode session ID is invalid");
    if (logFileByRoot.has(root)) {
      const existing = logFileByRoot.get(root);
      assertLogFile(existing, root);
      return existing;
    }
    const dir = path.join(LOGS_DIR, projectSlug);
    if (!pathInside(LOGS_DIR, dir)) throw new Error("OpenCode log directory escapes the configured root");
    ensureDirectory(dir);
    const prefix = root.slice(0, 12);
    const escapedPrefix = prefix.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const candidatePattern = new RegExp(`^session_[0-9_-]+_${escapedPrefix}\\.md$`);
    const candidates = fs.readdirSync(dir)
      .filter((name) => candidatePattern.test(name))
      .sort()
      .reverse();
    for (const name of candidates) {
      const candidate = path.join(dir, name);
      if (!pathInside(dir, candidate)) continue;
      try {
        assertLogFile(candidate, root);
        logFileByRoot.set(root, candidate);
        return candidate;
      } catch {
        // Ignore stale or malformed candidates and create a fresh managed file.
      }
    }
    const file = path.join(dir, `session_${fileStamp()}_${root}.md`);
    if (!pathInside(dir, file)) throw new Error("OpenCode log file escapes the configured root");
    const header = `# Prompts — ${new Date().toISOString()}\n\n**Session ID:** ${root}\n**Resume:** \`opencode --session ${root}\`\n\n---\n\n`;
    secureEnsureFile(file, header);
    assertLogFile(file, root);
    logFileByRoot.set(root, file);
    return file;
  };

  const append = (root, text, dedupeSuffix = false, expectedSignature = enabledSignature(), dedupeKey = "") => {
    const previous = appendLocks.get(root) || Promise.resolve();
    const operation = previous.then(() => {
      if (isLoggingSuspended(root, expectedSignature)) return false;
      const file = logFileByRoot.get(root);
      if (!file) return false;
      return appendFileLocked(file, root, text, { dedupeSuffix, enableSignature: expectedSignature, dedupeKey });
    });
    const settled = operation.then(
      (result) => {
        if (appendLocks.get(root) === settled) appendLocks.delete(root);
        return result;
      },
      (error) => {
        if (appendLocks.get(root) === settled) appendLocks.delete(root);
        throw error;
      },
    );
    appendLocks.set(root, settled);
    return settled;
  };

  const switchLine = (root, model, mode) => {
    const previous = lastByRoot.get(root);
    lastByRoot.set(root, { model, mode });
    if (!previous) return "";
    const parts = [];
    if (differs(previous.model, model)) parts.push(`model ${previous.model} → ${model}`);
    if (differs(previous.mode, mode)) parts.push(`agent ${previous.mode} → ${mode}`);
    return parts.length ? `switched: ${parts.join(", ")}\n` : "";
  };
  const nativeSessionParent = async (sessionID) => {
    let snapshot = "";
    try {
      const databasePath = resolveDatabasePath();
      assertNoFollowContained(DATA_DIR, databasePath);
      snapshot = secureDatabaseSnapshot(databasePath);
      const { Database } = await import("bun:sqlite");
      const database = new Database(snapshot, { readonly: true });
      try {
        const tables = new Set(database.query(
          "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('session', 'session_v2')",
        ).all().map((row) => row.name));
        let schemaAvailable = false;
        for (const table of ["session", "session_v2"]) {
          if (!tables.has(table)) continue;
          schemaAvailable = true;
          try {
            const rows = database.query(`SELECT parent_id FROM ${table} WHERE id = ?`).all(sessionID);
            const row = rows[0];
            if (row) {
              return {
                available: true,
                found: true,
                parentID: validId(row.parent_id) ? row.parent_id : "",
              };
            }
          } catch {
            // Try the other supported schema variant if this table is stale.
          }
        }
        return { available: schemaAvailable, found: false, parentID: "" };
      } finally {
        database.close();
      }
    } catch {
      return { available: false, found: false, parentID: "" };
    } finally {
      if (snapshot) {
        try { fs.unlinkSync(snapshot); } catch {}
      }
    }
  };
  const resolveRoot = async (sessionID) => {
    if (deletedSessions.has(sessionID)) return "";
    let id = sessionID;
    const guard = new Set();
    while (validId(id)) {
      if (guard.has(id)) return null;
      guard.add(id);
      if (childToParent.has(id)) {
        id = childToParent.get(id);
        continue;
      }
      if (knownSessions.has(id)) break;
      const native = await nativeSessionParent(id);
      if (!native.available || !native.found) return undefined;
      if (!validId(native.parentID) || native.parentID === id) {
        knownSessions.add(id);
        break;
      }
      childToParent.set(id, native.parentID);
      id = native.parentID;
    }
    if (!validId(id)) return null;
    return rootOf(sessionID);
  };
  const queueMessage = (sessionID, message) => {
    const pending = pendingMessagesBySession.get(sessionID) || new Map();
    pending.set(message.id, message);
    pendingMessagesBySession.set(sessionID, pending);
  };
  const queuePrompt = (sessionID, prompt, enableSignature = enabledSignature(), dedupeKey = "") => {
    const pending = pendingPromptsBySession.get(sessionID) || [];
    pending.push({
      text: prompt,
      retryAttempts: 0,
      retryable: false,
      enableSignature,
      dedupeKey: dedupeKey || stableKey("prompt", sessionID, prompt),
    });
    pendingPromptsBySession.set(sessionID, pending);
  };
  const discardPending = (sessionID) => {
    clearRetryTimers(pendingPromptsBySession.get(sessionID));
    clearRetryTimers(seenAssistantBySession.get(sessionID)?.values());
    clearRetryTimers(rootRetryMessagesBySession.get(sessionID)?.values());
    for (const collection of [rootRetryMessagesBySession, seenAssistantBySession, seenPromptIdsBySession]) {
      collection.delete(sessionID);
    }
  };
  const removeQueuedMessage = (sessionID, messageID) => {
    const queued = pendingMessagesBySession.get(sessionID);
    queued?.delete(messageID);
    if (queued && !queued.size) pendingMessagesBySession.delete(sessionID);
  };
  const removeFirstPrompt = (sessionID, item, reason) => {
    const pending = pendingPromptsBySession.get(sessionID);
    if (pending?.[0] === item) pending.shift();
    if (!pending?.length) pendingPromptsBySession.delete(sessionID);
    process.stderr.write(`session-log: OpenCode ${reason} for ${sessionID}\n`);
  };
  const retryPrompt = (sessionID, item) => {
    scheduleRetry(item, () => drainPrompts(sessionID));
    if (item.exhausted) removeFirstPrompt(sessionID, item, "prompt retry limit reached");
  };
  const drainUnresolvedPrompt = (sessionID) => {
    const item = pendingPromptsBySession.get(sessionID)?.[0];
    if (!item) return;
    if (isDifferentSignature(item, enabledSignature())) {
      clearRetryTimer(item);
      removeFirstPrompt(sessionID, item, "discarded prompt across logging restart");
    } else retryPrompt(sessionID, item);
  };
  const appendQueuedPrompt = async (sessionID, root, item) => {
    if (isDifferentSignature(item, enabledSignature())) {
      removeFirstPrompt(sessionID, item, "discarded prompt across logging restart");
      return true;
    }
    try {
      ensureLogFile(root);
      const appended = await append(root, promptBlock(item.text), item.retryAttempts > 0, item.enableSignature, item.dedupeKey);
      if (!appended) {
        if (isDifferentSignature(item, enabledSignature())) {
          removeFirstPrompt(sessionID, item, "discarded prompt across logging restart");
          return true;
        }
        discardPending(sessionID);
        return false;
      }
    } catch (error) {
      if (isUnsafePathError(error)) throw error;
      item.retryError = error;
      retryPrompt(sessionID, item);
      return Boolean(item.exhausted);
    }
    clearRetryTimer(item);
    item.retryable = false;
    pendingPromptsBySession.get(sessionID)?.shift();
    return true;
  };
  const processPendingPrompts = async (sessionID, root) => {
    const pending = pendingPromptsBySession.get(sessionID);
    if (!pending) return;
    while (pending.length && !deletedSessions.has(sessionID)) {
      if (pending[0].retryTimer) return;
      if (!await appendQueuedPrompt(sessionID, root, pending[0])) return;
    }
    if (!pending.length) pendingPromptsBySession.delete(sessionID);
    const queued = pendingMessagesBySession.get(sessionID);
    if (queued && !pending.length) {
      for (const message of [...queued.values()]) await processMessage(message, sessionID, root);
    }
  };
  const drainPrompts = async (sessionID, resolvedRoot = undefined) => {
    const previous = promptDrainsBySession.get(sessionID) || Promise.resolve();
    const operation = previous.then(async () => {
      if (!enabled()) return discardPending(sessionID);
      const root = resolvedRoot === undefined ? await resolveRoot(sessionID) : resolvedRoot;
      if (root === undefined) return drainUnresolvedPrompt(sessionID);
      if (rootReadyForSession(root, sessionID)) await processPendingPrompts(sessionID, root);
    });
    const settled = operation.finally(() => {
      if (promptDrainsBySession.get(sessionID) === settled) promptDrainsBySession.delete(sessionID);
    });
    promptDrainsBySession.set(sessionID, settled);
    return settled;
  };
  const processPrompt = async (
    sessionID,
    prompt,
    resolvedRoot = undefined,
    expectedSignature = enabledSignature(),
    dedupeKey = "",
  ) => {
    if (isLoggingSuspended(sessionID, expectedSignature)) {
      if (!enabled()) discardPending(sessionID);
      return;
    }
    const root = resolvedRoot === undefined ? await resolveRoot(sessionID) : resolvedRoot;
    queuePrompt(sessionID, prompt, expectedSignature, dedupeKey);
    if (root === null) return;
    if (root === undefined) {
      await drainPrompts(sessionID);
      return;
    }
    if (!rootReadyForSession(root, sessionID)) return;
    await drainPrompts(sessionID, root);
  };

  const removeRootRetry = (sessionID, messageID) => {
    const retries = rootRetryMessagesBySession.get(sessionID);
    const retry = retries?.get(messageID);
    clearRetryTimer(retry);
    retries?.delete(messageID);
    if (retries && !retries.size) rootRetryMessagesBySession.delete(sessionID);
  };
  const reportExhaustedRoot = (sessionID, message, retry) => {
    if (!retry.exhausted) return;
    removeQueuedMessage(sessionID, message.id);
    if (retry.reported) return;
    retry.reported = true;
    process.stderr.write(`session-log: OpenCode root retry limit reached for ${sessionID}/${message.id}\n`);
  };
  const retryUnresolvedRoot = (sessionID, message, signature) => {
    const retries = rootRetryMessagesBySession.get(sessionID) || new Map();
    const retry = retries.get(message.id) || { retryAttempts: 0, retryable: false, enableSignature: signature };
    if (isDifferentSignature(retry, signature)) {
      removeQueuedMessage(sessionID, message.id);
      removeRootRetry(sessionID, message.id);
      return;
    }
    if (retry.exhausted) return reportExhaustedRoot(sessionID, message, retry);
    queueMessage(sessionID, message);
    retries.set(message.id, retry);
    rootRetryMessagesBySession.set(sessionID, retries);
    scheduleRetry(retry, () => processMessage(message, sessionID));
    reportExhaustedRoot(sessionID, message, retry);
  };
  const readAssistantBody = async (message, sessionID, root, snapshot) => {
    const response = await responseText(message.id);
    if (response.ok) return response;
    snapshot.retryError = new Error(`response text unavailable for ${message.id}`);
    if ((snapshot.retryAttempts || 0) < MAX_RETRY_ATTEMPTS) {
      snapshot.retryable = true;
      scheduleRetry(snapshot, () => processMessage(message, sessionID, root));
    } else {
      snapshot.pending = false;
      snapshot.retryable = false;
      snapshot.exhausted = true;
      process.stderr.write(`session-log: OpenCode response text unavailable for ${message.id}\n`);
    }
    return response;
  };
  const appendAssistant = async (root, sessionID, message, snapshot, text) => {
    const snapshots = seenAssistantBySession.get(sessionID);
    try {
      const appended = await append(root, text, snapshot.retryAttempts > 0, snapshot.enableSignature, snapshot.dedupeKey);
      if (!appended) {
        if (!enabled()) discardPending(sessionID);
        else if (isDifferentSignature(snapshot, enabledSignature())) {
          clearRetryTimer(snapshot);
          snapshots.delete(message.id);
        } else discardPending(sessionID);
        return;
      }
    } catch (error) {
      snapshot.retryError = error;
      if (isUnsafePathError(error)) throw error;
      scheduleRetry(snapshot, () => processMessage(message, sessionID, root));
      return;
    }
    finishSnapshot(snapshot);
  };
  const processMessage = async (message, sessionID, resolvedRoot = undefined) => {
    if (!enabled() || deletedSessions.has(sessionID)) {
      if (!enabled()) discardPending(sessionID);
      return;
    }
    const signature = enabledSignature();
    const root = resolvedRoot === undefined ? await resolveRoot(sessionID) : resolvedRoot;
    if (root === undefined) return retryUnresolvedRoot(sessionID, message, signature);
    if (root === null) return queueMessage(sessionID, message);
    const retry = rootRetryMessagesBySession.get(sessionID)?.get(message.id);
    if (retry) {
      const stale = isDifferentSignature(retry, signature);
      removeRootRetry(sessionID, message.id);
      if (stale) {
        removeQueuedMessage(sessionID, message.id);
        return;
      }
    }
    if (!rootActive(root)) return;
    if (hasPendingPrompts(pendingPromptsBySession, sessionID)) {
      queueMessage(sessionID, message);
      await drainPrompts(sessionID, root);
      if (hasPendingPrompts(pendingPromptsBySession, sessionID)) return;
    }
    const snapshots = seenAssistantBySession.get(sessionID) || new Map();
    const snapshot = prepareAssistantSnapshot(snapshots, message, sessionID, root, signature);
    if (!snapshot) return;
    seenAssistantBySession.set(sessionID, snapshots);
    if (!rootActive(root)) return;
    try {
      ensureLogFile(root);
    } catch (error) {
      snapshot.retryError = error;
      if (isUnsafePathError(error)) throw error;
      scheduleRetry(snapshot, () => processMessage(message, sessionID, root));
      return;
    }
    const elapsed = elapsedSeconds(message.time?.created, message.time?.completed);
    if (sessionID !== root) {
      await appendAssistant(root, sessionID, message, snapshot, subagentLine(sessionID, message, elapsed));
      return;
    }
    const response = await readAssistantBody(message, sessionID, root, snapshot);
    if (!response.ok || !rootActive(root)) return;
    if (snapshot.switched === undefined) snapshot.switched = switchLine(root, message.modelID, message.mode);
    await appendAssistant(root, sessionID, message, snapshot, responseBlock(response.text, elapsed, message, snapshot.switched));
  };
  const reconcilePending = async () => {
    if (!enabled()) return;
    const sessions = new Set([...pendingMessagesBySession.keys(), ...pendingPromptsBySession.keys()]);
    for (const sessionID of sessions) {
      if (deletedSessions.has(sessionID)) continue;
      const root = await resolveRoot(sessionID);
      if (root === null) continue;
      await drainPrompts(sessionID, root);
      if (pendingPromptsBySession.get(sessionID)?.length) continue;
      const pendingMessages = pendingMessagesBySession.get(sessionID);
      if (pendingMessages) {
        for (const message of [...pendingMessages.values()]) await processMessage(message, sessionID, root);
      }
    }
  };
  const handleSessionDeleted = (properties) => {
    const info = properties.info;
    const id = info?.id || properties.sessionID;
    if (!validId(id)) return undefined;
    return enqueueSession(id, () => forgetSession(id, info?.parentID));
  };
  const handleSessionUpserted = (properties) => {
    const info = properties.info;
    if (!validId(info?.id) || deletedSessions.has(info.id)) return undefined;
    return enqueueSession(info.id, async () => {
      rememberSession(info);
      await reconcilePending(info.id);
    });
  };
  const handleMessageUpdated = (properties) => {
    if (!enabled()) return undefined;
    const message = properties.info;
    if (!isLoggableAssistantMessage(message)) return undefined;
    const sessionID = message.sessionID || properties.sessionID;
    if (!validId(sessionID)) return undefined;
    return enqueueSession(sessionID, () => processMessage(message, sessionID));
  };
  const eventHandlers = {
    "session.deleted": handleSessionDeleted,
    "session.created": handleSessionUpserted,
    "session.updated": handleSessionUpserted,
    "message.updated": handleMessageUpdated,
  };
  const onEvent = (event) => {
    const handler = eventHandlers[event?.type];
    return handler ? handler(event?.properties || {}) : undefined;
  };

  const repeatedPrompt = (sessionID, promptID, output) => {
    if (hasExplicitPromptId(promptID)) {
      const seen = seenPromptIdsBySession.get(sessionID) || new Set();
      const key = String(promptID);
      if (seen.has(key)) return true;
      seen.add(key);
      seenPromptIdsBySession.set(sessionID, seen);
      trimOldest(seen, MAX_PROMPT_IDS_PER_SESSION);
    } else if (output && typeof output === "object") {
      if (seenPromptOutputs.has(output)) return true;
      seenPromptOutputs.add(output);
    }
    return false;
  };
  const onChatMessage = (input, output) => {
    const { sessionID } = input;
    if (!enabled() || !validId(sessionID)) return;
    const expectedSignature = enabledSignature();
    return enqueueSession(sessionID, async () => {
      if (deletedSessions.has(sessionID)) return;
      const prompt = textParts(output?.parts).trim();
      if (!prompt) return;
      const promptID = promptIdentifier(input, output);
      if (repeatedPrompt(sessionID, promptID, output)) return;
      const promptKey = hasExplicitPromptId(promptID)
        ? stableKey("prompt-id", sessionID, promptID)
        : stableKey("prompt", sessionID, prompt);
      const root = await resolveRoot(sessionID);
      // chat.message is emitted for the active user-facing session even when
      // native storage has not yet materialized its root row.
      if (root === undefined) knownSessions.add(sessionID);
      await processPrompt(sessionID, prompt, root === undefined ? sessionID : root === null ? undefined : root,
        expectedSignature, promptKey);
    });
  };
  return {
    "chat.message": onChatMessage,
    event: async ({ event }) => onEvent(event),
  };
};