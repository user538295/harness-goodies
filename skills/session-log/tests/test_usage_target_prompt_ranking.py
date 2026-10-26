"""`usage --latest` among never-archived sessions names the session with the newest PROMPT, never the newest file: a copied or touched
transcript has a fresh mtime and old prompts, and a restored one has an old mtime and the newest prompt."""
import os
import unittest

from sessionlog.command_error import CommandError
from tests.test_sync import NOW, ON_AT, ONE_DAY
from tests.test_usage_target import BEFORE_ON, OTHER_PROJECT, PROJECT, UsageTargetTestCase, _report
from tests.test_usage_target_resolution import _ReadCounter, _WholeReadCounter

BROKEN_JSON = b"\xff\xfe\xfd not json\n"


class NewestPromptOfNeverArchivedSessionsTest(UsageTargetTestCase):
    def _break_sub_agent_file(self, session_id):
        self._install_sub_agent(session_id, "child", BEFORE_ON)
        path = os.path.join(self.folder, session_id, "subagents", "agent-child.jsonl")
        with open(path, "wb") as handle:
            handle.write(BROKEN_JSON)
        os.utime(path, (BEFORE_ON.timestamp(),) * 2)  # older than the on-time: sync never opens it

    def test_an_old_file_holding_the_newest_prompt_beats_a_fresh_file_holding_an_older_prompt(self):
        self._install("newest-prompt-old-file", prompts=[BEFORE_ON], modified=BEFORE_ON - 5 * ONE_DAY)
        self._install("fresh-file-older-prompt", prompts=[BEFORE_ON - 3 * ONE_DAY], modified=NOW)
        self._install("no-prompt", modified=NOW)
        self.assertEqual(self._text(), _report("newest-prompt-old-file"))

    def test_the_newest_prompt_of_a_session_counts_not_its_first(self):
        self._install("early-and-late", prompts=[BEFORE_ON - 4 * ONE_DAY, BEFORE_ON], modified=BEFORE_ON - 6 * ONE_DAY)
        self._install("middle", prompts=[BEFORE_ON - ONE_DAY], modified=NOW)
        self.assertEqual(self._text(), _report("early-and-late", 2))

    def test_a_tie_of_the_newest_prompt_goes_to_the_session_with_the_newest_file(self):
        self._install("older-file", prompts=[BEFORE_ON], modified=BEFORE_ON - ONE_DAY)
        self._install("newer-file", prompts=[BEFORE_ON], modified=BEFORE_ON)
        self.assertEqual(self._text(), _report("newer-file"))

    def test_the_newest_prompt_decides_in_the_nearest_folder_whatever_the_files_of_the_parent_are(self):
        self._install("exact-old-file", cwd=PROJECT + "/src", prompts=[BEFORE_ON - 2 * ONE_DAY], modified=BEFORE_ON - 9 * ONE_DAY)
        self._install("exact-fresh-file", cwd=PROJECT + "/src", prompts=[BEFORE_ON - 3 * ONE_DAY], modified=NOW)
        self._install("parent-newest-prompt", prompts=[BEFORE_ON], modified=NOW)
        self.assertEqual(self._text(cwd=PROJECT + "/src"), _report("exact-old-file"))
        self.assertEqual(self._text(), _report("parent-newest-prompt"))

    def test_a_never_archived_session_with_an_old_file_and_a_newer_prompt_beats_an_archived_session(self):
        self._install("archived", prompts=[ON_AT + ONE_DAY])
        self._install("restored", prompts=[ON_AT + 2 * ONE_DAY], modified=BEFORE_ON)  # its file is older than the on-time: never archived
        self._turn_on_and_sync()
        self.assertEqual(set(self.state.load_sync()["sessions"]), {"claude:archived"})
        self.assertEqual(self._text(), _report("restored"))

    def test_the_sessions_of_the_project_are_ranked_from_their_main_files_and_only_the_winner_is_read_whole(self):
        stores = {"oldest-prompt": (BEFORE_ON - 9 * ONE_DAY, NOW), "newest-prompt": (BEFORE_ON, NOW - 2 * ONE_DAY),
                  "middle-prompt": (BEFORE_ON - 4 * ONE_DAY, NOW - ONE_DAY)}  # the newest file holds the oldest prompt
        for session_id, (prompt_at, modified) in stores.items():
            self._install(session_id, prompts=[prompt_at], modified=modified)
            self._install_sub_agent(session_id, "child", prompt_at)
        reads, whole = _ReadCounter(self.reader), _WholeReadCounter(self.reader)
        self.assertEqual(self._text(), _report("newest-prompt"))
        self.assertEqual(sorted(set(reads.keys)), ["claude:middle-prompt", "claude:newest-prompt", "claude:oldest-prompt"])
        self.assertEqual(whole.keys, ["claude:newest-prompt"])

    def test_a_winner_whose_sub_agent_file_cannot_be_read_ends_the_lookup_alone_and_is_passed_over_beside_an_archived_session(self):
        self._install("older", prompts=[ON_AT + 2 * ONE_DAY], modified=BEFORE_ON)
        self._install("newer", prompts=[ON_AT + 3 * ONE_DAY], modified=BEFORE_ON - ONE_DAY)
        self._break_sub_agent_file("older")
        self._break_sub_agent_file("newer")
        with self.assertRaisesRegex(CommandError, "newer/subagents/agent-child.jsonl"):
            self._text()
        self._install("archived", prompts=[ON_AT + ONE_DAY])
        self._turn_on_and_sync()
        self.state.delete_on_flag()
        self.assertEqual(self._text(), _report("archived"))  # both store sessions are passed over

    def test_the_next_best_session_wins_when_the_best_one_cannot_be_read_whole_beside_an_archived_session(self):
        self._install("middle", prompts=[ON_AT + 2 * ONE_DAY], modified=BEFORE_ON)
        self._install("best", prompts=[ON_AT + 3 * ONE_DAY], modified=BEFORE_ON - ONE_DAY)
        self._break_sub_agent_file("best")
        self._install("archived", prompts=[ON_AT + ONE_DAY])
        self._turn_on_and_sync()
        self.state.delete_on_flag()
        self.assertEqual(self._text(), _report("middle"))

    def test_an_unreadable_main_file_of_another_project_ends_the_lookup_as_it_may_hold_the_newest_prompt(self):
        self._install("here", prompts=[BEFORE_ON], modified=NOW)
        path = self._install("elsewhere", cwd=OTHER_PROJECT, prompts=[BEFORE_ON], modified=BEFORE_ON - 9 * ONE_DAY)
        with open(path, "wb") as handle:
            handle.write(BROKEN_JSON)
        os.utime(path, (BEFORE_ON.timestamp() - 9 * ONE_DAY.total_seconds(),) * 2)
        with self.assertRaisesRegex(CommandError, "elsewhere.jsonl"):
            self._text()


if __name__ == "__main__":
    unittest.main()
