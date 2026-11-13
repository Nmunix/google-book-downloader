"""Tests for the browser layer that do not need a real browser."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from google_book_downloader.browser import Browser, BrowserConfig, _event_url, _Recorder


@dataclass
class _Request:
    url: str


@dataclass
class _Event:
    request: Any


def test_recorder_collects_pages_from_dataclass_events() -> None:
    recorder = _Recorder()
    recorder(_Event(_Request("https://books.google.com/books/content?id=x&pg=PA1&zoom=1&w=200")))
    recorder(_Event(_Request("https://books.google.com/books/content?id=x&pg=PA2&zoom=2&w=200")))
    pages = recorder.pages
    assert sorted(pages) == [1, 2]
    assert "zoom=3" in pages[1].url


def test_recorder_ignores_non_page_urls() -> None:
    recorder = _Recorder()
    recorder(_Event(_Request("https://books.google.com/books?id=x&jscmd=click3")))
    recorder(_Event(_Request("https://fonts.example.com/roboto.woff2")))
    assert recorder.pages == {}


def test_recorder_keeps_the_highest_zoom_for_a_page() -> None:
    recorder = _Recorder()
    recorder(_Event(_Request("https://books.google.com/books/content?id=x&pg=PA1&zoom=3&w=2000")))
    recorder(_Event(_Request("https://books.google.com/books/content?id=x&pg=PA1&zoom=1&w=200")))
    assert recorder.pages[1].url.count("zoom=3") == 1


def test_recorder_handles_dict_payloads() -> None:
    recorder = _Recorder()
    recorder({"request": {"url": "https://books.google.com/books/content?id=x&pg=PA9"}})
    assert list(recorder.pages) == [9]


def test_event_url_extraction_is_defensive() -> None:
    assert not _event_url(object())
    assert _event_url({"request": {"url": "https://a"}}) == "https://a"
    assert _event_url(_Event(_Request("https://b"))) == "https://b"


def test_browser_rejects_unknown_browser() -> None:
    from google_book_downloader.errors import BrowserError

    browser = Browser(BrowserConfig(browser="netscape"))
    with pytest.raises(BrowserError, match="Unsupported browser"):
        browser._build_options()


def test_driver_property_requires_start() -> None:
    from google_book_downloader.errors import BrowserError

    with pytest.raises(BrowserError):
        _ = Browser().driver


def test_close_is_idempotent() -> None:
    browser = Browser()
    browser.close()
    browser.close()


@pytest.mark.parametrize("browser_name", ["chrome", "edge", "firefox"])
def test_build_options_sets_bidi(browser_name: str) -> None:
    pytest.importorskip("selenium")
    options = Browser(BrowserConfig(browser=browser_name))._build_options()
    assert options.enable_bidi is True


# ---------------------------------------------------------------------------
# capture loop, driven by a fake WebDriver
# ---------------------------------------------------------------------------
class _FakeNetwork:
    """Records the event handlers the browser subscribes."""

    def __init__(self) -> None:
        self.handlers: dict[str, list[Any]] = {}

    def add_event_handler(self, event: str, callback: Any) -> int:
        self.handlers.setdefault(event, []).append(callback)
        return len(self.handlers[event])


class _FakeDriver:
    """Minimal WebDriver stand-in that advances through a preview."""

    def __init__(self, pages: int, *, step: int = 1, emit_all: bool = True, responsive: bool = True) -> None:
        self.pages = pages
        self.step = step
        self.responsive = responsive
        self.current = 1
        self.visited: list[int] = [1]
        self.clickable = True
        self.capabilities = {"webSocketUrl": "ws://fake"}
        self._emit_all = emit_all
        self.network = _FakeNetwork()

    def execute_script(self, script: str, *args: object) -> object:
        if "getPageCount" in script:  # state probe
            return {"page": self.current, "pages": self.pages, "id": "x", "vital": self.current}
        if args:  # goto-page
            if self.responsive:
                self.current = min(int(args[0]), self.pages)
            if self.current not in self.visited:
                self.visited.append(self.current)
        return True

    def find_element(self, by: str, value: str) -> Any:
        from selenium.common.exceptions import NoSuchElementException

        if not self.clickable:
            raise NoSuchElementException(value)
        return self

    def click(self) -> None:
        if not self.responsive:
            return
        self.current = min(self.current + self.step, self.pages)
        if self.current not in self.visited:
            self.visited.append(self.current)

    def send_keys(self, *keys: object) -> None:
        self.click()


def _fake_browser(driver: _FakeDriver) -> Browser:
    browser = Browser(BrowserConfig(settle_time=0.01))
    browser._driver = driver  # type: ignore[assignment]

    def record_pages() -> None:
        for number in driver.visited:
            browser._recorder(
                _Event(_Request(f"https://books.google.com/books/content?id=x&pg=PA{number}&zoom=3&w=2000"))
            )

    original_state = browser._viewer_state

    def state_then_record() -> dict[str, Any]:
        record_pages()
        return original_state()

    browser._viewer_state = state_then_record  # type: ignore[method-assign]
    browser._navigate = lambda url: None  # type: ignore[method-assign]
    browser.read_metadata = lambda ref: ("Title", "Author")  # type: ignore[method-assign]
    browser._drain_viewer = lambda: record_pages()  # type: ignore[method-assign]
    return browser


def test_capture_walks_every_page() -> None:
    from google_book_downloader.bookurl import BookRef

    driver = _FakeDriver(pages=6)
    book = _fake_browser(driver).capture(BookRef("buc0AAAAMAAJ"))
    assert sorted(book.pages) == [1, 2, 3, 4, 5, 6]
    assert book.title == "Title"
    assert book.author == "Author"


def test_capture_respects_max_pages() -> None:
    from google_book_downloader.bookurl import BookRef

    driver = _FakeDriver(pages=50)
    book = _fake_browser(driver).capture(BookRef("buc0AAAAMAAJ"), max_pages=3)
    assert len(book.pages) >= 3
    assert len(driver.visited) <= 6


def test_capture_stops_when_page_never_turns() -> None:
    from google_book_downloader.bookurl import BookRef

    driver = _FakeDriver(pages=100, responsive=False)
    book = _fake_browser(driver).capture(BookRef("buc0AAAAMAAJ"))
    # Only page 1 exists, and the loop must terminate quickly rather than spin.
    assert list(book.pages) == [1]


def test_capture_without_images_raises() -> None:
    from google_book_downloader.bookurl import BookRef
    from google_book_downloader.errors import NoPagesFound

    driver = _FakeDriver(pages=3)
    browser = _fake_browser(driver)
    browser._viewer_state = browser._viewer_state  # keep the recording hook
    browser._drain_viewer = lambda: None  # type: ignore[method-assign]
    browser._viewer_state = lambda: {"page": 3, "pages": 3}  # type: ignore[method-assign]
    with pytest.raises(NoPagesFound):
        browser.capture(BookRef("buc0AAAAMAAJ"))


def test_capture_progress_callback() -> None:
    from google_book_downloader.bookurl import BookRef

    driver = _FakeDriver(pages=4)
    seen: list[tuple[int, int]] = []
    _fake_browser(driver).capture(BookRef("buc0AAAAMAAJ"), progress=lambda count, number: seen.append((count, number)))
    assert [number for _count, number in seen] == [1, 2, 3, 4]
