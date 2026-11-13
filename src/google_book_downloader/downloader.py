"""Concurrent, retrying image downloader.

``requests`` was replaced by ``httpx``: it shares one connection pool across
threads, has sane timeout objects instead of tuple guesswork, and its API is
the same shape as the async one should this ever need to scale further.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from google_book_downloader.errors import DownloadError
from google_book_downloader.models import Book, BookPage
from google_book_downloader.paths import atomic_write_bytes

logger = logging.getLogger(__name__)

#: A realistic desktop UA; Google serves preview images happily to it.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)

#: Page images are PNG or JPEG; the extension follows the sniffed magic bytes.
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF8", ".gif"),
    (b"BM", ".bmp"),
    (b"RIFF", ".webp"),
)

ProgressCallback = Callable[[int, int, int], None]
"""Called as ``(done, total, page_number)`` after every saved page."""


@dataclass(slots=True)
class DownloadResult:
    """Outcome of a download run."""

    directory: Path
    saved: dict[int, Path] = field(default_factory=dict)
    failed: dict[int, str] = field(default_factory=dict)
    skipped: set[int] = field(default_factory=set)

    @property
    def ok(self) -> bool:
        """True when nothing failed."""
        return not self.failed

    def summary(self) -> str:
        """One-line human summary."""
        parts = [f"{len(self.saved)} page(s) saved"]
        if self.skipped:
            parts.append(f"{len(self.skipped)} already present")
        if self.failed:
            parts.append(f"{len(self.failed)} failed")
        return ", ".join(parts)


def sniff_suffix(payload: bytes) -> str:
    """Guess an image extension from the first bytes of ``payload``."""
    for magic, suffix in _MAGIC:
        if payload.startswith(magic):
            return suffix
    return ".png"


class Downloader:
    """Downloads page images with bounded concurrency and retries.

    Args:
        concurrency: Parallel connections. Google throttles aggressively above
            ~8, so the default is deliberately modest.
        retries: Attempts per page before giving up on it.
        timeout: Per-request timeout in seconds.
        user_agent: Override the ``User-Agent`` header.
        cookie: Optional raw ``Cookie`` header value captured from the browser.
            Preview images are public, but a session cookie makes the requests
            look like the browser that just viewed the book.
    """

    def __init__(
        self,
        *,
        concurrency: int = 4,
        retries: int = 3,
        timeout: float = 30.0,
        user_agent: str = DEFAULT_USER_AGENT,
        cookie: str | None = None,
    ) -> None:
        self.concurrency = max(1, concurrency)
        self.retries = max(1, retries)
        self.timeout = timeout
        self.user_agent = user_agent
        self.cookie = cookie
        self._lock = threading.Lock()

    def _headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Referer": "https://books.google.com/",
        }
        if self.cookie:
            headers["Cookie"] = self.cookie
        return headers

    def _fetch(self, client: httpx.Client, url: str) -> bytes:
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                response = client.get(url, headers=self._headers())
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                last_error = exc
                status = exc.response.status_code
                # 4xx (except 429) will not fix itself; stop early.
                if status in {401, 403, 404, 410}:
                    raise DownloadError(f"HTTP {status} for {url}") from exc
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if not response.content:
                    last_error = DownloadError(f"Empty response for {url}")
                else:
                    return response.content
            if attempt < self.retries:
                logger.debug("Retrying %s (attempt %s/%s)", url, attempt + 1, self.retries)
                threading.Event().wait(min(2 ** (attempt - 1), 5))
        raise DownloadError(str(last_error) if last_error else f"Could not download {url}")

    def download(
        self,
        pages: Iterable[BookPage],
        directory: Path,
        *,
        overwrite: bool = False,
        progress: ProgressCallback | None = None,
    ) -> DownloadResult:
        """Save ``pages`` as numbered image files inside ``directory``.

        Args:
            pages: Pages to download.
            directory: Destination folder; created if missing.
            overwrite: Re-download pages whose file already exists.
            progress: Optional ``(done, total, page_number)`` callback.

        Returns:
            A :class:`DownloadResult`.

        Raises:
            DownloadError: If *every* page failed, which almost always means
                the network is down or Google is blocking this IP.
        """
        directory.mkdir(parents=True, exist_ok=True)
        ordered = sorted(pages, key=lambda page: page.number)
        result = DownloadResult(directory=directory)
        if not ordered:
            return result

        pending: list[BookPage] = []
        for page in ordered:
            if not overwrite and _existing_page(directory, page.number) is not None:
                result.skipped.add(page.number)
            else:
                pending.append(page)
        done = len(result.skipped)
        total = len(ordered)
        if progress is not None and done:
            progress(done, total, 0)

        limits = httpx.Limits(max_connections=self.concurrency, max_keepalive_connections=self.concurrency)
        with (
            httpx.Client(
                timeout=httpx.Timeout(self.timeout, connect=10.0),
                limits=limits,
                follow_redirects=True,
                http2=False,
            ) as client,
            ThreadPoolExecutor(max_workers=self.concurrency) as pool,
        ):
            futures = {pool.submit(self._fetch, client, page.url): page for page in pending}
            for future in as_completed(futures):
                page = futures[future]
                try:
                    payload = future.result()
                except DownloadError as exc:
                    result.failed[page.number] = str(exc)
                    logger.warning("Page %s failed: %s", page.number, exc)
                else:
                    suffix = sniff_suffix(payload)
                    path = directory / f"page-{page.number:04d}{suffix}"
                    atomic_write_bytes(path, payload)
                    result.saved[page.number] = path
                finally:
                    done += 1
                    if progress is not None:
                        progress(done, total, page.number)

        if not result.saved and result.failed:
            first_error = next(iter(result.failed.values()))
            raise DownloadError(
                f"Every page failed to download ({first_error}). Check your internet "
                "connection, or whether Google is rate-limiting this network."
            )
        return result

    def download_book(self, book: Book, directory: Path, **kwargs: object) -> DownloadResult:
        """Convenience wrapper for :meth:`download` over a whole book."""
        return self.download(book.sorted_pages(), directory, **kwargs)  # type: ignore[arg-type]


def _existing_page(directory: Path, number: int) -> Path | None:
    for suffix in (".png", ".jpg", ".gif", ".bmp", ".webp"):
        candidate = directory / f"page-{number:04d}{suffix}"
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    return None
