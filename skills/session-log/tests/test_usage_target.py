"""`usage` target resolution (BE-19, S37): no argument = `--latest`, ranking from `.sync.json`, `<id>` exact else unique prefix
over archived and store sessions, project by cwd or its nearest parent folder. A real ClaudeReader, FileStateStore and sync
over a transcript tree; the archive is a fake that keeps its reports."""
import json
import os
import shutil
import tempfile
import unittest
from datetime import timedelta
from typing import Optional

from sessionlog.archive_port import WriteOutcome
from sessionlog.claude_reader import ClaudeReader
from sessionlog.command_error import CommandError
from sessionlog.commands import CommandUseCases
from sessionlog.model import Harness
from sessionlog.pricing import PricingRules
from sessionlog.reader_port import CANNOT_BE_LISTED_REASON, ScanResult, UnreadableSource
from sessionlog.state import FileStateStore
from sessionlog.state_port import OnFlag
from sessionlog.sync import SyncUseCase
from sessionlog.usage_target import MAX_LISTED_MATCHES, UsageTargetResolver
from tests.test_commands import ReportKeepingArchive
from tests.test_session_log import SandboxCase
from tests.test_sync import NOW, ON_AT, ONE_DAY, WRITE_FAILURE, NoLeftovers, NoProbe, _NoPrices, _stamp

PROJECT = "/work/proj"
OTHER_PROJECT = "/work/other"
EMPTY_PROJECT = "/work/empty"
FOLDER = "-work"
ONE_SECOND = timedelta(seconds=1)
BEFORE_ON = ON_AT - ONE_DAY


def _report(session_id: str, requests: int = 1) -> str:
    return "report %s (%d requests)" % (session_id, requests)


def _user(moment, cwd, text, prompt_source="typed", **extra) -> dict:
    return {"type": "user", "promptSource": prompt_source, "cwd": cwd, "timestamp": _stamp(moment),
            "message": {"role": "user", "content": text}, **extra}


def _reply(moment, message_id) -> dict:
    return {"type": "assistant", "timestamp": _stamp(moment),
            "message": {"id": message_id, "model": "claude-x", "content": [{"type": "text", "text": "reply"}],
                        "usage": {"input_tokens": 1, "output_tokens": 1}}}


class _CountingSync:
    """Counts the syncs the use cases start."""

    def __init__(self, sync: SyncUseCase) -> None:
        self._sync = sync
        self.calls = 0

    def sync(self):
        self.calls += 1
        return self._sync.sync()


class UsageTargetTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.folder = os.path.join(self.root, "projects", FOLDER)
        os.makedirs(self.folder)
        self.log_root = os.path.join(self.root, "prompt-logs")
        os.makedirs(self.log_root)
        self.state = FileStateStore(self.log_root)
        self.archive = ReportKeepingArchive()
        self.reader = ClaudeReader(self.root)
        self.sync = _CountingSync(SyncUseCase(self.reader, self.archive, self.state, PricingRules(_NoPrices()), lambda: NOW))
        self.commands = CommandUseCases(self.sync, UsageTargetResolver(self.reader), self.archive, self.state,
                                        PricingRules(_NoPrices()),
                                        lambda: NOW, self.log_root, NoLeftovers(), NoProbe())

    def _install(self, session_id: str, cwd: str = PROJECT, prompts=(), activity=None, modified=NOW):
        """A main session file: one typed prompt per time in `prompts`, each answered a second later, and an extra reply at
        `activity` (a session still streaming). Without prompts the session holds a meta record and a reply."""
        records = []
        for number, moment in enumerate(prompts):
            records += [_user(moment, cwd, "prompt %d" % number), _reply(moment + ONE_SECOND, "m%d" % number)]
        if not prompts:
            records.append(_user(activity or modified, cwd, "meta", None, isMeta=True))
        if activity is not None:
            records.append(_reply(activity, "streaming"))
        return self._install_records(session_id, records, modified)

    def _install_records(self, session_id: str, records, modified=NOW) -> str:
        path = os.path.join(self.folder, session_id + ".jsonl")
        self._write(path, records)
        os.utime(path, (modified.timestamp(), modified.timestamp()))
        return path

    def _install_sub_agent(self, session_id: str, name: str, prompt_at):
        directory = os.path.join(self.folder, session_id, "subagents")
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "agent-%s.jsonl" % name)
        self._write(path, [_user(prompt_at, PROJECT, "child prompt", isSidechain=True, agentId=name),
                           _reply(prompt_at + ONE_SECOND, "child")])
        os.utime(path, (NOW.timestamp(), NOW.timestamp()))

    @staticmethod
    def _write(path, records):
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.writelines(json.dumps(record) + "\n" for record in records)

    def _turn_on_and_sync(self, on_at=ON_AT):
        self.state.write_on_flag(OnFlag(on_at))
        self.sync.sync()
        self.sync.calls = 0

    def _inject_orphan(self, session_id: str, prompt_at, project_dir: str = PROJECT):
        """An archived orphan sub-agent log newer than everything: only a ranking that skips orphans passes. The Claude reader
        never makes orphans, so the entry is written to `.sync.json` directly."""
        state = self.state.load_sync()
        state["sessions"]["claude:" + session_id] = {
            "logPath": "/logs/%s.md" % session_id, "state": "written", "projectDir": project_dir, "orphan": True,
            "lastPromptAt": prompt_at.isoformat(), "lastActivityAt": prompt_at.isoformat(), "renderVersion": 1, "unitCount": 1}
        self.state.save_sync(state)
        self.archive.reports["/logs/%s.md" % session_id] = _report(session_id)

    def _text(self, target: Optional[str] = None, cwd: str = PROJECT) -> str:
        usage = self.commands.usage_latest(cwd) if target is None else self.commands.usage_of(target)
        return usage.report_text

    def _count_renders(self) -> list:
        """Wraps the archive's renderer: each rendering gets a distinct ` #<call number>` suffix and is counted in the list."""
        renders = []
        render = self.archive.render_report

        def counted(report):
            renders.append(report)
            return "%s #%d" % (render(report), len(renders))
        self.archive.render_report = counted
        return renders


class LatestSessionTest(UsageTargetTestCase):
    def test_latest_ranks_newest_prompt_streaming_session_and_ignores_subagents_orphans_and_no_prompt_sessions(self):
        self._install("streaming", prompts=[ON_AT + ONE_DAY], activity=NOW)  # older prompt, still writing
        self._install_records("newest", [  # its newest prompt is a slash command, as in the shell fixture
            _user(ON_AT + ONE_DAY, PROJECT, "a first prompt"), _reply(ON_AT + ONE_DAY + ONE_SECOND, "ra1"),
            _user(ON_AT + 2 * ONE_DAY, PROJECT, "<command-name>/plan</command-name>", None),
            _reply(ON_AT + 2 * ONE_DAY + ONE_SECOND, "ra2")], NOW - ONE_DAY)
        self._install_records("noprompt", [  # newest activity of all, but meta, sdk, tool_result and reply entries only
            _user(NOW, PROJECT, "<command-name>/model</command-name>", None, isMeta=True),
            _user(NOW, PROJECT, "<task-notification>done</task-notification>", "sdk"),
            _user(NOW, PROJECT, [{"type": "tool_result", "content": "ok"}], None), _reply(NOW, "rc1")])
        self._install_sub_agent("streaming", "child", ON_AT + 5 * ONE_DAY)  # newest prompt of all, in a sub-agent
        self._install("elsewhere", cwd=OTHER_PROJECT, prompts=[ON_AT + 6 * ONE_DAY])
        self._turn_on_and_sync()
        self._inject_orphan("orphan", ON_AT + 7 * ONE_DAY)

        self.assertEqual(self._text(), _report("newest", 2))
        self.assertEqual(self._text(cwd=OTHER_PROJECT), _report("elsewhere"))
        sessions = self.state.load_sync()["sessions"]
        self.assertIn("claude:orphan", sessions)  # the orphan was a candidate that lost
        self.assertNotIn("lastPromptAt", sessions["claude:noprompt"])  # and so were these two
        self.assertLess(sessions["claude:streaming"]["lastPromptAt"], sessions["claude:streaming"]["lastActivityAt"])

    def test_a_project_without_any_session_exits_1_with_a_message(self):
        self._install("abc", prompts=[ON_AT + ONE_DAY])
        self._turn_on_and_sync()
        with self.assertRaisesRegex(CommandError, EMPTY_PROJECT):
            self._text(cwd=EMPTY_PROJECT)

    def test_a_session_id_gives_the_same_text_as_latest_for_that_session(self):
        self._install("older", prompts=[ON_AT + ONE_DAY])
        self._install("newer", prompts=[ON_AT + 2 * ONE_DAY])
        self._turn_on_and_sync()
        self.assertEqual(self._text("newer"), self._text())


class IdTargetTest(UsageTargetTestCase):
    def _three_prefixed_sessions(self):
        for number, session_id in enumerate(("abc", "abcd", "abce")):
            self._install(session_id, prompts=[ON_AT + (number + 1) * ONE_DAY])
        self._install("xyz", cwd=OTHER_PROJECT, prompts=[ON_AT + ONE_DAY])
        self._turn_on_and_sync()

    def test_usage_id_exact_prefix_ambiguous_unknown_and_subfolder_and_symlinked_cwd(self):
        self._three_prefixed_sessions()

        self.assertEqual(self._text("abc"), _report("abc"))  # exact wins over abcd and abce
        self.assertEqual(self._text("abcd"), _report("abcd"))
        self.assertEqual(self._text("xy"), _report("xyz"))  # unique prefix, found from any project
        with self.assertRaises(CommandError) as ambiguous:
            self._text("ab")
        for session_id in ("abc", "abcd", "abce"):
            self.assertIn(session_id, str(ambiguous.exception))
        self.assertIn("'ab'", str(ambiguous.exception))
        with self.assertRaisesRegex(CommandError, "'nope'"):
            self._text("nope")
        self.assertEqual(self._text(cwd=PROJECT + "/src/deep"), _report("abce"))  # subfolder: nearest parent with sessions
        with self.assertRaisesRegex(CommandError, EMPTY_PROJECT):
            self._text(cwd=EMPTY_PROJECT)
        self._assert_a_symlinked_cwd_matches_its_project_also_from_a_subfolder()

    def _assert_a_symlinked_cwd_matches_its_project_also_from_a_subfolder(self):
        real, link = os.path.join(self.root, "real"), os.path.join(self.root, "link")
        os.makedirs(os.path.join(real, "src"))
        try:
            os.symlink(real, link)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links are not available here")
        self._install("linked", cwd=real, prompts=[ON_AT + 9 * ONE_DAY])
        self.sync.sync()
        self.assertEqual(self._text(cwd=link), _report("linked"))
        self.assertEqual(self._text(cwd=os.path.join(link, "src")), _report("linked"))

    def test_an_id_target_syncs_while_on_first_so_a_new_session_is_found(self):
        self._turn_on_and_sync()
        self._install("fresh", prompts=[ON_AT + ONE_DAY])
        self.assertEqual(self._text("fre"), _report("fresh"))
        self.assertEqual(self.sync.calls, 1)

    def test_a_session_new_while_logging_is_off_that_the_reader_cannot_place_is_not_matchable(self):
        self._install("abc", prompts=[ON_AT + ONE_DAY])
        self._turn_on_and_sync()
        self.state.delete_on_flag()
        self._install("newcomer", prompts=[NOW])
        self.reader.session_of = lambda unit: None
        with self.assertRaisesRegex(CommandError, "'new'"):
            self._text("new")

    def test_a_sub_agent_file_is_never_a_matchable_session_of_its_own(self):
        self._install("abc", prompts=[ON_AT + ONE_DAY])
        self._install_sub_agent("abc", "child", ON_AT + 2 * ONE_DAY)
        self._turn_on_and_sync()
        for target in ("agent", "child"):
            with self.assertRaisesRegex(CommandError, "no session has the id"):
                self._text(target)
        self.assertEqual(self._text("a"), _report("abc"))

    def test_latest_is_not_a_target_word_it_is_looked_up_as_an_id(self):
        self._install("abc", prompts=[ON_AT + ONE_DAY])
        self._turn_on_and_sync()
        with self.assertRaisesRegex(CommandError, "'latest'"):
            self._text("latest")
        self._install("latest", prompts=[ON_AT + 2 * ONE_DAY])
        self.sync.sync()
        self.assertEqual(self._text("latest"), _report("latest"))

    def test_the_harness_prefix_of_a_session_key_is_not_part_of_the_id(self):
        self._install("abc", prompts=[ON_AT + ONE_DAY])
        self._turn_on_and_sync()
        with self.assertRaisesRegex(CommandError, "'claude:abc'"):
            self._text("claude:abc")

    def _numbered_sessions(self, count):
        ids = ["s%d" % number for number in range(count)]
        for number, session_id in enumerate(reversed(ids)):  # installed out of order
            self._install(session_id, prompts=[ON_AT + (number + 1) * ONE_DAY])
        self._turn_on_and_sync()
        return ", ".join(ids[:MAX_LISTED_MATCHES])

    def test_a_prefix_shared_by_exactly_the_listed_maximum_names_every_session_without_ellipsis(self):
        listed = self._numbered_sessions(MAX_LISTED_MATCHES)
        with self.assertRaises(CommandError) as ambiguous:
            self._text("s")
        self.assertTrue(str(ambiguous.exception).endswith("matches %d sessions: %s" % (MAX_LISTED_MATCHES, listed)))

    def test_a_prefix_shared_by_more_sessions_names_the_first_sorted_ones_and_the_count(self):
        listed = self._numbered_sessions(MAX_LISTED_MATCHES + 2)
        with self.assertRaises(CommandError) as ambiguous:
            self._text("s")
        expected = "matches %d sessions: %s, ..." % (MAX_LISTED_MATCHES + 2, listed)
        self.assertTrue(str(ambiguous.exception).endswith(expected))

    def test_ids_match_archived_and_store_sessions_together(self):
        self._install("abc", prompts=[ON_AT + ONE_DAY])
        self._install("abcd", prompts=[BEFORE_ON], modified=BEFORE_ON)  # in the store, never archived
        self._install("zzz", prompts=[BEFORE_ON], modified=BEFORE_ON)
        self._turn_on_and_sync()

        self.assertEqual(self._text("abc"), _report("abc"))  # an exact archived id wins over a store prefix match
        with self.assertRaisesRegex(CommandError, "matches 2 sessions: abc, abcd"):  # `abc` is in the archive once
            self._text("ab")
        self.assertEqual(self.state.load_sync()["sessions"].keys(), {"claude:abc"})  # resolving archives nothing

    def test_an_exact_store_id_wins_over_a_longer_archived_id(self):
        self._install("abc", prompts=[ON_AT + ONE_DAY])
        self._install("ab", prompts=[BEFORE_ON], modified=BEFORE_ON)
        self._turn_on_and_sync()
        self.assertEqual(self._text("ab"), _report("ab"))  # not "ambiguous"

    def test_a_unique_store_session_that_was_never_archived_is_reported_live(self):
        self._install("abc", prompts=[ON_AT + ONE_DAY])
        self._install("zzz", prompts=[BEFORE_ON], modified=BEFORE_ON)
        self._turn_on_and_sync()
        self.assertEqual(self._text("zz"), _report("zzz"))
        self.assertEqual(set(self.state.load_sync()["sessions"]), {"claude:abc"})  # and archives nothing

    def test_a_session_new_while_logging_is_off_is_learned_from_the_reader_and_reported_live(self):
        self._install("abc", prompts=[ON_AT + ONE_DAY])
        self._turn_on_and_sync()
        self.state.delete_on_flag()
        self._install("newcomer", prompts=[NOW])  # no unit entry in .sync.json
        self.assertEqual(self._text("new"), _report("newcomer"))
        self.assertEqual(self.sync.calls, 0)

    def test_a_session_whose_first_write_failed_prints_the_report_it_could_not_save(self):
        self.state.write_on_flag(OnFlag(ON_AT))
        self.archive.failure = WriteOutcome("/logs/x.md", False, WRITE_FAILURE)
        self._install("first", prompts=[ON_AT + ONE_DAY])
        renders = self._count_renders()
        self.assertEqual(self._text("fir"), _report("first") + " #1")  # the text the sync rendered, not a second rendering
        self.assertEqual((len(renders), self.archive.reports), (1, {}))

    def test_a_session_with_an_unsafe_id_is_not_matchable(self):
        self._install("abc", prompts=[ON_AT + ONE_DAY])
        self._install("a..b", prompts=[BEFORE_ON], modified=BEFORE_ON)
        self._turn_on_and_sync()
        self.assertEqual(self._text("a"), _report("abc"))

    def test_an_exact_archived_id_does_not_list_the_store_and_a_listing_never_asks_about_recorded_units(self):
        self._three_prefixed_sessions()
        self.state.delete_on_flag()  # no sync: only the resolution itself may list the store
        scans = []
        real_scan = self.reader.scan
        self.reader.scan = lambda: scans.append(1) or real_scan()
        self.reader.session_of = self._fail
        self.assertEqual(self._text("abcd"), _report("abcd"))
        self.assertEqual(scans, [])
        with self.assertRaises(CommandError):
            self._text("ab")
        self.assertEqual(scans, [1])  # listed once; every unit's session is recorded in .sync.json

    @staticmethod
    def _fail(unit):
        raise AssertionError("session_of must not be called for a recorded unit")

    def test_a_store_that_could_not_be_listed_completely_resolves_no_prefix_but_still_an_exact_id(self):
        self._install("zzz", prompts=[BEFORE_ON], modified=BEFORE_ON)  # in the store only
        self._three_prefixed_sessions()
        self.state.delete_on_flag()
        real_scan = self.reader.scan
        incomplete = UnreadableSource(Harness.CLAUDE, "/store/locked", CANNOT_BE_LISTED_REASON)
        self.reader.scan = lambda: ScanResult(real_scan().units, (incomplete,))

        for target in ("xy", "nope"):
            with self.assertRaisesRegex(CommandError, "/store/locked: " + CANNOT_BE_LISTED_REASON):
                self._text(target)
        self.assertEqual(self._text("abc"), _report("abc"))  # exact archived: decided without the store
        self.assertEqual(self._text("zzz"), _report("zzz"))  # exact in the store: decided there, reported live

    def test_an_empty_id_is_refused_and_never_matches_every_session(self):
        self._install("abc", prompts=[ON_AT + ONE_DAY])
        self._turn_on_and_sync()
        with self.assertRaisesRegex(CommandError, "must not be empty"):
            self._text("")


class OffTest(UsageTargetTestCase):
    def test_usage_while_off_prints_report_saved_at_last_sync(self):
        path = self._install("abc", prompts=[ON_AT + ONE_DAY])
        self._turn_on_and_sync()
        self.state.delete_on_flag()
        self._write(path, [])  # the source changes after the last sync; an off command must not read it again

        self.assertEqual(self._text(), _report("abc"))
        self.assertEqual(self._text("abc"), _report("abc"))
        self.assertEqual(self._text("ab"), _report("abc"))
        self.assertEqual(self.sync.calls, 0)
        self.assertEqual(sorted(self.archive.reports), ["/logs/abc.md"])

    def test_an_archived_session_whose_source_is_gone_still_resolves_by_id_and_prefix(self):
        path = self._install("abc", prompts=[ON_AT + ONE_DAY])
        self._turn_on_and_sync()
        self.state.delete_on_flag()
        os.unlink(path)
        self.assertEqual(self._text("abc"), _report("abc"))
        self.assertEqual(self._text("ab"), _report("abc"))


class EntryTest(SandboxCase):
    """The entry script: `usage <id>` is an id target, `--latest` and no argument are the same, a failed lookup exits 1."""

    def test_entry_usage_targets_resolve_ids_and_exit_1_when_nothing_or_several_match(self):
        self.write_transcript(session="abc123")
        self.write_transcript(session="abd456")
        self.turn_logging_on()

        _, latest, _ = self.run_main(["claude", "usage", "--latest"])
        self.assertEqual((0, latest), self.run_main(["claude", "usage"])[:2])
        by_prefix = self.run_main(["claude", "usage", "abc"])
        self.assertEqual((0, ""), (by_prefix[0], by_prefix[2]))
        self.assertIn("abc123", by_prefix[1])
        self.assertEqual(by_prefix, self.run_main(["claude", "usage", "abc123"]))
        self.assertNotEqual(by_prefix[1], latest)  # the newest prompt is abd456's; an id is not --latest
        for target, message in (("ab", "session-log: the id prefix 'ab' matches 2 sessions: abc123, abd456\n"),
                                ("latest", "session-log: no session has the id 'latest'\n"),
                                ("nope", "session-log: no session has the id 'nope'\n")):
            self.assertEqual((1, "", message), self.run_main(["claude", "usage", target]))

    def test_entry_usage_in_a_project_without_sessions_exits_1_with_a_message(self):
        self.write_transcript()
        self.turn_logging_on()
        code, stdout, stderr = self.run_main(["claude", "usage"], get_cwd=lambda: EMPTY_PROJECT)
        self.assertEqual((1, ""), (code, stdout))
        self.assertEqual("session-log: no session for this project (%s)\n" % EMPTY_PROJECT, stderr)

    def test_entry_usage_while_off_prints_the_saved_report_and_a_target_needs_no_cwd(self):
        self.write_transcript(session="abc123")
        self.turn_logging_on()
        _, saved, _ = self.run_main(["claude", "usage"])
        self.run_main(["claude", "off"])

        def deleted_cwd():
            raise FileNotFoundError("the current folder was deleted")

        self.assertEqual((0, saved, ""), self.run_main(["claude", "usage", "abc"], get_cwd=deleted_cwd))
        self.assertEqual((0, saved, ""), self.run_main(["claude", "usage"]))


if __name__ == "__main__":
    unittest.main()
