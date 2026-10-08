"""Ingest ledger for the LLM Wiki raw/ folder.

Records which raw files have been ingested into the wiki and their sha256 at
ingest time, so changed files can be flagged for re-ingest. This is the
*ingested*-hash authority; the current-hash is computed on demand by get_status().
Script-owned so the LLM never hand-edits the ledger.
"""

import argparse
import hashlib
import json
import os
import stat
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

READ_CHUNK_BYTES = 65536
# Non-blocking so opening a FIFO/device never hangs; the type is then checked
# on the opened descriptor itself, leaving no check-then-open race window.
_HASH_OPEN_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY


def _hash_file(path: Path) -> str:
    # Guard the hash boundary: a user-supplied path (possibly via a symlink)
    # must resolve to a regular file before it is read. This blocks
    # devices/FIFOs/sockets that would otherwise be read here.
    try:
        fd = os.open(path, _HASH_OPEN_FLAGS)
    except FileNotFoundError as e:
        raise ValueError(f"not a regular file: {path}") from e
    with open(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"not a regular file: {path}")
        h = hashlib.sha256()
        while chunk := f.read(READ_CHUNK_BYTES):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class CatalogEntry:
    sha256: str
    ingested_at: str


class CatalogStore:
    @staticmethod
    def load(path: Path) -> dict[str, CatalogEntry]:
        """Load the ledger. Missing file → empty ledger. A corrupt or
        wrong-shape file raises (JSONDecodeError/KeyError/TypeError) rather than
        silently reading as empty, which would trigger a full re-ingest."""
        try:
            data = json.loads(path.read_text())
        except FileNotFoundError:
            return {}
        return {
            k: CatalogEntry(sha256=v["sha256"], ingested_at=v["ingested_at"])
            for k, v in data.items()
        }

    @staticmethod
    def save(path: Path, entries: dict[str, CatalogEntry]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            k: {"sha256": e.sha256, "ingested_at": e.ingested_at}
            for k, e in entries.items()
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
        tmp.rename(path)


def _now_iso(clock: Callable[[], datetime]) -> str:
    return clock().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _raw_dir(project_root: Path) -> Path:
    return (Path(project_root) / "llm-wiki" / "raw").resolve()


def _catalog_path(project_root: Path) -> Path:
    # Committed (not under gitignored .watcher/) so a fresh clone keeps the
    # ingest history instead of re-ingesting the whole corpus.
    return Path(project_root) / "llm-wiki" / "catalog.json"


def _has_raw_prefix(path: Path) -> bool:
    return not path.is_absolute() and len(path.parts) > 1 and path.parts[0] == "raw"


def _raw_path(project_root: Path, raw_path: str) -> Path:
    """Normalize a user path lexically and require it to remain under raw/."""
    raw_dir = _raw_dir(project_root)
    path = Path(raw_path)
    if _has_raw_prefix(path):
        path = Path(*path.parts[1:])
    path = path if path.is_absolute() else raw_dir / path
    # Collapse traversal components without resolving symlinks, preserving link paths.
    path = Path(os.path.normpath(path))
    try:
        path.relative_to(raw_dir)
    except ValueError:
        raise ValueError(f"path is not under {raw_dir}: {raw_path}")
    return path


def _existing_file_key(project_root: Path, raw_path: str) -> str:
    """Resolve an existing file to a POSIX key relative to raw/."""
    path = _raw_path(project_root, raw_path)
    if not path.is_file():
        raise ValueError(f"not an existing file under raw/: {raw_path}")
    return path.relative_to(_raw_dir(project_root)).as_posix()


def _ledger_key(project_root: Path, raw_path: str) -> str:
    """Resolve a ledger key, including paths for files already removed."""
    return _raw_path(project_root, raw_path).relative_to(_raw_dir(project_root)).as_posix()


def add(
    project_root: Path,
    paths: list[str],
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> None:
    """Record (upsert) each raw file's current sha256 and ingest time. Each path
    must be an existing file under raw/ (bare or `raw/`-prefixed); raises
    ValueError otherwise."""
    cat_path = _catalog_path(project_root)
    entries = CatalogStore.load(cat_path)
    raw_dir = _raw_dir(project_root)
    for raw_path in paths:
        key = _existing_file_key(project_root, raw_path)
        entries[key] = CatalogEntry(
            sha256=_hash_file(raw_dir / key),
            ingested_at=_now_iso(clock),
        )
    CatalogStore.save(cat_path, entries)


def remove(project_root: Path, paths: list[str]) -> None:
    """Drop each path's ledger entry. Idempotent: a path not in the ledger (or
    whose file was already deleted) is a no-op, not an error."""
    cat_path = _catalog_path(project_root)
    entries = CatalogStore.load(cat_path)
    for raw_path in paths:
        key = _ledger_key(project_root, raw_path)
        entries.pop(key, None)
    CatalogStore.save(cat_path, entries)


def get(project_root: Path, raw_path: str) -> CatalogEntry | None:
    """Return the ledger entry for a raw path, or None if not catalogued."""
    entries = CatalogStore.load(_catalog_path(project_root))
    key = _ledger_key(project_root, raw_path)
    return entries.get(key)


def _scan_children(directory: Path) -> list:
    """Directory entries, or [] if the directory can't be scanned."""
    try:
        with os.scandir(directory) as children:
            return list(children)
    except OSError:
        return []


def _dir_identity(child) -> tuple[int, int] | None:
    """(st_dev, st_ino) if child is a (followable) directory, else None."""
    try:
        if child.is_dir(follow_symlinks=True):
            child_stat = child.stat(follow_symlinks=True)
            return (child_stat.st_dev, child_stat.st_ino)
    except OSError:
        return None
    return None


def _is_regular_file(child) -> bool:
    try:
        return child.is_file(follow_symlinks=True)
    except OSError:
        return False


def _iter_raw_files(raw_dir: Path):
    """Yield (posix_key, full_path) for every regular file under raw/, following
    symlinks but never revisiting a directory already on the current path so
    symlink cycles terminate."""
    if not raw_dir.exists():
        return
    root_stat = raw_dir.stat()
    stack = [(raw_dir, Path(), frozenset({(root_stat.st_dev, root_stat.st_ino)}))]
    while stack:
        directory, relative_dir, ancestors = stack.pop()
        for child in _scan_children(directory):
            relative_path = relative_dir / child.name
            identity = _dir_identity(child)
            if identity is not None:
                if identity not in ancestors:
                    stack.append(
                        (Path(child.path), relative_path, ancestors | {identity})
                    )
                continue
            if not _is_regular_file(child):
                continue
            yield relative_path.as_posix(), Path(child.path)


def get_status(project_root: Path) -> dict[str, list[str]]:
    """Diff raw/ against the catalog. new/changed/current re-hash each raw file
    in full on every call. missing = catalogued files no longer in raw/."""
    entries = CatalogStore.load(_catalog_path(project_root))
    raw_dir = _raw_dir(project_root)

    new, changed, current = [], [], []
    seen: set[str] = set()
    for key, full in _iter_raw_files(raw_dir):
        seen.add(key)
        entry = entries.get(key)
        if entry is None:
            new.append(key)
        elif entry.sha256 == _hash_file(full):
            current.append(key)
        else:
            changed.append(key)

    missing = [k for k in entries if k not in seen]
    return {
        "new": sorted(new),
        "changed": sorted(changed),
        "current": sorted(current),
        "missing": sorted(missing),
    }


def _cmd_add(args: argparse.Namespace) -> None:
    add(args.project_root, args.paths)
    print(json.dumps(get_status(args.project_root), indent=2))


def _cmd_remove(args: argparse.Namespace) -> None:
    remove(args.project_root, args.paths)
    print(json.dumps(get_status(args.project_root), indent=2))


def _cmd_get(args: argparse.Namespace) -> None:
    entry = get(args.project_root, args.path)
    print(json.dumps(
        None if entry is None
        else {"sha256": entry.sha256, "ingested_at": entry.ingested_at},
        indent=2,
    ))


def _cmd_status(args: argparse.Namespace) -> None:
    print(json.dumps(get_status(args.project_root), indent=2))


_COMMANDS: dict[str, Callable[[argparse.Namespace], None]] = {
    "add": _cmd_add,
    "remove": _cmd_remove,
    "get": _cmd_get,
    "status": _cmd_status,
}


def main() -> None:
    parser = argparse.ArgumentParser(prog="catalog")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("add", "remove"):
        p = sub.add_parser(name)
        p.add_argument("project_root", type=Path)
        p.add_argument("paths", nargs="+")

    p = sub.add_parser("get")
    p.add_argument("project_root", type=Path)
    p.add_argument("path")

    p = sub.add_parser("status")
    p.add_argument("project_root", type=Path)

    args = parser.parse_args()

    try:
        _COMMANDS[args.command](args)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
