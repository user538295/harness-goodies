"""Pure grouping of what a transcript holds into the prompts of the session, shared by the readers whose transcripts interleave user
messages and the answers to them (OMP, OpenCode); no file access.

A transcript is a sequence of events: an `Opening` (a user message that opens a prompt) and answers (an assistant message or a
helper request: an `Answer`, or any object with its six attributes). A turn is an opening and the answers up to the next opening;
answers before the first opening belong to the first turn. A turn's reply is its last answer that stops it. A turn works from its
opening to its last answer that has a time, whatever stopped it (an error, an abort, a steering prompt, a limit), except for the
spans in which an answer put a question to the user: from the time it asked to the start of the next answer, the turn waited for
the user and works no time. A turn whose opening has no usable time gives no prompt: its replies and the requests under it are
dropped from the prompts (the requests stay in the list of every request the grouping returns, which a sub-agent reports)."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, List, NamedTuple, Optional, Protocol, Tuple, Union

from .model import Prompt, RequestUsage


class Opening(NamedTuple):
    moment: Optional[datetime]
    text: str


class Answer(NamedTuple):
    """What an assistant message or a helper request contributes to its turn: the request it made, whether it stops the turn, its reply
    text, the time it ended, the time it started and, when it put a question to the user, the time it asked (the user's wait begins
    there and ends when the next answer starts)."""

    request: Optional[RequestUsage]
    is_stop: bool
    text: Optional[str]
    moment: Optional[datetime]
    began: Optional[datetime] = None
    asked_at: Optional[datetime] = None


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

    @property
    def began(self) -> Optional[datetime]: ...

    @property
    def asked_at(self) -> Optional[datetime]: ...


@dataclass
class _Turn:
    opening: Opening
    answers: List[AnswerLike]

    def _working_time(self, opening_moment: datetime) -> timedelta:
        """From the opening to the last answer end, less the spans the user was asked a question in: a span runs from the time the
        answer asked to the start of the next answer, and a turn that ends asking works until it asked."""
        worked = timedelta(0)
        anchor: Optional[datetime] = opening_moment
        latest_end: Optional[datetime] = None
        for answer in self.answers:
            anchor = anchor or answer.began
            if answer.asked_at is not None:
                if anchor is not None and answer.asked_at >= anchor:
                    worked += answer.asked_at - anchor
                anchor = latest_end = None
            elif answer.moment is not None and (latest_end is None or answer.moment > latest_end):
                latest_end = answer.moment
        if anchor is not None and latest_end is not None and latest_end >= anchor:
            worked += latest_end - anchor
        return worked

    def to_prompt(self) -> Optional[Prompt]:
        """The prompt of the turn; None when its opening has no usable time. The reply is its last answer that stops the turn; its
        `reply_at` is the end of the turn's last answer."""
        if self.opening.moment is None:
            return None
        stops = [answer for answer in self.answers if answer.is_stop]
        reply = stops[-1] if stops else None
        ends = [moment for answer in self.answers for moment in (answer.moment, answer.asked_at) if moment is not None]
        reply_at = max(ends + [self.opening.moment]) if ends else None
        return Prompt(
            submitted_at=self.opening.moment,
            text=self.opening.text,
            reply_at=reply_at,
            reply_text=reply.text if reply is not None else None,
            working_time=self._working_time(self.opening.moment) if reply_at is not None else timedelta(0),
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
