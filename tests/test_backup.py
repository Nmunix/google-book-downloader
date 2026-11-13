"""Tests for the JSON backup format."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from google_book_downloader.backup import default_backup_path, load_backup, save_backup
from google_book_downloader.errors import BackupError
from google_book_downloader.models import Book


def test_roundtrip(tmp_path: Path, sample_book: Book) -> None:
    path = save_backup(tmp_path / "book.json", sample_book)
    restored = load_backup(path)
    assert restored.volume_id == sample_book.volume_id
    assert restored.title == sample_book.title
    assert restored.author == sample_book.author
    assert restored.pages == sample_book.pages


def test_written_file_is_human_readable_json(tmp_path: Path, sample_book: Book) -> None:
    path = save_backup(tmp_path / "book.json", sample_book)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema"] == 2
    assert data["volume_id"] == sample_book.volume_id
    assert data["pages"][0] == {"number": 1, "url": sample_book.pages[1].url, "label": "PA1"}


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(BackupError, match="not found"):
        load_backup(tmp_path / "nope.json")


def test_legacy_python_literal_is_rejected_with_guidance(tmp_path: Path) -> None:
    legacy = tmp_path / "old.txt"
    legacy.write_text('{1: "https://books.google.com/books/content?pg=PA1"}', encoding="utf-8")
    with pytest.raises(BackupError, match="old version"):
        load_backup(legacy)


def test_garbage_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "junk.json"
    path.write_text("not json at all", encoding="utf-8")
    with pytest.raises(BackupError, match="not a valid backup"):
        load_backup(path)


def test_newer_schema_is_refused(tmp_path: Path, sample_book: Book) -> None:
    payload = sample_book.to_dict()
    payload["schema"] = 99
    path = tmp_path / "future.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BackupError, match="newer version"):
        load_backup(path)


def test_backup_without_pages_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"schema": 2, "volume_id": "abc", "title": "T", "pages": []}), encoding="utf-8")
    with pytest.raises(BackupError, match="any pages"):
        load_backup(path)


def test_default_backup_path_is_slugged(sample_book: Book) -> None:
    path = default_backup_path(Path("/tmp"), sample_book)
    assert path.name.endswith(".gbd.json")
    assert " " not in path.name
