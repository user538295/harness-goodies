"""Entry script of the estimate skill: `estimate.py read|save|remove|doc-stats|git-stats|match-sessions|record-commit|estimator|
report ...`.

This file must parse on Python 3.4, so a Python that is too old gets a clear message instead of a SyntaxError: it uses no
f-strings, no variable annotations and no other newer syntax, and it imports the `estimation` package only after the version
check. Every other step (arguments, commands, every `estimate:` text on stderr) lives in `estimation.cli`. Exit codes: 0 ok,
1 runtime error, 2 bad arguments; never a traceback.
"""
import os
import sys

MINIMUM_PYTHON = (3, 14)
MINIMUM_TEXT = "%d.%d" % MINIMUM_PYTHON
EXIT_RUNTIME_ERROR = 1
ERROR_PREFIX = "estimate: "
SKILL_DIR = os.path.dirname(os.path.realpath(__file__))
FIX_HINTS = {
    "darwin": "brew install python@{0} or python.org/downloads, then make python3 resolve to it".format(MINIMUM_TEXT),
    "win32": "install the Python install manager from python.org/downloads/windows",
}
FIX_HINT_OTHER = ("install Python {0} with your package manager, pyenv or uv, "
                  "then make python3 resolve to it").format(MINIMUM_TEXT)


def _version_text(version_info, platform):
    requirement = "Python {0}+ required (found {1}.{2})".format(MINIMUM_TEXT, version_info[0], version_info[1])
    fix = "fix: " + FIX_HINTS.get(platform, FIX_HINT_OTHER)
    return ERROR_PREFIX + requirement + "\n" + ERROR_PREFIX + fix + "\n"


def _ensure_skill_dir_importable():
    if SKILL_DIR not in sys.path:
        sys.path.insert(0, SKILL_DIR)


def main(argv, environment=None):
    """Runs one command and returns the exit code. `environment` (an `estimation.environment.Environment`) is the test
    injection point; without it the real machine is used."""
    version_info = sys.version_info if environment is None else environment.version_info
    if tuple(version_info[:2]) < MINIMUM_PYTHON:
        stderr = sys.stderr if environment is None else environment.stderr
        platform = sys.platform if environment is None else environment.platform
        try:
            stderr.write(_version_text(version_info, platform))
            stderr.flush()
        except (OSError, ValueError):
            pass
        return EXIT_RUNTIME_ERROR
    _ensure_skill_dir_importable()
    from estimation.cli import run
    from estimation.environment import Environment
    return run(argv, Environment() if environment is None else environment, SKILL_DIR)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
