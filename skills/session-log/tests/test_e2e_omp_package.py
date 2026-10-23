"""E2E of the scripted part of S28: the OMP package as OMP installs it ships no session-log extension, and `status` runs the new script.

OMP's `plugin install` copies the plugin folder to `~/.omp/plugins/cache/plugins/<marketplace>___omp-goodies___<version>`. The test stages
the repository the same way in a sandbox `HOME` (a copy of the working tree without `.git`, `node_modules` and caches; the repository is never written) and
inspects that folder: neither `package.json` nor the files in `.omp-plugin` wire an extension, command or hook into OMP, the folder
holds no OMP extension source and no file of the removed stack, and the sandbox agent folder has no `extensions` folder. Then
`session_log.py omp status`, the command line `/skill:session-log status` makes OMP run, is run from that folder as a real subprocess and
its answer is the new script's, not the release-1 one (which said `Session logging is off for OMP.`).

That OMP really starts with no session-log extension, and which copy `/skill:session-log status` reaches when the Claude plugin of the
same name is installed too, is the manual half (T-10): `omp` needs a model login and the live OMP install is never touched here.

On macOS, from the repository root:

    python3 -m unittest discover -s skills/session-log/tests -t skills/session-log -p "test_e2e_omp_package.py" -v
"""
import json
import re
import shutil
import unittest
from pathlib import Path

from tests import test_e2e_skeleton as skeleton
from tests.test_e2e_installed_copy import INSTALL_ROOTS, OMP_AGENT_ROOT, InstalledCopyCase
from tests.test_e2e_claude_hook import SKILL_RELATIVE

REPO = skeleton.SKILL_DIR.parents[1]
OMP_MANIFEST_KEYS = ("omp", "pi")
PLUGIN_WIRING_KEYS = ("extensions", "commands", "hooks")
OMP_JSON_FILES = ("plugin.json", "marketplace.json")
EXTENSION_SOURCE = re.compile(r"\.(?:ts|mts|cts|tsx|js|mjs|cjs)$")
# the parts of the removed stack (BE-37): none may be in the installed folder
REMOVED_PATHS = ("install.sh", "bin", "lib", "VERSION", "adapters")
NOT_PACKAGED_FOLDERS = (".git", "node_modules", "__pycache__", ".pytest_cache")
# where OMP would look for a package's extension source: the package root, its OMP manifest folder and the session-log skill
EXTENSION_FOLDERS = (Path("."), Path(".omp-plugin"), SKILL_RELATIVE)
# the release-1 script answered `Session logging is off for OMP.`
NEW_STATUS_LINES = ("Session logging is off.", "Archived sessions: 0")


def last_key(path):
    return path.split(".")[-1]


def keys_of(node, trail=""):
    """Yield the dotted path of every object key in a parsed JSON document."""
    if isinstance(node, dict):
        for key, value in node.items():
            here = trail + "." + key if trail else key
            yield here
            yield from keys_of(value, here)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from keys_of(value, "%s[%d]" % (trail, index))


class OmpPackageTests(InstalledCopyCase):
    def stage_plugin(self):
        """Copies the working tree to the OMP install folder below the sandbox `HOME`; returns that folder."""
        plugin = Path(self.home, *INSTALL_ROOTS["omp"])
        plugin.parent.mkdir(parents=True)
        shutil.copytree(REPO, plugin, ignore=shutil.ignore_patterns(*NOT_PACKAGED_FOLDERS))
        return plugin

    def test_omp_packaged_plugin_ships_no_session_log_extension(self):
        plugin = self.stage_plugin()
        package = json.loads((plugin / "package.json").read_text(encoding="utf-8"))
        for key in OMP_MANIFEST_KEYS:
            self.assertNotIn(key, package)
        self.assertEqual([], [path for path in keys_of(package) if last_key(path) == "extensions"])
        for name in OMP_JSON_FILES:
            document = json.loads((plugin / ".omp-plugin" / name).read_text(encoding="utf-8"))
            self.assertEqual([], [path for path in keys_of(document) if last_key(path) in PLUGIN_WIRING_KEYS], msg=name)
        self.assertEqual([], [name for name in REMOVED_PATHS if (plugin / SKILL_RELATIVE / name).exists()])
        self.assertFalse((plugin / "extensions").exists())
        folders = [plugin / folder for folder in EXTENSION_FOLDERS]
        sources = {path for folder in folders for path in folder.iterdir() if path.is_file()} | {path for path in folders[-1].rglob("*") if path.is_file()}
        self.assertEqual([], sorted(str(path.relative_to(plugin)) for path in sources if EXTENSION_SOURCE.search(path.name)))

    def test_omp_status_runs_the_new_script_from_the_installed_plugin(self):
        plugin = self.stage_plugin()
        agent_folder = Path(self.home, *OMP_AGENT_ROOT)
        answer = self.run_installed(str(plugin / SKILL_RELATIVE), "omp", "status")
        lines = answer.splitlines()
        self.assertEqual(NEW_STATUS_LINES[0], lines[0] if lines else "")
        self.assertIn(NEW_STATUS_LINES[1], lines)
        self.assertEqual([], sorted(str(path) for path in agent_folder.rglob("*")), msg="status wrote below the agent folder")
        self.assertFalse((agent_folder / "extensions").exists())


if __name__ == "__main__":
    unittest.main()
