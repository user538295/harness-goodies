import signal as sig_mod
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from pid_file import PIDFile
from watcher import RETAINED_LOG_LINES, WatcherLog, _snapshot, main

_READY_TIMEOUT = 5.0
_POLL_INTERVAL = 0.01


def _wait_until(predicate, timeout=_READY_TIMEOUT, interval=_POLL_INTERVAL) -> bool:
    """Poll predicate() until it is truthy or the timeout elapses; return its
    final value. Deterministic poll-with-timeout on an observable condition, so
    the caller asserts on the condition itself instead of a fixed sleep."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


# --- _snapshot tests ---

def test_snapshot_lists_files_with_mtime_size(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    f = raw / "file.md"
    f.write_text("hello")
    snap = _snapshot(raw, WatcherLog(tmp_path))
    stat = f.stat()
    assert snap == {str(f): (stat.st_mtime, stat.st_size)}


def test_snapshot_follows_symlinked_file(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    (raw / "link.txt").symlink_to(outside)
    snap = _snapshot(raw, WatcherLog(tmp_path))
    assert str(raw / "link.txt") in snap


def test_snapshot_follows_symlinked_directory(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text("a")
    (raw / "linked").symlink_to(corpus)
    snap = _snapshot(raw, WatcherLog(tmp_path))
    assert str(raw / "linked" / "a.md") in snap


def test_snapshot_skips_broken_symlink(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "broken.md").symlink_to(tmp_path / "nonexistent.md")
    snap = _snapshot(raw, WatcherLog(tmp_path))
    assert snap == {}


def test_snapshot_nonexistent_dir(tmp_path):
    assert _snapshot(tmp_path / "does_not_exist", WatcherLog(tmp_path)) == {}


def test_snapshot_empty_dir(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    assert _snapshot(raw, WatcherLog(tmp_path)) == {}


def test_snapshot_nested_files(tmp_path):
    raw = tmp_path / "raw"
    (raw / "sub").mkdir(parents=True)
    (raw / "sub" / "a.md").write_text("a")
    (raw / "b.md").write_text("b")
    snap = _snapshot(raw, WatcherLog(tmp_path))
    assert set(snap) == {str(raw / "sub" / "a.md"), str(raw / "b.md")}


# --- PIDFile tests ---

def test_pidfile_write_then_read(tmp_path):
    pf = PIDFile(tmp_path)
    pf.write(12345, "abcd1234")
    result = pf.read()
    assert result is not None
    pid, nonce, heartbeat_dt = result
    assert pid == 12345
    assert nonce == "abcd1234"
    assert heartbeat_dt.tzinfo is not None


def test_pidfile_missing_returns_none(tmp_path):
    pf = PIDFile(tmp_path)
    assert pf.read() is None


def test_pidfile_malformed_returns_none(tmp_path):
    pid_file = tmp_path / "watcher.pid"
    pid_file.write_text("not-valid-content\n")
    pf = PIDFile(tmp_path)
    assert pf.read() is None


def test_pidfile_update_heartbeat_preserves_pid_nonce(tmp_path):
    pf = PIDFile(tmp_path)
    pf.write(99, "nonce99")
    pf.update_heartbeat(99, "nonce99")
    result = pf.read()
    assert result is not None
    pid, nonce, _ = result
    assert pid == 99
    assert nonce == "nonce99"
    assert not (tmp_path / "watcher.pid.tmp").exists()


def test_pidfile_one_line_only_returns_none(tmp_path):
    pid_file = tmp_path / "watcher.pid"
    pid_file.write_text("42:mynonce\n")
    pf = PIDFile(tmp_path)
    assert pf.read() is None


# --- WatcherLog tests ---

def test_log_write_creates_file(tmp_path):
    wl = WatcherLog(tmp_path)
    wl.write("hello")
    assert (tmp_path / "watcher.log").exists()


def test_log_write_includes_timestamp_and_message(tmp_path):
    import re
    wl = WatcherLog(tmp_path)
    wl.write("test message")
    content = (tmp_path / "watcher.log").read_text()
    pattern = r"^\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00\] test message\n$"
    assert re.match(pattern, content), f"Line did not match pattern: {content!r}"


def test_log_rotate_noop_under_threshold(tmp_path):
    wl = WatcherLog(tmp_path)
    log_file = tmp_path / "watcher.log"
    log_file.write_text("".join(f"line {i}\n" for i in range(9000)))
    wl.rotate_if_needed()
    lines = log_file.read_text().splitlines()
    assert len(lines) == 9000


def test_log_rotate_exactly_at_threshold(tmp_path):
    wl = WatcherLog(tmp_path)
    log_file = tmp_path / "watcher.log"
    log_file.write_text("".join(f"line {i}\n" for i in range(10001)))
    wl.rotate_if_needed()
    lines = log_file.read_text().splitlines()
    assert len(lines) == 5000


def test_log_rotate_cuts_at_line_boundary(tmp_path):
    wl = WatcherLog(tmp_path)
    log_file = tmp_path / "watcher.log"
    original = [f"line {i}\n" for i in range(10001)]
    log_file.write_text("".join(original))
    wl.rotate_if_needed()
    lines = log_file.read_text().splitlines(keepends=True)
    # After the rotation marker, the file must hold exactly the last retained
    # original lines, each whole: a cut mid-line would leave a partial first line.
    assert lines[1:] == original[-RETAINED_LOG_LINES:]


def test_log_rotate_missing_file_noop(tmp_path):
    wl = WatcherLog(tmp_path)
    log_file = tmp_path / "watcher.log"
    wl.rotate_if_needed()
    assert not log_file.exists()


def test_log_rotate_exactly_at_boundary_no_rotation(tmp_path):
    wl = WatcherLog(tmp_path)
    log_file = tmp_path / "watcher.log"
    log_file.write_text("".join(f"line {i}\n" for i in range(10000)))
    wl.rotate_if_needed()
    lines = log_file.read_text().splitlines()
    assert len(lines) == 10000


# --- main() unit tests ---

def test_main_missing_llm_wiki_raw_exits_1(tmp_path):
    with patch("sys.argv", ["watcher.py", "start", str(tmp_path)]):
        with pytest.raises(SystemExit) as exc_info:
            main()
    assert exc_info.value.code == 1


# --- Integration test ---

def test_watcher_process_journals_and_exits_cleanly(tmp_path):
    project_root = tmp_path
    raw_dir = project_root / "llm-wiki" / "raw"
    raw_dir.mkdir(parents=True)
    (raw_dir / "note.md").write_text("hello")
    (raw_dir / "broken.md").symlink_to(project_root / "missing.md")
    corpus = project_root / "corpus"
    corpus.mkdir()
    (corpus / "corpus.md").write_text("external")
    (raw_dir / "external").symlink_to(corpus)
    (corpus / "loop").symlink_to(raw_dir)

    watcher_py = Path(__file__).parent.parent / "watcher.py"
    proc = subprocess.Popen(
        ["python3", str(watcher_py), "start", str(project_root)],
        cwd=str(watcher_py.parent),
    )
    watcher_dir = project_root / "llm-wiki" / ".watcher"
    log_file = watcher_dir / "watcher.log"
    pid_file = watcher_dir / "watcher.pid"

    try:
        def watcher_ready() -> bool:
            if proc.poll() is not None:
                return False
            if not (pid_file.exists() and log_file.exists()):
                return False
            log_text = log_file.read_text()
            return "watcher started" in log_text and "appeared" in log_text

        assert _wait_until(watcher_ready), (
            "watcher did not become ready before the deadline"
        )
        proc.send_signal(sig_mod.SIGTERM)
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    assert proc.returncode == 0
    assert pid_file.exists()
    assert log_file.exists()
    log_text = log_file.read_text()
    assert "watcher stopped" in log_text
    assert "appeared" in log_text  # the pre-existing note.md must be journaled
    assert "stat failed" in log_text
    assert "broken.md" in log_text
    assert f"appeared {raw_dir / 'external' / 'corpus.md'}" in log_text
    assert f"appeared {corpus / 'loop' / 'note.md'}" not in log_text
