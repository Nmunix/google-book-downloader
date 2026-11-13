"""Plain data types shared by the scrape, download and export stages.

Everything here is framework-free and JSON-serialisable, which keeps the
browser layer thin and lets the rest be tested without a browser.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Placeholder used when Google exposes no author for a volume.
UNKNOWN_AUTHOR = "Unknown author"


@dataclass(frozen=True, slots=True)
class BookPage:
    """A single leaf of a Google Books preview.

    Attributes:
        number: 1-based page number derived from the ``pg=PA<n>`` marker.
        url: Direct ``books/content`` image URL, already widened to the
            highest resolution the server will hand out.
        label: The raw page marker reported by Google (``PA12``, ``PR3``...).
    """

    number: int
    url: str
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation."""
        return {"number": self.number, "url": self.url, "label": self.label}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BookPage:
        """Rebuild a page from :meth:`to_dict` output."""
        return cls(number=int(data["number"]), url=str(data["url"]), label=str(data.get("label", "")))


@dataclass(slots=True)
class Book:
    """A volume and every page image that was captured for it.

    Attributes:
        volume_id: The Google Books volume id (``id=`` query parameter).
        title: Volume title as shown by the viewer.
        author: Volume author, or :data:`UNKNOWN_AUTHOR`.
        pages: Page number -> page, ordered by page number.
    """

    volume_id: str
    title: str
    author: str = UNKNOWN_AUTHOR
    pages: dict[int, BookPage] = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        """Human-readable ``Title (Author)`` label used for folders and logs."""
        return f"{self.title} ({self.author})"

    @property
    def slug(self) -> str:
        """Filesystem-safe name derived from :attr:`display_name`."""
        from google_book_downloader.paths import slugify

        return slugify(f"{self.title} - {self.author}") or self.volume_id

    def sorted_pages(self) -> list[BookPage]:
        """Return the captured pages ordered by page number."""
        return [self.pages[number] for number in sorted(self.pages)]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation used for backups."""
        return {
            "schema": 2,
            "volume_id": self.volume_id,
            "title": self.title,
            "author": self.author,
            "pages": [page.to_dict() for page in self.sorted_pages()],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Book:
        """Rebuild a book from :meth:`to_dict` output."""
        book = cls(
            volume_id=str(data["volume_id"]),
            title=str(data.get("title") or data["volume_id"]),
            author=str(data.get("author") or UNKNOWN_AUTHOR),
        )
        for raw_page in data.get("pages", []):
            page = BookPage.from_dict(raw_page)
            book.pages[page.number] = page
        return book
