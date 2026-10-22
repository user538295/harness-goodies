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
MODEL_KEYS = frozenset(("prefixes", "fast", "source", "as_of") + RATE_KEYS)
REQUIRED_MODEL_KEYS = MODEL_KEYS - {"fast"}
MODELS_KEY = "models"
CLAUDE_MARKER = "claude-"
SOURCE_SCHEMES = ("https", "http")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


class Tariff(NamedTuple):
    """One model entry's normal rates and its optional fast-mode rates."""

    normal: Rates
    fast: Optional[Rates]


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
    return model["prefixes"], Tariff(_parse_rates(model), _parse_fast(model))


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


class PriceFile:
    """Load once; longest prefix wins after stripping a Claude provider prefix."""

    def __init__(self, path: Path):
        self._tariffs = ()  # type: Tuple[Tuple[str, Tariff], ...]
        self._problem = None  # type: Optional[str]
        try:
            self._tariffs = _load(path)
        except (OSError, ValueError, RecursionError) as error:
            self._problem = "{}: {}".format(path, error)

    def rate_for(self, model_name: str, fast: bool) -> Optional[Rates]:
        """Rates of the longest matching prefix; None when nothing matches or fast has no rates."""
        start = model_name.find(CLAUDE_MARKER)
        if start >= 0:
            model_name = model_name[start:]
        for prefix, tariff in self._tariffs:
            if model_name.startswith(prefix):
                return tariff.fast if fast else tariff.normal
        return None

    def problem(self) -> Optional[str]:
        """Why the file supplied no rates (path and first fault), or None when it loaded."""
        return self._problem
