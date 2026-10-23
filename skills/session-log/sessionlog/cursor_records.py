"""C5 file format of a Cursor hook record, shared by the hook that writes it and the reader that scans it."""
import re
from typing import Dict, Optional, Union

from .temp_files import STALE_TEMP_TAIL

RECORDS_FOLDER = ".records"
INVALID_FOLDER = ".invalid"
RECORD_SUFFIX = ".json"
RECORD_EVENTS = ("beforeSubmitPrompt", "afterAgentResponse", "stop", "subagentStop")
_RECORD_NAME = "%d-%s-%d" + RECORD_SUFFIX
# The temp of a record file (see `temp_files.temp_name`): a dot, the record's name, then the tail.
STALE_RECORD_TEMP = re.compile(r"\.[0-9]+-(?:%s)-[0-9]+%s" % ("|".join(RECORD_EVENTS), re.escape(RECORD_SUFFIX)) + STALE_TEMP_TAIL)


def record_file_name(received_at_ns: int, event: str, pid: int) -> str:
    """`<arrival_ns>-<event>-<pid>.json`."""
    return _RECORD_NAME % (received_at_ns, event, pid)


def record_fields(event: str, conversation_id: Optional[str], received_at_ns: int, pid: int, raw_input: str) -> Dict[str, Union[str, int]]:
    """The JSON object of one record file. A record filed under `.records/.invalid/` has no `conversationId`."""
    fields: Dict[str, Union[str, int]] = {"event": event, "receivedAtNs": received_at_ns, "pid": pid, "rawInput": raw_input}
    if conversation_id is not None:
        fields["conversationId"] = conversation_id
    return fields
