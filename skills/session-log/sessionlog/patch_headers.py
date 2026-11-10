"""The file headers of an `apply_patch` patch, which the Codex and OpenCode readers share: the files a patch names are the ones of its
`*** Add File:`, `*** Update File:` and `*** Move to:` headers, in order and each as often as the patch names it. A `*** Delete File:`
header names none, and neither does a body line that only looks like a header (a body line starts with `+`, `-` or a space, and a
header must start its line)."""
import re
from typing import Any, Tuple

PATCH_OPENING = "*** Begin Patch"
_PATH_HEADER = re.compile(r"^\*\*\* (?:Add File|Update File|Move to): (.+?)\s*$", re.MULTILINE)


def opens_a_patch(text: Any) -> bool:
    """Whether `text` is a patch: some text that opens with `*** Begin Patch` (after any blank space)."""
    return isinstance(text, str) and text.lstrip().startswith(PATCH_OPENING)


def patch_paths(patch: Any) -> Tuple[str, ...]:
    """The files the headers of a patch name, in order; none for anything but text."""
    return tuple(_PATH_HEADER.findall(patch)) if isinstance(patch, str) else ()
