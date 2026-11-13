"""Command-line interface.

Two ways to use the tool:

* **Flags** (scriptable, non-interactive)::

      gbd download https://books.google.com/books?id=buc0AAAAMAAJ --pages 1-20

* **Wizard** (no arguments, or ``gbd wizard``): the guided flow the original
  program offered, now with validation, a progress UI and no crashes::

      gbd
"""

from __future__ import annotations

import logging
import sys
import traceback
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.prompt import Confirm, Prompt

from google_book_downloader import __version__, ui
from google_book_downloader.backup import default_backup_path, load_backup, save_backup
from google_book_downloader.bookurl import BookRef, parse_book_reference
from google_book_downloader.browser import (
    Browser,
    BrowserConfig,
    default_browser_available,
)
from google_book_downloader.downloader import Downloader
from google_book_downloader.errors import GoogleBooksError
from google_book_downloader.exporters import images_to_pdf
from google_book_downloader.models import Book
from google_book_downloader.pages import parse_selection
from google_book_downloader.paths import slugify, unique_directory

logger = logging.getLogger("google_book_downloader")

app = typer.Typer(
    name="gbd",
    help="Download the pages of a Google Books preview as images or a PDF.",
    no_args_is_help=False,
    invoke_without_command=True,
    add_completion=False,
    rich_markup_mode="rich",
)


class BrowserChoice(str, Enum):
    """Browser to drive."""

    def __str__(self) -> str:
        return self.value

    chrome = "chrome"
    edge = "edge"
    firefox = "firefox"


class OutputFormat(str, Enum):
    """What to produce from the downloaded images."""

    def __str__(self) -> str:
        return self.value

    images = "images"
    pdf = "pdf"
    both = "both"


# --------------------------------------------------------------------------
# wiring
# --------------------------------------------------------------------------
@app.callback()
def main(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option("--version", "-V", help="Show the version and exit.", is_eager=True),
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Log every step (debug).")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Only print errors.")] = False,
) -> None:
    """Google Books Downloader.

    Run [bold]gbd wizard[/] for the guided flow, or [bold]gbd download[/] for a
    scriptable, non-interactive run.
    """
    if version:
        typer.echo(f"google-book-downloader {__version__}")
        raise typer.Exit(0)
    _configure_logging(verbose=verbose, quiet=quiet)
    ctx.obj = {"console": ui.make_console(quiet=quiet), "verbose": verbose, "quiet": quiet}
    if ctx.invoked_subcommand is None:
        # `gbd` with no arguments runs the wizard, like the original tool.
        ctx.invoke(wizard, ctx=ctx)


def _interactive() -> bool:
    """Whether the wizard can prompt the user."""
    try:
        return bool(sys.stdin.isatty())
    except (AttributeError, ValueError):  # pragma: no cover - exotic stdin
        return False


def _wizard_input_present() -> bool:
    """True when answers are piped in, e.g. ``printf ... | gbd`` in a script."""
    stream = getattr(sys.stdin, "stream", None)
    if stream is not None:
        # ``click.testing`` replaces stdin with an EchoingStdin that keeps the
        # answers in memory.
        try:
            return bool(stream.readable())
        except (AttributeError, ValueError):  # pragma: no cover
            return False
    return sys.stdin is not None and not sys.stdin.isatty()


def _console(ctx: typer.Context) -> Any:
    return ctx.obj["console"]


def _configure_logging(*, verbose: bool, quiet: bool) -> None:
    level = logging.DEBUG if verbose else logging.ERROR if quiet else logging.WARNING
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[dim]%(levelname)s %(name)s:[/] %(message)s"))
    logging.basicConfig(level=level, handlers=[handler], force=True)


# --------------------------------------------------------------------------
# download
# --------------------------------------------------------------------------
@app.command()
def download(
    ctx: typer.Context,
    url: Annotated[str, typer.Argument(help="Google Books URL, or just the volume id.", show_default=False)],
    pages: Annotated[str, typer.Option("--pages", "-p", help="Pages: all, 1-20, odd, 5,10-12, first-3.")] = "all",
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Save under this directory.")] = None,
    output_format: Annotated[
        OutputFormat, typer.Option("--format", "-f", help="Save images, a PDF, or both.")
    ] = OutputFormat.both,
    browser: Annotated[BrowserChoice, typer.Option("--browser", "-b", help="Browser to drive.")] = BrowserChoice.chrome,
    headless: Annotated[bool, typer.Option("--headless/--headed", help="Run the browser without a window.")] = True,
    binary: Annotated[Path | None, typer.Option(help="Path to the browser executable.")] = None,
    driver_path: Annotated[Path | None, typer.Option(help="Path to a driver binary (skip Selenium Manager).")] = None,
    concurrency: Annotated[int, typer.Option("--concurrency", "-c", min=1, max=16, help="Parallel downloads.")] = 4,
    retries: Annotated[int, typer.Option("--retries", min=1, max=10, help="Attempts per page.")] = 3,
    timeout: Annotated[float, typer.Option("--timeout", min=5.0, max=180.0, help="Network timeout (seconds).")] = 30.0,
    max_pages: Annotated[int | None, typer.Option("--max-pages", min=1, help="Stop capturing after N pages.")] = None,
    backup: Annotated[Path | None, typer.Option("--backup", help="Write the captured URLs to this JSON file.")] = None,
    reuse_backup: Annotated[
        Path | None, typer.Option("--reuse-backup", help="Skip capture and use this JSON backup instead.")
    ] = None,
    overwrite: Annotated[bool, typer.Option("--overwrite", help="Re-download pages already on disk.")] = False,
    keep_images: Annotated[
        bool, typer.Option("--keep-images/--no-keep-images", help="Keep the images when making a PDF.")
    ] = True,
) -> None:
    """Download a book non-interactively."""
    console = _console(ctx)
    options = DownloadOptions(
        pages=pages,
        output=output,
        output_format=output_format,
        concurrency=concurrency,
        retries=retries,
        timeout=timeout,
        overwrite=overwrite,
        keep_images=keep_images,
    )
    config = BrowserConfig(
        browser=browser.value,
        headless=headless,
        binary=str(binary) if binary else None,
        driver_path=str(driver_path) if driver_path else None,
    )
    raise typer.Exit(
        _run_download(
            console,
            url=url,
            options=options,
            config=config,
            reuse_backup=reuse_backup,
            backup_path=backup,
            max_pages=max_pages,
        )
    )


# --------------------------------------------------------------------------
# capture
# --------------------------------------------------------------------------
@app.command()
def capture(
    ctx: typer.Context,
    url: Annotated[str, typer.Argument(help="Google Books URL, or just the volume id.", show_default=False)],
    backup: Annotated[Path, typer.Option("--backup", "-B", help="Where to write the JSON backup.")],
    browser: Annotated[BrowserChoice, typer.Option("--browser", "-b", help="Browser to drive.")] = BrowserChoice.chrome,
    headless: Annotated[bool, typer.Option("--headless/--headed", help="Run the browser without a window.")] = True,
    binary: Annotated[Path | None, typer.Option(help="Path to the browser executable.")] = None,
    driver_path: Annotated[Path | None, typer.Option(help="Path to a driver binary.")] = None,
    max_pages: Annotated[int | None, typer.Option("--max-pages", min=1, help="Stop after N pages.")] = None,
) -> None:
    """Walk a preview and save the page URLs, without downloading them."""
    console = _console(ctx)
    config = BrowserConfig(
        browser=browser.value,
        headless=headless,
        binary=str(binary) if binary else None,
        driver_path=str(driver_path) if driver_path else None,
    )
    try:
        ref = parse_book_reference(url)
        book = capture_book(console, ref, config, max_pages=max_pages)
        path = save_backup(backup.expanduser(), book)
    except GoogleBooksError as exc:
        ui.error(console, str(exc))
        raise typer.Exit(1) from exc
    ui.success(console, f"Backup written to {path}")
    ui.show_book_table(console, book)
    raise typer.Exit(0)


# --------------------------------------------------------------------------
# wizard
# --------------------------------------------------------------------------
@app.command()
def wizard(
    ctx: typer.Context,
    url: Annotated[str | None, typer.Option("--url", "-u", help="Pre-fill the book URL.")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Pre-fill the output directory.")] = None,
    browser: Annotated[BrowserChoice, typer.Option("--browser", "-b", help="Browser to drive.")] = BrowserChoice.chrome,
    headless: Annotated[bool, typer.Option("--headless/--headed", help="Run the browser without a window.")] = True,
) -> None:
    """Guided, step-by-step download (the classic experience)."""
    console = _console(ctx)
    if not _interactive() and not _wizard_input_present():
        ui.error(console, "The wizard needs an interactive terminal.")
        ui.info(
            console,
            "For scripted runs use: gbd download <URL> --pages all --output DIR",
        )
        raise typer.Exit(2)
    try:
        code = _run_wizard(console, url=url, output=output, browser=browser, headless=headless)
    except (EOFError, KeyboardInterrupt):
        console.print()
        ui.warn(console, "Cancelled: no more answers on stdin.")
        code = 130
    raise typer.Exit(code)


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------
@app.command()
def doctor(ctx: typer.Context) -> None:
    """Check the environment: Python, Selenium and installed browsers."""
    console = _console(ctx)
    ui.banner(console, __version__)
    console.print(f"Python     [dim]{sys.version.split()[0]}[/]")
    try:
        import selenium

        console.print(f"Selenium   [dim]{selenium.__version__}[/]")
    except ImportError:
        ui.error(console, "Selenium is not installed. Run: pip install google-book-downloader")
        raise typer.Exit(1) from None
    try:
        import httpx
        import PIL

        console.print(f"httpx      [dim]{httpx.__version__}[/]")
        console.print(f"Pillow     [dim]{PIL.__version__}[/]")
    except ImportError as exc:  # pragma: no cover - installed with the package
        ui.error(console, f"Missing dependency: {exc}")
        raise typer.Exit(1) from exc
    found = default_browser_available()
    if found:
        ui.success(console, f"Browsers detected: {', '.join(found)}")
    else:
        ui.warn(console, "No Chrome/Edge/Firefox found on PATH. Install one, or pass --binary.")
    ui.info(console, "Selenium Manager fetches the matching driver on first run; no binary to install.")
    raise typer.Exit(0)


# --------------------------------------------------------------------------
# implementation
# --------------------------------------------------------------------------
class DownloadOptions:
    """Resolved knobs for :func:`_download`."""

    def __init__(
        self,
        *,
        pages: str,
        output: Path | None,
        output_format: OutputFormat,
        concurrency: int,
        retries: int,
        timeout: float,
        overwrite: bool,
        keep_images: bool,
    ) -> None:
        self.pages = pages
        self.output = output
        self.output_format = output_format
        self.concurrency = concurrency
        self.retries = retries
        self.timeout = timeout
        self.overwrite = overwrite
        self.keep_images = keep_images


def capture_book(console: Any, ref: BookRef, config: BrowserConfig, *, max_pages: int | None = None) -> Book:
    """Open the browser, capture the preview and return the book."""
    ui.info(console, f"Opening the preview in {config.browser} (headless={config.headless})…")
    with ui.spinner(console, "Walking through the pages…"), Browser(config) as browser:
        return browser.capture(ref, max_pages=max_pages)


def page_summary(book: Book) -> str:
    """Human description of the captured page range."""
    numbers = sorted(book.pages)
    if not numbers:
        return "none"
    return f"{len(numbers)} pages ({numbers[0]}–{numbers[-1]})"


def _download(console: Any, book: Book, options: DownloadOptions) -> int:
    """Select, download and export the pages of ``book``."""
    selection = parse_selection(options.pages, book.pages)
    if selection.missing:
        ui.warn(
            console,
            "Not available in this preview, skipped: " + ", ".join(str(number) for number in sorted(selection.missing)),
        )
    if not selection.numbers:
        ui.error(console, f"None of the requested pages exist. Available: {page_summary(book)}")
        return 2

    root = options.output.expanduser().resolve() if options.output else Path.cwd()
    directory = unique_directory(root, slugify(book.slug) or book.volume_id)
    pages = [book.pages[number] for number in sorted(selection.numbers)]
    ui.info(console, f"Downloading {len(pages)} page(s) into [bold]{directory}[/]")

    downloader = Downloader(concurrency=options.concurrency, retries=options.retries, timeout=options.timeout)
    with ui.progress_bar(console, "Downloading pages", total=len(pages)) as (progress, task):
        result = downloader.download(
            pages,
            directory,
            overwrite=options.overwrite,
            progress=lambda done, total, _number: progress.update(task, completed=done, total=total),
        )

    if result.failed:
        ui.warn(console, f"{len(result.failed)} page(s) could not be downloaded.")
    ui.success(console, result.summary())

    produced: list[Path] = []
    wants_pdf = options.output_format in {OutputFormat.pdf, OutputFormat.both}
    if wants_pdf:
        pdf_path = directory / f"{slugify(book.title) or 'book'}.pdf"
        try:
            with ui.spinner(console, "Building the PDF…"):
                images_to_pdf(result.saved.values(), pdf_path)
        except GoogleBooksError as exc:
            ui.error(console, str(exc))
            return 1
        produced.append(pdf_path)
        if not options.keep_images:
            for image in result.saved.values():
                image.unlink(missing_ok=True)

    if options.output_format in {OutputFormat.images, OutputFormat.both} and options.keep_images:
        produced.insert(0, directory)

    console.print()
    ui.success(console, "Done. Output:")
    ui.print_paths(console, produced or [directory])
    return 0 if result.ok else 1


def _run_download(
    console: Any,
    *,
    url: str,
    options: DownloadOptions,
    config: BrowserConfig,
    reuse_backup: Path | None = None,
    backup_path: Path | None = None,
    max_pages: int | None = None,
) -> int:
    try:
        if reuse_backup is not None:
            book = load_backup(reuse_backup.expanduser())
            ui.info(console, f"Loaded {len(book.pages)} page(s) from {reuse_backup}")
        else:
            ref = parse_book_reference(url)
            book = capture_book(console, ref, config, max_pages=max_pages)
            ui.show_book_table(console, book)
            if backup_path is not None:
                path = save_backup(backup_path.expanduser(), book)
                ui.success(console, f"Backup written to {path}")
        return _download(console, book, options)
    except GoogleBooksError as exc:
        ui.error(console, str(exc))
        return 1
    except KeyboardInterrupt:
        console.print()
        ui.warn(console, "Cancelled.")
        return 130
    except Exception:  # noqa: BLE001 - last-resort reporting, never a bare traceback
        ui.error(console, "Something went wrong. Full traceback:")
        console.print(traceback.format_exc(), style="dim")
        return 1


def _ask_url(console: Any, preset: str | None) -> BookRef:
    if preset:
        return parse_book_reference(preset)
    console.print()
    console.print("[bold]Step 1[/] · Paste the URL of the book preview.")
    console.print(
        "[dim]e.g. https://books.google.com/books?id=buc0AAAAMAAJ&printsec=frontcover[/]",
        highlight=False,
    )
    while True:
        raw = Prompt.ask("URL", console=console)
        try:
            return parse_book_reference(raw)
        except GoogleBooksError as exc:
            ui.error(console, str(exc))


def _run_wizard(console: Any, *, url: str | None, output: Path | None, browser: BrowserChoice, headless: bool) -> int:
    ui.banner(console, __version__)

    book: Book | None = None
    if url is None and Confirm.ask("Start from an existing backup file?", default=False, console=console):
        while True:
            candidate = Path(Prompt.ask("Backup file", console=console)).expanduser()
            try:
                book = load_backup(candidate)
                break
            except GoogleBooksError as exc:
                ui.error(console, str(exc))
                if not Confirm.ask("Try another file?", default=True, console=console):
                    return 1
        ui.info(console, f"Loaded {len(book.pages)} page(s) from {candidate}")
        ui.show_book_table(console, book)

    installed = default_browser_available()
    if not installed:
        ui.warn(console, "No Chrome/Edge/Firefox found on PATH; Selenium Manager will try to fetch one.")

    if book is None:
        ref = _ask_url(console, url)
        config = BrowserConfig(browser=browser.value, headless=headless)
        console.print()
        ui.info(console, f"Capturing “{ref.volume_id}” with {browser.value}. This is the slow part.")
        try:
            book = capture_book(console, ref, config)
        except GoogleBooksError as exc:
            ui.error(console, str(exc))
            alternatives = [name for name in installed if name != browser.value]
            if alternatives:
                ui.info(console, f"Try another browser with --browser {alternatives[0]}.")
            return 1
        ui.show_book_table(console, book)

        console.print()
        if Confirm.ask("Save a backup of the captured page URLs?", default=True, console=console):
            destination = (output.expanduser() if output else Path.cwd()) / default_backup_path(Path(), book).name
            path = save_backup(destination, book)
            ui.success(console, f"Backup written to {path}")

    console.print()
    console.print("[bold]Step 2[/] · Which pages?")
    console.print("[dim]all · 5,10-20 · odd · even · first-20 · last · 1, 10-50, odd, 603[/]", highlight=False)
    while True:
        spec = Prompt.ask("Pages", default="all", console=console)
        try:
            selection = parse_selection(spec, book.pages)
        except GoogleBooksError as exc:
            ui.error(console, str(exc))
            continue
        if not selection.numbers:
            ui.error(console, f"None of those pages exist. Available: {page_summary(book)}")
            continue
        break
    if selection.missing:
        ui.warn(console, f"Skipping {len(selection.missing)} page(s) not in this preview.")

    console.print()
    console.print("[bold]Step 3[/] · Where should the book go?")
    raw_dir = Prompt.ask("Directory", default=str(output or Path.cwd()), console=console)
    target = Path(raw_dir).expanduser()

    console.print()
    console.print("[bold]Step 4[/] · Output format")
    choice = Prompt.ask("Format", choices=["pdf", "images", "both"], default="both", console=console)

    options = DownloadOptions(
        pages=spec,
        output=target,
        output_format=OutputFormat(choice),
        concurrency=4,
        retries=3,
        timeout=30.0,
        overwrite=False,
        keep_images=True,
    )
    console.print()
    return _download(console, book, options)


def entry() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":  # pragma: no cover
    entry()
