"""BE-35 (S62, S64): the root checks every command and hook runs right after the Python version check and before the `.enabled`
migration, in the order of C1: a relocated root, then a symlinked harness root or `prompt-logs`. A command that fails them exits 1
with one `session-log:` line; a hook exits 0, prints nothing and writes nothing, not even the `.enabled` conversion. These run
`session_log.main` over a sandbox home; the real environment is never read."""
import io
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone

import session_log

FIXED_NOW = datetime(2000, 1, 1, tzinfo=timezone.utc)
LOG_FOLDER = "prompt-logs"
OLD_FLAG = ".enabled"
NEW_FLAG = ".log_enabled"
CWD = "/work/proj"
SILENT_SUCCESS = (0, "", "")
HOME_MARK = "~"
# (harness, relocating variable): each must make `<harness> status` fail when it names a folder that is not the harness's default.
RELOCATIONS = (
    ("claude", "CLAUDE_CONFIG_DIR"),
    ("codex", "CODEX_HOME"),
    ("opencode", "OPENCODE_CONFIG_DIR"),
    ("opencode", "XDG_CONFIG_HOME"),
    ("opencode", "XDG_DATA_HOME"),
    ("omp", "PI_CODING_AGENT_DIR"),
    ("omp", "OMP_PROMPT_LOG_DIR"),
)
# What each variable's default root is below home, in the words a user types (`~` spelled out).
DEFAULT_ROOTS = {
    "CLAUDE_CONFIG_DIR": ".claude",
    "CODEX_HOME": ".codex",
    "OPENCODE_CONFIG_DIR": ".config/opencode",
    "XDG_CONFIG_HOME": ".config",
    "XDG_DATA_HOME": ".local/share",
    "PI_CODING_AGENT_DIR": ".omp/agent",
    "OMP_PROMPT_LOG_DIR": ".omp/agent/prompt-logs",
}
HOOKS = {"claude": [["hook", "claude", "SessionStart"]],
         "cursor": [["hook", "cursor", event] for event in ("sessionStart",) + session_log.CURSOR_RECORD_EVENTS]}
OLD_PYTHON = (3, 13, 0)


class RootCheckCase(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)

    def run_main(self, argv, env=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        environment = session_log.Environment(stdout=stdout, stderr=stderr, home=self.home, get_cwd=lambda: CWD, now=lambda: FIXED_NOW,
                                              to_local=lambda moment: moment.astimezone(timezone.utc), env={} if env is None else env)
        return session_log.main(argv, environment), stdout.getvalue(), stderr.getvalue()

    def root_of(self, harness):
        return os.path.join(self.home, *session_log.SUPPORTED_HARNESS_ROOTS[harness])

    def log_root_of(self, harness):
        return os.path.join(self.root_of(harness), LOG_FOLDER)

    def seed_old_flag(self, log_root):
        os.makedirs(log_root, exist_ok=True)
        with open(os.path.join(log_root, OLD_FLAG), "w", encoding="utf-8") as stream:
            stream.write("old release flag\n")

    def snapshot(self, folder):
        return sorted(os.path.relpath(os.path.join(base, name), folder) for base, folders, names in os.walk(folder)
                      for name in folders + names)


class RelocatedRootTest(RootCheckCase):
    def test_moved_root_command_exits_1_naming_variable_hook_exits_0_enabled_left_unconverted(self):
        for harness, variable in RELOCATIONS:
            with self.subTest(variable=variable):
                shutil.rmtree(self.home, ignore_errors=True)
                os.makedirs(self.home)
                log_root = self.log_root_of(harness)
                self.seed_old_flag(log_root)
                moved = os.path.join(self.home, "moved")
                os.makedirs(moved)
                before = self.snapshot(self.home)
                env = {variable: moved}
                for command in session_log.COMMANDS:
                    code, stdout, stderr = self.run_main([harness, command], env)
                    self.assertEqual((1, ""), (code, stdout), command)
                    self.assertTrue(stderr.startswith("session-log: ") and stderr.endswith("\n") and stderr.count("\n") == 1, stderr)
                    self.assertIn(variable, stderr)
                    self.assertIn(os.path.join(self.home, *DEFAULT_ROOTS[variable].split("/")), stderr)
                for hook in HOOKS.get(harness, ()):
                    self.assertEqual(SILENT_SUCCESS, self.run_main(hook, env))
                self.assertEqual([OLD_FLAG], os.listdir(log_root))
                self.assertEqual(before, self.snapshot(self.home))

    def test_cursor_has_no_relocating_variable(self):
        self.seed_old_flag(self.log_root_of("cursor"))
        env = {variable: os.path.join(self.home, "moved") for variable in DEFAULT_ROOTS}
        code, _, stderr = self.run_main(["cursor", "status"], env)
        self.assertEqual((0, ""), (code, stderr))
        self.assertEqual(SILENT_SUCCESS, self.run_main(HOOKS["cursor"][0], env))
        self.assertIn(NEW_FLAG, os.listdir(self.log_root_of("cursor")))

    def test_a_home_mark_before_another_folder_is_a_moved_root(self):
        for spelling in ("~/elsewhere", "~elsewhere", "~/.claude-other", "~"):
            with self.subTest(spelling):
                code, _, stderr = self.run_main(["claude", "status"], {"CLAUDE_CONFIG_DIR": spelling})
                self.assertEqual(1, code)
                self.assertIn("CLAUDE_CONFIG_DIR", stderr)

    def test_the_version_check_comes_before_the_relocated_root_check(self):
        env = {"CLAUDE_CONFIG_DIR": os.path.join(self.home, "moved")}
        stdout, stderr = io.StringIO(), io.StringIO()
        environment = session_log.Environment(stdout=stdout, stderr=stderr, home=self.home, version_info=OLD_PYTHON, env=env)
        self.assertEqual(1, session_log.main(["claude", "status"], environment))
        self.assertTrue(stderr.getvalue().startswith("session-log: Python 3.14+ required"), stderr.getvalue())
        self.assertNotIn("relocated", stderr.getvalue())

    def test_an_empty_variable_counts_as_unset(self):
        for harness, variable in RELOCATIONS:
            with self.subTest(variable=variable):
                code, _, stderr = self.run_main([harness, "status"], {variable: ""})
                self.assertEqual((0, ""), (code, stderr))


class SpelledOutDefaultRootTest(RootCheckCase):
    def test_spelled_out_default_roots_work_as_unset(self):
        for harness, variable in RELOCATIONS:
            default = os.path.join(self.home, *DEFAULT_ROOTS[variable].split("/"))
            spellings = (HOME_MARK + "/" + DEFAULT_ROOTS[variable], HOME_MARK + "//" + DEFAULT_ROOTS[variable], default, default + os.sep,
                         os.path.join(self.home, ".", *DEFAULT_ROOTS[variable].split("/")))
            for spelling in spellings:
                with self.subTest(variable=variable, spelling=spelling):
                    code, stdout, stderr = self.run_main([harness, "status"], {variable: spelling})
                    self.assertEqual((0, ""), (code, stderr))
                    self.assertTrue(stdout.startswith("Session logging is off."), stdout)

    def test_the_spelled_out_default_claude_root_still_converts_the_old_flag_and_hooks_run(self):
        self.seed_old_flag(self.log_root_of("claude"))
        env = {"CLAUDE_CONFIG_DIR": "~/.claude"}
        self.assertEqual(SILENT_SUCCESS, self.run_main(HOOKS["claude"][0], env))
        self.assertIn(NEW_FLAG, os.listdir(self.log_root_of("claude")))
        self.assertNotIn(OLD_FLAG, os.listdir(self.log_root_of("claude")))

    def test_a_variable_reaching_the_default_root_through_a_link_works_as_unset(self):
        os.makedirs(self.root_of("claude"))
        alias = os.path.join(self.home, "alias")
        os.symlink(self.root_of("claude"), alias)
        code, _, stderr = self.run_main(["claude", "status"], {"CLAUDE_CONFIG_DIR": alias})
        self.assertEqual((0, ""), (code, stderr))


@unittest.skipUnless(hasattr(os, "symlink"), "needs symbolic links")
class SymlinkedRootTest(RootCheckCase):
    def make_link(self, harness, which):
        """Makes the harness root (or its `prompt-logs`) a link to a real folder that holds an old `.enabled`; returns that folder."""
        target = os.path.join(self.home, "dotfiles", harness)
        if which == "root":
            os.makedirs(os.path.join(target, LOG_FOLDER))
            self.seed_old_flag(os.path.join(target, LOG_FOLDER))
            os.makedirs(os.path.dirname(self.root_of(harness)), exist_ok=True)
            os.symlink(target, self.root_of(harness))
            return os.path.join(target, LOG_FOLDER)
        os.makedirs(target)
        self.seed_old_flag(target)
        os.makedirs(self.root_of(harness))
        os.symlink(target, self.log_root_of(harness))
        return target

    def test_symlinked_harness_root_or_prompt_logs_fails_command_and_hook_writes_nothing_even_with_old_enabled(self):
        for harness in ("claude", "codex", "cursor", "omp", "opencode"):
            for which in ("root", "prompt-logs"):
                with self.subTest(harness=harness, which=which):
                    shutil.rmtree(self.home, ignore_errors=True)
                    os.makedirs(self.home)
                    log_target = self.make_link(harness, which)
                    before = self.snapshot(self.home)
                    for command in ("on", "off", "status", "usage"):
                        code, stdout, stderr = self.run_main([harness, command])
                        self.assertEqual((1, ""), (code, stdout), command)
                        self.assertTrue(stderr.startswith("session-log: ") and stderr.count("\n") == 1, stderr)
                        self.assertIn("symbolic link", stderr)
                        self.assertIn(self.root_of(harness) if which == "root" else self.log_root_of(harness), stderr)
                    self.assertEqual([OLD_FLAG], os.listdir(log_target))
                    for hook in HOOKS.get(harness, ()):
                        self.assertEqual(SILENT_SUCCESS, self.run_main(hook))
                        self.assertEqual([OLD_FLAG], os.listdir(log_target))
                    self.assertEqual(before, self.snapshot(self.home))

    def test_a_relocated_root_is_reported_before_a_symlinked_one(self):
        self.make_link("claude", "root")
        moved = os.path.join(self.home, "moved")
        code, _, stderr = self.run_main(["claude", "status"], {"CLAUDE_CONFIG_DIR": moved})
        self.assertEqual(1, code)
        self.assertIn("CLAUDE_CONFIG_DIR", stderr)
        self.assertNotIn("symbolic link", stderr)

    def test_a_dangling_link_is_refused_too(self):
        os.symlink(os.path.join(self.home, "gone"), self.root_of("claude"))
        code, _, stderr = self.run_main(["claude", "status"])
        self.assertEqual(1, code)
        self.assertIn("symbolic link", stderr)
        for hook in HOOKS["claude"]:
            self.assertEqual(SILENT_SUCCESS, self.run_main(hook))

    def test_a_symlinked_root_with_the_default_spelled_out_names_the_link_not_a_relocation(self):
        self.make_link("claude", "root")
        code, _, stderr = self.run_main(["claude", "status"], {"CLAUDE_CONFIG_DIR": "~/.claude"})
        self.assertEqual(1, code)
        self.assertIn("symbolic link", stderr)
        self.assertNotIn("relocated", stderr)

    def test_a_real_root_and_prompt_logs_are_not_refused(self):
        self.seed_old_flag(self.log_root_of("claude"))
        code, _, stderr = self.run_main(["claude", "status"])
        self.assertEqual((0, ""), (code, stderr))


if __name__ == "__main__":
    unittest.main()
