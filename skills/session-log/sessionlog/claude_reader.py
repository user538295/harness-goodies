"""Claude Code adapter of the C2 reader port: finds main-session files under `<root>/projects/<project>/<sid>.jsonl` and
sub-agent files under `<root>/projects/<project>/<sid>/subagents/**/agent-*.jsonl`, and maps file-system problems to
unreadable sources; `claude_transcript` parses what is inside and `claude_subagents` nests the agents."""
import os
from dataclasses import replace
from typing import List, Optional, Tuple, Union

from .claude_subagents import ReadSubAgent, SubAgentMeta, is_agent_file_name, meta_path_of, nest_sub_agents, parse_meta
from .claude_transcript import TRANSCRIPT_SUFFIX, parse_main_session, parse_sub_agent_session
from .model import Harness, Session, is_safe_id
from .reader_port import UNSAFE_ID_REASON, ReadResult, ScanResult, SourceUnit, UnreadableSource, build_session_key
from .store_files import FILE_ENCODING, StoreFiles

_PROJECTS_DIR = "projects"
_SUBAGENTS_DIR = "subagents"
_SUB_AGENT_PLACE_LENGTH = 4  # <project>/<sid>/subagents/<agent file or folder>, below the projects folder
_SUBAGENTS_DIR_INDEX = 2

_Found = Union[SourceUnit, UnreadableSource]
_Owner = Tuple[str, str]  # (project folder name, session id) of the session folder a sub-agent file sits in


def _stem(path: str) -> str:
    return os.path.basename(path)[: -len(TRANSCRIPT_SUFFIX)]


_FILES = StoreFiles(Harness.CLAUDE)


def _source_of(entry: os.DirEntry) -> Optional[_Found]:
    """The unit of a session or sub-agent file; None for a directory or a file that vanished meanwhile."""
    return _FILES.regular_file_unit(entry, entry.path, lambda file_stat: "%d:%d" % (file_stat.st_size, file_stat.st_mtime_ns))


def _meta_fingerprint(agent_path: str) -> str:
    try:
        meta_stat = os.stat(meta_path_of(agent_path))
    except OSError:
        return "-"
    return "%d:%d" % (meta_stat.st_size, meta_stat.st_mtime_ns)


def _sub_agent_unit(entry: os.DirEntry) -> Optional[_Found]:
    """The unit of a sub-agent transcript. Its fingerprint also covers the `.meta.json` beside it, which gives its type and
    parent, so a meta that appears or changes alone makes the agent be read again."""
    source = _source_of(entry)
    if isinstance(source, SourceUnit):
        return replace(source, fingerprint="%s|%s" % (source.fingerprint, _meta_fingerprint(entry.path)))
    return source


def _sub_agent_entry(entry: os.DirEntry) -> Tuple[Optional[_Found], bool]:
    """What an entry of a `subagents` tree is: (its unit or unreadable source, whether it is a folder to walk into)."""
    if is_agent_file_name(entry.name):
        return _sub_agent_unit(entry), False
    is_dir = _FILES.is_real_dir(entry)
    return (is_dir, False) if isinstance(is_dir, UnreadableSource) else (None, bool(is_dir))


def _sub_agent_sources(subagents_dir: str) -> List[_Found]:
    """Every `agent-*.jsonl` below a session's `subagents` folder at any depth (workflow agents sit in subfolders). The
    folder itself must not be a link or junction."""
    if os.path.islink(subagents_dir) or os.path.isjunction(subagents_dir):
        return []
    found: List[_Found] = []
    pending = [subagents_dir]
    while pending:
        listing = _FILES.list_dir(pending.pop())
        if isinstance(listing, UnreadableSource):
            found.append(listing)
            continue
        for entry in listing:
            source, is_folder = _sub_agent_entry(entry)
            if source:
                found.append(source)
            if is_folder:
                pending.append(entry.path)
    return found


def _entry_sources(entry: os.DirEntry) -> List[_Found]:
    """A project folder entry: a main-session file, or a session folder with the sub-agent files of its `subagents` tree."""
    if entry.name.endswith(TRANSCRIPT_SUFFIX):
        source = _source_of(entry)
        return [source] if source else []
    is_dir = _FILES.is_real_dir(entry)
    if isinstance(is_dir, UnreadableSource):
        return [is_dir]
    return _sub_agent_sources(os.path.join(entry.path, _SUBAGENTS_DIR)) if is_dir else []


def _project_sources(project: os.DirEntry) -> List[_Found]:
    is_dir = _FILES.examine(project.path, project.is_dir)
    if isinstance(is_dir, UnreadableSource):
        return [is_dir]
    listing = _FILES.list_dir(project.path) if is_dir else []
    if isinstance(listing, UnreadableSource):
        return [listing]
    return [found for entry in listing for found in _entry_sources(entry)]


def _read_meta(agent_path: str) -> SubAgentMeta:
    """The agent's `.meta.json`. It is no unit of its own, so a missing, undecodable or unusable one is no reason to drop the
    agent: its type is "unknown" and it has no parent. A file that is not a regular one (a FIFO would block) is never opened."""
    meta_path = meta_path_of(agent_path)
    if not os.path.isfile(meta_path):
        return SubAgentMeta()
    try:
        with open(meta_path, encoding=FILE_ENCODING) as handle:
            return parse_meta(handle.read())
    except (OSError, UnicodeDecodeError):
        return SubAgentMeta()


def _read_sub_agent(unit: SourceUnit) -> Union[ReadSubAgent, UnreadableSource]:
    """An agent whose records carry no time but whose requests count starts when its file was last changed."""
    transcript = _FILES.parse_file(unit.key, parse_sub_agent_session)
    if isinstance(transcript, UnreadableSource):
        return transcript
    started_at = transcript.started_at or (unit.modified_at if transcript.requests else None)
    return ReadSubAgent(agent_id=_stem(unit.key), meta=_read_meta(unit.key), started_at=started_at,
                        working_time=transcript.working_time, requests=transcript.requests)


def _read_sub_agents(units: List[SourceUnit]) -> Tuple[List[ReadSubAgent], Tuple[UnreadableSource, ...]]:
    results = [_read_sub_agent(unit) for unit in sorted(units, key=lambda unit: unit.key)]
    return ([agent for agent in results if isinstance(agent, ReadSubAgent)],
            tuple(agent for agent in results if isinstance(agent, UnreadableSource)))


class ClaudeReader:
    """Reads `<root>/projects/<project>/<sid>.jsonl` and the sub-agent files `<sid>/subagents/**/agent-*.jsonl` below the
    Claude config dir `root`; each file is one unit, and a sub-agent unit belongs to the session of its folder. `read` uses
    the main file of the session; when several units carry the same session id it takes the newest `modified_at`, ties
    going to the greatest key, and the sub-agent units of that file's own folder. A sub-agent is named by its file stem
    (`agent-<id>`), typed and parented by its `.meta.json`. Every file-system or decoding problem becomes an
    `UnreadableSource`, never an exception."""

    def __init__(self, root: str) -> None:
        self._projects = os.path.join(root, _PROJECTS_DIR)

    def scan(self) -> ScanResult:
        projects = _FILES.list_dir(self._projects)
        if isinstance(projects, UnreadableSource):
            return ScanResult(unreadable=(projects,))
        found = [source for project in projects for source in _project_sources(project)]
        units = [source for source in found if isinstance(source, SourceUnit)]
        unreadable = [source for source in found if isinstance(source, UnreadableSource)]
        return ScanResult(units=sorted(units, key=lambda unit: unit.key), unreadable=sorted(unreadable, key=lambda source: source.path))

    def _sub_agent_owner(self, key: str) -> Optional[_Owner]:
        """The session folder a sub-agent file sits in; None for a main-session file or a key outside the projects folder.
        Decided from the key's place below the projects folder, with no file access."""
        try:
            parts = os.path.relpath(key, self._projects).split(os.sep)
        except (ValueError, OSError):
            return None
        is_sub_agent = (len(parts) >= _SUB_AGENT_PLACE_LENGTH and parts[0] != os.pardir
                        and parts[_SUBAGENTS_DIR_INDEX] == _SUBAGENTS_DIR)
        return (parts[0], parts[1]) if is_sub_agent else None

    def session_of(self, unit: SourceUnit) -> Optional[str]:
        owner = self._sub_agent_owner(unit.key)
        return build_session_key(Harness.CLAUDE, owner[1] if owner else _stem(unit.key))

    def read(self, session_key: str, units: Tuple[SourceUnit, ...]) -> ReadResult:
        matching = [unit for unit in units if self.session_of(unit) == session_key]
        mains = [unit for unit in matching if self._sub_agent_owner(unit.key) is None]
        if not mains:
            return ReadResult()
        path = max(mains, key=lambda unit: (unit.modified_at, unit.key)).key
        session_id = _stem(path)
        if not is_safe_id(session_id):
            return ReadResult(unreadable=(_FILES.unreadable(path, UNSAFE_ID_REASON),))
        transcript = _FILES.parse_file(path, parse_main_session)
        if isinstance(transcript, UnreadableSource):
            return ReadResult(unreadable=(transcript,))
        if transcript.started_at is None:
            return ReadResult()
        owner = (os.path.basename(os.path.dirname(path)), session_id)
        agents, unreadable = _read_sub_agents([unit for unit in matching if self._sub_agent_owner(unit.key) == owner])
        return ReadResult(
            session=Session(
                harness=Harness.CLAUDE, session_id=session_id, started_at=transcript.started_at,
                project_dir=transcript.project_dir, prompts=transcript.prompts,
                sub_agents=nest_sub_agents(agents, transcript.message_ids)),
            unreadable=unreadable)
