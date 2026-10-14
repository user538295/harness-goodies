// universal-session-log: managed
// Universal session-log adapter for the OpenCode V2 plugin API. The opencode-goodies package
// plugin (.opencode-plugin/index.js) runs setupSessionLog from its own setup, so logging needs no
// installed file: it stays idle until the enable flag exists, and `/session-log` runs this
// package's CLI with the explicit `opencode` identity.
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { execFile, execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";

const PACKAGE_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const VERSION = fs.readFileSync(path.join(PACKAGE_ROOT, "VERSION"), "utf8").trim();
const COMMAND_NAME = "session-log";
const COMMAND_DESCRIPTION = "Manage session prompt logging and usage totals (on / off / status / usage / repair)";
const DEFAULT_COMMAND = "status";

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
// What older releases left in CONFIG_DIR; lib/legacy_cleanup.sh (legacy_cleanup_opencode) removes
// what of it is proven session-log's, so keep the two lists in step.
const LEGACY_LEFTOVERS = [
  "commands/session-log.md", "commands/session-log-omp.md", "plugins/session-log.js", "plugins/session-log-omp.js",
  "scripts/session_log_usage.sh", "skills/session-log", "session-log/adapter.version", "session-log/adapter.manifest",
  "session-log/desired.version",
];

const report = (error) => process.stderr.write(`session-log: ${error?.message || error}\n`);
// Why logging cannot run with this configuration root, or "" when it can.
const rootProblem = () => {
  const configHome = process.env.XDG_CONFIG_HOME || defaultConfigHome;
  try {
    if (containsSymlinkComponent(configHome) || containsSymlinkComponent(path.join(configHome, "opencode"))) {
      return "OpenCode root contains an unsafe symlink";
    }
  } catch (error) {
    return `OpenCode root cannot be checked: ${error.message}`;
  }
  if (canonicalPath(configHome) !== canonicalPath(defaultConfigHome)) {
    return "OpenCode root is relocated; universal session-log does not support custom roots";
  }
  return "";
};
const exists = (file) => {
  try {
    fs.lstatSync(file);
    return true;
  } catch {
    return false;
  }
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

let processStartTime;
const processStart = () => {
  if (processStartTime === undefined) {
    try {
      processStartTime = execFileSync("ps", ["-p", String(process.pid), "-o", "lstart="], { encoding: "utf8" }).trim();
    } catch {
      processStartTime = "";
    }
  }
  return processStartTime;
};
const appendFileLocked = (file, root, text, enableSignature, dedupeKey) => {
  assertLogFile(file, root);
  const payload = text.endsWith("\n") ? text : `${text}\n`;
  return secureAppend(file, payload, { dedupe: Boolean(dedupeKey), enableSignature, dedupeKey });
};
// The runtime marker `status` reads: this process has the session-log handlers of this VERSION.
function markRuntime() {
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
    process_start: processStart(),
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
const markRuntimeReported = () => {
  try {
    markRuntime();
  } catch (error) {
    report(error);
  }
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

// The logging state of one plugin instance. `onPrompt` takes the V2 `prompt` hook input and
// `onEvent` one event of `ctx.event.subscribe()`; both return at once and log in arrival order on
// one queue, which `idle` awaits. A session's root comes from `session.created` or, for a session
// created before this instance started, from `ctx.session.get`. Only the root session's prompts
// are logged; each step of the root logs a response block, each step of a descendant a
// sub-agent line, both in the root's log file.
export function createSessionLog(ctx, { now = Date.now } = {}) {
  const projectSlug = String(ctx.location?.directory || process.cwd()).split(path.sep).filter(Boolean).slice(-2).join("-") || "root";
  const parents = new Map(); // session ID → parent session ID, "" for a root
  const deletedSessions = new Set();
  const steps = new Map(); // assistant message ID → the step that has started but not ended
  const logFileByRoot = new Map();
  const lastByRoot = new Map();
  let queue = Promise.resolve();
  const enqueue = (operation) => {
    queue = queue.then(operation).catch(report);
    return queue;
  };
  const idle = async () => {
    let current;
    do {
      current = queue;
      await current;
    } while (current !== queue);
  };

  const remember = (sessionID, parentID) => {
    parents.set(sessionID, validId(parentID) && parentID !== sessionID ? parentID : "");
    trimOldest(parents, MAX_KNOWN_SESSIONS);
  };
  const forget = (sessionID) => {
    for (const id of descendantsOf(sessionID, parents)) {
      deletedSessions.add(id);
      for (const collection of [parents, logFileByRoot, lastByRoot]) collection.delete(id);
      for (const [messageID, step] of steps) if (step.sessionID === id) steps.delete(messageID);
    }
    trimOldest(deletedSessions, MAX_DELETED_SESSIONS);
  };
  // The root of sessionID's tree, or "" when it is invalid, deleted, or part of a cycle.
  const rootOf = async (sessionID) => {
    let id = sessionID;
    const seen = new Set();
    while (validId(id) && !deletedSessions.has(id) && !seen.has(id)) {
      seen.add(id);
      if (!parents.has(id)) remember(id, (await ctx.session.get({ sessionID: id }))?.parentID);
      const parent = parents.get(id);
      if (!parent) return id;
      id = parent;
    }
    return "";
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
  // Appends only while the enable flag that was current when the content arrived still is.
  const append = (root, text, signature, dedupeKey) => {
    if (!enabled() || signature !== enabledSignature() || deletedSessions.has(root)) return;
    appendFileLocked(ensureLogFile(root), root, text, signature, dedupeKey);
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

  const onPrompt = ({ sessionID, messageID, prompt } = {}) => {
    const text = typeof prompt?.text === "string" ? prompt.text.trim() : "";
    if (!enabled() || !validId(sessionID) || !text) return queue;
    const signature = enabledSignature();
    const dedupeKey = validId(messageID) ? stableKey("prompt-id", sessionID, messageID) : stableKey("prompt", sessionID, text);
    return enqueue(async () => {
      // `off` removes the runtime marker; the next prompt after `on` writes it again.
      markRuntimeReported();
      if (await rootOf(sessionID) === sessionID) append(sessionID, promptBlock(text), signature, dedupeKey);
    });
  };
  const endStep = ({ sessionID, assistantMessageID, cost, tokens }) => {
    const step = steps.get(assistantMessageID);
    if (!step || step.sessionID !== sessionID) return;
    steps.delete(assistantMessageID);
    const elapsed = (now() - step.started) / 1000;
    const message = { mode: step.agent, modelID: step.model, tokens, cost };
    const body = [...step.texts].sort(([left], [right]) => left - right).map(([, text]) => text).filter(Boolean).join("\n");
    const dedupeKey = stableKey("assistant", sessionID, assistantMessageID);
    enqueue(async () => {
      const root = await rootOf(sessionID);
      if (!root) return;
      if (sessionID !== root) {
        append(root, subagentLine(sessionID, message, elapsed), step.signature, dedupeKey);
        return;
      }
      append(root, responseBlock(body, elapsed, message, switchLine(root, step.model, step.agent)), step.signature, dedupeKey);
    });
  };
  const eventHandlers = {
    "session.created": ({ sessionID, parentID }) => {
      if (validId(sessionID) && !deletedSessions.has(sessionID)) remember(sessionID, parentID);
    },
    "session.deleted": ({ sessionID }) => {
      if (validId(sessionID)) forget(sessionID);
    },
    "session.step.started": ({ sessionID, assistantMessageID, agent, model }) => {
      if (!enabled() || !validId(sessionID) || !validId(assistantMessageID) || deletedSessions.has(sessionID)) return;
      steps.set(assistantMessageID, { sessionID, agent, model: model?.id, started: now(), texts: new Map(), signature: enabledSignature() });
      trimOldest(steps, MAX_OPEN_STEPS);
    },
    "session.text.ended": ({ assistantMessageID, ordinal, text }) => {
      if (typeof text === "string") steps.get(assistantMessageID)?.texts.set(numeric(ordinal), text);
    },
    "session.step.ended": endStep,
    "session.step.failed": endStep,
  };
  const onEvent = (event) => {
    try {
      eventHandlers[event?.type]?.(event.data || {});
    } catch (error) {
      report(error);
    }
  };
  return { onPrompt, onEvent, idle };
}

const runSessionLogCommand = (text) => new Promise((resolve, reject) => {
  const argumentsText = text.trim() || DEFAULT_COMMAND;
  execFile("bash", [path.join(PACKAGE_ROOT, "install.sh"), "--harness", "opencode", "--arguments", argumentsText],
    { encoding: "utf8" }, (error, stdout, stderr) => {
      if (error) reject(new Error(stderr.trim() || stdout.trim() || error.message));
      else resolve(stdout.trim());
    });
});

// Runs `repair` when an older release left something behind, which removes what of it is proven
// session-log's; a leftover markdown /session-log command would otherwise override this
// package's. Best effort: its output and failure are discarded, as `on` and `repair` report them.
export function cleanupLegacyLeftovers() {
  if (!LEGACY_LEFTOVERS.some((relative) => exists(path.join(CONFIG_DIR, relative)))) return Promise.resolve();
  return new Promise((resolve) => {
    execFile("bash", [path.join(PACKAGE_ROOT, "bin/session-log"), "--entrypoint", "opencode", "--harness", "opencode", "repair"],
      () => resolve());
  });
}

// Registers `/session-log`, then (unless the configuration root is unsupported) the logging
// handlers. Returns the cleanup that stops the event stream and waits for pending log writes.
export async function setupSessionLog(ctx) {
  await ctx.command.transform((editor) => {
    editor.add({
      name: COMMAND_NAME,
      description: COMMAND_DESCRIPTION,
      execute: async ({ sessionID, prompt }) => {
        const output = await runSessionLogCommand(prompt?.text ?? "");
        await ctx.session.synthetic({ sessionID, text: output, description: output, resume: false });
      },
    });
  });
  const problem = rootProblem();
  if (problem) {
    report(problem);
    return () => {};
  }
  void cleanupLegacyLeftovers();
  markRuntimeReported();
  const log = createSessionLog(ctx);
  await ctx.session.hook("prompt", (input) => {
    log.onPrompt(input);
  });
  const controller = new AbortController();
  (async () => {
    for await (const event of ctx.event.subscribe({ signal: controller.signal })) log.onEvent(event);
  })().catch((error) => {
    if (!controller.signal.aborted) report(error);
  });
  return async () => {
    controller.abort();
    await log.idle();
  };
}
