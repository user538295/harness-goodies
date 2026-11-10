"""The files a Codex tool call writes with `apply_patch` (`sessionlog.codex_patch`): the call shapes that count, where relative paths
start, and the call outputs that report a failure. Verified against real rollouts: the native `apply_patch` custom call, the `exec`
call with `tools.apply_patch("...")`, and the `exec` output of a failed patch. Built from Codex's documented shapes, never seen in a
local rollout (UNVERIFIED): the `function_call` forms of `apply_patch`, every `shell`, `shell_command` and `exec_command` call with
`workdir`, argument vectors and heredocs, and the string and `function_call_output` forms of a failed output."""
import json
import unittest

from sessionlog.codex_patch import failed_call_id, paths_of_call


def _patch(*headers) -> str:
    body = "".join(f"{header}\n@@\n-old\n+new\n" for header in headers)
    return f"*** Begin Patch\n{body}*** End Patch\n"


def _custom(name, input_text, call_id="call") -> dict:
    return {"type": "custom_tool_call", "status": "completed", "call_id": call_id, "name": name, "input": input_text}


def _function(name, arguments, call_id="call") -> dict:
    return {"type": "function_call", "name": name, "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments), "call_id": call_id}


def _heredoc(patch, command="apply_patch", tag="'EOF'", end="EOF") -> str:
    return f"{command} <<{tag}\n{patch.rstrip()}\n{end}\n"


def _exec_paths(source, directory=None):
    return paths_of_call(_custom("exec", source), directory)


def _shell_paths(name, arguments, directory=None):
    return paths_of_call(_function(name, arguments), directory)


class NativeApplyPatchTest(unittest.TestCase):
    def test_a_custom_call_gives_the_paths_of_its_add_update_and_move_headers_in_order(self):
        patch = _patch("*** Add File: /w/a.md", "*** Update File: /w/b.py", "*** Move to: /w/c.py")
        self.assertEqual(("/w/a.md", "/w/b.py", "/w/c.py"), paths_of_call(_custom("apply_patch", patch), None))

    def test_a_function_call_gives_the_paths_of_its_input_argument(self):
        self.assertEqual(("/w/a.md",), paths_of_call(_function("apply_patch", {"input": _patch("*** Add File: /w/a.md")}), None))

    def test_a_function_call_with_unusable_arguments_gives_nothing(self):
        for arguments in ("not json", "[]", json.dumps({"input": 5}), json.dumps({}), 7):
            with self.subTest(arguments=arguments):
                self.assertEqual((), paths_of_call(_function("apply_patch", arguments), None))

    def test_a_native_call_whose_text_does_not_open_a_patch_gives_nothing(self):
        document = "Docs\n*** Add File: /w/doc.md\n*** Update File: /w/doc2.md\n"
        for call in (_custom("apply_patch", document), _custom("apply_patch", "Quote:\n" + _patch("*** Add File: /w/a.md")),
                     _function("apply_patch", {"input": document}), _custom("apply_patch", "*** Add File: /w/a.md\n+x")):
            with self.subTest(call=call):
                self.assertEqual((), paths_of_call(call, None))

    def test_a_native_patch_may_be_preceded_by_blank_space(self):
        self.assertEqual(("/w/a.md",), paths_of_call(_custom("apply_patch", "\n  " + _patch("*** Add File: /w/a.md")), None))

    def test_other_calls_and_odd_names_give_nothing_even_when_their_text_holds_a_patch(self):
        patch = _patch("*** Add File: /w/a.md")
        calls = [_function("read_file", {"input": patch}), _custom("write_file", patch), _function("exec", {"input": patch}),
                 _custom("apply_patch", 7), _custom(["apply_patch"], patch), _function(["shell"], {"command": ["apply_patch", patch]}),
                 {"type": "message", "name": "apply_patch", "input": patch}, {}]
        for call in calls:
            with self.subTest(call=call):
                self.assertEqual((), paths_of_call(call, None))


class ExecPatchTest(unittest.TestCase):
    """The JavaScript of Codex's `exec` tool names the files of a patch only when its code calls `tools.apply_patch` with the patch as
    a string literal, or as a name bound to one by `const name = <literal>;`, and the patch opens with `*** Begin Patch`."""
    PATCH = _patch("*** Add File: /w/a.md", "*** Update File: /w/b.py")
    DOC = "Docs\n*** Add File: /w/doc.md\n*** Update File: /w/doc2.md\n"

    def test_a_call_with_a_double_quoted_literal_gives_the_paths_of_its_headers(self):
        source = f'text(await tools.exec_command({{cmd:"ls"}}));\ntext(await tools.apply_patch({json.dumps(self.PATCH)}));'
        self.assertEqual(("/w/a.md", "/w/b.py"), _exec_paths(source))

    def test_every_call_of_one_source_counts(self):
        other = _patch("*** Update File: /w/a.md", "*** Add File: /w/c.md")
        source = f"await tools.apply_patch({json.dumps(self.PATCH)});\nawait tools.apply_patch({json.dumps(other)});"
        self.assertEqual(("/w/a.md", "/w/b.py", "/w/a.md", "/w/c.md"), _exec_paths(source))

    def test_a_name_bound_by_const_to_a_literal_and_passed_to_the_call_counts(self):
        source = f"const patch = {json.dumps(self.PATCH)};\nconst out = await tools.apply_patch(patch);\ntext(out);"
        self.assertEqual(("/w/a.md", "/w/b.py"), _exec_paths(source))

    def test_a_single_quoted_literal_with_escapes_is_decoded(self):
        source = r"""await tools.apply_patch('*** Begin Patch\n*** Add File: /w/it\'s \x41 \u{1F600} \u00e9.md\n+x\n*** End Patch');"""
        self.assertEqual(("/w/it's A \U0001F600 \u00e9.md",), _exec_paths(source))

    def test_a_json_escaped_non_ascii_path_with_a_surrogate_pair_is_decoded(self):
        source = f"await tools.apply_patch({json.dumps(_patch('*** Add File: /w/caf\u00e9 \U0001F600.md'))});"
        self.assertEqual(("/w/caf\u00e9 \U0001F600.md",), _exec_paths(source))

    def test_a_patch_quoted_but_never_passed_to_the_call_gives_nothing(self):
        doc = json.dumps(self.PATCH)
        for source in (f"const patch = {doc};\ntext('done');", f"text({doc});", f"await tools.write_file({{path:'x.md', content:{doc}}});",
                       f"const patch = {doc};\nawait tools.write_file({{path:'x.md', content:patch}});"):
            with self.subTest(source=source):
                self.assertEqual((), _exec_paths(source))

    def test_the_call_text_inside_a_string_a_comment_or_a_template_gives_nothing(self):
        call = f"tools.apply_patch({json.dumps(self.PATCH)})"
        for source in (f"text({json.dumps(call)});", f"// {call}\ntext('x');", f"/* {call} */ text('x');", f"text(`{call}`);",
                       f"text({json.dumps('see ' + call)});"):
            with self.subTest(source=source):
                self.assertEqual((), _exec_paths(source))

    def test_a_call_of_another_object_or_name_gives_nothing(self):
        doc = json.dumps(self.PATCH)
        for source in (f"await mytools.apply_patch({doc});", f"await x.tools.apply_patch({doc});", f"await tools.apply_patches({doc});",
                       f"await tools.apply_patch_later({doc});", f"await tools.exec_command({{cmd:{doc}}});"):
            with self.subTest(source=source):
                self.assertEqual((), _exec_paths(source))

    def test_an_argument_that_is_no_plain_literal_or_const_name_gives_nothing(self):
        doc = json.dumps(self.PATCH)
        for source in (f"await tools.apply_patch({doc} + extra);", "await tools.apply_patch(patch);",
                       f"let patch = {doc};\nawait tools.apply_patch(patch);", f"const patch = {doc} + more;\nawait tools.apply_patch(patch);",
                       f"await tools.apply_patch(`{self.PATCH}`);", f"await tools.apply_patch({doc}, {{}});",
                       f"const patch = {doc};\nawait tools.apply_patch(patch.trim());", "await tools.apply_patch(5);"):
            with self.subTest(source=source):
                self.assertEqual((), _exec_paths(source))

    def test_a_binding_needs_its_terminating_semicolon_and_a_continued_expression_binds_nothing(self):
        doc = json.dumps(self.PATCH)
        half = json.dumps("*** Begin Patch\n*** Add File: /w/a.md\n")
        rest = json.dumps("*** End Patch\n")
        for source in (f"const patch = {doc}\nawait tools.apply_patch(patch);", f"const patch = {half}\n  + {rest};\nawait tools.apply_patch(patch);",
                       f"const patch = {half} +\n  {rest};\nawait tools.apply_patch(patch);", f"const patch = {doc}\n.trim();\nawait tools.apply_patch(patch);"):
            with self.subTest(source=source):
                self.assertEqual((), _exec_paths(source))

    def test_a_binding_ended_by_a_semicolon_after_blank_space_or_a_comment_counts(self):
        doc = json.dumps(self.PATCH)
        for source in (f"const patch = {doc}  ;\nawait tools.apply_patch(patch);", f"const patch = {doc} /* x */ ;\nawait tools.apply_patch(patch);"):
            with self.subTest(source=source):
                self.assertEqual(("/w/a.md", "/w/b.py"), _exec_paths(source))

    def test_a_document_that_quotes_a_patch_header_is_no_patch(self):
        for text in (self.DOC, "Quote:\n" + self.PATCH, "   \n" + self.DOC):
            with self.subTest(text=text):
                self.assertEqual((), _exec_paths(f"await tools.apply_patch({json.dumps(text)});"))

    def test_only_the_exec_tool_counts(self):
        source = f"await tools.apply_patch({json.dumps(self.PATCH)});"
        self.assertEqual((), paths_of_call(_custom("js_repl", source), None))
        self.assertEqual((), paths_of_call(_function("exec", {"input": source}), None))


class ShellPatchTest(unittest.TestCase):
    """A shell call names the files of a patch only when its command is `apply_patch` with the patch as argument or as the heredoc
    that is the whole script (after an optional `cd <dir> &&`), and the patch opens with `*** Begin Patch`."""
    PATCH = _patch("*** Add File: /w/a.md", "*** Update File: /w/b.py")
    PATHS = ("/w/a.md", "/w/b.py")

    def test_the_argument_vector_of_apply_patch_gives_the_paths(self):
        for program in ("apply_patch", "applypatch"):
            with self.subTest(program=program):
                self.assertEqual(self.PATHS, _shell_paths("shell", {"command": [program, self.PATCH]}))

    def test_a_heredoc_script_run_by_a_shell_gives_the_paths(self):
        script = _heredoc(self.PATCH)
        for name, arguments in (("shell", {"command": ["bash", "-lc", script]}), ("shell", {"command": ["zsh", "-c", script]}),
                                ("shell", {"command": ["/bin/bash", "-lc", script]}), ("shell", {"command": ["/usr/bin/zsh", "-c", script]}),
                                ("shell_command", {"command": script}), ("exec_command", {"cmd": script}),
                                ("exec_command", {"cmd": _heredoc(self.PATCH, tag='"EOF"')}),
                                ("exec_command", {"cmd": _heredoc(self.PATCH, tag="PATCH", end="PATCH")}),
                                ("exec_command", {"cmd": script.rstrip()}),
                                ("exec_command", {"cmd": "cd /w && " + script}), ("shell_command", {"command": _heredoc(self.PATCH, "applypatch")})):
            with self.subTest(name=name, arguments=arguments):
                self.assertEqual(self.PATHS, _shell_paths(name, arguments))

    def test_a_script_that_is_more_than_the_heredoc_gives_nothing(self):
        for script in ("cat <<'EOF' > doc.md\n" + self.PATCH + "EOF\n", _heredoc(self.PATCH) + "rm -rf /w\n", "echo hi && " + _heredoc(self.PATCH),
                       "echo \"" + _heredoc(self.PATCH) + "\"", _heredoc(self.PATCH, end="NOPE"), "git commit -m '" + self.PATCH + "'",
                       _heredoc(self.PATCH, command="not_apply_patch"), _heredoc("Docs\n*** Add File: /w/doc.md")):
            with self.subTest(script=script):
                self.assertEqual((), _shell_paths("exec_command", {"cmd": script}))
                self.assertEqual((), _shell_paths("shell", {"command": ["bash", "-lc", script]}))

    def test_a_later_heredoc_delimiter_line_cannot_stretch_the_script_to_a_trailing_command(self):
        """The heredoc ends at the first delimiter line: a script that goes on with `cat <<EOF ... EOF` is more than the heredoc."""
        trailing = "cat <<'EOF'\nnotes\nEOF\n"
        mid_body = _heredoc(self.PATCH.replace("*** End Patch", "EOF\n*** End Patch"))
        for script in (_heredoc(self.PATCH) + trailing, _heredoc(self.PATCH) + _heredoc(self.PATCH), mid_body):
            with self.subTest(script=script):
                self.assertEqual((), _shell_paths("exec_command", {"cmd": script}))

    def test_blank_space_after_the_delimiter_line_is_no_command(self):
        self.assertEqual(self.PATHS, _shell_paths("exec_command", {"cmd": _heredoc(self.PATCH) + "\n  \n"}))
        self.assertEqual(self.PATHS, _shell_paths("exec_command", {"cmd": _heredoc(self.PATCH, end="EOF  ")}))

    def test_an_argument_vector_of_another_program_or_without_a_patch_gives_nothing(self):
        for command in (["git", "commit", "-m", self.PATCH], ["apply_patch", "*** Add File: /w/doc.md"], ["apply_patch"], ["apply_patch", 5],
                        ["apply_patch", self.PATCH, "more"], [], "", 7, None, [["apply_patch"], self.PATCH], [7, "-c", "x"]):
            with self.subTest(command=command):
                self.assertEqual((), _shell_paths("shell", {"command": command}))

    def test_a_vector_run_by_a_program_that_is_no_known_shell_gives_nothing(self):
        script = _heredoc(self.PATCH)
        for program in ("fish", "/bin/fish", "python", "/bin/bash/x", ""):
            with self.subTest(program=program):
                self.assertEqual((), _shell_paths("shell", {"command": [program, "-c", script]}))

    def test_unusable_arguments_or_a_call_of_another_name_give_nothing(self):
        script = _heredoc(self.PATCH)
        for call in (_function("shell", "not json"), _function("shell", "[]"), _function("shell", 7), _function("read_file", {"command": script}),
                     _function("shell", {"cmd": 7, "command": 7}), _custom("shell", script)):
            with self.subTest(call=call):
                self.assertEqual((), paths_of_call(call, None))


class WorkingDirectoryTest(unittest.TestCase):
    """Relative header paths start at the turn's directory, the `workdir` of a shell call and the `cd <dir> &&` target, in that order."""
    RELATIVE = _patch("*** Add File: docs/a.md", "*** Update File: ./b.py")

    def test_a_relative_path_is_joined_to_the_given_directory_and_an_absolute_one_stays(self):
        patch = _patch("*** Add File: docs/a.md", "*** Update File: /abs/b.py")
        self.assertEqual(("/work/proj/docs/a.md", "/abs/b.py"), paths_of_call(_custom("apply_patch", patch), "/work/proj"))

    def test_a_relative_path_stays_relative_without_any_directory(self):
        self.assertEqual(("docs/a.md", "./b.py"), paths_of_call(_custom("apply_patch", self.RELATIVE), None))
        self.assertEqual(("docs/a.md", "./b.py"), paths_of_call(_custom("apply_patch", self.RELATIVE), ""))

    def test_an_exec_call_resolves_against_the_directory(self):
        self.assertEqual(("/work/proj/docs/a.md", "/work/proj/b.py"), _exec_paths(f"await tools.apply_patch({json.dumps(self.RELATIVE)});", "/work/proj"))

    def test_the_workdir_of_a_shell_call_is_joined_to_the_directory(self):
        arguments = {"command": ["apply_patch", self.RELATIVE], "workdir": "sub"}
        self.assertEqual(("/work/proj/sub/docs/a.md", "/work/proj/sub/b.py"), _shell_paths("shell", arguments, "/work/proj"))
        self.assertEqual(("/elsewhere/docs/a.md", "/elsewhere/b.py"), _shell_paths("shell", {**arguments, "workdir": "/elsewhere"}, "/work/proj"))

    def test_a_cd_prefix_of_a_heredoc_script_is_joined_after_the_workdir(self):
        script = "cd pkg && " + _heredoc(self.RELATIVE)
        self.assertEqual(("/work/proj/pkg/docs/a.md", "/work/proj/pkg/b.py"), _shell_paths("exec_command", {"cmd": script}, "/work/proj"))
        self.assertEqual(("/work/proj/sub/pkg/docs/a.md", "/work/proj/sub/pkg/b.py"),
                         _shell_paths("exec_command", {"cmd": script, "workdir": "sub"}, "/work/proj"))
        self.assertEqual(("/abs/docs/a.md", "/abs/b.py"), _shell_paths("exec_command", {"cmd": "cd /abs && " + _heredoc(self.RELATIVE)}, "/work/proj"))

    def test_a_quoted_cd_target_may_hold_blank_space(self):
        for target in ('"dir with space"', "'dir with space'"):
            with self.subTest(target=target):
                script = f"cd {target} && " + _heredoc(self.RELATIVE)
                self.assertEqual(("/w/dir with space/docs/a.md", "/w/dir with space/b.py"), _shell_paths("exec_command", {"cmd": script}, "/w"))

    def test_a_cd_target_that_a_shell_would_expand_gives_nothing(self):
        for target in ("$HOME", '"$HOME/x"', "~/x", '"`pwd`"', "*", "a;b"):
            with self.subTest(target=target):
                self.assertEqual((), _shell_paths("exec_command", {"cmd": f"cd {target} && " + _heredoc(self.RELATIVE)}, "/w"))

    def test_a_relative_cd_target_still_prefixes_the_paths_when_no_turn_directory_is_known(self):
        self.assertEqual(("pkg/docs/a.md", "pkg/b.py"), _shell_paths("exec_command", {"cmd": "cd pkg && " + _heredoc(self.RELATIVE)}))


class FailedCallTest(unittest.TestCase):
    """A call output that says `apply_patch verification failed` names the failed call. Real shape: a `custom_tool_call_output` whose
    `output` is a list of `input_text` blocks, the second one holding `Script error:` and the message; the string and
    `function_call_output` forms are UNVERIFIED."""

    @staticmethod
    def _output(output, call_id="call_9", kind="custom_tool_call_output") -> dict:
        return {"type": kind, "id": "ctco_1", "call_id": call_id, "output": output}

    def test_a_script_output_with_the_verification_message_names_its_call(self):
        output = [{"type": "input_text", "text": "Script failed\nWall time 0.0 seconds\nOutput:\n"},
                  {"type": "input_text", "text": "Script error:\napply_patch verification failed: Failed to find expected lines in /w/a.md:\n  old"}]
        self.assertEqual("call_9", failed_call_id(self._output(output)))

    def test_a_string_output_and_a_function_call_output_name_their_call_too(self):
        self.assertEqual("call_9", failed_call_id(self._output("apply_patch verification failed: no such file")))
        self.assertEqual("call_9", failed_call_id(self._output(json.dumps({"output": "apply_patch verification failed: x"}), kind="function_call_output")))

    def test_an_output_that_succeeded_or_has_another_failure_names_nothing(self):
        done = [{"type": "input_text", "text": "Script completed\nWall time 0.1 seconds\nOutput:\n"}, {"type": "input_text", "text": "{}"}]
        for output in (done, "Success. Updated the following files:\nM a.md", "Script failed\nOutput:\nScript error:\nboom", "", None, 7, [], [7, {"text": 5}]):
            with self.subTest(output=output):
                self.assertIsNone(failed_call_id(self._output(output)))

    def test_an_output_without_a_usable_call_id_or_a_record_that_is_no_output_names_nothing(self):
        failed = "apply_patch verification failed"
        for payload in (self._output(failed, call_id=None), self._output(failed, call_id=""), self._output(failed, call_id=7),
                        self._output(failed, kind="custom_tool_call"), self._output(failed, kind=["custom_tool_call_output"]),
                        {"call_id": "call_9", "output": failed}, {}):
            with self.subTest(payload=payload):
                self.assertIsNone(failed_call_id(payload))


if __name__ == "__main__":
    unittest.main()
