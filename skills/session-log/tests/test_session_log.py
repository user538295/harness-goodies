"""Entry script (C1): argument and exit-code contract, Python version gate (S55), UTF-8 output (S54) and the 3.4-parseable syntax."""
import ast
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tokenize
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import session_log

SKILL_DIR = Path(__file__).resolve().parent.parent
ENTRY = SKILL_DIR / "session_log.py"
CLAUDE_FOLDER = ".claude"
LOG_FOLDER = "prompt-logs"
ON_FLAG = ".log_enabled"
SYNC_LOCK = ".sync.lock"
OLD_PYTHON = (3, 4, 10)
PROMPT_AT = "2099-06-01T10:00:00.000Z"
REPLY_AT = "2099-06-01T10:00:01.000Z"
LONG_AGO = "2000-01-01T00:00:00+00:00"
FIXED_NOW = datetime(2000, 1, 1, tzinfo=timezone.utc)
UNICODE_PROMPT = "needs \u2265 3.14 for \u0151"
CWD = "/work/proj"
PRICED_MODEL = "claude-opus-5-5"
UNPRICED_MODEL = "claude-x"
REQUIREMENT_LINE = "session-log: Python 3.14+ required (found 3.4)"
FIX_PREFIX = "session-log: fix: "
DARWIN_FIX = "brew install python@3.14 or python.org/downloads, then make python3 resolve to it"
WIN32_FIX = "install the Python install manager from python.org/downloads/windows"
FIX_HINTS = {
    "darwin": ("brew install python@3.14", "python.org/downloads", "python3"),
    "win32": ("Python install manager", "python.org/downloads/windows"),
    "linux": ("package manager", "pyenv", "uv", "python3"),
}
NOT_PARSEABLE_BY_PYTHON_3_4 = (ast.JoinedStr, ast.AnnAssign, ast.AsyncFunctionDef, ast.Await, ast.AsyncFor, ast.AsyncWith,
                               ast.NamedExpr, ast.Match, ast.TypeAlias, ast.TryStar, ast.MatMult)
TRAILING_COMMA_AFTER_UNPACKING = re.compile(r"\*\*?\w+\s*,\s*\)")
MODULE_LEVEL_IMPORTS = frozenset(("os", "sys", "datetime"))
FUNCTION_LEVEL_IMPORTS = frozenset(("sessionlog", "pathlib"))
UNPARENTHESIZED_EXCEPT = re.compile(r"except\s+[\w.]+\s*,")
SUBPROCESS_ENV_NAMES = ("PATH", "HOME", "USERPROFILE", "SYSTEMROOT")


class SandboxCase(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        self.claude = os.path.join(self.home, CLAUDE_FOLDER)
        self.log_root = os.path.join(self.claude, LOG_FOLDER)

    def environment(self, stdout, stderr, **kwargs):
        kwargs.setdefault("home", self.home)
        kwargs.setdefault("get_cwd", lambda: CWD)
        kwargs.setdefault("now", lambda: FIXED_NOW)
        kwargs.setdefault("to_local", lambda moment: moment.astimezone(timezone.utc))
        return session_log.Environment(stdout=stdout, stderr=stderr, **kwargs)

    def run_main(self, argv, **kwargs):
        stdout, stderr = io.StringIO(), io.StringIO()
        code = session_log.main(argv, self.environment(stdout, stderr, **kwargs))
        return code, stdout.getvalue(), stderr.getvalue()

    def home_entries(self):
        return sorted(os.listdir(self.home))

    def write_transcript(self, cwd=CWD, model=UNPRICED_MODEL):
        project = os.path.join(self.claude, "projects", "-work-proj")
        os.makedirs(project)
        records = [
            {"type": "user", "promptSource": "typed", "cwd": cwd, "timestamp": PROMPT_AT,
             "message": {"role": "user", "content": UNICODE_PROMPT}},
            {"type": "assistant", "timestamp": REPLY_AT,
             "message": {"id": "m0", "model": model, "content": [{"type": "text", "text": "reply"}],
                         "usage": {"input_tokens": 1000, "output_tokens": 1000}}},
        ]
        with open(os.path.join(project, "abc123.jsonl"), "w", encoding="utf-8", newline="\n") as stream:
            stream.write("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records))

    def turn_logging_on(self):
        """Runs `on` with the injected clock; a transcript written before it is newer than the on-time."""
        code, _, stderr = self.run_main(["claude", "on"])
        self.assertEqual((0, ""), (code, stderr))

    def turn_logging_on_long_ago(self):
        os.makedirs(self.log_root)
        with open(os.path.join(self.log_root, ON_FLAG), "w", encoding="utf-8") as stream:
            stream.write(LONG_AGO + "\n")


class ArgumentTests(SandboxCase):
    def test_unsupported_or_missing_harness_exits_2_with_session_log_prefix_writes_nothing(self):
        for argv in ([], ["bogus", "on"], ["bogus"], [""]):
            with self.subTest(argv=argv):
                code, stdout, stderr = self.run_main(argv)
                self.assertEqual(2, code)
                self.assertEqual("", stdout)
                self.assertTrue(stderr.startswith("session-log: "), stderr)
                self.assertEqual(1, len(stderr.splitlines()))
                self.assertEqual([], self.home_entries())

    def test_bad_arguments_exit_2_without_argparse_usage_and_runtime_error_exits_1(self):
        for argv in (["claude"], ["claude", "frobnicate"], ["claude", "on", "extra"], ["claude", "off", "--latest"],
                     ["claude", "status", "x"], ["claude", "usage", "--bogus"], ["claude", "usage", "a", "b"],
                     ["claude", "--help"], ["--help"]):
            with self.subTest(argv=argv):
                code, stdout, stderr = self.run_main(argv)
                self.assertEqual(2, code)
                self.assertEqual("", stdout)
                self.assertTrue(stderr.startswith("session-log: "), stderr)
                self.assertNotIn("usage:", stderr.lower())
                self.assertNotIn("Traceback", stderr)
                self.assertEqual([], self.home_entries())

        code, stdout, stderr = self.run_main(["claude", "usage"])
        self.assertEqual(1, code)
        self.assertEqual("", stdout)
        self.assertEqual("session-log: no archived session for this project (%s)\n" % CWD, stderr)

    def test_prompt_logs_that_is_a_file_exits_1_with_one_session_log_line(self):
        os.makedirs(self.claude)
        Path(self.log_root).write_text("a file where the log folder should be", encoding="utf-8")
        code, stdout, stderr = self.run_main(["claude", "on"])
        self.assertEqual(1, code)
        self.assertEqual("", stdout)
        self.assertTrue(stderr.startswith("session-log: "), stderr)
        self.assertNotIn("internal error", stderr)
        self.assertEqual(1, len(stderr.splitlines()))

    def test_latest_flag_and_no_argument_are_the_same_target(self):
        self.write_transcript()
        self.turn_logging_on()
        plain = self.run_main(["claude", "usage"])
        latest = self.run_main(["claude", "usage", "--latest"])
        self.assertEqual(0, plain[0])
        self.assertEqual(plain, latest)

    def test_empty_target_exits_2(self):
        code, stdout, stderr = self.run_main(["claude", "usage", ""])
        self.assertEqual((2, ""), (code, stdout))
        self.assertTrue(stderr.startswith("session-log: "), stderr)

    def test_single_dash_id_is_passed_to_the_use_case_not_rejected_by_the_parser(self):
        code, _, stderr = self.run_main(["claude", "usage", "-abc"])
        self.assertEqual(1, code)
        self.assertTrue(stderr.startswith("session-log: "), stderr)
        self.assertNotIn("unknown option", stderr)

    def test_double_dash_option_other_than_latest_is_unknown(self):
        code, _, stderr = self.run_main(["claude", "usage", "--abc"])
        self.assertEqual(2, code)
        self.assertEqual("session-log: unknown option '--abc' (expected --latest or a session id)\n", stderr)


class RuntimeErrorTests(SandboxCase):
    def test_os_error_is_a_runtime_error_not_an_internal_error(self):
        self.write_transcript()
        self.turn_logging_on()
        with mock.patch("sessionlog.sync.SyncUseCase.sync", side_effect=OSError("disk gone")):
            code, stdout, stderr = self.run_main(["claude", "status"])
        self.assertEqual((1, ""), (code, stdout))
        self.assertEqual("session-log: disk gone\n", stderr)
        self.assertNotIn("internal error", stderr)

    def test_relative_home_is_a_runtime_error(self):
        code, stdout, stderr = self.run_main(["claude", "status"], home="relative/home")
        self.assertEqual((1, ""), (code, stdout))
        self.assertEqual("session-log: cannot determine the home folder\n", stderr)

    def test_filesystem_root_home_is_a_runtime_error(self):
        code, stdout, stderr = self.run_main(["claude", "status"], home="/")
        self.assertEqual((1, ""), (code, stdout))
        self.assertEqual("session-log: cannot determine the home folder\n", stderr)
        self.assertEqual([], self.home_entries())

    def test_keyboard_interrupt_is_reported_as_interrupted(self):
        with mock.patch("sessionlog.commands.CommandUseCases.status", side_effect=KeyboardInterrupt):
            code, stdout, stderr = self.run_main(["claude", "status"])
        self.assertEqual((1, ""), (code, stdout))
        self.assertEqual("session-log: interrupted\n", stderr)

    def test_exception_without_message_is_reported_by_type_name_only(self):
        with mock.patch("sessionlog.commands.CommandUseCases.status", side_effect=ValueError()):
            code, _, stderr = self.run_main(["claude", "status"])
        self.assertEqual(1, code)
        self.assertEqual("session-log: internal error: ValueError\n", stderr)

    def test_failing_get_cwd_does_not_affect_on(self):
        def failing_cwd():
            raise OSError("cwd was deleted")

        code, stdout, stderr = self.run_main(["claude", "on"], get_cwd=failing_cwd)
        self.assertEqual((0, ""), (code, stderr))
        self.assertIn("Session logging is on since ", stdout)

    def test_failing_get_cwd_fails_usage_with_a_session_log_line(self):
        def failing_cwd():
            raise OSError("cwd was deleted")

        code, stdout, stderr = self.run_main(["claude", "usage"], get_cwd=failing_cwd)
        self.assertEqual((1, ""), (code, stdout))
        self.assertEqual("session-log: cwd was deleted\n", stderr)

    def test_broken_stdout_pipe_raises_nothing_and_fails_a_successful_run(self):
        class BrokenPipe(io.StringIO):
            def write(self, text):
                raise BrokenPipeError("closed")

        stderr = io.StringIO()
        code = session_log.main(["claude", "status"], self.environment(BrokenPipe(), stderr))
        self.assertEqual(1, code)
        self.assertEqual("", stderr.getvalue())

    def test_stream_whose_write_raises_attribute_error_raises_nothing(self):
        class NoStream(io.StringIO):
            def write(self, text):
                raise AttributeError("gone")

        stderr = io.StringIO()
        code = session_log.main(["claude", "status"], self.environment(NoStream(), stderr))
        self.assertEqual(1, code)
        self.assertEqual("", stderr.getvalue())

    def test_broken_stderr_pipe_keeps_the_exit_code(self):
        class BrokenPipe(io.StringIO):
            def write(self, text):
                raise BrokenPipeError("closed")

        code = session_log.main(["claude", "bogus"], self.environment(io.StringIO(), BrokenPipe()))
        self.assertEqual(2, code)


class CommandTests(SandboxCase):
    def test_on_prints_the_log_folder_and_creates_the_on_flag(self):
        code, stdout, stderr = self.run_main(["claude", "on"])
        self.assertEqual((0, ""), (code, stderr))
        self.assertIn("Session logging is on since ", stdout)
        self.assertIn("Logs are saved under %s." % self.log_root, stdout)
        self.assertTrue(os.path.isfile(os.path.join(self.log_root, ON_FLAG)))

    def test_on_uses_the_injected_clock_and_local_time(self):
        _, stdout, _ = self.run_main(["claude", "on"])
        self.assertIn("Session logging is on since 2000-01-01 00:00:00 +0000.", stdout)

    def test_status_counts_the_archived_session(self):
        self.write_transcript()
        self.turn_logging_on()
        code, stdout, stderr = self.run_main(["claude", "status"])
        self.assertEqual((0, ""), (code, stderr))
        self.assertIn("Archived sessions: 1", stdout)

    def test_usage_prints_the_report_of_the_latest_session(self):
        self.write_transcript()
        self.turn_logging_on()
        code, stdout, stderr = self.run_main(["claude", "usage"])
        self.assertEqual((0, ""), (code, stderr))
        self.assertIn(UNICODE_PROMPT, stdout)

    def test_off_prints_the_off_text_and_removes_the_on_flag(self):
        self.turn_logging_on()
        code, stdout, stderr = self.run_main(["claude", "off"])
        self.assertEqual((0, ""), (code, stderr))
        self.assertIn("Session logging is off.", stdout)
        self.assertFalse(os.path.exists(os.path.join(self.log_root, ON_FLAG)))

    def test_usage_shows_a_price_from_the_skill_price_file(self):
        self.write_transcript(model=PRICED_MODEL)
        self.turn_logging_on()
        code, stdout, _ = self.run_main(["claude", "usage"])
        self.assertEqual(0, code)
        self.assertIn("$", stdout)
        self.assertNotIn("price: unknown", stdout)

    def test_usage_of_a_model_missing_from_the_price_file_is_unknown(self):
        self.write_transcript(model=UNPRICED_MODEL)
        self.turn_logging_on()
        _, stdout, _ = self.run_main(["claude", "usage"])
        self.assertIn("price: unknown", stdout)

    def test_status_reports_an_unreadable_price_file(self):
        missing = os.path.join(self.home, "no-such-folder", "prices.json")
        with mock.patch.object(session_log, "PRICE_FILE_PATH", missing):
            code, stdout, _ = self.run_main(["claude", "status"])
        self.assertEqual(0, code)
        self.assertIn("Price file problem", stdout)

    def test_injected_path_module_is_used_to_build_paths(self):
        class RecordingPath(object):
            def __init__(self):
                self.joins = []

            def join(self, *parts):
                self.joins.append(parts)
                return os.path.join(*parts)

            def __getattr__(self, name):
                return getattr(os.path, name)

        recording = RecordingPath()
        code, _, stderr = self.run_main(["claude", "status"], path_module=recording)
        self.assertEqual((0, ""), (code, stderr))
        self.assertTrue(recording.joins)

    def test_streams_are_reconfigured_to_utf8_with_lf(self):
        class Stream(io.StringIO):
            def reconfigure(self, **options):
                self.options = options

        stdout, stderr = Stream(), Stream()
        session_log.main(["claude", "status"], self.environment(stdout, stderr))
        for stream in (stdout, stderr):
            self.assertEqual("utf-8", stream.options["encoding"])
            self.assertEqual("\n", stream.options["newline"])


class VersionGateTests(SandboxCase):
    def test_injected_old_python_prints_requirement_and_fix_line_exit_1_and_exception_gives_internal_error_not_traceback(self):
        code, stdout, stderr = self.run_main(["claude", "on"], version_info=OLD_PYTHON, platform="linux")
        self.assertEqual(1, code)
        self.assertEqual("", stdout)
        lines = stderr.splitlines()
        self.assertEqual(2, len(lines), stderr)
        self.assertEqual(REQUIREMENT_LINE, lines[0])
        self.assertTrue(lines[1].startswith(FIX_PREFIX), lines[1])
        self.assertEqual([], self.home_entries())

        with mock.patch("sessionlog.sync.SyncUseCase.sync", side_effect=RuntimeError("boom")):
            self.write_transcript()
            self.turn_logging_on_long_ago()
            code, stdout, stderr = self.run_main(["claude", "status"])
        self.assertEqual(1, code)
        self.assertEqual("", stdout)
        self.assertEqual("session-log: internal error: RuntimeError: boom\n", stderr)

    def test_fix_line_names_brew_on_darwin_install_manager_on_win32_and_package_manager_elsewhere(self):
        for platform, expected in FIX_HINTS.items():
            with self.subTest(platform=platform):
                code, _, stderr = self.run_main(["claude", "status"], version_info=OLD_PYTHON, platform=platform)
                self.assertEqual(1, code)
                fix = stderr.splitlines()[1]
                self.assertTrue(fix.startswith(FIX_PREFIX), fix)
                for fragment in expected:
                    self.assertIn(fragment, fix)
        self.assertNotIn("brew", self.run_main(["claude", "status"], version_info=OLD_PYTHON, platform="win32")[2])
        self.assertNotIn("brew", self.run_main(["claude", "status"], version_info=OLD_PYTHON, platform="freebsd14")[2])

    def test_each_platform_gets_exactly_a_requirement_line_and_a_fix_line(self):
        for platform in ("darwin", "win32", "linux"):
            with self.subTest(platform=platform):
                _, _, stderr = self.run_main(["claude", "status"], version_info=OLD_PYTHON, platform=platform)
                self.assertEqual(2, len(stderr.splitlines()), stderr)

    def test_darwin_and_win32_fix_hints_are_exact(self):
        _, _, darwin = self.run_main(["claude", "on"], version_info=OLD_PYTHON, platform="darwin")
        _, _, win32 = self.run_main(["claude", "on"], version_info=OLD_PYTHON, platform="win32")
        self.assertEqual(FIX_PREFIX + DARWIN_FIX, darwin.splitlines()[1])
        self.assertEqual(FIX_PREFIX + WIN32_FIX, win32.splitlines()[1])
        self.assertIn("then make python3 resolve to it", darwin)
        self.assertNotIn("python3 resolve", win32)

    def test_old_python_hook_is_silent_with_exit_0(self):
        code, stdout, stderr = self.run_main(["hook", "claude", "SessionStart"], version_info=OLD_PYTHON, platform="linux")
        self.assertEqual((0, "", ""), (code, stdout, stderr))

    def test_old_python_with_empty_argv_or_only_a_command_still_gets_the_version_message(self):
        for argv in ([], ["on"]):
            with self.subTest(argv=argv):
                code, _, stderr = self.run_main(argv, version_info=OLD_PYTHON, platform="linux")
                self.assertEqual(1, code)
                self.assertEqual(REQUIREMENT_LINE, stderr.splitlines()[0])

    def test_minimum_version_is_accepted(self):
        code, stdout, stderr = self.run_main(["claude", "status"], version_info=(3, 14, 0))
        self.assertEqual((0, ""), (code, stderr))
        self.assertIn("Session logging is off", stdout)

    def test_found_version_shows_major_and_minor_only(self):
        _, _, stderr = self.run_main(["claude", "on"], version_info=(3, 13, 7), platform="linux")
        self.assertEqual("session-log: Python 3.14+ required (found 3.13)", stderr.splitlines()[0])


class EntryFileSyntaxTests(unittest.TestCase):
    """Approximation only: the real proof that the file parses on 3.4 is the python:3.4-slim e2e (T-4)."""

    def setUp(self):
        self.source = ENTRY.read_text(encoding="utf-8")

    def test_entry_file_uses_only_syntax_python_3_4_parses(self):
        tree = ast.parse(self.source)
        not_parseable = NOT_PARSEABLE_BY_PYTHON_3_4 + tuple(
            node for node in (getattr(ast, "TemplateStr", None),) if node is not None)
        offenders = [type(node).__name__ + ":%d" % node.lineno for node in ast.walk(tree) if isinstance(node, not_parseable)]
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                starred = [arg for arg in node.args if isinstance(arg, ast.Starred)]
                keyword_unpacks = [keyword for keyword in node.keywords if keyword.arg is None]
                keyword_after_unpack = any(keyword.arg is not None and node.keywords.index(keyword) > node.keywords.index(unpack)
                                           for unpack in keyword_unpacks for keyword in node.keywords)
                if (len(starred) > 1 or len(keyword_unpacks) > 1 or keyword_after_unpack
                        or (starred and node.args.index(starred[0]) != len(node.args) - 1)):
                    offenders.append("call unpacking:%d" % node.lineno)
            if isinstance(node, (ast.Dict, ast.Set, ast.List, ast.Tuple)) and isinstance(getattr(node, "ctx", ast.Load()), ast.Load) and any(
                    isinstance(item, ast.Starred) for item in getattr(node, "elts", ())):
                offenders.append("unpacking literal:%d" % node.lineno)
            if isinstance(node, ast.Dict) and None in node.keys:
                offenders.append("dict unpacking:%d" % node.lineno)
            if isinstance(node, ast.arguments) and node.posonlyargs:
                offenders.append("positional-only:%d" % node.posonlyargs[0].lineno)
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.type_params:
                offenders.append("type params:%d" % node.lineno)
        self.assertEqual([], offenders)

    def test_entry_file_has_no_unparenthesized_multiple_exception_types(self):
        self.assertIsNone(UNPARENTHESIZED_EXCEPT.search(self.source))

    def test_entry_file_has_no_numeric_underscores(self):
        with open(ENTRY, "rb") as stream:
            numbers = [token.string for token in tokenize.tokenize(stream.readline) if token.type == tokenize.NUMBER]
        self.assertEqual([], [number for number in numbers if "_" in number])

    def test_entry_file_has_no_trailing_comma_after_unpacked_argument(self):
        self.assertIsNone(TRAILING_COMMA_AFTER_UNPACKING.search(self.source))

    def test_no_module_level_import_outside_the_python_3_4_stdlib_set(self):
        self.assertEqual([], [root for root in self.import_roots(ast.parse(self.source), in_function=False)
                              if root not in MODULE_LEVEL_IMPORTS])

    def test_function_level_imports_are_limited_to_the_package_and_pathlib(self):
        self.assertEqual([], [root for root in self.import_roots(ast.parse(self.source), in_function=True)
                              if root not in MODULE_LEVEL_IMPORTS | FUNCTION_LEVEL_IMPORTS])

    def import_roots(self, node, in_function):
        """Root modules of the imports nested inside a function (in_function) or outside every function."""
        roots = []
        for child in ast.iter_child_nodes(node):
            is_function = isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            if isinstance(child, ast.Import) and not in_function:
                roots += [alias.name.split(".")[0] for alias in child.names]
            elif isinstance(child, ast.ImportFrom) and not in_function:
                roots.append((child.module or "").split(".")[0])
            elif is_function and in_function:
                roots += self.import_roots(child, False)
            elif not is_function:
                roots += self.import_roots(child, in_function)
        return roots


class SubprocessTests(SandboxCase):
    def run_python_code(self, code):
        result = subprocess.run([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(0, result.returncode, result.stderr)
        return result.stdout.decode("utf-8").strip()

    def import_state_after(self, call):
        return self.run_python_code(
            "import io, sys\nsys.path.insert(0, %r)\nimport session_log\n%s\nprint('sessionlog' in sys.modules)" % (
                str(SKILL_DIR), call))

    def test_old_python_never_imports_the_package(self):
        call = ("session_log.main(['claude', 'on'], session_log.Environment("
                "version_info=(3, 4, 0), stderr=io.StringIO(), stdout=io.StringIO(), home=%r))" % self.home)
        self.assertEqual("False", self.import_state_after(call))

    def test_bad_arguments_never_import_the_package(self):
        call = ("session_log.main(['bogus'], session_log.Environment("
                "stderr=io.StringIO(), stdout=io.StringIO(), home=%r))" % self.home)
        self.assertEqual("False", self.import_state_after(call))


class Utf8SubprocessTests(SandboxCase):
    def cp1252_env(self, legacy_default_encoding=False):
        """`legacy_default_encoding` switches UTF-8 mode off, so a file opened without `encoding=` is ASCII under `LC_ALL=C`
        (needs ASCII paths: the file system encoding turns ASCII too)."""
        env = {name: os.environ[name] for name in SUBPROCESS_ENV_NAMES if name in os.environ}
        env.update(HOME=self.home, USERPROFILE=self.home, PYTHONIOENCODING="cp1252", LC_ALL="C", LANG="C")
        if legacy_default_encoding:
            env["PYTHONUTF8"] = "0"
        return env

    def run_entry(self, argv, cwd, legacy_default_encoding=False):
        return subprocess.run([sys.executable, str(ENTRY)] + argv, env=self.cp1252_env(legacy_default_encoding), cwd=cwd,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)

    def test_cp1252_subprocess_stdout_is_utf8_and_files_are_utf8_lf(self):
        self.write_transcript(cwd=self.home)
        self.turn_logging_on_long_ago()
        result = self.run_entry(["claude", "usage"], self.home, legacy_default_encoding=True)
        self.assertEqual((0, b""), (result.returncode, result.stderr))
        shown = result.stdout.decode("utf-8")
        self.assertIn("\u2265", shown)
        self.assertIn("\u0151", shown)
        self.assertNotIn(b"\r", result.stdout)

        written = [path for path in Path(self.log_root).rglob("*") if path.is_file() and path.name != SYNC_LOCK]
        self.assertTrue(written)
        texts = []
        for path in written:
            raw = path.read_bytes()
            self.assertNotIn(b"\r", raw, path)
            texts.append(raw.decode("utf-8"))
        self.assertTrue(any("\u0151" in text for text in texts))
        reports = [path for path in written if path.name.endswith(".usage.txt")]
        self.assertEqual(1, len(reports))
        saved_report = reports[0].read_bytes().decode("utf-8")
        self.assertIn("\u2265", saved_report)
        self.assertIn("\u0151", saved_report)
        self.assertEqual(saved_report, shown)

    def test_cp1252_subprocess_stderr_is_utf8(self):
        folder = os.path.join(self.home, "pr\u0151j")
        os.makedirs(folder)
        result = self.run_entry(["claude", "usage"], folder)
        self.assertEqual(1, result.returncode)
        self.assertIn("\u0151", result.stderr.decode("utf-8"))

    def test_entry_script_runs_from_an_unrelated_cwd_with_isolated_path(self):
        other = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, other, True)
        result = subprocess.run([sys.executable, "-P", str(ENTRY), "claude", "status"], env=self.cp1252_env(), cwd=other,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual((0, b""), (result.returncode, result.stderr))
        self.assertIn("Session logging is off.", result.stdout.decode("utf-8"))

    def test_real_closed_stdout_pipe_exits_1_with_silent_stderr(self):
        code = ("import sys\nsys.path.insert(0, %r)\nimport session_log\nsys.stdin.readline()\n"
                "sys.exit(session_log.main(['claude', 'status']))" % str(SKILL_DIR))
        proc = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=self.cp1252_env())
        self.addCleanup(proc.wait)
        proc.stdout.close()
        proc.stdin.write(b"\n")
        proc.stdin.close()
        _, stderr = proc.communicate(timeout=60)
        self.assertEqual(1, proc.returncode)
        self.assertEqual(b"", stderr)


if __name__ == "__main__":
    unittest.main()
