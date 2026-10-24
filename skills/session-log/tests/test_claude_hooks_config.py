"""ClaudeHooksConfig (BE-18): hooks/hooks.json registers one exec-form SessionStart hook whose `-c` guard runs the
script only when it exists, and never fails or prints itself."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import session_log

REPO = Path(__file__).resolve().parents[3]
HOOKS_JSON = REPO / "hooks" / "hooks.json"
PLUGIN_ROOT_VARIABLE = "${CLAUDE_PLUGIN_ROOT}"
SCRIPT_RELATIVE = "skills/session-log/session_log.py"
SCRIPT_PATH = PLUGIN_ROOT_VARIABLE + "/" + SCRIPT_RELATIVE
HOOK_ARGUMENTS = ["hook", "claude", "SessionStart"]
HOOK_TIMEOUT_SECONDS = 30
GUARD_INDEX = 2
PLUGIN_FOLDER_WITH_SPACE_AND_APOSTROPHE = "plug in's root"
SUBPROCESS_TIMEOUT_SECONDS = 60
SCRIPT_EXIT_CODE = 2
SHADOW_EXIT_CODE = 3
STDIN_PAYLOAD = b'{"session_id": "abc"}'
ENVIRONMENT_NAMES = ("PATH", "HOME", "USERPROFILE", "SYSTEMROOT")


def load_hooks():
    return json.loads(HOOKS_JSON.read_text(encoding="utf-8"))["hooks"]


def session_start_hook():
    return load_hooks()["SessionStart"][0]["hooks"][0]


def run_guard(plugin_root, cwd=None, stdin=None):
    """Runs the shipped args array with `${CLAUDE_PLUGIN_ROOT}` replaced by `plugin_root`, no shell, a controlled environment."""
    args = [argument.replace(PLUGIN_ROOT_VARIABLE, str(plugin_root)) for argument in session_start_hook()["args"]]
    environment = {name: os.environ[name] for name in ENVIRONMENT_NAMES if name in os.environ}
    return subprocess.run([sys.executable] + args, input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL,
                          capture_output=True, timeout=SUBPROCESS_TIMEOUT_SECONDS, env=environment, cwd=cwd)


class ClaudeHooksConfigTests(unittest.TestCase):
    def test_claude_hooks_json_has_one_sessionstart_exec_form_guard_before_script_no_matcher_async_timeout_30(self):
        hooks = load_hooks()
        self.assertEqual(["SessionStart"], list(hooks))
        [group] = hooks["SessionStart"]
        self.assertEqual({"hooks"}, set(group))
        [hook] = group["hooks"]
        self.assertEqual({"type", "command", "args", "async", "timeout"}, set(hook))
        self.assertEqual("command", hook["type"])
        self.assertEqual("python3", hook["command"])
        self.assertIs(True, hook["async"])
        self.assertEqual(HOOK_TIMEOUT_SECONDS, hook["timeout"])
        args = hook["args"]
        self.assertEqual(["-I", "-c"], args[:GUARD_INDEX])
        self.assertNotIn("\n", args[GUARD_INDEX])
        self.assertEqual([SCRIPT_PATH] + HOOK_ARGUMENTS, args[GUARD_INDEX + 1:])

    def test_shipped_script_path_names_the_entry_script_in_this_repo(self):
        self.assertTrue((REPO / SCRIPT_RELATIVE).is_file())

    def test_shipped_hook_arguments_name_a_hook_the_entry_script_handles(self):
        self.assertIn(tuple(HOOK_ARGUMENTS[1:]), session_log.HOOK_HANDLERS)

    def test_guard_exits_0_silently_when_script_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            plugin_root = Path(folder, PLUGIN_FOLDER_WITH_SPACE_AND_APOSTROPHE)
            plugin_root.mkdir()
            self.assertFalse((plugin_root / SCRIPT_RELATIVE).exists())
            result = run_guard(plugin_root)
        self.assertEqual((0, b"", b""), (result.returncode, result.stdout, result.stderr))

    def test_guard_runs_the_script_with_the_hook_arguments_and_stdin_and_exits_0_whatever_the_script_exit_code(self):
        with tempfile.TemporaryDirectory() as folder:
            plugin_root = Path(folder, PLUGIN_FOLDER_WITH_SPACE_AND_APOSTROPHE)
            script = plugin_root / SCRIPT_RELATIVE
            script.parent.mkdir(parents=True)
            script.write_text("import sys\nsys.stdout.buffer.write(repr(sys.argv[1:]).encode() + sys.stdin.buffer.read())\n"
                              "sys.exit(%d)\n" % SCRIPT_EXIT_CODE, encoding="utf-8")
            result = run_guard(plugin_root, stdin=STDIN_PAYLOAD)
        self.assertEqual((0, repr(HOOK_ARGUMENTS).encode() + STDIN_PAYLOAD, b""), (result.returncode, result.stdout, result.stderr))

    def test_guard_ignores_modules_in_the_working_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "subprocess.py").write_text("raise SystemExit(%d)\n" % SHADOW_EXIT_CODE, encoding="utf-8")
            plugin_root = Path(folder, "root")
            script = plugin_root / SCRIPT_RELATIVE
            script.parent.mkdir(parents=True)
            script.write_text("print('ran')\n", encoding="utf-8")
            result = run_guard(plugin_root, cwd=folder)
        self.assertEqual((0, ("ran" + os.linesep).encode(), b""), (result.returncode, result.stdout, result.stderr))


if __name__ == "__main__":
    unittest.main()
