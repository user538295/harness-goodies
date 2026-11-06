"""The rules of a feature's texts: `description` and `effort` are at most TEXT_LIMIT characters each and never blank, and the effort
holds no digits. `check-text` lists their problems and `save` refuses an entry that has any."""
import re

from estimation.errors import ArgumentError

TEXT_LIMIT = 300
TEXT_KEYS = ("description", "effort")
DIGIT = re.compile(r"[0-9]")


def check_texts(texts):
    """Refuses a `check-text` input that is not an object of `description` and/or `effort` strings."""
    if not isinstance(texts, dict):
        raise ArgumentError('texts must be {"description"?, "effort"?}')
    if not texts:
        raise ArgumentError("texts need description or effort")
    for key, value in texts.items():
        if key not in TEXT_KEYS:
            raise ArgumentError("unknown field '%s' (expected %s)" % (key, ", ".join(TEXT_KEYS)))
        if not isinstance(value, str):
            raise ArgumentError("%s must be a string" % key)


def _digit_words(effort):
    """Each whitespace-separated word of `effort` holding a digit, in order and once."""
    return list(dict.fromkeys(word for word in effort.split() if DIGIT.search(word)))


def problems(texts):
    """The problems of the `description` and `effort` strings that `texts` holds (both optional): each over the limit, the
    effort's words with digits, then each blank text."""
    found = ["%s has %d characters (at most %d)" % (key, len(texts[key]), TEXT_LIMIT)
             for key in TEXT_KEYS if key in texts and len(texts[key]) > TEXT_LIMIT]
    digit_words = _digit_words(texts.get("effort", ""))
    if digit_words:
        found.append("effort has digits: %s" % ", ".join(digit_words))
    return found + ["%s is empty" % key for key in TEXT_KEYS if key in texts and not texts[key].strip()]
