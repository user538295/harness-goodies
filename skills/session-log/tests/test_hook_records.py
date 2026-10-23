"""Cursor hook records (C5, BE-30): `session_log.py hook cursor <event>` writes one record per call under
`prompt-logs/.records/<conversation_id>/`, through an exclusive temp and a rename, and `sessionStart` removes stale record temps
before it syncs. A hook never speaks and always exits 0."""
import io
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unittest
from unittest import mock

import session_log
from sessionlog import cursor_records, hook_records, temp_files
from sessionlog.cursor_reader import CursorReader
from sessionlog.state import FileStateStore
from tests.test_session_log import ENTRY, ON_FLAG, SUBPROCESS_ENV_NAMES, SUBPROCESS_TIMEOUT_SECONDS, SandboxCase

CURSOR_FOLDER = ".cursor"
LOG_FOLDER = "prompt-logs"
SYNC_STATE = ".sync.json"
RECORDS_FOLDER = ".records"
INVALID_FOLDER = ".invalid"
LEGACY_FLAG = ".enabled"
SILENT_SUCCESS = (0, "", "")
RECORD_EVENTS = ("beforeSubmitPrompt", "afterAgentResponse", "stop", "subagentStop")
CONVERSATION = "conv-1"
OTHER_CONVERSATION = "conv-2"
FIXED_PID = 4242
START_NS = 1_700_000_000_000_000_000
STEP_NS = 1_000_000
SECONDS = 10 ** 9
HOUR = 3600
OLD_AGE_SECONDS = 2 * HOUR
FRESH_AGE_SECONDS = HOUR // 2
BOM = b"\xef\xbb\xbf"
NON_ASCII_TEXT = "hall\u00f3 \u2265 \u0151 \U0001f600"
RECORD_NAME = re.compile(r"(\d+)-([A-Za-z]+)-(\d+)\.json")
MAX_LENGTH_ID = "x" * 128
VALID_IDS = ("0f8fad5b-d9cb-469f-a165-70867728950e", "CONSOLE", "COM10", "a_b-C", MAX_LENGTH_ID)
UNSAFE_IDS = ("../x", "a/b", ".", "..", "CON", "nul", "COM1", "LPT9", "aux", "PRN", "", "a\\b", "x" * 129, "a.b", INVALID_FOLDER, RECORDS_FOLDER,
              " ", "a b", "\u00e9", "conv-1\n", "a\x00b", "CON ", "CON.")
DEEPLY_NESTED = b"[" * 100_000
ANSI_INPUT = b'{"conversation_id": "conv-1", "text": "caf\xe9 \x93x\x94"}'
ANSI_TEXT = '{"conversation_id": "conv-1", "text": "caf\ufffd \ufffdx\ufffd"}'


def payload(**fields):
    return json.dumps(fields, ensure_ascii=False).encode("utf-8")


def hook_environment(home):
    """The variables of a real hook process: the sandbox is its home."""
    env = {name: os.environ[name] for name in SUBPROCESS_ENV_NAMES if name in os.environ}
    env["HOME"] = env["USERPROFILE"] = home
    return env


def run_hook_process(home, event, **stdin):
    """Runs `session_log.py hook cursor <event>` as a real process under `home`; returns the completed process."""
    return subprocess.run([sys.executable, str(ENTRY), "hook", "cursor", event], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=SUBPROCESS_TIMEOUT_SECONDS, env=hook_environment(home), **stdin)


class CursorHookCase(SandboxCase):
    def setUp(self):
        super().setUp()
        self.cursor = os.path.join(self.home, CURSOR_FOLDER)
        self.log_root = os.path.join(self.cursor, LOG_FOLDER)
        self.records = os.path.join(self.log_root, RECORDS_FOLDER)
        self.ticks = itertools.count(START_NS, STEP_NS)

    def start_over(self):
        """A fresh sandbox inside one test: the folder of the harness is gone, the clock starts again."""
        shutil.rmtree(self.cursor, ignore_errors=True)
        self.ticks = itertools.count(START_NS, STEP_NS)

    def turn_logging_on(self):
        os.makedirs(self.log_root, exist_ok=True)
        with open(os.path.join(self.log_root, ON_FLAG), "w", encoding="utf-8") as stream:
            stream.write("2000-01-01T00:00:00+00:00\n")

    def sync_state_path(self):
        return os.path.join(self.log_root, SYNC_STATE)

    def forget_sync(self):
        """Removes what a sync saves, so `sync_ran` tells whether the next hook synced."""
        try:
            os.remove(self.sync_state_path())
        except FileNotFoundError:
            pass

    def sync_ran(self):
        """Whether a sync ran to the saving of its state file (it runs only while logging is on)."""
        return os.path.isfile(self.sync_state_path())

    def run_cursor_hook(self, event, stdin=b"", **kwargs):
        kwargs.setdefault("time_ns", lambda: next(self.ticks))
        kwargs.setdefault("pid", lambda: FIXED_PID)
        stream = io.BytesIO(stdin) if isinstance(stdin, bytes) else stdin
        return self.run_main(["hook", "cursor", event], stdin=stream, **kwargs)

    def files_under(self, folder):
        return sorted(os.path.relpath(os.path.join(base, name), folder) for base, _, names in os.walk(folder) for name in names)

    def snapshot(self):
        found = {}
        for base, folders, names in os.walk(self.home):
            found.update({os.path.join(base, name): None for name in folders})
            for name in names:
                with open(os.path.join(base, name), "rb") as stream:
                    found[os.path.join(base, name)] = stream.read()
        return found

    def outside_records(self):
        return {path: data for path, data in self.snapshot().items() if path != self.records and not path.startswith(self.records + os.sep)}

    def read_record(self, folder, name):
        with open(os.path.join(self.records, folder, name), "rb") as stream:
            return json.loads(stream.read().decode("utf-8"))

    def only_record(self, folder):
        names = os.listdir(os.path.join(self.records, folder))
        self.assertEqual(1, len(names), names)
        return names[0], self.read_record(folder, names[0])

    def age_file(self, path, seconds):
        moment = START_NS - seconds * SECONDS
        os.utime(path, ns=(moment, moment))


class RecordWritingTests(CursorHookCase):
    def test_hook_with_bom_and_non_ascii_writes_one_record_via_temp_rename_prints_nothing(self):
        self.turn_logging_on()
        raw = BOM + payload(conversation_id=CONVERSATION, generation_id="g1", prompt=NON_ASCII_TEXT)
        real_replace, real_open = os.replace, os.open
        replaced, opened = [], []

        def spy_replace(source, destination):
            if source.startswith(self.records):
                with open(source, "rb") as stream:
                    replaced.append((source, destination, stream.read()))
            return real_replace(source, destination)

        def spy_open(path, flags, *args, **kwargs):
            opened.append((path, flags))
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(temp_files.os, "replace", side_effect=spy_replace), mock.patch.object(temp_files.os, "open", side_effect=spy_open):
            self.assertEqual(SILENT_SUCCESS, self.run_cursor_hook("stop", raw))
        self.assertEqual([CONVERSATION], os.listdir(self.records))
        name, record = self.only_record(CONVERSATION)
        self.assertEqual((str(START_NS), "stop", str(FIXED_PID)), RECORD_NAME.fullmatch(name).groups())
        self.assertEqual({"event": "stop", "conversationId": CONVERSATION, "receivedAtNs": START_NS, "pid": FIXED_PID,
                          "rawInput": raw[len(BOM):].decode("utf-8")}, record)
        self.assertIn(NON_ASCII_TEXT, record["rawInput"])
        self.assertFalse(record["rawInput"].startswith("\ufeff"))
        self.assertEqual(1, len(replaced))
        source, destination, content_at_rename = replaced[0]
        self.assertEqual(os.path.join(self.records, CONVERSATION, name), destination)
        self.assertEqual(os.path.dirname(destination), os.path.dirname(source))
        self.assertRegex(os.path.basename(source), r"^\..+\.tmp$")
        self.assertTrue(cursor_records.STALE_RECORD_TEMP.fullmatch(os.path.basename(source)), source)  # the cleanup finds what the hook leaves
        with open(destination, "rb") as stream:
            self.assertEqual(stream.read(), content_at_rename)  # complete before it got its final name
        exclusive = [flags for path, flags in opened if path == source]
        self.assertEqual(1, len(exclusive))
        self.assertTrue(exclusive[0] & os.O_EXCL and exclusive[0] & os.O_CREAT)
        self.assertEqual([os.path.join(CONVERSATION, name)], self.files_under(self.records))  # no temp left

    def test_every_record_event_writes_its_own_file_with_its_event_and_the_same_event_twice_writes_two(self):
        self.turn_logging_on()
        for event in RECORD_EVENTS + ("stop",):
            with self.subTest(event=event):
                self.assertEqual(SILENT_SUCCESS, self.run_cursor_hook(event, payload(conversation_id=CONVERSATION)))
        folder = os.path.join(self.records, CONVERSATION)
        names = sorted(os.listdir(folder))
        self.assertEqual(len(RECORD_EVENTS) + 1, len(names))
        self.assertEqual(sorted(RECORD_EVENTS + ("stop",)), sorted(RECORD_NAME.fullmatch(name).group(2) for name in names))
        self.assertEqual(len(names), len({RECORD_NAME.fullmatch(name).group(1) for name in names}))
        for name in names:
            self.assertEqual(RECORD_NAME.fullmatch(name).group(2), self.read_record(CONVERSATION, name)["event"])

    def test_record_keeps_the_input_unparsed_beyond_the_conversation_id(self):
        self.turn_logging_on()
        raw = b'{"conversation_id": "conv-1", "generation_id": 5, "nested": {"a": [1, 2]}, "usage": null}\r\n'
        self.assertEqual(SILENT_SUCCESS, self.run_cursor_hook("afterAgentResponse", raw))
        self.assertEqual(raw.decode("utf-8"), self.only_record(CONVERSATION)[1]["rawInput"])

    def test_a_128_character_id_is_a_conversation_folder(self):
        self.turn_logging_on()
        self.assertEqual(SILENT_SUCCESS, self.run_cursor_hook("stop", payload(conversation_id=MAX_LENGTH_ID)))
        self.assertEqual([MAX_LENGTH_ID], os.listdir(self.records))

    def test_arrival_time_is_taken_before_the_input_is_read(self):
        self.turn_logging_on()
        ticks = self.ticks

        class SlowInput:
            def read(self, *_):
                next(ticks)  # time passes while the hook waits for its input
                return payload(conversation_id=CONVERSATION)

        self.assertEqual(SILENT_SUCCESS, self.run_cursor_hook("stop", stdin=SlowInput()))
        self.assertEqual(START_NS, self.only_record(CONVERSATION)[1]["receivedAtNs"])

    def test_real_process_reads_an_input_larger_than_a_pipe_buffer_whole(self):
        self.turn_logging_on()
        text = "x\u00e9" * 150_000
        result = run_hook_process(self.home, "afterAgentResponse", input=payload(conversation_id=CONVERSATION, text=text))
        self.assertEqual((0, b"", b""), (result.returncode, result.stdout, result.stderr))
        self.assertEqual(text, json.loads(self.only_record(CONVERSATION)[1]["rawInput"])["text"])

    def test_valid_ids_including_device_lookalikes_route_to_their_folder_whatever_other_ids_the_input_holds(self):
        self.turn_logging_on()
        for conversation in VALID_IDS:
            with self.subTest(conversation=conversation):
                raw = payload(conversation_id=conversation, child_conversation_id="../x", generation_id="a/b", parent={"conversation_id": ".."})
                self.assertEqual(SILENT_SUCCESS, self.run_cursor_hook("subagentStop", raw))
        self.assertEqual(sorted(VALID_IDS), sorted(os.listdir(self.records)))

    def test_record_file_is_private_on_posix(self):
        if os.name != "posix":
            self.skipTest("file modes are POSIX only")
        self.turn_logging_on()
        self.assertEqual(SILENT_SUCCESS, self.run_cursor_hook("stop", payload(conversation_id=CONVERSATION)))
        name, _ = self.only_record(CONVERSATION)
        self.assertEqual(0o600, os.stat(os.path.join(self.records, CONVERSATION, name)).st_mode & 0o777)

    def test_real_process_reads_raw_stdin_bytes_with_the_real_clock_and_pid_prints_nothing_and_exits_0(self):
        self.turn_logging_on()
        raw = BOM + payload(conversation_id=CONVERSATION, prompt=NON_ASCII_TEXT)
        before = time.time_ns()
        result = run_hook_process(self.home, "beforeSubmitPrompt", input=raw)
        after = time.time_ns()
        self.assertEqual((0, b"", b""), (result.returncode, result.stdout, result.stderr))
        name, record = self.only_record(CONVERSATION)
        received_at, event, pid = RECORD_NAME.fullmatch(name).groups()
        self.assertEqual("beforeSubmitPrompt", event)
        self.assertTrue(before <= int(received_at) <= after, (before, received_at, after))
        self.assertGreater(int(pid), 0)
        self.assertEqual({"event": "beforeSubmitPrompt", "conversationId": CONVERSATION, "receivedAtNs": int(received_at),
                          "pid": int(pid), "rawInput": raw[len(BOM):].decode("utf-8")}, record)

    def test_real_process_with_no_input_records_it_as_invalid_and_writes_nothing_else(self):
        self.turn_logging_on()
        before_outside = self.outside_records()
        result = run_hook_process(self.home, "stop", stdin=subprocess.DEVNULL)
        self.assertEqual((0, b"", b""), (result.returncode, result.stdout, result.stderr))
        self.assertEqual([INVALID_FOLDER], os.listdir(self.records))
        name, record = self.only_record(INVALID_FOLDER)
        self.assertEqual("stop", RECORD_NAME.fullmatch(name).group(2))
        self.assertEqual({"event", "receivedAtNs", "pid", "rawInput"}, set(record))  # no conversationId
        self.assertEqual("", record["rawInput"])
        self.assertEqual(before_outside, self.outside_records())


class InvalidInputTests(CursorHookCase):
    def check_goes_to_invalid(self, raw, event="stop"):
        self.start_over()
        self.turn_logging_on()
        before_outside = self.outside_records()
        self.assertEqual(SILENT_SUCCESS, self.run_cursor_hook(event, raw))
        self.assertEqual([INVALID_FOLDER], os.listdir(self.records))
        name, record = self.only_record(INVALID_FOLDER)
        self.assertEqual(event, RECORD_NAME.fullmatch(name).group(2))
        self.assertEqual({"event": event, "receivedAtNs": START_NS, "pid": FIXED_PID,
                          "rawInput": raw.decode("utf-8", errors="replace").removeprefix("\ufeff")}, record)
        self.assertEqual(before_outside, self.outside_records())

    def test_empty_invalid_json_missing_or_unsafe_id_go_to_invalid_ansi_input_kept_with_replacement_chars(self):
        cases = [b"", b"   ", BOM, BOM + b"junk", BOM + BOM + payload(conversation_id=CONVERSATION), b"not json", b"{", b"[]", b'"conv-1"',
                 b"null", DEEPLY_NESTED, payload(prompt="no id"), payload(conversation_id=None), payload(conversation_id=7),
                 payload(conversation_id=["a"]), payload(x={"conversation_id": CONVERSATION}), payload(conversationId=CONVERSATION),
                 b'{"conversation_id": "\\u002e\\u002e/x"}', b'{"conversation_id": "\\ud800"}', b'{"conversation_id": "conv\xe9"}', b'caf\xe9 not json']
        cases += [payload(conversation_id=unsafe) for unsafe in UNSAFE_IDS]
        for raw in cases:
            with self.subTest(raw=raw[:60]):
                self.check_goes_to_invalid(raw)
        for event in RECORD_EVENTS:
            with self.subTest(event=event):
                self.check_goes_to_invalid(b"junk", event)
        self.start_over()
        self.turn_logging_on()
        self.assertEqual(SILENT_SUCCESS, self.run_cursor_hook("stop", ANSI_INPUT))
        self.assertEqual([CONVERSATION], os.listdir(self.records))
        self.assertEqual(ANSI_TEXT, self.only_record(CONVERSATION)[1]["rawInput"])
        self.start_over()
        self.turn_logging_on()
        self.assertEqual(SILENT_SUCCESS, self.run_cursor_hook("stop", b'caf\xe9 \x93not json\x94'))
        self.assertEqual("caf\ufffd \ufffdnot json\ufffd", self.only_record(INVALID_FOLDER)[1]["rawInput"])

    def test_unsafe_ids_create_no_folder_outside_records(self):
        self.turn_logging_on()
        before_outside = self.outside_records()
        for unsafe in ("../x", "../../escape", "a/b", "/abs"):
            self.assertEqual(SILENT_SUCCESS, self.run_cursor_hook("stop", payload(conversation_id=unsafe)))
        self.assertEqual([INVALID_FOLDER], os.listdir(self.records))
        self.assertEqual(4, len(os.listdir(os.path.join(self.records, INVALID_FOLDER))))
        self.assertEqual(before_outside, self.outside_records())
        self.assertEqual(sorted([RECORDS_FOLDER, ON_FLAG]), sorted(os.listdir(self.log_root)))


class GateTests(CursorHookCase):
    def test_logging_off_writes_nothing_and_creates_nothing(self):
        self.assertEqual(SILENT_SUCCESS, self.run_cursor_hook("stop", payload(conversation_id=CONVERSATION)))
        self.assertEqual([], self.home_entries())
        os.makedirs(self.log_root)
        before = self.snapshot()
        self.assertEqual(SILENT_SUCCESS, self.run_cursor_hook("stop", payload(conversation_id=CONVERSATION)))
        self.assertEqual(before, self.snapshot())

    def test_old_enabled_flag_is_migrated_first_and_the_record_is_written(self):
        os.makedirs(self.log_root)
        with open(os.path.join(self.log_root, LEGACY_FLAG), "w", encoding="utf-8") as stream:
            stream.write("")
        self.assertEqual(SILENT_SUCCESS, self.run_cursor_hook("stop", payload(conversation_id=CONVERSATION)))
        self.assertFalse(os.path.exists(os.path.join(self.log_root, LEGACY_FLAG)))
        self.assertTrue(os.path.isfile(os.path.join(self.log_root, ON_FLAG)))
        self.assertEqual(1, len(os.listdir(os.path.join(self.records, CONVERSATION))))

    def test_a_failed_removal_of_the_old_flag_does_not_stop_the_record(self):
        self.turn_logging_on()
        with mock.patch.object(FileStateStore, "migrate_enabled", side_effect=OSError("cannot remove .enabled")) as migrate:
            self.assertEqual(SILENT_SUCCESS, self.run_cursor_hook("stop", payload(conversation_id=CONVERSATION)))
        migrate.assert_called_once()
        self.assertEqual(1, len(os.listdir(os.path.join(self.records, CONVERSATION))))

    def test_a_junction_folder_is_neither_written_through_nor_swept(self):
        self.turn_logging_on()
        junction = os.path.join(self.records, CONVERSATION)
        os.makedirs(junction)
        stale = os.path.join(junction, ".%d-stop-%d.json.%d.abcd1234.tmp" % (START_NS - 5, FIXED_PID, FIXED_PID))
        with open(stale, "w", encoding="utf-8") as stream:
            stream.write("x")
        self.age_file(stale, OLD_AGE_SECONDS)
        with mock.patch.object(hook_records.os.path, "isjunction", side_effect=lambda path: path == junction):
            self.assertEqual(SILENT_SUCCESS, self.run_cursor_hook("stop", payload(conversation_id=CONVERSATION)))
            self.assertEqual(SILENT_SUCCESS, self.run_cursor_hook("sessionStart", time_ns=lambda: START_NS))
        self.assertEqual([os.path.basename(stale)], os.listdir(junction))

    def test_a_relative_home_is_refused_silently_and_writes_nothing(self):
        self.turn_logging_on()
        before = self.snapshot()
        self.assertEqual(SILENT_SUCCESS, self.run_cursor_hook("stop", payload(conversation_id=CONVERSATION), home="relative/home"))
        self.assertEqual(before, self.snapshot())

    def test_old_python_records_nothing(self):
        self.turn_logging_on()
        before = self.snapshot()
        self.assertEqual(SILENT_SUCCESS, self.run_cursor_hook("stop", payload(conversation_id=CONVERSATION), version_info=(3, 13, 0)))
        self.assertEqual(before, self.snapshot())

    def test_malformed_cursor_hook_argv_is_silent_and_writes_nothing(self):
        self.turn_logging_on()
        before = self.snapshot()
        for arguments in (["cursor"], ["cursor", "bogus"], ["cursor", "Stop"], ["cursor", "stop", "extra"], ["cursor", "SessionStart"],
                          ["cursor", ""], ["cursor", "sessionstart"]):
            with self.subTest(arguments=arguments):
                stdout, stderr = io.StringIO(), io.StringIO()
                code = session_log.main(["hook"] + arguments, self.environment(
                    stdout, stderr, stdin=io.BytesIO(payload(conversation_id=CONVERSATION))))
                self.assertEqual((0, "", ""), (code, stdout.getvalue(), stderr.getvalue()))
                self.assertEqual(before, self.snapshot())

    def test_every_cursor_event_is_registered(self):
        self.assertEqual(RECORD_EVENTS, cursor_records.RECORD_EVENTS)  # the entry cannot import the package to share the list
        self.assertEqual(RECORD_EVENTS, session_log.CURSOR_RECORD_EVENTS)
        for event in RECORD_EVENTS + ("sessionStart",):
            self.assertIn(("cursor", event), session_log.HOOK_HANDLERS)

    def test_write_failure_anywhere_in_the_write_is_swallowed_and_leaves_no_temp(self):
        self.turn_logging_on()
        steps = (("replace", temp_files.os), ("fsync", temp_files.os), ("open", temp_files.os), ("fdopen", temp_files.os),
                 ("mkdir", hook_records.os))
        for failure in (OSError("disk"), RuntimeError("boom"), MemoryError(), KeyboardInterrupt()):
            for name, owner in steps:
                with self.subTest(failure=type(failure).__name__, step=name):
                    shutil.rmtree(self.records, ignore_errors=True)  # the folders are created again, so `mkdir` is reached
                    with mock.patch.object(owner, name, side_effect=failure) as step:
                        self.assertEqual(SILENT_SUCCESS, self.run_cursor_hook("stop", payload(conversation_id=CONVERSATION)))
                    step.assert_called()
                    self.assertEqual([], [name for _, _, names in os.walk(self.records) for name in names])

    def test_unreadable_stdin_is_swallowed(self):
        self.turn_logging_on()
        reads = []

        class Broken:
            def read(self, *_):
                reads.append(True)
                raise OSError("closed")

        self.assertEqual(SILENT_SUCCESS, self.run_cursor_hook("stop", stdin=Broken()))
        self.assertEqual([True], reads)
        self.assertEqual([], [name for _, _, names in os.walk(self.records) for name in names])


@unittest.skipUnless(hasattr(os, "symlink"), "needs symlinks")
class LinkSafetyTests(CursorHookCase):
    def setUp(self):
        super().setUp()
        self.turn_logging_on()
        self.elsewhere = os.path.join(self.home, "elsewhere")
        os.makedirs(self.elsewhere)

    def make_link(self, link, target=None):
        try:
            os.symlink(self.elsewhere if target is None else target, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("cannot create symlinks here")

    def stale_temp_in(self, folder):
        path = os.path.join(folder, ".%d-stop-%d.json.%d.abcd1234.tmp" % (START_NS, FIXED_PID, FIXED_PID))
        with open(path, "w", encoding="utf-8") as stream:
            stream.write("x")
        self.age_file(path, OLD_AGE_SECONDS)
        return path

    def test_symlinked_records_folder_is_not_written_through(self):
        self.make_link(self.records)
        for raw in (payload(conversation_id=CONVERSATION), b"junk"):
            self.assertEqual(SILENT_SUCCESS, self.run_cursor_hook("stop", raw))
        self.assertEqual([], os.listdir(self.elsewhere))
        self.assertTrue(os.path.islink(self.records))

    def test_symlinked_conversation_folder_is_not_written_through_and_other_conversations_still_record(self):
        os.makedirs(self.records)
        self.make_link(os.path.join(self.records, CONVERSATION))
        self.assertEqual(SILENT_SUCCESS, self.run_cursor_hook("stop", payload(conversation_id=CONVERSATION)))
        self.assertEqual([], os.listdir(self.elsewhere))
        self.assertEqual(SILENT_SUCCESS, self.run_cursor_hook("stop", payload(conversation_id=OTHER_CONVERSATION)))
        self.assertEqual(1, len(os.listdir(os.path.join(self.records, OTHER_CONVERSATION))))
        self.assertEqual([], os.listdir(self.elsewhere))

    def test_symlinked_invalid_folder_is_not_written_through(self):
        os.makedirs(self.records)
        self.make_link(os.path.join(self.records, INVALID_FOLDER))
        self.assertEqual(SILENT_SUCCESS, self.run_cursor_hook("stop", b"junk"))
        self.assertEqual([], os.listdir(self.elsewhere))

    def test_dangling_link_or_plain_file_in_place_of_a_folder_records_nothing_and_stays(self):
        os.makedirs(self.records)
        missing = os.path.join(self.home, "missing")
        self.make_link(os.path.join(self.records, CONVERSATION), missing)
        with open(os.path.join(self.records, OTHER_CONVERSATION), "w", encoding="utf-8") as stream:
            stream.write("a file")
        for conversation in (CONVERSATION, OTHER_CONVERSATION):
            self.assertEqual(SILENT_SUCCESS, self.run_cursor_hook("stop", payload(conversation_id=conversation)))
        self.assertFalse(os.path.exists(missing))
        self.assertTrue(os.path.islink(os.path.join(self.records, CONVERSATION)))
        self.assertTrue(os.path.isfile(os.path.join(self.records, OTHER_CONVERSATION)))

    def test_stale_temp_removal_does_not_follow_a_symlinked_conversation_folder(self):
        os.makedirs(self.records)
        self.make_link(os.path.join(self.records, CONVERSATION))
        stale = self.stale_temp_in(self.elsewhere)
        self.assertEqual(SILENT_SUCCESS, self.run_cursor_hook("sessionStart", time_ns=lambda: START_NS))
        self.assertTrue(self.sync_ran())
        self.assertTrue(os.path.exists(stale))

    def test_stale_temp_removal_does_not_follow_a_symlinked_records_folder(self):
        self.make_link(self.records)
        os.makedirs(os.path.join(self.elsewhere, CONVERSATION))
        stale = self.stale_temp_in(os.path.join(self.elsewhere, CONVERSATION))
        self.assertEqual(SILENT_SUCCESS, self.run_cursor_hook("sessionStart", time_ns=lambda: START_NS))
        self.assertTrue(self.sync_ran())
        self.assertTrue(os.path.exists(stale))


class SessionStartTests(CursorHookCase):
    def temp_name(self, event="stop", suffix="abcd1234"):
        return ".%d-%s-%d.json.%d.%s.tmp" % (START_NS - 5, event, FIXED_PID, FIXED_PID, suffix)

    def seed(self, folder, name, age):
        os.makedirs(os.path.join(self.records, folder), exist_ok=True)
        path = os.path.join(self.records, folder, name)
        with open(path, "w", encoding="utf-8") as stream:
            stream.write("{}")
        self.age_file(path, age)
        return path

    def run_session_start(self):
        """Runs a real `sessionStart` hook; whether its sync got as far as saving `.sync.json`."""
        self.forget_sync()
        self.assertEqual(SILENT_SUCCESS, self.run_cursor_hook("sessionStart", time_ns=lambda: START_NS))
        return self.sync_ran()

    def test_session_start_removes_old_record_temp_keeps_fresh_one_then_syncs(self):
        self.turn_logging_on()
        old = self.seed(CONVERSATION, self.temp_name(suffix="aaaa0000"), OLD_AGE_SECONDS)
        old_invalid = self.seed(INVALID_FOLDER, self.temp_name("subagentStop", "bbbb1111"), OLD_AGE_SECONDS)
        fresh = self.seed(CONVERSATION, self.temp_name(suffix="cccc2222"), FRESH_AGE_SECONDS)
        record = self.seed(CONVERSATION, "%d-stop-%d.json" % (START_NS - 9, FIXED_PID), OLD_AGE_SECONDS)
        foreign = self.seed(CONVERSATION, ".notes.tmp", OLD_AGE_SECONDS)
        scans = []
        real_scan = CursorReader.scan

        def scan(reader):
            scans.append((os.path.exists(old), os.path.exists(old_invalid), os.path.exists(fresh)))
            return real_scan(reader)

        reads = []

        class Unreadable:
            def read(self, *_):
                reads.append(True)
                raise AssertionError("sessionStart must not read stdin")

        with mock.patch.object(CursorReader, "scan", scan):
            stdout, stderr = io.StringIO(), io.StringIO()
            environment = self.environment(stdout, stderr, stdin=Unreadable(), time_ns=lambda: START_NS, pid=lambda: FIXED_PID)
            self.assertEqual(0, session_log.main(["hook", "cursor", "sessionStart"], environment))
        self.assertEqual(("", ""), (stdout.getvalue(), stderr.getvalue()))
        self.assertEqual([], reads)
        self.assertEqual([(False, False, True)], scans)  # the real sync ran once, after the old temps went
        self.assertTrue(self.sync_ran())
        self.assertEqual(sorted([os.path.join(CONVERSATION, os.path.basename(path)) for path in (fresh, record, foreign)]),
                         self.files_under(self.records))  # fresh temp, records and other files stay, nothing new
        self.assertFalse(os.path.exists(old_invalid))

    def test_session_start_writes_no_record_and_does_nothing_while_logging_is_off(self):
        old = self.seed(CONVERSATION, self.temp_name(), OLD_AGE_SECONDS)
        self.assertFalse(self.run_session_start())
        self.assertTrue(os.path.exists(old))
        self.turn_logging_on()
        self.assertTrue(self.run_session_start())
        self.assertFalse(os.path.exists(old))
        self.assertEqual([], self.files_under(self.records))  # no record of its own

    def test_session_start_without_a_records_folder_still_syncs(self):
        self.turn_logging_on()
        self.assertTrue(self.run_session_start())
        self.assertFalse(os.path.exists(self.records))

    def test_a_failing_sync_does_not_undo_the_cleanup_and_stays_silent(self):
        self.turn_logging_on()
        old = self.seed(CONVERSATION, self.temp_name(), OLD_AGE_SECONDS)
        with mock.patch.object(CursorReader, "scan", side_effect=RuntimeError("boom")):
            self.assertFalse(self.run_session_start())
        self.assertFalse(os.path.exists(old))

    def test_a_cleanup_that_cannot_remove_or_list_does_not_stop_the_sync(self):
        self.turn_logging_on()
        old = self.seed(CONVERSATION, self.temp_name(), OLD_AGE_SECONDS)
        for step in ("unlink", "listdir"):
            with self.subTest(step=step), mock.patch.object(temp_files.os, step, side_effect=PermissionError("denied")) as failing:
                self.assertTrue(self.run_session_start())
                failing.assert_any_call(old if step == "unlink" else self.records)
        self.assertTrue(os.path.exists(old))

    def test_temp_exactly_one_hour_old_is_kept_and_older_is_removed(self):
        self.turn_logging_on()
        edge = self.seed(CONVERSATION, self.temp_name(suffix="eeee0000"), HOUR)
        older = self.seed(CONVERSATION, self.temp_name(suffix="ffff0000"), HOUR + 1)
        self.run_session_start()
        self.assertTrue(os.path.exists(edge))
        self.assertFalse(os.path.exists(older))

    def test_lookalike_names_are_never_removed(self):
        self.turn_logging_on()
        good = self.temp_name()
        lookalikes = (good[1:], good + "x", good.replace("-stop-", "-sessionStart-"), good.replace("abcd1234", "xyz"),
                      good.replace(".json", ".txt"), ".x" + good, good.replace(".%d." % FIXED_PID, ".\u0663."),
                      ".-stop-%d.json.%d.abcd1234.tmp" % (FIXED_PID, FIXED_PID), ".1-stop-.json.%d.abcd1234.tmp" % FIXED_PID)
        paths = [self.seed(CONVERSATION, name, OLD_AGE_SECONDS) for name in lookalikes]
        removed = self.seed(CONVERSATION, good, OLD_AGE_SECONDS)  # the control: the real shape of a temp
        self.run_session_start()
        self.assertEqual([], [path for path in paths if not os.path.exists(path)])
        self.assertFalse(os.path.exists(removed))

    def test_one_folder_that_cannot_be_listed_does_not_stop_the_others(self):
        self.turn_logging_on()
        unlistable = self.seed(CONVERSATION, self.temp_name(suffix="aaaa0000"), OLD_AGE_SECONDS)
        removable = self.seed(OTHER_CONVERSATION, self.temp_name(suffix="bbbb1111"), OLD_AGE_SECONDS)
        real_listdir = os.listdir

        def listdir(path):
            if os.path.basename(path) == CONVERSATION:
                raise PermissionError("denied")
            return real_listdir(path)

        with mock.patch.object(temp_files.os, "listdir", side_effect=listdir):
            self.assertTrue(self.run_session_start())
        self.assertTrue(os.path.exists(unlistable))
        self.assertFalse(os.path.exists(removable))

    def test_real_process_session_start_removes_a_stale_temp_by_the_real_clock_and_prints_nothing(self):
        self.turn_logging_on()
        stale = self.seed(CONVERSATION, self.temp_name(suffix="aaaa0000"), 0)
        fresh = self.seed(CONVERSATION, self.temp_name(suffix="bbbb1111"), 0)
        for path, age in ((stale, OLD_AGE_SECONDS), (fresh, FRESH_AGE_SECONDS)):
            moment = time.time() - age
            os.utime(path, (moment, moment))
        result = run_hook_process(self.home, "sessionStart", stdin=subprocess.DEVNULL)
        self.assertEqual((0, b"", b""), (result.returncode, result.stdout, result.stderr))
        self.assertFalse(os.path.exists(stale))
        self.assertTrue(os.path.exists(fresh))

    def test_stale_temps_of_every_recorded_event_are_removed(self):
        self.turn_logging_on()
        paths = [self.seed(CONVERSATION, self.temp_name(event, "%08x" % number), OLD_AGE_SECONDS) for number, event in enumerate(RECORD_EVENTS)]
        self.run_session_start()
        self.assertEqual([], [path for path in paths if os.path.exists(path)])


class ConcurrentHooksTests(CursorHookCase):
    CALLS = 8

    def test_overlapping_first_calls_each_write_their_record_and_leave_no_temp(self):
        self.turn_logging_on()
        command = [sys.executable, str(ENTRY), "hook", "cursor", "stop"]
        data = payload(conversation_id=CONVERSATION)
        processes = [subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=hook_environment(self.home))
                     for _ in range(self.CALLS)]
        try:
            for process in processes:
                process.stdin.write(data)
                process.stdin.flush()
            for process in processes:  # every hook now holds its whole input and waits for its end: they create the folders together
                process.stdin.close()
            for process in processes:
                out, err = process.stdout.read(), process.stderr.read()
                self.assertEqual((0, b"", b""), (process.wait(timeout=SUBPROCESS_TIMEOUT_SECONDS), out, err))
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                process.stdout.close()
                process.stderr.close()
        names = os.listdir(os.path.join(self.records, CONVERSATION))
        self.assertEqual(self.CALLS, len(names))
        self.assertEqual(self.CALLS, len({RECORD_NAME.fullmatch(name).group(3) for name in names}))  # one process id each


class ReadStdinTests(unittest.TestCase):
    def test_injected_binary_stream_is_read_whole(self):
        self.assertEqual(b"\xef\xbb\xbfabc\xff", session_log.Environment(stdin=io.BytesIO(b"\xef\xbb\xbfabc\xff")).read_stdin())

    def test_real_stdin_is_its_binary_buffer(self):
        class Fake:
            buffer = io.BytesIO(b"raw bytes")

        with mock.patch.object(sys, "stdin", Fake()):
            self.assertEqual(b"raw bytes", session_log.Environment().read_stdin())

    def test_no_standard_input_or_one_without_a_buffer_is_no_input(self):
        for stdin in (None, io.StringIO("text")):
            with self.subTest(stdin=stdin), mock.patch.object(sys, "stdin", stdin):
                self.assertEqual(b"", session_log.Environment().read_stdin())


if __name__ == "__main__":
    unittest.main()
