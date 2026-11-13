"""Rich-powered presentation helpers shared by the CLI.

Keeping all console output here means the scraping and downloading code stays
free of ``print`` calls and is trivial to test.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from google_book_downloader.models import Book

BANNER = "Google Books Downloader"
ACCENT = "cyan"


class UiConsole(Console):
    """A console that can silence itself while still showing errors."""

    quiet_ui: bool = False


def make_console(*, quiet: bool = False) -> UiConsole:
    """Build the shared console.

    Args:
        quiet: Suppress informational output; errors are still shown.

    Returns:
        A configured :class:`rich.console.Console`.
    """
    console = UiConsole(
        log_path=False,
        log_time=False,
        highlight=False,
        emoji=False,
        markup=True,
        soft_wrap=False,
    )
    console.quiet_ui = quiet
    return console


def _silent(console: Console) -> bool:
    return bool(getattr(console, "quiet_ui", False))


def banner(console: Console, version: str) -> None:
    """Print the welcome banner."""
    console.print()
    console.print(
        Panel.fit(
            f"[bold {ACCENT}]{BANNER}[/] [dim]v{version}[/]\n[dim]Selenium 4 + WebDriver BiDi · no bundled driver[/]",
            border_style=ACCENT,
        )
    )


def info(console: Console, message: str) -> None:
    """Print an informational line."""
    if _silent(console):
        return
    console.print(f"[{ACCENT}]›[/] {message}")


def success(console: Console, message: str) -> None:
    """Print a success line."""
    if _silent(console):
        return
    console.print(f"[green]✓[/] {message}")


def warn(console: Console, message: str) -> None:
    """Print a warning line."""
    if _silent(console):
        return
    console.print(f"[yellow]![/] {message}")


def error(console: Console, message: str) -> None:
    """Print an error line. Always shown, even in quiet mode."""
    if _silent(console):
        Console().print(f"[bold red]✗[/] {message}")
        return
    console.print(f"[bold red]✗[/] {message}")


def show_book_table(console: Console, book: Book) -> None:
    """Print :func:`book_table` unless the console is quiet."""
    if _silent(console):
        return
    console.print(book_table(book))


def book_table(book: Book) -> Table:
    """Render a summary table for a captured book."""
    table = Table(title="Captured book", title_style=f"bold {ACCENT}", box=None, pad_edge=False)
    table.add_column("Field", style="dim")
    table.add_column("Value")
    table.add_row("Title", book.title)
    table.add_row("Author", book.author)
    table.add_row("Volume id", book.volume_id)
    numbers = sorted(book.pages)
    table.add_row("Pages available", str(len(numbers)))
    if numbers:
        table.add_row("Page range", f"{numbers[0]} – {numbers[-1]}")
    return table


@contextmanager
def progress_bar(console: Console, description: str, total: int | None = None) -> Iterator[tuple[Progress, TaskID]]:
    """Context manager yielding ``(progress, task)`` for manual updates."""
    progress = Progress(
        SpinnerColumn(style=ACCENT),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=None),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=True,
    )
    with progress:
        task = progress.add_task(description, total=total)
        yield progress, task


@contextmanager
def spinner(console: Console, description: str) -> Iterator[None]:
    """Show a spinner while the enclosed block runs."""
    progress = Progress(
        SpinnerColumn(style=ACCENT),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )
    with progress:
        progress.add_task(description, total=None)
        yield


def print_paths(console: Console, paths: Iterable[Any]) -> None:
    """Print a list of produced files."""
    if _silent(console):
        return
    for path in paths:
        console.print(f"  [dim]•[/] {path}")
