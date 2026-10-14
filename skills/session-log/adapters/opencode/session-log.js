// universal-session-log: managed
// Universal session-log adapter for the OpenCode V2 plugin API. The opencode-goodies package
// plugin (.opencode-plugin/index.js) runs setupSessionLog from its own setup, so logging needs no
// installed file: it stays idle until the enable flag exists, and `/session-log` runs this
// package's CLI with the explicit `opencode` identity.
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { execFile, execFileSync, spawn } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { fileURLToPath } from "node:url";

const PACKAGE_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const VERSION = fs.readFileSync(path.join(PACKAGE_ROOT, "VERSION"), "utf8").trim();
const HARNESS = "opencode";
const COMMAND_NAME = "session-log";
const COMMAND_DESCRIPTION = "Manage session prompt logging and usage totals (on / off / status / usage / repair)";
const DEFAULT_COMMAND = "status";
const USAGE_COMMAND = "usage";
// The path components of the project directory that name its log folder.
const PROJECT_SLUG_DEPTH = 2;
// Earlier releases named a log file after this many leading characters of the session ID.
const LEGACY_LOG_ID_LENGTH = 12;
// Bounds on child processes: a Python write helper (whose own lock wait is 5 s) and the CLI.
const HELPER_TIMEOUT_MS = 30_000;
const CLI_TIMEOUT_MS = 120_000;
const MAX_APPEND_ATTEMPTS = 3;
const APPEND_RETRY_DELAY_MS = 250;
const RESUBSCRIBE_DELAY_MS = 1_000;
const UNSAFE_PATH_CODE = "SESSION_LOG_UNSAFE_PATH";
// Random bytes that keep runtime temporary names unique between instances of one process.
const NONCE_BYTES = 8;

const stableKey = (...parts) => createHash("sha256").update(parts.map((part) => String(part)).join("\0")).digest("hex");
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
const home = canonicalPath(os.homedir());
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
const defaultConfigHome = path.join(home, ".config");
const CONFIG_DIR = path.join(canonicalPath(defaultConfigHome), "opencode");
const LOGS_DIR = path.join(CONFIG_DIR, "prompt-logs");
const ENABLED_FLAG = path.join(LOGS_DIR, ".enabled");
const STATE_DIR = path.join(CONFIG_DIR, "session-log");

const reportError = (error) => process.stderr.write(`session-log: ${error?.message || error}\n`);
// The roots bin/session-log refuses (guard_relocated_root), so the handlers never log where every
// command would fail. Returns why the root is unsupported, or "" when it is supported.
const describeUnsupportedRoot = () => {
  const configHome = process.env.XDG_CONFIG_HOME || defaultConfigHome;
  const relocated = [
    [configHome, defaultConfigHome],
    [process.env.OPENCODE_CONFIG_DIR || CONFIG_DIR, CONFIG_DIR],
    [process.env.XDG_DATA_HOME || path.join(home, ".local", "share"), path.join(home, ".local", "share")],
  ].some(([actual, expected]) => canonicalPath(actual) !== canonicalPath(expected));
  if (relocated) return "OpenCode root is relocated; universal session-log does not support custom roots";
  try {
    if (containsSymlinkComponent(configHome) || containsSymlinkComponent(path.join(configHome, "opencode"))) {
      return "OpenCode root contains an unsafe symlink";
    }
  } catch (error) {
    return `OpenCode root cannot be checked: ${error.message}`;
  }
  return "";
};
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
const unsafePathError = (message) => Object.assign(new Error(message), { code: UNSAFE_PATH_CODE });
const ensureDirectory = (directory) => {
  const resolved = path.resolve(directory);
  const parsed = path.parse(resolved);
  let current = parsed.root;
  for (const component of resolved.slice(parsed.root.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, component);
    let stat;
    try { stat = fs.lstatSync(current); } catch (error) {
      if (error?.code !== "ENOENT") throw error;
      try {
        fs.mkdirSync(current, { mode: 0o700 });
      } catch (mkdirError) {
        // Another OpenCode process or the session-log CLI may create it first.
        if (mkdirError?.code !== "EEXIST") throw mkdirError;
      }
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
// Bounds on what one plugin instance remembers: sessions it resolved, deleted-session tombstones,
// and steps that started but never ended (an interrupted step publishes no end event).
const MAX_KNOWN_SESSIONS = 4096;
const MAX_DELETED_SESSIONS = 4096;
const MAX_OPEN_STEPS = 512;
// Shared directory walk (create-on-missing, symlink-refusing) reused by the
// append and ensure-file Python helpers below, as an interpolated snippet.
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
const isUnsafeDirectory = (stat) => stat.isSymbolicLink() || !stat.isDirectory();
const isNotPrivateOrOwned = (stat, uid) =>
  (stat.mode & 0o077) !== 0 || (uid !== null && stat.uid !== uid);
const differs = (previous, next) => Boolean(previous && next && previous !== next);
const trimOldest = (collection, max) => {
  while (collection.size > max) collection.delete(collection.keys().next().value);
};
const descendantsOf = (sessionID, parentBySession) => {
  const childrenByParent = new Map();
  for (const [child, parent] of parentBySession) {
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
// Runs a Python helper without blocking OpenCode's event loop; one that outlives
// HELPER_TIMEOUT_MS is killed and fails. Isolated mode (-I) keeps the project directory and
// PYTHON* variables from replacing the modules the helper imports.
const runPython = (script, env, input = "") => new Promise((resolve, reject) => {
  const child = execFile("python3", ["-I", "-c", script], { env: { ...process.env, ...env }, encoding: "utf8", timeout: HELPER_TIMEOUT_MS },
    (error, stdout, stderr) => {
      if (error) reject(new Error(stderr.trim() || error.message, { cause: error }));
      else resolve(stdout);
    });
  // A helper that exits before reading its input fails through its exit status, not EPIPE.
  child.stdin.on("error", () => {});
  child.stdin.end(input);
});
const secureAppend = async (file, payload, { enableSignature, dedupeKey }) => {
  const expected = fs.lstatSync(file);
  const result = await runPython(SECURE_APPEND_SCRIPT, {
    SESSION_LOG_FILE: file,
    SESSION_LOG_EXPECTED_DEV: String(expected.dev),
    SESSION_LOG_EXPECTED_INO: String(expected.ino),
    SESSION_LOG_ENABLE_LOCK: path.join(LOGS_DIR, ".enabled.lock"),
    SESSION_LOG_ENABLE_FLAG: ENABLED_FLAG,
    SESSION_LOG_ENABLE_SIGNATURE: enableSignature,
    SESSION_LOG_DEDUPE: dedupeKey ? "1" : "0",
    SESSION_LOG_DEDUPE_KEY: dedupeKey,
  }, payload);
  return result.trim() === "1";
};
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
const secureEnsureFile = (file, content) =>
  runPython(SECURE_FILE_SCRIPT, { SESSION_LOG_FILE: file, SESSION_LOG_CONTENT: Buffer.from(content).toString("base64") });
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
const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

// The `ps` start time `status` compares with the live process; it never changes, so it is read once.
const readProcessStart = (() => {
  let start;
  return () => {
    if (start === undefined) {
      try {
        start = execFileSync("ps", ["-p", String(process.pid), "-o", "lstart="], { encoding: "utf8" }).trim();
      } catch {
        start = "";
      }
    }
    return start;
  };
})();
const processRuntimeFile = () => path.join(STATE_DIR, `runtime.${process.pid}.json`);
// The runtime marker `status` reads: this process runs the session-log handlers of this VERSION.
function markRuntime() {
  ensureDirectory(STATE_DIR);
  const nonce = `${process.pid}.${Date.now()}.${randomBytes(NONCE_BYTES).toString("hex")}`;
  const temporary = path.join(STATE_DIR, `.runtime.${nonce}.tmp`);
  const processTemporary = path.join(STATE_DIR, `.runtime.${nonce}.process.tmp`);
  const runtime = path.join(STATE_DIR, "runtime.json");
  const processRuntime = processRuntimeFile();
  const backup = path.join(STATE_DIR, `.runtime.${nonce}.backup`);
  ensureFile(temporary);
  ensureFile(processTemporary);
  ensureFile(runtime);
  ensureFile(processRuntime);
  const payload = {
    harness: HARNESS,
    version: VERSION,
    pid: process.pid,
    process_start: readProcessStart(),
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
// Marks the runtime only while logging is on, as the marker means "these handlers are logging".
const markRuntimeWhileEnabled = () => {
  if (!enabled()) return;
  try {
    markRuntime();
  } catch (error) {
    reportError(error);
  }
};
// This process's own marker; runtime.json may already name a newer process, so it stays.
const removeProcessRuntime = () => {
  try {
    const file = processRuntimeFile();
    if (fs.lstatSync(file).isFile()) fs.unlinkSync(file);
  } catch {}
};

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
// The step's text parts in their stream order.
const stepBody = (texts) => [...texts].sort(([left], [right]) => left - right).map(([, text]) => text).filter(Boolean).join("\n");

// The session tree one plugin instance knows: parents from `session.created` or, for a session
// created before the instance started, from `ctx.session.get`, plus tombstones of deleted trees.
function createSessionTree(ctx) {
  const parentBySession = new Map(); // session ID → parent session ID, "" for a root
  const deletedSessions = new Set();
  const remember = (sessionID, parentID) => {
    parentBySession.set(sessionID, validId(parentID) && parentID !== sessionID ? parentID : "");
    trimOldest(parentBySession, MAX_KNOWN_SESSIONS);
  };
  // Tombstones sessionID and every descendant; returns them.
  const forgetTree = (sessionID) => {
    const forgotten = descendantsOf(sessionID, parentBySession);
    for (const id of forgotten) {
      deletedSessions.add(id);
      parentBySession.delete(id);
    }
    trimOldest(deletedSessions, MAX_DELETED_SESSIONS);
    return forgotten;
  };
  const isWalkable = (id, seen) => validId(id) && !deletedSessions.has(id) && !seen.has(id);
  // The root of sessionID's tree, or "" when it is invalid, deleted, or part of a cycle.
  const resolveRoot = async (sessionID) => {
    let id = sessionID;
    const seen = new Set();
    while (isWalkable(id, seen)) {
      seen.add(id);
      if (!parentBySession.has(id)) remember(id, (await ctx.session.get({ sessionID: id }))?.parentID);
      const parent = parentBySession.get(id);
      if (!parent) return id;
      id = parent;
    }
    return "";
  };
  return { remember, forgetTree, resolveRoot, isDeleted: (id) => deletedSessions.has(id) };
}

// The logging state of one plugin instance. `onPrompt` takes the V2 `prompt` hook input and
// `onEvent` one event of `ctx.event.subscribe()`; both return at once and log in arrival order on
// one queue, which `waitUntilIdle` awaits. Only the root session's prompts are logged; each step of
// the root logs a response block, each step of a descendant a sub-agent line, both in the root's
// log file. A step's working time runs from its `session.step.started` to its end event.
export function createSessionLog(ctx, { now = Date.now } = {}) {
  const projectSlug = String(ctx.location?.directory || process.cwd()).split(path.sep).filter(Boolean)
    .slice(-PROJECT_SLUG_DEPTH).join("-") || "root";
  const tree = createSessionTree(ctx);
  const openStepsByMessage = new Map(); // assistant message ID → the step that has started but not ended
  const logFileByRoot = new Map();
  const lastByRoot = new Map();
  let queue = Promise.resolve();
  const enqueue = (operation) => {
    queue = queue.then(operation).catch(reportError);
    return queue;
  };
  const waitUntilIdle = async () => {
    let current;
    do {
      current = queue;
      await current;
    } while (current !== queue);
  };

  const ensureLogFile = async (root) => {
    if (!validId(root)) throw new Error("OpenCode session ID is invalid");
    if (logFileByRoot.has(root)) {
      const existing = logFileByRoot.get(root);
      assertLogFile(existing, root);
      return existing;
    }
    const dir = path.join(LOGS_DIR, projectSlug);
    if (!pathInside(LOGS_DIR, dir)) throw new Error("OpenCode log directory escapes the configured root");
    ensureDirectory(dir);
    // Releases before this one named the file after the first characters of the session ID.
    const escape = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const candidatePattern = new RegExp(`^session_[0-9_-]+_(${escape(root)}|${escape(root.slice(0, LEGACY_LOG_ID_LENGTH))})\\.md$`);
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
    await secureEnsureFile(file, header);
    assertLogFile(file, root);
    logFileByRoot.set(root, file);
    return file;
  };
  const isLoggingSuspended = (root, signature) => !enabled() || signature !== enabledSignature() || tree.isDeleted(root);
  // Appends only while the enable flag that was current when the content arrived still is. A failed
  // write is retried; the dedupe key keeps a write that did land from repeating.
  const append = async ({ root, text, signature, dedupeKey }) => {
    const payload = text.endsWith("\n") ? text : `${text}\n`;
    for (let attempt = 1; ; attempt += 1) {
      if (isLoggingSuspended(root, signature)) return;
      try {
        const file = await ensureLogFile(root);
        assertLogFile(file, root);
        await secureAppend(file, payload, { enableSignature: signature, dedupeKey });
        return;
      } catch (error) {
        if (error?.code === UNSAFE_PATH_CODE || attempt >= MAX_APPEND_ATTEMPTS) throw error;
        await delay(APPEND_RETRY_DELAY_MS * attempt);
      }
    }
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

  const isLoggablePrompt = (sessionID, text) => enabled() && validId(sessionID) && Boolean(text);
  const onPrompt = ({ sessionID, messageID, prompt } = {}) => {
    const text = typeof prompt?.text === "string" ? prompt.text.trim() : "";
    if (!isLoggablePrompt(sessionID, text)) return queue;
    const signature = enabledSignature();
    const dedupeKey = validId(messageID) ? stableKey("prompt-id", sessionID, messageID) : stableKey("prompt", sessionID, text);
    return enqueue(async () => {
      // `off` removes the runtime marker; the next prompt after `on` writes it again.
      markRuntimeWhileEnabled();
      if (await tree.resolveRoot(sessionID) !== sessionID) return;
      await append({ root: sessionID, text: promptBlock(text), signature, dedupeKey });
    });
  };
  const endStep = ({ sessionID, assistantMessageID, cost, tokens }, endedAt) => {
    const step = openStepsByMessage.get(assistantMessageID);
    if (!step || step.sessionID !== sessionID) return;
    openStepsByMessage.delete(assistantMessageID);
    const elapsed = (endedAt - step.startedAt) / 1000;
    const dedupeKey = stableKey("assistant", sessionID, assistantMessageID);
    enqueue(async () => {
      const root = await tree.resolveRoot(sessionID);
      if (!root) return;
      const message = { mode: step.agent, modelID: step.model, tokens, cost };
      const text = sessionID === root
        ? responseBlock(stepBody(step.texts), elapsed, message, switchLine(root, step.model, step.agent))
        : subagentLine(sessionID, message, elapsed);
      await append({ root, text, signature: step.signature, dedupeKey });
    });
  };
  const isLoggableStep = (sessionID, assistantMessageID) =>
    enabled() && validId(sessionID) && validId(assistantMessageID) && !tree.isDeleted(sessionID);
  const eventHandlers = {
    "session.created": ({ sessionID, parentID }) => {
      if (validId(sessionID) && !tree.isDeleted(sessionID)) tree.remember(sessionID, parentID);
    },
    "session.deleted": ({ sessionID }) => {
      if (!validId(sessionID)) return;
      for (const id of tree.forgetTree(sessionID)) {
        logFileByRoot.delete(id);
        lastByRoot.delete(id);
        for (const [messageID, step] of openStepsByMessage) if (step.sessionID === id) openStepsByMessage.delete(messageID);
      }
    },
    "session.step.started": ({ sessionID, assistantMessageID, agent, model }, createdAt) => {
      if (!isLoggableStep(sessionID, assistantMessageID)) return;
      openStepsByMessage.set(assistantMessageID,
        { sessionID, agent, model: model?.id, startedAt: createdAt, texts: new Map(), signature: enabledSignature() });
      trimOldest(openStepsByMessage, MAX_OPEN_STEPS);
    },
    "session.text.ended": ({ assistantMessageID, ordinal, text }) => {
      if (typeof text === "string") openStepsByMessage.get(assistantMessageID)?.texts.set(numeric(ordinal), text);
    },
    "session.step.ended": endStep,
    "session.step.failed": endStep,
  };
  // Times come from the event's own `created`, so queueing inside OpenCode does not stretch them.
  const onEvent = (event) => {
    try {
      const created = numeric(event?.created);
      eventHandlers[event?.type]?.(event.data || {}, created > 0 ? created : now());
    } catch (error) {
      reportError(error);
    }
  };
  return { onPrompt, onEvent, waitUntilIdle };
}

// Runs this package's CLI with the explicit OpenCode identity, as the skill entrypoint does. The CLI
// runs in its own process group, so its timeout also ends the helpers it started, which would
// otherwise hold its output open.
export const runSessionLogCli = (argumentsText, { timeoutMs = CLI_TIMEOUT_MS } = {}) => new Promise((resolve, reject) => {
  const child = spawn("bash", [path.join(PACKAGE_ROOT, "install.sh"), "--harness", HARNESS, "--arguments", argumentsText],
    { detached: true, stdio: ["ignore", "pipe", "pipe"] });
  const output = { stdout: "", stderr: "" };
  child.stdout.setEncoding("utf8").on("data", (chunk) => { output.stdout += chunk; });
  child.stderr.setEncoding("utf8").on("data", (chunk) => { output.stderr += chunk; });
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    try {
      process.kill(-child.pid, "SIGKILL");
    } catch {}
  }, timeoutMs);
  child.on("error", (error) => {
    clearTimeout(timer);
    reject(error);
  });
  child.on("close", (code, signal) => {
    clearTimeout(timer);
    if (timedOut) reject(new Error(`session-log: ${argumentsText} timed out after ${timeoutMs} ms`));
    else if (code !== 0) reject(new Error(output.stderr.trim() || output.stdout.trim() || `session-log: exited with ${code ?? signal}`));
    else resolve(output.stdout.trim());
  });
});
// `usage` without a target reports the session the command ran in, not the newest one.
const commandArguments = (text, sessionID) => {
  const argumentsText = text.trim() || DEFAULT_COMMAND;
  return argumentsText === USAGE_COMMAND && validId(sessionID) ? `${USAGE_COMMAND} ${sessionID}` : argumentsText;
};

// Runs `repair` once per installed package (its location and VERSION), whether or not it succeeds:
// it removes what older releases left in ~/.config/opencode and is proven session-log's, including a
// markdown /session-log command that would override this package's. Its report or failure goes to
// OpenCode's log; `on` and `repair` run the same cleanup and show the user what it did or why it failed.
export async function cleanupLegacyLeftovers() {
  const stamp = path.join(STATE_DIR, "legacy-cleanup.stamp");
  const fingerprint = `${PACKAGE_ROOT}\n${VERSION}\n`;
  try {
    if (fs.lstatSync(stamp).isFile() && fs.readFileSync(stamp, "utf8") === fingerprint) return;
  } catch {}
  try {
    const report = await runSessionLogCli("repair");
    if (report) reportError(report);
  } catch (error) {
    reportError(error);
  }
  try {
    ensureFile(stamp);
    fs.writeFileSync(stamp, fingerprint, { mode: 0o600 });
  } catch (error) {
    reportError(error);
  }
}

// Feeds every event to the log until `signal` aborts; a failed stream is reported and reopened.
const pumpEvents = async (ctx, sessionLog, signal) => {
  while (!signal.aborted) {
    try {
      for await (const event of ctx.event.subscribe({ signal })) sessionLog.onEvent(event);
    } catch (error) {
      if (!signal.aborted) reportError(error);
    }
    if (!signal.aborted) await delay(RESUBSCRIBE_DELAY_MS);
  }
};

// Instances of this module in this process (one per OpenCode location); the last one to close
// removes this process's runtime marker.
let openInstances = 0;

// Registers `/session-log`, then (unless the configuration root is unsupported) the logging
// handlers. Returns the cleanup that stops them and waits for pending log writes.
export async function setupSessionLog(ctx) {
  await ctx.command.transform((editor) => {
    editor.add({
      name: COMMAND_NAME,
      description: COMMAND_DESCRIPTION,
      execute: async ({ sessionID, prompt }) => {
        let output;
        try {
          output = await runSessionLogCli(commandArguments(prompt?.text ?? "", sessionID));
        } catch (error) {
          output = error.message;
        }
        // After `on`, these handlers log from the next prompt on, so `status` may say so at once.
        markRuntimeWhileEnabled();
        await ctx.session.synthetic({ sessionID, text: output, description: output, resume: false });
      },
    });
  });
  const unsupported = describeUnsupportedRoot();
  if (unsupported) {
    reportError(unsupported);
    return () => {};
  }
  const cleanup = cleanupLegacyLeftovers();
  const sessionLog = createSessionLog(ctx);
  const promptHook = await ctx.session.hook("prompt", (input) => {
    sessionLog.onPrompt(input);
  });
  const controller = new AbortController();
  void pumpEvents(ctx, sessionLog, controller.signal);
  openInstances += 1;
  markRuntimeWhileEnabled();
  return async () => {
    controller.abort();
    await promptHook?.dispose?.();
    await Promise.all([cleanup, sessionLog.waitUntilIdle()]);
    openInstances -= 1;
    if (openInstances === 0) removeProcessRuntime();
  };
}
