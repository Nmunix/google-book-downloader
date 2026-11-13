"""Shared fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from google_book_downloader.models import Book, BookPage


@pytest.fixture
def sample_book() -> Book:
    """A five-page book with realistic Google Books image URLs."""
    book = Book(volume_id="buc0AAAAMAAJ", title="The Art of Testing", author="Ada Lovelace")
    for number in range(1, 6):
        book.pages[number] = BookPage(
            number=number,
            url=(
                "https://books.google.com/books/content?id=buc0AAAAMAAJ"
                f"&pg=PA{number}&img=1&zoom=3&hl=en&sig=ACfU3U{number}"
            ),
            label=f"PA{number}",
        )
    return book


@pytest.fixture
def png_bytes() -> bytes:
    """A minimal 1x1 PNG."""
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
        b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )


@pytest.fixture
def image_dir(tmp_path: Path) -> Path:
    """A directory holding three tiny generated page images."""
    from PIL import Image

    directory = tmp_path / "images"
    directory.mkdir()
    for number in (1, 2, 3):
        target = directory / f"page-{number:04d}.png"
        Image.new("RGB", (40, 60), (number * 40, 60, 90)).save(target)
    return directory
