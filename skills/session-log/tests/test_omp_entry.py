"""`session_log.py omp ...` (S10, S42, S47 end to end through sync): the entry script runs the OMP reader over `~/.omp/agent`, with the
injected clock before the fixture's times unless a test says otherwise. The gzip files are generated here from text; none is committed."""
import io
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import session_log
from sessionlog.reader_port import PARENT_CYCLE_REASON
from tests.omp_records import (CHILD_FIXTURE, CWD, FIXTURE_CHILD_ID, FIXTURE_ROOT_ID, FIXTURE_ROOT_STEM, PROJECT, ROOT_FIXTURE, assistant_record,
                               header_record, jsonl, read_text, usage_of, user_record)
from tests.store_fakes import OmpOpenCounter, write_gzip

FIXED_NOW = datetime(2000, 1, 1, tzinfo=timezone.utc)
FIXTURE_TIME = datetime(2026, 10, 13, 8, 43, 0, tzinfo=timezone.utc)
ONE_SECOND_NS = 1_000_000_000
PROMPT_ONE_TOKENS = "input: 10, output: 160, cache_create: 200, cache_read: 2200, total_tokens: 2570"
PROMPT_TWO_TOKENS = "input: 10, output: 83, cache_create: 100, cache_read: 1500, total_tokens: 1693"
CHILD_TOKENS = "input: 50, output: 20, cache_create: 0, cache_read: 400, total_tokens: 470"
TOTAL_TOKENS = "input: 70, output: 263, cache_create: 300, cache_read: 4100, total_tokens: 4733"
SUPPORTED_HARNESSES = "(supported: claude, codex, omp, opencode)"


class OmpEntryTestCase(unittest.TestCase):
    """An OMP agent folder in a temporary home; a test installs the fixture root session and its child, or its own files."""

    NOW = FIXED_NOW

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        self.omp = os.path.join(self.home, ".omp", "agent")
        self.log_root = os.path.join(self.omp, "prompt-logs")
        self.sessions = os.path.join(self.omp, "sessions", PROJECT)
        self.archive = os.path.join(self.omp, "archive", "sessions", PROJECT)
        os.makedirs(self.sessions)

    def run_main(self, argv, now=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        environment = session_log.Environment(stdout=stdout, stderr=stderr, home=self.home, get_cwd=lambda: CWD,
                                              now=lambda: now or self.NOW, to_local=lambda moment: moment.astimezone(timezone.utc))
        return session_log.main(argv, environment), stdout.getvalue(), stderr.getvalue()

    def archived_files(self, suffix):
        return sorted(os.path.join(folder, name) for folder, _, names in os.walk(self.log_root) for name in names if name.endswith(suffix))

    def install(self, *names, text, mtime=FIXTURE_TIME, archived=False):
        """Writes the session file made of `names` (folders below the project folder, then the stem) with `mtime`, plain or gzipped."""
        folder = self.archive if archived else self.sessions
        path = os.path.join(folder, *names[:-1], names[-1] + (".jsonl.gz" if archived else ".jsonl"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mtime_ns = int(mtime.timestamp()) * ONE_SECOND_NS
        if archived:
            write_gzip(path, text, mtime_ns)
        else:
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
            os.utime(path, ns=(mtime_ns, mtime_ns))
        return path

    def install_fixtures(self, **kwargs):
        root = self.install(FIXTURE_ROOT_STEM, text=read_text(ROOT_FIXTURE), **kwargs)
        child = self.install(FIXTURE_ROOT_STEM, "ChildTask", text=read_text(CHILD_FIXTURE), **kwargs)
        return root, child

    def parent_of(self, *names):
        """The `parentSession` OMP writes for a session: the plain path where its parent was created."""
        return os.path.join(self.sessions, *names[:-1], names[-1] + ".jsonl")

    def sync_state(self):
        return json.loads(read_text(os.path.join(self.log_root, ".sync.json")))

    def archived_texts(self, suffix, session_id):
        (path,) = [path for path in self.archived_files(suffix) if session_id in os.path.basename(path)]
        return read_text(path)


class OmpEntryTest(OmpEntryTestCase):
    def setUp(self):
        super().setUp()
        self.root_path, self.child_path = self.install_fixtures()

    def test_on_archives_the_session_as_a_log_with_prompts_and_replies_and_a_report_with_recorded_cost_tokens_model_and_effort(self):
        code, _, stderr = self.run_main(["omp", "on"])
        self.assertEqual((0, ""), (code, stderr))
        (log_path,) = self.archived_files(".md")
        (report_path,) = self.archived_files(".usage.txt")
        log, report = read_text(log_path), read_text(report_path)
        self.assertIn(FIXTURE_ROOT_ID, os.path.basename(log_path))
        self.assertTrue(log.startswith("# Session log\n- harness: omp\n- session: %s\n- project: %s\n" % (FIXTURE_ROOT_ID, CWD)), log)
        for text in ("## 2026-10-13 08:42:10 UTC\nfirst prompt\n", "### 2026-10-13 08:42:25 UTC response\nfirst reply\n",
                     "## 2026-10-13 08:42:31 UTC\nskill prompt text\n", "### 2026-10-13 08:42:40 UTC response\nsecond reply\n"):
            self.assertIn(text, log)
        self.assertNotIn("not a prompt", log)
        self.assertNotIn("placeholder task", log)  # the task text handed to a sub-agent is no prompt
        self.assertNotIn("(parent not found)", log)
        for tokens in (PROMPT_ONE_TOKENS, PROMPT_TWO_TOKENS, CHILD_TOKENS, TOTAL_TOKENS):
            self.assertIn(tokens, report)
        self.assertIn("%s, price: $0.03, model: anthropic/claude-opus-5-5, effort: medium" % PROMPT_ONE_TOKENS, report)
        self.assertIn("price: $0.03, model: anthropic/claude-haiku-5+anthropic/claude-opus-5-5, effort: high", report)
        self.assertIn("sub-agent: task (%s), working time: 00:00:07" % FIXTURE_CHILD_ID, report)
        self.assertIn("TOTAL (2 requests, 1 sub-agents)", report)

    def test_status_then_usage_report_the_archived_session(self):
        self.run_main(["omp", "on"])
        code, stdout, stderr = self.run_main(["omp", "status"])
        self.assertEqual((0, ""), (code, stderr))
        self.assertIn("Archived sessions: 1", stdout)
        code, stdout, stderr = self.run_main(["omp", "usage"])
        self.assertEqual((0, ""), (code, stderr))
        self.assertIn(TOTAL_TOKENS, stdout)
        self.assertEqual(read_text(self.archived_files(".usage.txt")[0]), stdout)

    def test_usage_of_a_session_never_archived_is_read_from_the_store_and_nothing_is_written(self):
        code, stdout, stderr = self.run_main(["omp", "usage", FIXTURE_ROOT_ID[:8]])
        self.assertEqual((0, ""), (code, stderr))
        self.assertIn(TOTAL_TOKENS, stdout)
        self.assertFalse(os.path.exists(self.log_root))

    def test_an_omp_command_reads_only_the_omp_root_and_writes_under_it(self):
        project = os.path.join(self.home, ".claude", "projects", "-work-proj")
        os.makedirs(project)
        with open(os.path.join(project, "abc.jsonl"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "user", "promptSource": "typed", "cwd": CWD, "timestamp": "2026-09-12T08:00:00.000Z",
                                     "message": {"role": "user", "content": "claude prompt"}}) + "\n")
        self.run_main(["omp", "on"])
        self.assertEqual([".claude", ".omp"], sorted(os.listdir(self.home)))
        self.assertEqual(["projects"], os.listdir(os.path.join(self.home, ".claude")))
        self.assertNotIn("claude prompt", "".join(read_text(path) for path in self.archived_files(".md")))
        self.assertTrue(os.path.isdir(self.log_root))

    def test_an_unsupported_harness_exits_2_and_the_supported_list_names_omp(self):
        code, stdout, stderr = self.run_main(["bogus", "on"])
        self.assertEqual((2, ""), (code, stdout))
        self.assertIn(SUPPORTED_HARNESSES, stderr)

    def test_omp_has_no_hook_so_an_omp_hook_call_syncs_nothing(self):
        self.run_main(["omp", "on"])
        self.install("2026-10-13T08-50-00-000Z_later", text=jsonl(header_record("later"), user_record(10, "later")))
        before = (read_text(os.path.join(self.log_root, ".sync.json")), self.archived_files(".md"))
        stdout, stderr = io.StringIO(), io.StringIO()
        environment = session_log.Environment(stdout=stdout, stderr=stderr, home=self.home, now=lambda: FIXED_NOW)
        self.assertEqual(0, session_log.main(["hook", "omp", "SessionStart"], environment))
        self.assertEqual(("", "", before), (stdout.getvalue(), stderr.getvalue(), (read_text(os.path.join(self.log_root, ".sync.json")), self.archived_files(".md"))))


class OmpArchiveEntryTest(OmpEntryTestCase):
    def test_a_session_gzipped_into_archive_sessions_with_its_child_is_archived_like_a_plain_one(self):
        self.install_fixtures(archived=True)
        plain_stem = "2026-10-13T08-50-00-000Z_01a10000-0000-7000-8000-0000000000dd"
        self.install(plain_stem, text=jsonl(header_record("01a10000-0000-7000-8000-0000000000dd"), user_record(10, "plain ask")))
        self.assertEqual(0, self.run_main(["omp", "on"])[0])
        self.assertEqual(2, len(self.archived_files(".md")))
        self.assertIn("## 2026-10-13 08:42:10 UTC\nfirst prompt\n", self.archived_texts(".md", FIXTURE_ROOT_ID))
        report = self.archived_texts(".usage.txt", FIXTURE_ROOT_ID)
        for tokens in (PROMPT_ONE_TOKENS, PROMPT_TWO_TOKENS, CHILD_TOKENS, TOTAL_TOKENS):
            self.assertIn(tokens, report)
        self.assertIn("plain ask", self.archived_texts(".md", "0000000000dd"))

    def test_a_family_without_its_root_is_archived_alone_as_its_first_asking_child_labelled_with_the_parent_and_the_others_as_sub_agents(self):
        gone_stem = "2026-10-13T08-30-00-000Z_01a10000-0000-7000-8000-0000000000aa"
        gone_id = "01a10000-0000-7000-8000-0000000000aa"
        self.install(gone_stem, "Asked", text=jsonl(header_record("asked", parent=self.parent_of(gone_stem), cwd="/work/orphan"), user_record(12, "orphan prompt"),
                                                    assistant_record(13, "orphan reply", usage=usage_of(2, 2, cost=0.5), done=1)))
        self.install(gone_stem, "Asked", "Leaf", text=jsonl(header_record("leaf", parent=self.parent_of(gone_stem, "Asked")), assistant_record(16, usage=usage_of(3, 3, cost=0.25))))
        self.assertEqual(0, self.run_main(["omp", "on"])[0])
        log, report = self.archived_texts(".md", gone_id), self.archived_texts(".usage.txt", gone_id)
        self.assertIn("- project: /work/orphan\n- start: 2026-10-13 08:42:12 UTC\n- sub-agent of %s (parent not found)\n" % gone_id, log)
        self.assertIn("## 2026-10-13 08:42:12 UTC\norphan prompt\n\n### 2026-10-13 08:42:14 UTC response\norphan reply\n", log)
        self.assertIn("sub-agent: unknown (leaf)", report)
        self.assertIn("TOTAL (1 requests, 1 sub-agents)", report)

class OmpCycleEntryTest(OmpEntryTestCase):
    def test_a_cyclic_pair_in_a_family_is_listed_unreadable_in_status_and_not_archived_while_the_rest_of_its_family_is(self):
        stem = "2026-10-13T08-31-00-000Z_01a10000-0000-7000-8000-0000000000bb"
        self.install(stem, text=jsonl(header_record("01a10000-0000-7000-8000-0000000000bb"), user_record(10, "cycle ask")))
        first = self.install(stem, "A", text=jsonl(header_record("a", parent=self.parent_of(stem, "B")), assistant_record(13, usage=usage_of(1, 1))))
        second = self.install(stem, "B", text=jsonl(header_record("b", parent=self.parent_of(stem, "A")), assistant_record(14, usage=usage_of(1, 1))))
        self.assertEqual(0, self.run_main(["omp", "on"])[0])
        self.assertIn("cycle ask", self.archived_texts(".md", "0000000000bb"))
        code, stdout, stderr = self.run_main(["omp", "status"])
        self.assertEqual((0, ""), (code, stderr))
        for path in (first, second):
            self.assertIn("  %s: %s\n" % (path, PARENT_CYCLE_REASON), stdout)
        report = self.archived_texts(".usage.txt", "0000000000bb")
        self.assertNotIn("sub-agent: unknown (a)", report)
        self.assertNotIn("sub-agent: unknown (b)", report)

    def test_two_sessions_whose_parent_sessions_point_at_each_other_are_listed_unreadable_and_not_archived(self):
        """S42: two root sessions, one of them gzipped by `omp gc`, each naming the other as its parent."""
        first_stem = "2026-10-13T08-31-00-000Z_01a10000-0000-7000-8000-0000000000cc"
        second_stem = "2026-10-13T08-32-00-000Z_01a10000-0000-7000-8000-0000000000ee"
        first = self.install(first_stem, text=jsonl(header_record("01a10000-0000-7000-8000-0000000000cc", parent=self.parent_of(second_stem)), user_record(10, "first ask")))
        second = self.install(second_stem, text=jsonl(header_record("01a10000-0000-7000-8000-0000000000ee", parent=self.parent_of(first_stem)), user_record(10, "second ask")),
                              archived=True)
        hanging_stem = "2026-10-13T08-33-00-000Z_01a10000-0000-7000-8000-0000000000ff"
        hanging = self.install(hanging_stem, text=jsonl(header_record("01a10000-0000-7000-8000-0000000000ff", parent=self.parent_of(first_stem)), user_record(10, "hanging ask")))
        fine_stem = "2026-10-13T08-34-00-000Z_01a10000-0000-7000-8000-0000000000dd"
        self.install(fine_stem, text=jsonl(header_record("01a10000-0000-7000-8000-0000000000dd"), user_record(10, "fine ask")))
        self.assertEqual(0, self.run_main(["omp", "on"])[0])
        code, stdout, stderr = self.run_main(["omp", "status"])
        self.assertEqual((0, ""), (code, stderr))
        for path in (first, second, hanging):  # the pair, and the session that hangs from it
            self.assertIn("  %s: %s\n" % (path, PARENT_CYCLE_REASON), stdout)
        (log_path,) = self.archived_files(".md")
        self.assertIn("fine ask", read_text(log_path))


class OmpGcSyncTest(OmpEntryTestCase):
    """What `omp gc --apply` does to a session that had no new turn (S47): it gzips the file into `archive/sessions/<same relative path>.gz`
    with a new modification time, so the unit is new under a new key. The clock is set per test."""

    LATER = datetime(2026, 11, 2, tzinfo=timezone.utc)
    GC_TIME = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)

    def gc(self, plain_path):
        """Moves one session file into the archive as `.jsonl.gz`, as gc does; returns the new path."""
        relative = os.path.relpath(plain_path, os.path.join(self.omp, "sessions"))
        target = os.path.join(self.omp, "archive", "sessions", relative + ".gz")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        write_gzip(target, read_text(plain_path), int(self.GC_TIME.timestamp()) * ONE_SECOND_NS)
        os.remove(plain_path)
        return target

    def snapshot(self):
        return {os.path.relpath(path, self.log_root): read_text(path) for path in self.archived_files(".md") + self.archived_files(".usage.txt")}

    def test_omp_gc_moved_file_read_once_found_inactive_not_read_again(self):
        """Every record is older than the on-time, so the session is inactive: the moved files are read once, found inactive, never again."""
        self.NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)
        root, child = self.install_fixtures()
        with OmpOpenCounter() as opened:
            self.assertEqual(0, self.run_main(["omp", "on"])[0])
            self.assertEqual(([], {}), (self.archived_files(".md"), dict(opened)))  # the old files are inactive by their file time: not read at all

            moved_root, moved_child = self.gc(root), self.gc(child)
            self.assertEqual(0, self.run_main(["omp", "status"], now=self.LATER)[0])
            self.assertEqual({moved_root: 1, moved_child: 1}, dict(opened))  # each moved file read once, none learned from its content
            units = self.sync_state()["units"]
            self.assertEqual(["inactive", "inactive"], [units[moved_root]["state"], units[moved_child]["state"]])
            self.assertTrue(all("fingerprint" in units[key] for key in (moved_root, moved_child)))
            self.assertEqual([], self.archived_files(".md"))

            self.assertEqual(0, self.run_main(["omp", "status"], now=self.LATER + timedelta(hours=1))[0])
            self.assertEqual({moved_root: 1, moved_child: 1}, dict(opened))  # found inactive: not read again
        self.assertEqual([], self.archived_files(".md"))

    def test_an_archived_session_moved_by_gc_without_a_new_turn_is_read_once_and_then_left_alone(self):
        """An active session: its moved file is a new unit, so its session is read once more, with the child it did not move, and written the same."""
        root, child = self.install_fixtures()
        with OmpOpenCounter() as opened:
            self.assertEqual(0, self.run_main(["omp", "on"])[0])
            before_files, before_state = self.snapshot(), self.sync_state()
            self.assertEqual(2, len(before_files))
            self.assertEqual({root: 1, child: 1}, dict(opened))  # read once each: the session is learned from the path, not the content

            moved_root = self.gc(root)
            opened.clear()
            self.assertEqual(0, self.run_main(["omp", "status"], now=self.LATER)[0])
            self.assertEqual({moved_root: 1, child: 1}, dict(opened))
            self.assertEqual(before_files, self.snapshot())
            self.assertEqual({moved_root, child}, set(self.sync_state()["units"]) & {moved_root, child})

            opened.clear()
            self.assertEqual(0, self.run_main(["omp", "status"], now=self.LATER + timedelta(hours=1))[0])
            self.assertEqual({}, dict(opened))
        self.assertEqual(before_files, self.snapshot())
        self.assertEqual(before_state["sessions"].keys(), self.sync_state()["sessions"].keys())


if __name__ == "__main__":
    unittest.main()
