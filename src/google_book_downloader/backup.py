"""Backups of captured page URLs.

The 2020 tool wrote ``str(dict)`` to a ``.txt`` file and read it back with
``eval()``, which is both unsafe and fragile.  Backups are now JSON, carry a
schema version, and are validated on load.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from google_book_downloader.errors import BackupError
from google_book_downloader.models import Book
from google_book_downloader.paths import atomic_write_text, slugify

SCHEMA_VERSION = 2
SUFFIX = ".gbd.json"

#: 1.x wrote ``{1: 'https://...', 2: '...'}`` to a ``.txt`` file.
_LEGACY_LITERAL = re.compile(r"^\s*\{\s*\d+\s*:")


def default_backup_path(directory: Path, book: Book) -> Path:
    """Return ``<directory>/<book slug>.gbd.json``."""
    return directory / f"{slugify(book.slug) or book.volume_id}{SUFFIX}"


def save_backup(path: Path, book: Book) -> Path:
    """Serialise ``book`` to ``path`` atomically.

    Args:
        path: Destination file. Parent directories are created as needed.
        book: The captured book.

    Returns:
        The path that was written.

    Raises:
        BackupError: If the file cannot be written.
    """
    payload = book.to_dict()
    payload["schema"] = SCHEMA_VERSION
    try:
        atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    except OSError as exc:
        raise BackupError(f"Could not write the backup file {path}: {exc}") from exc
    return path


def load_backup(path: Path) -> Book:
    """Load a backup written by :func:`save_backup`.

    Files produced by the legacy tool (a Python ``dict`` literal) cannot be
    read safely and are rejected with an explanatory message.

    Args:
        path: Backup file to read.

    Returns:
        The restored book.

    Raises:
        BackupError: If the file is missing, unreadable or malformed.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise BackupError(f"Backup file not found: {path}") from exc
    except OSError as exc:
        raise BackupError(f"Could not read the backup file {path}: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        if _LEGACY_LITERAL.match(raw):
            raise BackupError(
                f"{path.name} looks like a backup from the old version of this tool. "
                "Re-run the capture for this book to create a v2 JSON backup."
            ) from exc
        raise BackupError(f"{path.name} is not a valid backup file: {exc}") from exc

    if not isinstance(data, dict) or "volume_id" not in data:
        raise BackupError(f"{path.name} is not a Google Books Downloader backup.")
    schema = int(data.get("schema", 1))
    if schema > SCHEMA_VERSION:
        raise BackupError(
            f"{path.name} was written by a newer version of Google Books Downloader (schema {schema}); please upgrade."
        )
    try:
        book = Book.from_dict(data)
    except (KeyError, TypeError, ValueError) as exc:
        raise BackupError(f"{path.name} is corrupted: {exc}") from exc
    if not book.pages:
        raise BackupError(f"{path.name} does not contain any pages.")
    return book
