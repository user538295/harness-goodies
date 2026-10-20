"""Background journal for the LLM Wiki raw/ folder.

Opt-in. The watcher only *observes*: every poll it records which files
appeared or changed in raw/ into watcher.log, and heartbeats a PID file so the
skill can report liveness. It never decides ingest and holds no ingest state —
catalog.py is the single authority for what needs ingesting (new/changed), and
the skill computes that on demand. The loop only stats files (no hashing), so
it stays cheap on large corpora.
"""

import argparse
import os
import signal
import stat
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from pid_file import PIDFile

MAX_LOG_LINES = 10000
RETAINED_LOG_LINES = 4999


class WatcherLog:
    def __init__(self, watcher_dir: Path) -> None:
        self._path = watcher_dir / "watcher.log"

    def write(self, msg: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        with open(self._path, "a") as f:
            f.write(f"[{ts}] {msg}\n")

    def rotate_if_needed(self) -> None:
        try:
            lines = self._path.read_text().splitlines(keepends=True)
        except OSError:
            return
        if len(lines) <= MAX_LOG_LINES:
            return
        n = len(lines)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        rotation_line = f"[{ts}] rotated: kept last {RETAINED_LOG_LINES + 1} of {n} lines\n"
        tail = lines[-RETAINED_LOG_LINES:]
        output = [rotation_line] + tail
        tmp = self._path.with_suffix(".log.tmp")
        tmp.write_text("".join(output))
        tmp.replace(self._path)


_POLL_INTERVAL = 30


def _snapshot(raw_dir: Path, log: WatcherLog) -> dict[str, tuple[float, int]]:
    """Cheap stat-only view of raw/: path -> (mtime, size). Missing raw_dir → empty."""
    snap: dict[str, tuple[float, int]] = {}
    ancestors: set[tuple[int, int]] = set()

    def record(entry) -> None:
        try:
            entry_stat = entry.stat(follow_symlinks=True)
        except OSError as exc:
            log.write(f"stat failed {entry.path}: {exc}")
            return
        if stat.S_ISDIR(entry_stat.st_mode):
            visit(entry.path)
        else:
            snap[entry.path] = (entry_stat.st_mtime, entry_stat.st_size)

    def visit(directory: Path | str) -> None:
        try:
            directory_stat = os.stat(directory)
        except OSError as exc:
            log.write(f"stat failed {directory}: {exc}")
            return

        identity = (directory_stat.st_dev, directory_stat.st_ino)
        if identity in ancestors:
            return

        ancestors.add(identity)
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    record(entry)
        except OSError as exc:
            log.write(f"scan failed {directory}: {exc}")
        finally:
            ancestors.remove(identity)

    # Follow directory symlinks, but do not revisit a directory on the current path.
    visit(raw_dir)
    return snap


def main() -> None:
    stop_event = threading.Event()

    def _sigterm_handler(signum, frame) -> None:
        stop_event.set()

    parser = argparse.ArgumentParser(prog="watcher")
    subparsers = parser.add_subparsers(dest="command")
    start_parser = subparsers.add_parser("start")
    start_parser.add_argument("project_root", type=Path)
    args = parser.parse_args()

    project_root: Path = args.project_root.resolve()
    raw_dir = project_root / "llm-wiki" / "raw"
    if not raw_dir.exists():
        print(f"error: {raw_dir} does not exist", file=sys.stderr)
        sys.exit(1)

    watcher_dir = project_root / "llm-wiki" / ".watcher"
    watcher_dir.mkdir(parents=True, exist_ok=True)

    nonce = os.urandom(4).hex()

    log = WatcherLog(watcher_dir)
    log.rotate_if_needed()
    pid_file = PIDFile(watcher_dir)

    pid = os.getpid()
    pid_file.write(pid, nonce)

    signal.signal(signal.SIGTERM, _sigterm_handler)

    log.write(f"watcher started pid={pid} nonce={nonce} project={project_root}")

    prev: dict[str, tuple[float, int]] = {}
    while not stop_event.is_set():
        cur = _snapshot(raw_dir, log)
        for path in sorted(set(cur) - set(prev)):
            log.write(f"appeared {path}")
        for path in sorted(k for k in cur if k in prev and cur[k] != prev[k]):
            log.write(f"modified {path}")
        prev = cur
        pid_file.update_heartbeat(pid, nonce)
        stop_event.wait(timeout=_POLL_INTERVAL)

    log.write("watcher stopped")


if __name__ == "__main__":
    main()
