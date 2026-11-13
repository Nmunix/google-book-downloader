"""Tests for filesystem helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from google_book_downloader.paths import atomic_write_bytes, atomic_write_text, slugify, unique_directory


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("The Art of Testing (Ada Lovelace)", "The-Art-of-Testing-Ada-Lovelace"),
        ("  spaces   and__underscores  ", "spaces-and-underscores"),
        ("Ünïcödé Tïtlé", "Unicode-Title"),
        ('bad<>:"/\\|?*chars', "bad-chars"),
        ("trailing...", "trailing"),
        ("", ""),
        ("---", ""),
    ],
)
def test_slugify(raw: str, expected: str) -> None:
    assert slugify(raw) == expected


def test_slugify_caps_length() -> None:
    assert len(slugify("x" * 500, max_length=20)) == 20


def test_slugify_escapes_windows_reserved_names() -> None:
    assert slugify("CON") == "CON-book"


def test_unique_directory_never_clobbers(tmp_path: Path) -> None:
    first = unique_directory(tmp_path, "book")
    second = unique_directory(tmp_path, "book")
    third = unique_directory(tmp_path, "book")
    assert first == tmp_path / "book"
    assert second == tmp_path / "book-2"
    assert third == tmp_path / "book-3"
    assert all(path.is_dir() for path in (first, second, third))


def test_atomic_write_is_exact(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "file.bin"
    atomic_write_bytes(target, b"\x00\x01\x02")
    assert target.read_bytes() == b"\x00\x01\x02"
    assert list(target.parent.glob("*.tmp")) == []


def test_atomic_write_text_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"
    atomic_write_text(target, "hola")
    assert target.read_text(encoding="utf-8") == "hola"
