import importlib.util
import io
import json
import os
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
