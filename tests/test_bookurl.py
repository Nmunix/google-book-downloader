"""Tests for book URL parsing and image URL handling."""

from __future__ import annotations

import pytest

from google_book_downloader.bookurl import (
    MAX_WIDTH,
    MAX_ZOOM,
    is_page_image_url,
    page_label_to_number,
    page_number_from_url,
    parse_book_reference,
    request_best_quality,
)
from google_book_downloader.errors import InvalidBookUrl

FULL_URL = (
    "https://books.google.com/books?id=buc0AAAAMAAJ&printsec=frontcover&sa=X&ved=2ahUKEwj"
    "-y8T4r5vrAhWKLewKHaIQBnYQ6AEwAXoECAQQAg#v=onepage&f=false"
)


@pytest.mark.parametrize(
    "value",
    [
        FULL_URL,
        "https://books.google.com/books?id=buc0AAAAMAAJ",
        "https://books.google.co.uk/books?id=buc0AAAAMAAJ&hl=en",
        "books.google.com/books?id=buc0AAAAMAAJ",
        "https://www.google.com/books/edition/_/buc0AAAAMAAJ?hl=en",
        "id=buc0AAAAMAAJ",
        "buc0AAAAMAAJ",
        "  buc0AAAAMAAJ  ",
    ],
)
def test_parse_book_reference_accepts_common_inputs(value: str) -> None:
    assert parse_book_reference(value).volume_id == "buc0AAAAMAAJ"


def test_parse_book_reference_uses_last_id_parameter() -> None:
    url = "https://books.google.com/books?id=wrong12345&id=buc0AAAAMAAJ"
    assert parse_book_reference(url).volume_id == "buc0AAAAMAAJ"


@pytest.mark.parametrize("value", ["", "   ", "https://example.com/books", "https://books.google.com/books", "a b c"])
def test_parse_book_reference_rejects_junk(value: str) -> None:
    with pytest.raises(InvalidBookUrl):
        parse_book_reference(value)


def test_urls_built_from_reference() -> None:
    ref = parse_book_reference("buc0AAAAMAAJ")
    assert "id=buc0AAAAMAAJ" in ref.data_url
    assert "output=embed" in ref.embed_url


@pytest.mark.parametrize(
    ("label", "expected"),
    [("PA12", 12), ("PR3", 3), ("PP1", 1), ("PA0", None), ("", None), ("XYZ", None)],
)
def test_page_label_to_number(label: str, expected: int | None) -> None:
    assert page_label_to_number(label) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://books.google.com/books/content?id=x&pg=PA5&img=1&zoom=1&hl=en", True),
        ("https://books.googleusercontent.com/books/content?req=ABC&pg=PA5&w=800", True),
        ("https://books.google.com/books?id=x&pg=PA5", False),
        ("https://evil.example.com/books/content?pg=PA5", False),
        ("ftp://books.google.com/books/content?pg=PA5", False),
        ("https://books.google.com/books/content?id=x", False),
    ],
)
def test_is_page_image_url(url: str, expected: bool) -> None:
    assert is_page_image_url(url) is expected


def test_request_best_quality_upgrades_zoom_and_width() -> None:
    observed = "https://books.google.com/books/content?id=abc&pg=PA7&img=1&zoom=1&hl=en&w=300&h=400"
    upgraded = request_best_quality(observed)
    assert f"zoom={MAX_ZOOM}" in upgraded
    assert f"w={MAX_WIDTH}" in upgraded
    assert "h=" not in upgraded
    assert "pg=PA7" in upgraded
    assert "id=abc" in upgraded


def test_request_best_quality_leaves_foreign_urls_alone() -> None:
    url = "https://example.com/cat.png"
    assert request_best_quality(url) == url


def test_page_number_from_url() -> None:
    url = "https://books.google.com/books/content?id=x&pg=PA42&img=1"
    assert page_number_from_url(url) == (42, "PA42")
    assert page_number_from_url("https://books.google.com/books/content?id=x") is None


def test_parse_pasted_fragment() -> None:
    assert parse_book_reference("buc0AAAAMAAJ&printsec=frontcover#v=onepage").volume_id == "buc0AAAAMAAJ"


def test_parse_domain_variants() -> None:
    for domain in ("books.google.de", "books.google.co.jp", "www.google.com"):
        url = f"https://{domain}/books?id=buc0AAAAMAAJ&hl=en"
        assert parse_book_reference(url).volume_id == "buc0AAAAMAAJ"


def test_non_google_url_is_rejected() -> None:
    with pytest.raises(InvalidBookUrl, match="does not look like a Google Books link"):
        parse_book_reference("https://example.com/books?id=buc0AAAAMAAJ")
