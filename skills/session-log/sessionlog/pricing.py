"""Use-case-owned report data and pricing port (C3/C4); no file or rendering dependencies."""
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Protocol, Tuple

from .model import Harness, RequestUsage, Session

TOKENS_PER_MILLION = Decimal(1000000)
CENTS_PER_DOLLAR = Decimal(100)
PROMPT_HEAD_LENGTH = 60
PRICED = "priced"
PARTIAL = "partial"
UNKNOWN = "unknown"


def _whole_seconds(span: timedelta) -> timedelta:
    return timedelta(seconds=max(0, int(span.total_seconds())))


def _prompt_head(text: str) -> str:
    return re.sub(r"[\r\n\t]+", " ", text)[:PROMPT_HEAD_LENGTH]


@dataclass(frozen=True)
class Rates:
    """USD per million tokens; decimal conversion avoids binary-float rounding."""

    input: float
    output: float
    cache_read: float
    cache_write_5m: float
    cache_write_1h: float


class PriceBook(Protocol):
    """Model-rate lookup supplied by an adapter; recorded costs bypass this port."""

    def rate_for(self, model_name: str, fast: bool) -> Optional[Rates]:
        """Return the requested tariff, or None for an unknown model/missing fast rates."""
        ...

    def problem(self) -> Optional[str]:
        """Return a price-source diagnostic, or None; invalid sources yield no rates."""
        ...


@dataclass(frozen=True)
class Price:
    """Half-up whole cents of the known subtotal, with priced/partial/unknown status."""

    cents: int
    status: str


@dataclass(frozen=True)
class Usage:
    """Disjoint token classes; total is absent when the source records no tokens."""

    input_tokens: int
    output_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    total_tokens: Optional[int]
    price: Price
    model_names: str
    effort: Optional[str]


@dataclass(frozen=True)
class PromptRow(Usage):
    """Main prompt timestamp, reply duration (zero without reply), and 60-code-point head."""

    at: datetime
    working_time: timedelta
    prompt_head: str


@dataclass(frozen=True)
class SubAgentRow(Usage):
    """Depth-first agent row; depth zero is top-level and missing tokens stay absent."""

    agent_id: str
    type: str
    depth: int
    working_time: Optional[timedelta]


@dataclass(frozen=True)
class Total:
    """Sum of displayed row cents and tokens; main durations only, in the whole seconds the rows show; explicit incomplete/API flags."""

    total_tokens: int
    price_cents: int
    price_incomplete: bool
    api_equivalent: bool
    working_time: timedelta
    request_count: int
    sub_agent_count: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    model_names: str
    effort: Optional[str]
    label: Optional[str] = None


@dataclass(frozen=True)
class UsageReport:
    """Prompt order from the session, then depth-first agents ordered by start and id."""

    harness: Harness
    session_id: str
    project_dir: Optional[str]
    prompts: Tuple[PromptRow, ...]
    sub_agents: Tuple[SubAgentRow, ...]
    total: Total


class PricingRules:
    """Build immutable report data using only a normalized session and injected rate lookup."""

    def __init__(self, price_book: PriceBook):
        self._price_book = price_book

    def problem(self) -> Optional[str]:
        """Why the price book cannot be used, or None; `status` reports it."""
        return self._price_book.problem()

    def _cost(self, request: RequestUsage) -> Optional[Decimal]:
        if request.recorded_cost_usd is not None:
            return Decimal(str(request.recorded_cost_usd))
        rates = self._price_book.rate_for(request.model_name, bool(request.fast))
        if rates is None:
            return None
        return sum((Decimal(str(rate)) * tokens for rate, tokens in (
            (rates.input, request.input_tokens), (rates.output, request.output_tokens),
            (rates.cache_read, request.cache_read_tokens),
            (rates.cache_write_5m, request.cache_write_5m_tokens),
            (rates.cache_write_1h, request.cache_write_1h_tokens))), Decimal(0)) / TOKENS_PER_MILLION

    def _usage(self, requests: Tuple[RequestUsage, ...], missing: bool = False) -> dict:
        amount = Decimal(0)
        known = 0
        for request in requests:
            cost = self._cost(request)
            if cost is not None:
                known += 1
                amount += cost
        if known == len(requests) and not missing:
            status = PRICED
        elif known:
            status = PARTIAL
        else:
            status = UNKNOWN
        input_tokens = sum(r.input_tokens for r in requests)
        output_tokens = sum(r.output_tokens for r in requests)
        cache_read = sum(r.cache_read_tokens for r in requests)
        cache_write = sum(r.cache_write_5m_tokens + r.cache_write_1h_tokens for r in requests)
        return dict(input_tokens=input_tokens, output_tokens=output_tokens,
                    cache_read_tokens=cache_read, cache_write_tokens=cache_write,
                    total_tokens=None if missing else input_tokens + output_tokens + cache_read + cache_write,
                    price=Price(int((amount * CENTS_PER_DOLLAR).quantize(Decimal(1), rounding=ROUND_HALF_UP)), status),
                    model_names="+".join(sorted({r.model_name + (":fast" if r.fast else "") for r in requests if r.model_name})) or "-",
                    effort="+".join(sorted({r.effort for r in requests if r.effort})) or None)

    def build(self, session: Session) -> UsageReport:
        """Prefer recorded costs; round each row once, then sum rows without repricing."""
        prompts = tuple(PromptRow(
            **self._usage(prompt.requests), at=prompt.submitted_at,
            working_time=_whole_seconds(prompt.working_time or timedelta(0)) if prompt.reply_at is not None else timedelta(0),
            prompt_head=_prompt_head(prompt.text)) for prompt in session.prompts)
        agents = []

        def visit(children, depth):
            for agent in children:
                agents.append(SubAgentRow(**self._usage(agent.requests, agent.tokens_not_recorded),
                                          agent_id=agent.agent_id, type=agent.type, depth=depth,
                                          working_time=agent.working_time))
                visit(agent.sub_agents, depth + 1)

        visit(session.sub_agents, 0)
        rows = (*prompts, *agents)
        cursor = session.harness == Harness.CURSOR
        total = Total(total_tokens=sum(row.total_tokens or 0 for row in rows),
                      price_cents=sum(row.price.cents for row in rows),
                      price_incomplete=cursor or any(row.price.status != PRICED for row in rows),
                      api_equivalent=session.harness in (Harness.CODEX, Harness.CURSOR),
                      working_time=sum((row.working_time for row in prompts), timedelta(0)),
                      request_count=len(prompts), sub_agent_count=len(agents),
                      input_tokens=sum(row.input_tokens for row in rows),
                      output_tokens=sum(row.output_tokens for row in rows),
                      cache_read_tokens=sum(row.cache_read_tokens for row in rows),
                      cache_write_tokens=sum(row.cache_write_tokens for row in rows),
                      model_names="+".join(sorted({name for row in rows for name in row.model_names.split("+") if name != "-"})) or "-",
                      effort="+".join(sorted({effort for row in rows if row.effort for effort in row.effort.split("+")})) or None,
                      label="tokens: main session only — Cursor does not record sub-agent tokens" if cursor else None)
        return UsageReport(session.harness, session.session_id, session.project_dir, prompts, tuple(agents), total)
