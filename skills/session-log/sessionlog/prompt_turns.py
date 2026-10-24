"""Pure grouping of what a transcript holds into the prompts of the session, shared by the readers whose transcripts interleave user
messages and the answers to them (OMP, OpenCode); no file access.

A transcript is a sequence of events: an `Opening` (a user message that opens a prompt) and answers (an assistant message or a
helper request: an `Answer`, or any object with its four attributes). A turn is an opening and the answers up to the next opening;
answers before the first opening belong to the first turn. A turn's reply is its last answer that stops it, and a turn whose opening has
no usable time gives no prompt: its replies and the requests under it are dropped from the prompts (the requests stay in the list of
every request the grouping returns, which a sub-agent reports)."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, List, NamedTuple, Optional, Protocol, Tuple, Union

from .model import Prompt, RequestUsage


class Opening(NamedTuple):
    moment: Optional[datetime]
    text: str


class Answer(NamedTuple):
    """What an assistant message or a helper request contributes to its turn: the request it made, whether it stops the turn, its reply
    text and its time."""

    request: Optional[RequestUsage]
    is_stop: bool
    text: Optional[str]
    moment: Optional[datetime]


class AnswerLike(Protocol):
    """What `group_turns` reads of an answer, for readers whose answers carry more than an `Answer` does."""

    @property
    def request(self) -> Optional[RequestUsage]: ...

    @property
    def is_stop(self) -> bool: ...

    @property
    def text(self) -> Optional[str]: ...

    @property
    def moment(self) -> Optional[datetime]: ...


@dataclass
class _Turn:
    opening: Opening
    answers: List[AnswerLike]

    def to_prompt(self) -> Optional[Prompt]:
        """The prompt of the turn; None when its opening has no usable time. The reply is its last answer that stops the turn."""
        if self.opening.moment is None:
            return None
        stops = [answer for answer in self.answers if answer.is_stop]
        reply = stops[-1] if stops else None
        reply_at = max(reply.moment, self.opening.moment) if reply is not None and reply.moment is not None else None
        return Prompt(
            submitted_at=self.opening.moment,
            text=self.opening.text,
            reply_at=reply_at,
            reply_text=reply.text if reply is not None else None,
            working_time=reply_at - self.opening.moment if reply_at is not None else timedelta(0),
            requests=tuple(answer.request for answer in self.answers if answer.request is not None),
        )


def group_turns(events: Iterable[Union[Opening, AnswerLike]]) -> Tuple[Tuple[Prompt, ...], Tuple[RequestUsage, ...]]:
    """The prompts of the events in order, and every request in order (also the ones under a dropped opening, and those before the first
    opening)."""
    turns: List[_Turn] = []
    leading: List[AnswerLike] = []
    requests: List[RequestUsage] = []
    for event in events:
        if isinstance(event, Opening):
            turns.append(_Turn(event, []))
            continue
        if event.request is not None:
            requests.append(event.request)
        current = turns[-1].answers if turns else leading
        current.append(event)
    if turns:
        turns[0].answers = leading + turns[0].answers
    prompts = (prompt for prompt in (turn.to_prompt() for turn in turns) if prompt is not None)
    return tuple(prompts), tuple(requests)
