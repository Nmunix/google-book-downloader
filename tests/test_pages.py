"""Tests for the page-selection mini-language."""

from __future__ import annotations

import pytest

from google_book_downloader.errors import PageSelectionError
from google_book_downloader.pages import parse_selection

AVAILABLE = {number: object() for number in range(1, 11)}


@pytest.mark.parametrize("spec", [None, "", "  ", "all", "ALL", "*"])
def test_all_forms(spec: str | None) -> None:
    selection = parse_selection(spec, AVAILABLE)
    assert selection.numbers == frozenset(range(1, 11))


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("3", {3}),
        ("3,5,9", {3, 5, 9}),
        ("3 5 9", {3, 5, 9}),
        ("2-4", {2, 3, 4}),
        ("4-2", {2, 3, 4}),
        ("1, 3-5, 9", {1, 3, 4, 5, 9}),
        ("odd", {1, 3, 5, 7, 9}),
        ("even", {2, 4, 6, 8, 10}),
        ("odd, 2", {1, 2, 3, 5, 7, 9}),
        ("first", {1}),
        ("last", {10}),
        ("first-3", {1, 2, 3}),
        ("8-last", {8, 9, 10}),
        ("-3", {1, 2, 3}),
        ("9-", {9, 10}),
        ("1..3", {1, 2, 3}),
    ],
)
def test_selection_specs(spec: str, expected: set[int]) -> None:
    assert parse_selection(spec, AVAILABLE).numbers == set(expected)


def test_out_of_range_pages_are_reported_as_missing() -> None:
    selection = parse_selection("1, 999", AVAILABLE)
    assert selection.numbers == {1}
    assert selection.missing == {999}


def test_selection_is_deduplicated_and_ordered_invariantly() -> None:
    selection = parse_selection("1,1,odd,1-2", AVAILABLE)
    assert selection.numbers == {1, 2, 3, 5, 7, 9}


@pytest.mark.parametrize("spec", ["abc", "1-x", "5-6-7", "page 5", "@"])
def test_invalid_specs_raise(spec: str) -> None:
    with pytest.raises(PageSelectionError):
        parse_selection(spec, AVAILABLE)


def test_empty_book_yields_nothing_for_explicit_ranges() -> None:
    assert parse_selection("1-5", {}).numbers == frozenset()
    assert parse_selection("1-5", {}).missing == {1, 2, 3, 4, 5}


def test_empty_book_raises_for_edge_keywords() -> None:
    with pytest.raises(PageSelectionError):
        parse_selection("first-3", {})


def test_selection_truthiness() -> None:
    assert parse_selection("1", AVAILABLE)
    assert not parse_selection("999", AVAILABLE)


def test_backwards_keyword_range_is_rejected() -> None:
    with pytest.raises(PageSelectionError, match="backwards"):
        parse_selection("last-first", AVAILABLE)
