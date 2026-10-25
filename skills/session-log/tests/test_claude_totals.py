"""Claude totals and prompt text: a forked session counts only what it did itself, and a slash command is logged as typed."""
import json

from sessionlog.claude_transcript import parse_main_session
from tests.test_claude_reader import ClaudeReaderTestCase, _answer, _at, _plain_user, _typed, _usage

PARENT = "11111111-2222-4333-8444-555555555555"
FORK = "66666666-7777-4888-8999-aaaaaaaaaaaa"
PARENT_OUTPUT_TOKENS = 700
OWN_OUTPUT_TOKENS = 5


def _replayed(record: dict, uuid: str) -> dict:
    """What `/branch` writes for each copied record: the parent's record under the new session id, marked `forkedFrom`."""
    return {**record, "uuid": uuid, "sessionId": FORK, "forkedFrom": {"sessionId": PARENT, "messageUuid": uuid}}


def _parent_history() -> list:
    return [_replayed(_typed(1, "parent prompt"), "u1"),
            _replayed(_answer(2, "parent-m1", _usage(PARENT_OUTPUT_TOKENS), "parent reply"), "a1")]


class ForkedSessionTests(ClaudeReaderTestCase):
    def test_a_fork_logs_only_its_own_prompts(self):
        records = _parent_history() + [_typed(10, "own prompt"), _answer(11, "own-m1", _usage(OWN_OUTPUT_TOKENS), "own reply")]
        (prompt,) = self._prompts(records)
        self.assertEqual((prompt.text, prompt.reply_text), ("own prompt", "own reply"))

    def test_a_fork_counts_only_its_own_tokens(self):
        records = _parent_history() + [_typed(10, "own prompt"), _answer(11, "own-m1", _usage(OWN_OUTPUT_TOKENS), "own reply")]
        (prompt,) = self._prompts(records)
        self.assertEqual([request.output_tokens for request in prompt.requests], [OWN_OUTPUT_TOKENS])

    def test_a_fork_starts_when_its_own_first_record_was_written(self):
        records = _parent_history() + [_typed(10, "own prompt"), _answer(11, "own-m1", _usage(), "own reply")]
        self.assertEqual(self._read(records).session.started_at, _at(10))

    def test_a_fork_reply_that_continues_a_parent_turn_is_not_counted_as_a_prompt(self):
        records = _parent_history() + [_answer(5, "late-m1", _usage(OWN_OUTPUT_TOKENS), "late"), _typed(10, "own prompt"),
                                       _answer(11, "own-m2", _usage(), "own reply")]
        (prompt,) = self._prompts(records)
        self.assertEqual(prompt.text, "own prompt")

    def test_a_fork_without_a_record_of_its_own_has_no_session(self):
        self.assertIsNone(self._read(_parent_history()).session)

    def test_a_fork_does_not_reserve_its_replayed_message_ids_for_the_main_session(self):
        transcript = parse_main_session(json.dumps(record) for record in
                                        _parent_history() + [_typed(10, "own"), _answer(11, "own-m1", _usage(), "r")])
        self.assertEqual(transcript.message_ids, frozenset({"own-m1"}))

    def test_a_resumed_session_with_the_same_id_keeps_every_prompt(self):
        records = [_typed(1, "before"), _answer(2, "m1", _usage(), "r1"), _typed(10, "after resume"), _answer(11, "m2", _usage(), "r2")]
        self.assertEqual([prompt.text for prompt in self._prompts(records)], ["before", "after resume"])

    def test_forked_from_that_is_not_an_object_does_not_hide_a_record(self):
        records = [_typed(1, "marked", forkedFrom=None), _answer(2, "m1", _usage(), "r1", forkedFrom="x"),
                   _typed(3, "also", forkedFrom=[])]
        self.assertEqual([prompt.text for prompt in self._prompts(records)], ["marked", "also"])


class SlashCommandTextTests(ClaudeReaderTestCase):
    def _command(self, text) -> str:
        records = [_plain_user(1, text), _answer(2, "m1", _usage(), "ok")]
        (prompt,) = self._prompts(records)
        return prompt.text

    def test_a_command_is_logged_as_the_user_typed_it_message_tag_first(self):
        text = ("<command-message>claude-goodies:foo</command-message>\n<command-name>/claude-goodies:foo</command-name>\n"
                "<command-args>arg1 arg2</command-args>")
        self.assertEqual(self._command(text), "/claude-goodies:foo arg1 arg2")

    def test_a_command_is_logged_as_the_user_typed_it_name_tag_first(self):
        text = ("<command-name>/claude-goodies:foo</command-name>\n<command-message>claude-goodies:foo</command-message>\n"
                "<command-args>arg1 arg2</command-args>")
        self.assertEqual(self._command(text), "/claude-goodies:foo arg1 arg2")

    def test_a_command_without_arguments_is_just_its_name(self):
        text = "<command-name>/compact</command-name>\n<command-message>compact</command-message>\n<command-args></command-args>"
        self.assertEqual(self._command(text), "/compact")

    def test_a_command_keeps_its_arguments_as_typed(self):
        text = ("<command-message>session-log</command-message>\n<command-name>/session-log</command-name>\n"
                "<command-args>usage\nne  formázd</command-args>")
        self.assertEqual(self._command(text), "/session-log usage\nne  formázd")

    def test_a_command_with_only_a_name_tag_is_its_name(self):
        self.assertEqual(self._command("<command-name>/x</command-name>"), "/x")
