"""The files a Codex rollout's tool calls write with `apply_patch`; pure, no file access. A call is the `payload` of a `response_item`
record; the files are the ones the headers of the patch it applies name (see `patch_headers`), and each relative one is made absolute.

Exactly these shapes apply a patch (the local rollouts hold the first two; the third and fourth are Codex's own shell forms, which
no local rollout has: their tests are built from Codex's documented shapes, not from a real record):
- a native `custom_tool_call` (or a `function_call` whose `arguments` hold the patch as `input`) named `apply_patch`;
- a `custom_tool_call` named `exec` (Codex desktop: free-form JavaScript) whose code, outside any string, comment or template
  literal, calls `tools.apply_patch(` with a string literal (`"` or `'`) or with a name that an earlier `const name = <literal>;`
  bound (the `;` ends the statement, so a continued expression such as `"a" + "b"` binds nothing), and nothing else, as its only
  argument; the literal is read with its JavaScript escapes;
- a `function_call` named `shell` whose `command` is the vector `["apply_patch" | "applypatch", <patch>]`;
- a `function_call` named `shell`, `shell_command` or `exec_command` whose script (the `command` or `cmd` text, or the third element
  of `[<bash | sh | zsh, also by path such as /bin/bash>, "-c" | "-lc", <script>]`) is, after an optional `cd <dir> &&` (the dir bare
  or in quotes, without any expansion), an `apply_patch <<EOF` heredoc and nothing else: the heredoc ends at the first line that is
  its delimiter, and any text after that line, a second heredoc or a mid-body delimiter line included, makes the script more than the
  heredoc.
In all of them the patch must open with `*** Begin Patch`. Deliberately ignored, so that a document quoting a patch gives no
path: a patch header in any other text of a call (a string only quoted, logged, written to a file, passed to another tool,
or merely held by a variable or `let`); the call text inside a string, comment or template literal; an argument that is a template
literal, a concatenation or any other expression; other objects or names (`mytools.apply_patch`); a heredoc of another command
(`cat <<EOF`) or one followed by further commands; `tools.exec_command` or any other tool nested in an `exec` source; and every
other call name.

Where the files are: the header paths of Codex are relative to the working directory of the call, while the other harnesses report
absolute ones. A relative path is joined, from the outside in, to the `cwd` the caller gives (the turn's, else the session's), the
call's `workdir` argument (a shell form) and the target of a `cd <dir> &&` prefix, each one relative to the one before it; an
absolute path stays as it is. Without any directory a relative path stays relative (the estimate command matches a path by its tail).

A call fails when its output (a `custom_tool_call_output` or `function_call_output` record with the `call_id` of the call) says
`apply_patch verification failed`: Codex then applied no patch (the real rollouts show it for an `exec` call as `Script error:` followed
by that text). `failed_call_id` names the call of such an output. It is the only failure Codex records in a shape known here; an output
is linked to its call by `call_id` alone, and a script that applied one patch and failed on a later one counts as failed as a whole."""
import posixpath
import re
from pathlib import PurePath
from types import MappingProxyType
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from .json_records import Record, decode_record
from .patch_headers import opens_a_patch, patch_paths

_CUSTOM_TOOL_CALL = "custom_tool_call"
_FUNCTION_CALL = "function_call"
_CALL_OUTPUTS = frozenset({"custom_tool_call_output", "function_call_output"})
_APPLY_PATCH = "apply_patch"
_EXEC = "exec"
_SHELL_CALLS = frozenset({"shell", "shell_command", "exec_command"})
_APPLY_PATCH_PROGRAMS = frozenset({"apply_patch", "applypatch"})
_SHELLS = frozenset({"bash", "sh", "zsh"})
_SHELL_SCRIPT_FLAGS = frozenset({"-c", "-lc"})
_PATCH_VECTOR_LENGTH = 2  # [program, patch]
_PROGRAM, _PATCH_ARGUMENT = 0, 1
_SHELL_VECTOR_LENGTH = 3  # [shell, flag, script]
_SHELL_PROGRAM, _SHELL_FLAG, _SCRIPT = 0, 1, 2
_VERIFICATION_FAILED = "apply_patch verification failed"
_JS_OPAQUE = re.compile(r"""//[^\n]*|/\*.*?\*/|"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`""", re.DOTALL)
_JS_BINDING = re.compile(r"""(?<![\w$.])const\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*(?P<quote>["'])(?P<literal>_*)(?P=quote)\s*;""")
_JS_CALL = re.compile(r"""(?<![\w$.])tools\s*\.\s*apply_patch\s*\(\s*(?:(?P<quote>["'])(?P<literal>_*)(?P=quote)|(?P<name>[A-Za-z_$][\w$]*))\s*\)""")
_JS_ESCAPE = re.compile(r"\\(?:u\{([0-9A-Fa-f]+)\}|u([0-9A-Fa-f]{4})|x([0-9A-Fa-f]{2})|(\r\n|[\s\S]))")
_JS_SIMPLE_ESCAPES = MappingProxyType({"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0", "\r\n": "", "\n": "", "\r": ""})
_SURROGATE = re.compile(r"[\ud800-\udfff]")
# `cd <dir> &&`: the dir is bare or quoted, and holds nothing a shell would expand (`$`, a backtick, `~`, a glob, a backslash).
_CD_PREFIX = r"""(?:cd[ \t]+(?:"(?P<double>[^"$`\\]*)"|'(?P<single>[^']*)'|(?P<bare>[^\s"'$`\\&;|<>()*?~]+))[ \t]*&&\s*)?"""
_HEREDOC_SCRIPT = re.compile(r"\A\s*" + _CD_PREFIX + r"""(?:apply_patch|applypatch)[ \t]+<<-?[ \t]*(?P<quote>['"]?)(?P<tag>\w+)(?P=quote)[ \t]*\n"""
                             r"(?P<body>.*?)\n(?P=tag)[ \t]*(?:\n|\Z)", re.DOTALL)


class _Applied(NamedTuple):
    patch: str
    directories: Tuple[str, ...] = ()  # where the call's relative paths start inside the turn's directory, outermost first


def _arguments_of(payload: Record) -> Optional[Record]:
    return decode_record(payload["arguments"]) if isinstance(payload.get("arguments"), str) else None


def _native_patch(payload: Record) -> Any:
    """The patch text of a native `apply_patch` call: a custom call's `input`, a function call's `input` argument (the arguments
    are JSON text)."""
    kind = payload.get("type")
    if kind == _CUSTOM_TOOL_CALL:
        return payload.get("input")
    if kind == _FUNCTION_CALL:
        arguments = _arguments_of(payload)
        return arguments.get("input") if arguments is not None else None
    return None


def _js_literal_text(source: str, span: Tuple[int, int]) -> Optional[str]:
    """The string a JavaScript string literal's inner text (`span` of `source`) stands for; None when an escape is no code point."""
    def decoded(match: "re.Match[str]") -> str:
        braced, short, hexadecimal, plain = match.groups()
        digits = braced or short or hexadecimal
        return chr(int(digits, 16)) if digits else _JS_SIMPLE_ESCAPES.get(plain, plain)

    try:
        text = _JS_ESCAPE.sub(decoded, source[span[0]:span[1]])
    except (ValueError, OverflowError):
        return None
    return text.encode("utf-16", "surrogatepass").decode("utf-16", "replace") if _SURROGATE.search(text) else text


def _blanked(opaque: "re.Match[str]") -> str:
    """The same-length stand-in of a string, comment or template literal of JavaScript: a string keeps its quotes around filler, so
    a pattern can see a literal's place without its content; a template and a comment leave nothing to see."""
    text = opaque.group(0)
    quote = text[0]
    if quote in "\"'":
        return quote + "_" * (len(text) - 2) + quote
    return " " * len(text)


def _exec_patches(source: str) -> List[str]:
    """The patches the code of an `exec` call applies: its `tools.apply_patch(` calls outside any string, comment or template literal
    whose only argument is a string literal, or a name an earlier `const` bound to one. The code is searched with strings blanked
    (same length), so that a match lies in code and maps back to the literal's text."""
    if _APPLY_PATCH not in source:
        return []
    code = _JS_OPAQUE.sub(_blanked, source)
    steps = sorted([(m.start(), m) for m in _JS_BINDING.finditer(code)] + [(m.start(), m) for m in _JS_CALL.finditer(code)], key=lambda step: step[0])
    bound: Dict[str, Optional[str]] = {}
    patches: List[str] = []
    for _, match in steps:
        groups = match.groupdict()
        if match.re is _JS_BINDING:
            bound[groups["name"]] = _js_literal_text(source, match.span("literal"))
        else:
            patch = _js_literal_text(source, match.span("literal")) if groups["quote"] else bound.get(groups["name"])
            if opens_a_patch(patch):
                patches.append(patch)
    return patches


def _is_patch_vector(command: Any) -> bool:
    """Whether a `command` is `[apply_patch | applypatch, <argument>]`."""
    return (isinstance(command, list) and len(command) == _PATCH_VECTOR_LENGTH and isinstance(command[_PROGRAM], str)
            and command[_PROGRAM] in _APPLY_PATCH_PROGRAMS)


def _is_script_vector(command: Any) -> bool:
    """Whether a `command` is `[<shell>, -c | -lc, <script>]`, the shell named bare or by path."""
    return (isinstance(command, list) and len(command) == _SHELL_VECTOR_LENGTH and isinstance(command[_SHELL_PROGRAM], str)
            and posixpath.basename(command[_SHELL_PROGRAM]) in _SHELLS and command[_SHELL_FLAG] in _SHELL_SCRIPT_FLAGS)


def _script_of(command: Any) -> Optional[str]:
    """The shell script a `command` holds: the text itself, or the third element of `[shell, -c | -lc, script]`."""
    if isinstance(command, str):
        return command
    if _is_script_vector(command):
        return command[_SCRIPT] if isinstance(command[_SCRIPT], str) else None
    return None


def _heredoc_applied(script: str, workdir: Optional[str]) -> List[_Applied]:
    """The patch of a script that is only an `apply_patch` heredoc, after an optional `cd <dir> &&`."""
    heredoc = _HEREDOC_SCRIPT.match(script)
    if heredoc is None or script[heredoc.end():].strip() or not opens_a_patch(heredoc["body"]):
        return []
    target = heredoc["double"] or heredoc["single"] or heredoc["bare"]
    return [_Applied(heredoc["body"], tuple(directory for directory in (workdir, target) if directory))]


def _shell_patches(payload: Record) -> List[_Applied]:
    """The patch a shell call applies: the argument of an `apply_patch` argument vector, or the body of a script that is only an
    `apply_patch` heredoc."""
    arguments = _arguments_of(payload)
    if arguments is None:
        return []
    workdir = arguments.get("workdir")
    workdir = workdir if isinstance(workdir, str) else None
    command = arguments.get("command")
    if _is_patch_vector(command):
        patch = command[_PATCH_ARGUMENT]
        return [_Applied(patch, (workdir,) if workdir else ())] if opens_a_patch(patch) else []
    script = _script_of(command if command is not None else arguments.get("cmd"))
    return _heredoc_applied(script, workdir) if script is not None else []


def _is_exec_source(payload: Record) -> bool:
    return payload.get("type") == _CUSTOM_TOOL_CALL and isinstance(payload.get("input"), str)


def _applied_by(payload: Record) -> List[_Applied]:
    """The patches a call applies, by the shapes the module docstring lists; none for any other call."""
    name = payload.get("name")
    if name == _APPLY_PATCH:
        patch = _native_patch(payload)
        return [_Applied(patch)] if opens_a_patch(patch) else []
    if name == _EXEC and _is_exec_source(payload):
        return [_Applied(patch) for patch in _exec_patches(payload["input"])]
    if isinstance(name, str) and name in _SHELL_CALLS and payload.get("type") == _FUNCTION_CALL:
        return _shell_patches(payload)
    return []


def _absolute(path: str, directories: Tuple[Optional[str], ...]) -> str:
    """`path` joined to the directories, outermost first (an absolute one starts over), when it is relative; as it is otherwise."""
    known = tuple(directory for directory in directories if directory)
    if not known or PurePath(path).is_absolute():
        return path
    return str(PurePath(*known, path))


def paths_of_call(payload: Record, directory: Optional[str]) -> Tuple[str, ...]:
    """The files the call applies patches to, in order, as often as its patches name them (none for any call that is no patch);
    `directory` is the working directory the turn gives, which relative paths start from."""
    return tuple(_absolute(path, (directory,) + applied.directories) for applied in _applied_by(payload) for path in patch_paths(applied.patch))


def _output_text(output: Any) -> str:
    """The text of a call output: the string itself or the `text` of each of its blocks."""
    if isinstance(output, str):
        return output
    blocks = output if isinstance(output, list) else ()
    return "\n".join(block["text"] for block in blocks if isinstance(block, dict) and isinstance(block.get("text"), str))


def failed_call_id(payload: Record) -> Optional[str]:
    """The `call_id` of the call that a call-output record reports as failed (`apply_patch verification failed`); None for any other
    record, and for an output without a `call_id`."""
    kind, call_id = payload.get("type"), payload.get("call_id")
    if not isinstance(kind, str) or kind not in _CALL_OUTPUTS or not isinstance(call_id, str) or not call_id:
        return None
    return call_id if _VERIFICATION_FAILED in _output_text(payload.get("output")) else None
