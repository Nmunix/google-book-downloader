"""Parsing of the page-selection mini-language.

The syntax kept from the original tool, plus a few conveniences::

    all                 every captured page
    5, 3, 16            individual pages
    11-13, 1-100        inclusive ranges (``13-11`` is normalised)
    odd, even           parity filters
    first, last         the first/last captured page
    first-10, 90-last   ranges anchored on either edge
    -20, 30-            open-ended ranges (``-20`` means "up to 20")
    1, 10-50, odd, 603  any comma-separated combination (deduplicated)

The parser never guesses: anything it cannot understand raises
:class:`~google_book_downloader.errors.PageSelectionError` naming the offending
token, which is far friendlier than the silent no-op the old code produced.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from google_book_downloader.errors import PageSelectionError

_KEYWORDS = {"all", "odd", "even", "first", "last"}
_TOKEN_SPLIT = re.compile(r"[,\s]+")
_RANGE_SPLIT = re.compile(r"\s*(?:\.\.-?|-)\s*")
_ANCHOR = re.compile(r"^(first|last|\d+)?$")


@dataclass(frozen=True, slots=True)
class PageSelection:
    """A parsed page specification, resolved against the captured pages."""

    spec: str
    numbers: frozenset[int]
    requested: frozenset[int]

    @property
    def missing(self) -> frozenset[int]:
        """Pages the user asked for explicitly that the preview does not expose."""
        return self.requested - self.numbers

    def __bool__(self) -> bool:
        return bool(self.numbers)


def _edge(available: list[int]) -> tuple[int, int]:
    if not available:
        raise PageSelectionError("There are no captured pages to select from.")
    return available[0], available[-1]


def _resolve_anchor(token: str, available: list[int], original: str) -> int:
    if token == "first":
        return _edge(available)[0]
    if token == "last":
        return _edge(available)[1]
    if token.isdigit():
        return int(token)
    raise PageSelectionError(f"Could not understand {original!r} in the page selection.")


def parse_selection(spec: str | None, available: Mapping[int, object] | Iterable[int]) -> PageSelection:
    """Resolve a page specification against the pages that were captured.

    Args:
        spec: The mini-language described in the module docstring. ``None`` or
            an empty string means *all*.
        available: Captured page numbers (a mapping keyed by number also works).

    Returns:
        A :class:`PageSelection` with concrete page numbers.

    Raises:
        PageSelectionError: If the specification is empty of meaning or malformed.
    """
    numbers = sorted(int(number) for number in available)
    text = (spec or "").strip().lower()
    if not text or text in {"all", "*"}:
        return PageSelection(spec=spec or "all", numbers=frozenset(numbers), requested=frozenset())

    tokens = [token for token in _TOKEN_SPLIT.split(text) if token]
    if not tokens:
        raise PageSelectionError("The page selection is empty.")

    selected: set[int] = set()
    requested: set[int] = set()

    for token in tokens:
        if token in _KEYWORDS:
            if token == "all":
                selected.update(numbers)
            elif token == "odd":
                selected.update(number for number in numbers if number % 2)
            elif token == "even":
                selected.update(number for number in numbers if number % 2 == 0)
            elif token in {"first", "last"}:
                selected.add(_resolve_anchor(token, numbers, token))
            continue

        bounds = _RANGE_SPLIT.split(token)
        if len(bounds) > 2:
            raise PageSelectionError(f"Could not understand the range {token!r}.")
        if len(bounds) == 2:
            raw_start, raw_end = bounds
            for bound in (raw_start, raw_end):
                if bound and not _ANCHOR.match(bound):
                    raise PageSelectionError(f"Could not understand the range {token!r}.")
            start = _resolve_anchor(raw_start, numbers, token) if raw_start else _edge(numbers)[0]
            end = _resolve_anchor(raw_end, numbers, token) if raw_end else _edge(numbers)[1]
            if start > end:
                if raw_start in {"first", "last"} or raw_end in {"first", "last"}:
                    raise PageSelectionError(f"The range {token!r} runs backwards (first/last cannot be swapped).")
                start, end = end, start
            for number in range(start, end + 1):
                requested.add(number)
                if number in available:
                    selected.add(number)
            continue

        if not token.isdigit():
            raise PageSelectionError(
                f"Could not understand {token!r}. Use numbers, ranges (10-20), "
                "the words all/odd/even/first/last, separated by commas."
            )
        number = int(token)
        requested.add(number)
        if number in available:
            selected.add(number)

    return PageSelection(spec=spec or "", numbers=frozenset(selected), requested=frozenset(requested))
