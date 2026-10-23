"""Private and link-free writes (BE-34; S50, S62): new folders 0700, every written file 0600 (a rewritten one too), existing folder modes kept,
and nothing written through a symlink. A symlinked slug folder, log file, `.records` or conversation folder is skipped and `status` names it.
Real writer, state store, readers and Cursor hook, through the entry in a sandbox home."""
import io
import json
import os
import shutil
import stat
import unittest
from datetime import datetime, timezone

from sessionlog.log_usage_writer import LogUsageWriter
from sessionlog.safe_paths import FOLDER_LINK_REASON, LINK_REASON
from tests import test_hook_records as hooks
from tests import test_session_log as session_log_tests
from tests.test_log_usage_writer import session

POSIX_ONLY = unittest.skipIf(os.name == "nt", "file modes are POSIX")
UMASK = 0o022
PRIVATE_FOLDER = 0o700
PRIVATE_FILE = 0o600
OPEN_FOLDER = 0o755
EXISTING_FOLDER = 0o750
OLD_FILE = 0o644
SECOND_PROMPT_AT = "2099-06-01T11:00:00.000Z"
SECOND_REPLY_AT = "2099-06-01T11:00:01.000Z"
SECOND_PROMPT = "second prompt"
ALPHA = "/work/alpha"
BETA = "/work/beta"
CONVERSATION = hooks.CONVERSATION
OTHER_CONVERSATION = hooks.OTHER_CONVERSATION
LEGACY_FLAG_TEXT = "old release flag\n"
STATUS = ["status"]
CLAUDE_HOOK = ["hook", "claude", "SessionStart"]
FILES_PER_SESSION = 2  # log and usage report
LEGACY_FLAG = ".enabled"
PROMPT_AT = datetime(2099, 6, 1, 10, tzinfo=timezone.utc)  # `test_session_log.PROMPT_AT`


def mode_of(path):
    return stat.S_IMODE(os.lstat(path).st_mode)


def entries_below(root):
    """Every folder and file below `root` (links not followed): path -> mode."""
    found = {}
    for base, folders, names in os.walk(root):
        for name in folders + names:
            path = os.path.join(base, name)
            found[path] = mode_of(path)
    return found


def snapshot_of(root):
    """The bytes of every file below `root`."""
    found = {}
    for base, _, names in os.walk(root):
        for name in names:
            with open(os.path.join(base, name), "rb") as stream:
                found[os.path.join(base, name)] = stream.read()
    return found


def append_turn(transcript_path, cwd, text=SECOND_PROMPT):
    records = [
        {"type": "user", "promptSource": "typed", "cwd": cwd, "timestamp": SECOND_PROMPT_AT,
         "message": {"role": "user", "content": text}},
        {"type": "assistant", "timestamp": SECOND_REPLY_AT,
         "message": {"id": "m1", "model": session_log_tests.UNPRICED_MODEL, "content": [{"type": "text", "text": "reply"}],
                     "usage": {"input_tokens": 1, "output_tokens": 1}}},
    ]
    with open(transcript_path, "a", encoding="utf-8", newline="\n") as stream:
        stream.write("".join(json.dumps(record) + "\n" for record in records))


class ClaudeAndCursorCase(hooks.CursorHookCase):
    def setUp(self):
        super().setUp()
        self.claude_logs = os.path.join(self.claude, session_log_tests.LOG_FOLDER)
        self.transcripts = os.path.join(self.claude, "projects", "-work-proj")

    def transcript(self, session_id):
        return os.path.join(self.transcripts, session_id + ".jsonl")

    def archived_files(self):
        return [path for path in snapshot_of(self.claude_logs) if os.path.basename(path).startswith("session_")]

    def archived_log(self, session_id):
        names = [path for path in self.archived_files() if path.endswith("_%s.md" % session_id)]
        self.assertEqual(1, len(names), names)
        return names[0]


@POSIX_ONLY
class PrivateModesTests(ClaudeAndCursorCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(os.umask, os.umask(UMASK))

    def test_umask_022_new_folders_0700_all_written_files_0600_existing_folder_mode_kept(self):
        os.makedirs(self.claude)
        os.chmod(self.claude, OPEN_FOLDER)
        self.write_transcript()
        with self.subTest("a first sync creates everything private"):
            self.assertEqual((0, ""), self.run_main(["claude", "on"])[::2])
            self.assertEqual(FILES_PER_SESSION, len(self.archived_files()))
            self.assert_everything_private(self.claude_logs)
            self.assertEqual(PRIVATE_FOLDER, mode_of(self.claude_logs))
            self.assertEqual(OPEN_FOLDER, mode_of(self.claude))
        with self.subTest("a rewrite makes the 0644 log 0600 and keeps the folders' modes"):
            self.assert_rewrite_private_and_modes_kept()
        with self.subTest("the Cursor hook creates its records private and keeps an existing .records"):
            self.assert_cursor_records_private()

    def assert_everything_private(self, root, kept=None):
        """Every file below `root` is 0600 and every folder 0700, except the folders in `kept` (path -> mode)."""
        kept = kept or {}
        for path, mode in entries_below(root).items():
            expected = kept.get(path, PRIVATE_FILE if os.path.isfile(path) else PRIVATE_FOLDER)
            self.assertEqual(expected, mode, path)

    def assert_rewrite_private_and_modes_kept(self):
        log = self.archived_log("abc123")
        slug = os.path.dirname(log)
        for folder in (self.claude_logs, slug):
            os.chmod(folder, EXISTING_FOLDER)
        state_file = os.path.join(self.claude_logs, hooks.SYNC_STATE)
        for path in [*self.archived_files(), state_file]:
            os.chmod(path, OLD_FILE)
        append_turn(self.transcript("abc123"), session_log_tests.CWD)
        self.assertEqual(0, self.run_main(["claude"] + STATUS)[0])
        self.assertIn(SECOND_PROMPT, snapshot_of(slug)[log].decode("utf-8"))
        self.assertEqual(FILES_PER_SESSION, len(self.archived_files()))
        self.assert_everything_private(self.claude_logs, {slug: EXISTING_FOLDER})
        self.assertEqual(EXISTING_FOLDER, mode_of(self.claude_logs))

    def assert_cursor_records_private(self):
        self.assertEqual((0, ""), self.run_main(["cursor", "on"])[::2])
        self.assertEqual(hooks.SILENT_SUCCESS, self.run_cursor_hook("stop", hooks.payload(conversation_id=CONVERSATION)))
        self.assertEqual(hooks.SILENT_SUCCESS, self.run_cursor_hook("stop", b"junk"))
        self.assertEqual(sorted([CONVERSATION, hooks.INVALID_FOLDER]), sorted(os.listdir(self.records)))
        self.assert_everything_private(self.log_root)
        self.assertEqual((PRIVATE_FOLDER, PRIVATE_FOLDER), (mode_of(self.log_root), mode_of(self.cursor)))
        os.chmod(self.records, EXISTING_FOLDER)
        self.assertEqual(hooks.SILENT_SUCCESS, self.run_cursor_hook("stop", hooks.payload(conversation_id=OTHER_CONVERSATION)))
        self.assertEqual(EXISTING_FOLDER, mode_of(self.records))
        self.assertEqual(PRIVATE_FOLDER, mode_of(os.path.join(self.records, OTHER_CONVERSATION)))
        self.assert_everything_private(self.log_root, {self.records: EXISTING_FOLDER})


@unittest.skipUnless(hasattr(os, "symlink"), "needs symlinks")
class LinkedPathsTests(ClaudeAndCursorCase):
    def setUp(self):
        super().setUp()
        self.elsewhere = os.path.join(self.home, "elsewhere")
        os.makedirs(self.elsewhere)

    def reset_home(self):
        """A fresh sandbox inside one test: both harness folders are gone and `elsewhere` is empty again."""
        for folder in (self.claude, self.cursor, self.elsewhere):
            shutil.rmtree(folder, ignore_errors=True)
        os.makedirs(self.elsewhere)
        self.start_over()

    def link(self, link, target):
        try:
            os.symlink(target, link, target_is_directory=os.path.isdir(target))
        except (OSError, NotImplementedError):
            self.skipTest("cannot create symlinks here")

    def status_lines(self, harness):
        code, stdout, stderr = self.run_main([harness] + STATUS)
        self.assertEqual((0, ""), (code, stderr))
        return stdout.splitlines()

    def test_symlinked_slug_folder_log_file_records_and_conversation_folder_skipped_and_named_in_status(self):
        with self.subTest("an archived session's slug folder and log file are links"):
            self.archive_two_projects_then_link_a_slug_folder_and_a_log_file()
        with self.subTest("a `.records` and a conversation folder that are links"):
            self.cursor_folders_are_skipped()

    def archive_two_projects_then_link_a_slug_folder_and_a_log_file(self):
        self.write_transcript(cwd=ALPHA, session="alpha")
        self.write_transcript(cwd=BETA, session="beta")
        self.assertEqual((0, ""), self.run_main(["claude", "on"])[::2])
        alpha_log, beta_log = self.archived_log("alpha"), self.archived_log("beta")
        alpha_folder = os.path.dirname(alpha_log)
        moved = os.path.join(self.elsewhere, "moved-alpha-folder")
        shutil.move(alpha_folder, moved)
        self.link(alpha_folder, moved)
        beta_copy = os.path.join(self.elsewhere, "beta-log-copy.md")
        shutil.copyfile(beta_log, beta_copy)
        os.unlink(beta_log)
        self.link(beta_log, beta_copy)
        before = snapshot_of(self.elsewhere)
        append_turn(self.transcript("alpha"), ALPHA)
        append_turn(self.transcript("beta"), BETA)
        for _ in range(2):  # the skip is retried by every sync, not remembered as done
            lines = self.status_lines("claude")
            self.assertEqual(before, snapshot_of(self.elsewhere))
            self.assertTrue(os.path.islink(alpha_folder) and os.path.islink(beta_log))
            self.assertIn("  %s: %s" % (alpha_log, FOLDER_LINK_REASON), lines)
            self.assertIn("  %s: %s" % (beta_log, LINK_REASON), lines)
        with open(beta_log[:-3] + ".usage.txt", encoding="utf-8") as stream:
            self.assertIn("TOTAL (2 requests", stream.read())  # the report beside a linked log is still written
        os.unlink(alpha_folder)
        shutil.move(moved, alpha_folder)
        os.unlink(beta_log)
        shutil.move(beta_copy, beta_log)
        lines = self.status_lines("claude")
        self.assertEqual([], [line for line in lines if LINK_REASON in line])
        for log in (alpha_log, beta_log):
            self.assertIn(SECOND_PROMPT, snapshot_of(os.path.dirname(log))[log].decode("utf-8"))

    def cursor_folders_are_skipped(self):
        self.assertEqual((0, ""), self.run_main(["cursor", "on"])[::2])
        records_target = os.path.join(self.home, "records-target")
        os.makedirs(records_target)
        self.link(self.records, records_target)
        for raw in (hooks.payload(conversation_id=CONVERSATION), b"junk"):
            self.assertEqual(hooks.SILENT_SUCCESS, self.run_cursor_hook("stop", raw))
        self.assertEqual([], os.listdir(records_target))
        self.assertIn("  %s: %s" % (self.records, LINK_REASON), self.status_lines("cursor"))
        os.unlink(self.records)
        os.makedirs(self.records)
        conversation_target = os.path.join(self.home, "conversation-target")
        os.makedirs(conversation_target)
        linked = os.path.join(self.records, CONVERSATION)
        self.link(linked, conversation_target)
        for conversation in (CONVERSATION, OTHER_CONVERSATION):
            self.assertEqual(hooks.SILENT_SUCCESS, self.run_cursor_hook("stop", hooks.payload(conversation_id=conversation)))
        self.assertEqual([], os.listdir(conversation_target))
        self.assertEqual(1, len(os.listdir(os.path.join(self.records, OTHER_CONVERSATION))))
        lines = self.status_lines("cursor")
        self.assertIn("  %s: %s" % (linked, LINK_REASON), lines)
        self.assertEqual([], [line for line in lines if OTHER_CONVERSATION in line])

    def test_first_write_into_a_symlinked_slug_folder_is_skipped_named_by_its_source_and_written_once_the_link_is_gone(self):
        self.write_transcript(cwd=ALPHA, session="alpha")
        os.makedirs(self.claude_logs)
        writer = LogUsageWriter(self.claude_logs, lambda moment: moment)
        slug = os.path.dirname(writer.new_log_path(session(cwd=ALPHA, session_id="alpha", started_at=PROMPT_AT)))
        self.link(slug, self.elsewhere)
        self.assertEqual((0, ""), self.run_main(["claude", "on"])[::2])
        self.assertEqual([], os.listdir(self.elsewhere))
        source = self.transcript("alpha")
        lines = self.status_lines("claude")
        self.assertEqual(1, len([line for line in lines if line.startswith("  %s: " % source) and FOLDER_LINK_REASON in line]), lines)
        self.assertEqual([], os.listdir(self.elsewhere))
        os.unlink(slug)
        self.assertEqual([], [line for line in self.status_lines("claude") if LINK_REASON in line])
        self.assertEqual(FILES_PER_SESSION, len(self.archived_files()))

    def test_claude_hook_over_a_symlinked_slug_folder_exits_0_silently_writes_nothing_through_it_and_syncs_again_once_the_link_is_gone(self):
        self.write_transcript(cwd=ALPHA, session="alpha")
        self.assertEqual((0, ""), self.run_main(["claude", "on"])[::2])
        log = self.archived_log("alpha")
        folder = os.path.dirname(log)
        moved = os.path.join(self.elsewhere, "moved")
        shutil.move(folder, moved)
        self.link(folder, moved)
        append_turn(self.transcript("alpha"), ALPHA)
        before = snapshot_of(self.elsewhere)
        self.assertEqual(hooks.SILENT_SUCCESS, self.run_main(CLAUDE_HOOK, stdin=io.BytesIO(b"")))
        self.assertEqual(before, snapshot_of(self.elsewhere))
        os.unlink(folder)
        shutil.move(moved, folder)
        self.assertEqual(hooks.SILENT_SUCCESS, self.run_main(CLAUDE_HOOK, stdin=io.BytesIO(b"")))
        self.assertIn(SECOND_PROMPT, snapshot_of(folder)[log].decode("utf-8"))

    def seed_old_enabled_flag(self, folder):
        os.makedirs(folder, exist_ok=True)
        legacy = os.path.join(folder, LEGACY_FLAG)
        with open(legacy, "w", encoding="utf-8") as stream:
            stream.write(LEGACY_FLAG_TEXT)
        return legacy

    def assert_old_enabled_flag_converted(self, folder, legacy):
        self.assertFalse(os.path.lexists(legacy))
        self.assertTrue(os.path.isfile(os.path.join(folder, session_log_tests.ON_FLAG)))

    def test_old_enabled_flag_is_converted_and_a_linked_slug_folder_or_log_is_still_skipped_and_named(self):
        for case in ("slug folder", "log file"):
            with self.subTest(case):
                self.reset_home()
                self.write_transcript(cwd=ALPHA, session="alpha")
                os.makedirs(self.claude_logs)
                writer = LogUsageWriter(self.claude_logs, lambda moment: moment)
                log = writer.new_log_path(session(cwd=ALPHA, session_id="alpha", started_at=PROMPT_AT))
                if case == "slug folder":
                    self.link(os.path.dirname(log), self.elsewhere)
                else:
                    os.makedirs(os.path.dirname(log))
                    self.link(log, os.path.join(self.elsewhere, "log.md"))
                legacy = self.seed_old_enabled_flag(self.claude_logs)
                lines = self.status_lines("claude")
                self.assert_old_enabled_flag_converted(self.claude_logs, legacy)
                self.assertEqual([], os.listdir(self.elsewhere))
                reason = FOLDER_LINK_REASON if case == "slug folder" else LINK_REASON
                self.assertEqual(1, len([line for line in lines if line.endswith(": " + log + ": " + reason)]), lines)

    def test_old_enabled_flag_is_converted_and_a_hook_over_a_linked_records_or_conversation_folder_records_nothing(self):
        for case in (".records", "conversation folder"):
            with self.subTest(case):
                self.reset_home()
                legacy = self.seed_old_enabled_flag(self.log_root)
                linked = self.records
                if case == "conversation folder":
                    os.makedirs(self.records)
                    linked = os.path.join(self.records, CONVERSATION)
                self.link(linked, self.elsewhere)
                self.assertEqual(hooks.SILENT_SUCCESS, self.run_cursor_hook("stop", hooks.payload(conversation_id=CONVERSATION)))
                self.assert_old_enabled_flag_converted(self.log_root, legacy)
                self.assertEqual([], os.listdir(self.elsewhere))
                self.assertEqual(hooks.SILENT_SUCCESS, self.run_cursor_hook("sessionStart"))
                self.assertEqual([], os.listdir(self.elsewhere))
                self.assertIn("  %s: %s" % (linked, LINK_REASON), self.status_lines("cursor"))


if __name__ == "__main__":
    unittest.main()
