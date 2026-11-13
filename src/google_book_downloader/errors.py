"""Exception hierarchy.

Every failure the tool can foresee raises a subclass of
:class:`GoogleBooksError`, so the CLI can present a short, actionable message
instead of a traceback.  Unexpected exceptions are still logged in full.
"""

from __future__ import annotations


class GoogleBooksError(Exception):
    """Base class for all expected failures."""


class InvalidBookUrl(GoogleBooksError):
    """The supplied book URL (or id) could not be parsed."""


class BrowserError(GoogleBooksError):
    """The browser or its driver could not be started or driven."""


class CaptureError(GoogleBooksError):
    """The page images could not be discovered."""


class NoPagesFound(CaptureError):
    """The preview loaded but no page images were observed.

    Usually means the book has no preview, or the viewer changed.
    """


class PageSelectionError(GoogleBooksError):
    """The page specification could not be parsed."""


class DownloadError(GoogleBooksError):
    """One or more page images could not be downloaded."""


class BackupError(GoogleBooksError):
    """A backup file could not be written, read or understood."""
