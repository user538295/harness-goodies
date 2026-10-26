"""A rewrite never loses what the archive holds, and still takes new activity (sync, C6 `promptCount` and `subAgentCount`).

Real `FileStateStore` and real `LogUsageWriter` over a Claude store. The cases: a missing sub-agent file beside a new prompt, a
transcript cut to fewer prompts, a restored unit, an entry written before the counts existed, and a reader fix that drops rows
from a source nothing has changed."""
import os
from dataclasses import replace
from unittest.mock import patch

from sessionlog.commands import UnreadableItem
from sessionlog.sync import FEWER_PROMPTS_REASON, PARTLY_GONE_REASON, RENDER_VERSION
from tests.test_sync import LONG_AGO, NOW, CountingReader, _agent_transcript, _transcript
from tests.test_sync_safety import AGENT_SPAN, BASE, CWD, MINUTE, SafetyTestCase, _read_bytes, _report_path

SECOND_AGENT = "a2"
CUT_TO = 1
FULL_TRANSCRIPT_PROMPTS = 5


def _prompt_times(count):
    return tuple(BASE + number * MINUTE for number in range(count))


class _DroppingReader(CountingReader):
    """A reader after a fix that no longer reads the last prompt of a session (what a junk filter does)."""

    drop_last_prompt = False

    def read(self, session_key, units):
        result = super().read(session_key, units)
        if not self.drop_last_prompt or result.session is None:
            return result
        return replace(result, session=replace(result.session, prompts=result.session.prompts[:-1]))


class MissingSubAgentFileTest(SafetyTestCase):
    def _archive_main_with_two_sub_agents(self):
        self._install("s1", _transcript(BASE), NOW)
        self._agent_file("s1")
        second = self._install_agent("s1", SECOND_AGENT, _agent_transcript(SECOND_AGENT, BASE, BASE + AGENT_SPAN), NOW)
        self._sync()
        entry = self._entry("s1")
        self.assertIn("TOTAL (1 requests, 2 sub-agents)", _read_bytes(_report_path(entry["logPath"])).decode("utf-8"))
        self.assertEqual(entry["unitCount"], 3)
        return entry["logPath"], second

    def test_a_new_prompt_is_archived_after_a_sub_agent_file_was_deleted_and_the_report_keeps_both_rows(self):
        log_path, second = self._archive_main_with_two_sub_agents()
        old_report = _read_bytes(_report_path(log_path))
        os.unlink(second)
        self._install("s1", _transcript(*_prompt_times(2)), NOW)
        self._sync()
        self.assertIn("prompt 1", _read_bytes(log_path).decode("utf-8"))
        self.assertEqual(_read_bytes(_report_path(log_path)), old_report)
        self.assertIn("TOTAL (1 requests, 2 sub-agents)", old_report.decode("utf-8"))
        entry = self._entry("s1")
        self.assertEqual((entry["state"], entry["reason"]), ("withheld", PARTLY_GONE_REASON % (2, 3)))
        self.assertEqual((entry["promptCount"], entry["subAgentCount"]), (2, 2))
        self.assertEqual(self._commands().status().unreadable, (UnreadableItem(log_path, entry["reason"]),))
        self.assertEqual(self._commands().usage_latest(CWD).report_text, old_report.decode("utf-8"))

    def test_the_withheld_session_takes_every_further_prompt_and_is_read_only_when_a_unit_changes(self):
        log_path, second = self._archive_main_with_two_sub_agents()
        os.unlink(second)
        self._install("s1", _transcript(*_prompt_times(2)), NOW)
        self._sync()
        self.assertIn("prompt 1", _read_bytes(log_path).decode("utf-8"))
        self.reader.read_keys.clear()
        self._sync()
        self.assertEqual(self.reader.read_keys, [])  # nothing changed: a withheld session costs no read
        self._install("s1", _transcript(*_prompt_times(3)), NOW)
        self._sync()
        self.assertEqual(self.reader.read_keys, [self._key("s1")])
        self.assertIn("prompt 2", _read_bytes(log_path).decode("utf-8"))
        self.assertEqual(self._entry("s1")["promptCount"], 3)

    def test_a_restored_sub_agent_file_heals_the_session_even_when_it_is_older_than_the_on_time(self):
        log_path, second = self._archive_main_with_two_sub_agents()
        os.unlink(second)
        self._install("s1", _transcript(*_prompt_times(2)), NOW)
        self._sync()
        self.assertIn("prompt 1", _read_bytes(log_path).decode("utf-8"))  # the new prompt is archived while the unit is gone
        self._install_agent("s1", SECOND_AGENT, _agent_transcript(SECOND_AGENT, BASE, BASE + AGENT_SPAN), LONG_AGO)
        self._sync()
        self.assertIn("TOTAL (2 requests, 2 sub-agents)", _read_bytes(_report_path(log_path)).decode("utf-8"))
        entry = self._entry("s1")
        self.assertEqual((entry["state"], "reason" in entry, entry["subAgentCount"], entry["unitCount"]), ("written", False, 2, 3))

    def test_an_entry_without_counts_protects_nothing(self):
        log_path, second = self._archive_main_with_two_sub_agents()
        state = self._saved()
        state["sessions"][self._key("s1")].pop("promptCount", None)
        state["sessions"][self._key("s1")].pop("subAgentCount", None)
        self.state.save_sync(state)
        os.unlink(second)
        self._install("s1", _transcript(*_prompt_times(2)), NOW)
        self._sync()
        self.assertIn("TOTAL (2 requests, 1 sub-agents)", _read_bytes(_report_path(log_path)).decode("utf-8"))
        entry = self._entry("s1")
        self.assertEqual((entry["state"], "reason" in entry, entry["subAgentCount"]), ("written", False, 1))

    def test_deleting_every_source_leaves_both_files_byte_identical(self):
        """Guard, green on HEAD by design: the rule that protects the archive must stay."""
        path = self._install("s1", _transcript(BASE), NOW)
        agent = self._agent_file("s1")
        self._sync()
        archived = self._archived()
        os.unlink(path)
        os.unlink(agent)
        self._sync()
        self.assertEqual((self._archived(), self._entry("s1")["state"]), (archived, "written"))


class SourceThatLostPromptsTest(SafetyTestCase):
    def _archive_five_prompts(self):
        self._install("s1", _transcript(*_prompt_times(FULL_TRANSCRIPT_PROMPTS)), NOW)
        self._sync()
        return self._entry("s1")["logPath"]

    def test_a_transcript_cut_to_fewer_prompts_leaves_log_and_report_and_is_listed(self):
        log_path = self._archive_five_prompts()
        archived = self._archived()
        self._install("s1", _transcript(*_prompt_times(CUT_TO)), NOW)
        self._sync()
        self.assertEqual(self._archived(), archived)
        reason = FEWER_PROMPTS_REASON % (CUT_TO, FULL_TRANSCRIPT_PROMPTS)
        entry = self._entry("s1")
        self.assertEqual((entry["state"], entry["reason"], entry["promptCount"]), ("withheld", reason, FULL_TRANSCRIPT_PROMPTS))
        self.assertEqual(self._commands().status().unreadable, (UnreadableItem(log_path, reason),))
        self.assertEqual(self._commands().usage_latest(CWD).report_text, _read_bytes(_report_path(log_path)).decode("utf-8"))

    def test_the_cut_source_is_not_read_again_until_it_changes_and_grows_back_to_a_superset(self):
        log_path = self._archive_five_prompts()
        self._install("s1", _transcript(*_prompt_times(CUT_TO)), NOW)
        self._sync()
        self.assertIn("prompt 4", _read_bytes(log_path).decode("utf-8"))  # the archive still holds the fifth prompt
        self.reader.read_keys.clear()
        self._sync()
        self.assertEqual(self.reader.read_keys, [])
        self._install("s1", _transcript(*_prompt_times(FULL_TRANSCRIPT_PROMPTS - 1)), NOW)  # still fewer: still withheld
        self._sync()
        self.assertIn("prompt 4", _read_bytes(log_path).decode("utf-8"))
        self.assertEqual(self._entry("s1")["state"], "withheld")
        self._install("s1", _transcript(*_prompt_times(FULL_TRANSCRIPT_PROMPTS + 1)), NOW)
        self._sync()
        self.assertIn("prompt 5", _read_bytes(log_path).decode("utf-8"))
        entry = self._entry("s1")
        self.assertEqual((entry["state"], "reason" in entry, entry["promptCount"]), ("written", False, FULL_TRANSCRIPT_PROMPTS + 1))

    def test_a_source_with_the_same_prompts_and_new_tokens_is_written(self):
        """Guard, green on HEAD by design: tokens are not compared, so a rewrite with other numbers goes through."""
        log_path = self._archive_five_prompts()
        report = _read_bytes(_report_path(log_path))
        self._install("s1", _transcript(*_prompt_times(FULL_TRANSCRIPT_PROMPTS)).replace('"output_tokens": 1', '"output_tokens": 99'), NOW)
        self._sync()
        self.assertNotEqual(_read_bytes(_report_path(log_path)), report)
        self.assertEqual(self._entry("s1")["state"], "written")


class ReaderFixTest(SafetyTestCase):
    def setUp(self):
        super().setUp()
        self.reader = _DroppingReader(self.root)

    def test_a_reader_fix_that_drops_a_prompt_is_applied_to_a_source_nothing_has_changed(self):
        """Guard, green on HEAD by design: the new rule must not stop a reader fix from applying to an unchanged source."""
        self._install("s1", _transcript(*_prompt_times(2)), NOW)
        self._sync()
        log_path = self._entry("s1")["logPath"]
        self.assertIn("prompt 1", _read_bytes(log_path).decode("utf-8"))
        self.reader.drop_last_prompt = True
        with patch("sessionlog.sync.RENDER_VERSION", RENDER_VERSION + 1):
            self._sync()
        self.assertNotIn("prompt 1", _read_bytes(log_path).decode("utf-8"))
        entry = self._entry("s1")
        self.assertEqual((entry["state"], entry["renderVersion"]), ("written", RENDER_VERSION + 1))

    def test_the_same_fix_does_not_shrink_a_source_that_changed_since_the_write(self):
        self._install("s1", _transcript(*_prompt_times(2)), NOW)
        self._sync()
        log_path = self._entry("s1")["logPath"]
        self.reader.drop_last_prompt = True
        self._install("s1", _transcript(*_prompt_times(2)).replace("reply", "a longer reply"), NOW)
        with patch("sessionlog.sync.RENDER_VERSION", RENDER_VERSION + 1):
            self._sync()
        self.assertIn("prompt 1", _read_bytes(log_path).decode("utf-8"))  # behaviour first: the archive keeps the prompt
        self.assertEqual((self._entry("s1")["state"], self._entry("s1")["promptCount"]), ("withheld", 2))
