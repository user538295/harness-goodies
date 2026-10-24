"""Repo scan (S58): no tracked file refers to the removed session-log components outside the files S58 exempts.

Runs `git grep` over the tracked files of this checkout; skipped when `git` is not on `PATH` or the checkout is not the top of a git work tree.
The legacy strings are built from pieces so that this file does not itself match its own scan.
"""
import shutil
import subprocess
import unittest
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parents[3]

LEGACY_STRINGS = (
    "install" + ".sh",
    "bin/session" + "-log",
    "adapters" + "/",
    "install_hooks" + ".py",
    "omp." + "extensions",
    "skills/session-log/" + "VERSION",
)

LEFTOVER_SCAN_MODULE = "skills/session-log/sessionlog/leftover_scan.py"
EXEMPT_FILES = frozenset(
    {
        LEFTOVER_SCAN_MODULE,
        "skills/session-log/tests/test_leftover_scan.py",
        "skills/session-log/tests/test_locator.sh",
        "skills/commit/SKILL.md",
    }
)
EXEMPT_PLAN_FILE_PREFIX = "session-log-simple-"
GIT_GREP_NO_MATCH = 1


def _is_exempt(path: str) -> bool:
    return path in EXEMPT_FILES or ("/" not in path and path.startswith(EXEMPT_PLAN_FILE_PREFIX))


def _tracked_files_naming_legacy_strings() -> List[str]:
    command = ["git", "-c", "core.quotePath=false", "grep", "--no-color", "--files-with-matches", "--fixed-strings"]
    for legacy in LEGACY_STRINGS:
        command += ["-e", legacy]
    result = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if result.returncode not in (0, GIT_GREP_NO_MATCH):
        raise RuntimeError("git grep failed: " + result.stderr.strip())
    return sorted(result.stdout.splitlines())


def _is_git_work_tree() -> bool:
    probe = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    return probe.returncode == 0 and Path(probe.stdout.strip()).resolve() == REPO_ROOT


@unittest.skipUnless(shutil.which("git") and _is_git_work_tree(), "git is not on PATH or the checkout is not the top of a git work tree")
class RepoScanTest(unittest.TestCase):
    def test_git_grep_lists_only_exempt_files(self) -> None:
        matching = _tracked_files_naming_legacy_strings()
        self.assertIn(LEFTOVER_SCAN_MODULE, matching, "the scan found nothing it must find: git grep is not covering the checkout")
        offenders = [path for path in matching if not _is_exempt(path)]
        self.assertEqual([], offenders, "tracked files name removed session-log components outside the S58 exempt list")


if __name__ == "__main__":
    unittest.main()
