"""Entry script of the session-log skill: `session_log.py <harness> on|off|status|usage [--latest|<id>]` and the hook entry
`session_log.py hook <harness> <event>`.

This file must parse on Python 3.4, so a Python that is too old gets a clear message instead of a SyntaxError: it uses no
f-strings, no variable annotations and no other newer syntax, and it imports the `sessionlog` package only after the
version check. It is the composition root (every adapter is built here) and the controller (argv, exit codes, every
`session-log:` text on stderr). Exit codes: 0 ok, 1 runtime error, 2 bad arguments; never a traceback. A hook is not a
command: it is told apart before any argument parsing, always exits 0 and never prints.
"""
import os
import sys
from datetime import datetime

MINIMUM_PYTHON = (3, 14)
MINIMUM_TEXT = "%d.%d" % MINIMUM_PYTHON
EXIT_OK = 0
EXIT_RUNTIME_ERROR = 1
EXIT_BAD_ARGUMENTS = 2
ERROR_PREFIX = "session-log: "
HOOK_ARGUMENT = "hook"
LATEST_FLAG = "--latest"
OPTION_PREFIX = "--"
COMMANDS = ("on", "off", "status", "usage")
SUPPORTED_HARNESS_ROOTS = {"claude": (".claude",), "codex": (".codex",), "cursor": (".cursor",), "omp": (".omp", "agent"),
                           "opencode": (".config", "opencode")}
CURSOR_HARNESS = "cursor"
CURSOR_RECORD_EVENTS = ("beforeSubmitPrompt", "afterAgentResponse", "stop", "subagentStop")
OPENCODE_DATA_HOME = (".local", "share")
OPENCODE_DATA_FOLDER = OPENCODE_DATA_HOME + ("opencode",)
# Per harness, the variables that can move its root (C1: the relocated-root check), each as (variable, how the message calls the root it
# moves, that root's default below home).
RELOCATION_VARIABLES = {"opencode": (("XDG_DATA_HOME", "OpenCode's XDG data home", OPENCODE_DATA_HOME),)}
LOG_FOLDER_NAME = "prompt-logs"
PRICE_FILE_NAME = "prices.json"
SKILL_DIR = os.path.dirname(os.path.realpath(__file__))
PRICE_FILE_PATH = os.path.join(SKILL_DIR, PRICE_FILE_NAME)
FIX_HINTS = {
    "darwin": "brew install python@{0} or python.org/downloads, then make python3 resolve to it".format(MINIMUM_TEXT),
    "win32": "install the Python install manager from python.org/downloads/windows",
}
FIX_HINT_OTHER = ("install Python {0} with your package manager, pyenv or uv, "
                  "then make python3 resolve to it").format(MINIMUM_TEXT)


class ArgumentError(Exception):
    """Bad command-line arguments; the message is printed after `session-log: ` and the exit code is 2."""


class RuntimeFailure(Exception):
    """A runtime error of the entry itself; the message is printed after `session-log: ` and the exit code is 1."""


class Environment(object):
    """Everything a run takes from the machine; each argument defaults to the real thing and is a test injection point. `env` is the
    whole set of environment variables the run reads (tests pass their own, so the developer's never leak in).
    `run_probe` stays None until `_compose`, because the package that holds the real runner is not importable before the
    version check; `stdin` (a binary stream), `time_ns` and `pid` stay None until the hook that needs them: the real standard input
    is read by `read_stdin`, the real clock and process id are the defaults of the hook records module."""

    def __init__(self, stdout=None, stderr=None, version_info=None, platform=None, home=None, get_cwd=None, now=None,
                 to_local=None, path_module=None, run_probe=None, env=None, stdin=None, time_ns=None, pid=None):
        self.stdin = stdin
        self.time_ns = time_ns
        self.pid = pid
        self.stdout = sys.stdout if stdout is None else stdout
        self.stderr = sys.stderr if stderr is None else stderr
        self.version_info = sys.version_info if version_info is None else version_info
        self.platform = sys.platform if platform is None else platform
        self.home = home
        self.get_cwd = os.getcwd if get_cwd is None else get_cwd
        self.now = (lambda: datetime.now().astimezone()) if now is None else now
        self.to_local = (lambda moment: moment.astimezone()) if to_local is None else to_local
        self.path_module = os.path if path_module is None else path_module
        self.run_probe = run_probe
        self.env = os.environ if env is None else env

    def read_stdin(self):
        """The raw bytes of the hook input: the injected binary stream, else the real standard input (no input at all when the
        process has none)."""
        stream = self.stdin
        if stream is None:
            stream = getattr(sys.stdin, "buffer", None)
        return b"" if stream is None else stream.read()


def _error_text(message):
    return ERROR_PREFIX + message + "\n"


def _version_text(version_info, platform):
    requirement = "Python {0}+ required (found {1}.{2})".format(MINIMUM_TEXT, version_info[0], version_info[1])
    fix = "fix: " + FIX_HINTS.get(platform, FIX_HINT_OTHER)
    return _error_text(requirement) + _error_text(fix)


def _use_utf8(stream):
    """Plain stdout and stderr follow the locale (cp1252 on Windows); reports hold characters outside it."""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace", newline="\n")


def _parse(argv, supported_harnesses):
    """Returns (harness, command, usage target or None). Never argparse: its usage text is not ours."""
    harnesses = ", ".join(sorted(supported_harnesses))
    if not argv:
        raise ArgumentError("missing harness (supported: %s)" % harnesses)
    harness = argv[0]
    if harness not in supported_harnesses:
        raise ArgumentError("unsupported harness '%s' (supported: %s)" % (harness, harnesses))
    expected = ", ".join(COMMANDS[:-1]) + " or " + COMMANDS[-1]
    if len(argv) < 2:
        raise ArgumentError("missing command (expected %s)" % expected)
    command, rest = argv[1], argv[2:]
    if command not in COMMANDS:
        raise ArgumentError("unknown command '%s' (expected %s)" % (command, expected))
    if command != "usage":
        if rest:
            raise ArgumentError("%s takes no arguments" % command)
        return harness, command, None
    if len(rest) > 1:
        raise ArgumentError("usage takes at most one argument: %s or a session id" % LATEST_FLAG)
    if not rest or rest[0] == LATEST_FLAG:
        return harness, command, None
    if rest[0] == "":
        raise ArgumentError("usage target must not be empty (expected %s or a session id)" % LATEST_FLAG)
    if rest[0].startswith(OPTION_PREFIX):
        raise ArgumentError("unknown option '%s' (expected %s or a session id)" % (rest[0], LATEST_FLAG))
    return harness, command, rest[0]


def _opencode_reader(home, path_module, env):
    """The reader of the database `choose_database` picks; a choice the run refuses ends it with the entry's own text."""
    from sessionlog.opencode_database import DatabaseOutsideDataFolderError, InMemoryDatabaseError, choose_database
    from sessionlog.opencode_reader import OpenCodeReader
    try:
        database_path = choose_database(path_module.join(home, *OPENCODE_DATA_FOLDER), env)
    except InMemoryDatabaseError:
        raise RuntimeFailure("OpenCode in-memory databases are unsupported")
    except DatabaseOutsideDataFolderError as error:
        raise RuntimeFailure("OpenCode database path is not inside the OpenCode data folder: %s" % error.path)
    return OpenCodeReader(database_path)


def _build_reader(harness, root, home, path_module, env):
    if harness == "claude":
        from sessionlog.claude_reader import ClaudeReader
        return ClaudeReader(root)
    if harness == "codex":
        from sessionlog.codex_reader import CodexReader
        return CodexReader(root)
    if harness == "omp":
        from sessionlog.omp_reader import OmpReader
        return OmpReader(root)
    if harness == "opencode":
        return _opencode_reader(home, path_module, env)
    if harness == CURSOR_HARNESS:
        from sessionlog.cursor_reader import CursorReader
        from sessionlog.cursor_records import RECORDS_FOLDER
        return CursorReader(path_module.join(root, LOG_FOLDER_NAME, RECORDS_FOLDER))
    raise RuntimeFailure("no reader for harness '%s'" % harness)


def _refuse_relocated_root(harness, home, environment):
    """The relocated-root check of C1 (right after the version check): a variable that moves the harness's root away from its default
    ends the run before anything is read or written. A variable that is empty counts as unset, and one that spells out the default
    root works as unset."""
    path_module = environment.path_module
    for variable, label, default_parts in RELOCATION_VARIABLES.get(harness, ()):
        actual = environment.env.get(variable, "")
        default = path_module.join(home, *default_parts)
        if actual and path_module.normcase(path_module.realpath(actual)) != path_module.normcase(path_module.realpath(default)):
            raise RuntimeFailure("%s is relocated: %s is %s; session-log supports only the default %s" % (label, variable, actual, default))


def _home_folder(environment):
    """The absolute, non-root home folder of the run."""
    path_module = environment.path_module
    home = environment.home if environment.home is not None else os.path.expanduser("~")
    if not path_module.isabs(home) or path_module.dirname(home) == home:
        raise RuntimeFailure("cannot determine the home folder")
    return home


def _harness_folders(harness, environment):
    """Returns (home, root, log root) of the harness; a relocated root ends the run first (the check of C1 after the version check)."""
    home = _home_folder(environment)
    _refuse_relocated_root(harness, home, environment)
    root = environment.path_module.join(home, *SUPPORTED_HARNESS_ROOTS[harness])
    return home, root, environment.path_module.join(root, LOG_FOLDER_NAME)


class _Composition(object):
    """The adapters of one run, the sync use case, the usage target resolver, the leftover scan and the Python probe; command use
    cases are built over them only for a command."""

    def __init__(self, sync, targets, archive, state, pricing, log_root, leftover_scan, python_probe):
        self.sync = sync
        self.targets = targets
        self.archive = archive
        self.state = state
        self.pricing = pricing
        self.log_root = log_root
        self.leftover_scan = leftover_scan
        self.python_probe = python_probe


def _compose(harness, environment):
    """Builds every adapter of one run, the sync use case and the usage target resolver over them, and ends by converting an old
    release's `.enabled` flag, so what a command or the hook reads afterwards is the converted state. A relocated root ends the run
    first, and a database the environment names but the run refuses ends it before the flag is converted."""
    from sessionlog.leftover_scan import LeftoverScan
    from sessionlog.log_usage_writer import LogUsageWriter
    from sessionlog.model import Harness
    from sessionlog.price_file import PriceFile
    from sessionlog.pricing import PricingRules
    from sessionlog.python_probe import PythonProbeRunner, run_command
    from sessionlog.state import FileStateStore
    from sessionlog.sync import SyncUseCase
    from sessionlog.usage_target import UsageTargetResolver
    from pathlib import Path

    path_module = environment.path_module
    home, root, log_root = _harness_folders(harness, environment)
    price_book = PriceFile(Path(PRICE_FILE_PATH))
    archive = LogUsageWriter(log_root, environment.to_local, path_module)
    state = FileStateStore(log_root, environment.now)
    reader = _build_reader(harness, root, home, path_module, environment.env)
    pricing = PricingRules(price_book)
    sync = SyncUseCase(reader, archive, state, pricing, environment.now)
    state.migrate_enabled()
    probe = PythonProbeRunner(environment.platform, run_command if environment.run_probe is None else environment.run_probe)
    return _Composition(sync, UsageTargetResolver(reader, path_module), archive, state, pricing, log_root,
                        LeftoverScan(Harness(harness), root), probe)


def _run(harness, command, target, environment):
    """Returns (code, stdout text, stderr text) of one parsed command."""
    from sessionlog import presentation
    from sessionlog.command_error import CommandError
    from sessionlog.commands import CommandUseCases

    composition = _compose(harness, environment)
    use_cases = CommandUseCases(composition.sync, composition.targets, composition.archive, composition.state,
                                composition.pricing, environment.now, composition.log_root, composition.leftover_scan,
                                composition.python_probe)
    handlers = {
        "on": lambda: presentation.render_on(use_cases.on()),
        "off": lambda: presentation.render_off(use_cases.off()),
        "status": lambda: presentation.render_status(use_cases.status()),
        "usage": lambda: presentation.render_usage(
            use_cases.usage_latest(environment.get_cwd()) if target is None else use_cases.usage_of(target)),
    }
    try:
        return EXIT_OK, handlers[command](), ""
    except CommandError as error:
        return EXIT_RUNTIME_ERROR, "", _error_text(str(error))


def _execute(argv, environment):
    """Returns (code, stdout text, stderr text); every failure becomes one `session-log:` line."""
    try:
        _use_utf8(environment.stdout)
        _use_utf8(environment.stderr)
        try:
            harness, command, target = _parse(argv, SUPPORTED_HARNESS_ROOTS)
        except ArgumentError as error:
            return EXIT_BAD_ARGUMENTS, "", _error_text(str(error))
        return _run(harness, command, target, environment)
    except KeyboardInterrupt:
        return EXIT_RUNTIME_ERROR, "", _error_text("interrupted")
    except (OSError, RuntimeFailure) as error:
        return EXIT_RUNTIME_ERROR, "", _error_text(str(error))
    except Exception as error:  # a defect must end as one line, never a traceback
        detail = str(error)
        name = type(error).__name__
        return EXIT_RUNTIME_ERROR, "", _error_text("internal error: " + (name + ": " + detail if detail else name))


def _sync_at_session_start(harness, environment):
    """Syncs the harness's store within the hook budget. The hook input is not needed, so stdin is never read: a hook run
    with an inherited, open stdin must not wait on it."""
    from sessionlog.sync import HOOK_SYNC_BUDGET_SECONDS

    _compose(harness, environment).sync.sync(HOOK_SYNC_BUDGET_SECONDS)


def _cursor_records_when_on(environment):
    """The hook records of Cursor's `prompt-logs` folder while logging is on, else None, in the C1 order of a hook: the
    relocated-root check, the `.enabled` migration, then the `.log_enabled` check. Nothing is created while logging is off. A
    migration that created `.log_enabled` but could not remove `.enabled` does not stop the hook: logging is on."""
    from sessionlog.hook_records import HookRecords
    from sessionlog.state import FileStateStore

    _, _, log_root = _harness_folders(CURSOR_HARNESS, environment)
    state = FileStateStore(log_root, environment.now)
    try:
        state.migrate_enabled()
    except OSError:
        pass
    return HookRecords(log_root, environment.time_ns, environment.pid) if state.read_on_flag() is not None else None


def _cursor_record_handler(event):
    """Hook handler of one Cursor event that is recorded: one record file holding the raw input, while logging is on."""
    def handler(harness, environment):
        records = _cursor_records_when_on(environment)
        if records is not None:
            records.record(event, environment.read_stdin)
    return handler


def _cursor_session_start(harness, environment):
    """Cursor `sessionStart`, while logging is on: removes the record temps a killed hook left, then syncs. It writes no record
    and, like the Claude hook, never reads its input."""
    records = _cursor_records_when_on(environment)
    if records is not None:
        records.remove_stale_temps()
        _sync_at_session_start(harness, environment)


HOOK_HANDLERS = dict([(("claude", "SessionStart"), _sync_at_session_start), ((CURSOR_HARNESS, "sessionStart"), _cursor_session_start)]
                     + [((CURSOR_HARNESS, event), _cursor_record_handler(event)) for event in CURSOR_RECORD_EVENTS])


def _run_hook(arguments, environment):
    """Runs the hook named by `<harness> <event>` and always returns EXIT_OK without printing: a malformed argv, an unknown
    harness or event and any failure (even an interrupt) end silently, because a hook must never block or fail the harness."""
    try:
        handler = HOOK_HANDLERS.get(tuple(arguments))
        if handler is not None:
            handler(arguments[0], environment)
    except BaseException:  # nothing a hook does may reach the harness
        pass
    return EXIT_OK


def _silence_stream(stream):
    """Points the stream's descriptor at the null device so the interpreter's exit flush cannot fail again."""
    try:
        null_descriptor = os.open(os.devnull, os.O_WRONLY)
    except OSError:
        return
    try:
        os.dup2(null_descriptor, stream.fileno())
    except (OSError, ValueError, AttributeError):
        pass
    finally:
        os.close(null_descriptor)


def _emit(stream, text):
    """Writes `text`; False when the stream is gone (a closed pipe must not raise)."""
    try:
        stream.write(text)
        stream.flush()
    except (OSError, ValueError, AttributeError):
        _silence_stream(stream)
        return False
    return True


def _ensure_skill_dir_importable():
    if SKILL_DIR not in sys.path:
        sys.path.insert(0, SKILL_DIR)


def main(argv, environment=None):
    """Runs one command or hook and returns the exit code."""
    environment = Environment() if environment is None else environment
    is_hook = argv[:1] == [HOOK_ARGUMENT]
    if tuple(environment.version_info[:2]) < MINIMUM_PYTHON:
        if is_hook:
            return EXIT_OK  # a hook never speaks; an old Python records nothing
        _emit(environment.stderr, _version_text(environment.version_info, environment.platform))
        return EXIT_RUNTIME_ERROR
    _ensure_skill_dir_importable()
    if is_hook:
        return _run_hook(argv[1:], environment)
    code, out_text, err_text = _execute(argv, environment)
    if out_text and not _emit(environment.stdout, out_text) and code == EXIT_OK:
        code = EXIT_RUNTIME_ERROR
    if err_text:
        _emit(environment.stderr, err_text)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
