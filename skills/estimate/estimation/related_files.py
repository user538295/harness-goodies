"""The code files a feature's documents link: its related files.

A related file is the target of a Markdown link in a document, kept when it is a tracked or untracked-not-ignored file of the
project and neither a Markdown file (a spec or another plan is not the feature's work) nor a document of any feature. Commits that
touch one are candidates for the feature's commits; a related file is never evidence for a prompt (see `evidence.py`)."""
import os
import re
from urllib.parse import unquote

LINK_TARGET = re.compile(r"\]\(\s*<?([^)\s>]+)")
NOT_A_FILE_LINK = re.compile(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*:|#|/)")  # a scheme (https:, mailto:), an anchor, an absolute path
MARKDOWN_SUFFIX = ".md"


def linked_files(text, folder, project, tracked, documents):
    """The stored paths of the related files `text` links, in order, each once. `folder` is the absolute folder of the document the
    links are relative to, `tracked` the set of the project's files (stored form) and `documents` the stored paths of every
    feature's documents."""
    found = []
    for target in LINK_TARGET.findall(text):
        target = unquote(target.split("#", 1)[0].split("?", 1)[0])
        if not target or NOT_A_FILE_LINK.match(target) or target.lower().endswith(MARKDOWN_SUFFIX):
            continue
        stored = project.stored(os.path.join(folder, target))
        if stored in tracked and stored not in documents and stored not in found:
            found.append(stored)
    return found
