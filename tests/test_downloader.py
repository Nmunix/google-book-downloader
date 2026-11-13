"""Tests for the downloader, with HTTP stubbed out."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from google_book_downloader.downloader import Downloader, sniff_suffix
from google_book_downloader.errors import DownloadError
from google_book_downloader.models import Book, BookPage


def _page(number: int) -> BookPage:
    return BookPage(
        number=number, url=f"https://books.google.com/books/content?id=x&pg=PA{number}", label=f"PA{number}"
    )


def test_sniff_suffix() -> None:
    assert sniff_suffix(b"\x89PNG\r\n\x1a\n...") == ".png"
    assert sniff_suffix(b"\xff\xd8\xff\xe0") == ".jpg"
    assert sniff_suffix(b"GIF89a") == ".gif"
    assert sniff_suffix(b"RIFFxxxxWEBP") == ".webp"
    assert sniff_suffix(b"????") == ".png"


def test_download_writes_numbered_files(tmp_path: Path, png_bytes: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=png_bytes))
    real_client = httpx.Client

    monkeypatch.setattr(
        "google_book_downloader.downloader.httpx.Client",
        lambda **kwargs: real_client(transport=transport, **{k: v for k, v in kwargs.items() if k != "http2"}),
    )

    result = Downloader(concurrency=2).download([_page(2), _page(1)], tmp_path)
    assert result.ok
    assert sorted(path.name for path in result.saved.values()) == ["page-0001.png", "page-0002.png"]
    assert (tmp_path / "page-0001.png").read_bytes() == png_bytes


def test_existing_pages_are_skipped(tmp_path: Path, png_bytes: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "page-0001.png").write_bytes(png_bytes)
    calls: list[str] = []
    transport = httpx.MockTransport(
        lambda request: (calls.append(str(request.url)), httpx.Response(200, content=png_bytes))[1]
    )
    real_client = httpx.Client
    monkeypatch.setattr(
        "google_book_downloader.downloader.httpx.Client",
        lambda **kwargs: real_client(transport=transport, **{k: v for k, v in kwargs.items() if k != "http2"}),
    )

    result = Downloader().download([_page(1), _page(2)], tmp_path)
    assert result.skipped == {1}
    assert set(result.saved) == {2}
    assert len(calls) == 1


def test_overwrite_re_downloads(tmp_path: Path, png_bytes: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "page-0001.png").write_bytes(b"stale")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=png_bytes))
    real_client = httpx.Client
    monkeypatch.setattr(
        "google_book_downloader.downloader.httpx.Client",
        lambda **kwargs: real_client(transport=transport, **{k: v for k, v in kwargs.items() if k != "http2"}),
    )
    result = Downloader().download([_page(1)], tmp_path, overwrite=True)
    assert set(result.saved) == {1}
    assert (tmp_path / "page-0001.png").read_bytes() == png_bytes


def test_partial_failure_is_reported(tmp_path: Path, png_bytes: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "PA2" in str(request.url):
            return httpx.Response(404)
        return httpx.Response(200, content=png_bytes)

    real_client = httpx.Client
    monkeypatch.setattr(
        "google_book_downloader.downloader.httpx.Client",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(handler), **{k: v for k, v in kwargs.items() if k != "http2"}
        ),
    )
    result = Downloader(retries=1).download([_page(1), _page(2)], tmp_path)
    assert set(result.saved) == {1}
    assert set(result.failed) == {2}
    assert not result.ok


def test_total_failure_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_client = httpx.Client
    monkeypatch.setattr(
        "google_book_downloader.downloader.httpx.Client",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(lambda request: httpx.Response(500)),
            **{k: v for k, v in kwargs.items() if k != "http2"},
        ),
    )
    with pytest.raises(DownloadError, match="Every page failed"):
        Downloader(retries=1).download([_page(1)], tmp_path)


def test_progress_callback_reports_every_page(
    tmp_path: Path, png_bytes: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_client = httpx.Client
    monkeypatch.setattr(
        "google_book_downloader.downloader.httpx.Client",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=png_bytes)),
            **{k: v for k, v in kwargs.items() if k != "http2"},
        ),
    )
    seen: list[tuple[int, int, int]] = []
    Downloader().download([_page(1), _page(2), _page(3)], tmp_path, progress=lambda *args: seen.append(args))
    assert seen[-1][:2] == (3, 3)


def test_download_book_uses_sorted_pages(
    tmp_path: Path, png_bytes: bytes, monkeypatch: pytest.MonkeyPatch, sample_book: Book
) -> None:
    real_client = httpx.Client
    monkeypatch.setattr(
        "google_book_downloader.downloader.httpx.Client",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=png_bytes)),
            **{k: v for k, v in kwargs.items() if k != "http2"},
        ),
    )
    result = Downloader().download_book(sample_book, tmp_path)
    assert sorted(result.saved) == [1, 2, 3, 4, 5]
