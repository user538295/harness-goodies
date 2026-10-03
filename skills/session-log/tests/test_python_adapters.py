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
CURSOR_TURNS = (
    {"generation_id": "generation-one", "input_tokens": 1000, "output_tokens": 50,
     "cache_read_tokens": 600, "cache_write_tokens": 300},
    {"generation_id": "generation-two", "input_tokens": 2000, "output_tokens": 70,
     "cache_read_tokens": 1500, "cache_write_tokens": 400},
)
EXPECTED_CURSOR_TOTALS = [
    "input_tokens: 3000", "cache_read_tokens: 2100", "cache_write_tokens: 700",
    "output_tokens: 120", "total_tokens: 3120",
]
CURSOR_TOKENS_SUFFIX = ".tokens.jsonl"
INVALID_CURSOR_TOKEN_LINES = (
    "{not json",
    "[1, 2]",
    json.dumps({"input_tokens": 9}),
    json.dumps({"generation_id": "../escape", "input_tokens": 9}),
    json.dumps({"generation_id": "generation-invalid", "input_tokens": -1, "output_tokens": True,
                "cache_read_tokens": "7", "cache_write_tokens": 1.5}),
)
CURSOR_STATE_DB_PATHS = {
    "darwin": Path("Library/Application Support/Cursor/User/globalStorage/state.vscdb"),
    "linux": Path(".config/Cursor/User/globalStorage/state.vscdb"),
}
SUBAGENTS_HEADER = "SUBAGENTS (final context tokens each, not billed totals)"
NO_SUBAGENTS = ["subagents: 0", "final_context_tokens: 0"]
SUBPROCESS_TIMEOUT_SECONDS = 10
OPEN_TOKEN_FILE_SCRIPT = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location("cursor_usage", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.open_token_file(sys.argv[2]).close()
"""


class CursorUsageAggregationTests(unittest.TestCase):
    def setUp(self):
        self.usage = load_module(CURSOR_USAGE_PATH, "cursor_session_log_usage_under_test")

    def aggregate(self, *lines):
        return self.usage.aggregate_tokens(io.StringIO("".join(f"{line}\n" for line in lines)))

    def test_aggregate_tokens_sums_valid_records_and_skips_invalid_lines(self):
        records, totals = self.aggregate(
            json.dumps(CURSOR_TURNS[0]), *INVALID_CURSOR_TOKEN_LINES,
            json.dumps({"generation_id": "generation-partial", "input_tokens": -4, "output_tokens": 5}),
        )
        self.assertEqual(records, 2)
        self.assertEqual(totals, {"input_tokens": 1000, "cache_read_tokens": 600, "cache_write_tokens": 300,
                                  "output_tokens": 55, "total_tokens": 1055})

    def test_aggregate_tokens_keeps_last_valid_record_per_generation(self):
        corrected = {"generation_id": "generation-one", "input_tokens": 10, "output_tokens": 1}
        records, totals = self.aggregate(
            json.dumps(CURSOR_TURNS[0]), json.dumps(CURSOR_TURNS[0]), json.dumps(corrected),
            json.dumps({"generation_id": "generation-one", "input_tokens": -1}),
        )
        self.assertEqual(records, 1)
        self.assertEqual(totals, {"input_tokens": 10, "cache_read_tokens": 0, "cache_write_tokens": 0,
                                  "output_tokens": 1, "total_tokens": 11})

    def test_aggregate_tokens_reports_zero_records_for_invalid_input(self):
        records, _ = self.aggregate(*INVALID_CURSOR_TOKEN_LINES)
        self.assertEqual(records, 0)

    def test_open_token_file_refuses_fifo_and_symlink_without_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            fifo = Path(directory) / f"session_fifo{CURSOR_TOKENS_SUFFIX}"
            os.mkfifo(fifo)
            regular = Path(directory) / f"session_regular{CURSOR_TOKENS_SUFFIX}"
            regular.write_text(json.dumps(CURSOR_TURNS[0]) + "\n")
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

    def test_subagent_lines_reports_unavailable_on_unsupported_platform(self):
        self.assertEqual(self.usage.subagent_lines(CURSOR_SESSION_ID, platform="win32"),
                         ["subagents: unavailable: unsupported platform: win32"])


def composer(context_tokens, root=CURSOR_SESSION_ID, parent=CURSOR_SESSION_ID):
    return {"contextTokensUsed": context_tokens,
            "subagentInfo": {"parentComposerId": parent, "rootParentConversationId": root,
                             "subagentTypeName": "explore"}}


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
        self.feed_session()

    def run_session_log(self, *arguments, cwd=None):
        return subprocess.run(
            [str(REPO / "skills/session-log/bin/session-log"),
             "--entrypoint", "cursor", "--harness", "cursor", *arguments],
            cwd=cwd or self.project, env={**os.environ, "HOME": str(self.home)},
            text=True, capture_output=True,
        )

    def token_file(self, session=CURSOR_SESSION_ID):
        recorded = next(self.home.glob(f".cursor/prompt-logs/*/session_{CURSOR_SESSION_ID}{CURSOR_TOKENS_SUFFIX}"))
        return recorded.parent / f"session_{session}{CURSOR_TOKENS_SUFFIX}"

    def append_token_lines(self, *lines, session=CURSOR_SESSION_ID):
        with self.token_file(session).open("a", encoding="utf-8") as handle:
            handle.writelines(f"{line}\n" for line in lines)

    def send_hook(self, event, payload):
        base = {
            "conversation_id": CURSOR_SESSION_ID, "session_id": CURSOR_SESSION_ID,
            "model": "claude-opus-5-5-medium", "workspace_roots": [str(self.project)],
        }
        result = subprocess.run(
            [sys.executable, str(HOOK_PATH), "cursor", event],
            input=json.dumps({**base, **payload}), env={**os.environ, "HOME": str(self.home)},
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def feed_session(self):
        self.send_hook("session-start", {})
        for number, turn in enumerate(CURSOR_TURNS, start=1):
            self.send_hook("user-prompt", {"generation_id": turn["generation_id"], "prompt": f"prompt {number}"})
            self.send_hook("assistant-response", {**turn, "text": f"response {number}"})
            self.send_hook("stop", {**turn, "status": "completed"})

    def assert_totals_report(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("native token usage unavailable", result.stdout)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], "Cursor: usage", result.stdout)
        self.assertIn("TOTAL", lines, result.stdout)
        start = lines.index("TOTAL") + 1
        self.assertEqual(lines[start:start + len(EXPECTED_CURSOR_TOTALS)], EXPECTED_CURSOR_TOTALS, result.stdout)

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

    def assert_subagents_report(self, expected):
        result = self.run_session_log("usage", CURSOR_SESSION_ID)
        self.assert_totals_report(result)
        lines = result.stdout.rstrip("\n").splitlines()
        self.assertIn(SUBAGENTS_HEADER, lines, result.stdout)
        start = lines.index(SUBAGENTS_HEADER)
        self.assertEqual(lines.index("TOTAL") + len(EXPECTED_CURSOR_TOTALS) + 1, start, result.stdout)
        self.assertEqual(lines[start + 1:], expected, result.stdout)

    def test_cursor_usage_sums_final_context_of_session_subagents_without_writing_db(self):
        path = self.create_state_db({
            "composerData:child-one": composer(1000),
            "composerData:child-nested": composer(250, parent="child-one"),
            "composerData:child-other-session": composer(9000, root="other-session", parent="other-session"),
            f"composerData:{CURSOR_SESSION_ID}": {"contextTokensUsed": 5000},
        })
        before = path.read_bytes()
        self.assert_subagents_report(["subagents: 2", "final_context_tokens: 1250"])
        self.assertEqual(path.read_bytes(), before)

    def test_cursor_usage_ignores_invalid_subagent_context_tokens(self):
        invalid_values = (-1, "7", True, 1.5, None)
        self.create_state_db({
            "composerData:child-valid": composer(1000),
            **{f"composerData:child-invalid-{index}": composer(value) for index, value in enumerate(invalid_values)},
        })
        self.assert_subagents_report([f"subagents: {1 + len(invalid_values)}", "final_context_tokens: 1000"])

    def test_cursor_usage_reports_zero_subagents(self):
        self.create_state_db({f"composerData:{CURSOR_SESSION_ID}": {"contextTokensUsed": 5000}})
        self.assert_subagents_report(NO_SUBAGENTS)

    def test_cursor_usage_reports_subagents_unavailable_without_state_db(self):
        self.assert_subagents_report(
            [f"subagents: unavailable: Cursor state database not found: {self.home / CURSOR_STATE_DB_PATHS[sys.platform]}"])

    def test_cursor_usage_reports_subagents_unavailable_for_corrupt_state_db(self):
        self.state_db().write_bytes(b"not a sqlite database" * 100)
        self.assert_subagents_report(
            ["subagents: unavailable: cannot read Cursor state database: file is not a database"])

    def test_cursor_usage_reports_totals_from_stop_hook_tokens(self):
        self.assert_totals_report(self.run_session_log("usage"))
        self.assert_totals_report(self.run_session_log("usage", "--latest"))
        self.assert_totals_report(self.run_session_log("usage", CURSOR_SESSION_ID))

    def test_cursor_usage_ignores_redelivered_stop_for_same_generation(self):
        self.send_hook("stop", {**CURSOR_TURNS[0], "status": "completed"})
        self.assert_totals_report(self.run_session_log("usage", CURSOR_SESSION_ID))

    def test_cursor_stop_without_token_fields_records_nothing(self):
        self.send_hook("stop", {"session_id": "tokenless", "conversation_id": "tokenless",
                                "generation_id": "generation-three", "status": "completed"})
        result = self.run_session_log("usage", "tokenless")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("Cursor session has no token usage records: tokenless", result.stderr)

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

    def test_cursor_stop_hook_appends_despite_corrupt_tokens_file(self):
        self.append_token_lines("{not json", json.dumps({"input_tokens": 9}))
        third = {"generation_id": "generation-three", "input_tokens": 5, "output_tokens": 2}
        self.send_hook("stop", {**third, "status": "completed"})
        last_line = self.token_file().read_text(encoding="utf-8").splitlines()[-1]
        self.assertEqual(json.loads(last_line), third)

    def test_cursor_usage_skips_corrupt_token_lines(self):
        self.append_token_lines(*INVALID_CURSOR_TOKEN_LINES)
        self.assert_totals_report(self.run_session_log("usage", CURSOR_SESSION_ID))

    def test_cursor_usage_counts_duplicate_generation_lines_once(self):
        self.append_token_lines(*(json.dumps(turn) for turn in CURSOR_TURNS))
        self.assert_totals_report(self.run_session_log("usage", CURSOR_SESSION_ID))

    def test_cursor_usage_fails_when_all_token_lines_are_invalid(self):
        self.append_token_lines(*INVALID_CURSOR_TOKEN_LINES, session="corrupt-only")
        result = self.run_session_log("usage", "corrupt-only")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(f"Cursor session has no token usage records: {self.token_file('corrupt-only')}", result.stderr)
        self.assertNotIn("total_tokens", result.stdout)

    def test_cursor_usage_latest_resolves_workspace_from_subdirectory(self):
        subdirectory = self.project / "src" / "nested"
        subdirectory.mkdir(parents=True)
        self.assert_totals_report(self.run_session_log("usage", "--latest", cwd=subdirectory))

    def test_cursor_usage_rejects_path_targets(self):
        transcript = self.token_file()
        for target in (str(transcript), f"./{transcript.name}"):
            result = self.run_session_log("usage", target, cwd=transcript.parent)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("Cursor usage accepts only a session ID or --latest", result.stderr)


if __name__ == "__main__":
    unittest.main()
