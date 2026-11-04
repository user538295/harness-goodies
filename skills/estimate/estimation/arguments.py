"""Parses `estimate.py <command> ...` into an `Invocation`. Never argparse: its usage text is not ours, and every argument error
must be one `estimate:` line with exit code 2."""
from dataclasses import dataclass
from enum import Enum

from estimation.errors import ArgumentError
from estimation.features import HARNESSES

OPTION_PREFIX = "--"
HARNESS_OPTION = "--harness"


class Kind(Enum):
    FLAG = "flag"          # present or not
    SINGLE = "single"      # at most once, with a value
    REQUIRED = "required"  # exactly once, with a value


@dataclass(frozen=True)
class Spec:
    positionals: tuple[str, ...]
    options: dict[str, Kind]


COMMANDS = {
    "read": Spec((), {"--references": Kind.FLAG}),
    "save": Spec(("<entry-json | ->",), {}),
    "remove": Spec(("<name>",), {}),
    "doc-stats": Spec(("<file.md>",), {"--plan-progress": Kind.SINGLE}),
    "git-stats": Spec(("<features-json | ->",), {}),
    "match-sessions": Spec(("<features-json | ->",), {"--session-log": Kind.REQUIRED}),
    "record-commit": Spec(("<plan-file>", "<commit>"), {HARNESS_OPTION: Kind.REQUIRED, "--session-log": Kind.SINGLE}),
}


@dataclass(frozen=True)
class Invocation:
    """`options` holds every option of the command: True/False for a FLAG, else the value or None."""
    command: str
    positionals: list[str]
    options: dict[str, object]


def _argument_count(count):
    return "no arguments" if count == 0 else "1 argument" if count == 1 else "%d arguments" % count


def _empty_options(spec):
    return {name: False if kind is Kind.FLAG else None for name, kind in spec.options.items()}


def parse(argv) -> Invocation:
    if not argv:
        raise ArgumentError("missing command (expected %s)" % ", ".join(COMMANDS))
    command, rest = argv[0], argv[1:]
    spec = COMMANDS.get(command)
    if spec is None:
        raise ArgumentError("unknown command '%s' (expected %s)" % (command, ", ".join(COMMANDS)))
    options = _empty_options(spec)
    positionals = []
    index = 0
    while index < len(rest):
        argument = rest[index]
        index += 1
        if not argument.startswith(OPTION_PREFIX):
            positionals.append(argument)
            continue
        kind = spec.options.get(argument)
        if kind is None:
            raise ArgumentError("unknown option '%s' for %s" % (argument, command))
        if kind is Kind.FLAG:
            options[argument] = True
            continue
        if index >= len(rest) or rest[index].startswith(OPTION_PREFIX):
            raise ArgumentError("%s needs a value" % argument)
        value = rest[index]
        index += 1
        if options[argument] is not None:
            raise ArgumentError("%s given twice" % argument)
        options[argument] = value
    if len(positionals) < len(spec.positionals):
        raise ArgumentError("%s needs %s" % (command, spec.positionals[len(positionals)]))
    if len(positionals) > len(spec.positionals):
        raise ArgumentError("%s takes %s" % (command, _argument_count(len(spec.positionals))))
    for name, kind in spec.options.items():
        if kind is Kind.REQUIRED and options[name] is None:
            raise ArgumentError("%s needs %s <value>" % (command, name))
    harness = options.get(HARNESS_OPTION)
    if harness is not None and harness not in HARNESSES:
        raise ArgumentError("unsupported harness '%s' (supported: %s)" % (harness, ", ".join(HARNESSES)))
    return Invocation(command, positionals, options)
