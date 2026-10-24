"""SKILL.md static text (S25): one command line that runs the script, no other shell syntax, no repair, no old package markers."""
import re
import unittest
from pathlib import Path

SKILL_MD = Path(__file__).resolve().parent.parent / "SKILL.md"
COMMAND_LINE = 'python3 "<skill dir>/session_log.py" <harness> <arguments>'
PLACEHOLDER = re.compile(r"<[^<>\n]+>")
PLAIN_COMMAND_CHARACTERS = re.compile(r'[A-Za-z0-9 _"/.]+')
SCRIPT_INVOCATION = re.compile(r'session_log\.py" <harness>')
FENCE = re.compile(r"^```(\w*)\n(.*?)^```$", re.MULTILINE | re.DOTALL)
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
LOCATOR_FENCE_LANGUAGE = "bash"
WINDOWS_FALLBACKS = ("py -3", "python ")
OLD_PACKAGE_MARKERS = ("repair", "install" + ".sh", "universal-session-log", "git bash", "bash 4")
LOCATOR_SHAPE = re.compile(
    r'\ABASE=""; for d in [^\n]*; do \[ -n "\$d" \] && \[ -f "\$d/session_log\.py" \] && \{ BASE="\$d"; break; \}; done; '
    r'\[ -n "\$BASE" \] && echo "\$BASE" \|\| \{ echo "ERROR: [^"\n]*" >&2; false; \}\n\Z')
LOCATOR_EXECUTIONS = ("bash ", "sh ", "eval", "exec", "source", "python")


def _text():
    return SKILL_MD.read_text(encoding="utf-8")


def _fences():
    return FENCE.findall(_text())


def _prose():
    return FENCE.sub("", _text())


def _frontmatter():
    return FRONTMATTER.match(_text()).group(1)


class SkillMdCommandLineTest(unittest.TestCase):
    def test_skill_md_has_exactly_one_script_line_and_no_other_shell_syntax(self):
        text = _text()
        self.assertEqual(len(SCRIPT_INVOCATION.findall(text)), 1)
        script_lines = [line for line in text.splitlines() if SCRIPT_INVOCATION.search(line)]
        self.assertEqual(script_lines, [COMMAND_LINE])
        self.assertIsNotNone(PLAIN_COMMAND_CHARACTERS.fullmatch(PLACEHOLDER.sub("x", script_lines[0])))
        command_fences = [body for language, body in _fences() if SCRIPT_INVOCATION.search(body)]
        self.assertEqual(command_fences, [COMMAND_LINE + "\n"])
        other_fences = [language for language, body in _fences() if body != COMMAND_LINE + "\n"]
        self.assertEqual(other_fences, [LOCATOR_FENCE_LANGUAGE])

    def test_windows_fallback_is_prose_in_order_and_not_shell_code(self):
        for language, body in _fences():
            for fallback in WINDOWS_FALLBACKS:
                self.assertNotIn(fallback, body, "fallback inside a %r fence" % language)
        fallback_paragraph = [paragraph for paragraph in _prose().split("\n\n") if "py -3" in paragraph]
        self.assertEqual(len(fallback_paragraph), 1)
        paragraph = fallback_paragraph[0]
        self.assertTrue(paragraph.startswith("On Windows only"))
        positions = [paragraph.find(token) for token in ("`python3`", "`py -3`", "`python`")]
        self.assertNotIn(-1, positions)
        self.assertEqual(positions, sorted(positions))

    def test_windows_without_an_announced_directory_asks_for_session_log_home(self):
        steps = [line for line in _prose().splitlines() if line.startswith("3. ")]
        self.assertEqual(len(steps), 1)
        self.assertIn("native Windows", steps[0])
        self.assertIn("ask the user to set `SESSION_LOG_HOME` to the skill directory and to tell you its value, and use that value", steps[0])
        self.assertLess(steps[0].index("macOS and Linux"), steps[0].index("native Windows"))

    def test_announced_directory_comes_before_the_locator(self):
        steps = [line for line in _prose().splitlines() if line[:3] in ("2. ", "3. ")]
        self.assertEqual([step[:2] for step in steps], ["2.", "3."])
        self.assertIn("announced", steps[0])
        self.assertIn("a `file://` URL stands for the filesystem path it names", steps[0])
        self.assertIn("announced no directory", steps[1])

    def test_arguments_are_limited_to_the_five_commands_and_a_safe_session_id(self):
        steps = [line for line in _prose().splitlines() if line.startswith("4. ")]
        self.assertEqual(len(steps), 1)
        allowlist = steps[0].split("Accept only", 1)[1].split(" (a session id", 1)[0]
        self.assertEqual(re.findall(r"`([^`]+)`", allowlist), ["on", "off", "status", "usage", "usage --latest", "usage <session-id>"])
        self.assertIn("or to `status` when none were supplied", steps[0])
        self.assertIn("letters, digits, `_` and `-`", steps[0])
        self.assertIn("stop and tell the user", steps[0])

    def test_windows_fallback_stops_on_a_script_answer_and_when_no_interpreter_starts(self):
        paragraph = [paragraph for paragraph in _prose().split("\n\n") if "py -3" in paragraph][0]
        self.assertIn("no `session-log:` line appears on stderr", paragraph)
        self.assertIn("try no other interpreter", paragraph)
        self.assertIn("Python 3.14 or newer is required", paragraph)

    def test_locator_block_only_prints_the_directory(self):
        blocks = [body for language, body in _fences() if language == LOCATOR_FENCE_LANGUAGE]
        self.assertEqual(len(blocks), 1)
        block = blocks[0]
        self.assertIn('-f "$d/session_log.py"', block)
        self.assertIn('echo "$BASE"', block)
        for execution in LOCATOR_EXECUTIONS:
            self.assertNotIn(execution, block)
        self.assertRegex(block, LOCATOR_SHAPE)


class SkillMdOldPackageTest(unittest.TestCase):
    def test_skill_md_has_no_repair_no_install_sh_no_managed_marker(self):
        lowered = _text().lower()
        for forbidden in OLD_PACKAGE_MARKERS:
            self.assertNotIn(forbidden, lowered)

    def test_frontmatter_has_name_and_description_without_repair(self):
        frontmatter = _frontmatter()
        keys = [line.split(":", 1)[0] for line in frontmatter.splitlines()]
        self.assertEqual(keys, ["name", "description"])
        self.assertIn("name: session-log", frontmatter)
        self.assertIn("usage)", frontmatter)
        self.assertNotIn("repair", frontmatter)


if __name__ == "__main__":
    unittest.main()
