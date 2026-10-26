"""ClaudeReader sub-agents (BE-12, S6): scan, session of a sub-agent file, meta, nesting, messages counted once, unreadable files."""
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from sessionlog.claude_reader import ClaudeReader
from sessionlog.claude_subagents import ReadSubAgent, SubAgentMeta, nest_sub_agents, parse_meta
from sessionlog.claude_transcript import MessageRequest
from sessionlog.log_usage_writer import LogUsageWriter
from sessionlog.model import Harness, RequestUsage
from sessionlog.pricing import PricingRules, Rates
from sessionlog.reader_port import (CANNOT_BE_LISTED_REASON, CANNOT_BE_OPENED_REASON, CANNOT_BE_PARSED_REASON, UNSAFE_ID_REASON,
                                    SourceUnit, UnreadableSource)
from tests.local_zone import local_zone
from tests.test_claude_reader import (ONE_SECOND_NS, PROJECT, SID, STAMP, ClaudeReaderTestCase, _FakeEntry, _answer, _at,
                                      _on_model, _plain_user, _typed, _usage)

TOOL_CALL = {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "ls"}}


def _agent_user(second, agent, **extra) -> dict:
    record = {"type": "user", "isSidechain": True, "agentId": agent, "cwd": "/work/proj",
              "message": {"role": "user", "content": "do the task"}}
    if second is not None:
        record["timestamp"] = STAMP % second
    return {**record, **extra}


def _run(agent, start, end, output_tokens=1) -> list:
    """The records of a sub-agent that starts at `start` and answers once at `end`."""
    return [_agent_user(start, agent), _answer(end, "msg_" + agent, _usage(output_tokens))]


def _outputs(agent) -> list:
    return [request.output_tokens for request in agent.requests]


class ClaudeSubAgentTestCase(ClaudeReaderTestCase):
    def _main(self, records=None, sid=SID, folder=None):
        records = [_typed(1), _answer(2, "main1", _usage(3), "done")] if records is None else records
        return self._write(sid, "".join(json.dumps(record) + "\n" for record in records), folder)

    def _agent(self, name, records, meta=None, sid=SID, folder=None, where=()):
        """`agent-<name>.jsonl` below `<folder>/<sid>/subagents/<where>`; `meta` is a dict or raw text, None writes no meta file."""
        directory = os.path.join(folder or self.folder, sid, "subagents", *where)
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "agent-%s.jsonl" % name)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.writelines(json.dumps(record) + "\n" for record in records)
        if meta is not None:
            with open(path[: -len(".jsonl")] + ".meta.json", "w", encoding="utf-8") as handle:
                handle.write(meta if isinstance(meta, str) else json.dumps(meta))
        return path

    def _session(self, sid=SID):
        return self.reader.read("claude:" + sid, self.reader.scan().units)

    def _agents(self, sid=SID):
        return self._session(sid).session.sub_agents

    def _opening_guarded(self, path, error=None):
        """Patches the reader's `open`: opening `path` raises `error`, or fails the test when `error` is None."""
        real_open = open

        def guarded(name, *args, **kwargs):
            if name == path:
                raise error or AssertionError("%s must not be opened" % path)
            return real_open(name, *args, **kwargs)

        return mock.patch("sessionlog.claude_reader.open", guarded, create=True)


class ClaudeSubAgentScanTest(ClaudeSubAgentTestCase):
    def test_claude_scan_lists_every_subagent_file_nested_or_workflow_as_a_unit_of_its_session(self):
        main = self._main()
        nested = self._agent("a1", _run("a1", 10, 11), {"agentType": "general-purpose"})
        workflow = self._agent("w1", _run("w1", 20, 21), {"agentType": "workflow-step"}, where=("workflows", "wf_x"))
        deeper = self._agent("w2", _run("w2", 22, 23), where=("workflows", "wf_x", "inner"))
        units = self.reader.scan().units
        self.assertEqual([unit.key for unit in units], sorted([main, nested, workflow, deeper]))
        self.assertEqual({self.reader.session_of(unit) for unit in units}, {"claude:" + SID})

    def test_claude_scan_ignores_meta_files_and_files_that_are_not_agent_transcripts(self):
        main = self._main()
        agent = self._agent("a1", _run("a1", 10, 11), {"agentType": "x"})
        directory = os.path.dirname(agent)
        for name in ("notes.jsonl", "agent-a1.txt", "agent-.json", "agent-a9.jsonl.bak"):
            with open(os.path.join(directory, name), "w") as handle:
                handle.write("{}\n")
        os.mkdir(os.path.join(directory, "agent-dir.jsonl"))
        other = os.path.join(self.folder, SID, "tool-results")
        os.makedirs(other)
        with open(os.path.join(other, "agent-z.jsonl"), "w") as handle:
            handle.write("{}\n")
        self.assertEqual([unit.key for unit in self.reader.scan().units], sorted([main, agent]))

    def test_claude_scan_subagent_fingerprint_and_modified_at_follow_its_own_file(self):
        self._main()
        path = self._agent("a1", _run("a1", 10, 11))
        os.utime(path, ns=(0, 1_700_000_000 * ONE_SECOND_NS))
        unit = next(unit for unit in self.reader.scan().units if unit.key == path)
        self.assertEqual(unit.modified_at, datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc))
        with open(path, "a") as handle:
            handle.write("\n")
        os.utime(path, ns=(0, 1_700_000_000 * ONE_SECOND_NS))
        grown = next(unit for unit in self.reader.scan().units if unit.key == path)
        self.assertNotEqual(unit.fingerprint, grown.fingerprint)

    def test_claude_scan_subagent_fingerprint_changes_when_its_meta_appears_or_changes(self):
        self._main()
        path = self._agent("a1", _run("a1", 10, 11))
        meta = path[: -len(".jsonl")] + ".meta.json"
        seen = []
        for text in (None, '{"agentType": "x"}', '{"agentType": "yy"}'):
            if text is not None:
                with open(meta, "w") as handle:
                    handle.write(text)
            seen.append(next(unit.fingerprint for unit in self.reader.scan().units if unit.key == path))
        self.assertEqual(len(set(seen)), 3)
        self.assertEqual(seen[2], next(unit.fingerprint for unit in self.reader.scan().units if unit.key == path))

    def test_claude_scan_does_not_follow_symlinked_directories_below_the_project(self):
        self._main()
        agent = self._agent("a1", _run("a1", 10, 11))
        other = os.path.join(self.folder, "elsewhere", "subagents")
        os.makedirs(other)
        with open(os.path.join(other, "agent-x.jsonl"), "w") as handle:
            handle.write("{}\n")
        os.makedirs(os.path.join(self.folder, "second-session"))
        try:
            os.symlink(os.path.dirname(agent), os.path.join(os.path.dirname(agent), "loop"), target_is_directory=True)
            os.symlink(os.path.join(self.folder, SID), os.path.join(self.folder, "linked-session"), target_is_directory=True)
            os.symlink(other, os.path.join(self.folder, "second-session", "subagents"), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        keys = [unit.key for unit in self.reader.scan().units]
        self.assertEqual([os.path.basename(key) for key in keys if key.startswith(os.path.join(self.folder, SID))], [SID + ".jsonl", "agent-a1.jsonl"])
        self.assertEqual([os.path.basename(key) for key in keys], [SID + ".jsonl", "agent-a1.jsonl", "agent-x.jsonl"])
        self.assertTrue(all(os.sep + "second-session" + os.sep not in key and "linked-session" not in key for key in keys))

    def test_claude_scan_subagent_folder_that_vanishes_is_skipped_and_one_that_cannot_be_listed_is_unreadable(self):
        main = self._main()
        agent = self._agent("a1", _run("a1", 10, 11))
        workflow = self._agent("w1", _run("w1", 20, 21), where=("workflows",))
        subagents = os.path.join(self.folder, SID, "subagents")
        for error, expected in ((FileNotFoundError(), ()), (PermissionError(), (UnreadableSource(Harness.CLAUDE, subagents, CANNOT_BE_LISTED_REASON),))):
            with self.subTest(error=type(error).__name__), self._scandir_wrapping(fail={subagents: error}):
                result = self.reader.scan()
                self.assertEqual([unit.key for unit in result.units], [main])
                self.assertEqual(result.unreadable, expected)
        inner = os.path.dirname(workflow)
        with self._scandir_wrapping(fail={inner: PermissionError()}):
            result = self.reader.scan()
        self.assertEqual([unit.key for unit in result.units], sorted([main, agent]))
        self.assertEqual(result.unreadable, (UnreadableSource(Harness.CLAUDE, inner, CANNOT_BE_LISTED_REASON),))

    def test_claude_scan_subagent_file_that_cannot_be_examined_is_unreadable(self):
        self._main()
        directory = os.path.dirname(self._agent("a1", _run("a1", 10, 11)))
        locked = _FakeEntry(os.path.join(directory, "agent-locked.jsonl"), PermissionError())
        with self._scandir_wrapping({directory: [locked]}):
            result = self.reader.scan()
        self.assertEqual(len(result.units), 2)
        self.assertEqual(result.unreadable, (UnreadableSource(Harness.CLAUDE, locked.path, CANNOT_BE_LISTED_REASON),))

    def test_claude_scan_session_folder_or_subagent_subfolder_that_cannot_be_examined_is_unreadable_and_one_that_vanished_skipped(self):
        self._main()
        directory = os.path.dirname(self._agent("a1", _run("a1", 10, 11)))
        session_folder = _FakeEntry(os.path.join(self.folder, "locked-session"), PermissionError())
        subfolder = _FakeEntry(os.path.join(directory, "locked-folder"), OSError("I/O error"))
        gone = _FakeEntry(os.path.join(directory, "gone-folder"), FileNotFoundError())
        with self._scandir_wrapping({self.folder: [session_folder], directory: [subfolder, gone]}):
            result = self.reader.scan()
        self.assertEqual(len(result.units), 2)
        self.assertEqual(result.unreadable, (UnreadableSource(Harness.CLAUDE, subfolder.path, CANNOT_BE_LISTED_REASON),
                                             UnreadableSource(Harness.CLAUDE, session_folder.path, CANNOT_BE_LISTED_REASON)))

    def test_claude_scan_does_not_walk_a_junction_below_the_subagents_folder(self):
        self._main()
        directory = os.path.dirname(self._agent("a1", _run("a1", 10, 11)))
        junction = _FakeEntry(os.path.join(directory, "junction"), AssertionError("a junction must not be examined further"))
        junction.is_dir = lambda *, follow_symlinks=True: not follow_symlinks
        junction.is_junction = lambda: True
        with self._scandir_wrapping({directory: [junction]}, fail={junction.path: AssertionError("a junction must not be listed")}):
            result = self.reader.scan()
        self.assertEqual((len(result.units), result.unreadable), (2, ()))

    def test_claude_scan_does_not_walk_a_subagents_folder_that_is_a_junction(self):
        main = self._main()
        self._agent("a1", _run("a1", 10, 11))
        subagents = os.path.join(self.folder, SID, "subagents")
        with mock.patch("os.path.isjunction", side_effect=lambda path: path == subagents):
            result = self.reader.scan()
        self.assertEqual(([unit.key for unit in result.units], result.unreadable), ([main], ()))


class ClaudeSubAgentSessionOfTest(ClaudeSubAgentTestCase):
    def test_claude_session_of_subagent_uses_the_folder_below_the_projects_dir_even_when_the_root_is_called_subagents(self):
        root = os.path.join(self.root, "subagents", ".claude")
        folder = os.path.join(root, "projects", PROJECT)
        os.makedirs(os.path.join(folder, SID, "subagents"))
        for path in (os.path.join(folder, SID + ".jsonl"), os.path.join(folder, "agent-top.jsonl"),
                     os.path.join(folder, SID, "subagents", "agent-a1.jsonl")):
            with open(path, "w") as handle:
                handle.write("{}\n")
        reader = ClaudeReader(root)
        self.assertEqual({os.path.basename(unit.key): reader.session_of(unit) for unit in reader.scan().units},
                         {SID + ".jsonl": "claude:" + SID, "agent-top.jsonl": "claude:agent-top", "agent-a1.jsonl": "claude:" + SID})

    def test_claude_session_of_key_outside_the_projects_dir_is_a_plain_file_whatever_its_path_looks_like(self):
        key = os.path.join(self.root, "elsewhere", "subagents", "a", "agent-f.jsonl")
        unit = SourceUnit(key, "0:0", _at(1))
        self.assertEqual(self.reader.session_of(unit), "claude:agent-f")
        for error in (ValueError("other drive"), FileNotFoundError("cwd gone")):
            with self.subTest(error=type(error).__name__), mock.patch("sessionlog.claude_reader.os.path.relpath", side_effect=error):
                self.assertEqual(self.reader.session_of(unit), "claude:agent-f")


class ClaudeSubAgentReadTest(ClaudeSubAgentTestCase):
    def test_nested_and_workflow_subagents_attach_to_session_with_type_from_meta(self):
        self._main()
        self._agent("a1", [_agent_user(10, "a1"), _answer(11, "ms1", _usage(5)), _answer(12, "ms1", _usage(9))],
                    {"agentType": "general-purpose", "spawnDepth": 0})
        self._agent("a2", _run("a2", 20, 20, 2), {"agentType": "Explore", "parentAgentId": "a1", "spawnDepth": 1})
        self._agent("a3", _run("a3", 30, 31, 3), {"agentType": "general-purpose", "parentAgentId": "a2", "spawnDepth": 2})
        self._agent("w1", _run("w1", 40, 45, 4), {"agentType": "workflow-step", "spawnDepth": 1}, where=("workflows", "wf_x"))
        result = self._session()
        session = result.session
        self.assertEqual(result.unreadable, ())
        self.assertEqual([prompt.text for prompt in session.prompts], ["ask"])
        first, workflow = session.sub_agents
        self.assertEqual((first.agent_id, first.type, first.started_at, first.working_time), ("agent-a1", "general-purpose", _at(10), timedelta(seconds=2)))
        self.assertEqual(_outputs(first), [9])
        (second,) = first.sub_agents
        self.assertEqual((second.agent_id, second.type, second.started_at, second.working_time), ("agent-a2", "Explore", _at(20), timedelta(0)))
        self.assertEqual(_outputs(second), [2])
        (third,) = second.sub_agents
        self.assertEqual((third.agent_id, third.type, third.started_at, third.sub_agents), ("agent-a3", "general-purpose", _at(30), ()))
        self.assertEqual(_outputs(third), [3])
        self.assertEqual((workflow.agent_id, workflow.type, workflow.started_at, workflow.working_time, workflow.sub_agents),
                         ("agent-w1", "workflow-step", _at(40), timedelta(seconds=5), ()))
        self.assertEqual(_outputs(workflow), [4])
        self.assertFalse(any(agent.tokens_not_recorded for agent in (first, second, third, workflow)))
        self.assertEqual(session.last_activity_at, _at(45))

    def test_claude_subagents_are_ordered_by_start_then_id_whatever_the_file_order(self):
        self._main()
        for name, start in (("c", 10), ("a", 20), ("b", 10)):
            self._agent(name, _run(name, start, start + 1))
        self.assertEqual([agent.agent_id for agent in self._agents()], ["agent-b", "agent-c", "agent-a"])

    def test_claude_subagent_type_is_unknown_and_parent_ignored_when_meta_is_missing_or_unusable(self):
        self._main()
        unusable = (None, "{", "[]", "null", "[" * 100000, '{"agentType": "x", "parentAgentId": "a0"', b"\xff\xfe", b'{"agentType": "x", "parentAgentId": "a0"}\xff')
        for meta in unusable:
            with self.subTest(meta=meta if meta is None or len(meta) < 80 else "long"):
                self._agent("a0", _run("a0", 5, 6))
                path = self._agent("a1", _run("a1", 10, 11), meta if not isinstance(meta, bytes) else None)
                if isinstance(meta, bytes):
                    with open(path[: -len(".jsonl")] + ".meta.json", "wb") as handle:
                        handle.write(meta)
                top = {agent.agent_id: agent for agent in self._agents()}
                self.assertEqual(sorted(top), ["agent-a0", "agent-a1"])
                self.assertEqual(top["agent-a1"].type, "unknown")

    def test_claude_subagent_type_and_parent_of_a_meta_are_used_independently(self):
        self._main()
        self._agent("a0", _run("a0", 5, 6), {"agentType": "x"})
        for meta in ('{"agentType": 5, "parentAgentId": "a0"}', '{"agentType": "", "parentAgentId": "a0"}'):
            with self.subTest(meta=meta):
                self._agent("a1", _run("a1", 10, 11), meta)
                (parent,) = self._agents()
                (child,) = parent.sub_agents
                self.assertEqual((child.agent_id, child.type), ("agent-a1", "unknown"))

    def test_claude_subagent_meta_with_wrongly_typed_parent_gives_a_top_level_agent_keeping_its_type(self):
        self._main()
        self._agent("a1", _run("a1", 10, 11), {"agentType": "Explore", "parentAgentId": 7})
        (agent,) = self._agents()
        self.assertEqual((agent.type, agent.sub_agents), ("Explore", ()))

    def test_claude_subagent_meta_is_read_with_a_byte_order_mark(self):
        self._main()
        self._agent("a1", _run("a1", 10, 11), "\ufeff" + json.dumps({"agentType": "Explore"}))
        self.assertEqual(self._agents()[0].type, "Explore")

    def test_claude_subagent_meta_that_cannot_be_opened_gives_unknown(self):
        self._main()
        path = self._agent("a1", _run("a1", 10, 11), {"agentType": "Explore"})
        with self._opening_guarded(path[: -len(".jsonl")] + ".meta.json", PermissionError()):
            (agent,) = self._agents()
        self.assertEqual(agent.type, "unknown")

    def test_claude_subagent_meta_that_is_not_a_regular_file_is_never_opened_and_gives_unknown(self):
        self._main()
        path = self._agent("a1", _run("a1", 10, 11))
        meta = path[: -len(".jsonl")] + ".meta.json"
        os.mkdir(meta)
        with self._opening_guarded(meta):
            self.assertEqual(self._agents()[0].type, "unknown")
        os.rmdir(meta)
        if hasattr(os, "mkfifo"):
            os.mkfifo(meta)
            with self._opening_guarded(meta):
                self.assertEqual(self._agents()[0].type, "unknown")

    def test_claude_subagent_with_unknown_or_own_parent_sits_at_top_level(self):
        self._main()
        self._agent("a1", _run("a1", 10, 11), {"agentType": "x", "parentAgentId": "gone"})
        self._agent("a2", _run("a2", 12, 13), {"agentType": "x", "parentAgentId": "a2"})
        self.assertEqual([(agent.agent_id, agent.sub_agents) for agent in self._agents()], [("agent-a1", ()), ("agent-a2", ())])

    def test_claude_subagent_parent_cycle_is_flattened_to_top_level_and_keeps_every_agent(self):
        self._main()
        self._agent("a", _run("a", 10, 11), {"agentType": "x", "parentAgentId": "b"})
        self._agent("b", _run("b", 12, 13), {"agentType": "x", "parentAgentId": "a"})
        self._agent("c", _run("c", 14, 15), {"agentType": "x", "parentAgentId": "a"})
        top = self._agents()
        self.assertEqual([agent.agent_id for agent in top], ["agent-a", "agent-b"])
        self.assertEqual([child.agent_id for child in top[0].sub_agents], ["agent-c"])
        self.assertEqual(top[1].sub_agents, ())

    def test_claude_subagent_parent_may_sit_in_a_workflow_folder(self):
        self._main()
        self._agent("w1", _run("w1", 10, 11), {"agentType": "x"}, where=("workflows", "wf_x"))
        self._agent("a1", _run("a1", 12, 13), {"agentType": "y", "parentAgentId": "w1"})
        (parent,) = self._agents()
        self.assertEqual((parent.agent_id, [child.agent_id for child in parent.sub_agents]), ("agent-w1", ["agent-a1"]))

    def test_claude_subagent_children_of_a_parent_without_any_time_or_request_sit_at_top_level(self):
        self._main()
        self._agent("a0", [])
        self._agent("a1", _run("a1", 10, 11), {"agentType": "x", "parentAgentId": "a0"})
        self.assertEqual([(agent.agent_id, agent.sub_agents) for agent in self._agents()], [("agent-a1", ())])

    def test_claude_subagent_files_with_one_agent_id_in_different_folders_both_keep_a_row_and_children_follow_the_first(self):
        self._main()
        self._agent("x", _run("x", 10, 11), {"agentType": "first"})
        self._agent("x", _run("x2", 12, 13), {"agentType": "second"}, where=("workflows", "wf"))
        self._agent("kid", _run("kid", 14, 15), {"agentType": "k", "parentAgentId": "x"})
        top = self._agents()
        self.assertEqual([(agent.agent_id, agent.type, [child.agent_id for child in agent.sub_agents]) for agent in top],
                         [("agent-x", "first", ["agent-kid"]), ("agent-x", "second", [])])

    def test_claude_subagent_start_is_earliest_record_of_any_type_and_working_time_ends_at_last_assistant_output(self):
        self._main()
        records = [_answer(14, "m2", _usage(2)), {"type": "system", "timestamp": STAMP % 9}, _agent_user(10, "a1"),
                   _answer(12, "m1", _usage(1)), _plain_user(30, "late local command"), {"type": "system", "timestamp": STAMP % 40}]
        self._agent("a1", records)
        (agent,) = self._agents()
        self.assertEqual((agent.started_at, agent.working_time), (_at(9), timedelta(seconds=5)))
        self.assertEqual(_outputs(agent), [2, 1])

    def test_claude_subagent_counts_every_assistant_entry_streamed_copies_once_and_synthetic_never(self):
        self._main()
        records = [_agent_user(10, "a1"), _answer(11, "m1", _usage(5)), _answer(12, "m1", _usage(9)),
                   _on_model(_answer(20, "m2", _usage(100)), "<synthetic>"), _answer(14, "m3", _usage(2)),
                   _answer(15, "m4", None, "no usage")]
        self._agent("a1", records)
        (agent,) = self._agents()
        self.assertEqual(_outputs(agent), [9, 2])
        self.assertEqual(agent.working_time, timedelta(seconds=5))

    def test_claude_subagent_interleaved_copies_keep_the_order_of_their_first_appearance_and_ids_fall_back_to_request_id_then_line(self):
        self._main()
        no_id = {"type": "assistant", "timestamp": STAMP % 14, "message": {"model": "claude-x", "usage": _usage(7)}}
        by_request = {**no_id, "timestamp": STAMP % 15, "requestId": "req_1", "message": {"model": "claude-x", "usage": _usage(8)}}
        records = [_agent_user(10, "a1"), _answer(11, "m1", _usage(1)), _answer(12, "m2", _usage(2)), _answer(13, "m1", _usage(3)),
                   no_id, by_request, {**by_request, "timestamp": STAMP % 16, "message": {"model": "claude-x", "usage": _usage(9)}}]
        self._agent("a1", records)
        (agent,) = self._agents()
        self.assertEqual(_outputs(agent), [3, 2, 7, 9])

    def test_claude_subagent_without_assistant_output_has_zero_working_time_and_no_requests(self):
        self._main()
        self._agent("a1", [_agent_user(10, "a1")])
        (agent,) = self._agents()
        self.assertEqual((agent.started_at, agent.working_time, agent.requests), (_at(10), timedelta(0), ()))

    def test_claude_subagent_start_is_in_utc_so_that_its_activity_end_never_overflows(self):
        self._main()
        last = {**_answer(None, "m1", _usage(1)), "timestamp": "9999-12-31T23:59:59+00:00"}
        self._agent("a1", [{**_agent_user(None, "a1"), "timestamp": "9999-12-31T10:00:00+05:00"}, last])
        with local_zone("UTC"):
            (agent,) = self._agents()
        self.assertEqual(agent.started_at.utcoffset(), timedelta(0))
        self.assertEqual(agent.last_activity_at, datetime(9999, 12, 31, 23, 59, 59, tzinfo=timezone.utc))

    def test_claude_subagent_skips_bad_lines_and_keeps_the_next_record(self):
        self._main()
        path = self._agent("a1", [_agent_user(10, "a1")])
        with open(path, "a", encoding="utf-8") as handle:
            handle.write('{"type": "assistant", "timestamp": "2026-08-28T10:00:11.000Z", "message": {"id": "torn\n')
            handle.write("[" * 100000 + "\n\n")
            handle.write(json.dumps(_answer(12, "m1", _usage(4))) + "\n")
        (agent,) = self._agents()
        self.assertEqual((_outputs(agent), agent.working_time), ([4], timedelta(seconds=2)))

    def test_claude_subagent_without_any_time_starts_when_its_file_last_changed_if_it_has_requests_and_else_gets_no_row(self):
        self._main()
        untimed = self._agent("a1", [_agent_user(None, "a1"), _answer(None, "m1", _usage(6))])
        self._agent("a2", [_agent_user(None, "a2")])
        self._agent("a3", [])
        os.utime(untimed, ns=(0, 1_700_000_000 * ONE_SECOND_NS))
        result = self._session()
        (agent,) = result.session.sub_agents
        self.assertEqual((agent.agent_id, agent.started_at, agent.working_time, _outputs(agent)),
                         ("agent-a1", datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc), timedelta(0), [6]))
        self.assertEqual(result.unreadable, ())

    def test_claude_subagent_id_is_the_file_stem_and_is_never_checked_against_the_id_rule(self):
        self._main()
        self._agent("..x", _run("x", 10, 11), {"agentType": "Explore"})
        (agent,) = self._agents()
        self.assertEqual((agent.agent_id, _outputs(agent)), ("agent-..x", [1]))

    def test_claude_subagent_file_that_cannot_be_read_is_unreadable_and_the_session_keeps_the_others(self):
        self._main()
        good = self._agent("a1", _run("a1", 10, 11), {"agentType": "Explore"})
        undecodable = self._agent("a2", [])
        with open(undecodable, "wb") as handle:
            handle.write(b"\xff\xfe\n")
        unparsable = self._agent("a3", [])
        with open(unparsable, "w") as handle:
            handle.write("not json\n[1, 2]\n")
        vanished = self._agent("a4", _run("a4", 12, 13))
        units = self.reader.scan().units
        os.remove(vanished)
        result = self.reader.read("claude:" + SID, units)
        self.assertEqual([agent.agent_id for agent in result.session.sub_agents], ["agent-a1"])
        self.assertEqual(result.unreadable, (
            UnreadableSource(Harness.CLAUDE, undecodable, CANNOT_BE_PARSED_REASON),
            UnreadableSource(Harness.CLAUDE, unparsable, CANNOT_BE_PARSED_REASON),
            UnreadableSource(Harness.CLAUDE, vanished, CANNOT_BE_OPENED_REASON)))
        self.assertTrue(os.path.exists(good))

    def test_claude_read_gives_only_the_subagents_of_the_units_handed_over_and_of_the_chosen_main_file(self):
        other = self._folder("-other-proj")
        older = self._main([_typed(1, "older")])
        newer = self._main([_typed(1, "newer")], folder=other)
        os.utime(older, ns=(0, ONE_SECOND_NS))
        os.utime(newer, ns=(0, 2 * ONE_SECOND_NS))
        self._agent("mine", _run("mine", 10, 11), folder=other)
        self._agent("theirs", _run("theirs", 12, 13))
        self._agent("elsewhere", _run("elsewhere", 14, 15), sid="other-session")
        units = self.reader.scan().units
        session = self.reader.read("claude:" + SID, units).session
        self.assertEqual(([prompt.text for prompt in session.prompts], [agent.agent_id for agent in session.sub_agents]),
                         (["newer"], ["agent-mine"]))
        main_only = tuple(unit for unit in units if unit.key == newer)
        self.assertEqual(self.reader.read("claude:" + SID, main_only).session.sub_agents, ())

    def test_claude_read_without_a_main_file_gives_an_empty_result_even_with_subagent_units(self):
        self._agent("a1", _run("a1", 10, 11))
        result = self._session()
        self.assertEqual((result.session, result.unreadable), (None, ()))

    def test_claude_read_unsafe_session_id_stays_unreadable_whatever_its_subagents(self):
        path = self._main(sid="bad.id")
        self._agent("a1", _run("a1", 10, 11), sid="bad.id")
        result = self._session("bad.id")
        self.assertEqual((result.session, result.unreadable), (None, (UnreadableSource(Harness.CLAUDE, path, UNSAFE_ID_REASON),)))


class ClaudeSubAgentCountedOnceTest(ClaudeSubAgentTestCase):
    """A forked agent replays messages of its parent, so one message id sits in several files (found in real stores)."""

    def test_message_replayed_by_a_forked_agent_counts_for_the_agent_that_started_first_whatever_the_file_names(self):
        self._main()
        self._agent("z-origin", [_agent_user(10, "o"), _answer(11, "shared1", _usage(5)), _answer(12, "shared2", _usage(6))], {"agentType": "origin"})
        self._agent("a-fork", [_agent_user(20, "f"), _answer(11, "shared1", _usage(5)), _answer(12, "shared2", _usage(6)),
                               _answer(21, "own", _usage(7))], {"agentType": "fork", "isFork": True, "parentAgentId": "z-origin"})
        origin, = [agent for agent in self._agents() if agent.agent_id == "agent-z-origin"]
        (fork,) = origin.sub_agents
        self.assertEqual((_outputs(origin), _outputs(fork)), ([5, 6], [7]))

    def test_message_replayed_by_a_fork_that_starts_with_its_parent_counts_for_the_parent_not_the_smaller_agent_id(self):
        self._main()
        self._agent("z-origin", [_agent_user(11, "o"), _answer(12, "shared1", _usage(5)), _answer(13, "shared2", _usage(6))], {"agentType": "origin"})
        self._agent("a-fork", [_agent_user(11, "o"), _answer(12, "shared1", _usage(5)), _answer(13, "shared2", _usage(6)),
                               _answer(21, "own", _usage(7))], {"agentType": "fork", "parentAgentId": "z-origin"})
        origin, = [agent for agent in self._agents() if agent.agent_id == "agent-z-origin"]
        (fork,) = origin.sub_agents
        self.assertEqual((_outputs(origin), _outputs(fork)), ([5, 6], [7]))

    def test_message_in_two_agents_that_started_together_counts_for_the_smaller_agent_id(self):
        self._main()
        for name in ("b", "a"):
            self._agent(name, [_agent_user(10, name), _answer(11, "shared", _usage(5))])
        first, second = self._agents()
        self.assertEqual((first.agent_id, _outputs(first), second.agent_id, _outputs(second)), ("agent-a", [5], "agent-b", []))

    def test_message_of_the_main_session_is_not_counted_again_by_an_agent(self):
        self._main([_typed(1), _answer(2, "main1", _usage(3)), _typed(3, "again"), _answer(4, "main2", _usage(4))])
        self._agent("a1", [_agent_user(10, "a1"), _answer(11, "main2", _usage(4)), _answer(12, "own", _usage(8))])
        (agent,) = self._agents()
        self.assertEqual(_outputs(agent), [8])

    def test_message_of_a_dropped_main_prompt_is_not_main_s_so_an_agent_keeps_it(self):
        self._main([_typed(None, "untimed"), _answer(2, "ghost", _usage(3)), _typed(5, "timed")])
        self._agent("a1", [_agent_user(10, "a1"), _answer(11, "ghost", _usage(4))])
        (agent,) = self._agents()
        self.assertEqual(_outputs(agent), [4])

    def test_requests_known_only_by_their_line_are_never_the_same_request_across_files(self):
        self._main()
        no_id = {"type": "assistant", "timestamp": STAMP % 11, "message": {"model": "claude-x", "usage": _usage(5)}}
        for name in ("a1", "a2"):
            self._agent(name, [_agent_user(10, name), no_id])
        self.assertEqual([_outputs(agent) for agent in self._agents()], [[5], [5]])

    def test_agent_that_only_replayed_messages_keeps_its_row_without_requests(self):
        self._main()
        self._agent("a1", [_agent_user(10, "a1"), _answer(11, "shared", _usage(5))])
        self._agent("a2", [_agent_user(20, "a2"), _answer(11, "shared", _usage(5))])
        first, second = self._agents()
        self.assertEqual([_outputs(first), _outputs(second)], [[5], []])


def _read_agent(agent_id, parent=None, start=10, requests=(), working_time=timedelta(0), agent_type="x"):
    meta = SubAgentMeta(agent_type, parent)
    started_at = None if start is None else datetime(2026, 1, 1, 0, 0, start, tzinfo=timezone.utc)
    return ReadSubAgent(agent_id, meta, started_at, working_time, tuple(requests))


def _request(message_id, output_tokens=1):
    return MessageRequest(message_id, RequestUsage("m", 0, output_tokens, 0, 0, 0))


class NestSubAgentsTest(unittest.TestCase):
    def test_parse_meta_takes_type_and_parent_independently_and_prefixes_the_parent_like_an_agent_id(self):
        cases = {
            '{"agentType": "Explore", "parentAgentId": "a1"}': ("Explore", "agent-a1"),
            '{"agentType": "Explore"}': ("Explore", None),
            '{"parentAgentId": "a1"}': ("unknown", "agent-a1"),
            '{"agentType": "", "parentAgentId": ""}': ("unknown", None),
            '{"agentType": 1, "parentAgentId": 1}': ("unknown", None),
            "[1]": ("unknown", None), "": ("unknown", None), "[" * 100000: ("unknown", None),
        }
        for text, expected in cases.items():
            with self.subTest(text=text[:40]):
                meta = parse_meta(text)
                self.assertEqual((meta.type, meta.parent_agent_id), expected)

    def test_agents_without_a_time_have_no_row_and_cannot_be_parents(self):
        result = nest_sub_agents([_read_agent("agent-a", start=None), _read_agent("agent-b", "agent-a")])
        self.assertEqual([(agent.agent_id, agent.sub_agents) for agent in result], [("agent-b", ())])

    def test_loops_of_any_length_and_self_parents_are_flattened_to_the_top_level(self):
        agents = [_read_agent("agent-a", "agent-c", 10), _read_agent("agent-b", "agent-a", 11), _read_agent("agent-c", "agent-b", 12),
                  _read_agent("agent-d", "agent-d", 13), _read_agent("agent-e", "agent-c", 14), _read_agent("agent-f", "", 15)]
        result = nest_sub_agents(agents)
        self.assertEqual([agent.agent_id for agent in result], ["agent-a", "agent-b", "agent-c", "agent-d", "agent-f"])
        self.assertEqual([[child.agent_id for child in agent.sub_agents] for agent in result], [[], [], ["agent-e"], [], []])

    def test_deeper_chains_nest_whatever_the_input_order(self):
        chain = [_read_agent("agent-c", "agent-b", 12), _read_agent("agent-a", None, 10), _read_agent("agent-b", "agent-a", 11)]
        (top,) = nest_sub_agents(chain)
        self.assertEqual((top.agent_id, top.sub_agents[0].agent_id, top.sub_agents[0].sub_agents[0].agent_id), ("agent-a", "agent-b", "agent-c"))

    def test_messages_count_once_for_the_earliest_agent_and_never_for_ids_already_counted(self):
        agents = [_read_agent("agent-b", start=11, requests=[_request("m1", 2), _request("m2", 3), _request(None, 4)]),
                  _read_agent("agent-a", start=10, requests=[_request("m1", 1), _request("main", 9), _request(None, 5)])]
        result = {agent.agent_id: [request.output_tokens for request in agent.requests]
                  for agent in nest_sub_agents(agents, counted_message_ids={"main"})}
        self.assertEqual(result, {"agent-a": [1, 5], "agent-b": [3, 4]})


class ClaudeSubAgentOutputTest(ClaudeSubAgentTestCase):
    def test_claude_log_has_header_prompts_and_final_replies_no_tool_calls_and_no_helpers_line(self):
        self._main([
            _typed(1, "ask one"),
            _answer(3, "m1", _usage(3), blocks=[{"type": "text", "text": "let me look"}, TOOL_CALL]),
            {"type": "user", "timestamp": STAMP % 4, "message": {"role": "user", "content": [{"type": "tool_result", "content": "files"}]}},
            _answer(5, "m2", _usage(4), "final answer"),
            _typed(20, "ask two"),
        ])
        self._agent("a1", [_agent_user(10, "a1"), _answer(11, "ms1", _usage(5), blocks=[TOOL_CALL]), _answer(12, "ms1", _usage(9), "sub text")],
                    {"agentType": "general-purpose"})
        self._agent("a2", _run("a2", 13, 14, 2), {"agentType": "Explore", "parentAgentId": "a1"})
        self._agent("w1", _run("w1", 30, 31, 3), {"agentType": "workflow-step"}, where=("workflows", "wf_x"))
        session = self._session().session
        writer = LogUsageWriter("/logs", lambda moment: moment)
        log = writer.render_log(session)
        self.assertEqual(log, "\n".join([
            "# Session log", "- harness: claude", "- session: " + SID, "- project: /work/proj", "- start: 2026-08-28 10:00:01 UTC",
            "", "## 2026-08-28 10:00:01 UTC", "ask one", "", "### 2026-08-28 10:00:05 UTC response", "final answer",
            "", "## 2026-08-28 10:00:20 UTC", "ask two", ""]))
        self.assertNotIn("Bash", log)
        self.assertNotIn("sub text", log)
        report = writer.render_report(PricingRules(_Book()).build(session)).splitlines()
        for expected in ('1. 10:00:01 (working time 00:00:04) "ask one"', '2. 10:00:20 (working time 00:00:00) "ask two"',
                         "sub-agent: general-purpose (agent-a1), working time: 00:00:02",
                         "sub-agent: Explore (agent-a2), working time: 00:00:01",
                         "sub-agent: workflow-step (agent-w1), working time: 00:00:01", "TOTAL (2 requests, 3 sub-agents)"):
            self.assertIn(expected, report)
        self.assertEqual([line for line in report if line.startswith(("sub-agent: ", "est. used token: input: 1,"))], [
            "sub-agent: general-purpose (agent-a1), working time: 00:00:02",
            "est. used token: input: 1, output: 9, cache_create: 0, cache_read: 0, total_tokens: 10, price: $0.00, model: claude-x, effort: -",
            "sub-agent: Explore (agent-a2), working time: 00:00:01",
            "est. used token: input: 1, output: 2, cache_create: 0, cache_read: 0, total_tokens: 3, price: $0.00, model: claude-x, effort: -",
            "sub-agent: workflow-step (agent-w1), working time: 00:00:01",
            "est. used token: input: 1, output: 3, cache_create: 0, cache_read: 0, total_tokens: 4, price: $0.00, model: claude-x, effort: -"])
        self.assertIn("est. used token: input: 5, output: 21, cache_create: 0, cache_read: 0, total_tokens: 26,", [line[:len("est. used token: input: 5, output: 21, cache_create: 0, cache_read: 0, total_tokens: 26,")] for line in report])
        self.assertIn("working time: 00:00:04", report)
        self.assertFalse([line for line in report if "internal helpers" in line])


class _Book:
    """A price book that rates every model the same, so a report can be built."""

    def rate_for(self, model_name, fast, prompt_tokens):
        return Rates(10, 20, 1, 12.5, 20)

    def problem(self):
        return None


if __name__ == "__main__":
    unittest.main()
