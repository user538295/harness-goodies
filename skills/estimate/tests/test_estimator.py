"""`estimator --harness <h> [--session-log <path>] [--session-dir <folder>]` (C3): who estimates now, for the `estimatedBy` or
`updatedBy` of a save: the harness given and the models session-log names for the last prompt of that harness's latest session in
the session folder (the folder the running session was started in; the project root when not given), which is the prompt now
running. The models are null, with the reason in `missing`, when they cannot be read."""
import os
import unittest

from tests.support import EXIT_BAD_ARGUMENTS, EXIT_OK, ProjectTestCase, agent_row, prompt_row, usage_payload

LATEST = "claude usage --latest --json"


def at(clock):
    return "2026-11-04T%s+00:00" % clock


class EstimatorCommandTest(ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.session_log = self.fake_session_log()

    def estimator(self, *extra):
        result = self.run_estimate("estimator", "--harness", "claude", *extra)
        self.assertEqual(EXIT_OK, result.code, result.err)
        return result.json

    def with_session_log(self):
        return self.estimator("--session-log", self.session_log.path)

    def test_the_models_of_the_last_prompt_of_the_latest_session(self):
        self.session_log.reply(LATEST, usage_payload("claude", "s1", 0, models=("claude-opus-5-5", "claude-sonnet-5-5"), prompts=[
            prompt_row(at("10:00:00"), "/plan-maker docs/x-brief.md", models=("claude-sonnet-5-5",)),
            prompt_row(at("10:30:00"), "/estimate docs/x-plan.md", models=("claude-opus-5-5",))],
            sub_agents=[agent_row(at("10:31:00"), models=("claude-haiku-5-5",))]))
        self.assertEqual({"estimator": {"harness": "claude", "models": ["claude-opus-5-5"]}, "missing": []}, self.with_session_log())

    def test_without_a_session_log_the_models_are_null_and_named(self):
        self.assertEqual({"estimator": {"harness": "claude", "models": None}, "missing": ["models not recorded: no --session-log"]},
                         self.estimator())

    def test_a_failed_call_a_session_without_prompt_rows_or_a_last_prompt_without_models_gives_null_models(self):
        named_none = "models not recorded: session-log claude usage --latest names none for its last prompt"
        for name, reply, reason in (
                ("failed", dict(code=1, stderr="session-log: no session\n"),
                 "session-log claude usage --latest failed (exit 1): session-log: no session"),
                ("no rows", dict(payload=usage_payload("claude", "s1", 0)), named_none),
                ("no prompt", dict(payload=usage_payload("claude", "s1", 0, prompts=[], sub_agents=[])), named_none),
                ("no model", dict(payload=usage_payload("claude", "s1", 0, prompts=[prompt_row(at("10:00:00"), "go", models=())],
                                                        sub_agents=[])), named_none)):
            with self.subTest(name=name):
                payload = reply.pop("payload", None)
                self.session_log.reply(LATEST, payload, **reply)
                self.assertEqual({"estimator": {"harness": "claude", "models": None}, "missing": [reason]}, self.with_session_log())

    def test_the_harness_is_required_and_must_be_known(self):
        self.assert_error(self.run_estimate("estimator"), EXIT_BAD_ARGUMENTS, "estimator needs --harness <value>")
        self.assert_error(self.run_estimate("estimator", "--harness", "vim"), EXIT_BAD_ARGUMENTS, "unsupported harness 'vim'")

    def test_the_latest_session_is_read_in_the_session_folder(self):
        session_folder = os.path.join(self.base, "session-home")
        os.makedirs(session_folder)
        self.session_log.reply(LATEST, usage_payload("claude", "s1", 0, prompts=[
            prompt_row(at("10:30:00"), "/estimate init", models=("claude-opus-5-5",))], sub_agents=[]))
        output = self.estimator("--session-log", self.session_log.path, "--session-dir", session_folder)
        self.assertEqual(["claude-opus-5-5"], output["estimator"]["models"])
        self.assertEqual([session_folder], [os.path.realpath(call["cwd"]) for call in self.session_log.calls()])

    def test_without_a_session_folder_the_latest_session_is_read_in_the_project_root(self):
        self.session_log.reply(LATEST, usage_payload("claude", "s1", 0, prompts=[], sub_agents=[]))
        self.with_session_log()
        self.assertEqual([self.root], [os.path.realpath(call["cwd"]) for call in self.session_log.calls()])

    def test_a_session_folder_that_is_not_a_folder_is_a_bad_argument(self):
        missing = os.path.join(self.base, "no-such-folder")
        result = self.run_estimate("estimator", "--harness", "claude", "--session-log", self.session_log.path, "--session-dir", missing)
        self.assert_error(result, EXIT_BAD_ARGUMENTS, "--session-dir %s is not a folder" % missing)


if __name__ == "__main__":
    unittest.main()
