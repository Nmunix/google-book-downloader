"""Browser automation built on Selenium 4.5x + WebDriver BiDi.

Why this module looks the way it does
-------------------------------------
The original program depended on ``selenium-wire`` (last released in 2022) to
sniff the network by proxying everything through mitmproxy, and on a
24 MB ``chromedriver.exe`` committed to the repository.  Both are gone:

* **Selenium Manager** ships inside Selenium (4.6+) and downloads the driver,
  and if needed the browser, that matches what is installed.  Nothing to pin,
  nothing to commit, nothing to keep in ``PATH``.
* **WebDriver BiDi** is the W3C standard replacement for the CDP hacks that
  sniffers relied on.  ``driver.network.add_event_handler`` gives us real
  ``network.responseStarted`` events, so page images are collected straight
  from the browser's own traffic -- no proxy, no certificate, no extra deps.

The scraping strategy is unchanged in spirit from the 2020 version: open the
embedded viewer, turn pages, and record every ``books/content`` image the
viewer requests.  Page turning now drives the viewer's own API (via
``script.execute``) instead of blind ``SPACE`` key presses, which makes
capture both faster and deterministic.
"""

from __future__ import annotations

import contextlib
import logging
import re
import shutil
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from google_book_downloader.bookurl import (
    BookRef,
    page_number_from_url,
    request_best_quality,
)
from google_book_downloader.errors import BrowserError, NoPagesFound
from google_book_downloader.models import UNKNOWN_AUTHOR, Book, BookPage

if TYPE_CHECKING:  # pragma: no cover - typing only
    from selenium import webdriver

logger = logging.getLogger(__name__)

CaptureCallback = Callable[[int, int], None]
"""Called as ``(captured_so_far, page_number)`` after every newly seen page."""

#: Browsers Selenium Manager knows how to provision for us.
SUPPORTED_BROWSERS = ("chrome", "edge", "firefox")

#: Title/author selectors, most specific first.  Google has shipped at least
#: three generations of this markup; we try them all instead of one.
_TITLE_SELECTORS = (
    ".gb-volume-title",
    "h1.gb-volume-title",
    "[itemprop='name']",
    "div.volume-title",
    "title",
)
_AUTHOR_SELECTORS = (
    ".addmd",
    "[itemprop='author']",
    "div.bookinfo_section a",
    ".gbb-bookauthor",
)

#: Scripts executed in the page to read the viewer's state.  Google's embedded
#: viewer keeps its controller on a couple of well-known globals.
_VIEWER_STATE_JS = """
try {
    const root = window._OC_Run || {};
    const cfg = root._b || root;
    const book = cfg._OC_Book || root._OC_Book || null;
    const total = book && book.getPageCount ? book.getPageCount() : null;
    const vital = book && book.getPageNumber ? book.getPageNumber() : null;
    return {
        page: cfg._pg || vital || null,
        pages: total || null,
        id: cfg._id || root._id || null,
        vital: vital,
    };
} catch (e) {
    return {error: String(e)};
}
"""

#: Ask the embedded viewer for a specific page.  Both the hash and the
#: ``_OC_Run`` helper are used because either alone can be a no-op depending
#: on the viewer revision that Google is serving today.
_GOTO_PAGE_JS = """
const page = arguments[0];
try {
    if (window._OC_Run && window._OC_Run.showPage) {
        window._OC_Run.showPage(String(page));
    }
} catch (e) {}
if (window.location.hash.indexOf('pg=') === -1) {
    window.location.hash = 'pg=PA' + page;
} else {
    window.location.replace('#pg=PA' + page);
}
return true;
"""

#: The viewer's next-page affordance, tried in order.
_NEXT_PAGE_SELECTORS = (
    "div[aria-label='Next page']",
    "button[aria-label='Next page']",
    ".nextPage",
    "#next-page",
    "div.pageImageDisplay",
)

#: The viewer asks Google for the next batch of page ids through this RPC; the
#: response carries a ``token`` that rotates while the book is still loading.
_TOKEN_URL = "https://books.google.com/books?id={volume_id}&jscmd=click3&pg=PA{page}&hl=en"


@dataclass(slots=True)
class BrowserConfig:
    """Everything needed to start a session.

    Attributes:
        browser: One of :data:`SUPPORTED_BROWSERS`.
        headless: Run without a window (recommended; the viewer still loads).
        binary: Optional path to a browser executable.
        driver_path: Optional path to a driver executable. Normally unset --
            Selenium Manager resolves it.
        page_load_timeout: Seconds to wait for a navigation.
        settle_time: Seconds to wait after a navigation for the viewer to
            start requesting images.
        window_size: Viewport, which also influences which image size the
            viewer requests.
        user_agent: Optional UA override.
    """

    browser: str = "chrome"
    headless: bool = True
    binary: str | None = None
    driver_path: str | None = None
    page_load_timeout: float = 60.0
    settle_time: float = 2.5
    window_size: tuple[int, int] = (1440, 1080)
    user_agent: str | None = None


@dataclass(slots=True)
class _Recorder:
    """Thread-safe collector for BiDi network events."""

    urls: dict[int, BookPage] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _seen: set[str] = field(default_factory=set, repr=False)

    def __call__(self, event: Any) -> None:
        """Handle a ``network.responseStarted`` (or completed) event."""
        url = _event_url(event)
        if not url or not page_number_from_url(url):
            return
        located = page_number_from_url(url)
        if located is None:
            return
        number, label = located
        with self._lock:
            self._seen.add(url)
            existing = self.urls.get(number)
            # Keep the highest-resolution URL we have seen for this page.
            if existing is None or len(url) >= len(existing.url) or _zoom(url) > _zoom(existing.url):
                self.urls[number] = BookPage(number=number, url=request_best_quality(url), label=label)

    @property
    def pages(self) -> dict[int, BookPage]:
        with self._lock:
            return dict(self.urls)


def _zoom(url: str) -> int:
    match = re.search(r"[?&]zoom=(\d+)", url)
    return int(match.group(1)) if match else 0


def _event_url(event: Any) -> str:
    """Pull the request URL out of a BiDi event, dataclass or plain dict."""
    candidates: list[Any] = [event]
    if isinstance(event, dict):
        candidates.extend([event.get("request"), event.get("response")])
    else:
        candidates.extend(
            [getattr(event, "request", None), getattr(event, "response", None), getattr(event, "__dict__", None)]
        )
    for candidate in candidates:
        if candidate is None:
            continue
        if isinstance(candidate, str):
            if candidate.startswith(("http://", "https://")):
                return candidate
            continue
        url = candidate.get("url") if isinstance(candidate, dict) else getattr(candidate, "url", None)
        if isinstance(url, str) and url:
            return url
    return ""


class Browser:
    """A headless browser session that records the page images it loads.

    Use as a context manager::

        with Browser() as session:
            book = session.capture(ref)
    """

    def __init__(self, config: BrowserConfig | None = None) -> None:
        self.config = config or BrowserConfig()
        self._driver: webdriver.Remote | None = None
        self._recorder = _Recorder()

    # -- lifecycle ---------------------------------------------------------
    def _build_options(self) -> Any:
        from selenium import webdriver

        browser = self.config.browser.lower()
        if browser not in SUPPORTED_BROWSERS:
            raise BrowserError(
                f"Unsupported browser {self.config.browser!r}. Choose one of: {', '.join(SUPPORTED_BROWSERS)}."
            )

        if browser == "firefox":
            options: Any = webdriver.FirefoxOptions()
            if self.config.headless:
                options.add_argument("-headless")
        else:
            options = webdriver.ChromeOptions() if browser == "chrome" else webdriver.EdgeOptions()
            if self.config.headless:
                # ``--headless=new`` is the current spelling; plain
                # ``--headless`` is aliased to it on Chrome 112+.
                options.add_argument("--headless=new")
            options.add_argument("--disable-gpu")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-background-networking")
            options.add_argument("--log-level=3")
            options.add_experimental_option("excludeSwitches", ["enable-logging"])
            options.add_experimental_option("prefs", {"safebrowsing.enabled": True})

        width, height = self.config.window_size
        options.add_argument(f"--window-size={width},{height}" if browser != "firefox" else f"--width={width}")
        options.add_argument(f"--user-agent={self.config.user_agent}" if self.config.user_agent else "--lang=en-US")
        options.page_load_strategy = "eager"
        # BiDi is what gives us network visibility; it is opt-in per session.
        options.enable_bidi = True
        if self.config.binary:
            options.binary_location = self.config.binary
        return options

    def _start_driver(self) -> Any:
        from selenium import webdriver
        from selenium.common.exceptions import WebDriverException

        options = self._build_options()
        browser = self.config.browser.lower()
        driver_class = {"chrome": webdriver.Chrome, "edge": webdriver.Edge, "firefox": webdriver.Firefox}[browser]

        kwargs: dict[str, Any] = {"options": options}
        if self.config.driver_path:
            service_class = {
                "chrome": webdriver.ChromeService,
                "edge": webdriver.EdgeService,
                "firefox": webdriver.FirefoxService,
            }[browser]
            kwargs["service"] = service_class(executable_path=self.config.driver_path)

        try:
            driver = driver_class(**kwargs)
        except WebDriverException as exc:
            raise BrowserError(self._explain_start_failure(exc)) from exc

        driver.set_page_load_timeout(self.config.page_load_timeout)
        return driver

    def _explain_start_failure(self, exc: Exception) -> str:
        binary = self.config.browser
        hints = [
            f"Could not start {binary}.",
            f"Original error: {exc}",
            "",
            "Selenium Manager downloads the driver automatically, so the usual causes are:",
            f"  - {binary} is not installed (or not on PATH); set --binary /path/to/{binary}",
            "  - the machine is offline, so the driver cannot be fetched;",
            "    pre-install it and pass --driver-path /path/to/driver",
            "  - a corporate proxy blocks the download; export HTTPS_PROXY accordingly",
        ]
        if binary == "chrome" and shutil.which("google-chrome") is None and shutil.which("chrome") is None:
            hints.append("  - on Debian/Ubuntu try: apt install chromium-browser  (or google-chrome-stable)")
        return "\n".join(hints)

    def __enter__(self) -> Browser:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def start(self) -> None:
        """Start the browser and subscribe to network events."""
        if self._driver is not None:
            return
        logger.debug("Starting %s (headless=%s)", self.config.browser, self.config.headless)
        self._driver = self._start_driver()
        self._subscribe()

    def close(self) -> None:
        """Close the browser, tolerating a session that already died."""
        if self._driver is None:
            return
        try:
            self._driver.quit()
        except Exception:  # shutdown must never mask the real error
            logger.debug("Ignoring error while closing the browser", exc_info=True)
        finally:
            self._driver = None

    @property
    def driver(self) -> webdriver.Remote:
        """The live WebDriver session."""
        if self._driver is None:
            raise BrowserError("The browser is not running. Call start() first.")
        return self._driver

    @property
    def bidi_available(self) -> bool:
        """Whether the session negotiated WebDriver BiDi."""
        try:
            return bool(self.driver.capabilities.get("webSocketUrl"))
        except BrowserError:
            return False

    # -- network capture ---------------------------------------------------
    def _subscribe(self) -> None:
        """Register the recorder on BiDi network events."""
        driver = self.driver
        if not driver.capabilities.get("webSocketUrl"):
            raise BrowserError(
                "This browser/session did not enable WebDriver BiDi, which is required to "
                "observe page images. Use a current Chrome, Edge or Firefox "
                "(Selenium 4.45+ talks to all three)."
            )
        try:
            network = driver.network
        except Exception as exc:
            raise BrowserError(
                f"The BiDi network module is unavailable in this Selenium build ({exc}). "
                "Upgrade with: pip install -U 'selenium>=4.45'"
            ) from exc
        for event in ("response_started", "response_completed", "before_request_sent"):
            try:
                network.add_event_handler(event, self._recorder)
            except Exception as exc:  # noqa: BLE001 - older bindings lack some events
                logger.debug("Could not subscribe to %s: %s", event, exc)
        logger.debug("Subscribed to BiDi network events")

    def reset_recording(self) -> None:
        """Forget every URL captured so far."""
        self._recorder = _Recorder()
        if self._driver is not None:
            self._subscribe()

    # -- page interaction --------------------------------------------------
    def _navigate(self, url: str) -> None:
        from selenium.common.exceptions import TimeoutException, WebDriverException

        try:
            self.driver.get(url)
        except TimeoutException:
            logger.debug("Navigation to %s timed out; continuing anyway", url)
        except WebDriverException as exc:
            raise BrowserError(f"Could not open {url}: {exc.msg if hasattr(exc, 'msg') else exc}") from exc
        time.sleep(self.config.settle_time)

    def _first_text(self, selectors: Iterable[str]) -> str:
        from selenium.common.exceptions import WebDriverException
        from selenium.webdriver.common.by import By

        for selector in selectors:
            try:
                element = self.driver.find_element(By.CSS_SELECTOR, selector)
                text = (element.text or "").strip()
            except WebDriverException:
                continue
            if text:
                return text
        return ""

    def read_metadata(self, ref: Any) -> tuple[str, str]:
        """Read the title and author of a volume.

        Args:
            ref: A :class:`~google_book_downloader.bookurl.BookRef`.

        Returns:
            ``(title, author)``; the author falls back to
            :data:`~google_book_downloader.models.UNKNOWN_AUTHOR`.
        """
        self._navigate(ref.data_url)
        title = self._first_text(_TITLE_SELECTORS)
        author = self._first_text(_AUTHOR_SELECTORS)
        if title.lower().startswith("books.google"):
            title = ""
        title = title or ref.volume_id
        author = author.lstrip("b").strip() if author else UNKNOWN_AUTHOR
        # "by John Doe" -> "John Doe"
        author = re.sub(r"^by\s+", "", author, flags=re.IGNORECASE) or UNKNOWN_AUTHOR
        return title, author

    def _viewer_state(self) -> dict[str, Any]:
        """Read the embedded viewer's current page and page count."""
        from selenium.common.exceptions import WebDriverException

        try:
            state = self.driver.execute_script(_VIEWER_STATE_JS)
        except WebDriverException:
            return {}
        return state if isinstance(state, dict) else {}

    def _click_first(self, selectors: Iterable[str]) -> bool:
        """Click the first element found for ``selectors``. Returns success."""
        from selenium.common.exceptions import WebDriverException
        from selenium.webdriver.common.by import By

        for selector in selectors:
            try:
                element = self.driver.find_element(By.CSS_SELECTOR, selector)
            except WebDriverException:
                continue
            try:
                element.click()
                return True
            except WebDriverException:
                # Covered by an overlay, or otherwise not interactable.
                with contextlib.suppress(WebDriverException):
                    self.driver.execute_script("arguments[0].click();", element)
                    return True
        return False

    def _turn_page(self, current: int | None) -> bool:
        """Advance the viewer by one page.

        The viewer API is asked first, then the next-page control is clicked,
        then the arrow key the 2020 tool relied on is sent.

        Args:
            current: The page the viewer reports, when it is known.

        Returns:
            ``False`` only when no way of turning the page worked at all.
        """
        from selenium.common.exceptions import WebDriverException
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys

        if isinstance(current, int):
            with contextlib.suppress(WebDriverException):
                self.driver.execute_script(_GOTO_PAGE_JS, current + 1)
        if self._click_first(_NEXT_PAGE_SELECTORS):
            return True
        try:
            self.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.RIGHT)
        except WebDriverException:
            return False
        return True

    def _viewer_exhausted(self, state: dict[str, Any]) -> bool:
        """Whether the viewer itself says it has reached the last page."""
        current, total = state.get("page"), state.get("pages")
        return isinstance(current, int) and isinstance(total, int) and 0 < total <= current

    def capture(
        self,
        ref: BookRef,
        *,
        max_pages: int | None = None,
        progress: CaptureCallback | None = None,
    ) -> Book:
        """Walk through a preview and return every page image it loaded.

        Page turning stops at whichever comes first: the viewer reporting its
        last page, page turning no longer advancing, ``max_pages``, or a hard
        safety cap derived from the viewer's page count.

        Args:
            ref: A :class:`~google_book_downloader.bookurl.BookRef`.
            max_pages: Optional cap, useful for a quick taste of a long book.
            progress: Optional callback receiving ``(captured, page_number)``.

        Returns:
            The captured :class:`~google_book_downloader.models.Book`.

        Raises:
            NoPagesFound: If the viewer never requested a page image.
        """
        title, author = self.read_metadata(ref)
        book = Book(volume_id=ref.volume_id, title=title, author=author)

        self.reset_recording()
        self._navigate(ref.embed_url)
        self._drain_viewer()

        # One loop iteration turns one page, so the viewer's own page count
        # plus slack is a hard, honest upper bound -- no runaway loops even if
        # the viewer ignores every command we send it.
        state = self._viewer_state()
        total = state.get("pages")
        hard_limit = int(total) * 2 + 50 if isinstance(total, int) and total > 0 else 1000
        if max_pages is not None:
            hard_limit = min(hard_limit, max_pages + 50)

        stagnant = 0
        previous: int | None = None
        for _ in range(hard_limit):
            self._collect_into(book, progress)
            state = self._viewer_state()
            current = state.get("page") if isinstance(state.get("page"), int) else None

            if max_pages is not None and len(book.pages) >= max_pages:
                logger.debug("Reached the page cap of %s", max_pages)
                break
            if self._viewer_exhausted(state):
                logger.debug("Viewer reports the last page (%s/%s)", current, state.get("pages"))
                break
            if not self._turn_page(current):
                logger.debug("No page control found; stopping")
                break

            if current is not None and current == previous:
                stagnant += 1
                if stagnant >= 3:
                    logger.debug("Page turning stopped advancing at %s", current)
                    break
            else:
                stagnant = 0
            previous = current

        self._drain_viewer()
        self._collect_into(book, progress)

        if not book.pages:
            raise NoPagesFound(
                f"No page images were found for {ref.volume_id!r}. The book probably has "
                "no preview available (Google shows only 'About this book' for it), or the "
                "viewer layout changed. Open the URL in a browser to check, then re-run "
                "with --headed if it does have a preview."
            )
        return book

    def _collect_into(self, book: Book, progress: CaptureCallback | None) -> None:
        """Move every newly recorded page from the recorder into ``book``."""
        for number, page in self._recorder.pages.items():
            if number not in book.pages:
                book.pages[number] = page
                if progress is not None:
                    progress(len(book.pages), number)

    def _drain_viewer(self) -> None:
        """Wait briefly so in-flight image requests can be recorded."""
        deadline = time.monotonic() + max(self.config.settle_time, 1.0)
        while time.monotonic() < deadline:
            time.sleep(0.1)

    def page_token(self, ref: BookRef, page: int) -> str | None:
        """Fetch the viewer's paging token for ``page``.

        Kept public because it is the most stable way to learn the real page
        count of a preview without turning every leaf; it degrades gracefully
        (returns ``None``) when the RPC shape changes again.
        """
        import httpx

        url = _TOKEN_URL.format(volume_id=ref.volume_id, page=page)
        try:
            response = httpx.get(url, timeout=10.0, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
            payload = response.json()
        except Exception:
            logger.debug("Could not read the paging token", exc_info=True)
            return None
        for entry in payload.get("data", []):
            token = entry.get("token")
            if token:
                return str(token)
        return None


def default_browser_available() -> list[str]:
    """Return the browsers that look installed on this machine."""
    found: list[str] = []
    candidates = {
        "chrome": ("google-chrome", "google-chrome-stable", "chrome", "chromium", "chromium-browser"),
        "edge": ("microsoft-edge", "msedge"),
        "firefox": ("firefox",),
    }
    extra = {
        "chrome": [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        ],
        "firefox": [
            "/Applications/Firefox.app/Contents/MacOS/firefox",
            r"C:\Program Files\Mozilla Firefox\firefox.exe",
        ],
        "edge": ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"],
    }
    for name, executables in candidates.items():
        if any(shutil.which(executable) for executable in executables) or any(
            Path(path).exists() for path in extra.get(name, [])
        ):
            found.append(name)
    return found
