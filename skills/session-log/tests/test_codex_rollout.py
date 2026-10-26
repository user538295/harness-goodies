"""Pure parsing of a Codex rollout (S7, S43): prompts, replies, requests and the session header; no file access."""
import json
import os
import unittest
from datetime import datetime, timedelta

from sessionlog.codex_rollout import parse_rollout, parse_session_meta
from sessionlog.model import RequestUsage

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "codex")
TOKEN_USAGE_RECORDS = os.path.join(FIXTURES, "root_with_token_usage_records.jsonl")
TOKEN_COUNT_ONLY = os.path.join(FIXTURES, "root_with_token_count_only.jsonl")
FORKED_CHILD = os.path.join(FIXTURES, "forked_child_with_inherited_total.jsonl")
STAMP = "2026-08-28T10:00:%06.3fZ"
THREAD = "11111111-1111-4111-8111-111111111111"
ROOT = "22222222-2222-4222-8222-222222222222"
USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens")
MAX_TOKENS = 2**63 - 1


def _at(second: float) -> datetime:
    return datetime.fromisoformat(STAMP % second)


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _line(record: dict) -> str:
    return json.dumps(record) + "\n"


def _meta(**payload) -> dict:
    fields = {"id": THREAD, "session_id": THREAD, "timestamp": STAMP % 0, "cwd": "/work/proj"}
    return {"timestamp": STAMP % 1, "type": "session_meta", "payload": {**fields, **payload}}


def _event(second, payload) -> dict:
    record = {"type": "event_msg", "payload": payload}
    if second is not None:
        record["timestamp"] = STAMP % second
    return record


def _user(second, text="ask", blocks=None) -> dict:
    content = blocks if blocks is not None else [{"type": "text", "text": text, "text_elements": []}]
    return _event(second, {"type": "item_completed", "item": {"type": "UserMessage", "content": content}})


def _answer(second, text="reply", blocks=None, phase=None) -> dict:
    content = blocks if blocks is not None else [{"type": "Text", "text": text}]
    item = {"type": "AgentMessage", "content": content}
    if phase is not None:
        item["phase"] = phase
    return _event(second, {"type": "item_completed", "item": item})


def _started(second, turn_id="turn") -> dict:
    return _event(second, {"type": "task_started", "turn_id": turn_id})


def _context(second, model="gpt-x", **extra) -> dict:
    return {"timestamp": STAMP % second, "type": "turn_context", "payload": {"model": model, **extra}}


def _usage(input_tokens=0, cached=0, cache_write=0, output=0) -> dict:
    return {"input_tokens": input_tokens, "cached_input_tokens": cached, "cache_write_input_tokens": cache_write,
            "output_tokens": output, "reasoning_output_tokens": 0, "total_tokens": input_tokens + output}


def _recorded(second, usage, thread_id=None) -> dict:
    payload = {"usage": usage} if thread_id is None else {"thread_id": thread_id, "usage": usage}
    return {"timestamp": STAMP % second, "type": "token_usage_record", "payload": payload}


def _count(second, total=None, last=None, info="given") -> dict:
    if info == "given":
        info = {"total_token_usage": total}
        if last is not None:
            info["last_token_usage"] = last
    return _event(second, {"type": "token_count", "info": info})


def _parse(records, meta=None):
    return parse_rollout([_line(meta or _meta())] + [_line(record) for record in records])


def _fixture_records(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


def _fixture(path):
    with open(path, encoding="utf-8") as handle:
        return parse_rollout(handle)


def _shape(request: RequestUsage):
    """(input, output, cache read, cache write) of a request."""
    return (request.input_tokens, request.output_tokens, request.cache_read_tokens, request.cache_write_5m_tokens)


def _shapes(rollout):
    """The request shapes of every prompt of a rollout."""
    return [[_shape(request) for request in prompt.requests] for prompt in rollout.prompts]


def _totals(path):
    """The `total_token_usage` of every token_count event with an info, read from the fixture's raw records."""
    return [record["payload"]["info"]["total_token_usage"] for record in _fixture_records(path)
            if record["type"] == "event_msg" and record["payload"].get("type") == "token_count" and record["payload"].get("info")]


def _sum_of(rollout, field):
    """The sum of one raw usage field over the requests of a rollout, rebuilt from the request tokens."""
    parts = {"input_tokens": lambda r: r.input_tokens + r.cache_read_tokens + r.cache_write_5m_tokens, "cached_input_tokens": lambda r: r.cache_read_tokens,
             "cache_write_input_tokens": lambda r: r.cache_write_5m_tokens, "output_tokens": lambda r: r.output_tokens}
    return sum(parts[field](request) for prompt in rollout.prompts for request in prompt.requests)


class SessionMetaTest(unittest.TestCase):
    def test_root_id_is_session_id_else_parent_thread_id_else_id(self):
        cases = (({"session_id": ROOT, "parent_thread_id": "p"}, ROOT), ({"session_id": None, "parent_thread_id": "p"}, "p"),
                 ({"session_id": None}, THREAD), ({"session_id": "", "parent_thread_id": ""}, THREAD))
        for payload, expected in cases:
            with self.subTest(payload=payload):
                meta = parse_session_meta(_line(_meta(**payload)))
                self.assertEqual((expected, THREAD), (meta.root_id, meta.thread_id))

    def test_a_record_without_any_id_has_no_root_id_and_is_not_a_meta(self):
        self.assertIsNone(parse_session_meta(_line(_meta(id=None, session_id=None))))

    def test_a_meta_without_the_threads_own_id_is_not_a_meta_even_with_a_session_id(self):
        for payload in ({"id": None, "session_id": ROOT}, {"id": "", "session_id": ROOT, "parent_thread_id": ROOT}):
            with self.subTest(payload=payload):
                self.assertIsNone(parse_session_meta(_line(_meta(**payload))))

    def test_started_at_is_the_payload_timestamp_else_the_record_timestamp(self):
        self.assertEqual(_at(0), parse_session_meta(_line(_meta())).started_at)
        without = _meta()
        del without["payload"]["timestamp"]
        self.assertEqual(_at(1), parse_session_meta(_line(without)).started_at)
        neither = _meta(timestamp="not a time")
        del neither["timestamp"]
        self.assertIsNone(parse_session_meta(_line(neither)).started_at)

    def test_a_timestamp_without_offset_is_no_time(self):
        self.assertEqual(_at(1), parse_session_meta(_line(_meta(timestamp="2026-08-28T10:00:00"))).started_at)

    def test_project_dir_is_the_recorded_cwd_when_it_is_text(self):
        self.assertEqual("/work/proj", parse_session_meta(_line(_meta())).project_dir)
        for cwd in (None, "", 5):
            with self.subTest(cwd=cwd):
                self.assertIsNone(parse_session_meta(_line(_meta(cwd=cwd))).project_dir)

    def test_anything_but_a_session_meta_object_is_not_a_meta(self):
        for text in ("", "   \n", "not json", "[1]", "{}", _line({"type": "event_msg", "payload": {}}),
                     _line({"type": "session_meta"}), _line({"type": "session_meta", "payload": []})):
            with self.subTest(text=text):
                self.assertIsNone(parse_session_meta(text))


class RolloutFrameTest(unittest.TestCase):
    def test_an_empty_rollout_has_no_meta_and_is_not_unparsable(self):
        for lines in ([], ["\n", "  \n"]):
            with self.subTest(lines=lines):
                rollout = parse_rollout(lines)
                self.assertEqual((None, (), False), (rollout.meta, rollout.prompts, rollout.unparsable))

    def test_a_first_line_that_is_not_session_meta_is_unparsable(self):
        for first in ("not json\n", "[1]\n", _line(_user(2)), _line({"type": "session_meta"})):
            with self.subTest(first=first):
                rollout = parse_rollout([first, _line(_meta())])
                self.assertEqual((None, (), True), (rollout.meta, rollout.prompts, rollout.unparsable))

    def test_blank_lines_before_the_meta_are_skipped(self):
        rollout = parse_rollout(["\n", "  \n", _line(_meta()), _line(_user(2))])
        self.assertEqual((THREAD, False, 1), (rollout.meta.root_id, rollout.unparsable, len(rollout.prompts)))

    def test_bad_lines_and_unknown_record_types_are_skipped_without_a_report(self):
        rollout = parse_rollout([_line(_meta()), "not json\n", "[1]\n", "\n", "7\n", _line({"type": "future_record", "payload": 1}),
                                 _line({"type": "event_msg", "payload": {"type": "future_event"}}),
                                 _line({"type": "event_msg", "payload": "text"}), _line({"type": "event_msg"}),
                                 _line(_user(2)), _line(_answer(3))])
        (prompt,) = rollout.prompts
        self.assertEqual(("ask", "reply", False), (prompt.text, prompt.reply_text, rollout.unparsable))

    def test_a_meta_header_carries_into_the_rollout(self):
        rollout = _parse([], meta=_meta(session_id=ROOT, cwd="/work/x"))
        self.assertEqual((ROOT, _at(0), "/work/x"), (rollout.meta.root_id, rollout.meta.started_at, rollout.meta.project_dir))

    def test_a_later_session_meta_line_never_replaces_the_first(self):
        rollout = _parse([_meta(id="other", session_id=ROOT)])
        self.assertEqual(THREAD, rollout.meta.root_id)


class PromptTest(unittest.TestCase):
    def test_prompts_only_from_user_message_items_injected_context_skipped(self):
        injected = [
            {"timestamp": STAMP % 2, "type": "response_item",
             "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "# AGENTS.md instructions"}]}},
            {"timestamp": STAMP % 2, "type": "response_item",
             "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "<environment_context>"}]}},
            {"timestamp": STAMP % 2, "type": "response_item",
             "payload": {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "<permissions>"}]}},
            {"timestamp": STAMP % 2, "type": "response_item",
             "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hello"}]}},
        ]
        ordinary = {"timestamp": STAMP % 2, "type": "response_item",
                    "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "the typed prompt"}]}}
        typed = _user(3, "the typed prompt")
        rollout = _parse(injected + [ordinary, typed, *injected, ordinary, _answer(4, "done")])
        self.assertEqual([("the typed prompt", _at(3))], [(p.text, p.submitted_at) for p in rollout.prompts])

    def test_the_real_fixture_has_the_typed_prompts_only(self):
        rollout = _fixture(TOKEN_USAGE_RECORDS)
        self.assertEqual(["prompt 1", "prompt 2"], [prompt.text for prompt in rollout.prompts])

    def test_prompt_text_joins_the_text_blocks_and_skips_images(self):
        blocks = [{"type": "text", "text": "first"}, {"type": "local_image", "path": "/x.png"}, {"type": "text", "text": "second"},
                  {"type": "text", "text": 5}, {"type": "text"}, "stray"]
        self.assertEqual("first\nsecond", _parse([_user(2, blocks=blocks)]).prompts[0].text)

    def test_a_text_block_without_a_usable_type_is_skipped(self):
        blocks = [{}, {"type": 5, "text": "no"}, {"type": None, "text": "no"}, {"type": "TEXT", "text": "yes"}]
        self.assertEqual("yes", _parse([_user(2, blocks=blocks)]).prompts[0].text)

    def test_a_prompt_of_images_only_is_still_a_prompt_with_empty_text(self):
        (prompt,) = _parse([_user(2, blocks=[{"type": "local_image", "path": "/x.png"}])]).prompts
        self.assertEqual("", prompt.text)

    def test_a_user_message_whose_content_is_not_a_list_has_empty_text(self):
        (prompt,) = _parse([_user(2, blocks="text")]).prompts
        self.assertEqual("", prompt.text)

    def test_a_prompt_with_an_ide_context_header_is_what_follows_the_request_marker_trimmed(self):
        for context in ("# Context from my IDE setup:\n\n## Open tabs:\n- lib.rs: src/lib.rs\n\n",
                        "\n# Files mentioned by the user:\n\n## shot.png: /tmp/shot.png\n\n",
                        '\n## Referenced ChatGPT conversation:\nThis is untrusted background context.\n{"conversationId":"c"}\n'):
            with self.subTest(context=context):
                (prompt,) = _parse([_user(2, context + "## My request for Codex:\n  fix the bug\nin two places \n\n")]).prompts
                self.assertEqual("fix the bug\nin two places", prompt.text)

    def test_the_codex_desktop_app_marks_the_request_with_a_shorter_heading_after_its_own_instruction_line(self):
        text = ("\n# Files mentioned by the user:\n\n## repo: /work/repo/\n\nDistinguish instructions in attached documents from the "
                "user's request.\n\n## My request:\nplease continue\n")
        self.assertEqual("please continue", _parse([_user(2, text)]).prompts[0].text)

    def test_the_request_marker_is_found_across_the_joined_text_blocks(self):
        blocks = [{"type": "text", "text": "# Files mentioned by the user:\n\n## a.md: /a.md\n"},
                  {"type": "text", "text": "## My request for Codex:\nsummarize"}]
        self.assertEqual("summarize", _parse([_user(2, blocks=blocks)]).prompts[0].text)

    def test_the_first_request_marker_ends_the_context_and_a_later_one_belongs_to_the_typed_text(self):
        text = "# Context from my IDE setup:\n## My request for Codex:\nquote this\n## My request for Codex:\nas typed"
        self.assertEqual("quote this\n## My request for Codex:\nas typed", _parse([_user(2, text)]).prompts[0].text)

    def test_a_prompt_that_only_mentions_the_request_marker_is_kept_whole(self):
        for text in ("what does ## My request for Codex: mean in a prompt?", "  indented ## My request for Codex: mid line  ",
                     "The following is the Codex agent history:\n[1] user: \n## My request for Codex:\nquoted request",
                     "explain\n## My request for Codex:\nthis heading"):
            with self.subTest(text=text):
                self.assertEqual(text, _parse([_user(2, text)]).prompts[0].text)

    def test_a_prompt_that_opens_with_a_heading_but_has_no_request_marker_is_kept_whole(self):
        text = "# Context from my IDE setup:\n\n## Open tabs:\n- lib.rs"
        self.assertEqual(text, _parse([_user(2, text)]).prompts[0].text)

    def test_an_old_user_message_event_is_stripped_of_its_context_the_same_way(self):
        legacy = _event(2, {"type": "user_message", "message": "# Files mentioned by the user:\n\n## a: /a\n\n## My request for Codex:\nold ask"})
        self.assertEqual("old ask", _parse([legacy]).prompts[0].text)

    def test_old_user_message_events_are_the_prompts_of_a_rollout_without_user_message_items(self):
        legacy = _event(2, {"type": "user_message", "message": "old prompt", "kind": "plain"})
        rollout = _parse([legacy, _event(3, {"type": "agent_message", "message": "old reply"}), _event(4, {"type": "user_message", "message": 5})])
        (prompt,) = rollout.prompts
        self.assertEqual(("old prompt", "old reply", _at(2), _at(3)), (prompt.text, prompt.reply_text, prompt.submitted_at, prompt.reply_at))

    def test_user_message_events_next_to_user_message_items_are_not_counted_twice_and_the_items_win(self):
        records = [_event(2, {"type": "user_message", "message": "event ask"}), _user(2, "item ask"),
                   _event(3, {"type": "agent_message", "message": "event reply"}), _answer(4, "item reply")]
        (prompt,) = _parse(records).prompts
        self.assertEqual(("item ask", "item reply", _at(4)), (prompt.text, prompt.reply_text, prompt.reply_at))

    def test_old_events_carry_requests_and_the_last_old_reply_is_the_reply(self):
        records = [_event(2, {"type": "user_message", "message": "old prompt"}), _event(3, {"type": "agent_message", "message": "first"}),
                   _recorded(4, _usage(5, output=1)), _event(5, {"type": "agent_message", "message": "last"})]
        (prompt,) = _parse(records).prompts
        self.assertEqual((("last", _at(5)), [(5, 1, 0, 0)]), ((prompt.reply_text, prompt.reply_at), [_shape(r) for r in prompt.requests]))

    def test_records_of_the_current_codex_build_with_their_extra_fields_are_read(self):
        attribution = {"turn_id": "t", "turn_trigger": "composer", "parent_turn_id": None, "initiating_agent_path": None, "root_turn_id": "t"}
        extras = {"rate_limits": {"limit_id": "codex", "primary": {"used_percent": 0.0}}, "model_context_window": 475000}
        total = {"total_token_usage": _usage(39170, output=120), "last_token_usage": _usage(39170, output=120), **extras}
        records = [_event(2, {"type": "task_started", "turn_attribution": attribution, "turn_id": "t", "root_turn_id": "t"}),
                   {"timestamp": STAMP % 2, "type": "world_state", "payload": {"full": True, "state": {}}},
                   _event(2, {"type": "thread_settings_applied", "thread_id": THREAD, "thread_settings": {"model": "gpt-x"}}),
                   _context(2, "gpt-x", effort="high", root_turn_id="t", workspace_roots=["/work/proj"]), _user(3),
                   {"timestamp": STAMP % 4, "type": "inter_agent_communication_metadata", "payload": {"trigger_turn": True}},
                   _event(4, {"type": "token_count", "info": total, "rate_limits": extras["rate_limits"]})]
        (prompt,) = _parse(records).prompts
        self.assertEqual(("ask", [("gpt-x", "high", (39170, 120, 0, 0))]),
                         (prompt.text, [(r.model_name, r.effort, _shape(r)) for r in prompt.requests]))

    def test_prompts_are_in_file_order_and_a_prompt_without_a_valid_time_is_dropped(self):
        records = [_user(2, "one"), _user(None, "no time"), _user(4, "three"),
                   _event(5, {"type": "item_completed", "item": {"type": "UserMessage", "content": []}})]
        records[3]["timestamp"] = "2026-08-28T10:00:05"  # no offset
        self.assertEqual(["one", "three"], [prompt.text for prompt in _parse(records).prompts])

    def test_prompts_keep_the_order_of_the_file_not_the_order_of_their_times(self):
        self.assertEqual(["late", "early"], [prompt.text for prompt in _parse([_user(5, "late"), _user(3, "early")]).prompts])

    def test_a_prompt_without_a_valid_time_is_dropped_with_its_replies_and_requests(self):
        records = [_user(2, "kept"), _recorded(3, _usage(1)), _user(None, "untimed"), _answer(4, "lost"), _recorded(5, _usage(2)),
                   _user(6, "later"), _recorded(7, _usage(3))]
        kept, later = _parse(records).prompts
        self.assertEqual((("kept", None, [1]), ("later", None, [3])),
                         ((kept.text, kept.reply_text, [r.input_tokens for r in kept.requests]),
                          (later.text, later.reply_text, [r.input_tokens for r in later.requests])))

    def test_other_item_types_are_not_prompts(self):
        records = [_event(2, {"type": "item_completed", "item": {"type": item_type, "content": [{"type": "text", "text": "x"}]}})
                   for item_type in ("Reasoning", "CommandExecution", "SubAgentActivity")]
        records.append(_event(3, {"type": "item_completed", "item": "UserMessage"}))
        records.append(_event(3, {"type": "item_completed"}))
        self.assertEqual((), _parse(records).prompts)


class ReplyTest(unittest.TestCase):
    def test_the_reply_is_the_last_agent_message_of_the_turn_and_its_time_the_latest_reply(self):
        records = [_user(2), _answer(3, "working on it"), _answer(5, "final"), _user(8, "again"), _answer(9, "second final")]
        first, second = _parse(records).prompts
        self.assertEqual(("final", _at(5), timedelta(seconds=3)), (first.reply_text, first.reply_at, first.working_time))
        self.assertEqual(("second final", _at(9), timedelta(seconds=1)), (second.reply_text, second.reply_at, second.working_time))

    def test_the_reply_is_the_last_final_answer_even_when_commentary_follows_it(self):
        records = [_user(2), _answer(3, "plan", phase="commentary"), _answer(5, "the answer", phase="final_answer"),
                   _answer(6, "afterthought", phase="commentary")]
        (prompt,) = _parse(records).prompts
        self.assertEqual(("the answer", _at(6)), (prompt.reply_text, prompt.reply_at))

    def test_without_a_final_answer_the_last_message_is_the_reply(self):
        records = [_user(2), _answer(3, "plan", phase="commentary"), _answer(4, "still working", phase="commentary")]
        self.assertEqual("still working", _parse(records).prompts[0].reply_text)

    def test_agent_message_paragraphs_are_joined_by_a_blank_line(self):
        blocks = [{"type": "Text", "text": "one"}, {"type": "Text", "text": "  "}, {"type": "Text", "text": "two"}]
        self.assertEqual("one\n\ntwo", _parse([_user(2), _answer(3, blocks=blocks)]).prompts[0].reply_text)

    def test_a_prompt_without_a_reply_has_no_reply_and_zero_working_time(self):
        (prompt,) = _parse([_user(2), _answer(3, blocks=[{"type": "Text", "text": " "}])]).prompts
        self.assertEqual((None, None, timedelta(0)), (prompt.reply_text, prompt.reply_at, prompt.working_time))

    def test_replies_before_the_first_prompt_belong_to_no_prompt(self):
        (prompt,) = _parse([_answer(2, "orphan"), _user(3)]).prompts
        self.assertIsNone(prompt.reply_text)

    def test_a_reply_without_a_time_keeps_its_text_and_leaves_the_time_to_the_other_replies(self):
        (prompt,) = _parse([_user(2), _answer(None, "text"), _answer(5, "timed")]).prompts
        self.assertEqual(("timed", _at(5)), (prompt.reply_text, prompt.reply_at))
        (untimed,) = _parse([_user(2), _answer(None, "text")]).prompts
        self.assertEqual(("text", None, timedelta(0)), (untimed.reply_text, untimed.reply_at, untimed.working_time))

    def test_a_reply_time_before_the_prompt_time_counts_as_the_prompt_time(self):
        (prompt,) = _parse([_user(5), _answer(3)]).prompts
        self.assertEqual((_at(5), timedelta(0)), (prompt.reply_at, prompt.working_time))

    def test_the_real_fixtures_reply_with_the_last_agent_message_of_each_prompt(self):
        first, second = _fixture(TOKEN_USAGE_RECORDS).prompts
        self.assertEqual(("reply 2", _utc("2026-09-11T08:20:31.976Z"), timedelta(minutes=8, seconds=15, milliseconds=273)),
                         (first.reply_text, first.reply_at, first.working_time))
        self.assertEqual(("reply 3", _utc("2026-09-11T09:43:23.363Z")), (second.reply_text, second.reply_at))

    def test_the_real_forked_child_replies_with_its_final_answer(self):
        (prompt,) = _fixture(FORKED_CHILD).prompts
        self.assertEqual("reply 5", prompt.reply_text)


class RequestTokensTest(unittest.TestCase):
    def test_input_is_input_minus_cached_and_minus_cache_write_because_both_are_inside_input_tokens(self):
        """The Responses API reports `input_tokens` as the whole prompt: cached and cache-write tokens are parts of it
        (`input_tokens_details.cached_tokens`, `.cache_write_tokens`), and `total_tokens` is input plus output."""
        records = [_user(2), _recorded(3, _usage(input_tokens=1000, cached=300, cache_write=50, output=70))]
        (request,) = _parse(records).prompts[0].requests
        self.assertEqual((650, 70, 300, 50, 0), (request.input_tokens, request.output_tokens, request.cache_read_tokens,
                                                  request.cache_write_5m_tokens, request.cache_write_1h_tokens))
        grown = [_user(2), _started(2), _count(3, _usage(input_tokens=1000, cached=300, cache_write=50, output=70))]
        (from_growth,) = _parse(grown).prompts[0].requests
        self.assertEqual(_shape(request), _shape(from_growth))

    def test_cache_read_larger_than_input_leaves_no_negative_input(self):
        (request,) = _parse([_user(2), _recorded(3, _usage(input_tokens=100, cached=300))]).prompts[0].requests
        self.assertEqual((0, 300), (request.input_tokens, request.cache_read_tokens))

    def test_cache_read_and_write_together_larger_than_input_leave_no_negative_input(self):
        (request,) = _parse([_user(2), _recorded(3, _usage(input_tokens=100, cached=30, cache_write=90))]).prompts[0].requests
        self.assertEqual((0, 30, 90), (request.input_tokens, request.cache_read_tokens, request.cache_write_5m_tokens))

    def test_a_request_of_only_cached_and_written_input_has_no_uncached_input(self):
        (request,) = _parse([_user(2), _recorded(3, _usage(input_tokens=100, cached=40, cache_write=60, output=10))]).prompts[0].requests
        self.assertEqual((0, 10, 40, 60), _shape(request))

    def test_a_token_field_that_is_not_a_whole_non_negative_number_counts_as_zero(self):
        usage = {"input_tokens": True, "cached_input_tokens": -5, "cache_write_input_tokens": "9", "output_tokens": 2.5}
        (request,) = _parse([_user(2), _recorded(3, usage)]).prompts[0].requests
        self.assertEqual((0, 0, 0, 0), _shape(request))

    def test_token_numbers_above_int64_are_zero_and_int64_max_is_kept(self):
        usage = {"input_tokens": MAX_TOKENS + 1, "cached_input_tokens": 0, "cache_write_input_tokens": MAX_TOKENS, "output_tokens": MAX_TOKENS}
        (request,) = _parse([_user(2), _recorded(3, usage)]).prompts[0].requests
        self.assertEqual((0, MAX_TOKENS, 0, MAX_TOKENS), _shape(request))

    def test_a_record_without_a_usage_object_is_no_request(self):
        records = [_user(2), {"type": "token_usage_record", "payload": {}}, {"type": "token_usage_record", "payload": {"usage": []}},
                   {"type": "token_usage_record"}]
        self.assertEqual((), _parse(records).prompts[0].requests)

    def test_model_and_effort_are_the_latest_turn_context_before_the_request(self):
        records = [_user(2), _recorded(3, _usage(1)), _context(4, "gpt-b", effort="high"), _recorded(5, _usage(1)),
                   _context(6, model=None), _recorded(7, _usage(1))]
        first, second, third = _parse(records).prompts[0].requests
        self.assertEqual(("", None), (first.model_name, first.effort))
        self.assertEqual(("gpt-b", "high"), (second.model_name, second.effort))
        self.assertEqual(("gpt-b", "high"), (third.model_name, third.effort))

    def test_effort_falls_back_to_the_collaboration_mode_setting(self):
        settings = {"collaboration_mode": {"settings": {"reasoning_effort": "low"}}}
        (request,) = _parse([_context(2, **settings), _user(3), _recorded(4, _usage(1))]).prompts[0].requests
        self.assertEqual("low", request.effort)
        (own,) = _parse([_context(2, effort="high", **settings), _user(3), _recorded(4, _usage(1))]).prompts[0].requests
        self.assertEqual("high", own.effort)

    def test_a_later_turn_context_with_a_model_but_no_effort_resets_the_effort(self):
        records = [_user(2), _context(3, "gpt-a", effort="high"), _recorded(4, _usage(1)), _context(5, "gpt-a"), _recorded(6, _usage(1))]
        first, second = _parse(records).prompts[0].requests
        self.assertEqual(("high", None), (first.effort, second.effort))

    def test_requests_belong_to_the_prompt_before_them_and_early_ones_to_the_first_prompt(self):
        records = [_recorded(2, _usage(1)), _user(3, "one"), _recorded(4, _usage(2)), _user(5, "two"), _recorded(6, _usage(3)),
                   _recorded(7, _usage(4))]
        first, second = _parse(records).prompts
        self.assertEqual(([1, 2], [3, 4]), ([r.input_tokens for r in first.requests], [r.input_tokens for r in second.requests]))

    def test_requests_of_a_rollout_without_prompts_are_in_no_prompt(self):
        self.assertEqual((), _parse([_recorded(2, _usage(1))]).prompts)

    def test_requests_carry_only_the_codex_fields(self):
        (request,) = _parse([_user(2), _recorded(3, _usage(1))]).prompts[0].requests
        self.assertEqual((None, None), (request.fast, request.recorded_cost_usd))


class RequestSourceTest(unittest.TestCase):
    def test_requests_from_token_usage_record_else_total_growth_with_fork_inherited_total_excluded(self):
        self.assertEqual([[(33977, 741, 0, 0), (949, 188, 33792, 0), (391, 188, 34560, 0), (11583, 215, 221056, 0),
                           (8592, 3825, 232448, 0), (7785, 128, 37888, 0)], [(65446, 4529, 0, 0)]],
                         _shapes(_fixture(TOKEN_USAGE_RECORDS)))
        self.assertEqual([[(4397, 617, 8448, 0)], [(147, 199, 12800, 0), (7833, 557, 12928, 0)]], _shapes(_fixture(TOKEN_COUNT_ONLY)))
        self.assertEqual([[(357, 837, 38272, 0),
                           (19630, 281, 13056, 0), (8514, 121, 32512, 0), (4170, 283, 40704, 0), (8630, 277, 43776, 0),
                           (8711, 744, 51968, 0), (9265, 1513, 60160, 0), (2598, 219, 68352, 0)]], _shapes(_fixture(FORKED_CHILD)))

    def test_token_usage_records_are_summed_by_their_own_usage_and_the_compaction_call_is_in(self):
        records = [record["payload"]["usage"] for record in _fixture_records(TOKEN_USAGE_RECORDS) if record["type"] == "token_usage_record"]
        rollout = _fixture(TOKEN_USAGE_RECORDS)
        for field in USAGE_FIELDS:
            with self.subTest(field=field):
                self.assertEqual(sum(usage[field] for usage in records), _sum_of(rollout, field))
        self.assertIn((8592, 3825, 232448, 0), [shape for shapes in _shapes(rollout) for shape in shapes])

    def test_growth_requests_add_up_to_the_final_total_less_the_starting_total(self):
        """Counting-only rollout: the starting total is zero. Forked child: it is the total at the end of the turn that replays its
        parent's history, the fifth event of the fixture (the first one comes before `task_started`)."""
        replay_end = 4
        counted, forked = _totals(TOKEN_COUNT_ONLY), _totals(FORKED_CHILD)
        for field in USAGE_FIELDS:
            with self.subTest(field=field, rollout="counting only"):
                self.assertEqual(counted[-1][field], _sum_of(_fixture(TOKEN_COUNT_ONLY), field))
            with self.subTest(field=field, rollout="forked child"):
                self.assertEqual(forked[-1][field] - forked[replay_end][field], _sum_of(_fixture(FORKED_CHILD), field))

    def test_token_count_events_are_ignored_once_the_rollout_has_a_token_usage_record(self):
        records = [_started(2), _user(2), _count(3, _usage(10, output=5)), _recorded(4, _usage(7, output=1)), _count(5, _usage(99, output=99))]
        (prompt,) = _parse(records).prompts
        self.assertEqual([(7, 1, 0, 0)], [_shape(request) for request in prompt.requests])

    def test_a_token_usage_record_after_the_growth_events_still_replaces_them(self):
        records = [_started(2), _user(2), _count(3, _usage(10)), _recorded(9, _usage(7))]
        self.assertEqual([(7, 0, 0, 0)], [_shape(r) for r in _parse(records).prompts[0].requests])

    def test_repeated_events_and_null_info_are_skipped_and_last_token_usage_is_never_summed(self):
        total = _usage(1000, cached=400, output=100)
        records = [_started(2), _user(2), _count(3, info=None), _count(4, total, last=_usage(1000, cached=400, output=100)),
                   _count(5, total, last=_usage(1000, cached=400, output=100)), _count(6, total, last=_usage(900)),
                   _count(7, _usage(1500, cached=700, output=130), last=_usage(500_000, output=500_000)),
                   _event(8, {"type": "token_count"}), _event(9, {"type": "token_count", "info": {"last_token_usage": _usage(5)}}),
                   _event(10, {"type": "token_count", "info": {"total_token_usage": "x"}})]
        self.assertEqual([(600, 100, 400, 0), (200, 30, 300, 0)], [_shape(r) for r in _parse(records).prompts[0].requests])

    def test_an_event_without_a_usable_total_changes_nothing_not_even_the_baseline(self):
        records = [_started(2), _user(2), _count(3, _usage(1000, output=100)), _count(4, {}), _count(5, {"input_tokens": "x"}),
                   _count(6, _usage(1200, output=120))]
        self.assertEqual([(1000, 100, 0, 0), (200, 20, 0, 0)], [_shape(r) for r in _parse(records).prompts[0].requests])

    def test_a_token_count_before_the_first_task_started_only_sets_the_starting_total(self):
        records = [_count(2, _usage(5000, cached=4000, output=500)), _started(3), _user(3), _count(4, _usage(5600, cached=4300, output=530)),
                   _count(5, _usage(5600, cached=4300, output=530))]
        (prompt,) = _parse(records).prompts
        self.assertEqual([(300, 30, 300, 0)], [_shape(request) for request in prompt.requests])

    def test_without_task_started_every_token_count_only_sets_the_starting_total(self):
        records = [_user(2), _count(3, _usage(10)), _count(4, _usage(20))]
        self.assertEqual((), _parse(records).prompts[0].requests)

    def test_a_growth_in_cache_write_alone_is_a_request(self):
        records = [_started(2), _user(2), _count(3, _usage(100, cache_write=0)), _count(4, _usage(100, cache_write=40))]
        self.assertEqual([(100, 0, 0, 0), (0, 0, 0, 40)], [_shape(r) for r in _parse(records).prompts[0].requests])

    def test_a_total_that_shrinks_becomes_the_new_baseline_and_is_never_a_negative_request(self):
        records = [_started(2), _user(2), _count(3, _usage(1000, output=100)), _count(4, _usage(200, output=20)),
                   _count(5, _usage(500, output=60))]
        self.assertEqual([(1000, 100, 0, 0), (300, 40, 0, 0)], [_shape(r) for r in _parse(records).prompts[0].requests])

    def test_a_field_of_the_total_that_is_not_a_usable_number_keeps_its_baseline_and_is_not_counted_twice_later(self):
        broken = {"input_tokens": 1500, "cached_input_tokens": "x", "cache_write_input_tokens": 0, "output_tokens": 120}
        records = [_started(2), _user(2), _count(3, _usage(1000, cached=400, output=100)), _count(4, broken),
                   _count(5, _usage(1600, cached=700, output=130))]
        self.assertEqual([(600, 100, 400, 0), (500, 20, 0, 0), (0, 10, 300, 0)], [_shape(r) for r in _parse(records).prompts[0].requests])

    def test_growth_requests_take_the_model_of_the_turn_that_grew_them(self):
        records = [_started(2), _context(2, "gpt-a"), _user(2), _count(3, _usage(10)), _started(4), _context(4, "gpt-b"),
                   _count(5, _usage(30))]
        first, second = _parse(records).prompts[0].requests
        self.assertEqual(("gpt-a", "gpt-b"), (first.model_name, second.model_name))


class SubAgentHeaderTest(unittest.TestCase):
    def test_meta_names_the_parent_thread_and_the_agent_type_and_marks_a_fork(self):
        spawn = {"subagent": {"thread_spawn": {"parent_thread_id": "p", "agent_role": "explorer"}}}
        cases = (({"parent_thread_id": "p", "agent_role": "reviewer"}, "p", "reviewer", True),
                 ({"parent_thread_id": "p", "source": spawn}, "p", "explorer", True),
                 ({"source": {"subagent": {"other": "guardian"}}, "parent_thread_id": "p"}, "p", "guardian", True),
                 ({"forked_from_id": "f"}, None, None, True),
                 ({}, None, None, False))
        for payload, parent, agent_type, forked in cases:
            with self.subTest(payload=payload):
                meta = parse_session_meta(_line(_meta(**payload)))
                self.assertEqual((parent, agent_type, forked), (meta.parent_thread_id, meta.agent_type, meta.forked))

    def test_a_parent_thread_id_role_or_source_that_is_not_text_is_ignored(self):
        for payload in ({"parent_thread_id": "", "agent_role": ""}, {"parent_thread_id": 5, "agent_role": 7, "source": "cli"},
                        {"source": {"subagent": "x"}}, {"source": {"subagent": {"thread_spawn": []}}}):
            with self.subTest(payload=payload):
                meta = parse_session_meta(_line(_meta(**payload)))
                self.assertEqual((None, None, False), (meta.parent_thread_id, meta.agent_type, meta.forked))

    def test_a_helper_thread_is_a_sub_agent_source_that_is_no_thread_spawn(self):
        spawn = {"subagent": {"thread_spawn": {"parent_thread_id": "p", "agent_role": "explorer"}}}
        cases = (({"source": {"subagent": {"other": "guardian"}}}, True), ({"source": {"subagent": "review"}}, True),
                 ({"source": {"subagent": "compact"}, "parent_thread_id": "p"}, True), ({"source": spawn}, False),
                 ({"source": {"subagent": {"thread_spawn": []}}}, False), ({"source": "cli"}, False), ({"source": "vscode"}, False),
                 ({"forked_from_id": "f", "source": "cli"}, False), ({"agent_role": "reviewer", "parent_thread_id": "p"}, False), ({}, False))
        for payload, helper in cases:
            with self.subTest(payload=payload):
                self.assertEqual(helper, parse_session_meta(_line(_meta(**payload))).helper)

    def test_a_role_of_the_payload_wins_over_the_one_of_the_spawn_source(self):
        source = {"subagent": {"thread_spawn": {"agent_role": "explorer"}, "other": "guardian"}}
        self.assertEqual("worker", parse_session_meta(_line(_meta(agent_role="worker", source=source))).agent_type)
        self.assertEqual("explorer", parse_session_meta(_line(_meta(source=source))).agent_type)

    def test_requests_of_a_rollout_without_a_prompt_are_still_listed_whole_in_file_order(self):
        rollout = _parse([_started(2), _recorded(3, _usage(10, output=1)), _recorded(4, _usage(20, output=2))])
        self.assertEqual(((), [(10, 1, 0, 0), (20, 2, 0, 0)]), (rollout.prompts, [_shape(request) for request in rollout.requests]))

    def test_requests_of_a_rollout_are_those_of_its_prompts_in_order(self):
        rollout = _parse([_recorded(2, _usage(1)), _user(3, "one"), _recorded(4, _usage(2)), _user(5, "two"), _recorded(6, _usage(3))])
        self.assertEqual([1, 2, 3], [request.input_tokens for request in rollout.requests])
        self.assertEqual(list(rollout.requests), [request for prompt in rollout.prompts for request in prompt.requests])

    def test_requests_are_the_growth_requests_when_the_rollout_has_no_token_usage_record(self):
        rollout = _parse([_started(2), _count(3, _usage(10)), _count(4, _usage(25))])
        self.assertEqual([10, 15], [request.input_tokens for request in rollout.requests])

    def test_last_moment_is_the_newest_time_of_any_record_whatever_the_order(self):
        rollout = _parse([_user(5), _event(3, {"type": "future_event"}), _event(None, {"type": "future_event"}),
                          {"timestamp": "not a time", "type": "future_record", "payload": {}}])
        self.assertEqual(_at(5), rollout.last_moment)

    def test_last_moment_counts_the_meta_line_and_is_none_without_any_usable_time(self):
        self.assertEqual(_at(1), _parse([_event(None, {"type": "future_event"})]).last_moment)
        meta = _meta()
        del meta["timestamp"]
        self.assertIsNone(_parse([], meta=meta).last_moment)
        self.assertIsNone(parse_rollout([]).last_moment)

    def test_first_moment_is_the_earliest_time_of_the_records_after_the_header_whatever_the_order(self):
        rollout = _parse([_user(5), _event(3, {"type": "future_event"}), _event(None, {"type": "future_event"}),
                          {"timestamp": "not a time", "type": "future_record", "payload": {}}, _event(4, {"type": "future_event"})])
        self.assertEqual(_at(3), rollout.first_moment)

    def test_first_moment_ignores_the_header_line_and_is_none_without_a_usable_time_after_it(self):
        self.assertIsNone(_parse([_event(None, {"type": "future_event"})]).first_moment)
        self.assertIsNone(_parse([]).first_moment)
        self.assertIsNone(parse_rollout([]).first_moment)

    def test_first_moment_may_lie_before_the_header_time_because_only_the_records_after_the_header_line_count(self):
        self.assertEqual(_at(0.5), _parse([_user(5), _event(0.5, {"type": "future_event"})]).first_moment)


class ForkedTotalTest(unittest.TestCase):
    """A fork starts from its parent's total; the parent's history it replays never counts as its own requests."""

    PARENT = "33333333-3333-4333-8333-333333333333"

    def _fork(self, records):
        return _parse(records, meta=_meta(session_id=self.PARENT, parent_thread_id=self.PARENT, forked_from_id=self.PARENT))

    def test_a_fork_replaying_its_parents_history_after_task_started_counts_only_its_own_growth(self):
        records = [_count(2, _usage(5000, cached=4000, output=500)), _started(3), _count(4, _usage(1000, cached=800, output=100)),
                   _count(5, _usage(3000, cached=2400, output=300)), _count(6, _usage(5000, cached=4000, output=500)), _user(7),
                   _count(8, _usage(5600, cached=4300, output=530))]
        (prompt,) = self._fork(records).prompts
        self.assertEqual([(300, 30, 300, 0)], [_shape(request) for request in prompt.requests])

    def test_the_same_records_of_a_rollout_that_is_no_fork_count_the_replayed_growth(self):
        records = [_count(2, _usage(5000, cached=4000, output=500)), _started(3), _count(4, _usage(1000, cached=800, output=100)),
                   _count(5, _usage(3000, cached=2400, output=300)), _user(7)]
        (prompt,) = _parse(records).prompts
        self.assertEqual([(400, 200, 1600, 0)], [_shape(request) for request in prompt.requests])

    def test_a_fork_total_below_the_highest_total_so_far_lowers_it_in_no_field(self):
        records = [_count(2, _usage(5000, output=500)), _started(3), _user(3), _count(4, _usage(4000, output=700)),
                   _count(5, _usage(4500, output=700))]
        (prompt,) = self._fork(records).prompts
        self.assertEqual([(0, 200, 0, 0)], [_shape(request) for request in prompt.requests])

    def test_a_fork_still_counts_growth_above_its_inherited_total_in_every_field(self):
        records = [_count(2, _usage(5000, cached=4000, output=500)), _started(3), _user(3),
                   _count(4, _usage(5100, cached=4000, cache_write=20, output=510))]
        (prompt,) = self._fork(records).prompts
        self.assertEqual([(80, 10, 0, 20)], [_shape(request) for request in prompt.requests])

    def test_a_fork_with_no_inherited_total_before_its_first_task_started_counts_its_replay_as_its_own_requests(self):
        """The documented limit: a replay that carries no mark (`rollout-<n>` turn id, `started_at` before the header) and no inherited starting total counts."""
        rollout = self._fork([_started(2), _count(3, _usage(1000, output=100)), _count(4, _usage(3000, output=300))])
        self.assertEqual([(1000, 100, 0, 0), (2000, 200, 0, 0)], [_shape(request) for request in rollout.requests])


class ForkOwnTurnInReplayTest(unittest.TestCase):
    """A guardian fork writes its first review turn inside the `rollout-<n>` wrapper that replays its parent's history; a record of
    its own thread there proves the wrapper holds the fork's own turn, a record of another thread (or none) proves nothing."""

    PARENT = "33333333-3333-4333-8333-333333333333"
    SIBLING = "44444444-4444-4444-8444-444444444444"

    def _fork(self, records):
        return _parse(records, meta=_meta(session_id=self.PARENT, parent_thread_id=self.PARENT, source={"subagent": {"other": "guardian"}}))

    def _wrapper(self, second, turn_id="rollout-3"):
        return _event(second, {"type": "task_started", "turn_id": turn_id, "started_at": 1})

    def test_a_replay_wrapper_holding_a_record_of_the_forks_own_thread_counts_its_prompt_reply_and_request(self):
        records = [_count(2, _usage(5000, output=500)), self._wrapper(3), _user(4, "first review"), _answer(5, "allow"),
                   _recorded(6, _usage(100, cached=40, output=10), thread_id=THREAD), _started(7, "turn-2"), _user(8, "second review"),
                   _recorded(9, _usage(200, output=20), thread_id=THREAD)]
        rollout = self._fork(records)
        self.assertEqual([("first review", "allow", [(60, 10, 40, 0)]), ("second review", None, [(200, 20, 0, 0)])],
                         [(p.text, p.reply_text, [_shape(r) for r in p.requests]) for p in rollout.prompts])
        self.assertEqual([(60, 10, 40, 0), (200, 20, 0, 0)], [_shape(request) for request in rollout.requests])

    def test_a_replay_wrapper_with_only_records_of_another_thread_or_without_a_thread_counts_nothing(self):
        for foreign in ({"thread_id": "33333333-3333-4333-8333-333333333333"}, {}):
            with self.subTest(record=foreign):
                usage = _usage(900, output=90)
                record = {"timestamp": STAMP % 6, "type": "token_usage_record", "payload": {**foreign, "usage": usage}}
                records = [self._wrapper(3), _user(4, "parent asked"), _answer(5, "parent answered"), record, _started(7, "turn-2"),
                           _user(8, "own"), _recorded(9, _usage(7, output=1), thread_id=THREAD)]
                rollout = self._fork(records)
                self.assertEqual([("own", [(7, 1, 0, 0)])], [(p.text, [_shape(r) for r in p.requests]) for p in rollout.prompts])
                self.assertEqual([(7, 1, 0, 0)], [_shape(request) for request in rollout.requests])

    def test_only_the_wrapper_with_the_own_record_gives_its_messages_and_an_earlier_wrapper_stays_replay(self):
        records = [self._wrapper(2, "rollout-1"), _user(3, "old parent ask"), _answer(4, "old parent reply"), _event(5, {"type": "task_complete"}),
                   self._wrapper(6, "rollout-3"), _user(7, "own review"), _recorded(8, _usage(50, output=5), thread_id=THREAD)]
        self.assertEqual(["own review"], [prompt.text for prompt in self._fork(records).prompts])

    def test_a_record_of_the_own_thread_outside_any_replay_is_counted_as_before(self):
        rollout = self._fork([_started(2, "turn-1"), _user(3, "ask"), _recorded(4, _usage(8, output=2), thread_id=THREAD),
                              _recorded(5, _usage(9, output=3), thread_id=self.SIBLING)])
        self.assertEqual([(8, 2, 0, 0), (9, 3, 0, 0)], [_shape(request) for request in rollout.requests])

    def test_a_rollout_that_is_no_fork_never_replays_so_its_wrapper_turn_is_its_own(self):
        rollout = _parse([self._wrapper(2), _user(3, "ask"), _recorded(4, _usage(8, output=2), thread_id=THREAD)])
        self.assertEqual((["ask"], [(8, 2, 0, 0)]), ([p.text for p in rollout.prompts], [_shape(r) for r in rollout.requests]))


class SafetyTest(unittest.TestCase):
    def test_a_deeply_nested_line_is_skipped_not_raised(self):
        deep = "[" * 100000 + "]" * 100000 + "\n"
        rollout = parse_rollout([_line(_meta()), deep, _line(_user(2))])
        self.assertEqual(1, len(rollout.prompts))

    def test_a_very_long_line_is_skipped_when_it_is_not_json_and_read_when_it_is(self):
        long_text = "x" * 5_000_000
        rollout = parse_rollout([_line(_meta()), long_text + "\n", _line(_user(2, long_text))])
        self.assertEqual([len(long_text)], [len(prompt.text) for prompt in rollout.prompts])


if __name__ == "__main__":
    unittest.main()
