"""Read-only C4 price-file adapter; invalid files never supply partial tariffs.

Vendor cache-write tiers map onto the contract's two fields: OpenAI's 30m/12h write prices are stored
as cache_write_5m/cache_write_1h.
"""
import json
import math
import re
from datetime import date
from pathlib import Path
from typing import List, NamedTuple, Optional, Tuple
from urllib.parse import urlsplit

from .pricing import Rates

RATE_KEYS = ("in", "out", "cache_read", "cache_write_5m", "cache_write_1h")
THRESHOLD_KEY = "over_prompt_tokens"
MODEL_KEYS = frozenset(("prefixes", "fast", "long_prompt", "source", "as_of") + RATE_KEYS)
REQUIRED_MODEL_KEYS = MODEL_KEYS - {"fast", "long_prompt"}
MODELS_KEY = "models"
CLAUDE_MARKER = "claude-"
SOURCE_SCHEMES = ("https", "http")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
PROVIDER_SEPARATOR = "/"
SUFFIX_OPENERS = "[:@"
EFFORT_WORDS = frozenset(("minimal", "low", "medium", "high", "xhigh", "max"))
SNAPSHOT_DATE = re.compile(r"-(\d{8}|\d{4}-\d{2}-\d{2})(?![0-9])")


class LongPrompt(NamedTuple):
    """The rates of a request whose whole prompt (input, cache reads and cache writes) is longer than `over_tokens`."""

    over_tokens: int
    rates: Rates


class Tariff(NamedTuple):
    """One model entry's normal rates, its optional fast-mode rates and its optional long-prompt rates."""

    normal: Rates
    fast: Optional[Rates]
    long_prompt: Optional[LongPrompt]


def _reject_duplicate_keys(pairs: List[Tuple[str, object]]) -> dict:
    keys = [key for key, _ in pairs]
    repeated = sorted({key for key in keys if keys.count(key) > 1})
    if repeated:
        entry = dict(pairs).get("prefixes")
        raise ValueError("duplicate key {}{}".format(
            ", ".join(repeated), " in the entry with prefixes {}".format(entry) if entry else ""))
    return dict(pairs)


def _is_rate(value) -> bool:
    try:
        return type(value) in (int, float) and value >= 0 and math.isfinite(value)
    except OverflowError:
        return False


def _parse_rates(values: dict, label: str = "") -> Rates:
    invalid_keys = [key for key in RATE_KEYS if not _is_rate(values[key])]
    if invalid_keys:
        raise ValueError("{}{} must be finite nonnegative numbers".format(label, ", ".join(invalid_keys)))
    return Rates(*(values[key] for key in RATE_KEYS))


def _is_nonempty_string_list(value) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) and item for item in value)


def _check_prefixes(prefixes) -> None:
    if not _is_nonempty_string_list(prefixes):
        raise ValueError("prefixes must be a nonempty list of nonempty strings")
    unmatchable = [prefix for prefix in prefixes if prefix.find(CLAUDE_MARKER) > 0]
    if unmatchable:
        raise ValueError("prefix {} can never match: names are matched from their first {}".format(
            ", ".join(unmatchable), CLAUDE_MARKER))


def _check_source(source) -> None:
    try:
        parts = urlsplit(source) if isinstance(source, str) else None
    except ValueError:
        parts = None
    if parts is None or parts.scheme not in SOURCE_SCHEMES or not parts.netloc:
        raise ValueError("source must be an http(s) URL")


def _check_as_of(as_of) -> None:
    try:
        if not isinstance(as_of, str) or not ISO_DATE.match(as_of):
            raise ValueError
        date.fromisoformat(as_of)
    except ValueError:
        raise ValueError("as_of must be a calendar date YYYY-MM-DD") from None


def _parse_fast(model: dict) -> Optional[Rates]:
    if "fast" not in model:
        return None
    fast = model["fast"]
    if not isinstance(fast, dict):
        raise ValueError("fast must be an object")
    if set(fast) != set(RATE_KEYS):
        raise ValueError("fast needs exactly {}".format(", ".join(RATE_KEYS)))
    return _parse_rates(fast, "fast: ")


def _parse_long_prompt(model: dict) -> Optional[LongPrompt]:
    if "long_prompt" not in model:
        return None
    long_prompt = model["long_prompt"]
    if not isinstance(long_prompt, dict):
        raise ValueError("long_prompt must be an object")
    if set(long_prompt) != {THRESHOLD_KEY, *RATE_KEYS}:
        raise ValueError("long_prompt needs exactly {}".format(", ".join((THRESHOLD_KEY,) + RATE_KEYS)))
    threshold = long_prompt[THRESHOLD_KEY]
    if type(threshold) is not int or threshold < 1:
        raise ValueError("long_prompt: {} must be a positive whole number".format(THRESHOLD_KEY))
    return LongPrompt(threshold, _parse_rates(long_prompt, "long_prompt: "))


def _parse_model(model) -> Tuple[List[str], Tariff]:
    if not isinstance(model, dict):
        raise ValueError("expected an object")
    missing = sorted(REQUIRED_MODEL_KEYS - model.keys())
    unknown = sorted(model.keys() - MODEL_KEYS)
    if missing or unknown:
        raise ValueError("; ".join(
            part for part in ("missing " + ", ".join(missing) if missing else "",
                              "unknown " + ", ".join(unknown) if unknown else "") if part))
    _check_prefixes(model["prefixes"])
    _check_source(model["source"])
    _check_as_of(model["as_of"])
    return model["prefixes"], Tariff(_parse_rates(model), _parse_fast(model), _parse_long_prompt(model))


def _parse_models(document) -> List[Tuple[int, List[str], Tariff]]:
    if not isinstance(document, dict) or not isinstance(document.get(MODELS_KEY), list) \
            or not document[MODELS_KEY]:
        raise ValueError("expected a nonempty {} array".format(MODELS_KEY))
    parsed = []
    for index, model in enumerate(document[MODELS_KEY]):
        try:
            parsed.append((index,) + _parse_model(model))
        except ValueError as error:
            raise ValueError("{}[{}]: {}".format(MODELS_KEY, index, error)) from error
    return parsed


def _load(path: Path) -> Tuple[Tuple[str, Tariff], ...]:
    document = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_reject_duplicate_keys)
    tariffs = {}
    for index, prefixes, tariff in _parse_models(document):
        for prefix in prefixes:
            if prefix in tariffs:
                raise ValueError("{}[{}]: duplicate prefix {}".format(MODELS_KEY, index, prefix))
            tariffs[prefix] = tariff
    return tuple(sorted(tariffs.items(), key=lambda item: len(item[0]), reverse=True))


def _ends_at_model_boundary(rest: str, is_claude: bool) -> bool:
    """Whether what follows a matched prefix is a variant of that model, not a different model.

    Variants: a snapshot date, `[1m]` / `:medium` / `@date` markers, a reasoning-effort word
    (`-high`) and, for Claude ids, any other platform word (`-v1:0`). A further version number
    (`-6`, `.5`) or sibling word (`-mini`) on a non-Claude id names another model.
    """
    if not rest:
        return True
    if rest[0] in SUFFIX_OPENERS:
        return True
    if rest[0] != "-":
        return False
    if rest[1:2].isdigit():
        return SNAPSHOT_DATE.match(rest) is not None
    word = re.split(r"[-.:\[@]", rest[1:], maxsplit=1)[0]
    return is_claude or word in EFFORT_WORDS


class PriceFile:
    """Load once; longest prefix wins at a model-name boundary after stripping a provider prefix."""

    def __init__(self, path: Path):
        self._tariffs = ()  # type: Tuple[Tuple[str, Tariff], ...]
        self._problem = None  # type: Optional[str]
        try:
            self._tariffs = _load(path)
        except (OSError, ValueError, RecursionError) as error:
            self._problem = "{}: {}".format(path, error)

    def rate_for(self, model_name: str, fast: bool, prompt_tokens: int = 0) -> Optional[Rates]:
        """Rates of the longest prefix ending at a model-name boundary; None when nothing matches or fast has no rates. A fast request
        keeps its fast rates at any length; any other request whose prompt is longer than the long-prompt threshold gets those rates."""
        start = model_name.find(CLAUDE_MARKER)
        is_claude = start >= 0
        if is_claude:
            model_name = model_name[start:]
        else:
            model_name = model_name.rpartition(PROVIDER_SEPARATOR)[2]
        for prefix, tariff in self._tariffs:
            if model_name.startswith(prefix) and _ends_at_model_boundary(model_name[len(prefix):], is_claude):
                if fast:
                    return tariff.fast
                long_prompt = tariff.long_prompt
                return long_prompt.rates if long_prompt and prompt_tokens > long_prompt.over_tokens else tariff.normal
        return None

    def problem(self) -> Optional[str]:
        """Why the file supplied no rates (path and first fault), or None when it loaded."""
        return self._problem
