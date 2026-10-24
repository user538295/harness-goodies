"""CursorHooksConfig (BE-33): .cursor-plugin/hooks.json registers the five Cursor events, each through the `hook` launcher with the
braced plugin-root variable and nothing after the event name; `hook` is committed executable."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

import session_log

REPO = Path(__file__).resolve().parents[3]
HOOKS_JSON = REPO / ".cursor-plugin" / "hooks.json"
LAUNCHER_RELATIVE = "skills/session-log/hook"
PLUGIN_ROOT_VARIABLE = "${CURSOR_PLUGIN_ROOT}"
EVENTS = ("sessionStart", "beforeSubmitPrompt", "afterAgentResponse", "subagentStop", "stop")
CONFIG_VERSION = 1
HOOK_TIMEOUT_SECONDS = 30
GIT_EXECUTABLE_MODE = "100755"
GIT_TIMEOUT_SECONDS = 60


def command_for(event):
    return '"%s/%s" cursor %s' % (PLUGIN_ROOT_VARIABLE, LAUNCHER_RELATIVE, event)


class CursorHooksConfigTests(unittest.TestCase):
    def test_cursor_hooks_json_five_events_exact_braced_command_nothing_after_it_timeout_30(self):
        config = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        self.assertEqual({"version", "hooks"}, set(config))
        self.assertEqual(CONFIG_VERSION, config["version"])
        self.assertEqual(set(EVENTS), set(config["hooks"]))
        for event in EVENTS:
            with self.subTest(event=event):
                [entry] = config["hooks"][event]
                self.assertEqual({"command", "timeout"}, set(entry))
                self.assertEqual(command_for(event), entry["command"])
                self.assertEqual(HOOK_TIMEOUT_SECONDS, entry["timeout"])

    def test_every_registered_event_is_one_the_entry_script_handles(self):
        for event in EVENTS:
            with self.subTest(event=event):
                self.assertIn(("cursor", event), session_log.HOOK_HANDLERS)

    @unittest.skipIf(shutil.which("git") is None, "git is not on PATH")
    def test_hook_file_is_git_mode_100755(self):
        listing = subprocess.run(["git", "ls-files", "-s", "--", LAUNCHER_RELATIVE], cwd=REPO, capture_output=True, text=True,
                                 timeout=GIT_TIMEOUT_SECONDS)
        if listing.returncode != 0:
            self.skipTest("the checkout is not a usable git work tree: " + listing.stderr.strip())
        self.assertTrue(listing.stdout.startswith(GIT_EXECUTABLE_MODE + " "), listing.stdout)


if __name__ == "__main__":
    unittest.main()
