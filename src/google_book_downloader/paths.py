"""Path helpers: sane filenames, unique output directories, atomic writes."""

from __future__ import annotations

import os
import re
import tempfile
import unicodedata
from pathlib import Path

# Everything that is neither a letter nor a digit collapses into one dash.
_NON_WORD = re.compile(r"[^A-Za-z0-9]+")
_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def slugify(text: str, *, max_length: int = 120) -> str:
    """Turn arbitrary text into a portable file or folder name.

    Unicode is normalised to ASCII where possible, whitespace collapses to a
    single dash and characters that Windows rejects are dropped.

    Args:
        text: Raw text, usually a book title and author.
        max_length: Hard cap so the name cannot blow past filesystem limits.

    Returns:
        A trimmed, filesystem-safe name; may be empty for all-punctuation input.
    """
    normalised = unicodedata.normalize("NFKD", text)
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii")
    slug = _NON_WORD.sub("-", ascii_only).strip("-.")
    slug = slug[:max_length].rstrip("-.")
    if slug.upper() in _RESERVED:
        slug += "-book"
    return slug


def unique_directory(parent: Path, name: str) -> Path:
    """Create ``parent/name`` without ever clobbering an existing folder.

    Args:
        parent: Directory to create the book folder in.
        name: Desired folder name.

    Returns:
        The created directory, suffixed with ``-2``, ``-3``... on collision.
    """
    parent.mkdir(parents=True, exist_ok=True)
    candidate = parent / name
    counter = 2
    while True:
        try:
            candidate.mkdir()
        except FileExistsError:
            candidate = parent / f"{name}-{counter}"
            counter += 1
        else:
            return candidate


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` so readers never see a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temp_path.replace(path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, text: str) -> None:
    """Write UTF-8 ``text`` to ``path`` atomically."""
    atomic_write_bytes(path, text.encode("utf-8"))
