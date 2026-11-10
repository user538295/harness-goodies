"""The controller behind `estimate.py`: parses the arguments, composes the adapters of one run, dispatches the command and turns
every outcome into stdout JSON, `estimate:` lines on stderr and an exit code (0 ok, 1 runtime error, 2 bad arguments)."""
import os

from estimation.arguments import parse
from estimation.commands import Commands
from estimation.errors import ArgumentError, EstimateError
from estimation.project import Project
from estimation.store import EstimationStore, SchemaCheck, to_json

EXIT_OK = 0
EXIT_RUNTIME_ERROR = 1
EXIT_BAD_ARGUMENTS = 2
ERROR_PREFIX = "estimate: "
PLAN_PROGRESS_FROM_SKILL = (os.pardir, "implement", "scripts", "plan-progress.sh")
REPORT_ASSETS = "report"  # the skill's folder of the report template and the vendored Chart.js


def _line(message):
    return ERROR_PREFIX + message + "\n"


def _use_utf8(stream):
    """Plain stdout and stderr follow the locale (cp1252 on Windows); descriptions and prompts hold characters outside it."""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace", newline="\n")


def _emit(stream, text):
    """Writes `text`; False when the stream is gone (a closed pipe must not raise)."""
    try:
        stream.write(text)
        stream.flush()
    except (OSError, ValueError):
        return False
    return True


def _dispatch(invocation, environment, skill_dir):
    project = Project(environment.get_cwd(), environment.run)
    check = SchemaCheck(skill_dir, environment.which, environment.run)
    commands = Commands(project, EstimationStore(project.root, check), environment,
                        os.path.normpath(os.path.join(skill_dir, *PLAN_PROGRESS_FROM_SKILL)),
                        os.path.join(skill_dir, REPORT_ASSETS))
    options = invocation.options
    positionals = invocation.positionals
    handlers = {
        "read": lambda: commands.read(options["--references"]),
        "save": lambda: commands.save(positionals[0]),
        "remove": lambda: commands.remove(positionals[0]),
        "doc-stats": lambda: commands.doc_stats(positionals[0], options["--plan-progress"]),
        "git-stats": lambda: commands.git_stats(positionals[0]),
        "match-sessions": lambda: commands.match_sessions(positionals[0], options["--session-log"]),
        "usage": lambda: commands.usage(positionals[0], options["--session-log"]),
        "record-commit": lambda: commands.record_commit(positionals[0], positionals[1], options["--harness"],
                                                        options["--session-log"], options["--session-dir"]),
        "estimator": lambda: commands.estimator(options["--harness"], options["--session-log"], options["--session-dir"]),
        "neighbours": lambda: commands.neighbours(positionals[0]),
        "place": lambda: commands.place(positionals[0]),
        "anchor": lambda: commands.anchor(positionals[0]),
        "find-sets": lambda: commands.find_sets(),
        "check-text": lambda: commands.check_text(positionals[0]),
        "calculate": lambda: commands.calculate(positionals[0]),
        "report": lambda: commands.report(options["--out"]),
    }
    return handlers[invocation.command]()


def _execute(argv, environment, skill_dir):
    """Returns (code, stdout text, stderr text)."""
    try:
        invocation = parse(argv)
    except ArgumentError as error:
        return EXIT_BAD_ARGUMENTS, "", _line(str(error))
    try:
        payload, warnings = _dispatch(invocation, environment, skill_dir)
    except ArgumentError as error:
        return EXIT_BAD_ARGUMENTS, "", _line(str(error))
    except (EstimateError, OSError) as error:
        return EXIT_RUNTIME_ERROR, "", _line(str(error))
    except KeyboardInterrupt:
        return EXIT_RUNTIME_ERROR, "", _line("interrupted")
    except Exception as error:  # a defect must end as one line, never a traceback
        detail = str(error)
        name = type(error).__name__
        return EXIT_RUNTIME_ERROR, "", _line("internal error: " + (name + ": " + detail if detail else name))
    return EXIT_OK, to_json(payload), "".join(_line(warning) for warning in warnings)


def run(argv, environment, skill_dir):
    """Runs one command and returns the exit code."""
    _use_utf8(environment.stdout)
    _use_utf8(environment.stderr)
    code, out_text, err_text = _execute(argv, environment, skill_dir)
    if out_text and not _emit(environment.stdout, out_text) and code == EXIT_OK:
        code = EXIT_RUNTIME_ERROR
    if err_text:
        _emit(environment.stderr, err_text)
    return code
