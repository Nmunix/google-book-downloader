"""Parsing of Google Books URLs and of the ``books/content`` image URLs.

Google reserves the right to change the viewer, so the extraction logic is
kept here, small and well-covered by tests.  Both URL shapes that Google has
used over the years are understood:

``https://books.google.com/books/content?id=ID&pg=PA12&img=1&zoom=3&hl=en``
``https://books.googleusercontent.com/books/content?req=...&pg=PA12&w=1280``
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from google_book_downloader.errors import InvalidBookUrl

#: Volume ids are 12 characters of ``[A-Za-z0-9_-]``, but be tolerant of
#: longer/shorter legacy ids instead of hard-failing on the exact length.
_VOLUME_ID = re.compile(r"^[A-Za-z0-9_-]{5,32}$")
_ID_IN_TEXT = re.compile(r"^id=([A-Za-z0-9_-]{5,32})$|[?&]id=([A-Za-z0-9_-]{5,32})")
_PG_MARKER = re.compile(r"^[A-Z]*?(\d+)$")
_EDITION_PATH = re.compile(r"/books/edition/(?:[^/]+/)?([A-Za-z0-9_-]{5,32})")

#: The zoom levels the viewer asks for; the largest one is the best quality
#: a preview will serve.  Higher values are rejected with HTTP 400.
MAX_ZOOM = 3
#: Legacy ``books/content`` endpoints honour a ``w=`` width hint.  Anything
#: above the source scan resolution is simply clamped by the server.
MAX_WIDTH = 2000
#: Hosts we accept a book URL from.
GOOGLE_HOSTS = ("books.google.", "google.com/books", "googleusercontent.com/books")

#: Page-image hosts we are willing to download from.
ALLOWED_HOSTS = ("books.google.com", "books.googleusercontent.com", "books.google.co.uk")


@dataclass(frozen=True, slots=True)
class BookRef:
    """A parsed reference to a volume."""

    volume_id: str

    @property
    def data_url(self) -> str:
        """Viewer URL used to read the title and author."""
        return f"https://books.google.com/books?id={self.volume_id}&pg=PA1&hl=en&f=false"

    @property
    def embed_url(self) -> str:
        """Embedded viewer URL; its page turns trigger the image requests."""
        return f"https://books.google.com/books?id={self.volume_id}&pg=PA1&hl=en&f=false&output=embed&source=gbs_embed"


def parse_book_reference(value: str) -> BookRef:
    """Extract the volume id from a URL, an ``id=...`` fragment or a bare id.

    Args:
        value: A full Google Books URL (with any amount of query noise), a
            string containing ``id=<volume id>``, or a bare id.

    Returns:
        The parsed :class:`BookRef`.

    Raises:
        InvalidBookUrl: When no valid volume id can be found.
    """
    text = value.strip().strip("'\"")
    if not text:
        raise InvalidBookUrl("No URL or book id was provided.")

    if "://" in text and not any(host in text for host in GOOGLE_HOSTS):
        raise InvalidBookUrl(
            f"{value!r} does not look like a Google Books link. "
            "Paste a URL from books.google.com (or just the volume id)."
        )

    candidate = ""
    edition = _EDITION_PATH.search(text)
    matches = _ID_IN_TEXT.findall(text)
    if edition:
        candidate = edition.group(1)
    elif matches:
        # Take the last occurrence so tracking parameters cannot shadow it.
        first, second = matches[-1]
        candidate = first or second
    else:
        parsed = urlparse(text if "://" in text else f"https://{text}")
        if "/books" in parsed.path:
            ids = parse_qs(parsed.query).get("id", [])
            candidate = ids[0] if ids else ""
        else:
            # A pasted fragment such as ``buc0AAAAMAAJ&printsec=frontcover#v=...``
            leading = re.split(r"[&#]", text, maxsplit=1)[0]
            if _VOLUME_ID.match(leading):
                candidate = leading

    if not candidate:
        raise InvalidBookUrl(
            "Could not find a book id in the input. Paste the full URL of the "
            "preview, e.g. https://books.google.com/books?id=buc0AAAAMAAJ"
        )
    if not _VOLUME_ID.match(candidate):
        raise InvalidBookUrl(f"{candidate!r} is not a valid Google Books volume id.")
    return BookRef(volume_id=candidate)


def page_label_to_number(label: str) -> int | None:
    """Convert a ``pg=`` marker such as ``PA12`` to ``12``.

    Front-matter markers (``PR3``, ``PT5``) and cover markers (``PP1``) follow
    the same rule, so the numeric suffix is enough to order and select pages.

    Args:
        label: Raw page marker.

    Returns:
        The page number, or ``None`` when the marker has no numeric part.
    """
    match = _PG_MARKER.match(label.strip())
    if not match:
        return None
    number = int(match.group(1))
    return number if number > 0 else None


def is_page_image_url(url: str) -> bool:
    """Return ``True`` for Google Books page-image URLs we may download."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.netloc.lower().split(":")[0]
    if not any(host == allowed or host.endswith(f".{allowed}") for allowed in ALLOWED_HOSTS):
        return False
    if "/books/content" not in parsed.path:
        return False
    return "pg" in parse_qs(parsed.query)


def request_best_quality(url: str) -> str:
    """Rewrite an observed image URL to ask for the highest available quality.

    The viewer only requests what fits on screen; the same resource can serve
    a much larger scan.  ``zoom`` is raised to :data:`MAX_ZOOM` and a generous
    ``w`` is added, which the server clamps to the native resolution.

    Args:
        url: Image URL as observed in the browser.

    Returns:
        The rewritten URL, or the original one when it is not a page image.
    """
    if not is_page_image_url(url):
        return url
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    flat = {key: values[-1] for key, values in query.items()}
    flat.pop("w", None)
    flat.pop("h", None)
    flat["zoom"] = str(MAX_ZOOM)
    flat["w"] = str(MAX_WIDTH)
    flat["hl"] = flat.get("hl", "en")
    return urlunparse(parsed._replace(query=urlencode(flat, safe=":")))


def page_number_from_url(url: str) -> tuple[int, str] | None:
    """Extract ``(page number, raw marker)`` from an image URL."""
    query = parse_qs(urlparse(url).query)
    labels = query.get("pg") or []
    if not labels:
        return None
    label = labels[0]
    number = page_label_to_number(label)
    if number is None:
        return None
    return number, label
