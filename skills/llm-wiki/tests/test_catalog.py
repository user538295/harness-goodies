import hashlib
import json
import os
import re
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

_ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$")

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from catalog import (
    CatalogEntry,
    CatalogStore,
    _catalog_path,
    _existing_file_key,
    _hash_file,
    _ledger_key,
    add,
    get,
    main,
    remove,
    get_status,
)


# --- helpers ---

def _mk_raw(tmp_path: Path) -> Path:
    raw = tmp_path / "llm-wiki" / "raw"
    raw.mkdir(parents=True)
    return raw


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


# --- CatalogStore tests ---

def test_store_load_missing_file(tmp_path):
    assert CatalogStore.load(tmp_path / "nope.json") == {}


def test_store_load_file_vanishing_after_check_reads_as_empty(tmp_path, monkeypatch):
    # Simulate the ledger being deleted between an existence check and the
    # read: Path.exists() reports True but the file is already gone.
    monkeypatch.setattr(Path, "exists", lambda self, **kwargs: True)
    assert CatalogStore.load(tmp_path / "nope.json") == {}


def test_store_load_invalid_json_raises(tmp_path):
    bad = tmp_path / "catalog.json"
    bad.write_text("not json{{{")
    with pytest.raises(json.JSONDecodeError):
        CatalogStore.load(bad)


def test_store_load_wrong_shape_raises(tmp_path):
    # Valid JSON, wrong shape: must fail loud, not silently wipe the ledger.
    bad = tmp_path / "catalog.json"
    bad.write_text('{"a.md": "not-an-entry"}')
    with pytest.raises((KeyError, TypeError)):
        CatalogStore.load(bad)


def test_store_roundtrip(tmp_path):
    path = tmp_path / "catalog.json"
    entries = {
        "a.md": CatalogEntry(sha256="abc", ingested_at="2026-01-01T00:00:00+00:00"),
        "sub/b.md": CatalogEntry(sha256="def", ingested_at="2026-02-02T00:00:00+00:00"),
    }
    CatalogStore.save(path, entries)
    assert CatalogStore.load(path) == entries


def test_store_save_atomic_no_tmp_left(tmp_path):
    path = tmp_path / "catalog.json"
    first = {"a.md": CatalogEntry(sha256="x", ingested_at="t")}
    CatalogStore.save(path, first)
    # The atomic rename leaves no temp file and lands the exact ledger.
    assert not path.with_suffix(".tmp").exists()
    assert CatalogStore.load(path) == first

    # A second save atomically replaces the file wholesale (no merge/leftover).
    second = {"b.md": CatalogEntry(sha256="y", ingested_at="u")}
    CatalogStore.save(path, second)
    assert not path.with_suffix(".tmp").exists()
    assert CatalogStore.load(path) == second


def test_store_save_creates_parent_dir(tmp_path):
    path = tmp_path / ".watcher" / "catalog.json"
    CatalogStore.save(path, {"a.md": CatalogEntry(sha256="x", ingested_at="t")})
    assert path.exists()


# --- _hash_file tests ---

_FIFO_OPEN_TIMEOUT_SECONDS = 2.0


def test_hash_file_rejects_fifo_swapped_in_after_type_check(tmp_path, monkeypatch):
    # Simulate a regular file swapped for a FIFO between the type check and
    # open(): Path.is_file() reports True, but the path is a FIFO. Hashing must
    # reject it without blocking on the FIFO open (no writer ever appears).
    fifo = tmp_path / "pipe.md"
    os.mkfifo(fifo)
    monkeypatch.setattr(Path, "is_file", lambda self, **kwargs: True)
    outcome: dict[str, BaseException | str] = {}

    def hash_in_thread() -> None:
        try:
            outcome["result"] = _hash_file(fifo)
        except BaseException as e:  # captured for assertion on the main thread
            outcome["error"] = e

    worker = threading.Thread(target=hash_in_thread, daemon=True)
    worker.start()
    worker.join(_FIFO_OPEN_TIMEOUT_SECONDS)
    if worker.is_alive():
        # Unblock the stuck open() by appearing as a writer, then fail.
        os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        worker.join(_FIFO_OPEN_TIMEOUT_SECONDS)
        pytest.fail("_hash_file blocked opening a FIFO")
    assert isinstance(outcome.get("error"), ValueError)


def test_hash_file_regular_file(tmp_path):
    f = tmp_path / "doc.md"
    f.write_bytes(b"hello")
    assert _hash_file(f) == _sha(b"hello")


def test_hash_file_missing_raises_value_error(tmp_path):
    with pytest.raises(ValueError):
        _hash_file(tmp_path / "ghost.md")


# --- raw key resolution tests ---

def test_existing_file_key_relative_to_raw(tmp_path):
    raw = _mk_raw(tmp_path)
    (raw / "doc.md").write_text("x")
    assert _existing_file_key(tmp_path, "doc.md") == "doc.md"


def test_existing_file_key_tolerates_documented_raw_prefix(tmp_path):
    # Docs (schema.md) instruct `add <root> raw/<file>`; that form must resolve.
    raw = _mk_raw(tmp_path)
    (raw / "doc.md").write_text("x")
    assert _existing_file_key(tmp_path, "raw/doc.md") == "doc.md"


def test_existing_file_key_absolute_under_raw(tmp_path):
    raw = _mk_raw(tmp_path)
    f = raw / "sub" / "doc.md"
    f.parent.mkdir()
    f.write_text("x")
    assert _existing_file_key(tmp_path, str(f)) == "sub/doc.md"


def test_existing_file_key_rejects_path_outside_raw(tmp_path):
    _mk_raw(tmp_path)
    outside = tmp_path / "llm-wiki" / "wiki" / "page.md"
    outside.parent.mkdir(parents=True)
    outside.write_text("x")
    with pytest.raises(ValueError):
        _existing_file_key(tmp_path, str(outside))


def test_ledger_key_rejects_traversal(tmp_path):
    _mk_raw(tmp_path)
    with pytest.raises(ValueError):
        _ledger_key(tmp_path, "../secret.md")


def test_existing_file_key_add_requires_existing_file(tmp_path):
    _mk_raw(tmp_path)
    with pytest.raises(ValueError):
        _existing_file_key(tmp_path, "ghost.md")


def test_existing_file_key_preserves_external_symlink_path(tmp_path):
    raw = _mk_raw(tmp_path)
    outside = tmp_path / "external.md"
    outside.write_text("content")
    (raw / "linked.md").symlink_to(outside)
    assert _existing_file_key(tmp_path, "linked.md") == "linked.md"


def test_existing_file_key_preserves_internal_symlink_path(tmp_path):
    raw = _mk_raw(tmp_path)
    target = raw / "target.md"
    target.write_text("content")
    (raw / "linked.md").symlink_to(target)
    assert _existing_file_key(tmp_path, "linked.md") == "linked.md"


# --- add tests ---

def test_add_follows_external_symlink_file_and_status_is_current(tmp_path):
    raw = _mk_raw(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_bytes(b"external content")
    (raw / "linked.md").symlink_to(outside)

    add(tmp_path, ["linked.md"])

    entries = CatalogStore.load(_catalog_path(tmp_path))
    assert entries["linked.md"].sha256 == _sha(b"external content")
    assert get_status(tmp_path) == {
        "new": [],
        "changed": [],
        "current": ["linked.md"],
        "missing": [],
    }


def test_add_follows_external_symlink_directory_and_status_is_current(tmp_path):
    raw = _mk_raw(tmp_path)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "paper.md").write_bytes(b"external paper")
    (raw / "linked-corpus").symlink_to(corpus)

    add(tmp_path, ["linked-corpus/paper.md"])

    assert get_status(tmp_path) == {
        "new": [],
        "changed": [],
        "current": ["linked-corpus/paper.md"],
        "missing": [],
    }


def test_status_stops_symlink_directory_ancestor_cycle(tmp_path):
    raw = _mk_raw(tmp_path)
    (raw / "doc.md").write_bytes(b"content")
    (raw / "loop").symlink_to(raw, target_is_directory=True)

    assert get_status(tmp_path)["new"] == ["doc.md"]

def test_add_new_file_records_hash_and_injected_timestamp(tmp_path):
    raw = _mk_raw(tmp_path)
    content = b"hello world"
    (raw / "doc.md").write_bytes(content)
    fixed_time = datetime(2024, 2, 3, 4, 5, 6, tzinfo=timezone.utc)

    add(tmp_path, ["doc.md"], clock=lambda: fixed_time)

    entries = CatalogStore.load(_catalog_path(tmp_path))
    assert entries["doc.md"].sha256 == _sha(content)
    assert entries["doc.md"].ingested_at == "2024-02-03T04:05:06+00:00"




def test_add_upsert_on_reingest_updates_hash(tmp_path):
    raw = _mk_raw(tmp_path)
    f = raw / "doc.md"
    f.write_bytes(b"v1")
    add(tmp_path, ["doc.md"])

    f.write_bytes(b"v2-changed")
    add(tmp_path, ["doc.md"])

    entries = CatalogStore.load(_catalog_path(tmp_path))
    assert entries["doc.md"].sha256 == _sha(b"v2-changed")
    assert _ISO_UTC.match(entries["doc.md"].ingested_at)
    assert len(entries) == 1


def test_add_multiple_files(tmp_path):
    raw = _mk_raw(tmp_path)
    (raw / "a.md").write_bytes(b"a")
    (raw / "b.md").write_bytes(b"b")
    add(tmp_path, ["a.md", "b.md"])
    entries = CatalogStore.load(_catalog_path(tmp_path))
    assert set(entries) == {"a.md", "b.md"}


def test_add_outside_raw_raises(tmp_path):
    _mk_raw(tmp_path)
    outside = tmp_path / "elsewhere.md"
    outside.write_text("x")
    with pytest.raises(ValueError):
        add(tmp_path, [str(outside)])


def test_add_nonexistent_raises(tmp_path):
    _mk_raw(tmp_path)
    with pytest.raises(ValueError):
        add(tmp_path, ["ghost.md"])


# --- get tests ---

def test_get_existing(tmp_path):
    raw = _mk_raw(tmp_path)
    (raw / "doc.md").write_bytes(b"hi")
    add(tmp_path, ["doc.md"])
    entry = get(tmp_path, "doc.md")
    assert entry is not None
    assert entry.sha256 == _sha(b"hi")


def test_get_missing_returns_none(tmp_path):
    _mk_raw(tmp_path)
    assert get(tmp_path, "doc.md") is None


# --- remove tests ---

def test_remove_existing(tmp_path):
    raw = _mk_raw(tmp_path)
    (raw / "a.md").write_bytes(b"a")
    (raw / "b.md").write_bytes(b"b")
    add(tmp_path, ["a.md", "b.md"])
    remove(tmp_path, ["a.md"])
    entries = CatalogStore.load(_catalog_path(tmp_path))
    assert set(entries) == {"b.md"}


def test_remove_missing_is_idempotent(tmp_path):
    _mk_raw(tmp_path)
    remove(tmp_path, ["ghost.md"])  # must not raise
    assert CatalogStore.load(_catalog_path(tmp_path)) == {}


def test_remove_accepts_deleted_file(tmp_path):
    raw = _mk_raw(tmp_path)
    f = raw / "a.md"
    f.write_bytes(b"a")
    add(tmp_path, ["a.md"])
    f.unlink()
    remove(tmp_path, ["a.md"])  # file gone, entry must still be removable
    assert CatalogStore.load(_catalog_path(tmp_path)) == {}


# --- status tests ---

def test_status_new_changed_current_missing(tmp_path):
    raw = _mk_raw(tmp_path)

    # current: ingested, unchanged
    (raw / "current.md").write_bytes(b"same")
    add(tmp_path, ["current.md"])

    # changed: ingested then edited
    changed = raw / "changed.md"
    changed.write_bytes(b"v1")
    add(tmp_path, ["changed.md"])
    changed.write_bytes(b"v2")

    # missing: ingested then file deleted
    gone = raw / "gone.md"
    gone.write_bytes(b"x")
    add(tmp_path, ["gone.md"])
    gone.unlink()

    # new: never ingested
    (raw / "new.md").write_bytes(b"fresh")

    result = get_status(tmp_path)
    assert result["new"] == ["new.md"]
    assert result["changed"] == ["changed.md"]
    assert result["current"] == ["current.md"]
    assert result["missing"] == ["gone.md"]


def test_status_empty_catalog_all_new(tmp_path):
    raw = _mk_raw(tmp_path)
    (raw / "a.md").write_bytes(b"a")
    (raw / "b.md").write_bytes(b"b")
    result = get_status(tmp_path)
    assert result["new"] == ["a.md", "b.md"]
    assert result["changed"] == []
    assert result["current"] == []
    assert result["missing"] == []


def test_status_empty_raw(tmp_path):
    _mk_raw(tmp_path)
    result = get_status(tmp_path)
    assert result == {"new": [], "changed": [], "current": [], "missing": []}


def test_status_follows_symlinked_file(tmp_path):
    raw = _mk_raw(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("content")
    (raw / "link.md").symlink_to(outside)
    result = get_status(tmp_path)
    assert result["new"] == ["link.md"]


def test_status_follows_symlinked_directory(tmp_path):
    raw = _mk_raw(tmp_path)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text("a")
    (corpus / "b.md").write_text("b")
    (raw / "linked-corpus").symlink_to(corpus)
    result = get_status(tmp_path)
    assert sorted(result["new"]) == ["linked-corpus/a.md", "linked-corpus/b.md"]


def test_status_skips_broken_symlink(tmp_path):
    raw = _mk_raw(tmp_path)
    (raw / "broken.md").symlink_to(tmp_path / "nonexistent.md")
    result = get_status(tmp_path)
    assert result["new"] == []


def test_status_lists_sorted(tmp_path):
    raw = _mk_raw(tmp_path)
    for name in ("z.md", "a.md", "m.md"):
        (raw / name).write_bytes(b"x")
    result = get_status(tmp_path)
    assert result["new"] == ["a.md", "m.md", "z.md"]


# --- CLI (main) tests ---

def test_cli_status_prints_json(tmp_path, capsys):
    raw = _mk_raw(tmp_path)
    (raw / "a.md").write_bytes(b"a")
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(sys, "argv", ["catalog.py", "status", str(tmp_path)])
        main()
    out = json.loads(capsys.readouterr().out)
    assert out["new"] == ["a.md"]


def test_cli_add_then_get(tmp_path, capsys):
    raw = _mk_raw(tmp_path)
    (raw / "a.md").write_bytes(b"a")
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(sys, "argv", ["catalog.py", "add", str(tmp_path), "a.md"])
        main()
    capsys.readouterr()
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(sys, "argv", ["catalog.py", "get", str(tmp_path), "a.md"])
        main()
    out = json.loads(capsys.readouterr().out)
    assert out["sha256"] == _sha(b"a")


def test_cli_add_with_documented_raw_prefix(tmp_path, capsys):
    # schema.md documents `add <root> raw/<file>`; it must record, not crash.
    raw = _mk_raw(tmp_path)
    (raw / "a.md").write_bytes(b"a")
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(sys, "argv", ["catalog.py", "add", str(tmp_path), "raw/a.md"])
        main()
    out = json.loads(capsys.readouterr().out)
    assert out["current"] == ["a.md"]
    assert get(tmp_path, "a.md") is not None


def test_cli_get_missing_prints_null(tmp_path, capsys):
    _mk_raw(tmp_path)
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(sys, "argv", ["catalog.py", "get", str(tmp_path), "a.md"])
        main()
    assert json.loads(capsys.readouterr().out) is None


def test_cli_add_outside_raw_exits_nonzero(tmp_path):
    _mk_raw(tmp_path)
    outside = tmp_path / "x.md"
    outside.write_text("x")
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(sys, "argv", ["catalog.py", "add", str(tmp_path), str(outside)])
        with pytest.raises(SystemExit) as exc:
            main()
    assert exc.value.code != 0
