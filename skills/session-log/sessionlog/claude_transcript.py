"""Pure parsing of a Claude Code main-session transcript (its JSON lines) into prompts, and of a sub-agent transcript into
one run; no file access."""
import math
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum, auto
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Tuple, TypeVar, Union

from .json_records import Record, decode_record, parse_moment
from .model import Prompt, RequestUsage

_F = TypeVar("_F", bound="_RecordFolder")

TRANSCRIPT_SUFFIX = ".jsonl"
_USER = "user"
_ASSISTANT = "assistant"
_ATTACHMENT = "attachment"
_BRIDGE_SESSION = "bridge-session"
_QUEUED_COMMAND = "queued_command"
_QUEUED_PROMPT_MODE = "prompt"
_PROMPT_SOURCES = frozenset({"typed", "queued", "sdk", "suggestion_accepted"})
_TYPED_ENTRYPOINTS = frozenset({"sdk-py"})
_EXTERNAL_USER = "external"
_COMMAND_HEADS = ("<command-name>", "<command-message>")
_NOT_A_PROMPT_HEADS = ("<local-command-", "<bash-", "[Request interrupted by user", "Another Claude session sent a message:")
_INJECTED_HEAD = "<task-notification>"
_SYNTHETIC_MODEL = "<synthetic>"
_API_ERROR_FLAG = "isApiErrorMessage"
_TAG = re.compile(r"<[^>]*>")
_WHITESPACE = re.compile(r"\s+")
_COMMAND_NAME = re.compile(r"<command-name>(.*?)</command-name>", re.DOTALL)
_COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)
_FORKED_FROM = "forkedFrom"
_COMMAND_PART_SEPARATOR = " "
_TEXT_BLOCK = "text"
_REPLY_PARAGRAPH_SEPARATOR = "\n\n"
_PROMPT_TEXT_SEPARATOR = "\n"
_FAST_SPEED = "fast"
_MAX_TOKENS = 2**63 - 1
_NUMERIC_STRING = re.compile(r"\d+(\.\d+)?([eE][+-]?\d+)?", re.ASCII)

_MessageKey = Union[str, int]


@dataclass(frozen=True)
class Transcript:
    """`started_at` and `project_dir` are taken from the session's own records of any type, not from a fork's replayed
    history. `message_ids` are the ids of the assistant messages whose requests the prompts count.
    `unparsable`: the text has lines, none of them a JSON object."""

    started_at: Optional[datetime]
    project_dir: Optional[str]
    prompts: Tuple[Prompt, ...]
    message_ids: FrozenSet[str]
    unparsable: bool


def _token_count(value: Any) -> int:
    """Whole tokens in 0..int64 max from an int, float or plain ASCII decimal string; anything else is 0."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, str):
        match = _NUMERIC_STRING.fullmatch(value)
        if match is None:
            return 0
        try:
            value = float(value) if match.lastindex else int(value)
        except ValueError:
            return 0
    if isinstance(value, float):
        value = math.floor(value) if math.isfinite(value) else -1
    return value if isinstance(value, int) and 0 <= value <= _MAX_TOKENS else 0


def _moment(record: Record) -> Optional[datetime]:
    return parse_moment(record.get("timestamp"))


def _cwd(record: Record) -> Optional[str]:
    cwd = record.get("cwd")
    return cwd if isinstance(cwd, str) and cwd else None


def _message_of(record: Record) -> Record:
    message = record.get("message")
    return message if isinstance(message, dict) else {}


def _texts_of(content: Any) -> Tuple[str, ...]:
    if isinstance(content, str):
        texts = [content]
    else:
        blocks = content if isinstance(content, list) else []
        texts = [b.get("text") for b in blocks if isinstance(b, dict) and b.get("type") == _TEXT_BLOCK]
    return tuple(text for text in texts if isinstance(text, str) and text.strip())


def _text_blocks(record: Record) -> Tuple[str, ...]:
    return _texts_of(_message_of(record).get("content"))


class _Kind(Enum):
    """How sure a prompt is: a REAL one always counts, a REMOTE one (a plain user entry) only in a Remote Control
    session, a COMMAND one only when an assistant reply follows it."""

    REAL = auto()
    REMOTE = auto()
    COMMAND = auto()


@dataclass(frozen=True)
class _Opening:
    kind: _Kind
    text: str


def _without_tags(text: str) -> str:
    return _WHITESPACE.sub(" ", _TAG.sub(" ", text)).strip()


def _command_text(text: str) -> str:
    """A slash command as the user typed it: `<command-name>` (it holds the slash) and, when given, `<command-args>` as they
    are; `<command-message>` only repeats the name without it. A command with no name tag falls back to the tag-stripped text."""
    name = _COMMAND_NAME.search(text)
    if name is None:
        return _without_tags(text)
    args = _COMMAND_ARGS.search(text)
    typed = [name.group(1).strip(), args.group(1).strip() if args else ""]
    return _COMMAND_PART_SEPARATOR.join(part for part in typed if part)


def _user_opening(record: Record) -> Optional[_Opening]:
    """`promptSource` is one of the prompt sources (a real prompt), absent or null (a plain entry), or anything else
    (not a prompt). A plain entry is a slash command by its tags; else a remote prompt unless it only reports local
    output, a shell command, an interrupt or a message from another Claude session. The Python Agent SDK writes no
    `promptSource` at all, so its plain text of an external user is a real prompt."""
    source = record.get("promptSource")
    texts = _text_blocks(record)
    text = _PROMPT_TEXT_SEPARATOR.join(texts)
    if isinstance(source, str) and source in _PROMPT_SOURCES:
        return _Opening(_Kind.REAL, text)
    if source is not None:
        return None
    if text.startswith(_COMMAND_HEADS):
        return _Opening(_Kind.COMMAND, _command_text(text))
    if not texts or text.startswith(_NOT_A_PROMPT_HEADS):
        return None
    typed_by_sdk = record.get("entrypoint") in _TYPED_ENTRYPOINTS and record.get("userType") == _EXTERNAL_USER
    return _Opening(_Kind.REAL if typed_by_sdk else _Kind.REMOTE, text)


def _queued_opening(record: Record) -> Optional[_Opening]:
    attachment = record.get("attachment")
    if not isinstance(attachment, dict) or attachment.get("type") != _QUEUED_COMMAND:
        return None
    if attachment.get("commandMode") != _QUEUED_PROMPT_MODE or attachment.get("isMeta") is True:
        return None
    return _Opening(_Kind.REAL, _PROMPT_TEXT_SEPARATOR.join(_texts_of(attachment.get("prompt"))))


def _opening_of(record: Record) -> Optional[_Opening]:
    """The prompt a record opens, or None. A `<task-notification>` that Claude Code injects is never a prompt."""
    record_type = record.get("type")
    if record_type == _ATTACHMENT:
        opening = _queued_opening(record)
    elif record_type == _USER and record.get("isMeta") is not True:
        opening = _user_opening(record)
    else:
        return None
    return None if opening is None or opening.text.startswith(_INJECTED_HEAD) else opening


def _is_replayed(record: Record) -> bool:
    """`/branch` and `--fork-session` copy the parent's history into the new session's file, each record under the new
    session id but marked with a `forkedFrom` object that names the parent; those records belong to the parent's log."""
    return isinstance(record.get(_FORKED_FROM), dict)


def _is_synthetic(record: Record) -> bool:
    return _message_of(record).get("model") == _SYNTHETIC_MODEL


def _is_api_error(record: Record) -> bool:
    """A synthetic message Claude Code wrote for a failed API call (rate limit, login expired): what the user saw as the answer."""
    return _is_synthetic(record) and record.get(_API_ERROR_FLAG) is True


def _cache_writes(usage: Record) -> Tuple[int, int]:
    """The 5m and 1h cache-write tokens; without the split object the legacy total counts as 5m."""
    split = usage.get("cache_creation")
    if isinstance(split, dict):
        return _token_count(split.get("ephemeral_5m_input_tokens")), _token_count(split.get("ephemeral_1h_input_tokens"))
    return _token_count(usage.get("cache_creation_input_tokens")), 0


def _request_of(record: Record) -> Optional[RequestUsage]:
    message = _message_of(record)
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    written_5m, written_1h = _cache_writes(usage)
    model, effort = message.get("model"), record.get("effort")
    return RequestUsage(
        model_name=model if isinstance(model, str) else "",
        input_tokens=_token_count(usage.get("input_tokens")),
        output_tokens=_token_count(usage.get("output_tokens")),
        cache_read_tokens=_token_count(usage.get("cache_read_input_tokens")),
        cache_write_5m_tokens=written_5m,
        cache_write_1h_tokens=written_1h,
        effort=effort if isinstance(effort, str) else None,
        fast=usage.get("speed") == _FAST_SPEED,
    )


def _message_key(record: Record, line_number: int) -> _MessageKey:
    for candidate in (_message_of(record).get("id"), record.get("requestId")):
        if isinstance(candidate, str):
            return candidate
    return line_number


@dataclass(frozen=True)
class _Reply:
    """The light data one assistant record contributes to its turn."""

    message_key: _MessageKey
    request: Optional[RequestUsage]
    texts: Tuple[str, ...]
    moment: Optional[datetime]


def _earliest(current: Optional[datetime], moment: Optional[datetime]) -> Optional[datetime]:
    return min((m for m in (current, moment) if m is not None), default=None)


def _latest(current: Optional[datetime], moment: Optional[datetime]) -> Optional[datetime]:
    return max((m for m in (current, moment) if m is not None), default=None)


class _Turn:
    """A prompt and the assistant entries up to the next prompt. One request per message id, the last copy wins.
    A prompt without a valid timestamp still collects its entries but is dropped when finished."""

    def __init__(self, submitted_at: Optional[datetime], opening: _Opening, keep_replies: bool) -> None:
        self.kind = opening.kind
        self._submitted_at = submitted_at
        self._text = opening.text
        self._keep_replies = keep_replies
        self._requests: Dict[_MessageKey, RequestUsage] = {}
        self._texts: Dict[_MessageKey, List[str]] = {}
        self._latest_reply: Optional[datetime] = None
        self._last_key: Optional[_MessageKey] = None
        self._kept_replies: List[_Reply] = []
        self._api_error_reply: Optional[_Reply] = None

    @property
    def has_reply(self) -> bool:
        return self._last_key is not None or self._api_error_reply is not None

    @property
    def message_ids(self) -> List[str]:
        """The ids of the messages whose requests this turn counts."""
        return [key for key in self._requests if isinstance(key, str)]

    def add(self, reply: _Reply) -> None:
        if self._keep_replies:
            self._kept_replies.append(reply)
        if reply.request is not None:
            self._requests[reply.message_key] = reply.request
        collected = self._texts.setdefault(reply.message_key, [])
        for text in reply.texts:
            if text not in collected:
                collected.append(text)
        self._latest_reply = _latest(self._latest_reply, reply.moment)
        self._last_key = reply.message_key

    def is_prompt(self, remote_control: bool) -> bool:
        """A REMOTE turn counts only in a Remote Control session, a COMMAND turn only when a reply followed it."""
        if self.kind is _Kind.COMMAND:
            return self.has_reply
        return self.kind is _Kind.REAL or remote_control

    def hand_replies_to(self, holder: "_Turn") -> None:
        """A turn that proved not to be a prompt (it kept its replies for this) gives them to the turn before it."""
        for reply in self._kept_replies:
            holder.add(reply)

    def add_api_error(self, reply: _Reply) -> None:
        """An API error message counts no request and is the reply only when no real answer came."""
        self._api_error_reply = reply

    def finish(self) -> Optional[Prompt]:
        if self._submitted_at is None:
            return None
        reply_at = max(self._latest_reply, self._submitted_at) if self._latest_reply else None
        reply_text = _REPLY_PARAGRAPH_SEPARATOR.join(self._texts.get(self._last_key, ())) or None
        if self._last_key is None and self._api_error_reply is not None:
            error = self._api_error_reply
            reply_text = _REPLY_PARAGRAPH_SEPARATOR.join(error.texts) or None
            reply_at = max(error.moment, self._submitted_at) if error.moment else None
        return Prompt(
            submitted_at=self._submitted_at,
            text=self._text,
            reply_at=reply_at,
            reply_text=reply_text,
            working_time=reply_at - self._submitted_at if reply_at else timedelta(0),
            requests=tuple(self._requests.values()),
        )


class _RecordFolder(ABC):
    """Feeds the JSON object of every line to `_add_record`; a line that is blank or not a JSON object is skipped."""

    def __init__(self) -> None:
        self._saw_text = False
        self._saw_record = False

    def add_line(self, line_number: int, line: str) -> None:
        if not line.strip():
            return
        self._saw_text = True
        record = decode_record(line)
        if record is not None:
            self._saw_record = True
            self._add_record(line_number, record)

    @abstractmethod
    def _add_record(self, line_number: int, record: Record) -> None:
        ...

    @property
    def _unparsable(self) -> bool:
        return self._saw_text and not self._saw_record


def _fold(folder: _F, lines: Iterable[str]) -> _F:
    for line_number, line in enumerate(lines):
        folder.add_line(line_number, line)
    return folder


class _Folder(_RecordFolder):
    """Folds the lines of one session into turns: assistant entries go to the latest turn, a bridge-session record
    switches the session to Remote Control. Records replayed from a forked-from session (`_is_replayed`) are skipped."""

    def __init__(self) -> None:
        super().__init__()
        self._started_at: Optional[datetime] = None
        self._project_dir: Optional[str] = None
        self._remote_control = False
        self._turns: List[_Turn] = []

    def _add_record(self, line_number: int, record: Record) -> None:
        if _is_replayed(record):
            return
        moment = _moment(record)
        self._started_at = _earliest(self._started_at, moment)
        self._project_dir = self._project_dir or _cwd(record)
        self._remote_control = self._remote_control or record.get("type") == _BRIDGE_SESSION
        opening = _opening_of(record)
        if opening is not None:
            self._turns.append(_Turn(moment, opening, keep_replies=opening.kind is _Kind.REMOTE))
        elif self._turns and record.get("type") == _ASSISTANT and _is_api_error(record):
            self._turns[-1].add_api_error(_Reply(_message_key(record, line_number), None, _text_blocks(record), moment))
        elif self._turns and record.get("type") == _ASSISTANT and not _is_synthetic(record):
            self._turns[-1].add(_Reply(_message_key(record, line_number), _request_of(record), _text_blocks(record), moment))

    def _counted_turns(self) -> List[_Turn]:
        """A plain user entry outside a Remote Control session is no prompt and hands its replies to the turn before
        (this consumes the turns); a command is a prompt only when a reply followed it."""
        holders: List[_Turn] = []
        for turn in self._turns:
            if turn.kind is not _Kind.REMOTE or self._remote_control:
                holders.append(turn)
            elif holders:
                turn.hand_replies_to(holders[-1])
        return [turn for turn in holders if turn.is_prompt(self._remote_control)]

    def transcript(self) -> Transcript:
        """A turn without a valid time is dropped, and so are its requests."""
        finished = [(turn, turn.finish()) for turn in self._counted_turns()]
        kept = [(turn, prompt) for turn, prompt in finished if prompt is not None]
        message_ids = frozenset(message_id for turn, _ in kept for message_id in turn.message_ids)
        return Transcript(self._started_at, self._project_dir, tuple(prompt for _, prompt in kept), message_ids,
                          unparsable=self._unparsable)


def parse_main_session(lines: Iterable[str]) -> Transcript:
    """The lines of one main session folded into prompts; every line that is not a JSON object (torn, blank, too deeply
    nested) is skipped, and so is every assistant record before the first prompt or from `<synthetic>`."""
    return _fold(_Folder(), lines).transcript()


@dataclass(frozen=True)
class MessageRequest:
    """One request and the id of the message it came from (see `Transcript.message_ids`); None when the record carries no
    such id (a request recognised only by its line is never the same request as one in another file)."""

    message_id: Optional[str]
    usage: RequestUsage


@dataclass(frozen=True)
class SubAgentTranscript:
    """`started_at` is the earliest timestamp of any record, in UTC; `working_time` runs from it to the last assistant
    output (0 without any); `requests` are the assistant messages in order of first appearance, a streamed message once
    with its last copy. `unparsable`: the text has lines, none of them a JSON object."""

    started_at: Optional[datetime]
    working_time: timedelta
    requests: Tuple[MessageRequest, ...]
    unparsable: bool


class _SubAgentFolder(_RecordFolder):
    """A sub-agent transcript has no prompt markers, so every assistant entry that is not `<synthetic>` belongs to the run."""

    def __init__(self) -> None:
        super().__init__()
        self._started_at: Optional[datetime] = None
        self._last_output: Optional[datetime] = None
        self._requests: Dict[_MessageKey, RequestUsage] = {}

    def _add_record(self, line_number: int, record: Record) -> None:
        moment = _moment(record)
        self._started_at = _earliest(self._started_at, moment)
        if record.get("type") != _ASSISTANT or _is_synthetic(record):
            return
        self._last_output = _latest(self._last_output, moment)
        request = _request_of(record)
        if request is not None:
            self._requests[_message_key(record, line_number)] = request

    def transcript(self) -> SubAgentTranscript:
        started_at = self._started_at.astimezone(timezone.utc) if self._started_at else None
        working_time = self._last_output - started_at if self._last_output else timedelta(0)
        requests = tuple(MessageRequest(key if isinstance(key, str) else None, request) for key, request in self._requests.items())
        return SubAgentTranscript(started_at, working_time, requests, unparsable=self._unparsable)


def parse_sub_agent_session(lines: Iterable[str]) -> SubAgentTranscript:
    """The lines of one sub-agent transcript folded into one run; bad lines are skipped as in `parse_main_session`."""
    return _fold(_SubAgentFolder(), lines).transcript()
