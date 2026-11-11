"""`collect <file.md> --harness <h> [--session-log <path>] [--session-dir <folder>] [--accept <hashes>]` (C3), what `/estimate <file.md>`
runs after it saved the size: everything the project knows about the feature that lists the file is refreshed in its stored file.
The plan's counts and the status follow the ticked tasks, the commits are those that change the feature's docs plus the recorded
and accepted ones that no other feature claims (their lines are counted from git), related files are read from the docs, and the
sessions that worked on the feature (every session of the project since it began, by the `match-sessions` rule over
every stored feature, plus the latest one of the session folder) are attached with their usage. A session is only ever added
to or refreshed, never dropped or shrunk, and a prompt that several features share is split equally between them."""
import os
import unittest

from estimation.features import derive_usage
from tests.support import (EXIT_BAD_ARGUMENTS, EXIT_OK, EXIT_RUNTIME_ERROR, FEATURES_PATH, ProjectTestCase, agent_row, document,
                           list_entry, listed_with_rows, new_feature, prompt_row, tokens_of, usage_payload)

HARNESSES = ("claude", "codex", "cursor", "omp", "opencode")
BRIEF = "docs/x-brief.md"
PLAN = "docs/x-tasks.md"
OTHER_BRIEF = "docs/y-brief.md"
OTHER_PLAN = "docs/y-tasks.md"
TASKS_OPEN = """## Task Breakdown

### Phase 1 · Core
- [ ] **BE-1** — Store the set #backend-role
    - Entity · 2.0h
- [ ] **BE-2** — Render the set #backend-role
    - Presentation · 3.0h
"""
TASKS_ONE_DONE = TASKS_OPEN.replace("- [ ] **BE-1**", "- [x] **BE-1**")
TASKS_ALL_DONE = TASKS_ONE_DONE.replace("- [ ] **BE-2**", "- [x] **BE-2**")
TEN_MINUTES = 600
LATEST = "claude usage --latest --json"
LATEST_FAILED = "session-log claude usage --latest failed (exit 1): session-log: no session of this project"


def at(clock):
    return "2026-10-01T%s+00:00" % clock


def listing(*docs, files=(), commits=()):
    return {"docs": list(docs), "files": list(files), "commits": list(commits), "sessions": []}


def recorded(session_id, numbers=(), minutes=3, harness="claude"):
    """A stored session entry counting the prompts `numbers`."""
    return {"harness": harness, "models": None, "id": session_id, "path": None, "log": None,
            "prompts": [{"number": number, "at": at("09:00:00"), "text": "t", "reason": "names " + BRIEF} for number in numbers],
            "agentTimeMinutes": minutes, "tokens": tokens_of(100), "costUsd": 1.0}


def stored_feature(*sessions, matched_by="prompt"):
    """The feature X listing the brief with `sessions` stored and the usage derived from them."""
    feature = new_feature("X", sources={**listing(BRIEF), "sessions": list(sessions)})
    derive_usage(feature, matched_by)
    return feature


class CollectTestCase(ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.docs_commit = self.commit("docs", {PLAN: TASKS_OPEN, BRIEF: "# X\n"}, when=at("08:00:00"))
        self.session_log = self.fake_session_log()
        self.lists = {harness: [] for harness in HARNESSES}
        for harness in HARNESSES:
            self.session_log.reply(harness + " list --json", [])
        self.session_log.reply(LATEST, None, code=1, stderr="session-log: no session of this project\n")

    def seed_feature(self, *others, **fields):
        fields.setdefault("sources", listing(BRIEF, PLAN))
        self.seed(document(new_feature("X", storyPoints=5, estimatedAt="2026-10-01", **fields), *others))

    def session(self, harness, session_id, rows, path=None, agents=()):
        """Lists a session with prompt rows `rows` and answers its `usage --json` with them."""
        self.lists[harness].append(listed_with_rows(harness, session_id, rows, path))
        self.session_log.reply(harness + " list --json", self.lists[harness])
        self.session_log.reply("%s usage %s --json" % (harness, session_id),
                               usage_payload(harness, session_id, 0, path=path, prompts=list(rows), sub_agents=list(agents)))

    def latest(self, rows, session_id="s1", agents=(), **keywords):
        self.session_log.reply(LATEST, usage_payload("claude", session_id, 0, path="/abs/%s.jsonl" % session_id, prompts=list(rows),
                                                     sub_agents=list(agents), **keywords))

    def run_collect(self, doc=PLAN, *extra, cwd=None):
        return self.run_estimate("collect", doc, "--harness", "claude", "--session-log", self.session_log.path, *extra, cwd=cwd)

    def collect(self, doc=PLAN, *extra, cwd=None):
        result = self.run_collect(doc, *extra, cwd=cwd)
        self.assertEqual(EXIT_OK, result.code, result.err)
        return result.json

    def usage_calls(self):
        return [call["argv"] for call in self.session_log.calls() if call["argv"][1] == "usage" and call["argv"][2] != "--latest"]


class CommitsAndPlanTest(CollectTestCase):
    def test_the_commits_that_change_the_docs_are_counted_with_their_lines_and_the_plan_is_read_from_the_tasks_file(self):
        self.seed_feature()
        c1 = self.commit("BE-1 store", {"src/store.py": "a\nb\nc\n", PLAN: TASKS_ONE_DONE}, when=at("11:00:00"))
        output = self.collect()
        feature = output["feature"]
        # The docs commit adds the tasks file (7 lines) and the brief (1); store.py is +3 and ticking BE-1 is +1 -1.
        self.assertEqual([self.docs_commit, c1], feature["sources"]["commits"])
        self.assertEqual({"commits": 2, "linesAdded": 12, "linesRemoved": 1}, feature["git"])
        self.assertEqual({"phases": 1, "tasks": 2, "estimatedHours": 5.0}, feature["plan"])
        self.assertEqual("in-progress", feature["status"])
        self.assertEqual({"feature": feature, "needsResize": False, "candidates": []}, {key: output[key] for key in
                                                                                      ("feature", "needsResize", "candidates")})
        self.assertIn(LATEST_FAILED, output["missing"])
        self.assertEqual(feature, self.feature("X"))

    def test_a_feature_with_no_ticked_task_stays_estimated_and_the_ticks_start_and_deliver_it(self):
        self.seed_feature()
        self.assertEqual("estimated", self.collect()["feature"]["status"])
        self.commit("BE-1", {PLAN: TASKS_ONE_DONE}, when=at("11:00:00"))
        started = self.collect()
        self.assertEqual(("in-progress", False), (started["feature"]["status"], started["needsResize"]))
        self.commit("BE-2", {PLAN: TASKS_ALL_DONE}, when=at("11:30:00"))
        delivered = self.collect()
        self.assertEqual(("delivered", True, 5), (delivered["feature"]["status"], delivered["needsResize"],
                                                  delivered["feature"]["storyPoints"]))

    def test_a_delivered_feature_with_an_after_delivery_size_needs_no_resize(self):
        self.seed_feature(updatedStoryPoints=8)
        self.commit("all", {PLAN: TASKS_ALL_DONE}, when=at("11:00:00"))
        output = self.collect()
        self.assertEqual(("delivered", False), (output["feature"]["status"], output["needsResize"]))

    def test_counts_the_document_cannot_give_keep_the_known_counts_and_the_status(self):
        self.seed_feature(plan={"phases": 1, "tasks": 2, "estimatedHours": 5.0}, status="in-progress")
        self.commit("rewrite", {PLAN: "# X tasks\n\nNo task section any more.\n"}, when=at("11:00:00"))
        feature = self.collect()["feature"]
        self.assertEqual(({"phases": 1, "tasks": 2, "estimatedHours": 5.0}, "in-progress"), (feature["plan"], feature["status"]))

    def test_a_commit_that_two_features_claim_counts_for_none(self):
        self.seed_feature(new_feature("Y", sources=listing(BRIEF)))
        result = self.run_collect()
        self.assertEqual(EXIT_OK, result.code, result.err)
        self.assertEqual([], result.json["feature"]["sources"]["commits"])
        self.assertIn("commits that several features claim count for none: %s\n" % self.docs_commit[:8], result.err)

    def test_a_recorded_commit_git_no_longer_holds_is_dropped_and_named(self):
        gone = "e" * 40
        self.seed_feature(sources=listing(BRIEF, PLAN, commits=[gone]))
        output = self.collect()
        self.assertEqual([self.docs_commit], output["feature"]["sources"]["commits"])
        self.assertIn("X: 1 recorded commits are no longer in git and were dropped: eeeeeeee", output["missing"])

    def test_a_recorded_commit_that_changes_another_features_doc_is_shared(self):
        for doc in (OTHER_BRIEF, 'docs/y "odd" brief.md'):  # git quotes the second name in its plain listings
            with self.subTest(doc=doc):
                other = self.commit("y doc", {doc: "# Y\n"}, when=at("09:00:00"))
                self.seed_feature(new_feature("Y", sources=listing(doc)), sources=listing(BRIEF, PLAN, commits=[other]))
                result = self.run_collect()
                self.assertEqual(EXIT_OK, result.code, result.err)
                self.assertEqual([self.docs_commit], result.json["feature"]["sources"]["commits"])
                self.assertIn("commits that several features claim count for none: %s\n" % other[:8], result.err)

    def test_only_the_collected_features_shared_dropped_and_uncovered_commits_are_reported(self):
        shared = self.commit("y doc", {OTHER_BRIEF: "# Y\n"}, when=at("09:00:00"))
        work = self.commit("y work", {"src/y.py": "y\n"}, when=at("09:10:00"))
        self.seed_feature(new_feature("Y", sources=listing(OTHER_BRIEF, commits=[shared, work, "e" * 40])),
                          new_feature("Z", sources=listing(OTHER_BRIEF, commits=[shared])))
        result = self.run_collect()
        self.assertEqual(EXIT_OK, result.code, result.err)
        self.assertNotIn("count for none", result.err)
        self.assertEqual([], [line for line in result.json["missing"] if line.startswith(("Y:", "Z:"))])

    def test_a_recorded_commit_of_the_feature_is_kept_though_it_changes_no_doc(self):
        c1 = self.commit("work", {"src/store.py": "a\n"}, when=at("11:00:00"))
        self.seed_feature(sources=listing(BRIEF, PLAN, commits=[c1]))
        self.assertEqual([self.docs_commit, c1], self.collect()["feature"]["sources"]["commits"])

    def test_only_the_features_own_file_is_written(self):
        self.seed_feature(new_feature("Y", sources=listing(OTHER_PLAN)))
        other = self.path(FEATURES_PATH + "/" + new_feature("Y")["file"])
        with open(other, encoding="utf-8") as handle:
            before = handle.read()
        self.collect()
        with open(other, encoding="utf-8") as handle:
            self.assertEqual(before, handle.read())

    def test_the_duplicates_of_the_other_files_are_named(self):
        self.seed_feature(new_feature("Y", file="2026-10-01-y.json"), new_feature("Y", file="2026-10-02-y.json"))
        result = self.run_collect()
        self.assertEqual(EXIT_OK, result.code, result.err)
        self.assertIn("estimate: duplicate name 'Y': 2026-10-01-y.json, 2026-10-02-y.json\n", result.err)

    def test_the_doc_path_is_relative_to_the_working_folder(self):
        self.seed_feature()
        self.assertEqual([self.docs_commit], self.collect("x-tasks.md", cwd=self.path("docs"))["feature"]["sources"]["commits"])

    def test_a_file_no_feature_lists_is_refused(self):
        self.seed(document(new_feature("Other")))
        self.assert_error(self.run_collect(), EXIT_RUNTIME_ERROR, "no feature lists docs/x-tasks.md; run /estimate docs/x-tasks.md first")

    def test_a_file_two_features_list_is_refused_naming_both(self):
        self.seed(document(new_feature("X", file="2026-10-01-x.json", sources=listing(PLAN)),
                           new_feature("Y", file="2026-10-02-y.json", sources=listing(PLAN))))
        self.assert_error(self.run_collect(), EXIT_RUNTIME_ERROR,
                          "2 features list docs/x-tasks.md: 2026-10-01-x.json, 2026-10-02-y.json")

    def test_a_feature_saved_before_effort_and_the_source_lists_still_collects(self):
        old = new_feature("X", storyPoints=5, sources={"docs": [PLAN], "commits": [], "sessions": []})
        del old["effort"]
        self.seed(document(old))
        feature = self.collect()["feature"]
        self.assertEqual(([self.docs_commit], []), (feature["sources"]["commits"], feature["sources"]["files"]))
        self.assertNotIn("effort", self.feature("X"))

    def test_a_project_that_is_no_git_repository_collects_no_commits_and_says_so(self):
        self.root = os.path.join(self.base, "plain")
        self.write(PLAN, TASKS_ONE_DONE)
        self.seed_feature()
        output = self.collect()
        self.assertEqual(([], {"phases": 1, "tasks": 2, "estimatedHours": 5.0}, "in-progress"),
                         (output["feature"]["sources"]["commits"], output["feature"]["plan"], output["feature"]["status"]))
        self.assertIn("not a git repository: no commits collected", output["missing"])


class DocReadingTest(CollectTestCase):
    def test_the_code_files_the_docs_link_are_the_related_files(self):
        self.commit("code", {"src/store.py": "a\n", "src/render.py": "r\n"}, when=at("08:30:00"))
        links = ("# X\n\n## References\n- [store](../src/store.py) and [again](../src/store.py#L3)\n- [spec](./spec.md)\n"
                 "- [site](https://example.com/a.py)\n- [anchor](#top)\n- [gone](../src/none.py)\n- [render](../src/render.py)\n"
                 "- [plan](x-tasks.md)\n")
        self.commit("links", {BRIEF: links}, when=at("08:40:00"))
        self.seed_feature()
        self.assertEqual(["src/store.py", "src/render.py"], self.collect(BRIEF)["feature"]["sources"]["files"])

    def test_collecting_one_doc_reads_the_links_of_the_others_the_feature_lists(self):
        self.commit("code", {"src/store.py": "a\n"}, when=at("08:30:00"))
        self.commit("links", {BRIEF: "# X\n\n- [store](../src/store.py)\n"}, when=at("08:40:00"))
        self.seed_feature(sources=listing(BRIEF, PLAN, "docs/archived-plan.md"))
        self.assertEqual(["src/store.py"], self.collect(PLAN)["feature"]["sources"]["files"])

    def test_a_related_file_is_no_evidence_for_a_prompt_that_names_or_writes_it(self):
        self.seed_feature(sources=listing(BRIEF, PLAN, files=["src/store.py"]))
        self.session("omp", "s1", [prompt_row(at("10:00:00"), "look at src/store.py", written=["/abs/project/src/store.py"],
                                              seconds=TEN_MINUTES)])
        self.assertEqual([], self.collect()["feature"]["sources"]["sessions"])

    def test_a_task_id_in_a_prompt_is_no_evidence(self):
        self.seed_feature()
        self.session("claude", "s1", [prompt_row(at("10:00:00"), "please do BE-2 now", seconds=TEN_MINUTES)])
        self.assertEqual([], self.collect()["feature"]["sources"]["sessions"])

    def test_the_related_files_follow_the_docs_as_they_are_now(self):
        self.commit("code", {"src/store.py": "a\n"}, when=at("08:30:00"))
        self.commit("links", {BRIEF: "# X\n\n- [store](../src/store.py)\n"}, when=at("08:40:00"))
        self.seed_feature(sources=listing(BRIEF, PLAN, files=["src/removed.py"]))
        self.assertEqual(["src/store.py"], self.collect(PLAN)["feature"]["sources"]["files"])

    def test_the_stored_related_files_stay_while_a_listed_doc_is_gone(self):
        self.commit("code", {"src/store.py": "a\n"}, when=at("08:30:00"))
        self.commit("links", {BRIEF: "# X\n\n- [store](../src/store.py)\n"}, when=at("08:40:00"))
        self.seed_feature(sources=listing(BRIEF, PLAN, "docs/archived-plan.md", files=["src/archived.py"]))
        self.assertEqual(["src/archived.py", "src/store.py"], self.collect(PLAN)["feature"]["sources"]["files"])


class CandidatesTest(CollectTestCase):
    def test_commits_touching_a_related_file_since_the_feature_began_are_candidates_until_accepted(self):
        before = self.commit("early", {"src/store.py": "a\n"}, when="2026-09-01T10:00:00+00:00")
        links = self.commit("links", {BRIEF: "# X\n\n- [store](../src/store.py)\n"}, when=at("08:40:00"))
        self.seed_feature()
        edit = self.commit("tweak the store", {"src/store.py": "a\nb\n"}, when=at("11:00:00"))
        output = self.collect()
        self.assertEqual([{"hash": edit, "subject": "tweak the store"}], output["candidates"])
        self.assertNotIn(before, [candidate["hash"] for candidate in output["candidates"]])
        self.assertEqual([self.docs_commit, links], output["feature"]["sources"]["commits"])
        accepted = self.collect(PLAN, "--accept", edit)
        self.assertEqual(([], [self.docs_commit, links, edit]), (accepted["candidates"], accepted["feature"]["sources"]["commits"]))
        self.assertEqual(3, accepted["feature"]["git"]["commits"])
        self.assertEqual(accepted["feature"]["sources"]["commits"], self.collect()["feature"]["sources"]["commits"])

    def test_commits_naming_the_features_id_are_candidates(self):
        self.seed_feature(id="PM-12")
        named = self.commit("PM-12: fix the totals", {"src/totals.py": "t\n"}, when=at("11:00:00"))
        self.assertEqual([named], [candidate["hash"] for candidate in self.collect()["candidates"]])

    def test_an_accepted_hash_must_be_a_full_hash(self):
        self.seed_feature()
        self.assert_error(self.run_collect(PLAN, "--accept", self.docs_commit[:10]), EXIT_BAD_ARGUMENTS,
                          "--accept holds '%s', which is not a full commit hash" % self.docs_commit[:10])

    def test_an_accepted_commit_git_does_not_hold_is_dropped_and_named(self):
        self.seed_feature()
        gone = "d" * 40
        output = self.collect(PLAN, "--accept", gone)
        self.assertEqual([self.docs_commit], output["feature"]["sources"]["commits"])
        self.assertIn("X: 1 recorded commits are no longer in git and were dropped: dddddddd", output["missing"])

    def test_several_accepted_hashes_all_count(self):
        self.seed_feature()
        first = self.commit("first", {"src/a.py": "a\n"}, when=at("11:00:00"))
        second = self.commit("second", {"src/b.py": "b\n"}, when=at("11:10:00"))
        output = self.collect(PLAN, "--accept", "%s,%s" % (first, second))
        self.assertEqual([self.docs_commit, first, second], output["feature"]["sources"]["commits"])

    def test_an_accepted_hash_another_feature_claims_does_not_count(self):
        claimed = self.commit("claimed", {"src/a.py": "a\n"}, when=at("11:00:00"))
        self.seed_feature(new_feature("Y", sources=listing(OTHER_PLAN, commits=[claimed])))
        result = self.run_collect(PLAN, "--accept", claimed)
        self.assertEqual(EXIT_OK, result.code, result.err)
        self.assertEqual([self.docs_commit], result.json["feature"]["sources"]["commits"])
        self.assertIn("commits that several features claim count for none: %s\n" % claimed[:8], result.err)


class SessionsTest(CollectTestCase):
    def test_a_session_whose_prompts_name_the_doc_is_attached_with_its_usage(self):
        self.seed_feature()
        self.session("claude", "s1", [prompt_row(at("10:00:00"), "estimate docs/x-brief.md", seconds=TEN_MINUTES)], path="/abs/s1.jsonl")
        feature = self.collect()["feature"]
        self.assertEqual(["s1"], [session["id"] for session in feature["sources"]["sessions"]])
        self.assertEqual(("prompt", 1, 10), (feature["usage"]["matchedBy"], feature["usage"]["sessions"],
                                             feature["usage"]["agentTimeMinutes"]))
        self.assertEqual(feature, self.feature("X"))

    def test_a_prompt_that_wrote_the_doc_without_naming_it_counts_for_the_feature(self):
        self.seed_feature()
        self.session("omp", "writer", [prompt_row(at("10:00:00"), "write the brief", written=["/abs/project/" + BRIEF]),
                                       prompt_row(at("10:30:00"), "check docs/x-brief.md again")])
        self.session("omp", "bystander", [prompt_row(at("11:00:00"), "write something", written=["/abs/project/docs/z.md"])])
        sessions = self.collect()["feature"]["sources"]["sessions"]
        self.assertEqual(["writer"], [session["id"] for session in sessions])
        self.assertEqual(["writes " + BRIEF, "names " + BRIEF], [prompt["reason"] for prompt in sessions[0]["prompts"]])

    def test_without_a_matching_session_no_usage_is_saved_and_it_is_named(self):
        self.seed_feature()
        self.session("claude", "other", [prompt_row(at("10:00:00"), "unrelated")])
        output = self.collect()
        self.assertEqual(([], None), (output["feature"]["sources"]["sessions"], output["feature"]["usage"]))
        self.assertIn("no session names or wrote a document of X: no session attached", output["missing"])

    def test_sessions_already_stored_survive_when_none_match(self):
        self.seed(document(stored_feature(recorded("old"))))
        feature = self.collect(BRIEF)["feature"]
        self.assertEqual(("prompt", ["old"]), (feature["usage"]["matchedBy"], [session["id"] for session in feature["sources"]["sessions"]]))

    def test_a_new_session_is_added_to_the_stored_ones(self):
        self.seed(document(stored_feature(recorded("rotated-out", minutes=3))))
        self.session("claude", "s1", [prompt_row(at("10:00:00"), "docs/x-brief.md", seconds=TEN_MINUTES)])
        feature = self.collect(BRIEF)["feature"]
        self.assertEqual(["rotated-out", "s1"], [session["id"] for session in feature["sources"]["sessions"]])
        self.assertEqual((2, 13), (feature["usage"]["sessions"], feature["usage"]["agentTimeMinutes"]))

    def test_stored_sessions_survive_a_failed_session_log_call(self):
        self.seed(document(stored_feature(recorded("stored-by-codex", harness="codex"))))
        self.session_log.reply("codex list --json", None, code=EXIT_RUNTIME_ERROR, stderr="codex store unreadable")
        self.session("claude", "s1", [prompt_row(at("10:00:00"), "docs/x-brief.md")])
        output = self.collect(BRIEF)
        self.assertEqual([("codex", "stored-by-codex"), ("claude", "s1")],
                         [(session["harness"], session["id"]) for session in self.feature("X")["sources"]["sessions"]])
        self.assertIn("session-log codex list failed (exit 1): codex store unreadable", output["missing"])

    def test_a_stored_session_is_refreshed_when_the_fresh_slice_keeps_every_counted_prompt(self):
        self.seed(document(stored_feature(recorded("s1", numbers=[1], minutes=1))))
        self.session("claude", "s1", [prompt_row(at("10:00:00"), "docs/x-brief.md", seconds=60),
                                      prompt_row(at("10:10:00"), "go", seconds=TEN_MINUTES)])
        self.collect(BRIEF)
        counted = self.feature("X")["sources"]["sessions"]
        self.assertEqual([("s1", [1, 2], 11)], [(session["id"], [prompt["number"] for prompt in session["prompts"]],
                                                 session["agentTimeMinutes"]) for session in counted])

    def test_a_stored_session_is_kept_when_the_fresh_slice_lacks_a_prompt_it_counts(self):
        feature = stored_feature(recorded("s1", numbers=[1, 2, 3], minutes=9), recorded("other", numbers=[1]))
        self.seed(document(feature))
        self.session("claude", "s1", [prompt_row(at("10:00:00"), "docs/x-brief.md", seconds=60)])
        self.collect(BRIEF)
        self.assertEqual(feature["sources"]["sessions"], self.feature("X")["sources"]["sessions"])

    def test_a_stored_whole_session_is_replaced_by_a_fresh_slice(self):
        self.seed(document(stored_feature({**recorded("s1", minutes=30), "prompts": None})))
        self.session("claude", "s1", [prompt_row(at("10:00:00"), "docs/x-brief.md", seconds=TEN_MINUTES)])
        self.collect(BRIEF)
        self.assertEqual([("s1", [1], 10)], [(session["id"], [prompt["number"] for prompt in session["prompts"]], session["agentTimeMinutes"])
                                             for session in self.feature("X")["sources"]["sessions"]])

    def test_a_stored_slice_is_kept_when_the_session_is_only_read_whole_now(self):
        stored = recorded("s1", numbers=[1], minutes=4)
        self.seed(document(stored_feature(stored)))
        self.lists["claude"].append(list_entry("claude", "s1", at("09:00:00"), at("10:00:00"), ["docs/x-brief.md"]))
        self.session_log.reply("claude list --json", self.lists["claude"])
        self.session_log.reply("claude usage s1 --json", usage_payload("claude", "s1", TEN_MINUTES))
        output = self.collect(BRIEF)
        self.assertEqual([stored], self.feature("X")["sources"]["sessions"])
        self.assertIn("session-log claude usage s1 has no prompt rows; the whole session is counted for X", output["missing"])

    def test_a_prompt_still_running_covers_the_commits_made_until_the_sessions_last_activity(self):
        self.seed_feature()
        self.commit("BE-1", {PLAN: TASKS_ONE_DONE}, when=at("11:05:00"))
        rows = [prompt_row(at("07:59:00"), "/implement " + PLAN, seconds=120), prompt_row(at("11:00:00"), "continue", seconds=0)]
        rows[1]["replyAt"] = None
        self.lists["claude"].append(list_entry("claude", "s1", rows[0]["at"], at("11:10:00"), [row["text"] for row in rows]))
        self.session_log.reply("claude list --json", self.lists["claude"])
        self.session_log.reply("claude usage s1 --json", usage_payload("claude", "s1", 0, prompts=rows, sub_agents=[]))
        output = self.collect()
        self.assertEqual([], [line for line in output["missing"] if "no session found" in line])

    def test_a_session_whose_usage_reply_is_no_json_object_is_named(self):
        self.seed_feature()
        self.lists["claude"].append(list_entry("claude", "s1", at("09:00:00"), at("10:00:00"), ["docs/x-brief.md"]))
        self.session_log.reply("claude list --json", self.lists["claude"])
        self.session_log.reply("claude usage s1 --json", "not an object")
        self.assertIn("session-log claude usage s1 failed: its output is not a JSON object", self.collect()["missing"])

    def test_a_session_with_an_empty_prompt_list_is_counted_whole_and_named(self):
        self.seed_feature()
        self.lists["claude"].append(list_entry("claude", "s1", at("09:00:00"), at("10:00:00"), ["docs/x-brief.md"]))
        self.session_log.reply("claude list --json", self.lists["claude"])
        self.session_log.reply("claude usage s1 --json", usage_payload("claude", "s1", TEN_MINUTES, prompts=[], sub_agents=[]))
        output = self.collect()
        self.assertEqual((None, 10), (output["feature"]["sources"]["sessions"][0]["prompts"], output["feature"]["usage"]["agentTimeMinutes"]))
        self.assertIn("session-log claude usage s1 has no prompt rows; the whole session is counted for X", output["missing"])

    def test_sessions_an_earlier_version_recorded_are_kept_and_the_label_follows_the_matching(self):
        feature = stored_feature(recorded("recorded", numbers=[1, 2]), matched_by="implement")
        self.seed(document(feature))
        self.session("claude", "s1", [prompt_row(at("10:00:00"), "estimate docs/x-brief.md")])
        saved = self.collect(BRIEF)["feature"]
        self.assertEqual(["recorded", "s1"], [session["id"] for session in saved["sources"]["sessions"]])
        self.assertEqual("prompt", saved["usage"]["matchedBy"])

    def test_a_doc_named_by_two_features_is_no_evidence_but_the_one_only_one_owns_is(self):
        self.seed(document(new_feature("X", sources=listing(BRIEF, PLAN)), new_feature("Y", sources=listing(BRIEF))))
        self.session("claude", "brief", [prompt_row(at("10:00:00"), "read docs/x-brief.md")])
        self.session("claude", "plan", [prompt_row(at("11:00:00"), "read docs/x-tasks.md")])
        self.assertEqual(["plan"], [session["id"] for session in self.collect(PLAN)["feature"]["sources"]["sessions"]])
        self.assertEqual([], self.feature("Y")["sources"]["sessions"])

    def test_another_feature_is_never_written(self):
        self.seed(document(new_feature("X", sources=listing(BRIEF)), new_feature("Y", sources=listing(OTHER_BRIEF))))
        self.session("claude", "s1", [prompt_row(at("10:00:00"), "docs/x-brief.md and docs/y-brief.md")])
        self.collect(BRIEF)
        self.assertEqual(["s1"], [session["id"] for session in self.feature("X")["sources"]["sessions"]])
        self.assertEqual(new_feature("Y", sources=listing(OTHER_BRIEF)), self.feature("Y"))

    def test_a_session_that_wrote_the_doc_unnamed_and_uncommitted_is_found_from_the_first_day_on(self):
        self.seed(document(new_feature("X", sources=listing(BRIEF))))
        self.session("omp", "writer", [prompt_row("2026-09-30T20:00:00+00:00", "go", written=["/abs/project/docs/z.md"]),
                                       prompt_row("2026-10-02T09:00:00+00:00", "write the brief", written=["/abs/project/" + BRIEF])])
        sessions = self.collect(BRIEF)["feature"]["sources"]["sessions"]
        self.assertEqual(["before prompt 2", "writes " + BRIEF], [prompt["reason"] for prompt in sessions[0]["prompts"]])

    def test_a_session_that_ended_before_the_first_day_is_not_read(self):
        self.seed(document(new_feature("X", sources=listing(BRIEF))))
        self.session("omp", "early", [prompt_row("2026-09-25T09:00:00+00:00", "write", written=["/abs/project/" + BRIEF])])
        self.assertEqual([], self.collect(BRIEF)["feature"]["sources"]["sessions"])
        self.assertEqual([], self.usage_calls())

    def test_the_window_opens_at_the_first_day_less_the_timezone_margin_to_the_second(self):
        self.seed(document(new_feature("X", sources=listing(BRIEF))))
        self.session("omp", "inside", [prompt_row("2026-09-30T09:59:00+00:00", "write", seconds=60, written=["/abs/project/" + BRIEF])])
        self.session("omp", "outside", [prompt_row("2026-09-30T09:58:59+00:00", "write", seconds=60, written=["/abs/project/" + BRIEF])])
        self.assertEqual(["inside"], [session["id"] for session in self.collect(BRIEF)["feature"]["sources"]["sessions"]])
        self.assertEqual([["omp", "usage", "inside", "--json"]], self.usage_calls())

    def test_a_doc_added_to_git_before_the_feature_file_day_opens_the_window_earlier(self):
        self.commit("old brief", {OTHER_BRIEF: "# old\n"}, when="2026-09-01T09:00:00+00:00")
        self.seed(document(new_feature("X", sources=listing(OTHER_BRIEF))))
        self.session("omp", "writer", [prompt_row("2026-09-01T08:00:00+00:00", "write", seconds=60, written=["/abs/project/" + OTHER_BRIEF])])
        self.assertEqual(["writer"], [session["id"] for session in self.collect(OTHER_BRIEF)["feature"]["sources"]["sessions"]])

    def test_a_sub_agent_that_wrote_the_doc_makes_the_prompt_it_started_under_count_as_writing_it(self):
        self.seed_feature()
        self.session("omp", "delegator", [prompt_row(at("10:00:00"), "write the brief", seconds=TEN_MINUTES)],
                     agents=[agent_row(at("10:01:00"), written=["/abs/project/" + BRIEF])])
        counted = self.collect()["feature"]["sources"]["sessions"]
        self.assertEqual([["writes " + BRIEF]], [[prompt["reason"] for prompt in session["prompts"]] for session in counted])

    def test_a_recent_session_without_a_start_is_not_attributed_by_commit_times(self):
        self.seed_feature()
        self.lists["claude"].append(list_entry("claude", "no-start", None, at("13:00:00"), ["unrelated"]))
        self.session_log.reply("claude list --json", self.lists["claude"])
        output = self.collect()
        self.assertEqual([], output["feature"]["sources"]["sessions"])
        self.assertTrue(any(line.startswith("session-log claude usage no-start failed") for line in output["missing"]))

    def test_a_second_run_changes_nothing(self):
        self.seed_feature()
        self.session("claude", "s1", [prompt_row(at("10:00:00"), "estimate docs/x-brief.md")])
        first = self.collect()
        stored = self.feature("X")
        self.assertEqual(first, self.collect())
        self.assertEqual(stored, self.feature("X"))
        self.assertEqual(1, self.feature("X")["usage"]["sessions"])

    def test_a_session_found_later_is_added(self):
        self.seed_feature()
        self.session("claude", "s1", [prompt_row(at("10:00:00"), "docs/x-brief.md", seconds=60)])
        self.collect()
        self.session("claude", "s2", [prompt_row(at("11:00:00"), "docs/x-brief.md", seconds=TEN_MINUTES)])
        self.collect()
        self.assertEqual((2, 11), (self.feature("X")["usage"]["sessions"], self.feature("X")["usage"]["agentTimeMinutes"]))

    def test_a_prompt_that_made_a_commit_belongs_to_the_feature_without_naming_it(self):
        self.seed_feature(new_feature("Y", sources=listing(OTHER_PLAN)))
        self.commit("BE-1", {PLAN: TASKS_ONE_DONE}, when=at("11:05:00"))
        self.session("claude", "s1", [prompt_row(at("10:00:00"), "/implement " + OTHER_PLAN, seconds=TEN_MINUTES),
                                      prompt_row(at("11:00:00"), "continue", seconds=TEN_MINUTES)])
        prompts = self.collect()["feature"]["sources"]["sessions"][0]["prompts"]
        self.assertEqual([(2, "made 1 of its commits")], [(prompt["number"], prompt["reason"]) for prompt in prompts])


class LatestSessionTest(CollectTestCase):
    def test_only_the_prompts_of_the_feature_are_taken_from_a_latest_session_that_also_worked_on_another(self):
        self.seed_feature(new_feature("Y", sources=listing(OTHER_PLAN)))
        self.commit("BE-1", {PLAN: TASKS_ONE_DONE}, when=at("11:40:00"))
        self.latest([prompt_row(at("10:00:00"), "/implement " + OTHER_PLAN, seconds=3000, total=4000, cost=4.0),
                     prompt_row(at("11:00:00"), "go", seconds=600, total=400, cost=0.4),
                     prompt_row(at("11:30:00"), "/implement docs/x-tasks.md", seconds=600, total=200, cost=0.2),
                     prompt_row(at("11:45:00"), "A", seconds=60, total=100, cost=0.1)],
                    agents=[agent_row(at("11:31:00"), total=1000, cost=1.0)])
        output = self.collect()
        # Prompts 3-4 (600 + 60 s) and the agent started under prompt 3; prompts 1-2 worked on Y.
        self.assertEqual([{"harness": "claude", "models": ["agent-model", "main-model"], "id": "s1", "path": "/abs/s1.jsonl", "log": None,
                           "prompts": [{"number": 3, "at": at("11:30:00"), "text": "/implement docs/x-tasks.md",
                                        "reason": "names docs/x-tasks.md", "share": 1.0},
                                       {"number": 4, "at": at("11:45:00"), "text": "A", "reason": "follows prompt 3", "share": 1.0}],
                           "agentTimeMinutes": 11, "tokens": tokens_of(1300), "costUsd": 1.3}], output["feature"]["sources"]["sessions"])
        self.assertEqual(["X: no session found for 1 of 2 commits"], output["missing"])  # the docs commit; the latest session ran over c1

    def test_the_session_that_is_listed_and_the_latest_one_are_one_session(self):
        self.seed_feature()
        rows = [prompt_row(at("10:00:00"), "/implement " + PLAN, seconds=TEN_MINUTES)]
        self.session("claude", "s1", rows)
        self.latest(rows)
        self.assertEqual(["s1"], [session["id"] for session in self.collect()["feature"]["sources"]["sessions"]])

    def test_a_latest_session_is_read_again_and_keeps_its_newer_numbers(self):
        self.seed_feature()
        self.latest([prompt_row(at("11:59:00"), "/implement " + PLAN, seconds=TEN_MINUTES)])
        self.collect()
        self.latest([prompt_row(at("11:59:00"), "/implement " + PLAN, seconds=18000)])
        feature = self.collect()["feature"]
        self.assertEqual((1, 300), (feature["usage"]["sessions"], feature["usage"]["agentTimeMinutes"]))

    def test_prompts_before_a_cleared_context_do_not_count_for_the_feature_that_follows_it(self):
        self.seed_feature()
        self.latest([prompt_row(at("10:00:00"), "tidy my notes", seconds=3000, total=4000, cost=4.0),
                     prompt_row(at("11:30:00"), "/implement docs/x-tasks.md", seconds=600, total=200, cost=0.2, after_reset=True),
                     prompt_row(at("11:45:00"), "A", seconds=60, total=100, cost=0.1)])
        feature = self.collect()["feature"]
        self.assertEqual([(2, "names docs/x-tasks.md"), (3, "follows prompt 2")],
                         [(prompt["number"], prompt["reason"]) for prompt in feature["sources"]["sessions"][0]["prompts"]])
        self.assertEqual(11, feature["usage"]["agentTimeMinutes"])

    def test_a_latest_session_without_a_prompt_of_the_feature_adds_no_session(self):
        self.seed_feature(new_feature("Y", sources=listing(OTHER_PLAN)))
        self.latest([prompt_row(at("10:00:00"), "/implement " + OTHER_PLAN, seconds=TEN_MINUTES)])
        output = self.collect()
        self.assertEqual(([], None), (output["feature"]["sources"]["sessions"], output["feature"]["usage"]))
        self.assertIn("the latest claude session s1 shows no prompt of X", output["missing"])

    def test_a_latest_session_without_prompt_rows_is_attached_whole_and_named(self):
        self.seed_feature()
        self.session_log.reply(LATEST, usage_payload("claude", "s1", TEN_MINUTES))
        output = self.collect()
        self.assertEqual((None, 10), (output["feature"]["sources"]["sessions"][0]["prompts"], output["feature"]["usage"]["agentTimeMinutes"]))
        self.assertIn("session-log claude usage --latest has no prompt rows; the whole session is counted for X", output["missing"])

    def test_a_latest_session_read_whole_does_not_replace_the_slice_of_the_same_session(self):
        self.seed_feature()
        self.session("claude", "s1", [prompt_row(at("10:00:00"), "/implement " + PLAN, seconds=TEN_MINUTES)])
        self.session_log.reply(LATEST, usage_payload("claude", "s1", 3600))
        output = self.collect()
        self.assertEqual([("s1", [1], 10)], [(session["id"], [prompt["number"] for prompt in session["prompts"]], session["agentTimeMinutes"])
                                             for session in output["feature"]["sources"]["sessions"]])

    def test_a_latest_reply_that_is_no_json_object_is_named(self):
        self.seed_feature()
        self.session_log.reply(LATEST, ["not", "an", "object"])
        output = self.collect()
        self.assertEqual([], output["feature"]["sources"]["sessions"])
        self.assertIn("session-log claude usage --latest failed: its output is not a JSON object", output["missing"])

    def test_a_failed_latest_call_is_named_and_adds_no_session(self):
        self.seed_feature()
        output = self.collect()
        self.assertEqual([], output["feature"]["sources"]["sessions"])
        self.assertIn(LATEST_FAILED, output["missing"])
        self.assertEqual(self.root, os.path.realpath(self.session_log.calls()[-1]["cwd"]))

    def test_the_latest_session_is_read_in_the_session_folder(self):
        self.seed_feature()
        self.latest([prompt_row(at("11:59:00"), "/implement " + PLAN, seconds=TEN_MINUTES)])
        folder = os.path.join(self.base, "session-home")
        os.makedirs(folder)
        feature = self.collect(PLAN, "--session-dir", folder)["feature"]
        self.assertEqual("s1", feature["sources"]["sessions"][0]["id"])
        latest_calls = [call for call in self.session_log.calls() if call["argv"][2:3] == ["--latest"]]
        self.assertEqual([folder], [os.path.realpath(call["cwd"]) for call in latest_calls])

    def test_a_session_folder_that_is_not_a_folder_is_a_bad_argument(self):
        self.seed_feature()
        missing = os.path.join(self.base, "no-such-folder")
        self.assert_error(self.run_collect(PLAN, "--session-dir", missing), EXIT_BAD_ARGUMENTS, "--session-dir %s is not a folder" % missing)

    def test_a_missing_session_log_script_is_named_and_the_rest_is_still_collected(self):
        self.seed_feature()
        absent = os.path.join(self.base, "nowhere", "session_log.py")
        result = self.run_estimate("collect", PLAN, "--harness", "claude", "--session-log", absent)
        self.assertEqual(EXIT_OK, result.code, result.err)
        self.assertEqual([self.docs_commit], result.json["feature"]["sources"]["commits"])
        self.assertEqual(["no session data: session-log not found (%s)" % absent], result.json["missing"])

    def test_the_harness_is_required_and_must_be_known(self):
        self.assert_error(self.run_estimate("collect", PLAN), EXIT_BAD_ARGUMENTS, "collect needs --harness <value>")
        self.assert_error(self.run_estimate("collect", PLAN, "--harness", "vim"), EXIT_BAD_ARGUMENTS, "unsupported harness 'vim'")


class SharedPromptTest(CollectTestCase):
    SHARED = "compare docs/x-brief.md with docs/y-brief.md"

    def setUp(self):
        super().setUp()
        self.commit("y", {OTHER_BRIEF: "# Y\n"}, when=at("08:05:00"))

    def test_a_prompt_two_features_share_is_split_equally_between_them(self):
        self.seed(document(new_feature("X", sources=listing(BRIEF)), new_feature("Y", sources=listing(OTHER_BRIEF))))
        self.session("claude", "s1", [prompt_row(at("10:00:00"), self.SHARED, seconds=TEN_MINUTES, total=200, cost=1.0)],
                     agents=[agent_row(at("10:01:00"), total=400, cost=2.0)])
        x = self.collect(BRIEF)["feature"]["sources"]["sessions"][0]
        y = self.collect(OTHER_BRIEF)["feature"]["sources"]["sessions"][0]
        for slice_ in (x, y):
            self.assertEqual((5, tokens_of(300), 1.5), (slice_["agentTimeMinutes"], slice_["tokens"], slice_["costUsd"]))
            self.assertEqual([0.5], [prompt["share"] for prompt in slice_["prompts"]])

    def test_the_slices_of_a_session_add_up_to_the_session_even_when_a_number_does_not_split_evenly(self):
        self.seed(document(new_feature("X", sources=listing(BRIEF)), new_feature("Y", sources=listing(OTHER_BRIEF)),
                           new_feature("Z", sources=listing("docs/z-brief.md"))))
        self.commit("z", {"docs/z-brief.md": "z\n"}, when=at("08:10:00"))
        text = self.SHARED + " and docs/z-brief.md"
        self.session("claude", "s1", [prompt_row(at("10:00:00"), text, seconds=100, total=101, cost=1.0)])
        slices = [self.collect(doc)["feature"]["sources"]["sessions"][0] for doc in (BRIEF, OTHER_BRIEF, "docs/z-brief.md")]
        for key in ("input", "output", "cacheRead", "cacheWrite", "total"):
            self.assertEqual(tokens_of(101)[key], sum(slice_["tokens"][key] for slice_ in slices), key)
        for slice_ in slices:
            tokens = slice_["tokens"]
            self.assertEqual(sum(tokens[key] for key in ("input", "output", "cacheRead", "cacheWrite")), tokens["total"])
        self.assertEqual([0.3333] * 3, [slice_["prompts"][0]["share"] for slice_ in slices])
        self.assertAlmostEqual(1.0, sum(slice_["costUsd"] for slice_ in slices), delta=0.011)  # each slice rounds to the cent
        self.assertAlmostEqual(100 / 60, sum(slice_["agentTimeMinutes"] for slice_ in slices), delta=len(slices))  # one minute per slice

    def test_a_prompt_that_follows_a_shared_prompt_is_shared_the_same_way(self):
        self.seed(document(new_feature("X", sources=listing(BRIEF)), new_feature("Y", sources=listing(OTHER_BRIEF))))
        self.session("claude", "s1", [prompt_row(at("10:00:00"), self.SHARED, seconds=60),
                                      prompt_row(at("10:10:00"), "go on", seconds=60)])
        prompts = self.collect(BRIEF)["feature"]["sources"]["sessions"][0]["prompts"]
        self.assertEqual([("follows prompt 1", 0.5)], [(prompt["reason"], prompt["share"]) for prompt in prompts[1:]])

    def test_a_prompt_only_one_feature_counts_for_is_not_split(self):
        self.seed(document(new_feature("X", sources=listing(BRIEF)), new_feature("Y", sources=listing(OTHER_BRIEF))))
        self.session("claude", "s1", [prompt_row(at("10:00:00"), "docs/x-brief.md", seconds=TEN_MINUTES, total=200, cost=1.0)])
        slice_ = self.collect(BRIEF)["feature"]["sources"]["sessions"][0]
        self.assertEqual((10, tokens_of(200), 1.0, [1.0]), (slice_["agentTimeMinutes"], slice_["tokens"], slice_["costUsd"],
                                                            [prompt["share"] for prompt in slice_["prompts"]]))

    def test_a_session_without_prompt_rows_is_shared_equally_and_named(self):
        self.seed(document(new_feature("X", sources=listing(BRIEF)), new_feature("Y", sources=listing(OTHER_BRIEF))))
        self.lists["claude"].append(list_entry("claude", "old", at("09:00:00"), at("10:00:00"), [self.SHARED]))
        self.session_log.reply("claude list --json", self.lists["claude"])
        self.session_log.reply("claude usage old --json", usage_payload("claude", "old", TEN_MINUTES, total=1000, cost=2.5))
        output = self.collect(BRIEF)
        whole = output["feature"]["sources"]["sessions"][0]
        self.assertEqual((None, 5, 1.25), (whole["prompts"], whole["agentTimeMinutes"], whole["costUsd"]))
        self.assertIn("session-log claude usage old has no prompt rows; the whole session is shared equally by X, Y", output["missing"])

    def test_a_slice_costs_what_its_own_rows_cost_even_when_a_row_of_another_feature_is_incomplete(self):
        self.seed(document(new_feature("X", sources=listing(BRIEF)), new_feature("Y", sources=listing(OTHER_BRIEF))))
        rows = [prompt_row(at("10:00:00"), "docs/x-brief.md", cost=0.5), prompt_row(at("10:10:00"), "docs/y-brief.md", cost=0.7, complete=False)]
        self.lists["claude"].append(listed_with_rows("claude", "s1", rows))
        self.session_log.reply("claude list --json", self.lists["claude"])
        self.session_log.reply("claude usage s1 --json", usage_payload("claude", "s1", 0, cost_complete=False, prompts=rows, sub_agents=[]))
        self.assertEqual(0.5, self.collect(BRIEF)["feature"]["sources"]["sessions"][0]["costUsd"])
        self.assertIsNone(self.collect(OTHER_BRIEF)["feature"]["sources"]["sessions"][0]["costUsd"])


if __name__ == "__main__":
    unittest.main()
