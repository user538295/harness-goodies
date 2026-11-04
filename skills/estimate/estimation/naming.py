"""The C2 file name of a feature: `<first day>[-<id>]-<name>.json` in lowercase ASCII (accents dropped, every other run of
characters one `-`, the id and the name part each cut to MAX_SLUG_LENGTH), so checkouts on macOS, Linux and Windows name it alike
and within their file-name limits. The first day is read back from a stored name."""
import re
import unicodedata
from datetime import date

from estimation.errors import EstimateError

FILE_SUFFIX = ".json"
SEPARATOR = "-"
FALLBACK_NAME = "feature"  # the name part of a name without any ASCII letter or digit
DAY_LENGTH = len("YYYY-MM-DD")
NOT_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")
MAX_SLUG_LENGTH = 60


def slug(text):
    """`text` as a file-name part: accents dropped, lowercase, every run of other characters one `-`, cut to MAX_SLUG_LENGTH;
    empty when it has no ASCII letter or digit. Two ids of the same slug name one file."""
    unaccented = "".join(char for char in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(char))
    return NOT_ALPHANUMERIC.sub(SEPARATOR, unaccented).strip(SEPARATOR)[:MAX_SLUG_LENGTH].rstrip(SEPARATOR)


def file_name(day, feature_id, name):
    """The file name of a feature first seen on `day` (a date), with its id (or None) and its name."""
    parts = [day.isoformat(), "" if feature_id is None else slug(feature_id), slug(name) or FALLBACK_NAME]
    return SEPARATOR.join(part for part in parts if part) + FILE_SUFFIX


def day_of_file(name):
    """The first day a stored file name starts with."""
    try:
        return date.fromisoformat(name[:DAY_LENGTH])
    except ValueError as error:
        raise EstimateError("%s does not start with its first day (YYYY-MM-DD)" % name) from error
