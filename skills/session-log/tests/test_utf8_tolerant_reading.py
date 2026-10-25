"""A line of a JSON-lines session file that is not UTF-8 (a write cut inside a multi-byte character, a damaged byte) is skipped like
a torn line is: the other lines still give the session, its sub-agents and its totals; a file none of whose lines can be decoded is
unreadable as a file without a record is."""
import gzip
import os
import unittest

from sessionlog.model import Harness
from sessionlog.reader_port import CANNOT_BE_PARSED_REASON, UnreadableSource
from tests.omp_records import FIXTURE_ROOT_ID, FIXTURE_ROOT_STEM
from tests.omp_records import ROOT_FIXTURE as OMP_ROOT_FIXTURE
from tests.test_claude_reader import FIXTURE as CLAUDE_FIXTURE
from tests.test_claude_reader import SID
from tests.test_claude_subagents import ClaudeSubAgentTestCase, _run
from tests.test_codex_reader import ROOT_FIXTURE as CODEX_ROOT_FIXTURE
from tests.test_codex_reader import ROOT_ID as CODEX_ROOT_ID
from tests.test_codex_reader import ROOT_NAME, CodexReaderTestCase
from tests.test_omp_reader import OmpReaderTestCase

try:
    from compression import zstd
except ImportError:
    zstd = None

TORN_IN_A_CHARACTER = b'{"type": "user", "text": "caf\xc3\n'  # a write cut after the first byte of "é"
DAMAGED_BYTE = b'{"type": "user", "text": "a\xffb"}\n'  # a whole JSON object with one byte that is no UTF-8
CUT_TAIL = b'{"type": "user", "text": "caf\xc3'  # the file ends inside a character, with no line end
ONLY_BAD_BYTES = b"\xff\xfe\n"


def _bytes_of(path) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def _damaged(data: bytes) -> bytes:
    """`data` with a torn line after its first line, a damaged line in the middle and a cut tail at the end."""
    lines = data.splitlines(keepends=True)
    middle = len(lines) // 2
    return lines[0] + TORN_IN_A_CHARACTER + b"".join(lines[1:middle]) + DAMAGED_BYTE + b"".join(lines[middle:]) + CUT_TAIL


def _totals(session) -> list:
    return [request.output_tokens for prompt in session.prompts for request in prompt.requests]


class ClaudeUtf8Test(ClaudeSubAgentTestCase):
    def _install_bytes(self, data):
        path = os.path.join(self.folder, SID + ".jsonl")
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def test_claude_bad_lines_are_skipped_and_the_session_and_its_totals_are_those_of_the_file_without_them(self):
        clean = _bytes_of(CLAUDE_FIXTURE)
        self._install_bytes(clean)
        expected = self._session().session
        self.assertTrue(_totals(expected))
        self._install_bytes(_damaged(clean))
        result = self._session()
        self.assertEqual((expected, ()), (result.session, result.unreadable))

    def test_claude_subagent_files_are_read_whatever_a_main_file_line_is(self):
        main = self._main()
        self._agent("a1", _run("a1", 10, 11, output_tokens=7), {"agentType": "Explore"})
        expected = self._session().session
        self._install_bytes(_damaged(_bytes_of(main)))
        result = self._session()
        self.assertEqual((expected, ()), (result.session, result.unreadable))
        self.assertEqual(1, len(result.session.sub_agents))

    def test_claude_a_bad_line_in_a_subagent_file_leaves_the_main_file_and_the_other_agents_alone(self):
        self._main()
        self._agent("a1", _run("a1", 10, 11), {"agentType": "Explore"})
        bad = self._agent("a2", _run("a2", 12, 13, output_tokens=5))
        data = _bytes_of(bad)
        with open(bad, "wb") as handle:
            handle.write(TORN_IN_A_CHARACTER + data + CUT_TAIL)
        result = self._session()
        self.assertEqual((["agent-a1", "agent-a2"], ()), ([agent.agent_id for agent in result.session.sub_agents], result.unreadable))
        self.assertEqual([5], [request.output_tokens for request in result.session.sub_agents[1].requests])

    def test_claude_a_file_none_of_whose_lines_can_be_decoded_is_unreadable(self):
        path = self._install_bytes(ONLY_BAD_BYTES)
        result = self._session()
        self.assertEqual((None, (UnreadableSource(Harness.CLAUDE, path, CANNOT_BE_PARSED_REASON),)), (result.session, result.unreadable))


class CodexUtf8Test(CodexReaderTestCase):
    def _read_bytes(self, data):
        self._write_bytes(ROOT_NAME, data)
        return self.reader.read("codex:" + CODEX_ROOT_ID, (self._unit(),))

    def test_codex_bad_lines_are_skipped_and_the_session_and_totals_are_those_of_the_file_without_them(self):
        clean = _bytes_of(CODEX_ROOT_FIXTURE)
        expected = self._read_bytes(clean)
        self.assertTrue(_totals(expected.session))
        result = self._read_bytes(_damaged(clean))
        self.assertEqual((expected.session, ()), (result.session, result.unreadable))

    @unittest.skipIf(zstd is None, "needs compression.zstd")
    def test_codex_bad_lines_of_a_compressed_rollout_are_skipped_too(self):
        clean = _bytes_of(CODEX_ROOT_FIXTURE)
        expected = self._read_bytes(clean)
        os.remove(self._unit().key)
        with open(os.path.join(self.day, ROOT_NAME + ".zst"), "wb") as handle:
            handle.write(zstd.compress(_damaged(clean)))
        result = self.reader.read("codex:" + CODEX_ROOT_ID, (self._unit(),))
        self.assertEqual((expected.session, ()), (result.session, result.unreadable))

    def test_codex_a_rollout_whose_header_is_the_only_decodable_line_gives_its_session(self):
        header = _bytes_of(CODEX_ROOT_FIXTURE).splitlines(keepends=True)[0]
        result = self._read_bytes(header + ONLY_BAD_BYTES + CUT_TAIL)
        self.assertEqual((CODEX_ROOT_ID, ()), (result.session.session_id, result.unreadable))

    def test_codex_a_rollout_none_of_whose_lines_can_be_decoded_is_unreadable(self):
        path = self._write_bytes(ROOT_NAME, ONLY_BAD_BYTES)
        result = self.reader.read("codex:" + CODEX_ROOT_ID, (self._unit(),))
        self.assertEqual((None, (UnreadableSource(Harness.CODEX, path, CANNOT_BE_PARSED_REASON),)), (result.session, result.unreadable))


class OmpUtf8Test(OmpReaderTestCase):
    def _read_bytes(self, data, archived=False):
        path = self._store_bytes(FIXTURE_ROOT_STEM, data=gzip.compress(data, mtime=0) if archived else data, archived=archived)
        return path, self._read_session(FIXTURE_ROOT_ID, path)

    def test_omp_bad_lines_are_skipped_and_the_session_and_totals_are_those_of_the_file_without_them(self):
        clean = _bytes_of(OMP_ROOT_FIXTURE)
        for archived in (False, True):
            with self.subTest(archived=archived):
                _, expected = self._read_bytes(clean, archived)
                self.assertTrue(_totals(expected.session))
                _, result = self._read_bytes(_damaged(clean), archived)
                self.assertEqual((expected.session, ()), (result.session, result.unreadable))

    def test_omp_a_file_none_of_whose_lines_can_be_decoded_is_unreadable(self):
        for archived in (False, True):
            with self.subTest(archived=archived):
                path, result = self._read_bytes(ONLY_BAD_BYTES, archived)
                self.assertEqual((None, (UnreadableSource(Harness.OMP, path, CANNOT_BE_PARSED_REASON),)), (result.session, result.unreadable))


if __name__ == "__main__":
    unittest.main()
