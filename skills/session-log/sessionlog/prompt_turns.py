"""Pure grouping of what a transcript holds into the prompts of the session, shared by the readers whose transcripts interleave user
messages and the answers to them (OMP, OpenCode); no file access.

A transcript is a sequence of events: an `Opening` (a user message that opens a prompt) and answers (an assistant message or a
helper request: an `Answer`, or any object with its six attributes). A turn is an opening and the answers up to the next opening;
answers before the first opening belong to the first turn. A turn's reply is its last answer that stops it. A turn works from its
opening to its last answer that has a time, whatever stopped it (an error, an abort, a steering prompt, a limit), except for the
spans in which an answer put a question to the user: from the time it asked to the start of the next answer, the turn waited for
the user and works no time. A turn whose opening has no usable time gives no prompt: its replies are dropped from the prompts and its
requests, with those of a transcript that has no opening at all, fall under no prompt: `group_turns` returns them apart (and, like
every request, in the list a sub-agent reports) and `with_unprompted` shows them as the leading row `NO_PROMPT_TEXT`, so their tokens
and cost still reach the TOTAL (the convention of Cursor's lone `stop`)."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, List, NamedTuple, Optional, Protocol, Tuple, Union

from .model import Prompt, RequestUsage

NO_PROMPT_TEXT = "(prompt not recorded)"


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


class Grouping(NamedTuple):
    """The prompts of a transcript, every request it made and the requests that fall under no prompt (see the module text)."""

    prompts: Tuple[Prompt, ...]
    requests: Tuple[RequestUsage, ...]
    unprompted: Tuple[RequestUsage, ...]


def group_turns(events: Iterable[Union[Opening, AnswerLike]]) -> Grouping:
    """The prompts of the events in order, every request in order (also the ones under a dropped opening, and those before the first
    opening) and, in order, the requests under no prompt: those of a dropped opening and, with no opening at all, every one."""
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
    prompts: List[Prompt] = []
    orphans: List[AnswerLike] = [] if turns else leading
    for turn in turns:
        prompt = turn.to_prompt()
        if prompt is None:
            orphans += turn.answers
        else:
            prompts.append(prompt)
    unprompted = tuple(answer.request for answer in orphans if answer.request is not None)
    return Grouping(tuple(prompts), tuple(requests), unprompted)


def with_unprompted(prompts: Tuple[Prompt, ...], unprompted: Tuple[RequestUsage, ...], started_at: datetime) -> Tuple[Prompt, ...]:
    """`prompts` led by the row of the requests that fall under no prompt, dated by the session start and without a reply or working
    time; the prompts themselves when there are no such requests."""
    if not unprompted:
        return prompts
    return (Prompt(submitted_at=started_at, text=NO_PROMPT_TEXT, working_time=timedelta(0), requests=unprompted),) + prompts
