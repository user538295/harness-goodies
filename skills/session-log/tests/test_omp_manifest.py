"""OmpManifest (BE-38): package.json wires no extension into OMP, so OMP loads no session-log extension (S28, S57); the OpenCode
plugin test expects the new session-log skill description and carries no ownership-marker assertion."""
import json
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PACKAGE_JSON = REPO / "package.json"
PLUGIN_TEST = REPO / ".opencode-plugin" / "index.test.js"
SKILL_MD = REPO / "skills" / "session-log" / "SKILL.md"
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
DESCRIPTION_LINE = re.compile(r"^description:[ \t]*(.+?)[ \t]*$", re.MULTILINE)
REMOVED_MARKER_TEXT = re.compile(r"This skill runs the bundled script|marker", re.IGNORECASE)
EXTENSION_MANIFEST_KEYS = ("omp", "pi")


def extension_paths(node, trail=""):
    """Yield the dotted path of every `extensions` key anywhere in a parsed JSON document."""
    if isinstance(node, dict):
        for key, value in node.items():
            here = trail + "." + key if trail else key
            if key == "extensions":
                yield here
            yield from extension_paths(value, here)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from extension_paths(value, "%s[%d]" % (trail, index))


class OmpManifestTests(unittest.TestCase):
    def test_package_json_has_no_omp_extensions(self):
        package = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
        self.assertEqual([], list(extension_paths(package)))
        for key in EXTENSION_MANIFEST_KEYS:
            self.assertNotIn(key, package)

    def test_plugin_test_expects_new_skill_description_and_no_marker(self):
        frontmatter = FRONTMATTER.match(SKILL_MD.read_text(encoding="utf-8"))
        self.assertIsNotNone(frontmatter, "SKILL.md has no frontmatter")
        description = DESCRIPTION_LINE.search(frontmatter.group(1))
        self.assertIsNotNone(description, "SKILL.md frontmatter has no one-line description")
        plugin_test = PLUGIN_TEST.read_text(encoding="utf-8")
        expected = 'registered.skills["session-log"]'
        self.assertIn(expected, plugin_test)
        self.assertRegex(
            plugin_test,
            r"skill\.description\)\.toBe\(%s\)" % re.escape(json.dumps(description.group(1), ensure_ascii=False)),
        )
        self.assertIsNone(REMOVED_MARKER_TEXT.search(plugin_test))


if __name__ == "__main__":
    unittest.main()
