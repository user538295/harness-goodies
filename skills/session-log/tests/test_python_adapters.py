import importlib.util
import fcntl
import io
import json
import os
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[3]
USAGE_PATH = REPO / "skills/session-log/adapters/codex/session_log_usage.py"
INSTALL_PATH = REPO / "skills/session-log/adapters/native/install_hooks.py"
HOOK_PATH = REPO / "skills/session-log/adapters/native/session_log_hook.py"
CURSOR_USAGE_PATH = REPO / "skills/session-log/adapters/cursor/session_log_usage.py"
CLAUDE_SETTINGS_PATH = REPO / "skills/session-log/lib/claude_settings.py"
LOCKING_PATH = REPO / "skills/session-log/lib/locking.py"

EMPTY_TOTALS = {
    "input_tokens": 0,
    "cached_input_tokens": 0,
    "output_tokens": 0,
    "reasoning_output_tokens": 0,
    "total_tokens": 0,
}


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def command_hook(command):
    """A settings.json command hook item."""
    return {"type": "command", "command": command}


def _is_temp_swap_target(source_name, target_name, target_basename):
    return (
        target_name == target_basename
        and source_name.startswith(".")
        and not source_name.startswith(".session-log-backup.")
    )


class CodexUsageTests(unittest.TestCase):
    def test_codex_usage_import_has_no_io_side_effects(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"HOME": directory}), \
                patch("sys.stdout", new_callable=io.StringIO) as stdout, \
                patch("sys.stderr", new_callable=io.StringIO) as stderr:
            load_module(USAGE_PATH, "session_log_usage_under_test")
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_codex_usage_totals_are_per_invocation(self):
        usage = load_module(USAGE_PATH, "session_log_usage_per_invocation_test")
        first = io.StringIO(json.dumps({"type": "token_usage_record", "data": {"input_tokens": 3, "output_tokens": 4}}) + "\n")
        second = io.StringIO(json.dumps({"type": "token_usage_record", "data": {"input_tokens": 2, "output_tokens": 1}}) + "\n")
        records, first_totals = usage.aggregate_tokens(first)
        self.assertEqual(records, 1)
        records, second_totals = usage.aggregate_tokens(second)
        self.assertEqual(records, 1)
        self.assertEqual(first_totals["total_tokens"], 7)
        self.assertEqual(second_totals["total_tokens"], 3)

    def test_aggregate_tokens_ignores_empty_and_malformed_input(self):
        usage = load_module(USAGE_PATH, "session_log_usage_edge_test")
        records, totals = usage.aggregate_tokens(io.StringIO(""))
        self.assertEqual(records, 0)
        self.assertEqual(totals, EMPTY_TOTALS)
        malformed = io.StringIO(
            "not json\n"
            "[1, 2, 3]\n"
            "{}\n"
            + json.dumps({"type": "token_usage_record", "data": "oops"}) + "\n"
            + json.dumps({"type": "session_meta", "payload": {}}) + "\n"
        )
        records, totals = usage.aggregate_tokens(malformed)
        self.assertEqual(records, 0)
        self.assertEqual(totals, EMPTY_TOTALS)

    def test_aggregate_tokens_respects_native_total_and_rejects_invalid_numeric_fields(self):
        usage = load_module(USAGE_PATH, "session_log_usage_mixed_records_test")
        transcript = io.StringIO("\n".join(json.dumps(record) for record in (
            {"type": "token_usage_record", "data": {
                "input_tokens": 8, "cached_input_tokens": 3, "output_tokens": 2,
                "reasoning_output_tokens": 1, "total_tokens": 25,
            }},
            {"type": "token_usage_record", "payload": {"usage": {
                "input_tokens": 5, "cached_input_tokens": True, "output_tokens": 4,
                "reasoning_output_tokens": -1, "total_tokens": False,
            }}},
            {"type": "token_usage_record", "data": {
                "input_tokens": -3, "cached_input_tokens": "9", "output_tokens": 7,
                "reasoning_output_tokens": 2, "total_tokens": -1,
            }},
        )) + "\n")
        records, totals = usage.aggregate_tokens(transcript)
        self.assertEqual(records, 3)
        self.assertEqual(totals, {
            "input_tokens": 13,
            "cached_input_tokens": 3,
            "output_tokens": 13,
            "reasoning_output_tokens": 3,
            "total_tokens": 41,
        })

    def test_codex_usage_cli_keeps_report_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "usage.jsonl"
            transcript.write_text(json.dumps({"type": "token_usage_record", "data": {"input_tokens": 3, "output_tokens": 4}}) + "\n")
            result = subprocess.run([sys.executable, str(USAGE_PATH), str(transcript)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            f"session: {transcript}", "TOTAL", "input_tokens: 3", "cached_input_tokens: 0",
            "output_tokens: 4", "reasoning_output_tokens: 0", "total_tokens: 7",
        ])

    def test_codex_usage_cli_errors_when_no_token_records(self):
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "empty.jsonl"
            transcript.write_text(json.dumps({"type": "session_meta", "payload": {}}) + "\n")
            result = subprocess.run([sys.executable, str(USAGE_PATH), str(transcript)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("no token_usage_record data", result.stderr)


CODEX_ROOT_SOURCE = "vscode"
CODEX_CHILD_SOURCE = {"subagent": {"thread_spawn": {"parent_thread_id": "session-a"}}}
OLD_MTIME = 1_000_000
NEW_MTIME = 2_000_000


def codex_user_prompt(timestamp):
    return {"timestamp": timestamp, "type": "event_msg", "payload": {
        "type": "item_completed", "item": {"type": "UserMessage", "content": [{"type": "text", "text": "prompt"}]}}}


def codex_injected_context(timestamp):
    return {"timestamp": timestamp, "type": "response_item", "payload": {
        "type": "message", "role": "user", "content": [{"type": "input_text", "text": "# AGENTS.md instructions"}]}}


def codex_tool_call(timestamp):
    return {"timestamp": timestamp, "type": "response_item", "payload": {"type": "function_call", "name": "shell"}}


class CodexLatestSessionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.home = root / "home"
        self.project = root / "project"
        self.sessions = self.home / ".codex" / "sessions" / "2026" / "10" / "08"
        self.sessions.mkdir(parents=True)
        self.project.mkdir()

    def rollout(self, name, mtime, *records, source=CODEX_ROOT_SOURCE):
        path = self.sessions / f"rollout-{name}.jsonl"
        lines = [{"type": "session_meta", "payload": {"id": name, "cwd": str(self.project), "source": source}},
                 *records, {"type": "token_usage_record", "data": {"input_tokens": 1, "output_tokens": 1}}]
        path.write_text("".join(json.dumps(line) + "\n" for line in lines))
        os.utime(path, (mtime, mtime))
        return path

    def latest(self):
        result = subprocess.run([sys.executable, str(USAGE_PATH)], cwd=self.project, text=True, capture_output=True,
                                env={**os.environ, "HOME": str(self.home)}, timeout=SUBPROCESS_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.splitlines()[0]

    def test_latest_picks_session_with_newest_user_prompt_over_newest_mtime(self):
        prompted = self.rollout("session-a", OLD_MTIME, codex_user_prompt("2026-10-08T10:05:00.000Z"))
        self.rollout("session-b", NEW_MTIME, codex_user_prompt("2026-10-08T10:00:00.000Z"),
                     codex_injected_context("2026-10-08T10:06:00.000Z"), codex_tool_call("2026-10-08T10:07:00.000Z"))
        self.assertEqual(self.latest(), f"session: {prompted}")

    def test_latest_picks_the_only_session(self):
        only = self.rollout("session-a", OLD_MTIME, codex_user_prompt("2026-10-08T10:05:00.000Z"))
        self.assertEqual(self.latest(), f"session: {only}")

    def test_latest_ranks_sessions_without_user_prompt_last_then_by_mtime(self):
        prompted = self.rollout("session-a", OLD_MTIME, codex_user_prompt("2026-10-08T10:05:00.000Z"))
        self.rollout("session-b", NEW_MTIME, codex_injected_context("2026-10-08T10:06:00.000Z"))
        self.assertEqual(self.latest(), f"session: {prompted}")
        prompted.unlink()
        newest = self.rollout("session-c", NEW_MTIME + 1, codex_tool_call("2026-10-08T10:07:00.000Z"))
        self.assertEqual(self.latest(), f"session: {newest}")

    def test_latest_never_picks_subagent_session(self):
        root = self.rollout("session-a", OLD_MTIME, codex_user_prompt("2026-10-08T10:05:00.000Z"))
        self.rollout("session-child", NEW_MTIME, codex_user_prompt("2026-10-08T10:09:00.000Z"),
                     source=CODEX_CHILD_SOURCE)
        self.assertEqual(self.latest(), f"session: {root}")


LOAD_SWAPPED_HOOKS_SCRIPT = """
import importlib.util, os, pathlib, sys
from unittest.mock import patch
spec = importlib.util.spec_from_file_location("install_hooks", sys.argv[1])
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
config, replacement = pathlib.Path(sys.argv[2]), sys.argv[3]
native_open, native_read_text = os.open, pathlib.Path.read_text

def swap():
    # A concurrent writer replaces the validated hooks file right before it is opened for reading.
    if config.is_symlink() or not config.is_file():
        return
    config.unlink()
    if replacement == "fifo":
        os.mkfifo(config)
    else:
        config.symlink_to(replacement)

def swap_then_open(path, *args, **kwargs):
    if os.fspath(path) == str(config):
        swap()
    return native_open(path, *args, **kwargs)

def swap_then_read_text(path, *args, **kwargs):
    if path == config:
        swap()
    return native_read_text(path, *args, **kwargs)

with patch.object(os, "open", side_effect=swap_then_open), \\
        patch.object(pathlib.Path, "read_text", new=swap_then_read_text):
    print(installer.load(config))
"""



class InstallerHooksTests(unittest.TestCase):
    def test_installer_import_has_no_io_side_effects(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"HOME": directory}), \
                patch("sys.stdout", new_callable=io.StringIO) as stdout, \
                patch("sys.stderr", new_callable=io.StringIO) as stderr:
            load_module(INSTALL_PATH, "install_hooks_under_test")
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_installer_cli_preserves_unmanaged_hooks(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory).resolve() / "hooks.json"
            hook = REPO / "skills/session-log/adapters/native/session_log_hook.py"
            config.write_text(json.dumps({"hooks": {"SessionStart": [{"command": "user-hook"}]}}))
            result = subprocess.run([sys.executable, str(INSTALL_PATH), "install", "codex", str(config), str(hook)], text=True, capture_output=True)
            updated = json.loads(config.read_text())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(updated["hooks"]["SessionStart"][0], {"command": "user-hook"})
        self.assertTrue(any("session_log_hook.py codex session-start" in item.get("command", "")
                            for entry in updated["hooks"]["SessionStart"]
                            for item in entry.get("hooks", [])))

    def test_installer_replaces_hooks_registered_from_a_native_windows_path(self):
        installer = load_module(INSTALL_PATH, "install_hooks_windows_path_test")
        windows_hook = r"C:\Users\Jane Doe\.codex\skills\session-log\adapters\native\session_log_hook.py"
        hook = Path("/stable/skills/session-log/adapters/native/session_log_hook.py")
        for harness in ("codex", "cursor"):
            with self.subTest(harness=harness):
                document = {"hooks": {}}
                installer.update(document, harness, Path(windows_hook))
                installer.update(document, harness, hook)
                self.assertTrue(installer.is_current(document, harness, hook))
                for entries in document["hooks"].values():
                    self.assertEqual(len(entries), 1, entries)

    def test_installer_completes_partial_writes_before_replacing_hooks_file(self):
        installer = load_module(INSTALL_PATH, "install_hooks_partial_write_test")
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory).resolve() / "hooks.json"
            document = {"hooks": {"SessionStart": [{"command": "complete-value"}]}}
            native_write = installer.os.write

            def write_partial(descriptor, data):
                return native_write(descriptor, data[:3])

            with patch.object(installer.os, "write", side_effect=write_partial):
                installer.atomic_write(config, document)

            self.assertEqual(json.loads(config.read_text()), document)

    def test_installer_load_refuses_hooks_file_swapped_after_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            secret = root / "secret.json"
            secret.write_text(json.dumps({"secret": True}))
            config = root / "hooks.json"
            for replacement in (str(secret), "fifo"):
                with self.subTest(replacement=replacement):
                    config.unlink(missing_ok=True)
                    config.write_text(json.dumps({"hooks": {}}))
                    result = subprocess.run(
                        [sys.executable, "-c", LOAD_SWAPPED_HOOKS_SCRIPT, str(INSTALL_PATH), str(config), replacement],
                        text=True, capture_output=True, timeout=SUBPROCESS_TIMEOUT_SECONDS,
                    )
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn("refusing to update unsafe hooks file", result.stderr)

    def test_installer_reports_invalid_utf8_hooks_file_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory).resolve() / "hooks.json"
            hook = REPO / "skills/session-log/adapters/native/session_log_hook.py"
            config.write_bytes(b"\xff\xfe{}")
            result = subprocess.run([sys.executable, str(INSTALL_PATH), "install", "codex", str(config), str(hook)], text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot read hooks file", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class AtomicSwapRaceTests(unittest.TestCase):
    def test_atomic_writers_preserve_targets_created_during_install(self):
        installer = load_module(INSTALL_PATH, "install_hooks_race_test")
        hook = load_module(HOOK_PATH, "session_log_hook_race_test")
        pathsafe = installer.pathsafe
        native_move = pathsafe.move_no_replace
        writers = (
            ("pathsafe", lambda path: pathsafe.write_file(str(path), "new value")),
            ("installer", lambda path: installer.atomic_write(path, {"value": "new"})),
            (
                "hook",
                lambda path: hook.atomic_private_json(path, {"value": "new"}, "codex"),
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            for name, writer in writers:
                target = Path(directory).resolve() / f"{name}.json"

                def create_racing_target(dir_fd, source_name, target_name):
                    if target_name == target.name:
                        descriptor = os.open(
                            target_name,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                            0o600,
                            dir_fd=dir_fd,
                        )
                        os.write(descriptor, b"raced\n")
                        os.close(descriptor)
                    return native_move(dir_fd, source_name, target_name)

                with patch.object(
                    pathsafe, "move_no_replace", side_effect=create_racing_target
                ):
                    with self.assertRaises(FileExistsError):
                        writer(target)
                self.assertEqual(target.read_text(), "raced\n")

    def test_atomic_writers_quarantine_swapped_temp_for_absent_targets(self):
        installer = load_module(INSTALL_PATH, "install_hooks_absent_swap_test")
        hook = load_module(HOOK_PATH, "session_log_hook_absent_swap_test")
        pathsafe = installer.pathsafe
        native_move = pathsafe.move_no_replace
        writers = (
            ("pathsafe", lambda path: pathsafe.write_file(str(path), "intended")),
            ("installer", lambda path: installer.atomic_write(path, {"value": "new"})),
            ("hook", lambda path: hook.atomic_private_json(path, {"value": "new"}, "codex")),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for name, writer in writers:
                target = root / f"{name}.json"
                conflicts_before = set(root.glob(".*.conflict.*"))

                def swap_temporary(dir_fd, source_name, target_name):
                    if _is_temp_swap_target(source_name, target_name, target.name):
                        os.unlink(source_name, dir_fd=dir_fd)
                        descriptor = os.open(
                            source_name,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                            0o600,
                            dir_fd=dir_fd,
                        )
                        os.write(descriptor, b"raced replacement\n")
                        os.close(descriptor)
                    return native_move(dir_fd, source_name, target_name)

                with patch.object(
                    pathsafe, "move_no_replace", side_effect=swap_temporary
                ):
                    with self.assertRaises(OSError):
                        writer(target)

                self.assertFalse(target.exists())
                conflicts = set(root.glob(".*.conflict.*")) - conflicts_before
                self.assertEqual(len(conflicts), 1)
                self.assertEqual(next(iter(conflicts)).read_bytes(), b"raced replacement\n")

    def test_atomic_writers_quarantine_entries_swapped_after_install(self):
        installer = load_module(INSTALL_PATH, "install_hooks_post_rename_test")
        hook = load_module(HOOK_PATH, "session_log_hook_post_rename_test")
        pathsafe = installer.pathsafe
        native_move = pathsafe.move_no_replace
        installer_document = {"value": "new"}
        writers = (
            ("pathsafe", lambda path: pathsafe.write_file(str(path), "intended"), b"intended\n"),
            (
                "installer",
                lambda path: installer.atomic_write(path, installer_document),
                (json.dumps(installer_document, indent=2) + "\n").encode(),
            ),
            (
                "hook",
                lambda path: hook.atomic_private_json(path, installer_document, "codex"),
                (json.dumps(installer_document, separators=(",", ":")) + "\n").encode(),
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for name, writer, expected_content in writers:
                target = root / f"{name}.json"
                displaced = f"{target.name}.displaced"
                conflicts_before = set(root.glob(".*.conflict.*"))

                occupied_conflicts = set()

                def swap_after_install(dir_fd, source_name, target_name):
                    result = native_move(dir_fd, source_name, target_name)
                    if target_name == target.name:
                        for attempt in range(pathsafe.MAX_TEMP_FILE_ATTEMPTS):
                            conflict = f"{source_name}.conflict.{attempt}"
                            descriptor = os.open(
                                conflict,
                                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                0o600,
                                dir_fd=dir_fd,
                            )
                            os.write(descriptor, b"occupied conflict\n")
                            os.close(descriptor)
                            occupied_conflicts.add(conflict)
                        os.rename(
                            target_name,
                            displaced,
                            src_dir_fd=dir_fd,
                            dst_dir_fd=dir_fd,
                        )
                        descriptor = os.open(
                            target_name,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                            0o600,
                            dir_fd=dir_fd,
                        )
                        os.write(descriptor, b"raced after rename\n")
                        os.close(descriptor)
                    return result

                with patch.object(
                    pathsafe, "move_no_replace", side_effect=swap_after_install
                ):
                    with self.assertRaises(OSError):
                        writer(target)

                self.assertFalse(target.exists())
                self.assertEqual((root / displaced).read_bytes(), expected_content)
                new_conflicts = set(root.glob(".*.conflict.*")) - conflicts_before
                raced_conflicts = [
                    conflict
                    for conflict in new_conflicts
                    if conflict.read_bytes() == b"raced after rename\n"
                ]
                self.assertEqual(len(occupied_conflicts), pathsafe.MAX_TEMP_FILE_ATTEMPTS)
                self.assertTrue(
                    all(
                        (root / conflict).read_bytes() == b"occupied conflict\n"
                        for conflict in occupied_conflicts
                    )
                )
                self.assertEqual(len(raced_conflicts), 1)
                self.assertNotIn(raced_conflicts[0].name, occupied_conflicts)

    def test_atomic_replacement_preserves_target_when_temp_path_is_swapped(self):
        installer = load_module(INSTALL_PATH, "install_hooks_temp_swap_test")
        hook = load_module(HOOK_PATH, "session_log_hook_temp_swap_test")
        pathsafe = installer.pathsafe
        native_move = pathsafe.move_no_replace
        writers = (
            ("pathsafe", lambda path: pathsafe.write_file(str(path), "intended replacement")),
            ("installer", lambda path: installer.atomic_write(path, {"value": "new"})),
            ("hook", lambda path: hook.atomic_private_json(path, {"value": "new"}, "codex")),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for name, writer in writers:
                target = root / f"{name}.json"
                conflicts_before = set(root.glob(".session-log-backup.*.conflict.*"))
                target.write_bytes(b"original\n")

                def swap_temporary(dir_fd, source_name, target_name):
                    if _is_temp_swap_target(source_name, target_name, target.name):
                        os.unlink(source_name, dir_fd=dir_fd)
                        descriptor = os.open(
                            source_name,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                            0o600,
                            dir_fd=dir_fd,
                        )
                        os.write(descriptor, b"raced replacement\n")
                        os.close(descriptor)
                    return native_move(dir_fd, source_name, target_name)

                with patch.object(
                    pathsafe, "move_no_replace", side_effect=swap_temporary
                ):
                    with self.assertRaises(OSError):
                        writer(target)

                self.assertEqual(target.read_bytes(), b"original\n")
                conflicts = set(target.parent.glob(".session-log-backup.*.conflict.*")) - conflicts_before
                self.assertEqual(len(conflicts), 1)
                self.assertEqual(next(iter(conflicts)).read_bytes(), b"raced replacement\n")

    def test_atomic_writer_cleanup_preserves_swapped_temp_source(self):
        installer = load_module(INSTALL_PATH, "install_hooks_temp_cleanup_test")
        hook = load_module(HOOK_PATH, "session_log_hook_temp_cleanup_test")
        pathsafe = installer.pathsafe
        native_replace = pathsafe.replace_file_no_replace
        writers = (
            ("pathsafe", lambda path: pathsafe.write_file(str(path), "new value")),
            ("installer", lambda path: installer.atomic_write(path, {"value": "new"})),
            ("hook", lambda path: hook.atomic_private_json(path, {"value": "new"}, "codex")),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for name, writer in writers:
                target = root / f"{name}.json"
                target.write_bytes(b"original\n")
                cleanups_before = set(root.glob(".session-log-cleanup.*"))

                def swap_before_validation(request):
                    os.unlink(request.source_name, dir_fd=request.dir_fd)
                    descriptor = os.open(
                        request.source_name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                        dir_fd=request.dir_fd,
                    )
                    os.write(descriptor, b"raced temp source\n")
                    os.close(descriptor)
                    return native_replace(request)

                with patch.object(
                    pathsafe, "replace_file_no_replace", side_effect=swap_before_validation
                ):
                    with self.assertRaises(OSError):
                        writer(target)

                self.assertEqual(target.read_bytes(), b"original\n")
                cleanups = set(root.glob(".session-log-cleanup.*")) - cleanups_before
                self.assertEqual(len(cleanups), 1)
                self.assertEqual(next(iter(cleanups)).read_bytes(), b"raced temp source\n")

    def test_cleanup_retains_temp_in_nonsticky_shared_directory(self):
        installer = load_module(INSTALL_PATH, "install_hooks_shared_cleanup_test")
        pathsafe = installer.pathsafe
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            root.chmod(0o777)
            directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                descriptor = os.open(
                    "temporary",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=directory_fd,
                )
                os.write(descriptor, b"keep this file\n")
                expected = os.fstat(descriptor)
                os.close(descriptor)
                pathsafe.unlink_if_same_file(directory_fd, "temporary", expected)
            finally:
                os.close(directory_fd)

            self.assertEqual((root / "temporary").read_bytes(), b"keep this file\n")


class DirectoryLifecycleTests(unittest.TestCase):
    def test_copy_closes_source_directory_when_target_cannot_open(self):
        installer = load_module(INSTALL_PATH, "install_hooks_directory_test")
        pathsafe = installer.pathsafe
        native_open = pathsafe.open_directory
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.write_bytes(b"source\n")
            source_descriptor = None

            def open_then_fail(path, *, create):
                nonlocal source_descriptor
                if source_descriptor is None:
                    source_descriptor = native_open(path, create=create)
                    return source_descriptor
                raise PermissionError("target directory unavailable")

            with patch.object(pathsafe, "open_directory", side_effect=open_then_fail):
                with self.assertRaises(PermissionError):
                    pathsafe.copy_file(pathsafe.CopyFileOptions(
                        str(source), str(Path(directory) / "target"),
                        preserve_mode=False, manifest_owned=False,
                    ))
            with self.assertRaises(OSError):
                os.fstat(source_descriptor)

    def test_settings_closes_directory_when_lock_open_fails(self):
        with patch.object(sys, "path", [str(CLAUDE_SETTINGS_PATH.parent), *sys.path]):
            settings = load_module(CLAUDE_SETTINGS_PATH, "claude_settings_directory_test")
        native_open = settings.os.open
        native_directory_open = settings.pathsafe.open_directory
        with tempfile.TemporaryDirectory() as directory:
            settings_path = str(Path(directory).resolve() / "settings.json")
            for operation in ("install", "migrate"):
                with self.subTest(operation=operation):
                    opened = []

                    def record_directory(path, *, create):
                        fd = native_directory_open(path, create=create)
                        opened.append(fd)
                        return fd

                    def reject_lock(path, *args, **kwargs):
                        if path == "settings.json.session-log.lock":
                            raise PermissionError("lock unavailable")
                        return native_open(path, *args, **kwargs)

                    with patch.dict(os.environ, {
                        "SETTINGS_PATH": settings_path,
                        "HOOK_PATH": "/unused/hook.sh",
                        "SESSION_LOG_OWNER_MARKER": "test-owner",
                        "CLAUDE_SCRIPTS_DIR": str(Path(directory).resolve() / "scripts"),
                    }), patch.object(settings.pathsafe, "open_directory", side_effect=record_directory), \
                            patch.object(settings.os, "open", side_effect=reject_lock):
                        with self.assertRaises(PermissionError):
                            settings.main([operation])
                    self.assertEqual(len(opened), 1)
                    with self.assertRaises(OSError):
                        os.fstat(opened[0])


class ClaudeSettingsOwnershipTests(unittest.TestCase):
    OWNER = "universal-session-log"

    def load(self):
        with patch.object(sys, "path", [str(CLAUDE_SETTINGS_PATH.parent), *sys.path]):
            return load_module(CLAUDE_SETTINGS_PATH, "claude_settings_ownership_test")

    def owned(self, path, name):
        return {"type": "command", "command": f"SESSION_LOG_OWNER={self.OWNER} bash {path} {name}"}

    def test_remove_strips_owned_hooks_of_any_path_and_keeps_the_rest(self):
        settings = self.load()
        lookalike = {"type": "command", "command": "SESSION_LOG_OWNER=universal-session-log-other bash x stop"}
        foreign = {"type": "command", "command": "cc-status stop"}
        document = {"hooks": {
            "Stop": [{"hooks": [self.owned("/old/1.14.1/claude_hook.sh", "stop"), foreign]}],
            "SubagentStop": [{"hooks": [self.owned("/stable/claude_hook.sh", "subagent-stop")]}],
            "Notification": [{"hooks": [lookalike]}],
        }}
        self.assertTrue(settings._remove_managed_hooks(document, settings._owned_hook(self.OWNER)))
        self.assertEqual(document, {"hooks": {
            "Stop": [{"hooks": [foreign]}],
            "Notification": [{"hooks": [lookalike]}],
        }})
        self.assertFalse(settings._remove_managed_hooks(document, settings._owned_hook(self.OWNER)))

    def test_remove_drops_hooks_object_left_empty(self):
        settings = self.load()
        document = {"model": "opus", "hooks": {"Stop": [{"hooks": [self.owned("/x/claude_hook.sh", "stop")]}]}}
        self.assertTrue(settings._remove_managed_hooks(document, settings._owned_hook(self.OWNER)))
        self.assertEqual(document, {"model": "opus"})

    def test_install_replaces_owned_hooks_with_stale_paths(self):
        settings = self.load()
        document = {"hooks": {"Stop": [{"hooks": [self.owned("/old/1.14.1/claude_hook.sh", "stop")]}]}}
        settings._inject_managed_hooks(document, "/stable/claude_hook.sh", self.OWNER)
        self.assertEqual(document["hooks"]["Stop"], [{"hooks": [self.owned("/stable/claude_hook.sh", "stop")]}])

    def test_install_replaces_unmarked_copies_and_keeps_lookalikes(self):
        settings = self.load()
        tail = PLUGIN_HOOK_TAIL
        unmarked = [
            settings.plugin_hook_command("stop"),
            f'exec bash "${{CLAUDE_PLUGIN_ROOT}}/{tail}" stop',
            f"bash /Users/a\\ b/.claude/{tail} stop",
            "bash 'C:\\Users\\Jane Doe\\.claude\\" + tail.replace("/", "\\") + "' stop",
        ]
        lookalikes = [f"bash /x/{tail} unknown-event", f"notify && bash /x/{tail} stop", "bash /x/other_hook.sh stop"]
        document = {"hooks": {"Stop": [{"hooks": [command_hook(value) for value in unmarked + lookalikes]}]}}
        settings._inject_managed_hooks(document, "/stable/claude_hook.sh", self.OWNER)
        self.assertEqual(document["hooks"]["Stop"], [
            {"hooks": [command_hook(value) for value in lookalikes]},
            {"hooks": [self.owned("/stable/claude_hook.sh", "stop")]},
        ])

    def test_install_strips_owned_hooks_from_unmanaged_events_and_keeps_the_rest(self):
        settings = self.load()
        foreign = {"type": "command", "command": "cc-status compact"}
        document = {"hooks": {
            "PreCompact": [{"hooks": [self.owned("/old/claude_hook.sh", "stop"), foreign]}],
            "SessionEnd": [{"hooks": [self.owned("/old/claude_hook.sh", "stop")]}],
            "Notification": [],
        }}
        settings._inject_managed_hooks(document, "/stable/claude_hook.sh", self.OWNER)
        self.assertEqual(document["hooks"]["PreCompact"], [{"hooks": [foreign]}])
        self.assertNotIn("SessionEnd", document["hooks"])
        self.assertEqual(document["hooks"]["Notification"], [])

    def test_windows_path_with_a_hash_is_recognized_as_a_claude_hook(self):
        settings = self.load()
        windows_hook = "C:\\a#b\\.claude\\" + PLUGIN_HOOK_TAIL.replace("/", "\\")
        self.assertTrue(settings._runs_claude_hook(f"bash {windows_hook} stop"))

    def test_remove_cli_leaves_missing_settings_absent(self):
        settings = self.load()
        with tempfile.TemporaryDirectory() as directory:
            settings_path = Path(directory) / "settings.json"
            with patch.dict(os.environ, {"SETTINGS_PATH": str(settings_path), "SESSION_LOG_OWNER_MARKER": self.OWNER}):
                settings.main(["remove", "plugin"])
            self.assertFalse(settings_path.exists())

    def test_remove_keeps_the_matcher_and_extra_keys_of_a_mixed_entry(self):
        settings = self.load()
        foreign = {"type": "command", "command": "cc-status session-start"}
        document = {"hooks": {"SessionStart": [{
            "matcher": "startup", "note": "kept", "hooks": [self.owned("/x/claude_hook.sh", "session-start"), foreign],
        }]}}
        self.assertTrue(settings._remove_managed_hooks(document, settings._owned_hook(self.OWNER)))
        self.assertEqual(document, {"hooks": {"SessionStart": [{"matcher": "startup", "note": "kept", "hooks": [foreign]}]}})

    def cli(self, environment, *arguments):
        return subprocess.run(
            [sys.executable, str(CLAUDE_SETTINGS_PATH), *arguments], env=environment, capture_output=True, text=True,
        )

    def run_cli(self, environment, *arguments):
        return self.cli(environment, *arguments).returncode

    def removable_cases(self, home):
        """``(command, plugin status, plugin-dead-only status)``: exit 0 when the mode removes the registration."""
        prefix = f"SESSION_LOG_OWNER={self.OWNER} bash"
        live_hook = home / "live" / PLUGIN_HOOK_TAIL
        cached_hook = home / PLUGIN_CACHE_RELATIVE / PLUGIN_HOOK_TAIL
        for hook in (live_hook, cached_hook):
            hook.parent.mkdir(parents=True)
            hook.write_text("")
        cache_tail = f"{PLUGIN_CACHE_RELATIVE}/{PLUGIN_HOOK_TAIL}"
        windows_hook = "C:\\Users\\Jane Doe\\.claude\\" + PLUGIN_HOOK_TAIL.replace("/", "\\")
        return {
            "foreign": ("cc-status", 1, 1),
            "live absolute path": (f"{prefix} {live_hook} stop", 0, 1),
            "missing absolute path": (f"{prefix} {home}/gone/{PLUGIN_HOOK_TAIL} stop", 0, 0),
            "verbatim plugin command": (PLUGIN_COMMANDS_FOR_TESTS["stop"], 0, 0),
            "owner-marked plugin root placeholder": (f'{prefix} "{PLUGIN_ROOT_PLACEHOLDER}/{PLUGIN_HOOK_TAIL}" stop', 0, 0),
            "native Windows path": (f"{prefix} '{windows_hook}' stop", 0, 0),
            "existing versioned plugin-cache copy": (f"{prefix} {cached_hook} stop", 0, 0),
            "plugin-cache copy under $HOME": (f'{prefix} "$HOME/{cache_tail}" stop', 0, 0),
            "plugin-cache copy under ~": (f"{prefix} ~/{cache_tail} stop", 0, 0),
            "unparsable": (f"{prefix} '/unterminated/{PLUGIN_HOOK_TAIL} stop", 0, 1),
            "path under ~": (f"{prefix} ~/gone/{PLUGIN_HOOK_TAIL} stop", 0, 1),
            "path under $HOME": (f'{prefix} "$HOME/gone/{PLUGIN_HOOK_TAIL}" stop', 0, 1),
            "path under ${HOME}": (f'{prefix} "${{HOME}}/gone/{PLUGIN_HOOK_TAIL}" stop', 0, 1),
            "relative path": (f"{prefix} gone/{PLUGIN_HOOK_TAIL} stop", 0, 1),
        }

    def test_has_removable_cli_reports_owned_hooks_or_only_dead_ones(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            settings_path = home / "settings.json"
            environment = {**os.environ, "HOME": str(home), "SETTINGS_PATH": str(settings_path),
                           "SESSION_LOG_OWNER_MARKER": self.OWNER}
            for name, (command, plugin_status, dead_only_status) in self.removable_cases(home).items():
                with self.subTest(name):
                    settings_path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [command_hook(command)]}]}}))
                    self.assertEqual(self.run_cli(environment, "has-removable", "plugin"), plugin_status)
                    self.assertEqual(self.run_cli(environment, "has-removable", "plugin-dead-only"), dead_only_status)

    def test_operations_refuse_a_missing_or_unknown_mode(self):
        environment = {**os.environ, "SETTINGS_PATH": "/nonexistent/settings.json", "SESSION_LOG_OWNER_MARKER": self.OWNER}
        for arguments in (("repair",), ("repair", "bogus"), ("remove", "standalone"), ("has-removable",)):
            with self.subTest(arguments=arguments):
                result = self.cli(environment, *arguments)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("claude_settings: expected one mode of ", result.stderr)

    def test_plugin_enabled_cli_accepts_only_a_true_user_scope_key(self):
        with tempfile.TemporaryDirectory() as directory:
            settings_path = Path(directory) / "settings.json"
            environment = {**os.environ, "SETTINGS_PATH": str(settings_path), "PLUGIN_KEY": PLUGIN_KEY}
            cases = {
                "enabled": (json.dumps(USER_SCOPE_ENABLED).encode(), 0),
                "malformed JSON": (b"{not json", 1),
                "invalid UTF-8": (b'{"enabledPlugins": {"\xff": true}}', 1),
                "root not an object": (json.dumps([USER_SCOPE_ENABLED]).encode(), 1),
                "enabledPlugins not an object": (json.dumps({"enabledPlugins": [PLUGIN_KEY]}).encode(), 1),
                "key false": (json.dumps({"enabledPlugins": {PLUGIN_KEY: False}}).encode(), 1),
            }
            for name, (contents, status) in cases.items():
                with self.subTest(name):
                    settings_path.write_bytes(contents)
                    self.assertEqual(self.run_cli(environment, "plugin-enabled"), status)
            settings_path.unlink()
            self.assertEqual(self.run_cli(environment, "plugin-enabled"), 1)

    def test_hooks_json_registers_the_plugin_hook_command_for_every_lifecycle(self):
        settings = self.load()
        for event, hook in plugin_hooks():
            with self.subTest(event=event):
                self.assertEqual(hook["command"], settings.plugin_hook_command(PLUGIN_EVENT_LIFECYCLES[event]))

    def test_plugin_hooks_current_requires_the_repository_hooks_json(self):
        with tempfile.TemporaryDirectory() as directory:
            plugin_root = Path(directory)
            hook = plugin_root / "skills/session-log/adapters/claude/claude_hook.sh"
            hook.parent.mkdir(parents=True)
            hook.write_text("#!/usr/bin/env bash\n")
            environment = {**os.environ, "PLUGIN_ROOT": str(plugin_root)}
            self.assertEqual(self.run_cli(environment, "plugin-hooks-current"), 1)
            (plugin_root / "hooks").mkdir()
            (plugin_root / "hooks/hooks.json").write_text((REPO / "hooks/hooks.json").read_text())
            self.assertEqual(self.run_cli(environment, "plugin-hooks-current"), 0)
            (plugin_root / "hooks/hooks.json").write_text(json.dumps({"hooks": {}}))
            self.assertEqual(self.run_cli(environment, "plugin-hooks-current"), 1)


PLUGIN_ROOT_PLACEHOLDER = "${CLAUDE_PLUGIN_ROOT}"
# Plugin-cache version directories: the current plugin, the release before it, an old release
# whose dead paths earlier installs wrote into settings.json, and one that no longer exists.
PLUGIN_VERSION = "1.18.2"
PREVIOUS_PLUGIN_VERSION = "1.18.1"
OLD_PLUGIN_VERSION = "1.14.1"
REMOVED_PLUGIN_VERSION = "1.0.0"
CLAUDE_LIFECYCLE_EVENTS = ("SessionStart", "UserPromptSubmit", "Stop", "SubagentStop")
WINDOWS_USER_HOME = r"C:\Users\Jane Doe"
WINDOWS_PLUGIN_ROOT = WINDOWS_USER_HOME + r"\.claude\plugins\cache\harness-goodies\claude-goodies" + "\\" + OLD_PLUGIN_VERSION
# Tools claude_hook.sh may touch while logging is off; jq is deliberately absent,
# as in Git for Windows, which ships Git Bash without jq.
GIT_BASH_TOOLS_WITHOUT_JQ = (
    "bash", "cat", "cksum", "date", "dirname", "grep", "mkdir", "mv", "ps", "python3", "sed", "tr", "unlink",
)


def sandbox_plugin_root(plugin_root):
    """A claude-goodies plugin root holding this repository's hooks and session-log package."""
    (plugin_root / "skills").mkdir(parents=True)
    shutil.copytree(REPO / "skills/session-log", plugin_root / "skills/session-log")
    shutil.copytree(REPO / "hooks", plugin_root / "hooks")
    return plugin_root


def plugin_hooks():
    document = json.loads((REPO / "hooks/hooks.json").read_text())
    for event in CLAUDE_LIFECYCLE_EVENTS:
        for entry in document["hooks"][event]:
            for hook in entry["hooks"]:
                yield event, hook


PLUGIN_EVENT_LIFECYCLES = {
    "SessionStart": "session-start", "UserPromptSubmit": "user-prompt", "Stop": "stop", "SubagentStop": "subagent-stop",
}
# The hooks/hooks.json command per lifecycle, as a copy pasted verbatim into settings.json would hold it.
PLUGIN_COMMANDS_FOR_TESTS = {PLUGIN_EVENT_LIFECYCLES[event]: hook["command"] for event, hook in plugin_hooks()}
PLUGIN_HOOK_TAIL = "skills/session-log/adapters/claude/claude_hook.sh"
# The plugin-cache tail below a real POSIX prefix, written with Windows separators.
WINDOWS_STYLE_PLUGIN_TAIL = r".claude\plugins\cache\harness-goodies\claude-goodies" + "\\" + PLUGIN_VERSION
# Records the lifecycle argument it was started with, proving the hook command reached the script.
RECORDING_CLAUDE_HOOK = '#!/usr/bin/env bash\nprintf "%s" "$1" > "$SESSION_LOG_TEST_RECORD"\n'


# Shells that run shell-form hooks: Git Bash on Windows, /bin/sh on macOS/Linux (dash on Debian/Ubuntu).
HOOK_SHELL_NAMES = ("bash", "/bin/sh", "dash")


def hook_shells(test):
    """The installed hook shells; a missing one is reported as a skipped subtest, never dropped silently."""
    installed = []
    for name in HOOK_SHELL_NAMES:
        with test.subTest(shell=name):
            shell = shutil.which(name)
            if shell is None:
                test.skipTest(f"hook shell {name} is not installed")
            installed.append(shell)
    return installed


def link_tools(directory, tools):
    """Symlink each tool into ``directory``, a PATH holding nothing else; skips the test when one is missing."""
    directory.mkdir()
    for tool in tools:
        path = sys.executable if tool == "python3" else shutil.which(tool)
        if path is None:
            raise unittest.SkipTest(f"{tool} is not installed")
        (directory / tool).symlink_to(path)
    return directory


def run_plugin_hook_command(shell, hook, plugin_root, *, substituted, environment, payload="{}"):
    """Run a hooks.json shell-form command the way Claude Code spawns it: `<shell> -c <command>`.

    Claude Code exports CLAUDE_PLUGIN_ROOT to the hook process and also substitutes the placeholder
    textually, with forward slashes on Windows (https://code.claude.com/docs/en/plugins-reference,
    "Environment variables"), so both the expanded and the substituted spelling must reach the script.
    """
    command = hook["command"].replace(PLUGIN_ROOT_PLACEHOLDER, plugin_root) if substituted else hook["command"]
    return subprocess.run(
        [shell, "-c", command], input=payload, capture_output=True, text=True,
        env={**environment, "CLAUDE_PLUGIN_ROOT": plugin_root},
    )


class ClaudeWindowsHookTests(unittest.TestCase):
    """Claude Code on Windows hands plugin hooks a native C:\\ plugin root.

    A shell-form hook with "shell": "bash" runs in Git Bash on Windows and through `sh -c` on
    macOS and Linux (https://code.claude.com/docs/en/hooks, "command hook fields"); an exec-form
    hook ("args") would resolve `bash` through the plain Windows PATH instead.
    """

    def test_plugin_hooks_use_shell_form_pinned_to_bash(self):
        for event, hook in plugin_hooks():
            with self.subTest(event=event):
                self.assertNotIn("args", hook, f"{event} hook uses exec form, which resolves bash through the Windows PATH")
                self.assertEqual(hook.get("shell"), "bash", f"{event} hook does not pin Git Bash on Windows: {hook}")

    def test_plugin_hooks_reach_the_script_through_a_windows_style_plugin_root(self):
        with tempfile.TemporaryDirectory() as directory:
            user_home = Path(directory).resolve() / "Jane O'Doe"
            hook_script = user_home / WINDOWS_STYLE_PLUGIN_TAIL.replace("\\", "/") / PLUGIN_HOOK_TAIL
            hook_script.parent.mkdir(parents=True)
            hook_script.write_text(RECORDING_CLAUDE_HOOK)
            plugin_root = f"{user_home}\\{WINDOWS_STYLE_PLUGIN_TAIL}"
            record = Path(directory) / "record"
            environment = {**os.environ, "SESSION_LOG_TEST_RECORD": str(record)}
            for shell in hook_shells(self):
                for substituted in (False, True):
                    for event, hook in plugin_hooks():
                        with self.subTest(shell=shell, substituted=substituted, event=event):
                            record.unlink(missing_ok=True)
                            result = run_plugin_hook_command(
                                shell, hook, plugin_root, substituted=substituted, environment=environment)
                            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
                            self.assertEqual(record.read_text() if record.exists() else None,
                                             PLUGIN_EVENT_LIFECYCLES[event])

    def test_plugin_hooks_exit_silently_when_the_script_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            missing_root = f"{Path(directory).resolve()}\\{WINDOWS_STYLE_PLUGIN_TAIL}"
            for shell in hook_shells(self):
                for substituted in (False, True):
                    for event, hook in plugin_hooks():
                        with self.subTest(shell=shell, substituted=substituted, event=event):
                            result = run_plugin_hook_command(
                                shell, hook, missing_root, substituted=substituted, environment=dict(os.environ))
                            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_plugin_hooks_run_the_real_hook_from_a_plugin_root_with_spaces_and_quotes(self):
        if shutil.which("jq") is None:
            self.skipTest("jq is not installed")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve() / "home"
            home.mkdir()
            plugin_root = sandbox_plugin_root(Path(directory).resolve() / "Jane O'Doe" / "plugin")
            (home / ".claude/prompt-logs").mkdir(parents=True)
            (home / ".claude/prompt-logs/.enabled").write_text("")
            environment = harness_environment(home)
            for event, hook in plugin_hooks():
                with self.subTest(event=event):
                    result = run_plugin_hook_command(
                        "bash", hook, str(plugin_root), substituted=False, environment=environment,
                        payload=json.dumps({"session_id": "windows-session", "cwd": directory}))
                    self.assertEqual((result.returncode, result.stderr), (0, ""))
            # The hook ran for real: session-start recorded this session's runtime marker.
            self.assertTrue((home / ".claude/session-log/runtime.json").is_file())

    def test_hook_clears_the_runtime_marker_without_jq_while_logging_is_off(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            home = root / "home"
            runtime = home / ".claude/session-log/runtime.json"
            runtime.parent.mkdir(parents=True)
            runtime.write_text("{}")
            tools = link_tools(root / "git-bash-bin", GIT_BASH_TOOLS_WITHOUT_JQ)
            plugin_root = sandbox_plugin_root(root / "plugin")
            result = run_claude_hook(plugin_root / "skills/session-log", home, "session-start",
                                     payload=json.dumps({"session_id": "windows-session", "cwd": directory}),
                                     environment={"HOME": str(home), "PATH": str(tools)})
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
            self.assertFalse(runtime.exists(), "a logging-off session start kept the stale runtime marker")


class ClaudeSettingsRepairTests(unittest.TestCase):
    """Installing in plugin mode repairs every stale session-log registration in settings.json."""

    maxDiff = None

    OWNER_PREFIX = "SESSION_LOG_OWNER=universal-session-log "
    FOREIGN_SESSION_START = {"type": "command", "command": "cc-status session-start"}
    FOREIGN_STOP = {"type": "command", "command": "bash ~/.claude/cc-status.sh stop"}
    FOREIGN_NOTIFICATION = {"type": "command", "command": "cc-status notify"}

    def stale_settings(self, home):
        windows_hook = WINDOWS_PLUGIN_ROOT + "\\" + PLUGIN_HOOK_TAIL.replace("/", "\\")
        windows_legacy = WINDOWS_USER_HOME + r"\.claude\scripts\prompt_log_save.sh"
        return {
            "model": "opus",
            "hooks": {
                "SessionStart": [
                    # The plugin's own command copied into settings.json, where no plugin root is substituted.
                    {"matcher": "startup", "hooks": [command_hook(f'exec bash "{PLUGIN_ROOT_PLACEHOLDER}/{PLUGIN_HOOK_TAIL}" session-start')]},
                    {"matcher": "startup|resume", "hooks": [command_hook(f"{self.OWNER_PREFIX}bash '{windows_hook}' session-start")]},
                    {"hooks": [self.FOREIGN_SESSION_START]},
                ],
                "UserPromptSubmit": [
                    {"hooks": [command_hook(f"bash /Users/old/.claude/{PLUGIN_HOOK_TAIL} user-prompt")]},
                    {"hooks": [command_hook(f'bash "{windows_legacy}"')]},
                ],
                "Stop": [{"hooks": [command_hook(f'bash "{windows_hook}" stop'), self.FOREIGN_STOP]}],
                "SubagentStop": [
                    {"hooks": [command_hook(f"{self.OWNER_PREFIX}bash {home}/.claude/skills/session-log/adapters/claude/claude_hook.sh subagent-stop")]},
                    {"hooks": [command_hook(f"{self.OWNER_PREFIX}bash {home}/.claude/{PLUGIN_HOOK_TAIL} subagent-stop")]},
                ],
                "Notification": [{"hooks": [self.FOREIGN_NOTIFICATION]}],
            },
        }

    def install_on(self, home, plugin_root):
        return subprocess.run(
            ["bash", str(plugin_root / "skills/session-log/install.sh"), "--harness", "claude", "--arguments", "on"],
            capture_output=True, text=True, env=harness_environment(home),
        )

    def test_plugin_install_removes_stale_duplicate_and_windows_registrations(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            (home / ".Trash").mkdir()
            plugin_root = sandbox_plugin_root(home / PLUGIN_CACHE_RELATIVE)
            settings = home / ".claude/settings.json"
            settings.write_text(json.dumps({**USER_SCOPE_ENABLED, **self.stale_settings(home)}, indent=2))

            first = self.install_on(home, plugin_root)
            self.assertEqual(first.returncode, 0, first.stderr)
            repaired = settings.read_bytes()
            self.assertEqual(json.loads(repaired), {
                **USER_SCOPE_ENABLED,
                "model": "opus",
                "hooks": {
                    "SessionStart": [{"hooks": [self.FOREIGN_SESSION_START]}],
                    "Stop": [{"hooks": [self.FOREIGN_STOP]}],
                    "Notification": [{"hooks": [self.FOREIGN_NOTIFICATION]}],
                },
            })

            second = self.install_on(home, plugin_root)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(settings.read_bytes(), repaired)

    def test_plugin_copy_with_stale_hooks_json_registers_the_stable_copy_on_on(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            (home / ".Trash").mkdir()
            plugin_root = sandbox_plugin_root(home / PLUGIN_CACHE_RELATIVE)
            (plugin_root / "hooks/hooks.json").write_text(json.dumps({"hooks": {}}))
            result = self.install_on(home, plugin_root)
            self.assertEqual(result.returncode, 0, result.stderr)
            settings = (home / ".claude/settings.json").read_text()
            self.assertEqual(json.loads(settings)["hooks"], claude_hooks_registered(home / STANDALONE_RELATIVE, 1))
            self.assertNotIn("plugins/cache", settings)

    def test_plugin_session_start_removes_unmarked_stale_registrations(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            plugin_root = sandbox_plugin_root(home / PLUGIN_CACHE_RELATIVE)
            settings = home / ".claude/settings.json"
            windows_hook = WINDOWS_PLUGIN_ROOT + "\\" + PLUGIN_HOOK_TAIL.replace("/", "\\")
            settings.write_text(json.dumps({"hooks": {
                "SessionStart": [{"hooks": [
                    {"type": "command", "command": f'exec bash "{PLUGIN_ROOT_PLACEHOLDER}/{PLUGIN_HOOK_TAIL}" session-start'},
                    self.FOREIGN_SESSION_START,
                ]}],
                "Stop": [{"hooks": [{"type": "command", "command": f'bash "{windows_hook}" stop'}]}],
            }}))
            result = run_claude_hook(plugin_root / "skills/session-log", home, "session-start",
                                     payload=json.dumps({"session_id": "cleanup-session", "cwd": directory}))
            self.assertEqual((result.returncode, result.stderr), (0, ""))
            self.assertEqual(json.loads(settings.read_text()), {"hooks": {
                "SessionStart": [{"hooks": [self.FOREIGN_SESSION_START]}],
            }})


OWNER_MARKER = "universal-session-log"
PLUGIN_CACHE_RELATIVE = f".claude/plugins/cache/harness-goodies/claude-goodies/{PLUGIN_VERSION}"
STANDALONE_RELATIVE = ".claude/skills/session-log"
CLAUDE_MANAGED_MATCHERS = {"SessionStart": "startup|resume"}
FOREIGN_HOOK = {"type": "command", "command": "cc-status notify"}
REPEATED_REGISTRATIONS = 3
PLUGIN_KEY = "claude-goodies@harness-goodies"
USER_SCOPE_ENABLED = {"enabledPlugins": {PLUGIN_KEY: True}}
PLUGIN_ROOT_MARKER_RELATIVE = ".claude/session-log/plugin-root"
INSTALL_LOCK_RELATIVE = ".claude/session-log/.install.lock"
PRIVATE_DIRECTORY_MODE = 0o700
EXECUTABLE_MODE = 0o755
NATIVE_HARNESSES = (("codex", ".codex"), ("cursor", ".cursor"))
NATIVE_LABELS = {"codex": "Codex", "cursor": "Cursor"}
# Claude Code writes this into a plugin version directory it has replaced or uninstalled.
ORPHANED_PLUGIN_MARKER = ".orphaned_at"
ORPHANED_AT_MILLISECONDS = "1700000000000"
# Settings.json registrations of a standalone copy that is not installed, so every one of them is dead.
DEAD_REGISTRATIONS = 2


def repair_stamp(home, package):
    """The session-start repair stamp of ``package``: one per copy, named by the `cksum` of its root."""
    checksum = subprocess.run(["cksum"], input=str(package), capture_output=True, text=True, check=True).stdout.split()[0]
    return home / f".claude/session-log/settings-repair.{checksum}.stamp"


def harness_environment(home):
    environment = {key: value for key, value in os.environ.items()
                   if key not in ("CLAUDE_PLUGIN_ROOT", "CLAUDE_CONFIG_DIR", "CODEX_HOME")}
    return {**environment, "HOME": str(home)}


def run_claude_hook(package, home, lifecycle, *, payload="{}", environment=None):
    return subprocess.run(
        ["bash", str(package / "adapters/claude/claude_hook.sh"), lifecycle],
        input=payload, capture_output=True, text=True, env=environment or harness_environment(home),
    )


def run_session_log(package, home, harness, *arguments):
    return subprocess.run(
        ["bash", str(package / "bin/session-log"), "--entrypoint", harness, "--harness", harness, *arguments],
        capture_output=True, text=True, env=harness_environment(home),
    )


def harness_package(home, subroot):
    """A copy of this repository's session-log package in ``<home>/<subroot>/skills/session-log``."""
    package = home / subroot / "skills/session-log"
    package.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(REPO / "skills/session-log", package)
    return package


def standalone_package(home):
    return harness_package(home, ".claude")


def standalone_version():
    """The VERSION of this repository's session-log package, which every test copy carries."""
    return (REPO / "skills/session-log/VERSION").read_text().strip()


def claude_managed_hook(package, lifecycle):
    hook = shlex.quote(str(package / "adapters/claude/claude_hook.sh"))
    return {"type": "command", "command": f"SESSION_LOG_OWNER={OWNER_MARKER} bash {hook} {lifecycle}"}


def claude_managed_entry(package, event):
    entry = {"hooks": [claude_managed_hook(package, PLUGIN_EVENT_LIFECYCLES[event])]}
    if event in CLAUDE_MANAGED_MATCHERS:
        entry["matcher"] = CLAUDE_MANAGED_MATCHERS[event]
    return entry


def claude_hooks_registered(package, times):
    return {event: [claude_managed_entry(package, event) for _ in range(times)] for event in CLAUDE_LIFECYCLE_EVENTS}


class SessionLogRepairCommandTests(unittest.TestCase):
    """`session-log repair` leaves each harness with exactly one current registration per lifecycle event."""

    maxDiff = None

    def test_claude_standalone_repair_collapses_repeated_and_stale_registrations(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            package = standalone_package(home)
            settings = home / ".claude/settings.json"
            hooks = claude_hooks_registered(package, REPEATED_REGISTRATIONS)
            hooks["Stop"].append({"hooks": [claude_managed_hook(home / "old-install", "stop")]})
            hooks["Notification"] = [{"hooks": [FOREIGN_HOOK]}]
            settings.write_text(json.dumps({"model": "opus", "hooks": hooks}))

            first = run_session_log(package, home, "claude", "repair")
            self.assertEqual((first.returncode, first.stderr), (0, ""))
            # Every repeat beyond the first per event, plus the stale old-install Stop hook.
            removed = (REPEATED_REGISTRATIONS - 1) * len(CLAUDE_LIFECYCLE_EVENTS) + 1
            self.assertIn(f"removed {removed} duplicate or stale hook registration(s)", first.stdout)
            expected = {**claude_hooks_registered(package, 1), "Notification": [{"hooks": [FOREIGN_HOOK]}]}
            self.assertEqual(json.loads(settings.read_text()), {"model": "opus", "hooks": expected})

            repaired = settings.read_bytes()
            second = run_session_log(package, home, "claude", "repair")
            self.assertEqual((second.returncode, second.stderr), (0, ""))
            self.assertEqual(second.stdout, "Claude Code: repair found no duplicate or stale registrations\n")
            self.assertEqual(settings.read_bytes(), repaired)

    def test_claude_repair_leaves_unregistered_settings_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            package = standalone_package(home)
            settings = home / ".claude/settings.json"
            settings.write_text(json.dumps({"hooks": {"Notification": [{"hooks": [FOREIGN_HOOK]}]}}))
            original = settings.read_bytes()
            result = run_session_log(package, home, "claude", "repair")
            self.assertEqual((result.returncode, result.stderr), (0, ""))
            self.assertEqual(settings.read_bytes(), original)

    def test_claude_plugin_repair_removes_every_settings_registration_while_enabled_at_user_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            plugin = sandbox_plugin_root(home / PLUGIN_CACHE_RELATIVE) / "skills/session-log"
            # Live standalone registrations go too: the user-scope plugin's hooks serve every project.
            package = standalone_package(home)
            settings = home / ".claude/settings.json"
            hooks = claude_hooks_registered(package, REPEATED_REGISTRATIONS)
            hooks["Notification"] = [{"hooks": [FOREIGN_HOOK]}]
            settings.write_text(json.dumps({**USER_SCOPE_ENABLED, "hooks": hooks}))
            result = run_session_log(plugin, home, "claude", "repair")
            self.assertEqual((result.returncode, result.stderr), (0, ""))
            removed = REPEATED_REGISTRATIONS * len(CLAUDE_LIFECYCLE_EVENTS)
            self.assertEqual(result.stdout, f"Claude Code: removed {removed} duplicate or stale hook registration(s) from {settings}\n")
            self.assertEqual(json.loads(settings.read_text()),
                             {**USER_SCOPE_ENABLED, "hooks": {"Notification": [{"hooks": [FOREIGN_HOOK]}]}})

    def test_claude_repair_removes_owned_registrations_under_unmanaged_events(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            package = standalone_package(home)
            settings = home / ".claude/settings.json"
            hooks = claude_hooks_registered(package, 1)
            hooks["PreCompact"] = [{"hooks": [claude_managed_hook(package, "stop"), FOREIGN_HOOK]}]
            settings.write_text(json.dumps({"hooks": hooks}))

            first = run_session_log(package, home, "claude", "repair")
            self.assertEqual((first.returncode, first.stderr), (0, ""))
            self.assertEqual(first.stdout, f"Claude Code: removed 1 duplicate or stale hook registration(s) from {settings}\n")
            self.assertEqual(json.loads(settings.read_text())["hooks"],
                             {**claude_hooks_registered(package, 1), "PreCompact": [{"hooks": [FOREIGN_HOOK]}]})

            repaired = settings.read_bytes()
            second = run_session_log(package, home, "claude", "repair")
            self.assertEqual((second.returncode, second.stdout, second.stderr),
                             (0, "Claude Code: repair found no duplicate or stale registrations\n", ""))
            self.assertEqual(settings.read_bytes(), repaired)

    def test_standalone_on_registers_nothing_while_the_plugin_hooks_are_active(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            plugin = sandbox_plugin_root(home / PLUGIN_CACHE_RELATIVE) / "skills/session-log"
            package = standalone_package(home)
            settings = home / ".claude/settings.json"
            settings.write_text(json.dumps({**USER_SCOPE_ENABLED, "hooks": claude_hooks_registered(package, 1)}))
            started = run_claude_hook(plugin, home, "session-start")
            self.assertEqual((started.returncode, started.stdout, started.stderr), (0, "", ""))
            self.assertEqual((home / PLUGIN_ROOT_MARKER_RELATIVE).read_text(), str(plugin))
            self.assertEqual(json.loads(settings.read_text()), USER_SCOPE_ENABLED)

            for command in ("on", "status"):
                with self.subTest(command=command):
                    result = run_session_log(package, home, "claude", command)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(settings.read_text()), USER_SCOPE_ENABLED)
            self.assertTrue((home / ".claude/prompt-logs/.enabled").is_file())

    def test_standalone_on_registers_hooks_once_the_plugin_is_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            plugin = sandbox_plugin_root(home / PLUGIN_CACHE_RELATIVE) / "skills/session-log"
            package = standalone_package(home)
            (home / ".claude/session-log").mkdir(parents=True)
            (home / PLUGIN_ROOT_MARKER_RELATIVE).write_text(str(plugin))
            settings = home / ".claude/settings.json"
            settings.write_text(json.dumps({"enabledPlugins": {PLUGIN_KEY: False}}))
            result = run_session_log(package, home, "claude", "on")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(settings.read_text())["hooks"], claude_hooks_registered(package, 1))

    def test_standalone_on_collapses_repeated_registrations(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            package = standalone_package(home)
            settings = home / ".claude/settings.json"
            installed = run_session_log(package, home, "claude", "on")
            self.assertEqual(installed.returncode, 0, installed.stderr)
            # The adapter is now current, which an existing registration still satisfies when repeated.
            settings.write_text(json.dumps({"hooks": claude_hooks_registered(package, REPEATED_REGISTRATIONS)}))
            result = run_session_log(package, home, "claude", "on")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(settings.read_text())["hooks"], claude_hooks_registered(package, 1))

    def test_standalone_session_start_collapses_repeated_registrations(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            package = standalone_package(home)
            settings = home / ".claude/settings.json"
            settings.write_text(json.dumps({"hooks": claude_hooks_registered(package, REPEATED_REGISTRATIONS)}))
            result = run_claude_hook(package, home, "session-start")
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
            self.assertEqual(json.loads(settings.read_text())["hooks"], claude_hooks_registered(package, 1))

    def test_session_start_skips_settings_without_session_log_hooks(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            package = standalone_package(home)
            settings = home / ".claude/settings.json"
            settings.write_text(json.dumps({"hooks": {"Notification": [{"hooks": [FOREIGN_HOOK]}]}}, indent=2))
            original = settings.read_bytes()
            result = run_claude_hook(package, home, "session-start")
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
            self.assertEqual(settings.read_bytes(), original)
            # The grep gate kept the repair command from running: it would have created the state directory.
            self.assertFalse((home / ".claude/session-log").exists())

    def native_hooks(self, harness, hook, times):
        installer = load_module(INSTALL_PATH, f"install_hooks_repair_{harness}")
        document = {"hooks": {}}
        installer.update(document, harness, hook)
        for event, entries in document["hooks"].items():
            document["hooks"][event] = entries * times
        return document

    def test_native_repair_collapses_repeated_and_windows_path_registrations(self):
        for harness, subroot in NATIVE_HARNESSES:
            with self.subTest(harness=harness), tempfile.TemporaryDirectory() as directory:
                home = Path(directory).resolve()
                package = harness_package(home, subroot)
                hook = package / "adapters/native/session_log_hook.py"
                document = self.native_hooks(harness, hook, REPEATED_REGISTRATIONS)
                windows_hook = Path(r"C:\Users\Jane Doe\skills\session-log\adapters\native\session_log_hook.py")
                first_event = next(iter(document["hooks"]))
                stale = self.native_hooks(harness, windows_hook, 1)["hooks"][first_event]
                document["hooks"][first_event] += stale
                config = home / subroot / "hooks.json"
                config.write_text(json.dumps(document))
                expected = self.native_hooks(harness, hook, 1)

                first = run_session_log(package, home, harness, "repair")
                self.assertEqual((first.returncode, first.stderr), (0, ""))
                removed = (REPEATED_REGISTRATIONS - 1) * len(document["hooks"]) + 1
                self.assertIn(f"removed {removed} duplicate or stale hook registration(s)", first.stdout)
                self.assertEqual(json.loads(config.read_text()), expected)

                repaired = config.read_bytes()
                second = run_session_log(package, home, harness, "repair")
                self.assertEqual((second.returncode, second.stderr), (0, ""))
                self.assertIn("repair found no duplicate or stale registrations", second.stdout)
                self.assertEqual(config.read_bytes(), repaired)


class PluginCopyWithoutUserScopeTests(unittest.TestCase):
    """A plugin copy not enabled in user-scope settings removes only dead settings.json registrations.

    Its plugin may be enabled for one project only, so a live standalone copy's hooks keep serving the rest.
    """

    maxDiff = None
    NOT_ENABLED_AT_USER_SCOPE = {"key absent": {}, "key false": {"enabledPlugins": {PLUGIN_KEY: False}}}

    def dead_hooks(self, home):
        """A stale install path, an unsubstituted copy of the plugin's command, a removed cache version, and a
        replaced cache version whose script still exists until Claude Code deletes the orphaned directory."""
        removed_cache = home / PLUGIN_CACHE_RELATIVE.replace(PLUGIN_VERSION, REMOVED_PLUGIN_VERSION) / "skills/session-log"
        orphaned_cache = home / PLUGIN_CACHE_RELATIVE.replace(PLUGIN_VERSION, PREVIOUS_PLUGIN_VERSION) / "skills/session-log"
        orphaned_script = orphaned_cache / "adapters/claude/claude_hook.sh"
        orphaned_script.parent.mkdir(parents=True, exist_ok=True)
        orphaned_script.write_text("")
        return [
            claude_managed_hook(home / "old-install", "stop"),
            command_hook(f'exec bash "{PLUGIN_ROOT_PLACEHOLDER}/{PLUGIN_HOOK_TAIL}" stop'),
            claude_managed_hook(removed_cache, "stop"),
            claude_managed_hook(orphaned_cache, "stop"),
        ]

    def prepare(self, directory, document):
        """Settings holding a live standalone set plus dead registrations; returns what must survive."""
        home = Path(directory).resolve()
        plugin = sandbox_plugin_root(home / PLUGIN_CACHE_RELATIVE) / "skills/session-log"
        package = standalone_package(home)
        live = {**document, "hooks": {**claude_hooks_registered(package, 1), "Notification": [{"hooks": [FOREIGN_HOOK]}]}}
        dirty = json.loads(json.dumps(live))
        dirty["hooks"]["Stop"].append({"hooks": self.dead_hooks(home)})
        settings = home / ".claude/settings.json"
        settings.write_text(json.dumps(dirty))
        return home, plugin, settings, live

    def test_repair_removes_only_dead_registrations(self):
        for name, document in self.NOT_ENABLED_AT_USER_SCOPE.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as directory:
                home, plugin, settings, live = self.prepare(directory, document)
                result = run_session_log(plugin, home, "claude", "repair")
                self.assertEqual((result.returncode, result.stderr), (0, ""))
                removed = len(self.dead_hooks(home))
                self.assertEqual(result.stdout, f"Claude Code: removed {removed} duplicate or stale hook registration(s) from {settings}\n")
                self.assertEqual(json.loads(settings.read_text()), live)
                again = run_session_log(plugin, home, "claude", "repair")
                self.assertEqual((again.returncode, again.stdout, again.stderr),
                                 (0, "Claude Code: repair found no duplicate or stale registrations\n", ""))

    def test_session_start_on_and_off_keep_live_registrations_and_inject_none(self):
        for command in ("session-start", "on", "status", "off"):
            with self.subTest(command), tempfile.TemporaryDirectory() as directory:
                home, plugin, settings, live = self.prepare(directory, {})
                if command == "session-start":
                    result = run_claude_hook(plugin, home, command)
                    self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
                else:
                    (home / ".claude/prompt-logs").mkdir(parents=True)
                    (home / ".claude/prompt-logs/.enabled").write_text("")
                    result = run_session_log(plugin, home, "claude", command)
                    self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(settings.read_text()), live)


def write_plugin_root_marker(home, content):
    marker = home / PLUGIN_ROOT_MARKER_RELATIVE
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(content)
    return marker


def process_start(pid):
    """The `ps -o lstart=` start time session-log records for liveness checks; skips where ps lacks that field."""
    result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="], capture_output=True, text=True)
    start = result.stdout.strip()
    if result.returncode != 0 or not start:
        raise unittest.SkipTest("ps does not support -o lstart=")
    return start


def hold_install_lock(home, harness_subroot=".claude"):
    """Record this live test process as the owner of the harness install lock."""
    start = process_start(os.getpid())
    lock = home / harness_subroot / Path(INSTALL_LOCK_RELATIVE).relative_to(".claude")
    lock.mkdir(parents=True)
    (lock / "owner").write_text(f"{os.getpid()}\n{start}\n")


class StandalonePluginDeferralTests(unittest.TestCase):
    """A standalone copy defers to the plugin only for a live plugin-cache root enabled in user-scope settings."""

    maxDiff = None

    def standalone_on(self, prepare, settings_document=USER_SCOPE_ENABLED):
        """Run the standalone `on` after ``prepare(home, plugin)``; returns the hooks it registered, if any."""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            plugin = sandbox_plugin_root(home / PLUGIN_CACHE_RELATIVE) / "skills/session-log"
            package = standalone_package(home)
            settings = home / ".claude/settings.json"
            settings.write_text(json.dumps(settings_document))
            prepare(home, plugin)
            result = run_session_log(package, home, "claude", "on")
            self.assertEqual(result.returncode, 0, result.stderr)
            hooks = json.loads(settings.read_text()).get("hooks")
            if hooks is not None:
                self.assertEqual(hooks, claude_hooks_registered(package, 1))
            return hooks

    def test_defers_to_a_live_plugin_enabled_in_user_settings(self):
        self.assertIsNone(self.standalone_on(lambda home, plugin: write_plugin_root_marker(home, str(plugin))))

    def test_defers_to_a_plugin_whose_hooks_json_differs_from_this_copy(self):
        def prepare(home, plugin):
            write_plugin_root_marker(home, str(plugin))
            (plugin.parents[1] / "hooks/hooks.json").write_text(json.dumps({"hooks": {}}))
        self.assertIsNone(self.standalone_on(prepare))

    PLUGIN_PACKAGE_VERSION = f"{PLUGIN_VERSION}-plugin"
    RESTART_REQUIRED = "Claude Code: on — restart required\n"

    def deferring_status(self, runtime_version, prepare_plugin=lambda plugin: None, command="status"):
        """``command``'s result for a standalone copy deferring to the plugin, once runtime.json records
        ``runtime_version`` (the plugin package's VERSION when None)."""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            plugin = sandbox_plugin_root(home / PLUGIN_CACHE_RELATIVE) / "skills/session-log"
            (plugin / "VERSION").write_text(f"{self.PLUGIN_PACKAGE_VERSION}\n")
            prepare_plugin(plugin)
            package = standalone_package(home)
            (home / ".claude/settings.json").write_text(json.dumps(USER_SCOPE_ENABLED))
            write_plugin_root_marker(home, str(plugin))
            enabled = run_session_log(package, home, "claude", "on")
            self.assertEqual(enabled.returncode, 0, enabled.stderr)
            # What the active hooks write: the plugin's hooks record their own package VERSION, not this copy's.
            (home / ".claude/session-log/runtime.json").write_text(json.dumps({
                "harness": "claude", "version": runtime_version or self.PLUGIN_PACKAGE_VERSION,
                "session_id": "plugin-session", "nonce": "plugin-session", "loaded_at": str(int(time.time())),
                "pid": str(os.getpid()), "process_start": process_start(os.getpid()),
            }))
            result = run_session_log(package, home, "claude", command)
            self.assertEqual((result.returncode, result.stderr), (0, ""))
            return result.stdout

    def test_status_accepts_the_runtime_marker_the_plugin_hooks_record(self):
        self.assertEqual(self.deferring_status(None), "Claude Code: on\n")

    def test_on_after_a_plugin_session_reports_on(self):
        self.assertEqual(self.deferring_status(None, command="on"), "Claude Code: on\n")

    def test_status_rejects_this_copys_version_while_deferring(self):
        self.assertEqual(self.deferring_status(standalone_version()), self.RESTART_REQUIRED)

    def test_status_expects_this_copys_version_when_the_plugin_has_none(self):
        def symlinked_version(plugin):
            (plugin / "VERSION").unlink()
            (plugin / "VERSION").symlink_to(plugin / "SKILL.md")

        for name, prepare in {"missing": lambda plugin: (plugin / "VERSION").unlink(), "symlinked": symlinked_version}.items():
            with self.subTest(name):
                self.assertEqual(self.deferring_status(standalone_version(), prepare), "Claude Code: on\n")

    def test_each_command_resolves_the_claude_settings_mode_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            home = root / "home"
            plugin = sandbox_plugin_root(home / PLUGIN_CACHE_RELATIVE) / "skills/session-log"
            package = standalone_package(home)
            (home / ".claude/settings.json").write_text(json.dumps(USER_SCOPE_ENABLED))
            write_plugin_root_marker(home, str(plugin))
            shims = root / "shims"
            shims.mkdir()
            (shims / "python3").write_text(
                f'#!/bin/sh\nprintf "%s\\n" "$*" >> "$SESSION_LOG_TEST_RECORD"\nexec "{sys.executable}" "$@"\n')
            (shims / "python3").chmod(EXECUTABLE_MODE)
            record = root / "record"
            environment = {**harness_environment(home), "PATH": f"{shims}{os.pathsep}{os.environ['PATH']}",
                           "SESSION_LOG_TEST_RECORD": str(record)}
            # Each copy's mode check: the plugin copy reads its hooks.json, and both read enabledPlugins.
            checks = {"standalone": (package, ("plugin-enabled",)), "plugin": (plugin, ("plugin-hooks-current", "plugin-enabled"))}
            for name, (copy, operations) in checks.items():
                for command in ("on", "status", "repair", "off"):
                    with self.subTest(copy=name, command=command):
                        record.unlink(missing_ok=True)
                        result = subprocess.run(
                            ["bash", str(copy / "bin/session-log"), "--entrypoint", "claude", "--harness", "claude", command],
                            capture_output=True, text=True, env=environment)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        calls = record.read_text().splitlines()
                        for operation in operations:
                            self.assertEqual(sum(call.endswith(f"claude_settings.py {operation}") for call in calls), 1, calls)

    def test_registers_its_hooks_unless_the_plugin_is_enabled_in_user_settings(self):
        documents = {
            "key absent": {},
            "key false": {"enabledPlugins": {PLUGIN_KEY: False}},
            "key not a boolean": {"enabledPlugins": {PLUGIN_KEY: "true"}},
            "enabledPlugins not an object": {"enabledPlugins": [PLUGIN_KEY]},
        }
        for name, document in documents.items():
            with self.subTest(name):
                hooks = self.standalone_on(lambda home, plugin: write_plugin_root_marker(home, str(plugin)), document)
                self.assertIsNotNone(hooks)

    def test_registers_its_hooks_when_the_marker_names_no_live_plugin(self):
        def orphaned(home, plugin):
            write_plugin_root_marker(home, str(plugin))
            (plugin.parents[1] / ORPHANED_PLUGIN_MARKER).write_text(ORPHANED_AT_MILLISECONDS)

        def orphaned_by_dangling_link(home, plugin):
            write_plugin_root_marker(home, str(plugin))
            (plugin.parents[1] / ORPHANED_PLUGIN_MARKER).symlink_to(home / "nowhere")

        def symlinked(home, plugin):
            target = home / "elsewhere"
            target.write_text(str(plugin))
            marker = home / PLUGIN_ROOT_MARKER_RELATIVE
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.symlink_to(target)

        def removed(home, plugin):
            write_plugin_root_marker(home, str(plugin).replace(PLUGIN_VERSION, REMOVED_PLUGIN_VERSION))

        cases = {
            "no marker": lambda home, plugin: None,
            "orphaned plugin version": orphaned,
            "orphaned plugin version marked by a dangling link": orphaned_by_dangling_link,
            "symlinked marker": symlinked,
            "dot-dot component": lambda home, plugin: write_plugin_root_marker(
                home, str(plugin).replace(f"/{PLUGIN_VERSION}/", f"/{PLUGIN_VERSION}/../{PLUGIN_VERSION}/")),
            "not a plugin-cache path": lambda home, plugin: write_plugin_root_marker(home, str(home / STANDALONE_RELATIVE)),
            "plugin version directory removed": removed,
        }
        for name, prepare in cases.items():
            with self.subTest(name):
                self.assertIsNotNone(self.standalone_on(prepare))


class PluginRootMarkerTests(unittest.TestCase):
    """The plugin's SessionStart records its package root for standalone copies, refusing unsafe marker paths."""

    def plugin_session_start(self, home, environment=None):
        plugin = sandbox_plugin_root(home / PLUGIN_CACHE_RELATIVE) / "skills/session-log"
        result = run_claude_hook(plugin, home, "session-start", environment=environment)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        return plugin

    def test_unchanged_marker_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            plugin = home / PLUGIN_CACHE_RELATIVE / "skills/session-log"
            marker = write_plugin_root_marker(home, str(plugin))
            before = marker.stat()
            self.plugin_session_start(home)
            self.assertEqual((marker.stat().st_ino, marker.stat().st_mtime_ns), (before.st_ino, before.st_mtime_ns))

    def test_marker_follows_a_plugin_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            marker = write_plugin_root_marker(home, str(home / PLUGIN_CACHE_RELATIVE).replace(PLUGIN_VERSION, PREVIOUS_PLUGIN_VERSION))
            plugin = self.plugin_session_start(home)
            self.assertEqual(marker.read_text(), str(plugin))

    def test_marker_state_directory_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            self.plugin_session_start(home)
            self.assertEqual(stat.S_IMODE((home / PLUGIN_ROOT_MARKER_RELATIVE).parent.stat().st_mode), PRIVATE_DIRECTORY_MODE)

    def test_symlinked_or_directory_marker_is_left_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            marker = home / PLUGIN_ROOT_MARKER_RELATIVE
            marker.parent.mkdir(parents=True)
            target = home / "target"
            target.write_text("unrelated")
            marker.symlink_to(target)
            self.plugin_session_start(home)
            self.assertTrue(marker.is_symlink())
            self.assertEqual(target.read_text(), "unrelated")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            marker = home / PLUGIN_ROOT_MARKER_RELATIVE
            marker.mkdir(parents=True)
            self.plugin_session_start(home)
            self.assertTrue(marker.is_dir())

    def test_failed_marker_write_leaves_no_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            home = root / "home"
            tools = link_tools(root / "bin", tuple(tool for tool in GIT_BASH_TOOLS_WITHOUT_JQ if tool != "mv"))
            record = root / "mv-called"
            (tools / "mv").write_text('#!/bin/sh\nprintf called > "$SESSION_LOG_TEST_RECORD"\nexit 1\n')
            (tools / "mv").chmod(EXECUTABLE_MODE)
            self.plugin_session_start(
                home, environment={"HOME": str(home), "PATH": str(tools), "SESSION_LOG_TEST_RECORD": str(record)})
            self.assertTrue(record.exists(), "the failing mv was never reached")
            state = home / ".claude/session-log"
            self.assertEqual(sorted(path.name for path in state.iterdir()), [])


RECORDING_SESSION_LOG = '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$SESSION_LOG_TEST_RECORD"\n'


class SessionStartRepairStampTests(unittest.TestCase):
    """Session start repairs settings.json once per change, however the file spells the registered paths."""

    def test_session_start_repairs_once_per_settings_change(self):
        variants = {
            "minified": ("home", lambda document: json.dumps(document, separators=(",", ":"))),
            "non-ASCII home escaped by json.dump": ("Jöhn Dœ", json.dumps),
            "shell-quoted home": ("Jane O'Doe", lambda document: json.dumps(document, indent=2)),
        }
        for name, (home_name, encode) in variants.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as directory:
                home = Path(directory).resolve() / home_name
                package = standalone_package(home)
                settings = home / ".claude/settings.json"
                settings.write_text(encode({"hooks": claude_hooks_registered(package, 1)}))
                original = settings.read_bytes()
                record = Path(directory) / "record"
                environment = {**harness_environment(home), "SESSION_LOG_TEST_RECORD": str(record)}

                first = run_claude_hook(package, home, "session-start", environment=environment)
                self.assertEqual((first.returncode, first.stdout, first.stderr), (0, "", ""))
                self.assertEqual(settings.read_bytes(), original)
                self.assertTrue(repair_stamp(home, package).is_file())

                (package / "bin/session-log").write_text(RECORDING_SESSION_LOG)
                run_claude_hook(package, home, "session-start", environment=environment)
                self.assertFalse(record.exists(), "an unchanged settings.json was repaired again")

                hooks = claude_hooks_registered(package, 1)
                hooks["Stop"] *= REPEATED_REGISTRATIONS
                settings.write_text(encode({"hooks": hooks}))
                run_claude_hook(package, home, "session-start", environment=environment)
                self.assertEqual(record.read_text(), "--entrypoint claude --harness claude repair\n")

    def dirty_standalone(self, home):
        package = standalone_package(home)
        settings = home / ".claude/settings.json"
        settings.write_text(json.dumps({"hooks": claude_hooks_registered(package, REPEATED_REGISTRATIONS)}))
        return package, settings

    def assert_repaired(self, package, home, settings):
        result = run_claude_hook(package, home, "session-start")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertEqual(json.loads(settings.read_text())["hooks"], claude_hooks_registered(package, 1))
        self.assertFalse((home / INSTALL_LOCK_RELATIVE).exists(), "session-start left the install lock behind")

    def test_session_start_repairs_dirty_settings_then_skips_them(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            package, settings = self.dirty_standalone(home)
            self.assert_repaired(package, home, settings)
            self.assertTrue(repair_stamp(home, package).is_file())
            record = Path(directory) / "record"
            (package / "bin/session-log").write_text(RECORDING_SESSION_LOG)
            run_claude_hook(package, home, "session-start",
                            environment={**harness_environment(home), "SESSION_LOG_TEST_RECORD": str(record)})
            self.assertFalse(record.exists(), "the repaired settings.json was repaired again")

    def change_version(self, home, package):
        (package / "VERSION").write_text(f"{PLUGIN_VERSION}-changed\n")
        return package

    def change_plugin_marker(self, home, package):
        write_plugin_root_marker(home, str(home / PLUGIN_CACHE_RELATIVE / "skills/session-log"))
        return package

    def change_package_location(self, home, package):
        moved = harness_package(home, "elsewhere")
        (moved / "bin/session-log").write_text(RECORDING_SESSION_LOG)
        return moved

    def test_session_start_repairs_again_when_the_package_or_plugin_marker_changes(self):
        for change in (self.change_version, self.change_plugin_marker, self.change_package_location):
            with self.subTest(change.__name__), tempfile.TemporaryDirectory() as directory:
                home = Path(directory).resolve()
                package = standalone_package(home)
                (home / ".claude/settings.json").write_text(json.dumps({"hooks": claude_hooks_registered(package, 1)}))
                first = run_claude_hook(package, home, "session-start")
                self.assertEqual((first.returncode, first.stdout, first.stderr), (0, "", ""))
                self.assertTrue(repair_stamp(home, package).is_file())
                (package / "bin/session-log").write_text(RECORDING_SESSION_LOG)
                record = Path(directory) / "record"
                runner = change(home, package)
                run_claude_hook(runner, home, "session-start",
                                environment={**harness_environment(home), "SESSION_LOG_TEST_RECORD": str(record)})
                self.assertEqual(record.read_text() if record.exists() else None,
                                 "--entrypoint claude --harness claude repair\n")

    def test_session_start_repairs_again_when_a_registered_hook_script_disappears(self):
        for home_name in ("home", "Jane Doe"):
            with self.subTest(home_name), tempfile.TemporaryDirectory() as directory:
                home = Path(directory).resolve() / home_name
                # A plugin copy not enabled at user scope keeps the live standalone set until its script is gone.
                plugin = sandbox_plugin_root(home / PLUGIN_CACHE_RELATIVE) / "skills/session-log"
                package = standalone_package(home)
                settings = home / ".claude/settings.json"
                settings.write_text(json.dumps({"hooks": claude_hooks_registered(package, 1)}))
                original = settings.read_bytes()
                first = run_claude_hook(plugin, home, "session-start")
                self.assertEqual((first.returncode, first.stdout, first.stderr), (0, "", ""))
                self.assertEqual(settings.read_bytes(), original)
                self.assertTrue(repair_stamp(home, plugin).is_file())

                shutil.rmtree(package)
                second = run_claude_hook(plugin, home, "session-start")
                self.assertEqual((second.returncode, second.stdout, second.stderr), (0, "", ""))
                self.assertEqual(json.loads(settings.read_text()), {})

    def test_plugin_and_standalone_copies_keep_separate_stamps(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            plugin = sandbox_plugin_root(home / PLUGIN_CACHE_RELATIVE) / "skills/session-log"
            package = standalone_package(home)
            (home / ".claude/settings.json").write_text(json.dumps({"hooks": claude_hooks_registered(package, 1)}))
            copies = (plugin, package)
            for copy in copies:
                result = run_claude_hook(copy, home, "session-start")
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
            record = Path(directory) / "record"
            environment = {**harness_environment(home), "SESSION_LOG_TEST_RECORD": str(record)}
            for copy in copies:
                (copy / "bin/session-log").write_text(RECORDING_SESSION_LOG)
                run_claude_hook(copy, home, "session-start", environment=environment)
            self.assertFalse(record.exists(), "a copy repaired settings.json again after the other copy's session start")

    def test_unsafe_stamp_path_never_skips_the_repair(self):
        for kind in ("symlink", "directory"):
            with self.subTest(kind), tempfile.TemporaryDirectory() as directory:
                home = Path(directory).resolve()
                package, settings = self.dirty_standalone(home)
                stamp = repair_stamp(home, package)
                stamp.parent.mkdir(parents=True)
                target = home / "stamp-target"
                if kind == "symlink":
                    target.write_text("unrelated")
                    stamp.symlink_to(target)
                else:
                    stamp.mkdir()
                self.assert_repaired(package, home, settings)
                self.assertTrue(stamp.is_symlink() if kind == "symlink" else stamp.is_dir())
                if kind == "symlink":
                    self.assertEqual(target.read_text(), "unrelated")

    def test_failed_repair_leaves_no_stamp_and_the_next_session_repairs(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            package, settings = self.dirty_standalone(home)
            original = settings.read_bytes()
            hold_install_lock(home)
            result = run_claude_hook(package, home, "session-start")
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
            self.assertEqual(settings.read_bytes(), original)
            self.assertFalse(repair_stamp(home, package).exists())

            shutil.rmtree(home / INSTALL_LOCK_RELATIVE)
            self.assert_repaired(package, home, settings)
            self.assertTrue(repair_stamp(home, package).is_file())

    def test_jq_less_plugin_session_start_repairs_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            home = root / "home"
            plugin = sandbox_plugin_root(home / PLUGIN_CACHE_RELATIVE) / "skills/session-log"
            settings = home / ".claude/settings.json"
            settings.write_text(json.dumps({"hooks": {
                **claude_hooks_registered(home / STANDALONE_RELATIVE, REPEATED_REGISTRATIONS),
                "Notification": [{"hooks": [FOREIGN_HOOK]}],
            }}))
            tools = link_tools(root / "git-bash-bin", GIT_BASH_TOOLS_WITHOUT_JQ)
            result = run_claude_hook(plugin, home, "session-start",
                                     environment={"HOME": str(home), "PATH": str(tools)})
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
            self.assertEqual(json.loads(settings.read_text()), {"hooks": {"Notification": [{"hooks": [FOREIGN_HOOK]}]}})


# A hook payload naming a session, so a hook that got past its safety checks would record a runtime marker.
SESSION_PAYLOAD = json.dumps({"session_id": "silent-session"})
# A lifecycle script that writes hook output, then fails loudly the way prompt_log_save.sh does.
LIFECYCLE_SCRIPT_STDOUT = "hook output"
FAILING_LIFECYCLE_SCRIPT = f'#!/usr/bin/env bash\nprintf "{LIFECYCLE_SCRIPT_STDOUT}"\necho "session-log: failed" >&2\nexit 1\n'


class ClaudeHookSilentFailureTests(unittest.TestCase):
    """A Claude hook never reports an error: every failure exits 0 with no output and no side effect."""

    def enabled_home(self, directory):
        home = Path(directory).resolve()
        package = standalone_package(home)
        (home / ".claude/prompt-logs").mkdir(parents=True)
        (home / ".claude/prompt-logs/.enabled").write_text("")
        return home, package

    def assert_silent(self, result, home=None):
        """Exit 0 with no output; given ``home``, also no runtime marker or transcript written under it."""
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        if home is not None:
            written = [path for path in Path(home).rglob("*")
                       if path.name.startswith("runtime") or (path.suffix == ".md" and "prompt-logs" in path.parts)]
            self.assertEqual(written, [])

    def test_unsafe_claude_root_exits_silently(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            package = standalone_package(home / "package-home")
            (home / ".claude").write_text("not a directory")
            self.assert_silent(run_claude_hook(package, home, "user-prompt"))

    def test_symlinked_claude_paths_exit_silently(self):
        for relative in (".claude", ".claude/prompt-logs", ".claude/session-log"):
            with self.subTest(relative), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                home = root / "home"
                package = standalone_package(root / "package-home")
                target = root / "real"
                target.mkdir()
                link = home / relative
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(target)
                (home / ".claude/prompt-logs").mkdir(parents=True, exist_ok=True)
                (home / ".claude/prompt-logs/.enabled").write_text("")
                self.assert_silent(run_claude_hook(package, home, "session-start", payload=json.dumps({"session_id": "s"})),
                                   root)

    def test_unsafe_per_process_runtime_file_exits_silently(self):
        with tempfile.TemporaryDirectory() as directory:
            home, package = self.enabled_home(directory)
            # The hook's parent process is this test process, so this is its runtime.$PPID.json.
            process_runtime = home / f".claude/session-log/runtime.{os.getpid()}.json"
            process_runtime.parent.mkdir(parents=True)
            process_runtime.symlink_to(home / "elsewhere")
            self.assert_silent(run_claude_hook(package, home, "user-prompt", payload=json.dumps({"session_id": "s"})))
            self.assertTrue(process_runtime.is_symlink())
            self.assertFalse((home / ".claude/session-log/runtime.json").exists())

    def test_missing_package_version_exits_silently(self):
        with tempfile.TemporaryDirectory() as directory:
            home, package = self.enabled_home(directory)
            (package / "VERSION").unlink()
            self.assert_silent(run_claude_hook(package, home, "session-start", payload=SESSION_PAYLOAD), home)

    def test_failing_lifecycle_script_keeps_its_stdout_and_exits_silently(self):
        if shutil.which("jq") is None:
            self.skipTest("jq is not installed")
        with tempfile.TemporaryDirectory() as directory:
            home, package = self.enabled_home(directory)
            (package / "adapters/claude/scripts/prompt_log_save.sh").write_text(FAILING_LIFECYCLE_SCRIPT)
            result = run_claude_hook(package, home, "user-prompt", payload=json.dumps({"session_id": "s"}))
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, LIFECYCLE_SCRIPT_STDOUT, ""))

    def test_relocated_claude_root_exits_silently(self):
        with tempfile.TemporaryDirectory() as directory:
            home, package = self.enabled_home(directory)
            (home / "relocated").mkdir()
            environment = {**harness_environment(home), "CLAUDE_CONFIG_DIR": str(home / "relocated")}
            self.assert_silent(run_claude_hook(package, home, "user-prompt", payload=SESSION_PAYLOAD,
                                               environment=environment), home)

    def test_missing_jq_while_logging_is_on_exits_silently(self):
        with tempfile.TemporaryDirectory() as directory:
            home, package = self.enabled_home(directory)
            tools = link_tools(home / "git-bash-bin", GIT_BASH_TOOLS_WITHOUT_JQ)
            result = run_claude_hook(package, home, "user-prompt", payload=json.dumps({"session_id": "s"}),
                                     environment={"HOME": str(home), "PATH": str(tools)})
            self.assert_silent(result)
            self.assertFalse((home / ".claude/session-log/runtime.json").exists())

    def test_unsafe_runtime_file_exits_silently(self):
        with tempfile.TemporaryDirectory() as directory:
            home, package = self.enabled_home(directory)
            runtime = home / ".claude/session-log/runtime.json"
            runtime.parent.mkdir(parents=True)
            runtime.symlink_to(home / "elsewhere")
            self.assert_silent(run_claude_hook(package, home, "user-prompt", payload=SESSION_PAYLOAD))
            self.assertTrue(runtime.is_symlink())
            self.assertFalse((home / "elsewhere").exists())
            self.assertEqual([path.name for path in runtime.parent.iterdir()], [runtime.name])

    def test_unknown_lifecycle_exits_silently(self):
        with tempfile.TemporaryDirectory() as directory:
            home, package = self.enabled_home(directory)
            self.assert_silent(run_claude_hook(package, home, "bogus-event"), home)

    def test_missing_lifecycle_script_exits_silently(self):
        with tempfile.TemporaryDirectory() as directory:
            home, package = self.enabled_home(directory)
            (package / "adapters/claude/scripts/prompt_log_save.sh").unlink()
            self.assert_silent(run_claude_hook(package, home, "user-prompt"), home)


class RepairCommandEdgeTests(unittest.TestCase):
    """`repair` reports every change it makes and refuses unsafe or invalid files without touching them."""

    maxDiff = None

    def test_claude_repair_restores_a_partial_managed_set(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            package = standalone_package(home)
            settings = home / ".claude/settings.json"
            hooks = claude_hooks_registered(package, 1)
            missing = ("SessionStart", "UserPromptSubmit")
            for event in missing:
                del hooks[event]
            hooks["Stop"] *= REPEATED_REGISTRATIONS
            settings.write_text(json.dumps({"hooks": hooks}))
            result = run_session_log(package, home, "claude", "repair")
            self.assertEqual((result.returncode, result.stderr), (0, ""))
            self.assertEqual(result.stdout, (
                f"Claude Code: removed {REPEATED_REGISTRATIONS - 1} duplicate or stale hook registration(s) from {settings}\n"
                f"Claude Code: restored {len(missing)} missing hook registration(s) in {settings}\n"
            ))
            self.assertEqual(json.loads(settings.read_text())["hooks"], claude_hooks_registered(package, 1))

    def test_native_repair_restores_a_partial_managed_set(self):
        for harness, subroot in NATIVE_HARNESSES:
            with self.subTest(harness=harness), tempfile.TemporaryDirectory() as directory:
                home = Path(directory).resolve()
                package = harness_package(home, subroot)
                installer = load_module(INSTALL_PATH, f"install_hooks_partial_{harness}")
                document = {"hooks": {}}
                installer.update(document, harness, package / "adapters/native/session_log_hook.py")
                expected = json.loads(json.dumps(document))
                removed_event, repeated_event = list(document["hooks"])[:2]
                del document["hooks"][removed_event]
                document["hooks"][repeated_event] *= REPEATED_REGISTRATIONS
                config = home / subroot / "hooks.json"
                config.write_text(json.dumps(document))
                result = run_session_log(package, home, harness, "repair")
                self.assertEqual((result.returncode, result.stderr), (0, ""))
                label = NATIVE_LABELS[harness]
                follow_up = "; review and trust them in Codex, then restart" if harness == "codex" else ""
                self.assertEqual(result.stdout, (
                    f"{label}: removed {REPEATED_REGISTRATIONS - 1} duplicate or stale hook registration(s) from {config}\n"
                    f"{label}: restored 1 missing hook registration(s) in {config}{follow_up}\n"
                ))
                self.assertEqual(json.loads(config.read_text()), expected)

    def assert_refused(self, result, path, original):
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
        self.assertTrue(result.stderr.startswith("session-log: "), result.stderr)
        self.assertEqual(path.read_bytes(), original)

    def invalid_documents(self, events_key):
        return {
            "malformed JSON": "{not json",
            "root not an object": "[1]",
            "hooks not an object": json.dumps({"hooks": []}),
            "event not an array": json.dumps({"hooks": {events_key: {}}}),
        }

    def test_claude_repair_refuses_invalid_settings(self):
        for name, contents in self.invalid_documents("Stop").items():
            with self.subTest(name), tempfile.TemporaryDirectory() as directory:
                home = Path(directory).resolve()
                package = standalone_package(home)
                settings = home / ".claude/settings.json"
                settings.write_text(contents)
                self.assert_refused(run_session_log(package, home, "claude", "repair"), settings, contents.encode())

    def test_native_repair_refuses_invalid_hooks_files(self):
        for (harness, subroot), events_key in zip(NATIVE_HARNESSES, ("Stop", "stop")):
            for name, contents in self.invalid_documents(events_key).items():
                with self.subTest(harness=harness, case=name), tempfile.TemporaryDirectory() as directory:
                    home = Path(directory).resolve()
                    package = harness_package(home, subroot)
                    config = home / subroot / "hooks.json"
                    config.write_text(contents)
                    self.assert_refused(run_session_log(package, home, harness, "repair"), config, contents.encode())

    def test_repair_refuses_symlinked_files(self):
        for harness, subroot, name in (("claude", ".claude", "settings.json"),
                                       *((harness, subroot, "hooks.json") for harness, subroot in NATIVE_HARNESSES)):
            with self.subTest(harness=harness), tempfile.TemporaryDirectory() as directory:
                home = Path(directory).resolve()
                package = harness_package(home, subroot)
                target = home / "real.json"
                target.write_text(json.dumps({"hooks": claude_hooks_registered(package, REPEATED_REGISTRATIONS)}))
                (home / subroot / name).symlink_to(target)
                original = target.read_bytes()
                self.assert_refused(run_session_log(package, home, harness, "repair"), target, original)
                self.assertTrue((home / subroot / name).is_symlink())

    def test_repair_refuses_a_missing_hook_asset(self):
        cases = (("claude", ".claude", "settings.json", "adapters/claude/claude_hook.sh"),
                 ("codex", ".codex", "hooks.json", "adapters/native/session_log_hook.py"))
        for harness, subroot, name, asset in cases:
            with self.subTest(harness=harness), tempfile.TemporaryDirectory() as directory:
                home = Path(directory).resolve()
                package = harness_package(home, subroot)
                config = home / subroot / name
                config.write_text(json.dumps({"hooks": claude_hooks_registered(package, REPEATED_REGISTRATIONS)}))
                original = config.read_bytes()
                (package / asset).unlink()
                self.assert_refused(run_session_log(package, home, harness, "repair"), config, original)

    def test_repair_fails_while_another_install_holds_the_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            package = standalone_package(home)
            settings = home / ".claude/settings.json"
            settings.write_text(json.dumps({"hooks": claude_hooks_registered(package, REPEATED_REGISTRATIONS)}))
            original = settings.read_bytes()
            hold_install_lock(home)
            result = run_session_log(package, home, "claude", "repair")
            self.assert_refused(result, settings, original)
            self.assertIn("already in progress", result.stderr)

    def test_plugin_copy_with_stale_hooks_json_reports_a_skipped_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            plugin = sandbox_plugin_root(home / PLUGIN_CACHE_RELATIVE) / "skills/session-log"
            (plugin.parents[1] / "hooks/hooks.json").write_text(json.dumps({"hooks": {}}))
            settings = home / ".claude/settings.json"
            settings.write_text(json.dumps({"hooks": claude_hooks_registered(home / STANDALONE_RELATIVE, DEAD_REGISTRATIONS)}))
            original = settings.read_bytes()
            result = run_session_log(plugin, home, "claude", "repair")
            self.assertEqual((result.returncode, result.stderr), (0, ""))
            self.assertTrue(result.stdout.startswith("Claude Code: repair skipped: "), result.stdout)
            self.assertNotIn("found no duplicate", result.stdout)
            self.assertEqual(settings.read_bytes(), original)

    def test_plugin_copy_with_stale_hooks_json_refuses_to_register_its_cache_path(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            plugin = sandbox_plugin_root(home / PLUGIN_CACHE_RELATIVE) / "skills/session-log"
            (plugin.parents[1] / "hooks/hooks.json").write_text(json.dumps({"hooks": {}}))
            settings = home / ".claude/settings.json"
            settings.write_text(json.dumps({"hooks": {"Notification": [{"hooks": [FOREIGN_HOOK]}]}}))
            original = settings.read_bytes()
            result = run_session_log(plugin, home, "claude", "on")
            self.assert_refused(result, settings, original)
            self.assertIn("plugin-cache", result.stderr)
            self.assertFalse((home / ".claude/prompt-logs/.enabled").exists())

    def test_native_repair_leaves_absent_or_unmanaged_hooks_files_untouched(self):
        for harness, subroot in NATIVE_HARNESSES:
            with self.subTest(harness=harness), tempfile.TemporaryDirectory() as directory:
                home = Path(directory).resolve()
                package = harness_package(home, subroot)
                config = home / subroot / "hooks.json"
                label = NATIVE_LABELS[harness]
                clean = f"{label}: repair found no duplicate or stale registrations\n"
                absent = run_session_log(package, home, harness, "repair")
                self.assertEqual((absent.returncode, absent.stdout, absent.stderr), (0, clean, ""))
                self.assertFalse(config.exists())
                config.write_text(json.dumps({"hooks": {"Stop": [{"command": "user-hook"}]}}))
                original = config.read_bytes()
                unmanaged = run_session_log(package, home, harness, "repair")
                self.assertEqual((unmanaged.returncode, unmanaged.stdout, unmanaged.stderr), (0, clean, ""))
                self.assertEqual(config.read_bytes(), original)


ADAPTER_LINKS = {
    "opencode": (".config/opencode", "plugins/session-log.js", "adapters/opencode/session-log.js"),
    "omp": (".omp/agent", "extensions/session-log.js", "adapters/omp/session-log.js"),
}
PLUGIN_CACHE_PACKAGE_RELATIVE = f".claude/plugins/cache/harness-goodies/claude-goodies/{OLD_PLUGIN_VERSION}/skills/session-log"


class AdapterLinkRepairTests(unittest.TestCase):
    """Link repair removes only dangling plugin-cache adapter links, and `on` runs it first."""

    def test_repair_keeps_live_and_unrecognized_links_and_is_idempotent(self):
        for harness, (subroot, target, source) in ADAPTER_LINKS.items():
            with self.subTest(harness=harness), tempfile.TemporaryDirectory() as directory:
                home = Path(directory).resolve()
                package = harness_package(home, subroot)
                link = home / subroot / target
                link.parent.mkdir(parents=True)
                clean = run_session_log(package, home, harness, "repair").stdout
                for destination in (package / source, home / "nowhere/session-log.js"):
                    link.symlink_to(destination)
                    result = run_session_log(package, home, harness, "repair")
                    self.assertEqual((result.returncode, result.stdout, result.stderr), (0, clean, ""))
                    self.assertEqual(os.readlink(link), str(destination))
                    link.unlink()
                link.symlink_to(home / PLUGIN_CACHE_PACKAGE_RELATIVE / source)
                repaired = run_session_log(package, home, harness, "repair")
                self.assertEqual((repaired.returncode, repaired.stderr), (0, ""))
                self.assertIn(f"removed dangling adapter link {link}", repaired.stdout)
                self.assertFalse(os.path.lexists(link))
                again = run_session_log(package, home, harness, "repair")
                self.assertEqual((again.returncode, again.stdout, again.stderr), (0, clean, ""))

    def test_on_repairs_dangling_adapter_links(self):
        for harness, (subroot, target, source) in ADAPTER_LINKS.items():
            with self.subTest(harness=harness), tempfile.TemporaryDirectory() as directory:
                home = Path(directory).resolve()
                package = harness_package(home, subroot)
                link = home / subroot / target
                link.parent.mkdir(parents=True)
                link.symlink_to(home / PLUGIN_CACHE_PACKAGE_RELATIVE / source)
                result = run_session_log(package, home, harness, "on")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"removed dangling adapter link {link}", result.stdout)
                self.assertEqual(os.readlink(link), str(package / source))

    def test_on_collapses_repeated_native_hook_registrations(self):
        for harness, subroot in NATIVE_HARNESSES:
            with self.subTest(harness=harness), tempfile.TemporaryDirectory() as directory:
                home = Path(directory).resolve()
                package = harness_package(home, subroot)
                installed = run_session_log(package, home, harness, "on")
                self.assertEqual(installed.returncode, 0, installed.stderr)
                config = home / subroot / "hooks.json"
                expected = json.loads(config.read_text())
                repeated = json.loads(config.read_text())
                for event, entries in repeated["hooks"].items():
                    repeated["hooks"][event] = entries * REPEATED_REGISTRATIONS
                config.write_text(json.dumps(repeated))
                result = run_session_log(package, home, harness, "on")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("duplicate or stale hook registration(s)", result.stdout)
                self.assertEqual(json.loads(config.read_text()), expected)


# Python stand-ins Git Bash may find on Windows: none at all, or the Microsoft Store
# alias, which prints an install hint to stderr and exits with its own status instead of running.
WINDOWS_STORE_ALIAS_STATUS = 9009
WINDOWS_STORE_PYTHON_ALIAS = (
    '#!/bin/sh\necho "Python was not found; run without arguments to install from the Microsoft Store" >&2\n'
    f"exit {WINDOWS_STORE_ALIAS_STATUS}\n"
)
HOOK_TOOLS_WITHOUT_PYTHON = tuple(tool for tool in GIT_BASH_TOOLS_WITHOUT_JQ if tool != "python3")


class ClaudeHookWithoutPythonTests(unittest.TestCase):
    """The SessionStart settings repair is best effort: without a working python3 the hook still exits 0 silently."""

    def run_session_start(self, python3_stub):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            home = root / "home"
            plugin = sandbox_plugin_root(home / PLUGIN_CACHE_RELATIVE) / "skills/session-log"
            settings = home / ".claude/settings.json"
            settings.write_text(json.dumps({"hooks": claude_hooks_registered(home / STANDALONE_RELATIVE, DEAD_REGISTRATIONS)}))
            tools = link_tools(root / "git-bash-bin", HOOK_TOOLS_WITHOUT_PYTHON)
            if python3_stub is not None:
                (tools / "python3").write_text(python3_stub)
                (tools / "python3").chmod(EXECUTABLE_MODE)
            original = settings.read_bytes()
            result = subprocess.run(
                [str(tools / "bash"), str(plugin / "adapters/claude/claude_hook.sh"), "session-start"],
                input="{}", capture_output=True, text=True, env={"HOME": str(home), "PATH": str(tools)},
            )
            return result, settings.read_bytes() == original

    def test_hook_exits_zero_silently_without_python3(self):
        result, untouched = self.run_session_start(None)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertTrue(untouched)

    def test_hook_exits_zero_silently_when_python3_is_the_windows_store_alias(self):
        result, untouched = self.run_session_start(WINDOWS_STORE_PYTHON_ALIAS)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertTrue(untouched)


STALE_OWNER_START = "Stale Owner Start"
ACQUIRER_START = "Acquirer Start"


class PackageLockTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.lock = self.directory / "install.lock"

    def create_lock(self):
        # The test process is alive but its start time differs, so the lock owner is judged dead.
        self.lock.mkdir()
        (self.lock / "owner").write_text(f"{os.getpid()}\n{STALE_OWNER_START}\n")

    def create_stale_lock(self):
        self.create_lock()
        os.utime(self.lock, (OLD_MTIME, OLD_MTIME))

    def release_lock(self):
        (self.lock / "owner").unlink()
        self.lock.rmdir()

    def owner_lines(self):
        return (self.lock / "owner").read_text().splitlines()

    def acquire(self):
        environment = {
            "SESSION_LOG_LOCK_PATH": str(self.lock),
            "SESSION_LOG_LOCK_PID": str(os.getpid()),
            "SESSION_LOG_LOCK_START": ACQUIRER_START,
        }
        with patch.object(sys, "argv", ["locking.py", "acquire"]), \
                patch.object(sys, "path", [str(LOCKING_PATH.parent), *sys.path]), \
                patch.dict(os.environ, environment):
            try:
                load_module(LOCKING_PATH, "locking_under_test")
            except SystemExit as exit_status:
                return exit_status.code
        return 0

    def competing_reclaim(self, native_rmdir):
        """Another acquirer that judged the same lock stale reclaims it, then a creator re-makes it."""
        parent = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            try:
                fcntl.flock(parent, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return None
            (self.lock / "owner").unlink(missing_ok=True)
            native_rmdir(self.lock)
            self.lock.mkdir()
            # The creator holds the fresh lock open before writing its owner, which also pins its inode.
            fresh = os.open(self.lock, os.O_RDONLY | os.O_DIRECTORY)
            self.addCleanup(os.close, fresh)
            return os.fstat(fresh).st_ino
        finally:
            os.close(parent)

    def test_stale_reclaim_never_deletes_a_competitors_fresh_lock(self):
        self.create_stale_lock()
        native_rmdir = os.rmdir
        fresh_inodes = []

        def compete_then_rmdir(path, *args, **kwargs):
            if path == self.lock.name and not fresh_inodes:
                fresh_inodes.append(self.competing_reclaim(native_rmdir))
            return native_rmdir(path, *args, **kwargs)

        with patch.object(os, "rmdir", side_effect=compete_then_rmdir):
            self.acquire()
        self.assertIn(fresh_inodes[0], (None, os.stat(self.lock).st_ino),
                      "stale-lock reclaim deleted a competitor's freshly created lock")

    def test_acquire_reports_busy_while_another_process_reclaims(self):
        self.create_stale_lock()
        parent = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, parent)
        fcntl.flock(parent, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.assertEqual(self.acquire(), 1)
        self.assertEqual(self.owner_lines(), [str(os.getpid()), STALE_OWNER_START])

    def test_acquire_retries_when_holder_releases_after_mkdir_conflict(self):
        self.create_lock()
        native_open = os.open
        released = []

        def release_then_open(path, *args, **kwargs):
            if path == self.lock.name and not released:
                released.append(True)
                self.release_lock()
            return native_open(path, *args, **kwargs)

        with patch.object(os, "open", side_effect=release_then_open):
            status = self.acquire()
        self.assertEqual(status, 0)
        self.assertEqual(self.owner_lines(), [str(os.getpid()), ACQUIRER_START])

    def test_acquire_reports_busy_when_holder_releases_during_owner_check(self):
        self.create_lock()
        native_open = os.open
        released = []

        def release_then_open(path, *args, **kwargs):
            if path == "owner" and not released:
                released.append(True)
                self.release_lock()
            return native_open(path, *args, **kwargs)

        with patch.object(os, "open", side_effect=release_then_open):
            status = self.acquire()
        self.assertEqual(status, 1)


class NativeHookTests(unittest.TestCase):
    def test_native_hook_import_has_no_io_side_effects(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"HOME": directory}), \
                patch("sys.stdout", new_callable=io.StringIO) as stdout, \
                patch("sys.stderr", new_callable=io.StringIO) as stderr:
            load_module(HOOK_PATH, "session_log_hook_import_test")
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_native_hook_processing_writes_injected_clock_and_nonce(self):
        hook = load_module(HOOK_PATH, "session_log_hook_under_test")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / ".codex"
            (root / "prompt-logs").mkdir(parents=True)
            (root / "prompt-logs" / ".enabled").touch()
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                hook.process_payload(
                    hook.HookRequest(
                        "codex", "user-prompt",
                        {"session_id": "session-1", "cwd": directory, "prompt": "hello"},
                        root,
                    ),
                    hook.RuntimeHooks(
                        now=lambda: 1_700_000_000,
                        nonce_factory=lambda: "fixed-nonce",
                        process_start_lookup=lambda _pid: "started",
                    ),
                )
            self.assertEqual(stdout.getvalue(), "{}\n")
            runtime = json.loads((root / "session-log" / "runtime.json").read_text())
            self.assertEqual(runtime["nonce"], "fixed-nonce")
            self.assertEqual(runtime["loaded_at"], 1_700_000_000)
            log_files = list((root / "prompt-logs").glob("*/session_session-1.md"))
            self.assertEqual(len(log_files), 1)
            self.assertIn("2023-11-14T22:13:20Z prompt", log_files[0].read_text())
            self.assertIn("\nhello\n", log_files[0].read_text())

    def test_native_hook_reports_process_lookup_failure_in_runtime_and_keeps_response(self):
        hook = load_module(HOOK_PATH, "session_log_hook_failure_under_test")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / ".codex"
            (root / "prompt-logs").mkdir(parents=True)
            (root / "prompt-logs" / ".enabled").touch()
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                hook.process_payload(
                    hook.HookRequest("codex", "session-start", {"session_id": "session-2"}, root),
                    hook.RuntimeHooks(
                        now=lambda: 10,
                        nonce_factory=lambda: "nonce",
                        process_start_lookup=lambda _pid: None,
                    ),
                )
            runtime = json.loads((root / "session-log" / "runtime.json").read_text())
        self.assertEqual(stdout.getvalue(), "{}\n")
        self.assertIsNone(runtime["process_start"])

    def test_native_hook_disabled_tolerates_runtime_removed_by_concurrent_hook(self):
        hook = load_module(HOOK_PATH, "session_log_hook_concurrent_disable_test")
        native_unlink = Path.unlink
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            runtime = home / ".codex" / "session-log" / "runtime.json"
            runtime.parent.mkdir(parents=True)
            runtime.write_text("{}")

            def concurrent_unlink(path, *args, **kwargs):
                if path == runtime and path.exists():
                    native_unlink(path)
                return native_unlink(path, *args, **kwargs)

            with patch.dict(os.environ, {"HOME": str(home)}), \
                    patch.object(Path, "unlink", new=concurrent_unlink), \
                    patch("sys.stdout", new_callable=io.StringIO) as stdout:
                hook.main(["codex", "stop"])
            self.assertEqual(stdout.getvalue(), "{}\n")
            self.assertFalse(runtime.exists())


CURSOR_SESSION_ID = "cursor-usage-session"
CURSOR_TOKENS_SUFFIX = ".tokens.jsonl"
OPUS_MODEL = "claude-opus-5-5-medium"
INVALID_CURSOR_RECORD_LINES = (
    "{not json",
    "[1, 2]",
    json.dumps({"generation_id": "generation-legacy", "input_tokens": 9}),
    json.dumps({"event": "stop", "generation_id": "../escape", "input_tokens": 9}),
    json.dumps({"event": "unknown", "generation_id": "generation-unknown"}),
    json.dumps({"event": "subagent", "child_conversation_id": "../escape"}),
)
CURSOR_STATE_DB_PATHS = {
    "darwin": Path("Library/Application Support/Cursor/User/globalStorage/state.vscdb"),
    "linux": Path(".config/Cursor/User/globalStorage/state.vscdb"),
}
HOOK_CLOCK = 1_700_000_000
SUBPROCESS_TIMEOUT_SECONDS = 10
OPEN_TOKEN_FILE_SCRIPT = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location("cursor_usage", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.open_token_file(sys.argv[2]).close()
"""


def run_cursor_hook(hook, root, event, payload, now=HOOK_CLOCK):
    with patch("sys.stdout", new_callable=io.StringIO):
        hook.process_payload(
            hook.HookRequest("cursor", event, payload, root),
            hook.RuntimeHooks(now=lambda: now, nonce_factory=lambda: "nonce",
                              process_start_lookup=lambda _pid: "started"),
        )


def usage_records(harness_root):
    return [json.loads(line)
            for path in harness_root.glob(f"prompt-logs/*/session_*{CURSOR_TOKENS_SUFFIX}")
            for line in path.read_text(encoding="utf-8").splitlines()]


class CursorHookRecordTests(unittest.TestCase):
    def setUp(self):
        self.hook = load_module(HOOK_PATH, "cursor_hook_records_under_test")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.root = self.directory / ".cursor"
        self.base = {"session_id": CURSOR_SESSION_ID, "workspace_roots": [str(self.directory)]}

    def send(self, event, now=HOOK_CLOCK, **payload):
        run_cursor_hook(self.hook, self.root, event, {**self.base, **payload}, now=now)

    def test_cursor_hook_records_prompt_stop_and_subagent_events(self):
        self.send("user-prompt", generation_id="generation-one",
                  prompt="<cmd>run</cmd>\n  the   tests " + "a" * 80)
        self.send("stop", now=HOOK_CLOCK + 5, generation_id="generation-one", model=OPUS_MODEL,
                  model_params=[{"id": "effort", "value": "high"}, {"id": "fast", "value": "true"}],
                  input_tokens=10, output_tokens=2, cache_read_tokens=True, cache_write_tokens=-1,
                  transcript_path="/transcripts/parent.jsonl")
        self.send("subagent-stop", child_conversation_id="child-one", subagent_type="explore",
                  duration_ms=1500, agent_transcript_path=None)
        self.assertEqual(usage_records(self.root), [
            {"event": "prompt", "generation_id": "generation-one", "started_at": HOOK_CLOCK,
             "head": "run the tests " + "a" * 46},
            {"event": "stop", "generation_id": "generation-one", "ended_at": HOOK_CLOCK + 5,
             "input_tokens": 10, "output_tokens": 2, "model": OPUS_MODEL,
             "transcript_path": "/transcripts/parent.jsonl", "effort": "high", "fast": True},
            {"event": "subagent", "child_conversation_id": "child-one", "subagent_type": "explore",
             "duration_ms": 1500},
        ])

    def test_cursor_hook_records_tokenless_stop_and_skips_unsafe_ids(self):
        self.send("user-prompt", generation_id="../escape", prompt="unsafe")
        self.send("subagent-stop", child_conversation_id="../escape", duration_ms=1)
        self.send("stop", generation_id="generation-two", model_params=None, status="completed")
        self.assertEqual(usage_records(self.root), [
            {"event": "stop", "generation_id": "generation-two", "ended_at": HOOK_CLOCK},
        ])

    def test_codex_hook_writes_no_usage_records(self):
        root = self.directory / ".codex"
        with patch("sys.stdout", new_callable=io.StringIO):
            self.hook.process_payload(
                self.hook.HookRequest("codex", "stop", {
                    "session_id": CURSOR_SESSION_ID, "cwd": str(self.directory),
                    "generation_id": "generation-one", "input_tokens": 5,
                }, root),
                self.hook.RuntimeHooks(now=lambda: HOOK_CLOCK, nonce_factory=lambda: "nonce",
                                       process_start_lookup=lambda _pid: "started"),
            )
        self.assertEqual(usage_records(root), [])


class CursorUsageParserTests(unittest.TestCase):
    def setUp(self):
        self.usage = load_module(CURSOR_USAGE_PATH, "cursor_session_log_usage_under_test")

    def read(self, *lines):
        return self.usage.read_records(io.StringIO("".join(f"{line}\n" for line in lines)))

    def test_read_records_keeps_last_valid_record_per_event_and_skips_invalid_lines(self):
        first_stop = {"event": "stop", "generation_id": "generation-one", "output_tokens": 1}
        last_stop = {**first_stop, "output_tokens": 2}
        prompt = {"event": "prompt", "generation_id": "generation-one", "head": "first"}
        later = {"event": "prompt", "generation_id": "generation-two", "head": "later"}
        child = {"event": "subagent", "child_conversation_id": "child-one"}
        records = self.read(json.dumps(first_stop), *INVALID_CURSOR_RECORD_LINES, json.dumps(prompt),
                            json.dumps(later), json.dumps(last_stop), json.dumps(child))
        self.assertEqual(records.generations, ["generation-one", "generation-two"])
        self.assertEqual(records.prompts, {"generation-one": prompt, "generation-two": later})
        self.assertEqual(records.stops, {"generation-one": last_stop})
        self.assertEqual(records.subagents, {"child-one": child})

    def test_read_records_reports_no_generations_for_invalid_input(self):
        self.assertEqual(self.read(*INVALID_CURSOR_RECORD_LINES).generations, [])

    def test_money_rounds_cents_half_up_like_claude(self):
        self.assertEqual([self.usage.money(cents) for cents in (0, 7, 204.5, 1234.49)],
                         ["$0.00", "$0.07", "$2.05", "$12.34"])

    def test_rate_uses_fast_table_then_longest_contained_price_key(self):
        prices = {"per_mtok": {"opus-5": {"in": 5}, "opus-5-5": {"in": 6}}, "fast": {"in": 10}}
        bucket = self.usage.Bucket
        self.assertEqual(self.usage.rate(bucket("us.claude-opus-5-5-medium"), prices), {"in": 6})
        self.assertEqual(self.usage.rate(bucket("claude-opus-5", fast=True), prices), {"in": 10})
        self.assertIsNone(self.usage.rate(bucket("composer-2.5-fast"), prices))

    def test_state_subagents_reports_unsupported_platform(self):
        with self.assertRaisesRegex(self.usage.SubagentsUnavailable, "unsupported platform: win32"):
            self.usage.state_subagents(CURSOR_SESSION_ID, platform="win32")

    def test_open_token_file_refuses_fifo_and_symlink_without_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            fifo = Path(directory) / f"session_fifo{CURSOR_TOKENS_SUFFIX}"
            os.mkfifo(fifo)
            regular = Path(directory) / f"session_regular{CURSOR_TOKENS_SUFFIX}"
            regular.write_text(json.dumps({"event": "prompt", "generation_id": "generation-one"}) + "\n")
            link = Path(directory) / f"session_link{CURSOR_TOKENS_SUFFIX}"
            link.symlink_to(regular)
            for target, message in ((fifo, "unsafe Cursor token usage file"),
                                    (link, "cannot read Cursor token usage records")):
                result = subprocess.run(
                    [sys.executable, "-c", OPEN_TOKEN_FILE_SCRIPT, str(CURSOR_USAGE_PATH), str(target)],
                    text=True, capture_output=True, timeout=SUBPROCESS_TIMEOUT_SECONDS,
                )
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn(message, result.stderr)


def composer(context_tokens, type_name, root=CURSOR_SESSION_ID, parent=CURSOR_SESSION_ID):
    return {"contextTokensUsed": context_tokens,
            "subagentInfo": {"parentComposerId": parent, "rootParentConversationId": root,
                             "subagentTypeName": type_name}}


SESSION_START = 1_787_911_200  # 2026-08-28T10:00:00Z
SESSION_COMPOSERS = {
    "composerData:child-a": composer(42000, "generalPurpose"),
    "composerData:child-b": composer(9000, "explore", parent="child-a"),
    "composerData:child-other-session": composer(7000, "explore", root="other-session", parent="other-session"),
    f"composerData:{CURSOR_SESSION_ID}": {"contextTokensUsed": 5000},
}
UNTRACKED = "(not added to TOTAL; token usage not recorded client-side)"
GOLDEN_WITH_STATE = """\
Cursor: usage
session: <home>/.cursor/projects/project/agent-transcripts/cursor-usage-session/cursor-usage-session.jsonl

1. 10:00:00 (working time 00:00:11) "first prompt with tags"
est. used token: input: 80000, output: 55000, cache_create: 20000, cache_read: 300000, total_tokens: 455000, price: $2.05, model: claude-opus-5-5-medium, effort: high
2. 10:01:00 (working time 00:00:21) "second prompt unknown model"
est. used token: input: 1500, output: 500, cache_create: 500, cache_read: 1000, total_tokens: 3500, price: $0.00, model: composer-2.5-fast?, effort: unknown
3. 10:02:00 (working time 00:00:30) "third prompt fast"
est. used token: input: 50000, output: 10000, cache_create: 0, cache_read: 0, total_tokens: 60000, price: $1.00, model: claude-opus-5-5-medium:fast, effort: max
4. 10:03:00 (working time 00:00:00) "fourth prompt no answer"
est. used token: input: 0, output: 0, cache_create: 0, cache_read: 0, total_tokens: 0, price: $0.00, model: -, effort: -

sub-agent: general-purpose (child-a), working time: 00:02:05, jsonl: <home>/.cursor/projects/project/agent-transcripts/cursor-usage-session/subagents/child-a.jsonl
final context: 42000 tokens (not added to TOTAL; token usage not recorded client-side)
sub-agent: explore (child-b), working time: 00:00:00, jsonl: <home>/.cursor/projects/project/agent-transcripts/cursor-usage-session/subagents/child-b.jsonl
final context: 9000 tokens (not added to TOTAL; token usage not recorded client-side)
sub-agent: shell (child-c), working time: 00:01:01, jsonl: -
final context: unavailable: no contextTokensUsed in Cursor state database (not added to TOTAL; token usage not recorded client-side)

TOTAL (4 requests, 3 sub-agents)
working time: 00:01:02
est. used token: input: 131500, output: 65500, cache_create: 20500, cache_read: 301000, total_tokens: 518500, price: $3.05, model: claude-opus-5-5-medium+claude-opus-5-5-medium:fast+composer-2.5-fast?, effort: high+max+unknown

"""
GOLDEN_WITHOUT_STATE = """\
Cursor: usage
session: <home>/.cursor/projects/project/agent-transcripts/cursor-usage-session/cursor-usage-session.jsonl

1. 10:00:00 (working time 00:00:11) "first prompt with tags"
est. used token: input: 80000, output: 55000, cache_create: 20000, cache_read: 300000, total_tokens: 455000, price: $2.05, model: claude-opus-5-5-medium, effort: high
2. 10:01:00 (working time 00:00:21) "second prompt unknown model"
est. used token: input: 1500, output: 500, cache_create: 500, cache_read: 1000, total_tokens: 3500, price: $0.00, model: composer-2.5-fast?, effort: unknown
3. 10:02:00 (working time 00:00:30) "third prompt fast"
est. used token: input: 50000, output: 10000, cache_create: 0, cache_read: 0, total_tokens: 60000, price: $1.00, model: claude-opus-5-5-medium:fast, effort: max
4. 10:03:00 (working time 00:00:00) "fourth prompt no answer"
est. used token: input: 0, output: 0, cache_create: 0, cache_read: 0, total_tokens: 0, price: $0.00, model: -, effort: -

sub-agent: general-purpose (child-a), working time: 00:02:05, jsonl: <home>/.cursor/projects/project/agent-transcripts/cursor-usage-session/subagents/child-a.jsonl
final context: unavailable: Cursor state database not found: <home>/<state-db> (not added to TOTAL; token usage not recorded client-side)
sub-agent: shell (child-c), working time: 00:01:01, jsonl: -
final context: unavailable: Cursor state database not found: <home>/<state-db> (not added to TOTAL; token usage not recorded client-side)

TOTAL (4 requests, 2 sub-agents)
working time: 00:01:02
est. used token: input: 131500, output: 65500, cache_create: 20500, cache_read: 301000, total_tokens: 518500, price: $3.05, model: claude-opus-5-5-medium+claude-opus-5-5-medium:fast+composer-2.5-fast?, effort: high+max+unknown

"""


class CursorUsageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.home = root / "home"
        self.project = root / "project"
        self.home.mkdir()
        self.project.mkdir()
        enabled = self.run_session_log("on")
        self.assertEqual(enabled.returncode, 0, enabled.stderr)
        self.hook = load_module(HOOK_PATH, "cursor_usage_hook_under_test")
        self.transcript = (self.home / ".cursor/projects/project/agent-transcripts"
                           / CURSOR_SESSION_ID / f"{CURSOR_SESSION_ID}.jsonl")
        self.subagent_dir = self.transcript.parent / "subagents"
        self.subagent_dir.mkdir(parents=True)
        for path in (self.transcript, self.subagent_dir / "child-a.jsonl", self.subagent_dir / "child-b.jsonl"):
            path.touch()
        self.feed_session()

    def run_session_log(self, *arguments, cwd=None):
        return subprocess.run(
            [str(REPO / "skills/session-log/bin/session-log"),
             "--entrypoint", "cursor", "--harness", "cursor", *arguments],
            cwd=cwd or self.project, env={**os.environ, "HOME": str(self.home), "TZ": "UTC"},
            text=True, capture_output=True,
        )

    def usage_output(self, *arguments, cwd=None):
        result = self.run_session_log("usage", *arguments, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.replace(str(self.home), "<home>")

    def token_file(self, session=CURSOR_SESSION_ID):
        recorded = next(self.home.glob(f".cursor/prompt-logs/*/session_{CURSOR_SESSION_ID}{CURSOR_TOKENS_SUFFIX}"))
        return recorded.parent / f"session_{session}{CURSOR_TOKENS_SUFFIX}"

    def append_token_lines(self, *lines, session=CURSOR_SESSION_ID):
        with self.token_file(session).open("a", encoding="utf-8") as handle:
            handle.writelines(f"{line}\n" for line in lines)

    def send_hook(self, event, offset, **payload):
        base = {
            "conversation_id": CURSOR_SESSION_ID, "session_id": CURSOR_SESSION_ID,
            "model": OPUS_MODEL, "workspace_roots": [str(self.project)],
        }
        run_cursor_hook(self.hook, self.home / ".cursor", event, {**base, **payload}, now=SESSION_START + offset)

    def send_stop(self, generation, offset, **payload):
        self.send_hook("stop", offset, generation_id=generation, transcript_path=str(self.transcript),
                       status="completed", **payload)

    def feed_session(self):
        self.send_hook("session-start", 0)
        self.send_hook("user-prompt", 0, generation_id="generation-one", prompt="first <b>prompt</b>\n  with\ttags")
        first_stop = {"model_params": [{"id": "effort", "value": "high"}], "input_tokens": 400000,
                      "output_tokens": 55000, "cache_read_tokens": 300000, "cache_write_tokens": 20000}
        self.send_stop("generation-one", 11, **first_stop)
        self.send_stop("generation-one", 11, **first_stop)
        self.send_hook("subagent-stop", 50, child_conversation_id="child-a", subagent_type="general-purpose",
                       duration_ms=125000, agent_transcript_path=str(self.subagent_dir / "child-a.jsonl"))
        self.send_hook("user-prompt", 60, generation_id="generation-two", prompt="second prompt unknown model")
        self.send_stop("generation-two", 81, model="composer-2.5-fast", model_params=None, input_tokens=3000,
                       output_tokens=500, cache_read_tokens=1000, cache_write_tokens=500)
        self.send_hook("user-prompt", 120, generation_id="generation-three", prompt="third prompt fast")
        self.send_hook("subagent-stop", 140, child_conversation_id="child-c", subagent_type="shell",
                       duration_ms=61500, agent_transcript_path=None)
        self.send_stop("generation-three", 150,
                       model_params=[{"id": "effort", "value": "max"}, {"id": "fast", "value": "true"}],
                       input_tokens=50000, output_tokens=10000, cache_read_tokens=0, cache_write_tokens=0)
        self.send_hook("user-prompt", 180, generation_id="generation-four", prompt="fourth prompt no answer")

    def golden_without_state(self):
        return GOLDEN_WITHOUT_STATE.replace("<state-db>", str(CURSOR_STATE_DB_PATHS[sys.platform]))

    def state_db(self):
        path = self.home / CURSOR_STATE_DB_PATHS[sys.platform]
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def create_state_db(self, composers):
        path = self.state_db()
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("create table cursorDiskKV (key text unique on conflict replace, value blob)")
            connection.executemany("insert into cursorDiskKV values (?, ?)",
                                   [(key, json.dumps(value)) for key, value in composers.items()])
            connection.commit()
        return path

    def test_cursor_usage_matches_claude_layout_golden_without_writing_db(self):
        path = self.create_state_db(SESSION_COMPOSERS)
        before = path.read_bytes()
        self.assertEqual(self.usage_output(CURSOR_SESSION_ID), GOLDEN_WITH_STATE)
        self.assertEqual(path.read_bytes(), before)

    def test_cursor_usage_lists_hook_subagents_as_unavailable_without_state_db(self):
        self.assertEqual(self.usage_output(CURSOR_SESSION_ID), self.golden_without_state())

    def test_cursor_usage_reports_unreadable_state_db_on_each_subagent(self):
        self.state_db().write_bytes(b"not a sqlite database" * 100)
        line = f"final context: unavailable: cannot read Cursor state database: file is not a database {UNTRACKED}"
        self.assertEqual(self.usage_output(CURSOR_SESSION_ID).splitlines().count(line), 2)

    def test_cursor_usage_marks_invalid_subagent_context_unavailable(self):
        self.create_state_db({**SESSION_COMPOSERS, "composerData:child-b": composer("7", "explore")})
        lines = self.usage_output(CURSOR_SESSION_ID).splitlines()
        header = next(index for index, line in enumerate(lines) if line.startswith("sub-agent: explore (child-b)"))
        self.assertEqual(lines[header + 1],
                         f"final context: unavailable: no contextTokensUsed in Cursor state database {UNTRACKED}")

    def test_cursor_usage_resolves_latest_and_session_targets_identically(self):
        self.create_state_db(SESSION_COMPOSERS)
        subdirectory = self.project / "src" / "nested"
        subdirectory.mkdir(parents=True)
        self.assertEqual(self.usage_output(), GOLDEN_WITH_STATE)
        self.assertEqual(self.usage_output("--latest"), GOLDEN_WITH_STATE)
        self.assertEqual(self.usage_output("--latest", cwd=subdirectory), GOLDEN_WITH_STATE)

    def test_cursor_usage_ignores_invalid_and_duplicate_record_lines(self):
        self.create_state_db(SESSION_COMPOSERS)
        recorded = self.token_file().read_text(encoding="utf-8").splitlines()
        self.append_token_lines(*INVALID_CURSOR_RECORD_LINES, *recorded)
        self.assertEqual(self.usage_output(CURSOR_SESSION_ID), GOLDEN_WITH_STATE)

    def test_cursor_stop_without_token_fields_still_reports_the_turn(self):
        session = {"session_id": "tokenless", "conversation_id": "tokenless"}
        self.send_hook("user-prompt", 0, generation_id="generation-one", prompt="tokenless prompt", **session)
        self.send_hook("stop", 7, generation_id="generation-one", status="completed", **session)
        empty = ("est. used token: input: 0, output: 0, cache_create: 0, cache_read: 0, total_tokens: 0, "
                 "price: $0.00, model: -, effort: -")
        self.assertEqual(self.usage_output("tokenless"), "\n".join([
            "Cursor: usage",
            f"session: {self.token_file('tokenless')}".replace(str(self.home), "<home>"),
            "",
            '1. 10:00:00 (working time 00:00:07) "tokenless prompt"',
            empty,
            "",
            "TOTAL (1 requests, 0 sub-agents)",
            "working time: 00:00:07",
            empty,
            "",
            "",
        ]))

    def test_cursor_usage_fails_for_unknown_session(self):
        result = self.run_session_log("usage", "missing-session")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("Cursor session has no token usage records: missing-session", result.stderr)

    def test_cursor_usage_latest_fails_without_project_records(self):
        other = self.project.parent / "other"
        other.mkdir()
        result = self.run_session_log("usage", "--latest", cwd=other)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("no Cursor token usage records found for the current project", result.stderr)

    def test_cursor_stop_hook_appends_despite_corrupt_record_file(self):
        self.append_token_lines("{not json", json.dumps({"input_tokens": 9}))
        self.send_stop("generation-five", 200, input_tokens=5, output_tokens=2)
        last_line = json.loads(self.token_file().read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual((last_line["event"], last_line["generation_id"]), ("stop", "generation-five"))

    def test_cursor_usage_fails_when_all_record_lines_are_invalid(self):
        self.append_token_lines(*INVALID_CURSOR_RECORD_LINES, session="corrupt-only")
        result = self.run_session_log("usage", "corrupt-only")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(f"Cursor session has no token usage records: {self.token_file('corrupt-only')}", result.stderr)
        self.assertNotIn("est. used token", result.stdout)

    def test_cursor_usage_rejects_path_targets(self):
        transcript = self.token_file()
        for target in (str(transcript), f"./{transcript.name}"):
            result = self.run_session_log("usage", target, cwd=transcript.parent)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("Cursor usage accepts only a session ID or --latest", result.stderr)


class CursorLatestSessionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.home = root / "home"
        self.project = root / "project"
        self.project.mkdir()
        workspace = load_module(HOOK_PATH, "cursor_latest_hook_under_test").workspace_key({"cwd": str(self.project)})
        self.logs = self.home / ".cursor" / "prompt-logs" / workspace
        self.logs.mkdir(parents=True)

    def token_file(self, session, mtime, *records):
        path = self.logs / f"session_{session}{CURSOR_TOKENS_SUFFIX}"
        path.write_text("".join(json.dumps(record) + "\n" for record in records))
        os.utime(path, (mtime, mtime))
        return path

    def latest(self):
        result = subprocess.run([sys.executable, str(CURSOR_USAGE_PATH)], cwd=self.project, text=True,
                                capture_output=True, env={**os.environ, "HOME": str(self.home), "TZ": "UTC"},
                                timeout=SUBPROCESS_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.splitlines()[0]

    def test_latest_picks_session_with_newest_user_prompt_over_newest_mtime(self):
        prompted = self.token_file("session-a", OLD_MTIME,
                                   {"event": "prompt", "generation_id": "generation-a", "started_at": 300})
        self.token_file("session-b", NEW_MTIME,
                        {"event": "prompt", "generation_id": "generation-b", "started_at": 200},
                        {"event": "stop", "generation_id": "generation-b", "ended_at": 400})
        self.assertEqual(self.latest(), f"session: {prompted}")

    def test_latest_picks_the_only_session(self):
        only = self.token_file("session-a", OLD_MTIME,
                               {"event": "prompt", "generation_id": "generation-a", "started_at": 300})
        self.assertEqual(self.latest(), f"session: {only}")

    def test_latest_ranks_sessions_without_user_prompt_last_then_by_mtime(self):
        prompted = self.token_file("session-a", OLD_MTIME,
                                   {"event": "prompt", "generation_id": "generation-a", "started_at": 300})
        self.token_file("session-b", NEW_MTIME, {"event": "stop", "generation_id": "generation-b", "ended_at": 400})
        self.assertEqual(self.latest(), f"session: {prompted}")
        prompted.unlink()
        newest = self.token_file("session-c", NEW_MTIME + 1,
                                 {"event": "stop", "generation_id": "generation-c", "ended_at": 500})
        self.assertEqual(self.latest(), f"session: {newest}")

    def test_latest_never_picks_subagent_session(self):
        root = self.token_file("session-a", OLD_MTIME,
                               {"event": "prompt", "generation_id": "generation-a", "started_at": 300},
                               {"event": "subagent", "child_conversation_id": "session-child"})
        self.token_file("session-child", NEW_MTIME,
                        {"event": "prompt", "generation_id": "generation-child", "started_at": 900})
        self.assertEqual(self.latest(), f"session: {root}")


if __name__ == "__main__":
    unittest.main()
