import importlib.util
import fcntl
import hashlib
import io
import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
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
    def test_settings_closes_directory_when_lock_open_fails(self):
        with patch.object(sys, "path", [str(CLAUDE_SETTINGS_PATH.parent), *sys.path]):
            settings = load_module(CLAUDE_SETTINGS_PATH, "claude_settings_directory_test")
        native_open = settings.os.open
        native_directory_open = settings.pathsafe.open_directory
        with tempfile.TemporaryDirectory() as directory:
            settings_path = Path(directory).resolve() / "settings.json"
            settings_path.write_text("{}")
            for operation in ("remove", "migrate"):
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
                        "SETTINGS_PATH": str(settings_path),
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

    def test_remove_strips_unmarked_copies_and_keeps_lookalikes(self):
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
        self.assertTrue(settings._remove_managed_hooks(document, settings._owned_hook(self.OWNER)))
        self.assertEqual(document["hooks"]["Stop"], [{"hooks": [command_hook(value) for value in lookalikes]}])

    def test_remove_strips_owned_hooks_from_every_event_and_keeps_the_rest(self):
        settings = self.load()
        foreign = {"type": "command", "command": "cc-status compact"}
        document = {"hooks": {
            "PreCompact": [{"hooks": [self.owned("/old/claude_hook.sh", "stop"), foreign]}],
            "SessionEnd": [{"hooks": [self.owned("/old/claude_hook.sh", "stop")]}],
            "Notification": [],
        }}
        settings._remove_managed_hooks(document, settings._owned_hook(self.OWNER))
        self.assertEqual(document["hooks"], {"PreCompact": [{"hooks": [foreign]}], "Notification": []})

    def test_windows_path_with_a_hash_is_recognized_as_a_claude_hook(self):
        settings = self.load()
        windows_hook = "C:\\a#b\\.claude\\" + PLUGIN_HOOK_TAIL.replace("/", "\\")
        self.assertTrue(settings._runs_claude_hook(f"bash {windows_hook} stop"))

    def test_remove_cli_leaves_missing_settings_absent(self):
        settings = self.load()
        with tempfile.TemporaryDirectory() as directory:
            settings_path = Path(directory) / "settings.json"
            with patch.dict(os.environ, {"SETTINGS_PATH": str(settings_path), "SESSION_LOG_OWNER_MARKER": self.OWNER}):
                settings.main(["remove"])
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

    def owned_commands(self, home):
        """Every spelling of a session-log registration older releases wrote, live or dead, by name."""
        prefix = f"SESSION_LOG_OWNER={self.OWNER} bash"
        live_hook = home / "live" / PLUGIN_HOOK_TAIL
        live_hook.parent.mkdir(parents=True)
        live_hook.write_text("")
        cache_tail = f".claude/plugins/cache/harness-goodies/claude-goodies/{OLD_PLUGIN_VERSION}/{PLUGIN_HOOK_TAIL}"
        windows_hook = "C:\\Users\\Jane Doe\\.claude\\" + PLUGIN_HOOK_TAIL.replace("/", "\\")
        return {
            "live absolute path": f"{prefix} {live_hook} stop",
            "missing absolute path": f"{prefix} {home}/gone/{PLUGIN_HOOK_TAIL} stop",
            "verbatim plugin command": PLUGIN_COMMANDS_FOR_TESTS["stop"],
            "owner-marked plugin root placeholder": f'{prefix} "{PLUGIN_ROOT_PLACEHOLDER}/{PLUGIN_HOOK_TAIL}" stop',
            "native Windows path": f"{prefix} '{windows_hook}' stop",
            "versioned plugin-cache copy under $HOME": f'{prefix} "$HOME/{cache_tail}" stop',
            "unparsable": f"{prefix} '/unterminated/{PLUGIN_HOOK_TAIL} stop",
            "relative path": f"{prefix} gone/{PLUGIN_HOOK_TAIL} stop",
        }

    def test_remove_cli_removes_every_owned_registration_and_reports_it(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            settings_path = home / "settings.json"
            environment = {**os.environ, "HOME": str(home), "SETTINGS_PATH": str(settings_path),
                           "SESSION_LOG_OWNER_MARKER": self.OWNER}
            for name, command in self.owned_commands(home).items():
                with self.subTest(name):
                    settings_path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [command_hook(command)]}]}}))
                    result = self.cli(environment, "remove")
                    self.assertEqual((result.returncode, result.stderr), (0, ""))
                    self.assertEqual(result.stdout,
                                     f"removed 1 legacy session-log hook registration(s) from {settings_path}\n")
                    self.assertEqual(json.loads(settings_path.read_text()), {})

    def test_remove_cli_leaves_foreign_hooks_untouched_and_silent(self):
        with tempfile.TemporaryDirectory() as directory:
            settings_path = Path(directory).resolve() / "settings.json"
            environment = {**os.environ, "SETTINGS_PATH": str(settings_path), "SESSION_LOG_OWNER_MARKER": self.OWNER}
            settings_path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [FOREIGN_HOOK]}]}}, indent=2))
            original = settings_path.read_bytes()
            result = self.cli(environment, "remove")
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
            self.assertEqual(settings_path.read_bytes(), original)

    def test_remove_cli_counts_every_removed_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            settings_path = Path(directory).resolve() / "settings.json"
            environment = {**os.environ, "SETTINGS_PATH": str(settings_path), "SESSION_LOG_OWNER_MARKER": self.OWNER}
            settings_path.write_text(json.dumps({"hooks": {
                "SessionStart": [{"hooks": [self.owned("/x/claude_hook.sh", "session-start")]}],
                "Stop": [{"hooks": [self.owned("/x/claude_hook.sh", "stop"), self.owned("/y/claude_hook.sh", "stop")]}],
            }}))
            result = self.cli(environment, "remove")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, f"removed 3 legacy session-log hook registration(s) from {settings_path}\n")

    def test_unknown_operations_are_refused(self):
        environment = {**os.environ, "SETTINGS_PATH": "/nonexistent/settings.json", "SESSION_LOG_OWNER_MARKER": self.OWNER}
        for operation in ("install", "repair", "has-removable", "plugin-enabled", "plugin-hooks-current"):
            with self.subTest(operation=operation):
                result = self.cli(environment, operation)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"claude_settings: unknown operation: {operation}", result.stderr)

    def test_hooks_json_registers_the_plugin_hook_command_for_every_lifecycle(self):
        settings = self.load()
        for event, hook in plugin_hooks():
            with self.subTest(event=event):
                self.assertEqual(hook["command"], settings.plugin_hook_command(PLUGIN_EVENT_LIFECYCLES[event]))


PLUGIN_ROOT_PLACEHOLDER = "${CLAUDE_PLUGIN_ROOT}"
# Plugin-cache version directories: the current plugin, and an old release whose dead paths
# earlier installs wrote into settings.json.
PLUGIN_VERSION = "1.18.2"
OLD_PLUGIN_VERSION = "1.14.1"
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
        env={**environment, "CLAUDE_PLUGIN_ROOT": plugin_root}, timeout=HOOK_TIMEOUT_SECONDS,
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


class ClaudeSettingsCleanupTests(unittest.TestCase):
    """`on` and a plugin session start remove every session-log registration older releases left in settings.json."""

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
                    {"hooks": [command_hook(f"{self.OWNER_PREFIX}bash {home}/{SEEDED_RELATIVE}/adapters/claude/claude_hook.sh subagent-stop")]},
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

    def test_on_removes_stale_duplicate_and_windows_registrations(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            (home / ".Trash").mkdir()
            plugin_root = sandbox_plugin_root(home / PLUGIN_CACHE_RELATIVE)
            settings = home / ".claude/settings.json"
            settings.write_text(json.dumps(self.stale_settings(home), indent=2))

            first = self.install_on(home, plugin_root)
            self.assertEqual(first.returncode, 0, first.stderr)
            repaired = settings.read_bytes()
            self.assertEqual(json.loads(repaired), {
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
# Where older releases seeded a copy of the package, whose hooks they registered in settings.json.
SEEDED_RELATIVE = ".claude/skills/session-log"
CLAUDE_MANAGED_MATCHERS = {"SessionStart": "startup|resume"}
FOREIGN_HOOK = {"type": "command", "command": "cc-status notify"}
REPEATED_REGISTRATIONS = 3
# Claude Code writes this into a plugin version directory it has replaced or uninstalled.
ORPHANED_PLUGIN_MARKER = ".orphaned_at"
INSTALL_LOCK_RELATIVE = ".claude/session-log/.install.lock"
PRIVATE_DIRECTORY_MODE = 0o700
LEGACY_CLEANUP_STAMP_RELATIVE = ".claude/session-log/legacy-cleanup.stamp"
EXECUTABLE_MODE = 0o755
READ_ONLY_DIRECTORY_MODE = 0o555
NATIVE_HARNESSES = (("codex", ".codex"), ("cursor", ".cursor"))
NATIVE_LABELS = {"codex": "Codex", "cursor": "Cursor"}
# Settings.json registrations of a seeded copy that is not installed, so every one of them is dead.
DEAD_REGISTRATIONS = 2
# State files older releases wrote that nothing reads any more.
OBSOLETE_CLAUDE_STATE = ("adapter.version", "adapter.manifest", "desired.version", "plugin-root",
                         "settings-repair.1322337178.stamp")
# The scripts the pre-plugin Claude Code install copied into ~/.claude/scripts, in cleanup order.
LEGACY_SCRIPT_NAMES = ("prompt_log_save.sh", "prompt_log_new_session.sh", "prompt_log_stop.sh", "prompt_log_subagent.sh",
                       "prompt_log_lib.sh", "prompt_log_usage.sh", "prompt_log_usage.jq", "prompt_log_prices.json")
CLAUDE_REPAIR_CLEAN = "Claude Code: repair found no duplicate or stale registrations\n"


def harness_environment(home):
    environment = {key: value for key, value in os.environ.items()
                   if key not in ("CLAUDE_PLUGIN_ROOT", "CLAUDE_CONFIG_DIR", "CODEX_HOME")}
    return {**environment, "HOME": str(home)}


# A hook that blocks on its stdin fails the test instead of hanging the suite.
HOOK_TIMEOUT_SECONDS = 60


def run_claude_hook(package, home, lifecycle, *, payload="{}", environment=None):
    return subprocess.run(
        ["bash", str(package / "adapters/claude/claude_hook.sh"), lifecycle],
        input=payload, capture_output=True, text=True, env=environment or harness_environment(home),
        timeout=HOOK_TIMEOUT_SECONDS,
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


def claude_package(home):
    """The session-log package of a claude-goodies plugin installed under ``home``."""
    return sandbox_plugin_root(home / PLUGIN_CACHE_RELATIVE) / "skills/session-log"


def claude_managed_hook(package, lifecycle):
    hook = shlex.quote(str(package / "adapters/claude/claude_hook.sh"))
    return {"type": "command", "command": f"SESSION_LOG_OWNER={OWNER_MARKER} bash {hook} {lifecycle}"}


def claude_managed_entry(package, event):
    entry = {"hooks": [claude_managed_hook(package, PLUGIN_EVENT_LIFECYCLES[event])]}
    if event in CLAUDE_MANAGED_MATCHERS:
        entry["matcher"] = CLAUDE_MANAGED_MATCHERS[event]
    return entry


def claude_hooks_registered(package, times):
    """The settings.json hooks older releases registered for ``package``, each repeated ``times``."""
    return {event: [claude_managed_entry(package, event) for _ in range(times)] for event in CLAUDE_LIFECYCLE_EVENTS}


class SessionLogRepairCommandTests(unittest.TestCase):
    """`session-log repair` removes legacy leftovers and leaves each harness with its current registrations."""

    maxDiff = None

    def test_claude_repair_removes_every_legacy_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            package = claude_package(home)
            settings = home / ".claude/settings.json"
            hooks = claude_hooks_registered(home / SEEDED_RELATIVE, REPEATED_REGISTRATIONS)
            hooks["Stop"].append({"hooks": [claude_managed_hook(home / "old-install", "stop")]})
            hooks["PreCompact"] = [{"hooks": [claude_managed_hook(home / SEEDED_RELATIVE, "stop"), FOREIGN_HOOK]}]
            hooks["Notification"] = [{"hooks": [FOREIGN_HOOK]}]
            settings.write_text(json.dumps({"model": "opus", "hooks": hooks}))

            first = run_session_log(package, home, "claude", "repair")
            self.assertEqual((first.returncode, first.stderr), (0, ""))
            removed = REPEATED_REGISTRATIONS * len(CLAUDE_LIFECYCLE_EVENTS) + 2
            self.assertEqual(first.stdout,
                             f"Claude Code: removed {removed} legacy session-log hook registration(s) from {settings}\n")
            self.assertEqual(json.loads(settings.read_text()), {"model": "opus", "hooks": {
                "PreCompact": [{"hooks": [FOREIGN_HOOK]}], "Notification": [{"hooks": [FOREIGN_HOOK]}],
            }})

            repaired = settings.read_bytes()
            second = run_session_log(package, home, "claude", "repair")
            self.assertEqual((second.returncode, second.stdout, second.stderr), (0, CLAUDE_REPAIR_CLEAN, ""))
            self.assertEqual(settings.read_bytes(), repaired)

    def test_claude_repair_leaves_unregistered_settings_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            package = claude_package(home)
            settings = home / ".claude/settings.json"
            settings.write_text(json.dumps({"hooks": {"Notification": [{"hooks": [FOREIGN_HOOK]}]}}))
            original = settings.read_bytes()
            result = run_session_log(package, home, "claude", "repair")
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, CLAUDE_REPAIR_CLEAN, ""))
            self.assertEqual(settings.read_bytes(), original)

    def test_claude_repair_removes_obsolete_state_and_keeps_runtime_markers(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            package = claude_package(home)
            state = home / ".claude/session-log"
            state.mkdir(parents=True)
            for name in (*OBSOLETE_CLAUDE_STATE, "runtime.json", "runtime.1.json"):
                (state / name).write_text("kept\n" if name.startswith("runtime") else "obsolete\n")
            result = run_session_log(package, home, "claude", "repair")
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, CLAUDE_REPAIR_CLEAN, ""))
            self.assertEqual(sorted(path.name for path in state.iterdir()), ["runtime.1.json", "runtime.json"])

    def test_claude_repair_points_plugin_root_at_the_running_plugin_while_a_seeded_copy_is_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            package = claude_package(home)
            # A copy without an ownership manifest is kept. Its older code (package VERSION 2.0.0,
            # shipped with plugin 1.19.0) defers to the plugin only while plugin-root names a live
            # plugin version, so the marker must follow the plugin past the version that wrote it.
            seeded = home / SEEDED_RELATIVE
            seeded.parent.mkdir(parents=True)
            shutil.copytree(REPO / "skills/session-log", seeded)
            orphaned = sandbox_plugin_root(home / PLUGIN_CACHE_RELATIVE.replace(PLUGIN_VERSION, OLD_PLUGIN_VERSION))
            (orphaned / ORPHANED_PLUGIN_MARKER).write_text("1700000000000")
            state = home / ".claude/session-log"
            state.mkdir(parents=True)
            for name in OBSOLETE_CLAUDE_STATE:
                (state / name).write_text("obsolete\n")
            (state / "plugin-root").write_text(str(orphaned / "skills/session-log"))
            result = run_session_log(package, home, "claude", "repair")
            self.assertEqual((result.returncode, result.stderr), (0, ""))
            self.assertEqual(result.stdout, f"Claude Code: kept legacy package copy {seeded}: it has no ownership manifest; move it aside\n")
            self.assertEqual(sorted(path.name for path in state.iterdir()), ["plugin-root"])
            self.assertEqual((state / "plugin-root").read_text().strip(), str(package))

    def test_claude_repair_removes_plugin_root_with_the_seeded_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            package = claude_package(home)
            seeded = home / SEEDED_RELATIVE
            seeded.parent.mkdir(parents=True)
            shutil.copytree(REPO / "skills/session-log", seeded)
            write_seeded_manifest(seeded)
            state = home / ".claude/session-log"
            state.mkdir(parents=True)
            (state / "plugin-root").write_text(str(package))
            result = run_session_log(package, home, "claude", "repair")
            self.assertEqual((result.returncode, result.stderr), (0, ""))
            self.assertEqual(result.stdout, f"Claude Code: removed legacy package copy {seeded}\n")
            self.assertFalse(os.path.lexists(seeded))
            self.assertEqual(list(state.iterdir()), [])

    def test_claude_repair_still_removes_registrations_and_the_copy_beside_an_edited_legacy_script(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            package = claude_package(home)
            scripts = home / ".claude/scripts"
            scripts.mkdir(parents=True)
            edited = scripts / "prompt_log_prices.json"
            edited.write_text("{}\n")
            seeded = home / SEEDED_RELATIVE
            shutil.copytree(REPO / "skills/session-log", seeded)
            write_seeded_manifest(seeded)
            settings = home / ".claude/settings.json"
            legacy_hook = command_hook(f"bash {scripts}/prompt_log_save.sh")
            hooks = claude_hooks_registered(seeded, 1)
            hooks["UserPromptSubmit"].append({"hooks": [legacy_hook]})
            settings.write_text(json.dumps({"hooks": hooks}))
            result = run_session_log(package, home, "claude", "repair")
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stderr, f"session-log: refusing to remove unrecognized legacy Claude script: {edited}\n")
            self.assertEqual(result.stdout, (
                f"Claude Code: removed {len(CLAUDE_LIFECYCLE_EVENTS)} legacy session-log hook registration(s) from {settings}\n"
                f"Claude Code: removed legacy package copy {seeded}\n"))
            self.assertEqual(json.loads(settings.read_text()), {"hooks": {"UserPromptSubmit": [{"hooks": [legacy_hook]}]}})
            self.assertFalse(os.path.lexists(seeded))
            self.assertEqual(edited.read_text(), "{}\n")

    def test_claude_repair_reports_the_legacy_scripts_and_hooks_it_removes(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            package = claude_package(home)
            scripts = home / ".claude/scripts"
            scripts.mkdir(parents=True)
            for name in LEGACY_SCRIPT_NAMES:
                (scripts / name).write_text("# universal-session-log: managed\nlegacy\n")
            settings = home / ".claude/settings.json"
            settings.write_text(json.dumps({"hooks": {
                "Stop": [{"hooks": [command_hook(f"bash {scripts}/prompt_log_stop.sh"), FOREIGN_HOOK]}],
                "UserPromptSubmit": [{"hooks": [command_hook(f"bash {scripts}/prompt_log_save.sh")]}],
            }}))
            result = run_session_log(package, home, "claude", "repair")
            self.assertEqual((result.returncode, result.stderr), (0, ""))
            self.assertEqual(result.stdout, "".join([
                f"Claude Code: removed 2 legacy prompt_log hook registration(s) from {settings}\n",
                *(f"Claude Code: removed legacy script {scripts / name}\n" for name in LEGACY_SCRIPT_NAMES),
            ]))
            self.assertEqual(json.loads(settings.read_text()), {"hooks": {"Stop": [{"hooks": [FOREIGN_HOOK]}]}})
            self.assertEqual(list(scripts.iterdir()), [])

    def test_claude_commands_never_register_hooks(self):
        # `usage` fails here only because the sandbox holds no Claude Code transcript.
        for command, status in (("on", 0), ("status", 0), ("usage", 1), ("off", 0), ("repair", 0)):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                home = Path(directory).resolve()
                package = claude_package(home)
                settings = home / ".claude/settings.json"
                settings.write_text(json.dumps({"hooks": {"Notification": [{"hooks": [FOREIGN_HOOK]}]}}, indent=2))
                original = settings.read_bytes()
                result = run_session_log(package, home, "claude", command)
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertEqual(settings.read_bytes(), original)

    def test_claude_on_enables_logging_when_the_legacy_cleanup_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            package = claude_package(home)
            script = home / ".claude/scripts/prompt_log_save.sh"
            script.parent.mkdir(parents=True)
            script.write_text("edited by the user\n")
            result = run_session_log(package, home, "claude", "on")
            self.assertEqual((result.returncode, result.stderr), (0, ""))
            self.assertEqual(result.stdout, (
                f"Claude Code: legacy cleanup failed: refusing to remove unrecognized legacy Claude script: {script}; "
                "fix it, then run repair\nClaude Code: on — logging starts with the next prompt\n"))
            self.assertTrue((home / ".claude/prompt-logs/.enabled").is_file())
            self.assertEqual(script.read_text(), "edited by the user\n")
            repaired = run_session_log(package, home, "claude", "repair")
            self.assertEqual(repaired.returncode, 1)
            self.assertIn("refusing to remove unrecognized legacy Claude script", repaired.stderr)

    @unittest.skipIf(os.geteuid() == 0, "root ignores directory permissions")
    def test_claude_on_stops_the_cleanup_at_a_failing_step_and_keeps_its_scripts(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            package = claude_package(home)
            claude_root = home / ".claude"
            scripts = claude_root / "scripts"
            scripts.mkdir(parents=True)
            (claude_root / "prompt-logs").mkdir()
            (claude_root / "session-log").mkdir()
            script = scripts / "prompt_log_save.sh"
            script.write_text("# universal-session-log: managed\nlegacy\n")
            settings = claude_root / "settings.json"
            settings.write_text(json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [command_hook(f"bash {script}")]}]}}))
            original = settings.read_bytes()
            # settings.json cannot be locked or replaced, so `migrate` fails before anything is removed.
            claude_root.chmod(READ_ONLY_DIRECTORY_MODE)
            try:
                result = run_session_log(package, home, "claude", "on")
            finally:
                claude_root.chmod(PRIVATE_DIRECTORY_MODE)
            self.assertEqual((result.returncode, result.stderr), (0, ""))
            failure, status = result.stdout.splitlines()
            self.assertTrue(failure.startswith("Claude Code: legacy cleanup failed: "), failure)
            self.assertIn("Permission denied", failure)
            self.assertTrue(failure.endswith("; fix it, then run repair"), failure)
            self.assertNotIn("Traceback", failure)
            self.assertEqual(status, "Claude Code: on — logging starts with the next prompt")
            self.assertEqual(script.read_text(), "# universal-session-log: managed\nlegacy\n")
            self.assertEqual(settings.read_bytes(), original)
            self.assertTrue((claude_root / "prompt-logs/.enabled").is_file())

    def test_claude_on_reports_a_silent_cleanup_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            home = root / "home"
            package = claude_package(home)
            settings = home / ".claude/settings.json"
            settings.write_text(json.dumps({"hooks": {}}))
            shims = root / "shims"
            shims.mkdir()
            # A python3 whose `migrate` fails without a word.
            (shims / "python3").write_text(
                f'#!/bin/sh\ncase "$*" in *"claude_settings.py migrate"*) exit 1 ;; esac\nexec "{sys.executable}" "$@"\n')
            (shims / "python3").chmod(EXECUTABLE_MODE)
            result = subprocess.run(
                ["bash", str(package / "bin/session-log"), "--entrypoint", "claude", "--harness", "claude", "on"],
                capture_output=True, text=True,
                env={**harness_environment(home), "PATH": f"{shims}{os.pathsep}{os.environ['PATH']}"})
            self.assertEqual((result.returncode, result.stderr), (0, ""))
            self.assertEqual(result.stdout, "Claude Code: legacy cleanup failed; run repair to see why\n"
                                            "Claude Code: on — logging starts with the next prompt\n")

    def test_claude_on_skips_the_cleanup_while_another_command_holds_the_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            package = claude_package(home)
            settings = home / ".claude/settings.json"
            settings.write_text(json.dumps({"hooks": claude_hooks_registered(home / SEEDED_RELATIVE, 1)}))
            original = settings.read_bytes()
            hold_install_lock(home)
            result = run_session_log(package, home, "claude", "on")
            self.assertEqual((result.returncode, result.stderr), (0, ""))
            self.assertEqual(result.stdout, "Claude Code: legacy cleanup skipped: another session-log command is running\n"
                                            "Claude Code: on — logging starts with the next prompt\n")
            self.assertTrue((home / ".claude/prompt-logs/.enabled").is_file())
            self.assertEqual(settings.read_bytes(), original)
            self.assertTrue((home / INSTALL_LOCK_RELATIVE / "owner").is_file())

    def test_claude_on_without_jq_fails_before_enabling_logging(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            home = root / "home"
            package = claude_package(home)
            tools = link_tools(root / "bin-without-jq", GIT_BASH_TOOLS_WITHOUT_JQ)
            result = subprocess.run(
                ["bash", str(package / "bin/session-log"), "--entrypoint", "claude", "--harness", "claude", "on"],
                capture_output=True, text=True, env={"HOME": str(home), "PATH": str(tools)})
            self.assertEqual((result.returncode, result.stderr), (1, "session-log: missing dependency: jq\n"))
            self.assertFalse((home / ".claude/prompt-logs/.enabled").exists())

    def test_claude_on_removes_repeated_registrations(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            package = claude_package(home)
            settings = home / ".claude/settings.json"
            settings.write_text(json.dumps({"hooks": claude_hooks_registered(home / SEEDED_RELATIVE, REPEATED_REGISTRATIONS)}))
            result = run_session_log(package, home, "claude", "on")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(settings.read_text()), {})
            self.assertTrue((home / ".claude/prompt-logs/.enabled").is_file())

    def test_session_start_removes_repeated_registrations(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            package = claude_package(home)
            settings = home / ".claude/settings.json"
            settings.write_text(json.dumps({"hooks": claude_hooks_registered(home / SEEDED_RELATIVE, REPEATED_REGISTRATIONS)}))
            result = run_claude_hook(package, home, "session-start")
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
            self.assertEqual(json.loads(settings.read_text()), {})

    def test_session_start_skips_settings_without_session_log_hooks(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            package = claude_package(home)
            settings = home / ".claude/settings.json"
            settings.write_text(json.dumps({"hooks": {"Notification": [{"hooks": [FOREIGN_HOOK]}]}}, indent=2))
            original = settings.read_bytes()
            result = run_claude_hook(package, home, "session-start")
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
            self.assertEqual(settings.read_bytes(), original)
            # The grep gate kept the cleanup from running: it would have created the state directory.
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


RECORDING_SESSION_LOG = '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$SESSION_LOG_TEST_RECORD"\n'
# A command the session-start gate matches but the cleanup must keep: it is not a session-log registration.
LOOKALIKE_HOOK = command_hook(f"bash /x/{PLUGIN_HOOK_TAIL} unknown-event")


class SessionStartCleanupStampTests(unittest.TestCase):
    """Session start runs the legacy cleanup once per change of settings.json or the package."""

    def recorded_session_start(self, package, home, record):
        return run_claude_hook(package, home, "session-start",
                               environment={**harness_environment(home), "SESSION_LOG_TEST_RECORD": str(record)})

    def test_session_start_cleans_once_per_settings_change(self):
        variants = {
            "minified": ("home", lambda document: json.dumps(document, separators=(",", ":"))),
            "non-ASCII home escaped by json.dump": ("Jöhn Dœ", json.dumps),
            "shell-quoted home": ("Jane O'Doe", lambda document: json.dumps(document, indent=2)),
        }
        for name, (home_name, encode) in variants.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as directory:
                home = Path(directory).resolve() / home_name
                package = claude_package(home)
                settings = home / ".claude/settings.json"
                settings.write_text(encode({"hooks": {"Stop": [{"hooks": [LOOKALIKE_HOOK]}]}}))
                original = settings.read_bytes()
                record = Path(directory) / "record"

                first = run_claude_hook(package, home, "session-start")
                self.assertEqual((first.returncode, first.stdout, first.stderr), (0, "", ""))
                self.assertEqual(settings.read_bytes(), original)
                self.assertTrue((home / LEGACY_CLEANUP_STAMP_RELATIVE).is_file())

                (package / "bin/session-log").write_text(RECORDING_SESSION_LOG)
                self.recorded_session_start(package, home, record)
                self.assertFalse(record.exists(), "an unchanged settings.json was cleaned again")

                hooks = claude_hooks_registered(home / SEEDED_RELATIVE, 1)
                hooks["Stop"].append({"hooks": [LOOKALIKE_HOOK]})
                settings.write_text(encode({"hooks": hooks}))
                self.recorded_session_start(package, home, record)
                self.assertEqual(record.read_text(), "--entrypoint claude --harness claude repair\n")

    def dirty_settings(self, home):
        package = claude_package(home)
        settings = home / ".claude/settings.json"
        settings.write_text(json.dumps({"hooks": claude_hooks_registered(home / SEEDED_RELATIVE, REPEATED_REGISTRATIONS)}))
        return package, settings

    def assert_cleaned(self, package, home, settings):
        result = run_claude_hook(package, home, "session-start")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertEqual(json.loads(settings.read_text()), {})
        self.assertFalse((home / INSTALL_LOCK_RELATIVE).exists(), "session-start left the install lock behind")

    def change_version(self, home, package):
        (package / "VERSION").write_text(f"{PLUGIN_VERSION}-changed\n")
        return package

    def change_package_location(self, home, package):
        moved = harness_package(home, "elsewhere")
        (moved / "bin/session-log").write_text(RECORDING_SESSION_LOG)
        return moved

    def test_session_start_cleans_again_when_the_package_changes(self):
        for change in (self.change_version, self.change_package_location):
            with self.subTest(change.__name__), tempfile.TemporaryDirectory() as directory:
                home = Path(directory).resolve()
                package = claude_package(home)
                (home / ".claude/settings.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [LOOKALIKE_HOOK]}]}}))
                first = run_claude_hook(package, home, "session-start")
                self.assertEqual((first.returncode, first.stdout, first.stderr), (0, "", ""))
                self.assertTrue((home / LEGACY_CLEANUP_STAMP_RELATIVE).is_file())
                (package / "bin/session-log").write_text(RECORDING_SESSION_LOG)
                record = Path(directory) / "record"
                self.recorded_session_start(change(home, package), home, record)
                self.assertEqual(record.read_text() if record.exists() else None,
                                 "--entrypoint claude --harness claude repair\n")

    def test_session_start_removes_legacy_scripts_without_any_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            package = claude_package(home)
            script = home / ".claude/scripts/prompt_log_save.sh"
            script.parent.mkdir(parents=True)
            script.write_text("# universal-session-log: managed\nlegacy\n")
            result = run_claude_hook(package, home, "session-start")
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
            self.assertFalse(script.exists())
            self.assertFalse((home / ".claude/settings.json").exists())

    def test_session_start_removes_a_skill_root_link_into_the_plugin_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            package = claude_package(home)
            old = sandbox_plugin_root(home / PLUGIN_CACHE_RELATIVE.replace(PLUGIN_VERSION, OLD_PLUGIN_VERSION))
            link = home / SEEDED_RELATIVE
            link.parent.mkdir(parents=True)
            link.symlink_to(old / "skills/session-log")
            result = run_claude_hook(package, home, "session-start")
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
            self.assertFalse(os.path.lexists(link))
            self.assertTrue((old / "skills/session-log/install.sh").is_file())

    def test_session_start_cleans_again_when_legacy_scripts_reappear(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            package = claude_package(home)
            (home / ".claude/settings.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [LOOKALIKE_HOOK]}]}}))
            first = run_claude_hook(package, home, "session-start")
            self.assertEqual((first.returncode, first.stdout, first.stderr), (0, "", ""))
            script = home / ".claude/scripts/prompt_log_save.sh"
            script.parent.mkdir(parents=True)
            script.write_text("# universal-session-log: managed\nlegacy\n")
            again = run_claude_hook(package, home, "session-start")
            self.assertEqual((again.returncode, again.stdout, again.stderr), (0, "", ""))
            self.assertFalse(script.exists())

    def test_session_start_cleans_again_when_a_seeded_copy_appears(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            package = claude_package(home)
            (home / ".claude/settings.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [LOOKALIKE_HOOK]}]}}))
            first = run_claude_hook(package, home, "session-start")
            self.assertEqual((first.returncode, first.stdout, first.stderr), (0, "", ""))
            self.assertTrue((home / LEGACY_CLEANUP_STAMP_RELATIVE).is_file())
            seeded = home / SEEDED_RELATIVE
            shutil.copytree(REPO / "skills/session-log", seeded)
            write_seeded_manifest(seeded)
            second = run_claude_hook(package, home, "session-start")
            self.assertEqual((second.returncode, second.stdout, second.stderr), (0, "", ""))
            self.assertFalse(os.path.lexists(seeded))

    def test_unsafe_stamp_path_never_skips_the_cleanup(self):
        for kind in ("symlink", "directory"):
            with self.subTest(kind), tempfile.TemporaryDirectory() as directory:
                home = Path(directory).resolve()
                package, settings = self.dirty_settings(home)
                stamp = home / LEGACY_CLEANUP_STAMP_RELATIVE
                stamp.parent.mkdir(parents=True)
                target = home / "stamp-target"
                if kind == "symlink":
                    target.write_text("unrelated")
                    stamp.symlink_to(target)
                else:
                    stamp.mkdir()
                self.assert_cleaned(package, home, settings)
                self.assertTrue(stamp.is_symlink() if kind == "symlink" else stamp.is_dir())
                if kind == "symlink":
                    self.assertEqual(target.read_text(), "unrelated")

    def test_failed_cleanup_is_stamped_and_retried_once_settings_change(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            package, settings = self.dirty_settings(home)
            original = settings.read_bytes()
            hold_install_lock(home)
            result = run_claude_hook(package, home, "session-start")
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
            self.assertEqual(settings.read_bytes(), original)
            self.assertTrue((home / LEGACY_CLEANUP_STAMP_RELATIVE).is_file())

            shutil.rmtree(home / INSTALL_LOCK_RELATIVE)
            unchanged = run_claude_hook(package, home, "session-start")
            self.assertEqual((unchanged.returncode, unchanged.stdout, unchanged.stderr), (0, "", ""))
            self.assertEqual(settings.read_bytes(), original, "an unchanged settings.json was cleaned again")

            settings.write_text(json.dumps({"hooks": claude_hooks_registered(home / SEEDED_RELATIVE, 1)}))
            self.assert_cleaned(package, home, settings)

    def test_failed_stamp_write_leaves_no_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            home = root / "home"
            package, settings = self.dirty_settings(home)
            tools = link_tools(root / "bin", tuple(tool for tool in GIT_BASH_TOOLS_WITHOUT_JQ if tool != "mv"))
            record = root / "mv-called"
            (tools / "mv").write_text('#!/bin/sh\nprintf called > "$SESSION_LOG_TEST_RECORD"\nexit 1\n')
            (tools / "mv").chmod(EXECUTABLE_MODE)
            result = run_claude_hook(package, home, "session-start",
                                     environment={"HOME": str(home), "PATH": str(tools), "SESSION_LOG_TEST_RECORD": str(record)})
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
            self.assertTrue(record.exists(), "the failing mv was never reached")
            self.assertEqual(json.loads(settings.read_text()), {})
            stamp = home / LEGACY_CLEANUP_STAMP_RELATIVE
            self.assertEqual([path.name for path in stamp.parent.iterdir() if path.name.startswith(f".{stamp.name}")], [])
            self.assertFalse(stamp.exists())

    def test_jq_less_plugin_session_start_cleans_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            home = root / "home"
            plugin = claude_package(home)
            settings = home / ".claude/settings.json"
            settings.write_text(json.dumps({"hooks": {
                **claude_hooks_registered(home / SEEDED_RELATIVE, REPEATED_REGISTRATIONS),
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


# Cursor (3.23.12) imports enabled Claude Code plugins and ~/.claude/settings.json hooks and runs them itself,
# through two runners. The workbench writes `JSON.stringify` of a request carrying `cursor_version` to the hook's
# stdin (workbench.desktop.main.js `_executeCommandHookScript`). The agent-host daemon's default `argv_heredoc`
# transport appends `<<'CURSOR_HOOK_EOF'` with that JSON to the command (daemon.cjs `executeCommandScript`), so for
# hooks/hooks.json's `...; exec bash "$s" <event>; exit 0` the heredoc feeds the final `exit 0` and the hook reads
# nothing. Both runners also export CURSOR_VERSION, which child processes such as `claude -p` inherit.
CURSOR_VERSION_UNDER_TEST = "3.23.12"
CURSOR_HOOK_EVENTS = {"session-start": "sessionStart", "user-prompt": "beforeSubmitPrompt",
                      "stop": "stop", "subagent-stop": "subagentStop"}
CLAUDE_HOOK_EVENTS = {lifecycle: event for event, lifecycle in PLUGIN_EVENT_LIFECYCLES.items()}
CURSOR_HEREDOC_DELIMITER = "CURSOR_HOOK_EOF"


def enabled_claude_home(directory):
    """A home with logging on for Claude Code and the session-log package of a claude-goodies plugin."""
    home = Path(directory).resolve()
    package = claude_package(home)
    (home / ".claude/prompt-logs").mkdir(parents=True)
    (home / ".claude/prompt-logs/.enabled").write_text("")
    return home, package


def prompt_log_transcripts(home):
    return sorted(path for path in (home / ".claude/prompt-logs").rglob("*.md"))


class ClaudeHookInsideCursorTests(unittest.TestCase):
    """Run by Cursor instead of Claude Code, the Claude hook only runs the session-start legacy cleanup: it never
    logs, writes, or removes Claude runtime state. Claude Code keeps logging when it inherits Cursor's environment."""

    def require_jq(self):
        """Logging (and so a guard that failed to stop it) needs jq; logging off and the cleanup do not."""
        if shutil.which("jq") is None:
            self.skipTest("jq is not installed")

    def build_cursor_environment(self, home, plugin):
        return {**harness_environment(home), "CURSOR_VERSION": CURSOR_VERSION_UNDER_TEST,
                "CURSOR_PROJECT_DIR": str(home), "CLAUDE_PROJECT_DIR": str(home),
                "CURSOR_PLUGIN_ROOT": str(plugin.parents[1]), "CLAUDE_PLUGIN_ROOT": str(plugin.parents[1])}

    def build_cursor_payload(self, home, lifecycle):
        return json.dumps({"conversation_id": "cursor-conversation", "session_id": "cursor-conversation",
                           "hook_event_name": CURSOR_HOOK_EVENTS[lifecycle], "cursor_version": CURSOR_VERSION_UNDER_TEST,
                           "workspace_roots": [str(home)], "prompt": "cursor prompt", "cwd": str(home)},
                          separators=(",", ":"))

    def build_claude_payload(self, home, lifecycle, prompt="claude prompt"):
        return json.dumps({"session_id": "claude-session", "cwd": str(home), "prompt": prompt,
                           "hook_event_name": CLAUDE_HOOK_EVENTS[lifecycle]})

    def write_runtime_markers(self, home):
        """Markers a running Claude Code session wrote; Cursor must leave them alone, logging on or off."""
        state = home / ".claude/session-log"
        state.mkdir(parents=True, exist_ok=True)
        markers = [state / "runtime.json", state / "runtime.1.json"]
        for marker in markers:
            marker.write_text('{"harness": "claude"}')
        return {marker: marker.read_bytes() for marker in markers}

    def assert_untouched(self, home, markers=None, cleanup_ran=False):
        expected = markers or {}
        state = home / ".claude/session-log"
        if not expected and not cleanup_ran:
            self.assertFalse(state.exists(), "Cursor's hook created Claude's state directory")
        actual = {path: path.read_bytes() for path in state.glob("runtime*.json")} if state.exists() else {}
        self.assertEqual(actual, expected, "Cursor's hook wrote or removed a Claude runtime marker")
        self.assertEqual(prompt_log_transcripts(home), [], "Cursor's hook logged into the Claude prompt logs")
        self.assertFalse((home / ".claude/session-maps").exists(), "Cursor's hook wrote Claude session state")

    def test_every_lifecycle_stays_out_of_claude_logging(self):
        self.require_jq()
        for lifecycle in CURSOR_HOOK_EVENTS:
            with self.subTest(lifecycle=lifecycle), tempfile.TemporaryDirectory() as directory:
                home, plugin = enabled_claude_home(directory)
                result = run_claude_hook(plugin, home, lifecycle, payload=self.build_cursor_payload(home, lifecycle),
                                         environment=self.build_cursor_environment(home, plugin))
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
                self.assert_untouched(home)

    def test_logging_off_keeps_claude_runtime_markers(self):
        for lifecycle in CURSOR_HOOK_EVENTS:
            for payload_kind in ("payload", "empty stdin"):
                with self.subTest(lifecycle=lifecycle, payload=payload_kind), \
                        tempfile.TemporaryDirectory() as directory:
                    home, plugin = enabled_claude_home(directory)
                    (home / ".claude/prompt-logs/.enabled").unlink()
                    markers = self.write_runtime_markers(home)
                    payload = self.build_cursor_payload(home, lifecycle) if payload_kind == "payload" else ""
                    result = run_claude_hook(plugin, home, lifecycle, payload=payload,
                                             environment=self.build_cursor_environment(home, plugin))
                    self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
                    self.assert_untouched(home, markers)

    def run_with_heredoc(self, hook, home, plugin, lifecycle):
        """Cursor's agent-host daemon: the payload heredoc is appended to the whole hook command."""
        command = (f"{hook['command']} <<'{CURSOR_HEREDOC_DELIMITER}'\n"
                   f"{self.build_cursor_payload(home, lifecycle)}\n{CURSOR_HEREDOC_DELIMITER}")
        return subprocess.run(["bash", "-c", command], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              env=self.build_cursor_environment(home, plugin), timeout=HOOK_TIMEOUT_SECONDS)

    def test_cursor_session_start_still_removes_legacy_registrations(self):
        session_start = next(hook for event, hook in plugin_hooks() if event == "SessionStart")
        for transport in ("stdin", "heredoc"):
            with self.subTest(transport=transport), tempfile.TemporaryDirectory() as directory:
                home, plugin = enabled_claude_home(directory)
                settings = home / ".claude/settings.json"
                settings.write_text(json.dumps({"hooks": {**claude_hooks_registered(home / SEEDED_RELATIVE, 1),
                                                          "Notification": [{"hooks": [FOREIGN_HOOK]}]}}))
                if transport == "stdin":
                    result = run_claude_hook(plugin, home, "session-start",
                                             payload=self.build_cursor_payload(home, "session-start"),
                                             environment=self.build_cursor_environment(home, plugin))
                else:
                    result = self.run_with_heredoc(session_start, home, plugin, "session-start")
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
                self.assertEqual(json.loads(settings.read_text()), {"hooks": {"Notification": [{"hooks": [FOREIGN_HOOK]}]}})
                self.assert_untouched(home, cleanup_ran=True)

    def test_plugin_hook_commands_stay_out_of_claude_logging_on_stdin(self):
        self.require_jq()
        for event, hook in plugin_hooks():
            lifecycle = PLUGIN_EVENT_LIFECYCLES[event]
            with self.subTest(event=event), tempfile.TemporaryDirectory() as directory:
                home, plugin = enabled_claude_home(directory)
                result = run_plugin_hook_command("bash", hook, str(plugin.parents[1]), substituted=False,
                                                 environment=self.build_cursor_environment(home, plugin),
                                                 payload=self.build_cursor_payload(home, lifecycle))
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
                self.assert_untouched(home)

    def test_plugin_hook_commands_keep_claude_markers_with_a_heredoc_payload(self):
        # With logging off and no payload the hook would unlink these markers: only the guard keeps them.
        for event, hook in plugin_hooks():
            lifecycle = PLUGIN_EVENT_LIFECYCLES[event]
            with self.subTest(event=event), tempfile.TemporaryDirectory() as directory:
                home, plugin = enabled_claude_home(directory)
                (home / ".claude/prompt-logs/.enabled").unlink()
                markers = self.write_runtime_markers(home)
                result = self.run_with_heredoc(hook, home, plugin, lifecycle)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
                self.assert_untouched(home, markers)

    def test_plugin_hook_commands_reach_the_hook_with_a_claude_payload(self):
        """Positive control for the hooks.json commands above: run them as Claude Code does and they log."""
        self.require_jq()
        for event, hook in plugin_hooks():
            lifecycle = PLUGIN_EVENT_LIFECYCLES[event]
            with self.subTest(event=event), tempfile.TemporaryDirectory() as directory:
                home, plugin = enabled_claude_home(directory)
                result = run_plugin_hook_command("bash", hook, str(plugin.parents[1]), substituted=False,
                                                 environment=self.build_cursor_environment(home, plugin),
                                                 payload=self.build_claude_payload(home, lifecycle))
                self.assertEqual((result.returncode, result.stderr), (0, ""))
                self.assertTrue((home / ".claude/session-log/runtime.json").is_file(),
                                "the hooks.json command never reached claude_hook.sh")

    def test_claude_code_writes_runtime_markers_with_cursor_environment_inherited(self):
        self.require_jq()
        for lifecycle in CURSOR_HOOK_EVENTS:
            with self.subTest(lifecycle=lifecycle), tempfile.TemporaryDirectory() as directory:
                home, plugin = enabled_claude_home(directory)
                result = run_claude_hook(plugin, home, lifecycle, payload=self.build_claude_payload(home, lifecycle),
                                         environment=self.build_cursor_environment(home, plugin))
                self.assertEqual((result.returncode, result.stderr), (0, ""))
                self.assertTrue((home / ".claude/session-log/runtime.json").is_file(),
                                "Claude Code's hook wrote no runtime marker under Cursor's environment")

    def test_claude_code_logs_prompts_that_mention_the_key(self):
        self.require_jq()
        prompts = {"plain": "claude prompt", "exactly the key name": "cursor_version",
                   "key-like text": 'look at "cursor_version": "x" here'}
        for name, prompt in prompts.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as directory:
                home, plugin = enabled_claude_home(directory)
                result = run_claude_hook(plugin, home, "user-prompt",
                                         payload=self.build_claude_payload(home, "user-prompt", prompt),
                                         environment=self.build_cursor_environment(home, plugin))
                self.assertEqual((result.returncode, result.stderr), (0, ""))
                transcripts = prompt_log_transcripts(home)
                self.assertEqual(len(transcripts), 1, transcripts)
                self.assertIn(prompt, transcripts[0].read_text())


class ClaudeHookSilentFailureTests(unittest.TestCase):
    """A Claude hook never reports an error: every failure exits 0 with no output and no side effect."""

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
            package = claude_package(home / "package-home")
            (home / ".claude").write_text("not a directory")
            self.assert_silent(run_claude_hook(package, home, "user-prompt"))

    def test_symlinked_claude_paths_exit_silently(self):
        for relative in (".claude", ".claude/prompt-logs", ".claude/session-log"):
            with self.subTest(relative), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                home = root / "home"
                package = claude_package(root / "package-home")
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
            home, package = enabled_claude_home(directory)
            # The hook's parent process is this test process, so this is its runtime.$PPID.json.
            process_runtime = home / f".claude/session-log/runtime.{os.getpid()}.json"
            process_runtime.parent.mkdir(parents=True)
            process_runtime.symlink_to(home / "elsewhere")
            self.assert_silent(run_claude_hook(package, home, "user-prompt", payload=json.dumps({"session_id": "s"})))
            self.assertTrue(process_runtime.is_symlink())
            self.assertFalse((home / ".claude/session-log/runtime.json").exists())

    def test_missing_package_version_exits_silently(self):
        with tempfile.TemporaryDirectory() as directory:
            home, package = enabled_claude_home(directory)
            (package / "VERSION").unlink()
            self.assert_silent(run_claude_hook(package, home, "session-start", payload=SESSION_PAYLOAD), home)

    @unittest.skipIf(os.geteuid() == 0, "root reads files whatever their mode")
    def test_unreadable_package_version_exits_silently(self):
        """An unreadable VERSION makes the hook exit 0 silently, writing nothing."""
        with tempfile.TemporaryDirectory() as directory:
            home, package = enabled_claude_home(directory)
            (package / "VERSION").chmod(0)
            self.assert_silent(run_claude_hook(package, home, "session-start", payload=SESSION_PAYLOAD), home)

    def test_failing_lifecycle_script_keeps_its_stdout_and_exits_silently(self):
        if shutil.which("jq") is None:
            self.skipTest("jq is not installed")
        with tempfile.TemporaryDirectory() as directory:
            home, package = enabled_claude_home(directory)
            (package / "adapters/claude/scripts/prompt_log_save.sh").write_text(FAILING_LIFECYCLE_SCRIPT)
            result = run_claude_hook(package, home, "user-prompt", payload=json.dumps({"session_id": "s"}))
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, LIFECYCLE_SCRIPT_STDOUT, ""))

    def test_relocated_claude_root_exits_silently(self):
        with tempfile.TemporaryDirectory() as directory:
            home, package = enabled_claude_home(directory)
            (home / "relocated").mkdir()
            environment = {**harness_environment(home), "CLAUDE_CONFIG_DIR": str(home / "relocated")}
            self.assert_silent(run_claude_hook(package, home, "user-prompt", payload=SESSION_PAYLOAD,
                                               environment=environment), home)

    def test_missing_jq_while_logging_is_on_exits_silently(self):
        with tempfile.TemporaryDirectory() as directory:
            home, package = enabled_claude_home(directory)
            tools = link_tools(home / "git-bash-bin", GIT_BASH_TOOLS_WITHOUT_JQ)
            result = run_claude_hook(package, home, "user-prompt", payload=json.dumps({"session_id": "s"}),
                                     environment={"HOME": str(home), "PATH": str(tools)})
            self.assert_silent(result)
            self.assertFalse((home / ".claude/session-log/runtime.json").exists())

    def test_unsafe_runtime_file_exits_silently(self):
        with tempfile.TemporaryDirectory() as directory:
            home, package = enabled_claude_home(directory)
            runtime = home / ".claude/session-log/runtime.json"
            runtime.parent.mkdir(parents=True)
            runtime.symlink_to(home / "elsewhere")
            self.assert_silent(run_claude_hook(package, home, "user-prompt", payload=SESSION_PAYLOAD))
            self.assertTrue(runtime.is_symlink())
            self.assertFalse((home / "elsewhere").exists())
            self.assertEqual([path.name for path in runtime.parent.iterdir()], [runtime.name])

    def test_unknown_lifecycle_exits_silently(self):
        with tempfile.TemporaryDirectory() as directory:
            home, package = enabled_claude_home(directory)
            self.assert_silent(run_claude_hook(package, home, "bogus-event"), home)

    def test_missing_lifecycle_script_exits_silently(self):
        with tempfile.TemporaryDirectory() as directory:
            home, package = enabled_claude_home(directory)
            (package / "adapters/claude/scripts/prompt_log_save.sh").unlink()
            self.assert_silent(run_claude_hook(package, home, "user-prompt"), home)


class RepairCommandEdgeTests(unittest.TestCase):
    """`repair` reports every change it makes and refuses unsafe or invalid files without touching them."""

    maxDiff = None

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
                package = claude_package(home)
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

    def test_native_repair_refuses_a_missing_hook_asset(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            package = harness_package(home, ".codex")
            config = home / ".codex/hooks.json"
            config.write_text(json.dumps({"hooks": claude_hooks_registered(package, REPEATED_REGISTRATIONS)}))
            original = config.read_bytes()
            (package / "adapters/native/session_log_hook.py").unlink()
            self.assert_refused(run_session_log(package, home, "codex", "repair"), config, original)

    def test_repair_fails_while_another_install_holds_the_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            package = claude_package(home)
            settings = home / ".claude/settings.json"
            settings.write_text(json.dumps({"hooks": claude_hooks_registered(home / SEEDED_RELATIVE, REPEATED_REGISTRATIONS)}))
            original = settings.read_bytes()
            hold_install_lock(home)
            result = run_session_log(package, home, "claude", "repair")
            self.assert_refused(result, settings, original)
            self.assertIn("already in progress", result.stderr)

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


LEGACY_PACKAGE_PATH = REPO / "skills/session-log/lib/legacy_package.py"
SEEDED_MANIFEST = ".universal-session-log.manifest"
PLUGIN_CACHE_LINK_RELATIVE = f".claude/plugins/cache/harness-goodies/claude-goodies/{OLD_PLUGIN_VERSION}/skills/session-log"
# A remover that blocks (e.g. on a FIFO) fails the test instead of hanging the suite.
LEGACY_PACKAGE_TIMEOUT_SECONDS = 30
RELEASE_STORE_LINK_RELATIVE = ".local/share/universal-session-log/releases/1.0.0/skills/session-log"


def write_seeded_manifest(copy):
    """The ownership manifest older install.sh releases wrote: the SHA-256 of every file in ``copy``."""
    lines = ["format=1", "owner=universal-session-log", "version=2.0.0"]
    for path in sorted(item for item in copy.rglob("*") if item.is_file()):
        lines += [f"asset={path.relative_to(copy)}", f"sha256={hashlib.sha256(path.read_bytes()).hexdigest()}"]
    (copy / SEEDED_MANIFEST).write_text("\n".join(lines) + "\n")


class LegacyPackageCopyTests(unittest.TestCase):
    """A seeded package copy goes only when its manifest proves every file in it; symlinks are never followed."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.copy = self.home / SEEDED_RELATIVE

    def fresh_skill_root(self):
        """An empty skill root, whatever an earlier subtest left behind (even if it failed)."""
        if self.copy.parent.exists():
            shutil.rmtree(self.copy.parent)
        self.copy.parent.mkdir(parents=True)

    def seeded(self):
        self.fresh_skill_root()
        shutil.copytree(REPO / "skills/session-log", self.copy)
        write_seeded_manifest(self.copy)
        return self.copy

    def remove(self, path=None):
        result = subprocess.run(
            [sys.executable, str(LEGACY_PACKAGE_PATH), "remove", str(path or self.copy)],
            capture_output=True, text=True, env=harness_environment(self.home), timeout=LEGACY_PACKAGE_TIMEOUT_SECONDS,
        )
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        return result.stdout

    def assert_kept(self, reason):
        files = sorted(path for path in self.copy.rglob("*"))
        self.assertEqual(self.remove(), f"kept: {reason}\n")
        self.assertEqual(sorted(path for path in self.copy.rglob("*")), files)

    def test_removes_a_copy_its_manifest_proves(self):
        self.seeded()
        self.assertEqual(self.remove(), "removed\n")
        self.assertFalse(os.path.lexists(self.copy))
        self.assertTrue(self.copy.parent.is_dir())

    def test_removes_the_bytecode_python_wrote_beside_its_modules(self):
        copy = self.seeded()
        (copy / "lib/__pycache__").mkdir(exist_ok=True)
        (copy / "lib/__pycache__/pathsafe.cpython-314.pyc").write_bytes(b"bytecode")
        self.assertEqual(self.remove(), "removed\n")
        self.assertFalse(os.path.lexists(copy))

    def test_keeps_a_copy_with_a_changed_file(self):
        copy = self.seeded()
        with (copy / "SKILL.md").open("a") as handle:
            handle.write("changed\n")
        self.assert_kept("SKILL.md was changed")

    def test_keeps_a_copy_with_a_file_it_did_not_install(self):
        copy = self.seeded()
        (copy / "notes.md").write_text("mine\n")
        self.assert_kept("notes.md is not a file session-log installed")

    def test_keeps_a_copy_holding_a_symlink(self):
        copy = self.seeded()
        (copy / "scripts").mkdir()
        (copy / "scripts/session_log_usage.ts").symlink_to(copy / "adapters/omp/session_log_usage.ts")
        self.assert_kept("scripts/session_log_usage.ts is not a file session-log installed")

    def test_keeps_a_marked_copy_without_a_manifest(self):
        self.copy.parent.mkdir(parents=True)
        shutil.copytree(REPO / "skills/session-log", self.copy)
        self.assert_kept("it has no ownership manifest")

    def test_keeps_a_copy_with_an_invalid_manifest(self):
        copy = self.seeded()
        (copy / SEEDED_MANIFEST).write_text("format=1\nowner=someone-else\n")
        self.assert_kept("its ownership manifest is invalid")

    def test_keeps_a_copy_with_an_invalid_manifest_of_any_kind(self):
        valid_head = "format=1\nowner=universal-session-log\nversion=2.0.0\n"
        cases = {
            "missing version": "format=1\nowner=universal-session-log\n",
            "odd asset lines": valid_head + "asset=SKILL.md\n",
            "bad asset prefix": valid_head + "path=SKILL.md\nsha256=00\n",
            "bad digest prefix": valid_head + "asset=SKILL.md\nmd5=00\n",
            "not UTF-8": b"format=1\xff\n",
        }
        for name, contents in cases.items():
            with self.subTest(name):
                copy = self.seeded()
                manifest = copy / SEEDED_MANIFEST
                manifest.write_bytes(contents if isinstance(contents, bytes) else contents.encode())
                self.assert_kept("its ownership manifest is invalid")
        for kind in ("symlink", "fifo"):
            with self.subTest(kind):
                copy = self.seeded()
                manifest = copy / SEEDED_MANIFEST
                manifest.unlink()
                if kind == "symlink":
                    manifest.symlink_to(copy / "SKILL.md")
                else:
                    os.mkfifo(manifest)
                self.assert_kept("its ownership manifest is invalid")

    def test_keeps_bytecode_and_files_outside_the_places_python_writes_them(self):
        for relative in ("lib/stray.pyc", "lib/__pycache__/notes.txt"):
            with self.subTest(relative):
                copy = self.seeded()
                (copy / relative).parent.mkdir(exist_ok=True)
                (copy / relative).write_bytes(b"not session-log's")
                self.assert_kept(f"{relative} is not a file session-log installed")

    def test_tolerates_the_finder_metadata_macos_writes(self):
        copy = self.seeded()
        (copy / ".DS_Store").write_bytes(b"finder")
        (copy / "lib/.DS_Store").write_bytes(b"finder")
        self.assertEqual(self.remove(), "removed\n")
        self.assertFalse(os.path.lexists(copy))

    @unittest.skipIf(os.geteuid() == 0, "root ignores directory permissions")
    def test_keeps_a_copy_it_cannot_read_without_a_traceback(self):
        copy = self.seeded()
        (copy / "lib").chmod(0)
        self.addCleanup((copy / "lib").chmod, PRIVATE_DIRECTORY_MODE)
        self.assertEqual(self.remove(), "kept: cannot read lib: Permission denied\n")
        self.assertTrue((copy / SEEDED_MANIFEST).is_file())

    def in_process(self):
        with patch.object(sys, "path", [str(LEGACY_PACKAGE_PATH.parent), *sys.path]):
            return load_module(LEGACY_PACKAGE_PATH, "legacy_package_race_test")

    @unittest.skipIf(os.geteuid() == 0, "root ignores directory permissions")
    def test_stays_silent_about_a_copy_it_cannot_prove_is_session_logs(self):
        for name, unreadable in (("copy directory", ""), ("SKILL.md of a manifest-less copy", "SKILL.md")):
            with self.subTest(name):
                self.fresh_skill_root()
                shutil.copytree(REPO / "skills/session-log", self.copy)
                (self.copy / unreadable).chmod(0)
                try:
                    self.assertEqual(self.remove(), "")
                finally:
                    (self.copy / unreadable).chmod(PRIVATE_DIRECTORY_MODE)
                self.assertTrue((self.copy / "install.sh").is_file())

    def test_keeps_the_manifest_when_the_copy_changes_after_its_files_are_removed(self):
        changes = {
            "a top-level file appears": (lambda copy: (copy / "notes.md").write_text("mine\n"), "notes.md"),
            "the manifest is rewritten": (
                lambda copy: (copy / SEEDED_MANIFEST).write_text("format=1\nowner=universal-session-log\nversion=9.9.9\n"),
                SEEDED_MANIFEST),
        }
        for name, (change, culprit) in changes.items():
            with self.subTest(name):
                copy = self.seeded()
                module = self.in_process()
                removed = module._remove_tree

                def remove_then_change(*arguments):
                    removed(*arguments)
                    if not arguments[1]:
                        change(copy)

                with patch.object(module, "_remove_tree", side_effect=remove_then_change):
                    result = module.remove(str(copy), str(self.home))
                self.assertEqual(result, f"kept: {culprit} changed during removal")
                self.assertTrue((copy / SEEDED_MANIFEST).is_file())

    def test_keeps_a_copy_that_changes_between_its_check_and_removal(self):
        changes = {
            "a file appears": (lambda copy: (copy / "lib/notes.md").write_text("mine\n"), "lib/notes.md"),
            "a checked file is rewritten": (
                lambda copy: (copy / "VERSION").write_text("9.9.9 rewritten by the user\n"), "VERSION"),
        }
        for name, (change, culprit) in changes.items():
            with self.subTest(name):
                copy = self.seeded()
                module = self.in_process()
                checked = module._inventory

                def check_then_change(*arguments):
                    result = checked(*arguments)
                    if not arguments[1]:
                        change(copy)
                    return result

                with patch.object(module, "_inventory", side_effect=check_then_change):
                    result = module.remove(str(copy), str(self.home))
                self.assertEqual(result, f"kept: {culprit} changed during removal")
                # The manifest goes last, so a later run still knows the copy is session-log's.
                self.assertTrue((copy / SEEDED_MANIFEST).is_file())

    def test_a_later_run_removes_a_partly_removed_copy_once_the_stray_file_is_gone(self):
        copy = self.seeded()
        module = self.in_process()
        checked = module._inventory

        def check_then_add(*arguments):
            result = checked(*arguments)
            if not arguments[1]:
                (copy / "lib/notes.md").write_text("mine\n")
            return result

        with patch.object(module, "_inventory", side_effect=check_then_add):
            self.assertEqual(module.remove(str(copy), str(self.home)), "kept: lib/notes.md changed during removal")
        self.assertEqual(self.remove(), "kept: lib/notes.md is not a file session-log installed\n")
        (copy / "lib/notes.md").unlink()
        self.assertEqual(self.remove(), "removed\n")
        self.assertFalse(os.path.lexists(copy))

    def test_leaves_a_directory_that_is_not_session_logs_alone_and_silent(self):
        self.copy.mkdir(parents=True)
        (self.copy / "SKILL.md").write_text("---\nname: session-log\n---\nmine\n")
        self.assertEqual(self.remove(), "")
        self.assertTrue((self.copy / "SKILL.md").is_file())
        self.assertEqual(self.remove(self.home / "absent/session-log"), "")

    def test_removes_only_links_into_a_plugin_cache_or_release_store(self):
        self.copy.parent.mkdir(parents=True)
        live_target = self.home / PLUGIN_CACHE_LINK_RELATIVE
        live_target.mkdir(parents=True)
        (live_target / "SKILL.md").write_text("kept\n")
        for target in (live_target, self.home / RELEASE_STORE_LINK_RELATIVE):
            with self.subTest(target=target):
                self.copy.symlink_to(target)
                self.assertEqual(self.remove(), "removed link\n")
                self.assertFalse(os.path.lexists(self.copy))
        self.assertEqual((live_target / "SKILL.md").read_text(), "kept\n")
        foreign = (self.home / "elsewhere/session-log",
                   self.home / ".claude/plugins/cache/x/../../../elsewhere/claude-goodies/1.0.0/skills/session-log")
        for target in foreign:
            with self.subTest(target=target):
                self.copy.symlink_to(target)
                self.assertEqual(self.remove(), "")
                self.assertEqual(os.readlink(self.copy), str(target))
                self.copy.unlink()

    def test_recognizes_a_link_spelled_through_the_resolved_home(self):
        self.copy.parent.mkdir(parents=True)
        self.copy.symlink_to(self.home / PLUGIN_CACHE_LINK_RELATIVE)
        linked_home = self.home / "linked-home"
        linked_home.symlink_to(self.home)
        result = subprocess.run(
            [sys.executable, str(LEGACY_PACKAGE_PATH), "remove", str(self.copy)],
            capture_output=True, text=True, env={**harness_environment(self.home), "HOME": str(linked_home)},
            timeout=LEGACY_PACKAGE_TIMEOUT_SECONDS,
        )
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "removed link\n", ""))
        self.assertFalse(os.path.lexists(self.copy))

    @unittest.skipIf(os.geteuid() == 0, "root ignores directory permissions")
    def test_reports_a_recognized_link_it_cannot_remove(self):
        self.copy.parent.mkdir(parents=True)
        self.copy.symlink_to(self.home / PLUGIN_CACHE_LINK_RELATIVE)
        self.copy.parent.chmod(READ_ONLY_DIRECTORY_MODE)
        self.addCleanup(self.copy.parent.chmod, PRIVATE_DIRECTORY_MODE)
        self.assertEqual(self.remove(), "kept: cannot remove it: Permission denied\n")
        self.assertTrue(self.copy.is_symlink())

    def test_never_follows_a_symlinked_skill_root(self):
        real_root = self.home / "dotfiles/skills"
        real_copy = real_root / "session-log"
        real_root.mkdir(parents=True)
        shutil.copytree(REPO / "skills/session-log", real_copy)
        write_seeded_manifest(real_copy)
        (self.home / ".claude").mkdir()
        (self.home / ".claude/skills").symlink_to(real_root)
        self.assertEqual(self.remove(), "")
        self.assertTrue((real_copy / SEEDED_MANIFEST).is_file())


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

    def test_on_relinks_an_adapter_link_into_another_session_log_package(self):
        for harness, (subroot, target, source) in ADAPTER_LINKS.items():
            with self.subTest(harness=harness), tempfile.TemporaryDirectory() as directory:
                home = Path(directory).resolve()
                package = harness_package(home, "cache/2.0.0")
                # The copy older releases seeded into the skill root, and another package version.
                for previous in (harness_package(home, subroot), harness_package(home, "cache/1.0.0")):
                    link = home / subroot / target
                    link.parent.mkdir(parents=True, exist_ok=True)
                    if os.path.lexists(link):
                        link.unlink()
                    link.symlink_to(previous / source)
                    result = run_session_log(package, home, harness, "on")
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(os.readlink(link), str(package / source))

    def foreign_destinations(self, home, source):
        """Link targets that are not a session-log package's adapter, including dotted lookalikes."""
        return (str(home / "elsewhere/session-log.js"), f"{home}/evil/../skills/session-log/{source}",
                f"../skills/session-log/{source}", f"./skills/session-log/{source}")

    def test_on_refuses_an_adapter_link_to_a_foreign_file(self):
        for harness, (subroot, target, source) in ADAPTER_LINKS.items():
            with tempfile.TemporaryDirectory() as directory:
                home = Path(directory).resolve()
                package = harness_package(home, "cache/2.0.0")
                link = home / subroot / target
                link.parent.mkdir(parents=True)
                for destination in self.foreign_destinations(home, source):
                    with self.subTest(harness=harness, destination=destination):
                        link.symlink_to(destination)
                        result = run_session_log(package, home, harness, "on")
                        # The shell preflight refuses it before anything is installed.
                        self.assertEqual((result.returncode, result.stderr),
                                         (1, f"session-log: refusing to overwrite unowned adapter link: {link}\n"))
                        self.assertEqual(os.readlink(link), destination)
                        self.assertFalse((home / subroot / "prompt-logs/.enabled").exists())
                        link.unlink()

    def test_link_adapter_refuses_a_dotted_package_lookalike(self):
        for harness, (subroot, target, source) in ADAPTER_LINKS.items():
            with tempfile.TemporaryDirectory() as directory:
                home = Path(directory).resolve()
                package = harness_package(home, "cache/2.0.0")
                link = home / subroot / target
                link.parent.mkdir(parents=True)
                for destination in self.foreign_destinations(home, source)[1:]:
                    with self.subTest(harness=harness, destination=destination):
                        link.symlink_to(destination)
                        result = subprocess.run(
                            [sys.executable, str(package / "lib/pathsafe.py"), "link-adapter"], capture_output=True, text=True,
                            env={**harness_environment(home), "SESSION_LOG_SOURCE": str(package / source),
                                 "SESSION_LOG_TARGET": str(link), "SESSION_LOG_STORE": str(home / "store"),
                                 "SESSION_LOG_ADAPTER_RELATIVE": source})
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("refusing to overwrite unowned adapter link", result.stderr)
                        self.assertEqual(os.readlink(link), destination)
                        link.unlink()

    def test_on_migrates_a_marked_standalone_adapter_copy(self):
        for harness, (subroot, target, source) in ADAPTER_LINKS.items():
            with self.subTest(harness=harness), tempfile.TemporaryDirectory() as directory:
                home = Path(directory).resolve()
                package = harness_package(home, "cache/2.0.0")
                standalone = home / subroot / target
                standalone.parent.mkdir(parents=True)
                standalone.write_text("// universal-session-log: managed\nlegacy standalone copy\n")
                result = run_session_log(package, home, harness, "on")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(os.readlink(standalone), str(package / source))

    def test_status_with_logging_on_migrates_a_marked_standalone_adapter_copy(self):
        for harness, (subroot, target, source) in ADAPTER_LINKS.items():
            with self.subTest(harness=harness), tempfile.TemporaryDirectory() as directory:
                home = Path(directory).resolve()
                package = harness_package(home, "cache/2.0.0")
                standalone = home / subroot / target
                standalone.parent.mkdir(parents=True)
                standalone.write_text("// universal-session-log: managed\nlegacy standalone copy\n")
                flag = home / subroot / "prompt-logs/.enabled"
                flag.parent.mkdir(parents=True)
                flag.write_text("enabled\n")
                flag.chmod(0o600)
                result = run_session_log(package, home, harness, "status")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(os.readlink(standalone), str(package / source))

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
    """The SessionStart legacy cleanup is best effort: without a working python3 the hook still exits 0 silently."""

    def run_session_start(self, python3_stub):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            home = root / "home"
            plugin = claude_package(home)
            settings = home / ".claude/settings.json"
            settings.write_text(json.dumps({"hooks": claude_hooks_registered(home / SEEDED_RELATIVE, DEAD_REGISTRATIONS)}))
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
