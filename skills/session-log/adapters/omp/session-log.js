// universal-session-log: managed
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
const stableKey = (...parts) => createHash("sha256").update(parts.map((part) => String(part)).join("\0")).digest("hex");
const SECURE_APPEND_SCRIPT = String.raw`
import fcntl
import json
import hashlib
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

def open_directory(path):
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
                os.mkdir(part, 0o700, dir_fd=fd)
                next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise
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
            raise RuntimeError("unsafe OMP dedupe state")
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
                raise RuntimeError("invalid OMP dedupe state")
            records.append(record)
        return records
    except UnicodeDecodeError:
        raise RuntimeError("invalid OMP dedupe state")
def retain_records(records):
    pending = [record for record in records if not record.get("committed")]
    committed = [record for record in records if record.get("committed")]
    return pending + committed[-max(0, 64 - len(pending)):]
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
        raise RuntimeError("unsafe OMP enable lock")
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
            raise RuntimeError("unsafe OMP enable flag")
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
                    raise TimeoutError("OMP append lock timed out")
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
                raise TimeoutError("OMP append lock timed out")
    dedupe_records = read_dedupe_records(parent_fd)
    path_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if (
        stat.S_ISLNK(path_stat.st_mode)
        or not stat.S_ISREG(path_stat.st_mode)
        or path_stat.st_dev != expected_dev
        or path_stat.st_ino != expected_ino
    ):
        raise RuntimeError("OMP log file changed during append")
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
        raise RuntimeError("unsafe OMP log file")
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
            raise RuntimeError("invalid OMP dedupe record")
        if offset < 0 or length < 0:
            raise RuntimeError("invalid OMP dedupe record")
        if offset < before < offset + length:
            for record in dedupe_records:
                if record is existing_record or record.get("legacy"):
                    continue
                try:
                    record_offset = int(record["offset"])
                except (KeyError, TypeError, ValueError):
                    raise RuntimeError("invalid OMP dedupe record")
                if record_offset >= offset:
                    raise RuntimeError("OMP dedupe record has later data")
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
            raise RuntimeError("OMP dedupe record does not match log")
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
const secureAppend = (file, payload, { dedupe = false, enableSignature = "", dedupeKey = "" } = {}) => {
	const expected = fs.lstatSync(file);
	const result = execFileSync("python3", ["-c", SECURE_APPEND_SCRIPT], {
		env: {
			...process.env,
			SESSION_LOG_FILE: file,
			SESSION_LOG_EXPECTED_DEV: String(expected.dev),
			SESSION_LOG_EXPECTED_INO: String(expected.ino),
			SESSION_LOG_ENABLE_LOCK: path.join(logDir, ".enabled.lock"),
			SESSION_LOG_ENABLE_FLAG: enabledFile,
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
parent_fd = os.open(os.sep, flags)
descriptor = None
created = False
try:
	for part in directory.split(os.sep)[1:]:
		if not part or part == ".":
			continue
		if part == "..":
			raise RuntimeError("parent traversal")
		try:
			next_fd = os.open(part, flags, dir_fd=parent_fd)
		except FileNotFoundError:
			try:
				os.mkdir(part, 0o700, dir_fd=parent_fd)
			except FileExistsError:
				pass
			next_fd = os.open(part, flags, dir_fd=parent_fd)
		os.close(parent_fd)
		parent_fd = next_fd
	try:
		descriptor = os.open(name, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd)
	except FileNotFoundError:
		descriptor = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=parent_fd)
		created = True
	current = os.fstat(descriptor)
	if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1 or current.st_uid != os.getuid() or (current.st_mode & 0o077):
		raise RuntimeError("unsafe OMP log file")
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

const rawHome = os.homedir();
const canonicalPath = (value) => {
	const resolved = path.resolve(value);
	try { return fs.realpathSync.native(resolved); } catch {}
	const suffix = [];
	let current = resolved;
	while (true) {
		try {
			const canonical = fs.realpathSync.native(current);
			return path.join(canonical, ...suffix.reverse());
		} catch (error) {
			if (error?.code !== "ENOENT") return resolved;
			const parent = path.dirname(current);
			if (parent === current) return resolved;
			suffix.push(path.basename(current));
			current = parent;
		}
	}
};
const home = canonicalPath(rawHome);
const containsSymlinkComponent = (value) => {
	const resolved = path.resolve(value);
	let current = resolved;
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
const rawDefaultAgentDir = path.join(home, ".omp", "agent");
const rawDefaultLogDir = path.join(rawDefaultAgentDir, "prompt-logs");
const rawAgentDir = process.env.PI_CODING_AGENT_DIR || rawDefaultAgentDir;
const rawLogDir = process.env.OMP_PROMPT_LOG_DIR || rawDefaultLogDir;
if (containsSymlinkComponent(rawAgentDir) || containsSymlinkComponent(rawLogDir)) {
	throw new Error("OMP root contains an unsafe symlink");
}
const defaultAgentDir = path.join(home, ".omp", "agent");
const defaultLogDir = path.join(defaultAgentDir, "prompt-logs");
const agentDir = canonicalPath(process.env.PI_CODING_AGENT_DIR || defaultAgentDir);
const logDir = canonicalPath(process.env.OMP_PROMPT_LOG_DIR || defaultLogDir);
if (agentDir !== canonicalPath(defaultAgentDir) || logDir !== canonicalPath(defaultLogDir)) {
	throw new Error("OMP root is relocated; universal session-log does not support custom roots");
}
const enabledFile = path.join(logDir, ".enabled");
const stateDir = path.join(agentDir, "session-log");
const packageRoot = path.resolve(import.meta.dir, "../..");
const version = fs.readFileSync(path.join(packageRoot, "VERSION"), "utf8").trim();
const MAX_APPEND_ATTEMPTS = 5;
const APPEND_RETRY_DELAY_MS = 250;
const MAX_CLOSED_SESSIONS = 1024;
const validId = (value) => typeof value === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(value);
function isEnabled() {
	try { return fs.lstatSync(enabledFile).isFile(); } catch { return false; }
}
function enableSignature() {
	try {
		const stat = fs.lstatSync(enabledFile);
		if (!stat.isFile()) return "";
		const token = stat.size < 4096 ? fs.readFileSync(enabledFile, "utf8") : "";
		return token || `${stat.dev}:${stat.ino}`;
	} catch {
		return "";
	}
}
const pathInside = (root, candidate) => {
	const base = path.resolve(root);
	const resolved = path.resolve(candidate);
	return resolved === base || resolved.startsWith(`${base}${path.sep}`);
};
const safePath = (base, candidate, allowMissing = true) => {
	const root = path.resolve(base);
	const resolved = path.resolve(candidate);
	if (!pathInside(root, resolved)) throw new Error(`OMP path escapes the configured root: ${resolved}`);
	try {
		if (fs.lstatSync(root).isSymbolicLink()) throw new Error(`OMP path contains a symlink: ${root}`);
	} catch (error) {
		if (error?.code !== "ENOENT" || !allowMissing) throw error;
	}
	let current = root;
	for (const component of resolved.slice(root.length).split(path.sep).filter(Boolean)) {
		current = path.join(current, component);
		try {
			const stat = fs.lstatSync(current);
			if (stat.isSymbolicLink()) throw new Error(`OMP path contains a symlink: ${current}`);
		} catch (error) {
			if (error?.code !== "ENOENT" || !allowMissing) throw error;
			break;
		}
	}
	return resolved;
};
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
		if (stat.isSymbolicLink() || !stat.isDirectory()) throw new Error(`OMP path is not a safe directory: ${current}`);
		const privateDirectory = pathInside(logDir, current) || pathInside(stateDir, current);
		const uid = typeof process.getuid === "function" ? process.getuid() : null;
		const ownedByAnotherUser = privateDirectory && uid !== null && stat.uid !== uid;
		if (ownedByAnotherUser) {
			throw new Error(`OMP private directory is not owned by this process: ${current}`);
		}
		if (privateDirectory && (stat.mode & 0o077) !== 0) fs.chmodSync(current, 0o700);
	}
};
const ensureFile = (file, allowMissing = true) => {
	ensureDirectory(path.dirname(file));
	try {
		const stat = fs.lstatSync(file);
		if (stat.isSymbolicLink() || !stat.isFile()) throw new Error(`OMP path is not a safe file: ${file}`);
	} catch (error) {
		if (error?.code !== "ENOENT" || !allowMissing) throw error;
	}
};
const isUnsafePathError = (error) => {
	const message = error instanceof Error ? error.message : String(error);
	return message.startsWith("OMP path ") || message.startsWith("OMP log ");
};

function contentText(content) {
	if (typeof content === "string") return content;
	if (!Array.isArray(content)) return "";
	return content.map(item => item?.type === "text" && typeof item.text === "string" ? item.text : "").filter(Boolean).join("\n");
}

function readHeader(file) {
	try {
		for (const line of fs.readFileSync(file, "utf8").split("\n").slice(0, 8)) {
			if (!line.trim()) continue;
			const entry = JSON.parse(line);
			if (entry.type === "session") return entry;
		}
	} catch {
		return {};
	}
	return {};
}

function sessionState(ctx) {
	const manager = ctx.sessionManager;
	let file = "";
	let id = "";
	let header = {};
	let metadataAvailable = true;
	try {
		file = manager?.getSessionFile?.() || "";
		id = manager?.getSessionId?.() || "";
		header = manager?.getHeader?.() || {};
	} catch {
		metadataAvailable = false;
	}
	if (file) {
		try {
			const sessionsRoot = path.join(agentDir, "sessions");
			const candidate = path.isAbsolute(file) ? file : path.join(sessionsRoot, file);
			file = safePath(sessionsRoot, candidate);
			ensureFile(file);
		} catch {
			file = "";
			metadataAvailable = false;
		}
	}
	if (!id && file) id = path.basename(file, ".jsonl");
	if (!validId(id)) id = "";
	return { file: file ? path.resolve(file) : "", id, header, metadataAvailable };
}
function sessionEntries(ctx) {
	try {
		const entries = ctx.sessionManager?.getEntries?.();
		return Array.isArray(entries) ? entries : null;
	} catch {
		return null;
	}
}

function rootState(ctx) {
	let current = sessionState(ctx);
	const unusableRoot = current.metadataAvailable === false || !current.file || !validId(current.id);
	if (unusableRoot) return null;
	const seen = new Set();
	while (!seen.has(current.file || current.id)) {
		seen.add(current.file || current.id);
		const parent = current.header?.parentSession;
		if (typeof parent !== "string" || !parent) return current;
		let parentFile;
		try {
			const sessionsRoot = path.join(agentDir, "sessions");
			const candidate = path.isAbsolute(parent) ? parent : path.join(sessionsRoot, parent);
			parentFile = safePath(sessionsRoot, candidate, false);
			ensureFile(parentFile, false);
		} catch {
			return null;
		}
		const parentHeader = readHeader(parentFile);
		const parentId = typeof parentHeader.id === "string" && validId(parentHeader.id) ? parentHeader.id : "";
		if (!parentId) return null;
		current = { file: parentFile, id: parentId, header: parentHeader };
	}
	return null;
}

function projectSlug(cwd) {
	const slug = String(cwd || "unknown-project").replace(/^\/+/, "").replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "");
	return slug || "unknown-project";
}

function logPathFor(root, cwd) {
	if (!validId(root.id)) throw new Error("OMP session ID is invalid");
	const project = projectSlug(root.header?.cwd || cwd);
	const directory = path.join(logDir, project);
	if (!pathInside(logDir, directory)) throw new Error("OMP log directory escapes the configured root");
	const file = path.join(directory, `session_${root.id}.md`);
	return safePath(logDir, file);
}

function ensureLog(root, cwd) {
	const file = logPathFor(root, cwd);
	ensureDirectory(path.dirname(file));
	secureEnsureFile(file, `# Prompts — ${new Date().toISOString()}\n\n**Session ID:** ${root.id}\n**Session file:** ${root.file || "(not materialized yet)"}\n\n---\n`);
	ensureFile(file, false);
	const stat = fs.statSync(file);
	const uid = typeof process.getuid === "function" ? process.getuid() : null;
	if (uid !== null && stat.uid !== uid) throw new Error(`OMP log file is not owned by the current user: ${file}`);
	const permissions = stat.mode & 0o777;
	if (permissions > 0o600) throw new Error(`OMP log file permissions are too broad: ${file}`);
	const hasSessionId = fs.readFileSync(file, "utf8").split("\n").slice(0, 8).includes(`**Session ID:** ${root.id}`);
	if (!hasSessionId) throw new Error(`OMP log file has an unexpected session ID: ${file}`);
	return file;
}
function append(file, text, dedupe = false, expectedSignature = enableSignature(), dedupeKey = "") {
	if (!isEnabled() || expectedSignature !== enableSignature()) return false;
	ensureFile(file, false);
	const payload = text.endsWith("\n") ? text : `${text}\n`;
	if (!isEnabled() || expectedSignature !== enableSignature()) return false;
	return secureAppend(file, payload, { dedupe: Boolean(dedupeKey) || dedupe, enableSignature: expectedSignature, dedupeKey });
}
function finiteNumber(value) {
	if (typeof value === "number" && Number.isFinite(value)) return value;
	if (typeof value === "string" && value.trim() !== "") {
		const numeric = Number(value);
		if (Number.isFinite(numeric)) return numeric;
	}
	return undefined;
}
function timestampNumber(value) {
	const numeric = finiteNumber(value);
	if (numeric !== undefined) return numeric;
	if (typeof value === "string" && value.trim() !== "") {
		const timestamp = Date.parse(value);
		if (Number.isFinite(timestamp)) return timestamp;
	}
	return undefined;
}

function nonNegativeInteger(value) {
	const numeric = finiteNumber(value);
	return numeric === undefined ? 0 : Math.max(0, Math.floor(numeric));
}

function nonNegativeNumber(value) {
	const numeric = finiteNumber(value);
	return numeric === undefined ? 0 : Math.max(0, numeric);
}

function usageNumber(recorded, names) {
	for (const name of names) {
		const numeric = finiteNumber(recorded?.[name]);
		if (numeric !== undefined) return nonNegativeInteger(numeric);
	}
	return 0;
}

function nativeUsageNumber(recorded, names) {
	for (const name of names) {
		const numeric = finiteNumber(recorded?.[name]);
		if (numeric !== undefined) return nonNegativeInteger(numeric);
	}
	return undefined;
}

function messageTime(message) {
	const completed = timestampNumber(message?.completedAt);
	if (completed !== undefined && completed >= 0) return completed;
	const timestamp = timestampNumber(message?.timestamp);
	if (timestamp !== undefined && timestamp >= 0) return timestamp;
	return Date.now();
}

function modelName(message, ctx) {
	const model = typeof message?.model === "string" ? message.model : ctx.model?.id;
	const provider = typeof message?.provider === "string" ? message.provider : "";
	return model ? (provider && !model.includes("/") ? `${provider}/${model}` : model) : "";
}

function messageKey(message) {
	if (!message || message.role !== "assistant") return "";
	const identity = message.responseId || message.messageId || message.id;
	const hasStableIdentity = (typeof identity === "string" && identity) || typeof identity === "number";
	if (hasStableIdentity) return `id:${String(identity)}`;
	const timestamp = timestampNumber(message.timestamp);
	if (timestamp !== undefined) return `timestamp:${String(timestamp)}`;
	try {
		return `anonymous:${JSON.stringify(message)}`;
	} catch {
		return "";
	}
}
function seedSessionMessages(ctx, stores) {
	const state = sessionState(ctx);
	if (stores.committedMessages.has(state.id)) return;
	const entries = sessionEntries(ctx);
	const pending = stores.pendingMessages.get(state.id) || new Map();
	if (entries === null) {
		stores.pendingMessages.set(state.id, pending);
		return;
	}
	const committed = new Set();
	for (const entry of entries) if (entry?.message?.role === "assistant") committed.add(messageKey(entry.message));
	stores.committedMessages.set(state.id, committed);
	stores.pendingMessages.set(state.id, pending);
}

function pendingAssistantMessages(ctx, messages, stores) {
	const state = sessionState(ctx);
	seedSessionMessages(ctx, stores);
	const committed = stores.committedMessages.get(state.id) || new Set();
	const pending = stores.pendingMessages.get(state.id) || new Map();
	for (const message of messages || []) {
		if (message?.role !== "assistant") continue;
		const key = messageKey(message);
		if (key && !committed.has(key)) pending.set(key, message);
		else if (!key) pending.set(`anonymous:${pending.size}`, message);
	}
	return [...pending.values()];
}

function currentEffort(ctx) {
	const entries = sessionEntries(ctx) || [];
	for (let index = entries.length - 1; index >= 0; index -= 1) {
		if (entries[index]?.type === "thinking_level_change" && typeof entries[index].thinkingLevel === "string") return entries[index].thinkingLevel;
	}
	return "";
}

function summarize(messages, ctx) {
	const result = { input: 0, output: 0, reasoning: 0, cacheRead: 0, cacheWrite: 0, total: 0, cost: 0, models: new Set(), efforts: new Set() };
	const fallbackEffort = currentEffort(ctx);
	for (const message of messages || []) {
		if (message?.role !== "assistant" || !message.usage) continue;
		const recorded = message.usage;
		const input = usageNumber(recorded, ["input", "inputTokens", "input_tokens"]);
		const output = usageNumber(recorded, ["output", "outputTokens", "output_tokens"]);
		const reasoning = usageNumber(recorded, ["reasoning", "reasoningTokens", "reasoning_tokens"]);
		const cacheRead = usageNumber(recorded, ["cacheRead", "cacheReadTokens", "cache_read"]);
		const cacheWrite = usageNumber(recorded, ["cacheWrite", "cacheWriteTokens", "cache_write"]);
		result.input += input;
		result.output += output;
		result.reasoning += reasoning;
		result.cacheRead += cacheRead;
		result.cacheWrite += cacheWrite;
		const total = nativeUsageNumber(recorded, ["totalTokens", "total_tokens", "total"]);
		result.total += total ?? input + output + reasoning + cacheRead + cacheWrite;
		const cost = recorded.cost;
		result.cost += nonNegativeNumber(typeof cost === "number" ? cost : cost?.total);
		const model = modelName(message, ctx);
		if (model) result.models.add(model);
		const effort = message.thinkingLevel || message.effort || fallbackEffort;
		if (effort) result.efforts.add(effort);
	}
	return result;
}

function formatHms(milliseconds) {
	const seconds = Math.max(0, Math.floor(milliseconds / 1000));
	return [Math.floor(seconds / 3600), Math.floor((seconds % 3600) / 60), seconds % 60].map(value => String(value).padStart(2, "0")).join(":");
}

function usageLine(value) {
	const input = nonNegativeInteger(value.input);
	const output = nonNegativeInteger(value.output);
	const reasoning = nonNegativeInteger(value.reasoning);
	const cacheWrite = nonNegativeInteger(value.cacheWrite);
	const cacheRead = nonNegativeInteger(value.cacheRead);
	const total = nonNegativeInteger(value.total);
	const cost = nonNegativeNumber(value.cost);
	const models = [...value.models].sort().join("+") || "-";
	const efforts = [...value.efforts].sort().join("+") || "-";
	return `est. used token: input: ${input}, output: ${output}, reasoning: ${reasoning}, cache_write: ${cacheWrite}, cache_read: ${cacheRead}, total_tokens: ${total}, cost: $${cost.toFixed(4)}, model: ${models}, effort: ${efforts}`;
}

function responseText(messages) {
	for (let index = (messages || []).length - 1; index >= 0; index -= 1) {
		if (messages[index]?.role !== "assistant") continue;
		const text = contentText(messages[index].content);
		if (text) return text;
	}
	return "(no text response)";
}

function notify(ctx, message) {
	if (ctx.mode === "print") process.stdout.write(`${message}\n`);
	else ctx.ui?.notify?.(message, "info");
}

function processStart(pid) {
	try { return execFileSync("ps", ["-p", String(pid), "-o", "lstart="], { encoding: "utf8" }).trim(); } catch { return ""; }
}

function markLoaded() {
	ensureDirectory(stateDir);
	const nonce = `${process.pid}-${Date.now()}-${randomUUID()}`;
	const temporary = path.join(stateDir, `.runtime.${nonce}.tmp`);
	const processTemporary = path.join(stateDir, `.runtime.${nonce}.process.tmp`);
	const runtime = path.join(stateDir, "runtime.json");
	const processRuntime = path.join(stateDir, `runtime.${process.pid}.json`);
	const payload = { harness: "omp", version, pid: process.pid, process_start: processStart(process.pid), nonce };
	// Publish the per-process runtime before the canonical runtime.json pointer so a
	// mid-failure can't leave runtime.json referencing a pid with no per-process file.
	const temporaries = [processTemporary, temporary];
	const runtimes = [processRuntime, runtime];
	const owned = new Set();
	try {
		for (const file of [...temporaries, ...runtimes]) ensureFile(file);
		const encoded = `${JSON.stringify(payload)}\n`;
		for (const file of temporaries) {
			owned.add(file);
			fs.writeFileSync(file, encoded, { flag: "wx", mode: 0o600 });
			ensureFile(file, false);
		}
		for (let index = 0; index < temporaries.length; index += 1) {
			ensureFile(runtimes[index]);
			fs.renameSync(temporaries[index], runtimes[index]);
			owned.delete(temporaries[index]);
		}
	} catch (error) {
		for (const file of owned) {
			try { if (!fs.lstatSync(file).isSymbolicLink()) fs.unlinkSync(file); } catch {}
		}
		throw error;
	}
}
// A bare `usage` must report the session it was typed in, not whichever session
// in the project was written last; quote the path for the CLI's tokenizer.
function usageCommandArguments(rawArguments, sessionFile) {
	if (!sessionFile || rawArguments.trim() !== "usage") return rawArguments;
	return `usage '${sessionFile.replaceAll("'", "'\\''")}'`;
}

function currentSessionFile(ctx) {
	const { file } = sessionState(ctx);
	try { return file && fs.statSync(file).isFile() ? file : ""; } catch { return ""; }
}

function runUniversalCommand(argumentString, ctx) {
	const cli = path.join(packageRoot, "bin", "session-log");
	const rawArguments = usageCommandArguments(String(argumentString || "status"), currentSessionFile(ctx));
	const child = Bun.spawnSync([cli, "--entrypoint", "omp", "--harness", "omp", "--arguments", rawArguments], { stdout: "pipe", stderr: "pipe" });
	const stdout = new TextDecoder().decode(child.stdout).trimEnd();
	const stderr = new TextDecoder().decode(child.stderr).trim();
	if (child.exitCode !== 0) throw new Error(stderr || stdout || `session-log exited with ${child.exitCode}`);
	notify(ctx, stdout);
}

function createRun(overrides = {}) {
	return { messages: [], endSeen: false, settled: false, finalizing: false, appendAttempts: 0, responseDedupeKey: "", ...overrides };
}
const queuedEventHandlers = {
	end(run, item) {
		run.messages.push(...(item.event.messages || []));
		if (item.terminal !== false) {
			run.endSeen = true;
			run.settled = true;
		}
	},
	default(run, item) {
		for (const entry of sessionEntries(item.ctx) || []) {
			if (entry?.message?.role === "assistant") run.messages.push(entry.message);
		}
		run.settled = run.messages.some(message => message?.role === "assistant");
	},
};
function composeFinalizeRecord({ state, root, messages, ctx, start, end, previousModel }) {
	const summary = summarize(messages, ctx);
	const model = [...summary.models][0] || ctx.model?.id || "";
	const switched = model && previousModel && model !== previousModel ? `switched: ${previousModel} → ${model}\n` : "";
	const block = `${switched}${usageLine(summary)}\n\n---\n`;
	const record = state.id === root.id
		? `### ${new Date(end).toTimeString().slice(0, 8)} response\n\n${responseText(messages)}\n\nworking time: ${formatHms(end - start)}\n${block}`
		: `sub-agent: ${state.id}, working time: ${formatHms(end - start)}\n${usageLine(summary)}\n\n`;
	const responseIdentity = messages.map(messageKey).filter(Boolean).join("\0");
	return { record, model, responseIdentity };
}

export default function sessionLogOmp(pi, options = {}) {
	const now = options.now ?? Date.now;
	markLoaded();
	pi.registerCommand("session-log", {
		description: "Manage session prompt logging and usage totals",
		handler: async (args, ctx) => {
			try { runUniversalCommand(args, ctx); }
			catch (error) {
				const message = error instanceof Error ? error.message : String(error);
				notify(ctx, message);
				throw error;
			}
		},
	});
	const promptStates = new Map();
	const promptAttempts = new Map();
	const promptRetryTimers = new Map();
	const promptRetryAttempts = new Map();
	const lastModels = new Map();
	const committedMessages = new Map();
	const pendingMessages = new Map();
	const pendingRuns = new Map();
	const queuedRuns = new Map();
	const closedSessions = new Set();
	const finalizedRuns = new Set();
	const retryTimers = new Map();
	const stores = { committedMessages, pendingMessages };
	const clearRetry = (id) => {
		clearTimeout(retryTimers.get(id));
		retryTimers.delete(id);
	};
	const sessionUnavailable = (state) => !state.file || !validId(state.id) || closedSessions.has(state.id);
	const runSignatureMismatch = (run, signature) => run && run.enableSignature !== signature;
	const logAsyncFailure = (label, id) => (error) => {
		const message = error instanceof Error ? error.message : String(error);
		process.stderr.write(`session-log: ${label} failed for ${id}: ${message}\n`);
	};
	const clearPromptRetry = (id) => {
		clearTimeout(promptRetryTimers.get(id));
		promptRetryTimers.delete(id);
		promptRetryAttempts.delete(id);
	};
	const scheduleRetry = (id, ctx) => {
		if (retryTimers.has(id)) return;
		const timer = setTimeout(() => {
			retryTimers.delete(id);
			if (!pendingRuns.has(id) || finalizedRuns.has(id)) return;
			finalize(ctx).catch(logAsyncFailure("finalize", id));
		}, APPEND_RETRY_DELAY_MS);
		retryTimers.set(id, timer);
	};
	const schedulePromptRetry = (id, operation) => {
		if (promptRetryTimers.has(id)) return;
		const attempts = (promptRetryAttempts.get(id) || 0) + 1;
		if (attempts >= MAX_APPEND_ATTEMPTS) {
			const run = pendingRuns.get(id);
			if (run) abandonRun(id, run, "prompt retry limit reached");
			else {
				promptRetryAttempts.delete(id);
				promptAttempts.delete(id);
			}
			process.stderr.write(`session-log: prompt retry limit reached for ${id}\n`);
			return;
		}
		promptRetryAttempts.set(id, attempts);
		const timer = setTimeout(async () => {
			promptRetryTimers.delete(id);
			try {
				await operation();
			} catch {
				schedulePromptRetry(id, operation);
			}
		}, APPEND_RETRY_DELAY_MS);
		timer.unref?.();
		promptRetryTimers.set(id, timer);
	};
	const writePrompt = async (state, event, ctx, startedAt, dedupe = false, expectedSignature = enableSignature(), dedupeKey = "") => {
		if (!isEnabled() || expectedSignature !== enableSignature() || finalizedRuns.has(state.id)) {
			if (!isEnabled() || expectedSignature !== enableSignature()) discardDisabledSession(state.id);
			return;
		}
		const root = rootState(ctx);
		if (!root) throw new Error(`OMP session root unavailable: ${state.id}`);
		const logFile = ensureLog(root, ctx.cwd);
		if (state.id === root.id) {
			const appended = await append(logFile, `## ${new Date(startedAt).toTimeString().slice(0, 8)}\n\n${event.prompt.trim()}\n\n`, dedupe, expectedSignature, dedupeKey);
			if (!appended) {
				discardDisabledSession(state.id);
				return;
			}
		}
		clearPromptRetry(state.id);
		promptAttempts.delete(state.id);
		promptStates.set(state.id, { active: true, prompt: event.prompt, startedAt });
		const activeRun = pendingRuns.get(state.id);
		if (activeRun) {
			activeRun.promptReady = true;
			if (activeRun.endSeen && activeRun.settled) await finalize(ctx, expectedSignature);
		}
	};
	const queueFor = (id) => {
		let queue = queuedRuns.get(id);
		if (!queue) {
			queue = { prompts: [] };
			queuedRuns.set(id, queue);
		}
		return queue;
	};
	const startQueuedRun = async (id) => {
		const queue = queuedRuns.get(id);
		if (!queue?.prompts.length || pendingRuns.has(id)) return;
		const next = queue.prompts.shift();
		if (!queue.prompts.length) queuedRuns.delete(id);
		const state = sessionState(next.ctx);
		if (!state.file || !validId(state.id)) return;
		finalizedRuns.delete(id);
		const startedAt = now();
		const runSignature = next.enableSignature ?? enableSignature();
		const run = createRun({ prompt: next.event.prompt, ctx: next.ctx, enableSignature: runSignature, dedupeKey: next.dedupeKey });
		pendingRuns.set(id, run);
		promptAttempts.set(id, { prompt: next.event.prompt, startedAt });
		try {
			await writePrompt(state, next.event, next.ctx, startedAt, false, runSignature, next.dedupeKey);
		} catch (error) {
			if (isUnsafePathError(error)) throw error;
			promptAttempts.get(id).lastError = error;
			schedulePromptRetry(id, () => writePrompt(state, next.event, next.ctx, startedAt, true, runSignature, next.dedupeKey));
		}
		const events = next.events || [];
		if (!queue.prompts.length) queuedRuns.delete(id);
		for (const item of events) {
			if (item.enableSignature !== runSignature) continue;
			(queuedEventHandlers[item.type] || queuedEventHandlers.default)(run, item);
		}
		if (run.endSeen && run.settled) await finalize(next.ctx, runSignature);
	};
	const forgetSession = (id) => {
		promptStates.delete(id);
		promptAttempts.delete(id);
		committedMessages.delete(id);
		pendingMessages.delete(id);
		pendingRuns.delete(id);
		queuedRuns.delete(id);
		finalizedRuns.delete(id);
		lastModels.delete(id);
		clearRetry(id);
		clearPromptRetry(id);
	};
	const discardDisabledSession = forgetSession;
	pi.on("session_start", () => {
		markLoaded();
	});
	pi.on("session_shutdown", (_event, ctx) => {
		const state = sessionState(ctx);
		if (validId(state.id)) {
			closedSessions.add(state.id);
			if (closedSessions.size > MAX_CLOSED_SESSIONS) closedSessions.delete(closedSessions.values().next().value);
			forgetSession(state.id);
		}
	});
	pi.on("before_agent_start", async (event, ctx) => {
		if (!isEnabled()) {
			const disabledState = sessionState(ctx);
			if (validId(disabledState.id)) discardDisabledSession(disabledState.id);
			return;
		}
		const currentSignature = enableSignature();
		if (typeof event.prompt !== "string") return;
		const state = sessionState(ctx);
		if (sessionUnavailable(state)) return;
		seedSessionMessages(ctx, stores);
		let active = pendingRuns.get(state.id);
		if (runSignatureMismatch(active, currentSignature)) {
			discardDisabledSession(state.id);
			active = undefined;
		}
		if (active) {
			if (active.prompt === event.prompt && !active.endSeen && !active.finalizing) return;
			queueFor(state.id).prompts.push({
				event,
				ctx,
				enableSignature: currentSignature,
				dedupeKey: stableKey("omp-prompt", state.id, event.id ?? event.promptId ?? event.requestId ?? event.prompt),
				events: [],
			});
			return;
		}
		const previous = promptStates.get(state.id);
		if (previous?.active && previous.prompt === event.prompt) return;
		const attempt = promptAttempts.get(state.id);
		if (attempt?.prompt === event.prompt && promptRetryTimers.has(state.id)) return;
		finalizedRuns.delete(state.id);
		const startedAt = attempt?.prompt === event.prompt ? attempt.startedAt : now();
		const run = createRun({ promptReady: false, prompt: event.prompt, ctx, enableSignature: currentSignature, dedupeKey: stableKey("omp-prompt", state.id, event.id ?? event.promptId ?? event.requestId ?? event.prompt) });
		pendingRuns.set(state.id, run);
		promptAttempts.set(state.id, { prompt: event.prompt, startedAt });
		try {
			await writePrompt(state, event, ctx, startedAt, false, currentSignature, run.dedupeKey);
		} catch (error) {
			if (isUnsafePathError(error)) throw error;
			promptAttempts.get(state.id).lastError = error;
			schedulePromptRetry(state.id, () => writePrompt(state, event, ctx, startedAt, true, currentSignature, run.dedupeKey));
		}
	});
	const abandonRun = (id, run, reason) => {
		const hasNext = queuedRuns.get(id)?.prompts.length > 0;
		pendingRuns.delete(id);
		pendingMessages.delete(id);
		promptStates.delete(id);
		promptAttempts.delete(id);
		clearPromptRetry(id);
		clearRetry(id);
		if (hasNext) {
			finalizedRuns.delete(id);
			startQueuedRun(id).catch(logAsyncFailure("startQueuedRun", id));
		} else {
			finalizedRuns.add(id);
		}
		process.stderr.write(`session-log: OMP run abandoned for ${id}: ${reason}\n`);
	};
	const finalize = async (ctx, expectedSignature = enableSignature()) => {
		if (!isEnabled()) {
			const disabledState = sessionState(ctx);
			if (validId(disabledState.id)) discardDisabledSession(disabledState.id);
			return;
		}
		const state = sessionState(ctx);
		if (!state.file || !validId(state.id)) return;
		if (finalizedRuns.has(state.id)) return;
		const run = pendingRuns.get(state.id);
		const runNotReadyToFinalize = !run || !run.endSeen || run.finalizing;
		if (runNotReadyToFinalize) return;
		if (run.enableSignature !== expectedSignature || run.enableSignature !== enableSignature()) {
			discardDisabledSession(state.id);
			return;
		}
		if (!run.promptReady) return;
		if (run.appendAttempts >= MAX_APPEND_ATTEMPTS) {
			abandonRun(state.id, run, run.lastError || "append retry limit reached");
			return;
		}
		const messages = pendingAssistantMessages(ctx, run.messages, stores);
		const promptState = promptStates.get(state.id);
		if (!promptState && messages.length === 0) {
			pendingRuns.delete(state.id);
			finalizedRuns.add(state.id);
			clearRetry(state.id);
			await startQueuedRun(state.id);
			return;
		}
		const pending = pendingMessages.get(state.id);
		const root = rootState(ctx);
		if (!root) {
			run.rootAttempts = (run.rootAttempts || 0) + 1;
			if (run.rootAttempts >= MAX_APPEND_ATTEMPTS) {
				abandonRun(state.id, run, "session root unavailable");
			} else {
				scheduleRetry(state.id, ctx);
			}
			return;
		}
		const logFile = ensureLog(root, ctx.cwd);
		const messageStart = messages.map(message => timestampNumber(message?.timestamp)).find(value => value !== undefined);
		const start = promptState?.startedAt ?? messageStart ?? now();
		const end = messages.filter(message => message?.role === "assistant").map(messageTime).reduce((latest, value) => Math.max(latest, value), start);
		const { record, model, responseIdentity } = composeFinalizeRecord({ state, root, messages, ctx, start, end, previousModel: lastModels.get(root.id) });
		run.responseDedupeKey ||= stableKey("omp-response", state.id, responseIdentity || run.prompt || "");
		run.finalizing = true;
		run.appendAttempts = (run.appendAttempts || 0) + 1;
		try {
			const appended = await append(logFile, record, run.appendAttempts > 1, run.enableSignature, run.responseDedupeKey);
			if (!appended) {
				run.finalizing = false;
				discardDisabledSession(state.id);
				return;
			}
		} catch (error) {
			run.finalizing = false;
			run.lastError = error instanceof Error ? error.message : String(error);
			if (run.appendAttempts >= MAX_APPEND_ATTEMPTS) {
				abandonRun(state.id, run, run.lastError);
			} else {
				scheduleRetry(state.id, ctx);
			}
			return;
		}
		run.finalizing = false;
		clearRetry(state.id);
		pendingRuns.delete(state.id);
		finalizedRuns.add(state.id);
		const committed = committedMessages.get(state.id) || new Set();
		for (const message of messages) {
			const key = messageKey(message);
			if (key) committed.add(key);
		}
		committedMessages.set(state.id, committed);
		pending?.clear();
		if (model) lastModels.set(root.id, model);
		promptStates.delete(state.id);
		await startQueuedRun(state.id);
	};

	pi.on("agent_end", async (event, ctx) => {
		if (!isEnabled()) {
			const disabledState = sessionState(ctx);
			if (validId(disabledState.id)) discardDisabledSession(disabledState.id);
			return;
		}
		const currentSignature = enableSignature();
		const state = sessionState(ctx);
		if (sessionUnavailable(state)) return;
		const terminal = event.willContinue !== true;
		const queued = queuedRuns.get(state.id);
		let run = pendingRuns.get(state.id);
		if (run && run.enableSignature !== currentSignature) {
			discardDisabledSession(state.id);
			return;
		}
		if (queued?.prompts.some(prompt => prompt.enableSignature !== currentSignature)) {
			discardDisabledSession(state.id);
			return;
		}
		const runBusyOrDone = !run || run.finalizing || run.endSeen || finalizedRuns.has(state.id);
		if (queued?.prompts.length && runBusyOrDone) {
			queued.prompts[0].events.push({ type: "end", event, ctx, terminal, enableSignature: currentSignature });
			return;
		}
		if (finalizedRuns.has(state.id) && !run) return;
		if (run?.finalizing || run?.endSeen) return;
		run ||= createRun({ ctx, enableSignature: currentSignature, dedupeKey: "" });
		pendingRuns.set(state.id, run);
		run.ctx = ctx;
		run.messages.push(...(event.messages || []));
		if (terminal) run.endSeen = true;
		if (terminal) {
			run.settled = true;
			await finalize(ctx, currentSignature);
		}
	});

	pi.on("session_stop", async (event, ctx) => {
		if (!isEnabled()) {
			const disabledState = sessionState(ctx);
			if (validId(disabledState.id)) discardDisabledSession(disabledState.id);
			return;
		}
		const currentSignature = enableSignature();
		const state = sessionState(ctx);
		if (sessionUnavailable(state)) return;
		let run = pendingRuns.get(state.id);
		if (run && run.enableSignature !== currentSignature) {
			discardDisabledSession(state.id);
			return;
		}
		if (finalizedRuns.has(state.id) || run?.finalizing) return;
		run ||= createRun({ ctx, enableSignature: currentSignature, dedupeKey: "" });
		run.ctx = ctx;
		run.messages.push(...(event.messages || []));
		run.endSeen = true;
		run.settled = true;
		pendingRuns.set(state.id, run);
		await finalize(ctx, currentSignature);
	});

}

export { stableKey, projectSlug, contentText, messageKey, pathInside, finiteNumber, timestampNumber, nonNegativeInteger, usageLine, formatHms, createRun, queuedEventHandlers, composeFinalizeRecord, usageCommandArguments };
