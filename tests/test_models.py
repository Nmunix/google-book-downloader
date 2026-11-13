"""Tests for the data model."""

from __future__ import annotations

import json

from google_book_downloader.models import UNKNOWN_AUTHOR, Book, BookPage


def test_display_name_and_slug(sample_book: Book) -> None:
    assert sample_book.display_name == "The Art of Testing (Ada Lovelace)"
    assert sample_book.slug == "The-Art-of-Testing-Ada-Lovelace"


def test_sorted_pages(sample_book: Book) -> None:
    sample_book.pages.pop(3)
    assert [page.number for page in sample_book.sorted_pages()] == [1, 2, 4, 5]


def test_unknown_author_roundtrip() -> None:
    book = Book(volume_id="abcde", title="Untitled")
    assert book.author == UNKNOWN_AUTHOR
    assert Book.from_dict(json.loads(json.dumps(book.to_dict()))).author == UNKNOWN_AUTHOR


def test_to_dict_is_json_serialisable(sample_book: Book) -> None:
    payload = json.dumps(sample_book.to_dict())
    assert '"schema": 2' in payload
    assert Book.from_dict(json.loads(payload)).pages == sample_book.pages


def test_page_from_dict_tolerates_missing_label() -> None:
    page = BookPage.from_dict({"number": "7", "url": "https://x"})
    assert page.number == 7
    assert page.label == ""
