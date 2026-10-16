#!/bin/bash
# Discard stdout from a failed BSD stat before trying GNU stat.
_claude_stat() {
  local value
  value=$(stat -f "$1" "$3" 2>/dev/null) || value=$(stat -c "$2" "$3" 2>/dev/null) || return 1
  printf '%s\n' "$value"
}
_CLAUDE_HOME="$(cd "$HOME" && pwd -P)"
# The package's lib/, whose pathsafe.py and process_identity.py the inline Python below imports.
_CLAUDE_SESSION_LOG_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../lib" && pwd -P)"
_CLAUDE_SESSION_MAP_DIR="$_CLAUDE_HOME/.claude/session-maps"
_claude_default_root_selected() {
  local config_dir="${CLAUDE_CONFIG_DIR:-}"
  [ -z "$config_dir" ] && return 0
  local config_real
  config_real=$(cd "$config_dir" 2>/dev/null && pwd -P) || return 1
  [ "$config_real" = "$_CLAUDE_HOME/.claude" ]
}
_claude_default_root_alias_safe() {
  local raw_root="$HOME/.claude" canonical_root="$_CLAUDE_HOME/.claude" raw_real
  [ ! -L "$HOME" ] || {
    raw_real=$(cd "$HOME" 2>/dev/null && pwd -P) || return 1
    [ "$raw_real" = "$_CLAUDE_HOME" ] || return 1
  }
  if [ -e "$raw_root" ] || [ -L "$raw_root" ]; then
    [ ! -L "$raw_root" ] || {
      raw_real=$(cd "$raw_root" 2>/dev/null && pwd -P) || return 1
      [ "$raw_real" = "$canonical_root" ] || return 1
    }
  fi
}
_claude_enable_signature() {
  local flag="$_CLAUDE_HOME/.claude/prompt-logs/.enabled" size token device inode
  [ -f "$flag" ] && [ ! -L "$flag" ] || return 1
  size=$(_claude_stat %z %s "$flag") || return 1
  case "$size" in ''|*[!0-9]*) return 1 ;; esac
  if [ "$size" -lt 4096 ]; then
    token=$(cat "$flag") || return 1
    if [ -n "$token" ]; then
      printf '%s' "$token"
      return 0
    fi
  fi
  device=$(_claude_stat %d %d "$flag") || return 1
  inode=$(_claude_stat %i %i "$flag") || return 1
  printf '%s:%s' "$device" "$inode"
}
_CLAUDE_EXPECTED_SIGNATURE=$(_claude_enable_signature 2>/dev/null || true)

_claude_display_path() {
  local file="$1"
  case "$file" in
    "$_CLAUDE_HOME"/*) printf '%s/%s\n' "$HOME" "${file#"$_CLAUDE_HOME/"}" ;;
    *) printf '%s\n' "$file" ;;
  esac
}



# Path mangling Claude uses for ~/.claude/projects/<key>/<session-id>.jsonl: every character but a letter or
# a digit becomes "-" (C:\Users\a.b -> C--Users-a-b).
resolve_project_key() {
  printf '%s\n' "$1" | sed 's|[^A-Za-z0-9]|-|g'
}
_claude_path_components_secure() {
  local current="$1"
  while :; do
    [ ! -L "$current" ] || return 1
    [ "$current" = "/" ] && break
    current=$(dirname "$current")
  done
}

_claude_session_map_path() {
  local session_id="$1" dir="$_CLAUDE_SESSION_MAP_DIR" map_file dir_uid dir_mode
  [[ "$session_id" =~ ^[A-Za-z0-9._-]+$ ]] || return 1
  _claude_default_root_alias_safe || return 1
  _claude_path_components_secure "$dir" || return 1
  if [ -e "$dir" ] || [ -L "$dir" ]; then
    [ -d "$dir" ] && [ ! -L "$dir" ] || return 1
    dir_uid=$(id -u 2>/dev/null) || return 1
    [ "$(_claude_stat %u %u "$dir")" = "$dir_uid" ] || return 1
    dir_mode=$(_claude_stat %Lp %a "$dir") || return 1
    case "$dir_mode" in ''|*[!0-7]*) return 1 ;; esac
    [ "$((8#$dir_mode & 0077))" -eq 0 ] || chmod 700 "$dir" || return 1
  fi
  map_file="$dir/$session_id"
  [ ! -L "$map_file" ] || return 1
  printf '%s\n' "$map_file"
}
_claude_read_session_file() {
  local session_id="$1" map_file session_file safe_file map_links map_uid map_mode
  map_file=$(_claude_session_map_path "$session_id") || return 1
  [ -f "$map_file" ] && [ ! -L "$map_file" ] || return 1
  map_uid=$(id -u 2>/dev/null) || return 1
  [ "$(_claude_stat %u %u "$map_file")" = "$map_uid" ] || return 1
  map_mode=$(_claude_stat %Lp %a "$map_file") || return 1
  case "$map_mode" in ''|*[!0-7]*) return 1 ;; esac
  [ "$((8#$map_mode & 0077))" -eq 0 ] || chmod 600 "$map_file" || return 1
  map_links=$(_claude_stat %l %h "$map_file") || return 1
  [ "$map_links" = "1" ] || return 1
  IFS= read -r session_file < "$map_file" || return 1
  safe_file=$(_claude_log_file_is_safe "$session_file") || return 1
  if [ -s "$safe_file" ]; then
    if [[ "$(sed -n '1p' "$safe_file")" == '# Prompts — '* ]]; then
      [ "$(sed -n '3p' "$safe_file")" = "**Session ID:** $session_id" ] || return 1
    fi
  fi
  printf '%s\n' "$safe_file"
}

_claude_log_file_is_safe() {
  local file="$1" raw_root="$HOME/.claude/prompt-logs" root="$_CLAUDE_HOME/.claude/prompt-logs"
  local relative component current canonical_file current_uid file_uid file_links file_mode
  _claude_default_root_alias_safe || return 1
  case "$file" in
    "$root"/*) canonical_file="$file" ;;
    "$raw_root"/*) canonical_file="$root/${file#"$raw_root"/}" ;;
    *) return 1 ;;
  esac
  _claude_path_components_secure "$root" || return 1
  relative="${canonical_file#"$root"/}"
  [ -n "$relative" ] || return 1
  current="$root"
  IFS='/' read -r -a components <<< "$relative"
  for component in "${components[@]}"; do
    case "$component" in ''|.|..) return 1 ;; esac
    current="$current/$component"
    [ ! -L "$current" ] || return 1
    if [ "$current" = "$canonical_file" ]; then
      [ -f "$current" ] || return 1
      current_uid=$(id -u 2>/dev/null) || return 1
      file_uid=$(_claude_stat %u %u "$current") || return 1
      [ "$file_uid" = "$current_uid" ] || return 1
      file_links=$(_claude_stat %l %h "$current") || return 1
      [ "$file_links" = "1" ] || return 1
      file_mode=$(_claude_stat %Lp %a "$current") || return 1
      case "$file_mode" in ''|*[!0-7]*) return 1 ;; esac
      if [ "$((8#$file_mode & 0077))" -ne 0 ]; then
        chmod 600 "$current" || return 1
      fi
    else
      [ -d "$current" ] || return 1
    fi
  done
  printf '%s\n' "$canonical_file"
}
# Claude Code on Windows reports paths as C:\...; Git Bash names them /c/..., as cygpath converts.
_claude_shell_path() {
  case "$1" in
    [A-Za-z]:[\\/]*) if command -v cygpath >/dev/null 2>&1; then cygpath -u "$1"; return; fi ;;
  esac
  printf '%s\n' "$1"
}
# Shell path $1 as Claude Code names it: on Windows its long C:\... form.
_claude_harness_path() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -wl "$1"; else printf '%s\n' "$1"; fi
}
_claude_transcript_path_is_safe() {
  local file raw_root="$HOME/.claude/projects" root="$_CLAUDE_HOME/.claude/projects"
  local relative component current canonical_file
  file=$(_claude_shell_path "$1") || return 1
  _claude_default_root_alias_safe || return 1
  case "$file" in
    "$root"/*) canonical_file="$file" ;;
    "$raw_root"/*) canonical_file="$root/${file#"$raw_root"/}" ;;
    *) return 1 ;;
  esac
  _claude_path_components_secure "$root" || return 1
  relative="${canonical_file#"$root"/}"
  [ -n "$relative" ] || return 1
  current="$root"
  IFS='/' read -r -a components <<< "$relative"
  for component in "${components[@]}"; do
    case "$component" in ''|.|..) return 1 ;; esac
    current="$current/$component"
    [ ! -L "$current" ] || return 1
    if [ "$current" != "$canonical_file" ] && [ -e "$current" ] && [ ! -d "$current" ]; then
      return 1
    fi
  done
  printf '%s\n' "$canonical_file"
}
_claude_transcript_file_is_safe() {
  local file="$1" canonical current_uid file_uid file_links file_mode
  canonical=$(_claude_transcript_path_is_safe "$file") || return 1
  [ -f "$canonical" ] && [ ! -L "$canonical" ] || return 1
  current_uid=$(id -u 2>/dev/null) || return 1
  file_uid=$(_claude_stat %u %u "$canonical") || return 1
  [ "$file_uid" = "$current_uid" ] || return 1
  file_links=$(_claude_stat %l %h "$canonical") || return 1
  [ "$file_links" = "1" ] || return 1
  file_mode=$(_claude_stat %Lp %a "$canonical") || return 1
  case "$file_mode" in ''|*[!0-7]*) return 1 ;; esac
  if [ "$((8#$file_mode & 0077))" -ne 0 ]; then
    chmod 600 "$canonical" || return 1
  fi
  printf '%s\n' "$canonical"
}
_claude_secure_append() {
  local file="$1" record="$2" kind="${3:-record}" expected_signature="${4:-${_CLAUDE_EXPECTED_SIGNATURE:-}}" expected_dev="" expected_ino=""
  if [ -e "$file" ] || [ -L "$file" ]; then
    [ ! -L "$file" ] || return 1
    expected_dev=$(_claude_stat %d %d "$file") || return 1
    expected_ino=$(_claude_stat %i %i "$file") || return 1
  fi
  if [ "$kind" = "state" ]; then
    printf '%s\n' "$record"
  else
    printf '%s\n\n' "$record"
  fi | SESSION_LOG_ENABLE_LOCK="$_CLAUDE_HOME/.claude/prompt-logs/.enabled.lock" SESSION_LOG_ENABLE_FLAG="$_CLAUDE_HOME/.claude/prompt-logs/.enabled" SESSION_LOG_ENABLE_SIGNATURE="$expected_signature" SESSION_LOG_EXPECTED_DEV="$expected_dev" SESSION_LOG_EXPECTED_INO="$expected_ino" SESSION_LOG_LIB="$_CLAUDE_SESSION_LOG_LIB" python3 -I -c '
import json
import os
import stat
import sys
import time

if not os.path.isabs(os.environ["SESSION_LOG_LIB"]):
    raise SystemExit("session-log: package lib directory not found")  # "" would import from the cwd
sys.path.insert(0, os.environ["SESSION_LOG_LIB"])
import pathsafe
import process_identity

file = sys.argv[1]
payload = sys.stdin.buffer.read()
directory, name = os.path.split(file)
file_flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
enable_lock_path = os.environ["SESSION_LOG_ENABLE_LOCK"]
enable_flag_path = os.environ["SESSION_LOG_ENABLE_FLAG"]
expected_enable_signature = os.environ.get("SESSION_LOG_ENABLE_SIGNATURE", "")
expected_dev = os.environ.get("SESSION_LOG_EXPECTED_DEV", "")
expected_ino = os.environ.get("SESSION_LOG_EXPECTED_INO", "")
def is_expected_file(info, device, inode):
    # Git Bash numbers a volume unlike Windows Python does; there the inode (the file index) alone tells.
    return str(info.st_ino) == inode and (sys.platform == "win32" or str(info.st_dev) == device)

def owner_alive(lock_fd):
    try:
        descriptor = pathsafe.open_at(lock_fd, ".owner", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            raw = os.read(descriptor, 256)
        finally:
            os.close(descriptor)
        owner = json.loads(raw.decode("utf-8"))
        pid = int(owner.get("pid", 0))
        if pid <= 0:
            return None
        expected_start = owner.get("start")
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if isinstance(expected_start, str) and expected_start:
        actual_start = process_identity.start_time(pid)
        if actual_start is None:
            return None
        return actual_start == expected_start
    if process_identity.WINDOWS:
        return None  # os.kill(pid, 0) sends a CTRL_C_EVENT there instead of probing
    try:
        os.kill(pid, 0)
    except OSError as error:
        return getattr(error, "errno", None) != 3
    return True

enable_lock_fd = os.open(
    enable_lock_path,
    os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
    0o600,
)
enable_lock_stat = os.fstat(enable_lock_fd)
if (
    not stat.S_ISREG(enable_lock_stat.st_mode)
    or enable_lock_stat.st_nlink != 1
    or not pathsafe.is_owned_by_user(enable_lock_stat)
):
    raise RuntimeError("unsafe Claude enable lock")
pathsafe.set_file_mode(enable_lock_fd, 0o600)
pathsafe.lock_file(enable_lock_fd)

parent_fd = pathsafe.open_directory(directory, create=True)
lock_name = name + ".lock"
lock_fd = None
enable_flag_fd = None
descriptor = None
token = "%s-%s" % (os.getpid(), time.monotonic_ns())
deadline = time.monotonic() + 5
try:
    try:
        enable_flag_stat = os.lstat(enable_flag_path)
    except FileNotFoundError:
        raise SystemExit(0)
    if (
        stat.S_ISLNK(enable_flag_stat.st_mode)
        or not stat.S_ISREG(enable_flag_stat.st_mode)
        or not pathsafe.is_owned_by_user(enable_flag_stat)
    ):
        raise RuntimeError("unsafe Claude enable flag")
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
        or not stat.S_ISREG(current_flag_stat.st_mode)
        or current_flag_stat.st_nlink != 1
        or not pathsafe.is_owned_by_user(current_flag_stat)
    ):
        raise SystemExit(0)
    flag_bytes = bytearray()
    while len(flag_bytes) < 4096:
        chunk = os.read(enable_flag_fd, 4096 - len(flag_bytes))
        if not chunk:
            break
        flag_bytes.extend(chunk)
    if len(flag_bytes) >= 4096:
        flag_token = ""
    else:
        try:
            flag_token = bytes(flag_bytes).decode("utf-8").rstrip("\n")
        except UnicodeDecodeError:
            raise SystemExit(0)
    # Without a token the signature is the flag file identity, "<device>:<inode>".
    signature_device, _, signature_inode = expected_enable_signature.rpartition(":")
    if expected_enable_signature and (
        flag_token != expected_enable_signature
        if flag_token
        else not is_expected_file(current_flag_stat, signature_device, signature_inode)
    ):
        raise SystemExit(0)
    while True:
        try:
            pathsafe.mkdir_at(parent_fd, lock_name, 0o700)
            lock_fd = pathsafe.open_child_directory(parent_fd, lock_name)
            owner = pathsafe.open_at(lock_fd, ".owner", os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                owner_data = {"pid": os.getpid(), "token": token}
                owner_start = process_identity.start_time(os.getpid())
                if owner_start:
                    owner_data["start"] = owner_start
                data = json.dumps(owner_data).encode("utf-8")
                os.write(owner, data)
                os.fsync(owner)
            finally:
                os.close(owner)
            break
        except FileExistsError:
            if lock_fd is not None:
                pathsafe.close_directory(lock_fd)
                lock_fd = None
            try:
                lock_fd = pathsafe.open_child_directory(parent_fd, lock_name)
                state = owner_alive(lock_fd)
                stale_invalid = state is not True and time.time() - pathsafe.stat_directory(lock_fd).st_mtime >= 5
                if state is True or not stale_invalid:
                    pathsafe.close_directory(lock_fd)
                    lock_fd = None
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Claude append lock timed out")
                    time.sleep(0.01)
                    continue
                try:
                    pathsafe.unlink_at(lock_fd, ".owner")
                except FileNotFoundError:
                    pass
                pathsafe.close_directory(lock_fd)
                lock_fd = None
                pathsafe.rmdir_at(parent_fd, lock_name)
            except (FileNotFoundError, NotADirectoryError):
                if lock_fd is not None:
                    pathsafe.close_directory(lock_fd)
                    lock_fd = None
            if time.monotonic() >= deadline:
                raise TimeoutError("Claude append lock timed out")
    if expected_dev and expected_ino:
        path_stat = pathsafe.stat_at(parent_fd, name)
        if (
            stat.S_ISLNK(path_stat.st_mode)
            or not stat.S_ISREG(path_stat.st_mode)
            or not is_expected_file(path_stat, expected_dev, expected_ino)
        ):
            raise RuntimeError("Claude log file changed during append")
    descriptor = pathsafe.open_at(parent_fd, name, file_flags, 0o600)
    current = os.fstat(descriptor)
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_nlink != 1
        or not pathsafe.is_owned_by_user(current)
        or not pathsafe.has_private_mode(current)
        or (expected_dev and expected_ino and not is_expected_file(current, expected_dev, expected_ino))
    ):
        raise RuntimeError("unsafe Claude append file")
    before = current.st_size
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
finally:
    if enable_flag_fd is not None:
        os.close(enable_flag_fd)
    if descriptor is not None:
        os.close(descriptor)
    if lock_fd is not None:
        try:
            owner = pathsafe.open_at(lock_fd, ".owner", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                data = os.read(owner, 256)
            finally:
                os.close(owner)
            if json.loads(data.decode("utf-8")).get("token") == token:
                pathsafe.unlink_at(lock_fd, ".owner")
                pathsafe.close_directory(lock_fd)
                lock_fd = None
                pathsafe.rmdir_at(parent_fd, lock_name)
        except (FileNotFoundError, NotADirectoryError, json.JSONDecodeError, OSError):
            pass
        if lock_fd is not None:
            pathsafe.close_directory(lock_fd)
    pathsafe.close_directory(parent_fd)
    # Closing the enable lock releases it.
    os.close(enable_lock_fd)
' "$file"
}
_claude_append_record() {
  local file="$1" record="$2" safe_file
  safe_file=$(_claude_log_file_is_safe "$file") || return 1
  _claude_secure_append "$safe_file" "$record"
}
_claude_append_state_record() {
  local file="$1" record="$2"
  _claude_path_components_secure "$(dirname "$file")" || return 1
  [ ! -L "$file" ] || return 1
  if [ -e "$file" ] && [ ! -f "$file" ]; then return 1; fi
  _claude_secure_append "$file" "$record" state
}
_claude_safe_snapshot() {
  SESSION_LOG_LIB="$_CLAUDE_SESSION_LOG_LIB" python3 -I -c '
import os
import stat
import sys
import tempfile

if not os.path.isabs(os.environ["SESSION_LOG_LIB"]):
    raise SystemExit("session-log: package lib directory not found")  # "" would import from the cwd
sys.path.insert(0, os.environ["SESSION_LOG_LIB"])
import pathsafe

source = sys.argv[1]
directory, name = os.path.split(source)
descriptor_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
parent_fd = pathsafe.open_directory(directory, create=False)
descriptor = None
temporary = None
try:
    descriptor = pathsafe.open_at(parent_fd, name, descriptor_flags)
    current = os.fstat(descriptor)
    if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1 or not pathsafe.is_owned_by_user(current)
            or not pathsafe.has_private_mode(current)):
        raise RuntimeError("unsafe Claude transcript")
    data = bytearray()
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        data.extend(chunk)
    temporary_fd, temporary = tempfile.mkstemp(prefix="session-log-transcript-")
    pathsafe.set_file_mode(temporary_fd, 0o600)
    os.write(temporary_fd, data)
    os.fsync(temporary_fd)
    os.close(temporary_fd)
    temporary_fd = None
    print(temporary)
except BaseException:
    if temporary is not None:
        try:
            os.unlink(temporary)
        except OSError:
            pass
    raise
finally:
    if descriptor is not None:
        os.close(descriptor)
    pathsafe.close_directory(parent_fd)
' "$1"
}
_claude_atomic_state_write() {
  local file="$1" value="$2" dir temp
  dir=$(dirname "$file")
  _claude_path_components_secure "$dir" || return 1
  [ ! -L "$file" ] || return 1
  if [ -e "$file" ] && [ ! -f "$file" ]; then return 1; fi
  temp="$file.tmp.$$.$RANDOM"
  [ ! -e "$temp" ] && [ ! -L "$temp" ] || return 1
  if ! (set -C; umask 077; printf '%s\n' "$value" > "$temp"); then
    [ ! -L "$temp" ] && unlink "$temp" 2>/dev/null || true
    return 1
  fi
  chmod 600 "$temp" || { unlink "$temp" 2>/dev/null || true; return 1; }
  [ ! -L "$file" ] || { unlink "$temp" 2>/dev/null || true; return 1; }
  mv -f "$temp" "$file"
}


_claude_state_file_path() {
  local session_id="$1" suffix="$2" map_file links uid mode
  case "$suffix" in .pstart|.last|.helpers) ;; *) return 1 ;; esac
  map_file=$(_claude_session_map_path "$session_id") || return 1
  local state_file="${map_file}${suffix}"
  _claude_path_components_secure "$_CLAUDE_SESSION_MAP_DIR" || return 1
  [ ! -L "$state_file" ] || return 1
  if [ -e "$state_file" ]; then
    [ -f "$state_file" ] || return 1
    links=$(_claude_stat %l %h "$state_file") || return 1
    [ "$links" = "1" ] || return 1
    uid=$(id -u 2>/dev/null) || return 1
    [ "$(_claude_stat %u %u "$state_file")" = "$uid" ] || return 1
    mode=$(_claude_stat %Lp %a "$state_file") || return 1
    case "$mode" in ''|*[!0-7]*) return 1 ;; esac
    [ "$((8#$mode & 0077))" -eq 0 ] || chmod 600 "$state_file" || return 1
  fi
  printf '%s\n' "$state_file"
}

_claude_private_dir() {
  local dir="$1"
  _claude_path_components_secure "$dir" || return 1
  [ -d "$dir" ] || return 1
  [ ! -L "$dir" ]
}


# Seconds -> HH:MM:SS. Anything that is not a plain number counts as zero, so a
# corrupt state file can never abort a hook mid-append.
fmt_hms() {
  local s="${1:-0}"
  case "$s" in ''|*[!0-9]*) s=0 ;; esac
  printf '%02d:%02d:%02d\n' "$((s / 3600))" "$((s % 3600 / 60))" "$((s % 60))"
}

# Prints the "switched:" line when the model or the effort changed since the
# previous turn, nothing otherwise. Each half needs both sides to be known: a
# .last written before the effort was known holds "model ", and "effort  → high"
# is not a transition. With neither side known there is no line at all.
switch_line() {
  local prev_model="$1" prev_effort="$2" model="$3" effort="$4" parts=""
  if [ -n "$prev_model" ] && [ -n "$model" ] && [ "$prev_model" != "$model" ]; then
    parts="model $prev_model → $model"
  fi
  if [ -n "$prev_effort" ] && [ -n "$effort" ] && [ "$prev_effort" != "$effort" ]; then
    if [ -n "$parts" ]; then parts="$parts, "; fi
    parts="${parts}effort $prev_effort → $effort"
  fi
  if [ -n "$parts" ]; then printf 'switched: %s\n' "$parts"; fi
}

create_session_file() {
  local session_id="$1"
  local cwd="$2"
  [[ "$session_id" =~ ^[A-Za-z0-9._-]+$ ]] || return 0
  local old_umask; old_umask=$(umask); umask 077
  local project_slug slug_cwd="$cwd"
  # A Windows cwd (C:\a\b) uses backslashes, and its drive colon is no file name character there.
  case "$cwd" in [A-Za-z]:[\\/]*) slug_cwd=$(printf '%s' "$cwd" | tr '\\:' '/-') ;; esac
  # Include parent dir to avoid collisions between same-named projects
  project_slug=$(echo "$slug_cwd" | sed 's|.*/\([^/]*/[^/]*\)$|\1|' | tr '/' '-')
  local prompts_root="$_CLAUDE_HOME/.claude/prompt-logs"
  local prompts_dir="$prompts_root/$project_slug"
  _claude_default_root_alias_safe || { umask "$old_umask"; return 1; }
  [ ! -L "$_CLAUDE_HOME/.claude/prompt-logs" ] || { umask "$old_umask"; return 1; }
  _claude_path_components_secure "$_CLAUDE_HOME/.claude" || { umask "$old_umask"; return 1; }
  _claude_path_components_secure "$prompts_root" || { umask "$old_umask"; return 1; }
  _claude_path_components_secure "$prompts_dir" || { umask "$old_umask"; return 1; }
  mkdir -p "$prompts_dir"
  _claude_private_dir "$prompts_root" || { umask "$old_umask"; return 1; }
  _claude_private_dir "$prompts_dir" || { umask "$old_umask"; return 1; }
  chmod 700 "$prompts_dir"
  chmod 700 "$prompts_root"
  local timestamp
  timestamp=$(date '+%Y-%m-%d_%H-%M-%S')
  local session_file="$prompts_dir/session_${timestamp}_${session_id}_${BASHPID:-$$}.md"
  [ ! -L "$_CLAUDE_HOME/.claude/session-maps" ] || { umask "$old_umask"; return 1; }
  _claude_path_components_secure "$_CLAUDE_SESSION_MAP_DIR" || { umask "$old_umask"; return 1; }
  mkdir -p "$_CLAUDE_SESSION_MAP_DIR"
  # Store session map in a private directory, not world-writable /tmp
  local map_file
  map_file=$(_claude_session_map_path "$session_id") || { umask "$old_umask"; return 1; }
  if [ -e "$map_file" ] || [ -L "$map_file" ]; then
    [ ! -L "$map_file" ] || { umask "$old_umask"; return 1; }
    [ -f "$map_file" ] || { umask "$old_umask"; return 1; }
    local map_links
    map_links=$(_claude_stat %l %h "$map_file") || { umask "$old_umask"; return 1; }
    [ "$map_links" = "1" ] || { umask "$old_umask"; return 1; }
    umask "$old_umask"
    return 0
  fi

  # Derive path to Claude's session JSONL file
  local project_key
  project_key=$(resolve_project_key "$cwd")
  local session_jsonl="$_CLAUDE_HOME/.claude/projects/${project_key}/${session_id}.jsonl"

  (
    set -C
    {
      printf '# Prompts — %s\n\n' "$(date '+%Y-%m-%d %H:%M:%S')"
      printf '**Session ID:** %s\n' "$session_id"
      # shellcheck disable=SC2016  # the backticks are Markdown code formatting, not command substitution
      printf '**Session file:** `%s`\n\n' "$session_jsonl"
      printf '%s\n\n' '---'
    } > "$session_file"
  ) || { umask "$old_umask"; return 1; }
  if ! (
    set -C
    printf '%s\n' "$session_file" > "$map_file"
  ); then
    if [ -f "$map_file" ] && [ ! -L "$map_file" ]; then
      [ "$(_claude_stat %l %h "$map_file")" = "1" ] || { umask "$old_umask"; return 1; }
      [ ! -L "$session_file" ] && unlink "$session_file" 2>/dev/null || true
      umask "$old_umask"
      return 0
    fi
    [ ! -L "$session_file" ] && unlink "$session_file" 2>/dev/null || true
    umask "$old_umask"
    return 1
  fi
  umask "$old_umask"
}
