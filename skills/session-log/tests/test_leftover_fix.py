"""Leftover fix text (C1): the one-line fix of a leftover, for the shell the user is in, and the safe order of a hook entry and what it runs.

The fixes are text only: nothing here runs a command, and nothing is deleted.
"""
import unittest

from sessionlog.leftover_fix import HookFile, move_command, move_fix, ordered_fix, remove_entries_fix
from sessionlog.model import Harness

POSIX_FOLDER = "/home/u/.codex/session-log-old/skills"
POSIX_SOURCE = "/home/u/.codex/skills/session-log"
SPACED_FOLDER = "/home/my user/o'brien/session-log-old/skills"
SPACED_SOURCE = "/home/my user/o'brien/skills/session-log"
WINDOWS_FOLDER = r"C:\Users\o'brien\.codex\session-log-old\skills"
WINDOWS_SOURCE = r"C:\Users\o'brien\.codex\skills\session-log"
COMMAND = "bash /x/old_hook.sh stop"
OTHER_COMMAND = 'python3 "/x/other hook.py" stop'
MOVE = (POSIX_FOLDER, (POSIX_SOURCE,))


class MoveCommandTests(unittest.TestCase):
    def test_posix_platforms_make_the_folder_then_move_into_it(self):
        for platform in ("linux", "darwin", "freebsd14"):
            self.assertEqual("mkdir -p %s && mv %s %s" % (POSIX_FOLDER, POSIX_SOURCE, POSIX_FOLDER),
                             move_command(platform, [MOVE]), platform)

    def test_posix_quotes_spaces_and_apostrophes_as_one_word_each(self):
        self.assertEqual("mkdir -p '/home/my user/o'\"'\"'brien/session-log-old/skills' && "
                         "mv '/home/my user/o'\"'\"'brien/skills/session-log' '/home/my user/o'\"'\"'brien/session-log-old/skills'",
                         move_command("linux", [(SPACED_FOLDER, (SPACED_SOURCE,))]))

    def test_windows_uses_powershell_with_literal_single_quoted_paths(self):
        self.assertEqual("New-Item -ItemType Directory -Force -ErrorAction Stop -Path 'C:\\Users\\o''brien\\.codex\\session-log-old\\skills' "
                         "| Out-Null; Move-Item -LiteralPath 'C:\\Users\\o''brien\\.codex\\skills\\session-log' "
                         "-Destination 'C:\\Users\\o''brien\\.codex\\session-log-old\\skills' -ErrorAction Stop",
                         move_command("win32", [(WINDOWS_FOLDER, (WINDOWS_SOURCE,))]))

    def test_windows_doubles_every_character_powershell_reads_as_a_single_quote(self):
        command = move_command("win32", [("C:\\a\u2018b\u2019c\u201ad\u201be", ("C:\\src",))])
        self.assertIn("'C:\\a\u2018\u2018b\u2019\u2019c\u201a\u201ad\u201b\u201be'", command)

    def test_windows_does_not_expand_dollar_or_backtick_and_has_no_double_ampersand(self):
        command = move_command("win32", [("C:\\$HOME\\`x", ("C:\\src",))])
        self.assertIn("'C:\\$HOME\\`x'", command)  # a single-quoted PowerShell string expands nothing
        self.assertNotIn("&&", command)  # Windows PowerShell 5.1 has no `&&`

    def test_several_sources_in_one_folder_are_one_move(self):
        self.assertEqual("mkdir -p /d && mv /a /b /d", move_command("linux", [("/d", ("/a", "/b"))]))
        self.assertIn("-LiteralPath '/a','/b'", move_command("win32", [("/d", ("/a", "/b"))]))

    def test_several_folders_are_joined_so_the_next_runs_only_after_the_previous_succeeded_on_posix(self):
        self.assertEqual("mkdir -p /d1 && mv /a /d1 && mkdir -p /d2 && mv /b /d2",
                         move_command("linux", [("/d1", ("/a",)), ("/d2", ("/b",))]))
        windows = move_command("win32", [("/d1", ("/a",)), ("/d2", ("/b",))])
        self.assertEqual(2, windows.count("Move-Item"))
        self.assertTrue(windows.index("'/d1'") < windows.index("'/d2'"))


class FixTextTests(unittest.TestCase):
    def test_a_plain_move_fix_is_one_line_naming_the_command_and_why_outside_the_skills_folders(self):
        fix = move_fix("linux", [MOVE])
        self.assertEqual("move it outside the skills folders: `%s` (a copy moved inside a skills folder may still load)"
                         % move_command("linux", [MOVE]), fix)
        self.assertNotIn("\n", fix)

    def test_a_plain_hook_fix_names_each_exact_command_json_quoted_and_keeps_the_others(self):
        fix = remove_entries_fix((COMMAND, OTHER_COMMAND))
        self.assertEqual('delete from its "hooks" every entry whose "command" is "bash /x/old_hook.sh stop" or '
                         '"python3 \\"/x/other hook.py\\" stop" (keep the other entries)', fix)

    def test_the_ordered_fix_starts_with_first_deletes_the_entries_and_only_then_moves(self):
        fix = ordered_fix("linux", [HookFile("/h/.cursor/hooks.json", Harness.CURSOR, (COMMAND,))], [MOVE])
        self.assertEqual('First delete from "hooks" in /h/.cursor/hooks.json every entry whose "command" is "bash /x/old_hook.sh stop" '
                         '(keep the other entries), then move what they run: `%s`. Not the other way round: a hook whose script is '
                         'gone makes Cursor block every prompt.' % move_command("linux", [MOVE]), fix)
        self.assertNotIn("\n", fix)

    def test_each_harness_names_what_goes_wrong_when_the_order_is_reversed(self):
        expected = {Harness.CURSOR: "makes Cursor block every prompt", Harness.CLAUDE: "makes Claude Code report a hook error on every prompt",
                    Harness.CODEX: "blocks every Codex prompt"}
        for harness, consequence in expected.items():
            self.assertIn(consequence, ordered_fix("linux", [HookFile("/h/f.json", harness, (COMMAND,))], [MOVE]), harness.value)

    def test_only_codex_is_told_to_restart_before_the_move(self):
        for harness in (Harness.CLAUDE, Harness.CURSOR):
            self.assertNotIn("restart", ordered_fix("linux", [HookFile("/h/f.json", harness, (COMMAND,))], [MOVE]))
        fix = ordered_fix("linux", [HookFile("/h/.codex/hooks.json", Harness.CODEX, (COMMAND,))], [MOVE])
        self.assertIn("(keep the other entries), then restart Codex (sessions started earlier keep running the old hook), "
                      "then move what they run: `", fix)

    def test_two_hook_files_are_two_deletions_before_the_one_move(self):
        fix = ordered_fix("linux", [HookFile("/h/.codex/hooks.json", Harness.CODEX, (COMMAND,)),
                                    HookFile("/h/.cursor/hooks.json", Harness.CURSOR, (OTHER_COMMAND,))], [MOVE])
        self.assertTrue(fix.startswith('First delete from "hooks" in /h/.codex/hooks.json every entry whose "command" is '))
        self.assertLess(fix.index("/h/.codex/hooks.json"), fix.index("/h/.cursor/hooks.json"))
        self.assertLess(fix.index("/h/.cursor/hooks.json"), fix.index("then restart Codex"))
        self.assertLess(fix.index("then restart Codex"), fix.index("move what they run"))
        self.assertEqual(1, fix.count("move what they run"))

    def test_the_ordered_fix_for_windows_carries_the_powershell_move(self):
        fix = ordered_fix("win32", [HookFile(r"C:\u\.cursor\hooks.json", Harness.CURSOR, (COMMAND,))], [(WINDOWS_FOLDER, (WINDOWS_SOURCE,))])
        self.assertIn("Move-Item -LiteralPath", fix)
        self.assertNotIn("mkdir", fix)


if __name__ == "__main__":
    unittest.main()
